"""Measure production click-to-PCM preparation without opening an audio device.

The sink is deliberately blocked. Numbers exclude OS/device buffering and are
not acoustic latency. Run with PYTHONPATH=src and an output JSON path.
"""
from __future__ import annotations

import argparse
import cProfile
from contextlib import ExitStack
import faulthandler
import json
import os
import platform
from pathlib import Path
import tempfile
import statistics
import math
from time import perf_counter
from unittest.mock import patch


def configure_rendering(window_system, render_loop=None):
    """Select the process backend before importing Qt or creating an application."""
    os.environ['QT_QPA_PLATFORM'] = window_system
    selected_loop = render_loop or ('basic' if window_system == 'offscreen' else 'auto')
    if selected_loop == 'auto':
        os.environ.pop('QSG_RENDER_LOOP', None)
    else:
        os.environ['QSG_RENDER_LOOP'] = selected_loop


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--platform', choices=['offscreen', 'windows'], default='offscreen',
                        help='windows opens a temporary native test window; all modes block audio output')
    parser.add_argument('--render-loop', choices=['auto', 'basic', 'threaded'],
                        help='Default: basic for offscreen, Qt automatic selection for windows')
    parser.add_argument('--notes', type=int, default=0)
    parser.add_argument('--samples', type=int, default=30)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--playing', action='store_true')
    parser.add_argument('--settled-audio', action='store_true',
                        help='Wait for selected sample banks and the probe pitch to finish warming; no sound')
    parser.add_argument('--overview-render-strategy', choices=['immediate', 'threaded'],
                        help='Diagnostic-only Canvas strategy override; production default is unchanged')
    parser.add_argument('--verify-pixels', action='store_true',
                        help='Verify the new selected-note border in captured pixels (idle only); includes readback cost')
    args = parser.parse_args(argv)
    if args.notes < 0 or args.samples < 2:
        parser.error('--notes must be nonnegative and --samples must be at least 2')
    if args.playing and args.verify_pixels:
        parser.error('--verify-pixels requires idle playback to isolate the new note')
    return args


def summarize_samples(rows, metrics):
    summary = {}
    for key in metrics:
        values = sorted(row[key] for row in rows[1:] if row.get(key) is not None)
        summary[key] = {'count': len(values),
                        'p50': statistics.median(values) if values else None,
                        'p95': values[math.ceil(len(values) * .95) - 1] if values else None,
                        'max': max(values) if values else None}
    return summary


def main(argv=None):
    args = parse_arguments(argv)
    configure_rendering(args.platform, args.render_loop)
    # QApplication and the production imports must follow platform selection.
    import PySide6
    from PySide6.QtCore import QObject, QPointF, Qt, qVersion
    from PySide6.QtTest import QTest
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    from PySide6.QtWidgets import QApplication
    from ai_piano_studio.audio.mixer import PianoVoiceMixer
    from ai_piano_studio.audio.profile import ReleaseMode
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.studio import build_studio_engine

    faulthandler.dump_traceback_later(30, exit=True)
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == args.platform, 'Actual window system differs from the requested mode'
    rows = []
    profiler = cProfile.Profile() if args.profile else None
    with tempfile.TemporaryDirectory(prefix='note-response-') as directory, \
            patch.object(PianoVoiceMixer, 'start_audio') as blocked_audio, \
            patch('ai_piano_studio.audio.mixer.QAudioSink',
                  side_effect=AssertionError('Hardware output is forbidden in this diagnostic')), \
            patch.object(PianoVoiceMixer, 'is_audio_ready', return_value=True), \
            ExitStack() as cleanup:
        cleanup.callback(faulthandler.cancel_dump_traceback_later)
        app, engine, editor, playback, keyboard = build_studio_engine(Path(directory))
        root = engine.rootObjects()[0]
        def close_session():
            try:
                playback.stop()
            finally:
                root.close()
                engine.deleteLater()
                app.processEvents()
        cleanup.callback(close_session)
        root.resize(1366, 768)
        initial = ProjectState(tuple(Note.create(id=f'fixture-{i}', pitch=48 + i % 24,
            start_time=i * .04, duration=.12) for i in range(args.notes)))
        editor._replace_project(initial, saved_content=initial.content, path=None)
        root.setProperty('pageState', 'editor')
        app.processEvents()
        if args.platform == 'windows':
            assert QTest.qWaitForWindowExposed(root, 5000), 'Native window was not exposed'
        root.grabWindow()
        runtime = {'python': platform.python_version(), 'pyside': PySide6.__version__,
                   'qt': qVersion(), 'os': platform.platform(), 'qpa': app.platformName(),
                   'render_loop_requested': os.environ.get('QSG_RENDER_LOOP', 'auto'),
                   'graphics_api': str(root.rendererInterface().graphicsApi()),
                   'quick_backend_requested': os.environ.get('QT_QUICK_BACKEND', 'auto'),
                   'rhi_backend_requested': os.environ.get('QSG_RHI_BACKEND', 'auto'),
                   'window_size': [root.width(), root.height()],
                   'device_pixel_ratio': root.devicePixelRatio(),
                   'screen_refresh_hz': root.screen().refreshRate(),
                   'window_exposed': root.isExposed()}
        if args.overview_render_strategy:
            overview = root.findChild(QObject, 'miniMapDenseNoteCanvas')
            assert overview is not None, 'Overview Canvas was not created'
            expression = QQmlExpression(QQmlEngine.contextForObject(overview), overview,
                'renderStrategy = Canvas.' + args.overview_render_strategy.title())
            expression.evaluate()
            assert not expression.hasError(), expression.error().toString()
            app.processEvents()
        canvas = root.findChild(type(root.contentItem()), 'drawNoteArea')
        if canvas is None:
            canvas = root.findChild(QObject, 'drawNoteArea')
        mixer = playback._player.piano_source.mixer
        timeline = root.findChild(QObject, 'timeline')
        warmup_ms = None
        if args.settled_audio:
            selection = playback._player.piano_source._coordinator.selection()
            assert selection is not None, 'No sample bank selected; cannot claim settled sample audio'
            banks = [bank for bank in (selection.primary, selection.secondary) if bank is not None]
            started_warmup = perf_counter()
            for bank in banks:
                bank._schedule_roots((bank.nearest_root(timeline.property('highestPitch')),))
            while True:
                ready = True
                for bank in banks:
                    with bank._lock:
                        ready = ready and not bank._scheduled_roots and bank.nearest_root(timeline.property('highestPitch')) in bank.cached_root_midis
                if ready:
                    break
                assert perf_counter() - started_warmup < 10, 'Sample warmup did not settle'
                QTest.qWait(5)
            warmup_ms = (perf_counter() - started_warmup) * 1000
        def border_visible(image, before, note):
            x = (timeline.property('keyWidth')
                 + (note.start_time - timeline.property('viewStartSeconds')) * timeline.property('pixelsPerSecond'))
            y = (timeline.property('highestPitch') - note.pitch) * timeline.property('rowHeight') + 2
            origin = canvas.mapToScene(QPointF(x, y))
            scale = image.width() / root.width()
            border = timeline.property('theme').property('noteSelectedBorder')
            # Sparse delegates fade pending notes, including their border. Dense
            # Canvas borders are opaque. Requiring the final opaque color would
            # incorrectly measure the 500 ms snapshot sync, not initial display.
            repeater = root.findChild(QObject, 'rawNoteRepeater')
            expression = QQmlExpression(QQmlEngine.contextForObject(repeater), repeater,
                '(function() { for (var i = 0; i < count; i++) {'
                'var item = itemAt(i); if (item && item.noteId === '
                + json.dumps(note.id) + ') return item.opacity; } return 1.0; })()')
            alpha = float(expression.evaluate()[0])
            assert not expression.hasError(), expression.error().toString()
            matches = 0
            for px in range(max(0, int((origin.x()+4)*scale)), min(image.width(), int((origin.x()+12)*scale))):
                for py in range(max(0, int(origin.y()*scale)), min(image.height(), int((origin.y()+2)*scale))):
                    background = before.pixelColor(px, py).getRgb()[:3]
                    actual = image.pixelColor(px, py).getRgb()[:3]
                    expected = tuple(round(fg * alpha + bg * (1-alpha))
                                     for fg, bg in zip(border.getRgb()[:3], background))
                    # Allow rounding/antialiasing, but require multiple pixels
                    # to change from the captured empty row toward the border.
                    if (max(abs(a-b) for a, b in zip(actual, background)) >= 20
                            and max(abs(a-b) for a, b in zip(actual, expected)) <= 8):
                        matches += 1
            return matches >= 4
        if args.playing:
            editor.synchronizeNow()
            playback.play()
            app.processEvents()
            assert playback.playing, 'Playback did not start'
            root.findChild(QObject, 'timeline').setProperty('followPlayback', False)
        observed = {}
        def observe_created(*_args):
            observed['note_created'] = perf_counter()
            # Playback's production noteCreated listener was registered first.
            # Sample immediately; mouseRelease may also flush later Qt work.
            if any(mixer.mix_frames(256)):
                observed['first_pcm'] = perf_counter()
        editor.noteCreated.connect(observe_created)
        def observe_frame():
            if 'note_created' in observed and 'frame' not in observed:
                observed['frame'] = perf_counter()
        root.frameSwapped.connect(observe_frame)
        def capture_window():
            started_readback = perf_counter()
            image = root.grabWindow()
            observed['readback_seconds'] = observed.get('readback_seconds', 0) + perf_counter() - started_readback
            return image
        def check_border(image, before, note):
            started_check = perf_counter()
            visible = border_visible(image, before, note)
            observed['pixel_check_seconds'] = observed.get('pixel_check_seconds', 0) + perf_counter() - started_check
            return visible
        for index in range(args.samples):
            # Reuse viewport cells only after undoing each independent sample.
            before_image = root.grabWindow() if args.verify_pixels else None
            point = canvas.mapToScene(QPointF(260 + (index % 24) * 24, 9)).toPoint()
            QTest.mousePress(root, Qt.LeftButton, pos=point)
            count = len(editor.state.notes)
            observed.clear()
            started = perf_counter()
            if profiler:
                profiler.enable()
            QTest.mouseRelease(root, Qt.LeftButton, pos=point)
            if profiler:
                profiler.disable()
            returned = perf_counter()
            assert len(editor.state.notes) == count + 1, f"Click {index} did not create a new note"
            pcm = mixer.mix_frames(256)
            # A cold sample may be prepared asynchronously. Include that wait
            # rather than silently measuring an empty output block as success.
            while not any(pcm) and perf_counter() - started < 2:
                QTest.qWait(1)
                pcm = mixer.mix_frames(256)
            prepared = observed.get('first_pcm', perf_counter())
            assert any(pcm), 'Audition produced no PCM'
            app.processEvents()
            captured = capture_window()
            if args.verify_pixels:
                assert len(editor.state.selected_notes) == 1, 'Expected exactly one newly selected note'
                new_note = editor.state.selected_notes[0]
                assert all(note.pitch != new_note.pitch for note in initial.notes), 'Pixel probe row must be empty before creation'
                assert not check_border(before_image, before_image, new_note), 'Empty row passed pixel verification'
                while not check_border(captured, before_image, new_note) and perf_counter() - started < 2:
                    QTest.qWait(1)
                    captured = capture_window()
                assert check_border(captured, before_image, new_note), 'New note border never appeared in pixels'
                observed['verified_pixels'] = perf_counter()
            capture_completed = perf_counter()
            frame_time = observed.get('frame')
            rows.append({'sample': index, 'release_return_ms': (returned-started)*1000,
                         'note_created_ms': (observed['note_created']-started)*1000,
                         'first_pcm_ms': (prepared-started)*1000,
                         'first_frame_swap_ms': (frame_time-started)*1000 if frame_time is not None else None,
                         'capture_readback_total_ms': observed['readback_seconds']*1000,
                         'window_capture_ms': (capture_completed-started)*1000})
            if args.platform == 'offscreen':
                # Preserve the old field for existing offscreen report readers.
                rows[-1]['offscreen_frame_ms'] = rows[-1]['window_capture_ms']
            if args.verify_pixels:
                rows[-1]['verified_note_pixels_ms'] = (observed['verified_pixels']-started)*1000
                rows[-1]['pixel_check_total_ms'] = observed['pixel_check_seconds']*1000
            playback.stopAudition()
            if not args.playing:
                mixer.release_all(ReleaseMode.STOP)
            mixer.mix_frames(4096)
            editor.undo()
            app.processEvents()
            assert editor.state.content == initial.content, 'One undo did not restore the fixture'
        assert mixer._sink is None, 'Hardware output was unexpectedly opened'
        blocked_start_requests = blocked_audio.call_count
    result = {'schema_version': 2,
              'scope': f'{args.platform}, memory PCM, no hardware output; first sample reported separately, excluded from summary',
              'runtime': runtime,
              'hardware_output_opened': False,
              'blocked_audio_start_requests': blocked_start_requests,
              'fixture_restored_after_every_sample': True,
              'audio_preparation': 'selected banks and probe pitch settled before sampling' if args.settled_audio else 'startup overlap allowed; excluding first sample does not prove completed warmup',
              'sample_warmup_wait_ms': warmup_ms,
              'frame_scope': 'first observed frameSwapped callback after note creation; null if absent; neither new-note visibility nor physical display presentation is asserted',
              'instrumentation_scope': 'capture_readback_total_ms and pixel_check_total_ms expose measurement work; subtracting them does not recover physical display latency',
              'initial_notes': args.notes, 'playing': args.playing,
              'overview_render_strategy_override': args.overview_render_strategy,
              'pixel_verification': 'v2: at least four changed border pixels matching opacity-aware color against empty-row baseline; upper bound including readback' if args.verify_pixels else 'not requested',
              'pcm_attribution': 'mixed playback and audition; not isolated audition onset' if args.playing else 'audition',
              'samples': rows}
    metrics = ['release_return_ms', 'note_created_ms', 'first_pcm_ms', 'first_frame_swap_ms',
               'window_capture_ms', 'capture_readback_total_ms']
    if args.platform == 'offscreen':
        metrics.append('offscreen_frame_ms')
    if args.verify_pixels:
        metrics.extend(['verified_note_pixels_ms', 'pixel_check_total_ms'])
    result['warm_summary_ms'] = summarize_samples(rows, metrics)
    if profiler:
        args.profile.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(str(args.profile))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'samples'}))
    faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    main()
