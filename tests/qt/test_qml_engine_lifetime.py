"""QML destruction callbacks must run while Python context state still exists."""
import gc
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent
import weakref
from threading import Thread, get_ident

import pytest

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QUrl, Slot

from ai_piano_studio.__main__ import QQmlApplicationEngine
from ai_piano_studio.qml_engine import _pending_engines


@pytest.mark.parametrize('collection_thread', ['gui', 'worker'])
def test_cyclic_engine_destroys_qml_before_python_context_is_cleared(qapp, monkeypatch, collection_thread):
    values = []
    errors = []
    engines = []
    destruction_threads = []
    gui_thread = get_ident()
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(str(error)))

    class Model(QObject):
        def __init__(self):
            super().__init__()
            self._value = 17

        @Property(int, constant=True)
        def value(self):
            return self._value

        @Slot()
        def readOnDestruction(self):
            values.append(self.value)
            destruction_threads.append(get_ident())

    def create_cycle():
        model = Model()
        engine = QQmlApplicationEngine()
        engine._context_model = model
        model._engine = engine
        engine.rootContext().setContextProperty("backend", model)
        engine.loadData(b"""
            import QtQuick
            Item {
                property int currentValue: backend.value
                Component.onDestruction: backend.readOnDestruction()
            }
        """, QUrl("file:///lifetime-regression.qml"))
        assert engine.rootObjects()
        engines.append(weakref.ref(engine))

    for _ in range(20):
        create_cycle()
        if collection_thread == 'worker':
            collector = Thread(target=gc.collect)
            collector.start()
            collector.join(timeout=5)
            assert not collector.is_alive()
        else:
            gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
        gc.collect()
    assert not errors, errors
    assert values == [17] * 20
    assert destruction_threads == [gui_thread] * 20
    assert all(engine() is None for engine in engines)
    assert not _pending_engines


def test_application_quit_destroys_qml_with_live_python_context():
    # Exercise the real application event-loop exit in a separate process;
    # quitting pytest's shared QApplication would corrupt later UI tests.
    script = dedent('''
        import gc
        import sys
        import weakref
        from PySide6.QtCore import QObject, QTimer, Slot
        from PySide6.QtGui import QGuiApplication
        from shiboken6 import isValid
        from ai_piano_studio.qml_engine import ApplicationQmlEngine, _pending_engines
        app = QGuiApplication([])
        errors, values = [], []
        sys.excepthook = lambda kind, error, traceback: errors.append(str(error))
        class Model(QObject):
            def __init__(self):
                super().__init__()
                self.value = 17
            @Slot()
            def record(self):
                values.append(self.value)
        model = Model()
        engine = ApplicationQmlEngine()
        engine._context_model = model
        model._engine = engine
        engine.rootContext().setContextProperty('backend', model)
        engine.loadData(b'import QtQuick; Item { Component.onDestruction: backend.record() }')
        assert engine.rootObjects()
        engine_ref = weakref.ref(engine)
        QTimer.singleShot(0, app.quit)
        assert app.exec() == 0
        assert not isValid(engine)
        del engine, model
        gc.collect()
        assert values == [17], values
        assert not errors, errors
        assert engine_ref() is None
        assert not _pending_engines
    ''')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    result = subprocess.run([sys.executable, '-c', script],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
