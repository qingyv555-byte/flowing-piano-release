"""Ownership and teardown of the independent Qt PCM output thread."""
import pytest
import sys
from PySide6.QtCore import QObject, QThread, QCoreApplication, QEvent
from shiboken6 import isValid

from ai_piano_studio.audio.output_thread import ThreadedAudioSink, _ACTIVE_OUTPUTS


class Sink(QObject):
    def start(self, device):
        assert QThread.currentThread() == self.thread() == device.thread()

    def stop(self):
        assert QThread.currentThread() == self.thread()


def test_repeated_output_recreation_retires_threads_and_owners(qapp):
    original = set(_ACTIVE_OUTPUTS)
    original_interval = sys.getswitchinterval()
    owner = QObject()
    threads = []
    for _ in range(50):
        sink = ThreadedAudioSink(lambda parent: Sink(parent), lambda parent: QObject(parent), owner)
        threads.append(sink._thread)
        sink.start(None)
        sink.stop()
        sink.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert set(_ACTIVE_OUTPUTS) == original
    assert sys.getswitchinterval() == pytest.approx(original_interval)
    assert all(not thread.isRunning() for thread in threads)
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_owner_destruction_stops_output_even_without_explicit_close(qapp):
    owner = QObject()
    sink = ThreadedAudioSink(lambda parent: Sink(parent), lambda parent: QObject(parent), owner)
    thread, native = sink._thread, sink._sink
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not thread.isRunning()
    assert sink not in _ACTIVE_OUTPUTS
    assert not isValid(native)


def test_construction_failure_retires_worker(qapp):
    original = set(_ACTIVE_OUTPUTS)
    owner = QObject()
    def fail(parent):
        raise RuntimeError('controlled output failure')
    with pytest.raises(RuntimeError, match='controlled output failure'):
        ThreadedAudioSink(fail, lambda parent: QObject(parent), owner)
    assert set(_ACTIVE_OUTPUTS) == original
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
