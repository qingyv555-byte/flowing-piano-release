"""Engine teardown must stop nested native worker threads before their parents."""
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent

import pytest


@pytest.mark.parametrize('disposal', ['gc', 'deleteLater', 'quit'])
def test_engine_stops_owned_services_before_native_child_destruction(disposal):
    script = dedent('''
        import gc
        import sys
        import weakref
        from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Slot
        from PySide6.QtGui import QGuiApplication
        from ai_piano_studio.qml_engine import ApplicationQmlEngine, _pending_engines
        app = QGuiApplication([])
        events, errors = [], []
        sys.excepthook = lambda kind, error, traceback: errors.append(str(error))
        class Service(QObject):
            def __init__(self, engine):
                super().__init__(engine)
                self.engine = engine
                self.session = QObject(self)
                self.thread = QThread(self.session)
                self.thread.start()
            def shutdown(self):
                events.append('shutdown')
                self.thread.quit()
                assert self.thread.wait(3000)
            @Slot()
            def record(self):
                events.append('qml-destruction')
                assert not self.thread.isRunning()
        engine = ApplicationQmlEngine()
        service = Service(engine)
        engine._service = service
        engine.add_shutdown_callback(service.shutdown)
        engine.rootContext().setContextProperty('backend', service)
        engine.loadData(b'import QtQuick; Item { Component.onDestruction: backend.record() }')
        assert engine.rootObjects()
        engine_ref = weakref.ref(engine)
        service_ref = weakref.ref(service)
        disposal = sys.argv[1]
        if disposal == 'deleteLater':
            engine.deleteLater()
        elif disposal == 'quit':
            QTimer.singleShot(0, app.quit)
            assert app.exec() == 0
        del service, engine
        gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        gc.collect()
        assert not errors, errors
        assert events == ['shutdown', 'qml-destruction'], events
        assert engine_ref() is None and service_ref() is None
        assert not _pending_engines
    ''')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', script, disposal],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_audio_import_is_cancelled_before_engine_child_teardown(tmp_path):
    script = dedent('''
        import gc
        from pathlib import Path
        import sys
        import wave
        import weakref
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtMultimedia import QAudioSink, QAudioSource, QMediaPlayer, QSoundEffect
        from ai_piano_studio.qml_engine import ApplicationQmlEngine, _pending_engines
        from ai_piano_studio.audio.import_controller import AudioImportController
        from ai_piano_studio.domain.project import ProjectState
        import ai_piano_studio.editor.controller as editor_module
        from ai_piano_studio.persistence.project_library import ProjectLibrary
        folder = Path(sys.argv[1])
        editor_module.ProjectLibrary = lambda: ProjectLibrary(folder / 'projects')
        def forbid_output(*args, **kwargs):
            raise AssertionError('Physical audio output is forbidden')
        QAudioSink.start = QAudioSource.start = forbid_output
        QMediaPlayer.play = QSoundEffect.play = forbid_output
        app = QGuiApplication([])
        errors, weak_engines = [], []
        sys.excepthook = lambda kind, error, traceback: errors.append(str(error))
        path = folder / 'source.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            stream.writeframes(bytes(88200))
        for _ in range(10):
            engine = ApplicationQmlEngine()
            editor = editor_module.EditorController(ProjectState())
            controller = AudioImportController(editor, parent=engine)
            engine._controller = controller
            controller._engine = engine
            assert controller.startAnalysis(str(path))
            weak_engines.append(weakref.ref(engine))
            del controller, editor, engine
            gc.collect()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            gc.collect()
        assert not errors, errors
        assert all(reference() is None for reference in weak_engines)
        assert not _pending_engines
    ''')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
