"""Qt output ownership isolated from the GUI event loop.

Only sink/device operations run here. Sample preparation stays on the producer;
the device reads already prepared mixer commands. No GUI callbacks are invoked.
"""
from threading import Event, Lock
import atexit
import sys
from weakref import ref
from shiboken6 import isValid

from PySide6.QtCore import QObject, QThread, Signal, Slot, QCoreApplication


_ACTIVE_OUTPUTS = set()
_OUTPUT_LIFETIME_LOCK = Lock()
_previous_switch_interval = None
_audio_switch_interval = None


def _close_outputs():
    # Python globals must not be collected while a native QThread still runs.
    for output in tuple(_ACTIVE_OUTPUTS):
        output.close()


atexit.register(_close_outputs)


def _retain_output(output):
    global _previous_switch_interval, _audio_switch_interval
    with _OUTPUT_LIFETIME_LOCK:
        if not _ACTIVE_OUTPUTS:
            _previous_switch_interval = sys.getswitchinterval()
            _audio_switch_interval = min(_previous_switch_interval, .001)
            # NumPy releases the GIL. A busy GUI Python callback otherwise gets
            # a fresh 5ms quantum between each small mixing operation, starving
            # dense chords despite the dedicated Qt output event loop.
            sys.setswitchinterval(_audio_switch_interval)
        _ACTIVE_OUTPUTS.add(output)


def _release_output(output):
    with _OUTPUT_LIFETIME_LOCK:
        _ACTIVE_OUTPUTS.discard(output)
        if not _ACTIVE_OUTPUTS and _previous_switch_interval is not None:
            if abs(sys.getswitchinterval() - _audio_switch_interval) < 1e-9:
                sys.setswitchinterval(_previous_switch_interval)


class _Dispatch(QObject):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.requested.connect(self.run)

    @Slot(object)
    def run(self, request):
        operation, result, done = request
        try:
            result.append((True, operation()))
        except BaseException as error:
            result.append((False, error))
        finally:
            done.set()


class ThreadedAudioSink(QObject):
    """Synchronous control facade; pull notifications use the output QThread."""
    stateChanged = Signal(object)

    def __init__(self, factory, device_factory, parent):
        super().__init__(parent)
        self._thread = QThread()
        self._thread.setObjectName('Piano PCM output')
        self._dispatch = _Dispatch()
        self._dispatch.moveToThread(self._thread)
        self._thread.finished.connect(self._dispatch.deleteLater)
        self._thread.start()
        self._closed = False
        _retain_output(self)
        try:
            self._sink, self._device = self._invoke(lambda: (factory(self._dispatch), device_factory(self._dispatch)))
            signal = self._invoke(lambda: getattr(self._sink, 'stateChanged', None))
            if signal is not None:
                self._invoke(lambda: signal.connect(self.stateChanged.emit))
        except BaseException:
            self.close()
            raise
        # A child receiver can already be disconnected during parent teardown;
        # retain a plain closure until the parent announces destruction.
        self._owner = ref(parent)
        self._owner_destroyed = lambda: self.close(owner_destroyed=True)
        parent.destroyed.connect(self._owner_destroyed)
        application = QCoreApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.close)

    def _invoke(self, operation):
        if QThread.currentThread() == self._thread:
            return operation()
        if self._closed:
            raise RuntimeError('audio output thread is closed')
        result, done = [], Event()
        self._dispatch.requested.emit((operation, result, done))
        # This is a control call, never the PCM pull path. Do not pump GUI events
        # while waiting (transport changes must not re-enter preparation).
        done.wait()
        success, value = result[0]
        if not success:
            raise value
        return value

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        value = self._invoke(lambda: getattr(self._sink, name))
        if callable(value):
            return lambda *args, **kwargs: self._invoke(lambda: getattr(self._sink, name)(*args, **kwargs))
        return value

    def start(self, _device):
        return self._invoke(lambda: self._sink.start(self._device))

    @property
    def is_closed(self):
        return self._closed

    def deleteLater(self):
        self.close()
        super().deleteLater()

    @Slot()
    def close(self, *, owner_destroyed=False):
        if self._closed:
            return
        try:
            if hasattr(self, '_sink') and isValid(self._dispatch):
                self._invoke(lambda: self._sink.deleteLater())
        finally:
            self._closed = True
            self._thread.quit()
            self._thread.wait()
            _release_output(self)
            owner = self._owner() if hasattr(self, '_owner') else None
            if not owner_destroyed and owner is not None and isValid(owner):
                owner.destroyed.disconnect(self._owner_destroyed)
