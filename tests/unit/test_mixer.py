from __future__ import annotations


def test_retired_sink_state_signal_cannot_clear_current_output_readiness(qapp, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtMultimedia import QAudio
    from ai_piano_studio.audio import mixer as module

    class Sink(QObject):
        stateChanged = Signal(object)

        def setBufferSize(self, size):
            self.buffer_size = size
        def __init__(self, *args):
            super().__init__()
        def start(self, device):
            self.stateChanged.emit(QAudio.State.ActiveState)
        def state(self):
            return QAudio.State.ActiveState
        def stop(self):
            pass
        def deleteLater(self):
            pass

    monkeypatch.setattr(module, 'QAudioSink', Sink)
    mixer = module.PianoVoiceMixer()
    mixer.start_audio()
    retired = mixer._sink
    mixer.stop_audio()
    mixer.start_audio()
    retired.stateChanged.emit(QAudio.State.StoppedState)
    assert mixer.is_audio_ready()
    mixer.stop_audio()


def test_delayed_stopped_signal_does_not_mark_an_active_sink_unready(qapp, monkeypatch):
    """Windows can deliver a stale stopped notification after start() returns."""
    from PySide6.QtCore import QObject, Signal

    class Sink(QObject):
        stateChanged = Signal(object)

        def setBufferSize(self, size):
            self.buffer_size = size

        def __init__(self, *_args: object) -> None:
            super().__init__()

        def start(self, _device: object) -> None:
            pass

        def state(self):
            return QAudio.State.ActiveState

        def error(self):
            return QAudio.Error.NoError

        def stop(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    monkeypatch.setattr(mixer_module, "QAudioSink", Sink)
    mixer = PianoVoiceMixer()
    mixer.start_audio()

    mixer._sink.stateChanged.emit(QAudio.State.StoppedState)

    assert mixer.is_audio_ready() is True
    mixer.stop_audio()


def test_sink_state_readiness_accepts_equivalent_qt_enum_wrappers() -> None:
    """PySide may expose a fresh enum wrapper for a real audio-state value."""
    equivalent_active_state = type("QtStateWrapper", (), {"name": "ActiveState"})()

    assert PianoVoiceMixer._sink_state_is_ready(equivalent_active_state) is True


from types import SimpleNamespace

import pytest
from PySide6.QtMultimedia import QAudio

from ai_piano_studio.audio import mixer as mixer_module
from ai_piano_studio.audio.mixer import PianoVoiceMixer


class RecordingSink:
    def __init__(self, *_args: object) -> None:
        self.buffer_sizes: list[int] = []
        self.free_bytes = 0
        self.started_devices: list[object] = []
        self.suspend_calls = 0
        self.resume_calls = 0
        self.volumes: list[float] = []
        self._state = QAudio.State.ActiveState

    def setBufferSize(self, size: int) -> None:
        self.buffer_sizes.append(size)

    def bufferSize(self) -> int:
        return self.buffer_sizes[-1]

    def bytesFree(self) -> int:
        return self.free_bytes

    def start(self, device: object) -> None:
        self.started_devices.append(device)

    def state(self):
        return self._state

    def suspend(self) -> None:
        self.suspend_calls += 1

    def resume(self) -> None:
        self.resume_calls += 1

    def setVolume(self, volume: float) -> None:
        self.volumes.append(volume)

    def stop(self) -> None:
        pass

    def deleteLater(self) -> None:
        pass


def test_sink_open_error_is_reported_instead_of_preparing_forever(qapp, monkeypatch):
    class FailedSink(RecordingSink):
        def state(self):
            return QAudio.State.StoppedState
        def error(self):
            return QAudio.Error.OpenError
    monkeypatch.setattr(mixer_module, 'QAudioSink', FailedSink)
    mixer = PianoVoiceMixer()
    with pytest.raises(RuntimeError, match='audio output'):
        mixer.start_audio()
    assert mixer._sink is None
    assert not mixer.is_audio_ready()


def test_qt_audio_error_enum_is_compared_by_name(monkeypatch):
    class QtAudioErrorSink(RecordingSink):
        def error(self):
            return type("QtAudioError", (), {"name": "NoError"})()

    monkeypatch.setattr(mixer_module, "QAudioSink", QtAudioErrorSink)
    mixer = PianoVoiceMixer()
    mixer.start_audio()
    assert mixer._sink is not None
    mixer.stop_audio()


def install_recording_sink(monkeypatch) -> RecordingSink:
    sinks: list[RecordingSink] = []

    def create_sink(*args: object) -> RecordingSink:
        sink = RecordingSink(*args)
        sinks.append(sink)
        return sink

    monkeypatch.setattr(mixer_module, "QAudioSink", create_sink)
    monkeypatch.setattr(
        mixer_module, "QMediaDevices", SimpleNamespace(defaultAudioOutput=lambda: object())
    )
    return sinks


def test_start_audio_configures_200ms_stereo_int16_buffer(monkeypatch):
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=48_000)
    mixer.start_audio(buffer_milliseconds=200)
    sink = sinks[0]
    assert sink.buffer_sizes == [38400]
    assert len(sink.started_devices) == 1
    assert sink.started_devices[0]._mixer == mixer
    assert sink.started_devices[0].thread() != mixer.thread()
    mixer.stop_audio()


def test_audition_default_bounds_buffer_and_restart_preserves_budget(monkeypatch):
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=48_000)
    mixer.start_audio()
    assert sinks[0].buffer_sizes == [7680]  # 40 ms, stereo int16
    assert mixer.output_latency_seconds() == pytest.approx(0.04)
    mixer.stop_audio()
    mixer.start_audio()
    assert sinks[-1].buffer_sizes == [7680]


def test_start_audio_reports_active_sink_readiness(monkeypatch):
    install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=48_000)

    mixer.start_audio(buffer_milliseconds=80)

    assert mixer.is_audio_ready() is True
    mixer.stop_audio()
    assert mixer.is_audio_ready() is False


def test_idle_pull_sink_is_ready_for_the_first_audition(qapp, monkeypatch):
    """An already-started pull sink may idle while it waits for the next notes."""
    from PySide6.QtCore import QObject, Signal

    class IdleSink(QObject):
        stateChanged = Signal(object)

        def setBufferSize(self, size):
            self.buffer_size = size

        def __init__(self, *_args: object) -> None:
            super().__init__()

        def start(self, _device: object) -> None:
            self.stateChanged.emit(QAudio.State.IdleState)

        def state(self):
            return QAudio.State.IdleState

        def error(self):
            return QAudio.Error.NoError

        def stop(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    monkeypatch.setattr(mixer_module, "QAudioSink", IdleSink)
    mixer = PianoVoiceMixer()

    mixer.start_audio()

    assert mixer.is_audio_ready() is True
    mixer.stop_audio()


@pytest.mark.parametrize("buffer_milliseconds", [20, 40, 60, 80, 100, 200])
def test_output_latency_matches_configured_buffer_budget(
    monkeypatch, buffer_milliseconds: int
) -> None:
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=48_000)

    mixer.start_audio(buffer_milliseconds=buffer_milliseconds)
    sinks[0].free_bytes = sinks[0].buffer_sizes[0] // 2

    assert mixer.output_latency_seconds() == pytest.approx(buffer_milliseconds / 1_000)


def test_pause_resume_and_volume_control_an_active_sink(monkeypatch):
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer()
    mixer.start_audio()
    mixer.pause_audio()
    mixer.resume_audio()
    mixer.set_volume(0.35)
    sink = sinks[0]
    assert (sink.suspend_calls, sink.resume_calls, sink.volumes) == (1, 1, [0.35])


def test_master_output_gain_boosts_quiet_pcm_before_soft_limit():
    import numpy as np

    from ai_piano_studio.audio.mixer import MASTER_OUTPUT_GAIN, _stereo_pcm
    from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2

    baseline = _stereo_pcm(
        np.array([1_000.0 / 32_767]),
        np.array([1_000.0 / 32_767]),
        ACOUSTIC_PIANO_V2.peak_amplitude,
    )
    boosted = _stereo_pcm(
        np.array([1_000.0 / 32_767 * MASTER_OUTPUT_GAIN]),
        np.array([1_000.0 / 32_767 * MASTER_OUTPUT_GAIN]),
        ACOUSTIC_PIANO_V2.peak_amplitude,
    )

    base_sample = memoryview(baseline).cast("h")[0]
    boosted_sample = memoryview(boosted).cast("h")[0]
    assert MASTER_OUTPUT_GAIN == pytest.approx(4.0)
    assert boosted_sample == base_sample * 4


def test_deleted_sink_does_not_block_the_next_audio_start(monkeypatch):
    """A Qt-deleted sink must not leave playback permanently silent."""
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer()
    mixer.start_audio()

    def raise_deleted() -> None:
        raise RuntimeError("Internal C++ object already deleted")

    sinks[0].stop = raise_deleted  # type: ignore[method-assign]

    mixer.stop_audio()
    mixer.start_audio()

    assert len(sinks) == 2
    assert len(sinks[1].started_devices) == 1
    assert sinks[1].started_devices[0]._mixer == mixer
    assert sinks[1].started_devices[0].thread() != mixer.thread()
    mixer.stop_audio()


def test_start_audio_replaces_an_invalidated_existing_sink(monkeypatch):
    """A device-invalidated sink must not leave the next audition preparing silently."""
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer()
    mixer.start_audio(buffer_milliseconds=80)
    sinks[0]._state = QAudio.State.StoppedState
    sinks[0].error = lambda: QAudio.Error.IOError  # type: ignore[attr-defined]

    mixer.start_audio(buffer_milliseconds=80)

    assert len(sinks) == 2
    assert len(sinks[1].started_devices) == 1
    assert sinks[1].started_devices[0]._mixer == mixer
    assert sinks[1].started_devices[0].thread() != mixer.thread()
    assert mixer.is_audio_ready() is True
    mixer.stop_audio()


def test_discarding_no_voice_ids_does_not_reset_the_active_sink(monkeypatch):
    sinks = install_recording_sink(monkeypatch)
    mixer = PianoVoiceMixer()
    mixer.start_audio()
    reset_calls: list[bool] = []
    stop_calls: list[bool] = []
    sinks[0].reset = lambda: reset_calls.append(True)  # type: ignore[attr-defined]
    sinks[0].stop = lambda: stop_calls.append(True)  # type: ignore[method-assign]

    mixer.discard_voice_ids_and_reset_output(())

    assert reset_calls == []
    assert stop_calls == []
    assert len(sinks) == 1


def test_mix_io_device_advertises_a_bounded_continuous_stream() -> None:
    """Fails if a pull sink sees the mixer device as an exhausted empty file."""
    # The device borrows its mixer; retain the owner for the full read.
    mixer = PianoVoiceMixer()
    device = mixer.io_device
    available = device.bytesAvailable()

    assert device.isSequential()
    assert 0 < available <= 4_096
    assert not device.atEnd()
