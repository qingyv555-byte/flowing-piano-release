"""Offline B-03 timing evidence. Never constructs a physical output sink."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from time import process_time, perf_counter

import numpy as np
from PySide6.QtCore import QCoreApplication


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.baseline:
        for module in ('mixer', 'project_session'):
            name = 'ai_piano_studio.audio.' + module
            spec = importlib.util.spec_from_file_location(name, args.baseline / (module + '.py'))
            loaded = importlib.util.module_from_spec(spec)
            sys.modules[name] = loaded
            spec.loader.exec_module(loaded)
    from ai_piano_studio.audio.mixer import PianoVoiceMixer
    from ai_piano_studio.audio.project_session import RealtimePlaybackSession
    from ai_piano_studio.audio.profile import ReleaseMode
    from ai_piano_studio.domain.note import Note

    app = QCoreApplication.instance() or QCoreApplication([])
    results = []
    rate_hz = 48000
    for fixture in ('short-notes', 'fur-elise-88bpm-3-8', 'chords-low-high'):
        for stall in (.06, .12, .3, .5):
            for speed in (.5, 1., 2.):
                for repeat in range(3):
                    step = .1 if fixture != 'fur-elise-88bpm-3-8' else 60 / 88 / 4
                    notes = []
                    melody = (76, 75, 76, 75, 76, 71, 74, 72, 69)
                    for i in range(int(17 / step)):
                        pitches = (24, 36, 48, 60, 72, 84, 96, 108) if fixture == 'chords-low-high' else (melody[i % len(melody)],)
                        for pitch in pitches:
                            notes.append(Note.create(id=f'{i}:{pitch}', pitch=pitch, velocity=110,
                                                     start_time=i * step, duration=.04))
                    current = [0.]
                    starts, queued, start_counts = {}, {}, {}

                    class MeasuredMixer(PianoVoiceMixer):
                        def _apply_start(self, command):
                            key = command.voice_id.split(':', 2)[2]
                            starts[key] = self._output_frame
                            start_counts[key] = start_counts.get(key, 0) + 1
                            super()._apply_start(command)

                        def start_audio(self, **kwargs):
                            self._audio_ready = True

                        def processed_audio_seconds(self):
                            return current[0]

                        def output_latency_seconds(self):
                            return .16

                    mixer = MeasuredMixer(sample_rate=rate_hz, clock=lambda: current[0])

                    class Source:
                        def start_voice(self, voice_id, pitch, velocity):
                            queued[voice_id.split(':', 2)[2]] = current[0]
                            mixer.start_voice(voice_id, pitch, velocity, pulse,
                                              source_sample_rate=rate_hz, owner_tag='fixture')

                        def note_off(self, voice_id, mode, **kwargs):
                            mixer.note_off(voice_id, mode, **kwargs)

                    pulse = memoryview(np.full(960, 16000, dtype=np.int16)).toreadonly()
                    session = RealtimePlaybackSession(notes, Source(), mixer, clock=lambda: current[0])
                    session.set_playback_rate(speed)
                    session.start()
                    pcm, cursor_errors, cpu, render_cost = [], [], [], []
                    count = round((17 / speed + .6) * rate_hz / 480)
                    for chunk in range(count):
                        current[0] = max(0., chunk * .01 - .16)
                        # Repeated main-thread stalls while output consumption continues.
                        blocked = .4 <= current[0] % 2 < .4 + stall
                        if not blocked:
                            session.process_due_events()
                            expected_cursor = min(session._project_end_seconds, current[0] * speed)
                            cursor_errors.append(abs(session.position_seconds - expected_cursor) * 1000)
                        before = process_time()
                        wall_before = perf_counter()
                        pcm.append(np.frombuffer(mixer.io_device.readData(1920), dtype=np.int16)[::2])
                        render_cost.append((perf_counter() - wall_before) * 1000)
                        cpu.append((process_time() - before) / .01 * 100)
                    expected = {n.id: round(n.start_time / speed * rate_hz) for n in notes}
                    errors = [abs(starts[k] - v) / rate_hz * 1000 for k, v in expected.items() if k in starts]
                    clusters = {}
                    for key, frame in starts.items():
                        clusters.setdefault(frame, set()).add(expected[key])
                    sounding = np.concatenate(pcm) != 0
                    pcm_onsets = np.flatnonzero(sounding & ~np.r_[False, sounding[:-1]])
                    theoretical_onsets = sorted(set(expected.values()))
                    results.append(dict(fixture=fixture, stall_ms=stall * 1000, speed=speed, repeat=repeat,
                        notes=len(notes), missing=len(expected.keys() - starts.keys()),
                        unexpected=len(starts.keys() - expected.keys()),
                        duplicates=sum(count - 1 for count in start_counts.values()),
                        bunched_onset_groups=sum(len(times) > 1 for times in clusters.values()),
                        max_error_ms=max(errors, default=0), p95_error_ms=float(np.percentile(errors, 95)),
                        pcm_onset_count=len(pcm_onsets), expected_onset_count=len(theoretical_onsets),
                        pcm_onsets_match=pcm_onsets.tolist() == theoretical_onsets,
                        cursor_max_error_ms=max(cursor_errors, default=0),
                        peak_one_core_pcm_cpu_percent=max(cpu),
                        pcm_render_p95_ms=float(np.percentile(render_cost, 95)),
                        pcm_render_max_ms=max(render_cost),
                        first_notes=[dict(id=k, target_frame=v, actual_frame=starts.get(k), queued_seconds=queued.get(k))
                                     for k, v in list(expected.items())[:12]],
                        queue_first_seconds=min(queued.values(), default=0)))
                    session.stop(ReleaseMode.STOP)
                    session.deleteLater()
                    mixer.deleteLater()
                    app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(sample_rate=rate_hz, chunk_frames=480, simulated_buffer_ms=160,
        repeats=3, measurement='virtual sink; CPU is PCM path only, not full GUI peak', results=results), indent=2), encoding='utf-8')
    print(json.dumps(dict(cases=len(results), max_error_ms=max(r['max_error_ms'] for r in results),
        p95_worst_ms=max(r['p95_error_ms'] for r in results), bunched=sum(r['bunched_onset_groups'] for r in results),
        missing=sum(r['missing'] for r in results), pcm_match_cases=sum(r['pcm_onsets_match'] for r in results))))


if __name__ == '__main__':
    main()
