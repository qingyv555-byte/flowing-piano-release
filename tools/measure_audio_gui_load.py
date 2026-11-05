"""Offscreen UI / virtual output load check. No physical audio or user profile."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QSG_RENDER_LOOP'] = 'basic'

from PySide6.QtCore import QObject, QTimer, QThread
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioSink, QAudioSource, QMediaPlayer, QSoundEffect, QAudio


def forbidden(*args, **kwargs):
    raise AssertionError('Physical audio forbidden in this measurement')


for kind, method in ((QAudioSink, 'start'), (QAudioSource, 'start'), (QMediaPlayer, 'play'), (QSoundEffect, 'play')):
    setattr(kind, method, forbidden)

from ai_piano_studio.audio import mixer as mixer_module
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.studio import build_studio_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.music_layer import MusicLayer
from benchmark_studio import visual_item


class VirtualSink(QObject):
    def __init__(self, device, audio_format, parent):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.pull)
        self.frames = 0
        self.last = None
        self.gaps = []
        self.buffer = 0

    def pull(self):
        now = time.perf_counter()
        if self.last is not None:
            self.gaps.append((now - self.last) * 1000)
        self.last = now
        self.device.readData(1920)
        self.frames += 480

    def start(self, device):
        self.device = device
        self.timer.start()

    def processedUSecs(self):
        return round(self.frames / 48000 * 1e6)

    def state(self):
        return QAudio.State.ActiveState

    def setBufferSize(self, size):
        self.buffer = size

    def bufferSize(self):
        return self.buffer

    def setVolume(self, value):
        pass

    def stop(self):
        self.timer.stop()

    reset = stop
    suspend = stop

    def resume(self):
        self.timer.start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gil-switch-ms', type=float)
    args = parser.parse_args()
    if args.gil_switch_ms is not None:
        sys.setswitchinterval(args.gil_switch_ms / 1000)
    mixer_module.QAudioSink = VirtualSink
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    results = []
    with tempfile.TemporaryDirectory(prefix='piano-b03-ui-') as temporary:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(temporary))
        initialize_deferred_services(engine)
        root = engine.rootObjects()[0]
        root.resize(1440, 900)
        root.setProperty('pageState', 'editor')
        appearance = engine.rootContext().contextProperty('studioAppearance')
        mixer = playback._player._mixer
        selection = playback.piano_source._coordinator.selection()
        for bank in (selection.primary, selection.secondary):
            if bank is not None:
                bank.preload_roots_sync({bank.nearest_root(p) for p in range(48, 84)})
        captured = []
        original = mixer._apply_start
        def measured(command):
            captured.append((command.voice_id.split(':', 2)[-1], mixer._output_frame))
            original(command)
        mixer._apply_start = measured
        for style in ('off', 'glass', 'weave'):
            for accompaniment in (False, True):
                state = ProjectState(tuple(Note.create(id=str(i), pitch=48 + i % 36,
                    start_time=(i // 2) * .06, duration=.12, velocity=90,
                    layer_id='layer-default' if i % 2 == 0 else 'accompaniment') for i in range(1000)),
                    layers=(MusicLayer.create(id='layer-default', name='Fixture melody', role='melody'),
                            MusicLayer.create(id='accompaniment', name='Fixture accompaniment', role='accompaniment', muted=not accompaniment, order=1)))
                editor._replace_project(state, saved_content=state.content, path=None)
                appearance.setValue('feedbackLevel', 'off' if style == 'off' else 'rich')
                appearance.setValue('feedbackStyle', 'string' if style in ('off', 'glass') else style)
                app.processEvents()
                timeline = visual_item(root.contentItem(), 'timeline')
                assert timeline is not None
                captured.clear()
                playback.play()
                sink = mixer._sink
                native = sink._sink
                origin = mixer._sink_frame_origin
                started = last_sample = time.perf_counter()
                last_cursor_sample = started
                last_cpu = time.process_time()
                cpu, blocked, frames_during_block, cursor_errors = [], False, 0, []
                while time.perf_counter() - started < 3:
                    now = time.perf_counter()
                    if not blocked and now - started >= 1:
                        before = native.frames
                        until = time.perf_counter() + .5
                        value = 1
                        while time.perf_counter() < until:
                            value = (value * 1664525 + 1013904223) % (2 ** 32)
                        frames_during_block = native.frames - before
                        blocked = True
                    app.processEvents()
                    now = time.perf_counter()
                    if now - last_cursor_sample >= .05:
                        session = playback._player._project_session
                        cursor_errors.append(abs(timeline.property('cursorSeconds') - session.position_seconds) * 1000)
                        last_cursor_sample = now
                    if now - last_sample >= .25:
                        current_cpu = time.process_time()
                        cpu.append(100 * (current_cpu - last_cpu) / (now - last_sample))
                        last_cpu, last_sample = current_cpu, now
                    time.sleep(.001)
                errors = [abs(frame - origin - round((int(identifier) // 2) * .06 * 48000)) / 48
                          for identifier, frame in captured if identifier.isdigit()]
                results.append(dict(style=style, accompaniment=accompaniment, score_notes=1000,
                    starts=len(captured), max_onset_error_ms=max(errors, default=0),
                    pcm_frames_during_500ms_gui_busy=frames_during_block,
                    max_pull_gap_ms=max(native.gaps, default=0),
                    max_qml_cursor_error_ms=max(cursor_errors, default=0),
                    peak_process_one_core_cpu_percent=max(cpu, default=0)))
                playback.stop()
                mixer.stop_audio()
                app.processEvents()
        root.close()
        engine.deleteLater()
        app.processEvents()
    args.output.write_text(json.dumps(dict(physical_audio_calls=0, backend='Qt worker + virtual 10ms sink; offscreen QML', results=results), indent=2), encoding='utf-8')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
