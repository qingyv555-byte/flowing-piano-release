from __future__ import annotations

import math
import base64
from pathlib import Path
import struct
import wave

import numpy as np
import pytest

import ai_piano_studio.audio.importer as importer_module
from ai_piano_studio.audio.importer import (
    AudioImportSession,
    AudioImportStatus,
    SUPPORTED_AUDIO_SUFFIXES,
    analyze_pcm,
    cleanup_candidates,
    describe_audio_error,
    estimate_tempo_from_onsets,
)


def _write_tone(path: Path, seconds: float = 1.2) -> None:
    sample_rate = 22_050
    count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = (
            struct.pack(
                "<h",
                int(0.35 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate)),
            )
            for index in range(count)
        )
        handle.writeframes(b"".join(frames))


_SHORT_SILENT_MP3 = (
    "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA/+M4wAAAAAAAAAAAAEluZm8AAAAPAAAAAwAAAbAA"
    "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq1dXV1dXV1dXV1dXV1dXV1dXV1dXV1dXV1dXV1dXV1dXV////////////"
    "////////////////////////////////AAAAAExhdmM1OC4xMwAAAAAAAAAAAAAAACQDkAAAAAAAAAGw9wrNaQAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/+MYxAAAAANIAAAAAExBTUUzLjEwMFVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV/+MYxDsAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV/+MYxHYAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
)
def _tone(pitch: float, seconds: float, sample_rate: int = 22_050) -> np.ndarray:
    count = int(seconds * sample_rate)
    time = np.arange(count, dtype=np.float32) / sample_rate
    return (0.35 * np.sin(2 * math.pi * pitch * time)).astype(np.float32)


def test_audio_import_contract_exposes_real_local_states_and_formats() -> None:
    assert [state.value for state in AudioImportStatus] == [
        "loading",
        "analyzing",
        "ready",
        "failed",
        "imported",
        "cancelled",
    ]
    assert SUPPORTED_AUDIO_SUFFIXES == (".wav", ".mp3")
    assert describe_audio_error("unsupported_format") == "当前只支持 WAV 和 MP3 音频。"
    assert describe_audio_error("file_too_long") == "音频时长超过当前版本允许的范围。"


def test_analyze_pcm_returns_a_melody_candidate_without_project_state() -> None:
    signal = np.concatenate([_tone(440, 0.55), _tone(523.25, 0.55)])

    result = analyze_pcm(signal, 22_050, sensitivity="standard")

    assert result["durationSeconds"] == pytest.approx(1.1, abs=0.001)
    assert result["detectedNotes"]
    pitches = {note["pitch"] for note in result["detectedNotes"]}
    assert 69 in pitches
    assert 72 in pitches
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["range"]["lowest"] <= result["range"]["highest"]


def test_analyze_pcm_handles_complex_audio_without_project_mutation_or_crash() -> None:
    sample_rate = 22_050
    time_axis = np.arange(sample_rate * 3, dtype=np.float32) / sample_rate
    signal = (
        0.22 * np.sin(2 * math.pi * 220.0 * time_axis)
        + 0.12 * np.sin(2 * math.pi * 659.25 * time_axis)
        + 0.015 * np.random.default_rng(7).normal(size=time_axis.size)
    ).astype(np.float32)

    result = analyze_pcm(signal, sample_rate, sensitivity="sensitive")

    assert result["durationSeconds"] == pytest.approx(3.0, abs=0.001)
    assert all(21 <= int(note["pitch"]) <= 108 for note in result["detectedNotes"])
    assert len(result["rawDetectedNotes"]) < 1000


def test_analyze_pcm_honors_cancellation_callback() -> None:
    signal = np.concatenate([_tone(440, 5.0), _tone(523.25, 5.0)])
    checks = 0

    def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(RuntimeError, match="analysis cancelled"):
        analyze_pcm(signal, 22_050, cancel_check=cancel_check)


def test_cleanup_removes_noise_merges_short_gaps_and_quantizes() -> None:
    raw = [
        {"pitch": 60, "startTime": 0.01, "duration": 0.03, "confidence": 0.95},
        {"pitch": 60, "startTime": 0.49, "duration": 0.48, "confidence": 0.90},
        {"pitch": 60, "startTime": 1.01, "duration": 0.40, "confidence": 0.88},
        {"pitch": 200, "startTime": 2.0, "duration": 0.6, "confidence": 0.99},
        {"pitch": 64, "startTime": 2.0, "duration": 0.6, "confidence": 0.12},
    ]

    cleaned = cleanup_candidates(raw, tempo_bpm=120, sensitivity="standard")

    assert len(cleaned) == 1
    assert cleaned[0]["pitch"] == 60
    assert cleaned[0]["startTime"] == 0.5
    assert cleaned[0]["duration"] >= 0.75


def test_tempo_estimate_uses_onset_spacing_and_has_safe_fallback() -> None:
    assert estimate_tempo_from_onsets([0.0, 0.5, 1.0, 1.5]) == 120
    assert estimate_tempo_from_onsets([]) == 120
    assert estimate_tempo_from_onsets([0.0, 10.0]) == 60


def test_audio_import_session_decodes_wav_off_the_ui_thread(qapp, qtbot, tmp_path) -> None:
    path = tmp_path / "melody.wav"
    _write_tone(path)
    session = AudioImportSession()

    assert session.start(str(path)) is True
    with qtbot.waitSignal(session.finished, timeout=10_000):
        pass

    assert session.status == AudioImportStatus.READY.value
    assert session.sourceDisplayName == "melody.wav"
    assert session.sampleRate == 22_050
    assert session.channels == 1
    assert session.detectedNotes
    assert session.errorCode == ""


def test_audio_import_session_enters_analyzing_before_worker_runs(qapp, tmp_path) -> None:
    path = tmp_path / "analysis-start.wav"
    _write_tone(path, seconds=0.4)
    session = AudioImportSession()

    try:
        assert session.start(str(path)) is True
        assert session.status == AudioImportStatus.ANALYZING.value
    finally:
        session.shutdown()


def test_audio_import_session_repeated_runs_retire_decoder_thread_cleanly(
    qapp, qtbot, tmp_path
) -> None:
    path = tmp_path / "repeated.wav"
    _write_tone(path, seconds=0.4)
    session = AudioImportSession()

    for _ in range(3):
        assert session.start(str(path)) is True
        with qtbot.waitSignal(session.finished, timeout=10_000):
            pass
        assert session.status == AudioImportStatus.READY.value
        assert session._thread is None or not session._thread.isRunning()


def test_audio_import_session_reports_unsupported_format_without_worker(
    qapp, tmp_path
) -> None:
    path = tmp_path / "song.flac"
    path.write_bytes(b"not an accepted format")
    session = AudioImportSession()

    assert session.start(str(path)) is False
    assert session.status == AudioImportStatus.FAILED.value
    assert session.errorCode == "unsupported_format"
    assert session.errorMessage == "当前只支持 WAV 和 MP3 音频。"


def test_audio_import_session_reports_corrupt_wav_and_can_retry_with_valid_wav(
    qapp, qtbot, tmp_path
) -> None:
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"RIFF-not-a-valid-wave")
    valid = tmp_path / "retry.wav"
    _write_tone(valid, seconds=0.4)
    session = AudioImportSession()

    assert session.start(str(corrupt)) is True
    with qtbot.waitSignal(session.finished, timeout=10_000):
        pass
    assert session.status == AudioImportStatus.FAILED.value
    assert session.errorCode == "decode_failed"

    assert session.start(str(valid)) is True
    qtbot.waitUntil(
        lambda: session.status == AudioImportStatus.READY.value,
        timeout=10_000,
    )
    assert session.detectedNotes
    qtbot.waitUntil(lambda: session._thread is None, timeout=5_000)


def test_audio_import_session_rejects_oversize_input_before_worker(
    qapp, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "oversize.wav"
    _write_tone(path, seconds=0.4)
    monkeypatch.setattr(importer_module, "MAX_AUDIO_FILE_BYTES", 32)
    session = AudioImportSession()

    assert session.start(str(path)) is False
    assert session.status == AudioImportStatus.FAILED.value
    assert session.errorCode == "file_too_large"
    assert session._thread is None


def test_audio_import_session_reaches_analysis_for_mp3_decoder(qapp, qtbot, tmp_path) -> None:
    path = tmp_path / "silent.mp3"
    path.write_bytes(base64.b64decode(_SHORT_SILENT_MP3))
    session = AudioImportSession()

    assert session.start(str(path)) is True
    with qtbot.waitSignal(session.finished, timeout=10_000):
        pass

    # The tiny fixture is intentionally silent: decode succeeds and the
    # melody stage reports an empty result rather than a format/decode error.
    assert session.status == AudioImportStatus.FAILED.value
    assert session.errorCode == "analysis_failed"


def test_audio_import_session_can_cancel_without_leaving_a_running_thread(
    qapp, qtbot, tmp_path
) -> None:
    path = tmp_path / "long.wav"
    _write_tone(path, seconds=5.0)
    session = AudioImportSession()

    assert session.start(str(path)) is True
    session.cancel()
    with qtbot.waitSignal(session.finished, timeout=10_000):
        pass

    assert session.status == AudioImportStatus.CANCELLED.value
    assert session._thread is None or not session._thread.isRunning()


def test_audio_import_session_serializes_a_restart_after_cancellation(
    qapp, qtbot, tmp_path
) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write_tone(first, seconds=5.0)
    _write_tone(second, seconds=0.4)
    session = AudioImportSession()

    assert session.start(str(first)) is True
    qtbot.waitUntil(
        lambda: session.status == AudioImportStatus.ANALYZING.value,
        timeout=5_000,
    )
    assert session.start(str(second)) is True

    qtbot.waitUntil(
        lambda: (
            session.status == AudioImportStatus.READY.value
            and session.sourceDisplayName == second.name
        ),
        timeout=10_000,
    )
    assert session.detectedNotes
    qtbot.waitUntil(
        lambda: session._thread is None,
        timeout=5_000,
    )


def test_audio_import_session_shutdown_waits_for_worker_teardown(qapp, qtbot, tmp_path) -> None:
    path = tmp_path / "shutdown.wav"
    _write_tone(path, seconds=5.0)
    session = AudioImportSession()

    assert session.start(str(path)) is True
    qtbot.waitUntil(
        lambda: session.status == AudioImportStatus.ANALYZING.value,
        timeout=5_000,
    )
    session.shutdown()

    assert session.status == AudioImportStatus.CANCELLED.value
    assert session._thread is None


def test_audio_import_session_twenty_cancel_restart_cycles_stay_clean(
    qapp, qtbot, tmp_path
) -> None:
    path = tmp_path / "stress.wav"
    _write_tone(path, seconds=1.2)
    session = AudioImportSession()

    for _ in range(20):
        assert session.start(str(path)) is True
        qtbot.waitUntil(
            lambda: session.status == AudioImportStatus.ANALYZING.value,
            timeout=5_000,
        )
        session.cancel()
        qtbot.waitUntil(
            lambda: session.status == AudioImportStatus.CANCELLED.value
            and session._thread is None,
            timeout=10_000,
        )
        assert session.start(str(path)) is True
        qtbot.waitUntil(
            lambda: session.status == AudioImportStatus.READY.value,
            timeout=10_000,
        )
        qtbot.waitUntil(lambda: session._thread is None, timeout=5_000)
        assert session.detectedNotes
