"""App-cache A/B with production PCM, never a physical audio device.

Each worker is a fresh process. 'Cold' means decoded application cache, NOT
an OS disk-cache flush. Default output is local evidence, not user data.
"""
from __future__ import annotations

import argparse
import ctypes
import cProfile
import json
import logging
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from threading import Thread

import numpy as np
from PySide6.QtCore import QCoreApplication

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.audio.sample_piano import SamplePianoEngine
from ai_piano_studio.audio.source import ContinuousPianoToneSource
from ai_piano_studio.audio.tone import PianoToneCoordinator
from ai_piano_studio.domain.note import Note

ROOT = Path(__file__).resolve().parents[1]


class MemoryOutput(PianoVoiceMixer):
    def __init__(self):
        super().__init__()
        self.consumed = 0.0

    def start_audio(self, **kwargs):
        self._audio_ready = True

    def resume_audio(self):
        self._audio_ready = True

    def pause_audio(self):
        self._audio_ready = False

    def processed_audio_seconds(self):
        return self.consumed

    def output_latency_seconds(self):
        return 0.0


def memory():
    if sys.platform != 'win32':
        return {'rss_bytes': None, 'peak_rss_bytes': None}
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'quota_peak', 'quota',
                                                'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak')]
    value = Counters()
    value.cb = ctypes.sizeof(value)
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
        raise ctypes.WinError()
    return {'rss_bytes': value.working, 'peak_rss_bytes': value.peak}


def ui_worker(strategy):
    from ai_piano_studio.audio import sample_piano as samples
    from ai_piano_studio.studio import build_studio_engine
    if strategy == 'baseline':
        samples.SamplePianoEngine.preload_remaining_async = lambda self: None
        samples._schedule_background = lambda job: Thread(target=job, daemon=True).start()
    def forbidden(*args, **kwargs):
        raise AssertionError('physical audio forbidden')
    PianoVoiceMixer.start_audio = forbidden
    with TemporaryDirectory(prefix='cache-ui-bench-') as home:
        wall, cpu = time.perf_counter(), time.thread_time()
        app, engine, editor, playback, keyboard = build_studio_engine(Path(home))
        app.processEvents()
        root = engine.rootObjects()[0]
        report = dict(strategy=strategy, build_ms=(time.perf_counter()-wall)*1000,
                      gui_thread_cpu_ms=(time.thread_time()-cpu)*1000,
                      dpr=root.devicePixelRatio(), **memory())
        selected = playback.piano_source._coordinator.selection().primary
        report['initial_roots'] = list(selected.cached_root_midis)
        root.close()
        engine.deleteLater()
        app.processEvents()
        return report


def stability_worker():
    app = QCoreApplication.instance() or QCoreApplication([])
    engines = []
    def factory(path):
        engine = SamplePianoEngine(path, preload_c3_c5_on_init=False)
        engines.append(engine)
        return engine
    coordinator = PianoToneCoordinator(ROOT/'resources/piano/banks.json', engine_factory=factory)
    mixer = MemoryOutput()
    source = ContinuousPianoToneSource(coordinator, mixer)
    rows = []
    for index in range(30):
        begin = time.perf_counter()
        coordinator.commit((0, .25, .5, .75, 1)[index % 5])
        deadline = time.perf_counter() + 2
        while any(e.cache_metrics()['pending'] for e in engines) and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(.001)
        assert not any(e.cache_metrics()['pending'] for e in engines)
        assert all(e.cache_bytes <= 24 * 1024 * 1024 for e in engines)
        source.start_voice(str(index), (39,75,81)[index%3], 75)
        mixer.mix_frames(512)
        source.note_off(str(index), ReleaseMode.STOP)
        mixer.mix_frames(1024)
        rows.append(dict(cycle=index, ready_ms=(time.perf_counter()-begin)*1000,
                         cache_bytes=sum(e.cache_bytes for e in engines),
                         metrics=[e.cache_metrics() for e in engines], **memory()))
    return rows


def worker(strategy, cache, scenario, profile=None, tone=.5):
    app = QCoreApplication.instance() or QCoreApplication([])
    engines = []
    def factory(path):
        options = {'preload_c3_c5_on_init': False}
        if strategy == 'baseline':
            options['scheduler'] = lambda job: Thread(target=job, daemon=True).start()
        engine = SamplePianoEngine(path, **options)
        if strategy == 'baseline':
            engine.preload_remaining_async = lambda: None
        engines.append(engine)
        return engine
    begin = time.perf_counter()
    cpu = time.process_time()
    coordinator = PianoToneCoordinator(ROOT / 'resources/piano/banks.json', engine_factory=factory)
    coordinator.commit(tone)
    mixer = MemoryOutput()
    source = ContinuousPianoToneSource(coordinator, mixer)
    if strategy == 'baseline':
        source.prefetch_pitches = lambda pitches: None
        source.ready_for_pitches = lambda pitches: True
    pitches = (57, 63, 69) if scenario == 'common' else (39, 75, 81, 33, 99, 57, 63)
    count = {'common': 12, 'cross': 48, 'keys': 48, 'score1000': 1000, 'score5000': 5000}[scenario]
    notes = tuple(Note.create(id=str(i), pitch=pitches[i % len(pitches)],
                             start_time=i / (48 if count >= 1000 else 12),
                             duration=.18, velocity=(40, 75, 100, 127)[i % 4]) for i in range(count))
    if cache == 'warm':
        selection = coordinator.selection()
        for engine in (selection.primary, selection.secondary):
            if engine is not None:
                engine.preload_roots_sync(pitches)
        deadline = time.perf_counter() + 2
        while any(e.cache_metrics()['pending'] for e in engines) and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(.002)
    session = RealtimePlaybackSession(notes, source, mixer)
    blocks, gui_times = [], []
    first_pcm_ms = None
    peak = 0
    squared = 0.0
    samples = 0
    output_hash = __import__('hashlib').sha256()
    before_start = [engine.cache_metrics() for engine in engines]
    start = time.perf_counter()
    if scenario != 'keys':
        session.start()
    while session.is_preparing and time.perf_counter() - start < .5:
        app.processEvents()
        time.sleep(.001)
    # Two seconds of scheduling/mixing, including source warmup and 24-voice cap.
    profiler = cProfile.Profile() if profile else None
    if profiler:
        profiler.enable()
    stream_cpu_start = time.process_time()
    instrument_seconds = 0.0
    for block in range(188):
        if scenario == 'keys':
            # Incoming keys must follow wall time, not an accelerated render.
            # Otherwise two seconds of human input would arrive in ~0.1 s.
            due = start + block * 512 / 48000
            while time.perf_counter() < due:
                app.processEvents()
                time.sleep(.001)
        t = time.perf_counter()
        if scenario == 'keys':
            if block % 4 == 0:
                index = block // 4
                source.start_voice('key-' + str(index), pitches[index % len(pitches)], (40,75,100,127)[index%4])
                if index >= 3:
                    source.note_off('key-' + str(index-3), ReleaseMode.SAFE)
        else:
            session.process_due_events()
        gui_times.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        pcm = mixer._read_output_frames(512)
        blocks.append((time.perf_counter() - t) * 1000)
        instrument_start = time.perf_counter()
        mixer.consumed += 512 / 48000
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float64)
        if first_pcm_ms is None and np.any(values):
            first_pcm_ms = (time.perf_counter() - start) * 1000
        peak = max(peak, int(np.max(np.abs(values))))
        squared += float(np.dot(values, values))
        samples += len(values)
        output_hash.update(pcm)
        instrument_seconds += time.perf_counter() - instrument_start
        app.processEvents()
    if profiler:
        profiler.disable()
        profiler.dump_stats(profile)
    stream_cpu_ms = (time.process_time() - stream_cpu_start) * 1000
    session.stop(ReleaseMode.STOP)
    mixer.release_all(ReleaseMode.STOP)
    mixer.mix_frames(1024)
    before_idle = memory()
    # Finish outstanding bounded loads, then measure stable working set.
    deadline = time.perf_counter() + 2
    while any(e.cache_metrics()['pending'] for e in engines) and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(.002)
    metrics = [e.cache_metrics() for e in engines]
    result = dict(strategy=strategy, cache=cache, scenario=scenario, score_notes=count,
                  first_pcm_ms=first_pcm_ms, prepare_ms=session.preparation_wait_ms,
                  preparation_timeout=session.preparation_timed_out,
                  elapsed_ms=(time.perf_counter()-begin)*1000, cpu_ms=(time.process_time()-cpu)*1000,
                  mix_p50_ms=float(np.percentile(blocks, 50)), mix_p99_ms=float(np.percentile(blocks, 99)),
                  gui_p99_ms=float(np.percentile(gui_times, 99)), peak_pcm=peak,
                  rms_dbfs=20*np.log10(max(1e-10, np.sqrt(squared/max(1, samples))/32767)),
                  pcm_sha256=output_hash.hexdigest(), engines=metrics, **memory(),
                  before_idle_rss_bytes=before_idle['rss_bytes'], instrument_profile=bool(profile),
                  instrument_ms=instrument_seconds * 1000, before_start=before_start)
    result['stream_cpu_ms'] = stream_cpu_ms
    result['paced_input'] = scenario == 'keys'
    result['tone'] = tone
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--ui-worker', action='store_true')
    parser.add_argument('--stability-worker', action='store_true')
    parser.add_argument('--strategy', choices=['baseline', 'candidate'], default='candidate')
    parser.add_argument('--cache', choices=['cold', 'warm'], default='cold')
    parser.add_argument('--scenario', choices=['common', 'cross', 'keys', 'score1000', 'score5000'], default='cross')
    parser.add_argument('--only-scenario', choices=['common', 'cross', 'keys', 'score1000', 'score5000'])
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--profile')
    parser.add_argument('--tone', type=float, default=.5)
    parser.add_argument('--output', type=Path, default=Path('artifacts/audio-cache-stability-20261016/baseline.json'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.stability_worker:
        print(json.dumps(stability_worker()))
        return
    if args.ui_worker:
        print(json.dumps(ui_worker(args.strategy)))
        return
    if args.worker:
        print(json.dumps(worker(args.strategy, args.cache, args.scenario, args.profile, args.tone)))
        return
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for round_no in range(args.rounds):
        for scenario in ([args.only_scenario] if args.only_scenario else ['common', 'cross', 'keys', 'score1000', 'score5000']):
            for cache in ['cold', 'warm']:
                for strategy in ['baseline', 'candidate']:
                    command = [sys.executable, str(Path(__file__).resolve()), '--worker',
                               '--strategy', strategy, '--scenario', scenario, '--cache', cache, '--tone', str(args.tone)]
                    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
                    if run.returncode:
                        raise RuntimeError(run.stderr)
                    row = json.loads(run.stdout)
                    row['warnings'] = run.stderr.splitlines()
                    row['round'] = round_no
                    results.append(row)
            print(f'round {round_no+1}: {scenario} finished', flush=True)
            args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f'{len(results)} fresh-process samples: {args.output}', flush=True)


if __name__ == '__main__':
    main()
