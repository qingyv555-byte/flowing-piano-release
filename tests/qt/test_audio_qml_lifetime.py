"""An active loop must not damage unrelated QML during cyclic collection."""
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent

import pytest


@pytest.mark.parametrize("window_action", [
    "test_background_settings_and_autosave_entries_fit_1280_by_720",
    "test_top_bar_extracts_commands_and_uses_overflow",
    "test_stage_popovers_fit_close_and_return_space_to_playback",
])
def test_active_loop_collection_preserves_later_qml_windows(tmp_path, window_action):
    root = Path(__file__).resolve().parents[2]
    script = dedent('''
        import gc
        import os
        from pathlib import Path
        import sys
        from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QThreadPool
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtWidgets import QApplication
        from pytest import MonkeyPatch
        import ai_piano_studio.editor.controller as editor_controller
        from ai_piano_studio.persistence.project_library import ProjectLibrary
        from PySide6.QtMultimedia import QAudioSink, QAudioSource, QMediaPlayer, QSoundEffect
        from tests.qt.test_audio_player_realtime import (
            test_selected_loop_audition_reuses_the_sink_after_natural_completion as exercise_loop,
        )
        from tests.qt import test_editor_ui
        if sys.argv[2].startswith('test_stage_'):
            from tests.qt.test_playful_workspace import test_stage_popovers_fit_close_and_return_space_to_playback
            exercise_window = lambda app: test_stage_popovers_fit_close_and_return_space_to_playback(app, 1280, 720)
        else:
            exercise_window = getattr(test_editor_ui, sys.argv[2])
        unhandled = []
        original_excepthook = sys.excepthook
        sys.excepthook = lambda kind, error, tb: unhandled.append(f'{kind.__name__}: {error}')

        home = Path(sys.argv[1])
        QQuickStyle.setStyle('Basic')
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope, str(home / 'settings'))
        app = QApplication([])
        def settle():
            assert QThreadPool.globalInstance().waitForDone(5000)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            gc.collect()

        print(f'isolated lifetime regression pid={os.getpid()}', flush=True)
        with MonkeyPatch.context() as patch:
            physical_calls = []
            def reject_physical_start(*args, **kwargs):
                physical_calls.append('physical audio start')
                raise AssertionError('Physical audio is forbidden in this regression')
            for kind, method in ((QAudioSink, 'start'), (QAudioSource, 'start'),
                                 (QMediaPlayer, 'play'), (QSoundEffect, 'play')):
                patch.setattr(kind, method, reject_physical_start)
            patch.setattr(editor_controller, 'ProjectLibrary',
                          lambda: ProjectLibrary(home / 'projects'))
            exercise_loop(app, patch)
            settle()
            for iteration in range(20):
                exercise_window(app)
                settle()
            assert not physical_calls, physical_calls
        sys.excepthook = original_excepthook
        assert not unhandled, unhandled
        print('active loop and 20 QML window lifetimes passed', flush=True)
    ''')
    environment = os.environ.copy()
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['PYTHONPATH'] = os.pathsep.join((str(root / 'src'), str(root)))
    environment['LOCALAPPDATA'] = str(tmp_path / 'appdata')
    environment['TEMP'] = str(tmp_path)
    environment['TMP'] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path), window_action],
        cwd=root, env=environment, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    assert result.returncode == 0, (
        f'lifetime child exited {result.returncode}\n'
        f'{result.stdout[-3000:]}\n{result.stderr[-6000:]}'
    )
