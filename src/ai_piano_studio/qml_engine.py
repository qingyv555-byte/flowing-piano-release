"""Defer native QML destruction while retaining cyclic Python context state."""
from functools import partial
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtQml import QQmlApplicationEngine
from shiboken6 import isValid


_pending_engines = {}


def _release_engine(key):
    _pending_engines.pop(key, None)


def _native_destroyed(key, *_):
    # QObject's destroyed signal precedes child destruction. Retain the Python
    # context through the entire native destructor, then release next turn.
    QTimer.singleShot(0, partial(_release_engine, key))


class ApplicationQmlEngine(QQmlApplicationEngine):
    """Preserve contexts until Qt can destroy their views outside Python GC."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown_callbacks: list[Callable[[], None]] = []
        self._shutdown_started = False
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.deleteLater)

    def add_shutdown_callback(self, callback: Callable[[], None]) -> None:
        """Stop owned services while the engine and their Qt parents are live."""
        self._shutdown_callbacks.append(callback)

    def event(self, event):
        if event.type() == QEvent.Type.DeferredDelete and not self._shutdown_started:
            self._shutdown_started = True
            # Shutdown may run a bounded nested Qt loop (audio import cancellation).
            # Do this before entering the native destructor, and retain every
            # callback/context reference until that entire destructor completes.
            for shutdown in self._shutdown_callbacks:
                shutdown()
        return super().event(event)

    def __del__(self, _valid=isValid, _pending=_pending_engines,
                _destroyed=_native_destroyed, _partial=partial,
                _direct=Qt.ConnectionType.DirectConnection):
        if _valid(self):
            key = id(self)
            _pending[key] = self
            self.destroyed.connect(_partial(_destroyed, key), _direct)
            self.deleteLater()
