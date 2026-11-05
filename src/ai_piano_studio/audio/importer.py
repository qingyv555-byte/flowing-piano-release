from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
import logging
import math
from pathlib import Path
from typing import Any
import wave

import numpy as np
from shiboken6 import isValid
from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QMetaObject,
    Property,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
)


logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_SUFFIXES = (".wav", ".mp3")
MAX_AUDIO_FILE_BYTES = 500 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 15 * 60
ANALYSIS_SAMPLE_RATE = 22_050
_FRAME_SIZE = 2048
_HOP_SIZE = _FRAME_SIZE // 2
_MIN_PITCH = 21
_MAX_PITCH = 108
EXTRACTION_MODES = ("auto", "melody", "bass", "simplified")


@dataclass(frozen=True)
class ExtractionControls:
    """Deterministic, user-facing post-processing controls for audio notes."""

    mode: str = "auto"
    min_note_duration: float = 0.08
    noise_threshold: float = 0.0
    quantize_strength: float = 1.0
    simplify_density: float = 0.5
    pitch_min: int = _MIN_PITCH
    pitch_max: int = _MAX_PITCH


def normalize_extraction_controls(
    *,
    mode: str = "auto",
    min_note_duration: float = 0.08,
    noise_threshold: float = 0.0,
    quantize_strength: float = 1.0,
    simplify_density: float = 0.5,
    pitch_min: int = _MIN_PITCH,
    pitch_max: int = _MAX_PITCH,
) -> ExtractionControls:
    """Clamp UI values to safe musical ranges without mutating project state."""

    def _float(value: object, default: float, low: float, high: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        if not math.isfinite(number):
            number = default
        return max(low, min(high, number))

    def _int(value: object, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(_MIN_PITCH, min(_MAX_PITCH, number))

    lower = _int(pitch_min, _MIN_PITCH)
    upper = _int(pitch_max, _MAX_PITCH)
    if lower > upper:
        lower, upper = upper, lower
    normalized_mode = str(mode or "auto")
    if normalized_mode not in EXTRACTION_MODES:
        normalized_mode = "auto"
    return ExtractionControls(
        mode=normalized_mode,
        min_note_duration=_float(min_note_duration, 0.08, 0.01, 4.0),
        noise_threshold=_float(noise_threshold, 0.0, 0.0, 1.0),
        quantize_strength=_float(quantize_strength, 1.0, 0.0, 1.0),
        simplify_density=_float(simplify_density, 0.5, 0.0, 1.0),
        pitch_min=lower,
        pitch_max=upper,
    )


class AudioImportStatus(StrEnum):
    LOADING = "loading"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"
    IMPORTED = "imported"
    CANCELLED = "cancelled"


class _AnalysisCancelled(RuntimeError):
    """Internal control flow used to stop CPU analysis promptly."""


_ERROR_MESSAGES = {
    "unsupported_format": "当前只支持 WAV 和 MP3 音频。",
    "decode_failed": "无法读取这份音频，请确认文件没有损坏。",
    "analysis_failed": "无法从音频中分析出主要旋律。",
    "cancelled": "音频分析已取消。",
    "file_too_large": "音频文件超过当前版本允许的大小。",
    "file_too_long": "音频时长超过当前版本允许的范围。",
    "analysis_dependency_missing": "本地音频解码组件不可用，请重新安装软件。",
    "worker_crashed": "音频分析进程意外退出，请重试。",
}


def describe_audio_error(code: str) -> str:
    return _ERROR_MESSAGES.get(code, "音频分析失败，请重试。")


def _sensitivity_values(sensitivity: str) -> tuple[float, float, float]:
    values = {
        "conservative": (0.78, 0.12, 0.12),
        "standard": (0.64, 0.08, 0.08),
        "sensitive": (0.52, 0.05, 0.05),
    }
    return values.get(str(sensitivity), values["standard"])


def estimate_tempo_from_onsets(onsets: Sequence[float]) -> int:
    """Estimate a conservative quarter-note BPM from note onset spacing."""
    values = sorted(
        float(value)
        for value in onsets
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    if len(values) < 2:
        return 120
    gaps = [right - left for left, right in zip(values, values[1:]) if right > left]
    if not gaps:
        return 120
    gap = float(np.median(np.asarray(gaps, dtype=np.float64)))
    if gap <= 0:
        return 120
    bpm = 60.0 / gap
    while bpm > 180.0:
        bpm /= 2.0
    while 0 < bpm < 60.0:
        return 60
    return int(round(max(60.0, min(180.0, bpm))))


def _pitch_name(pitch: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


def _raw_pitch_frames(
    samples: np.ndarray,
    sample_rate: int,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    if samples.size < _FRAME_SIZE or sample_rate <= 0:
        return []
    frames: list[dict[str, Any]] = []
    minimum_lag = max(2, int(sample_rate / 1000))
    maximum_lag = min(_FRAME_SIZE - 2, int(sample_rate / 60))
    window = np.hanning(_FRAME_SIZE).astype(np.float32)
    for start in range(0, samples.size - _FRAME_SIZE + 1, _HOP_SIZE):
        if cancel_check is not None and cancel_check():
            raise _AnalysisCancelled("analysis cancelled")
        frame = samples[start : start + _FRAME_SIZE].astype(np.float32, copy=False)
        frame = frame - float(np.mean(frame))
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms <= 1e-5:
            continue
        tapered = frame * window
        spectrum = np.fft.rfft(tapered, n=_FRAME_SIZE * 2)
        autocorrelation = np.fft.irfft(
            np.abs(spectrum) ** 2, n=_FRAME_SIZE * 2
        )[:_FRAME_SIZE]
        zero_lag = float(autocorrelation[0])
        if zero_lag <= 1e-8:
            continue
        autocorrelation /= zero_lag
        region = autocorrelation[minimum_lag : maximum_lag + 1]
        lag = minimum_lag + int(np.argmax(region))
        confidence = float(autocorrelation[lag])
        if lag > minimum_lag and lag < maximum_lag:
            left = float(autocorrelation[lag - 1])
            center = float(autocorrelation[lag])
            right = float(autocorrelation[lag + 1])
            denominator = left - 2.0 * center + right
            if abs(denominator) > 1e-8:
                lag = lag + 0.5 * (left - right) / denominator
        frequency = sample_rate / max(1e-6, float(lag))
        pitch_value = 69.0 + 12.0 * math.log2(frequency / 440.0)
        pitch = int(round(pitch_value))
        if not _MIN_PITCH <= pitch <= _MAX_PITCH:
            continue
        velocity = int(max(1, min(127, round(45.0 + rms * 210.0))))
        frames.append(
            {
                "pitch": pitch,
                "startTime": start / sample_rate,
                "duration": _HOP_SIZE / sample_rate,
                "confidence": max(0.0, min(1.0, confidence)),
                "velocity": velocity,
            }
        )
    return frames


def _merge_frames(frames: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    gap_limit = 2.5 * _HOP_SIZE / ANALYSIS_SAMPLE_RATE
    for frame in frames:
        if not merged:
            merged.append(dict(frame))
            continue
        previous = merged[-1]
        previous_end = float(previous["startTime"]) + float(previous["duration"])
        start = float(frame["startTime"])
        if int(frame["pitch"]) == int(previous["pitch"]) and start <= previous_end + gap_limit:
            end = max(previous_end, start + float(frame["duration"]))
            previous["duration"] = end - float(previous["startTime"])
            previous["confidence"] = max(
                float(previous["confidence"]), float(frame["confidence"])
            )
            previous["velocity"] = max(
                int(previous.get("velocity", 75)), int(frame.get("velocity", 75))
            )
        else:
            merged.append(dict(frame))
    return merged


def cleanup_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    tempo_bpm: int = 120,
    sensitivity: str = "standard",
) -> list[dict[str, Any]]:
    """Remove obvious transcription noise and quantize to a light eighth grid."""
    threshold, minimum_duration, gap_limit = _sensitivity_values(sensitivity)
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            pitch = int(candidate["pitch"])
            start = float(candidate["startTime"])
            duration = float(candidate["duration"])
            confidence = float(candidate.get("confidence", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not _MIN_PITCH <= pitch <= _MAX_PITCH
            or not math.isfinite(start)
            or not math.isfinite(duration)
            or duration < minimum_duration
            or confidence < threshold
        ):
            continue
        filtered.append(
            {
                "pitch": pitch,
                "startTime": max(0.0, start),
                "duration": duration,
                "confidence": max(0.0, min(1.0, confidence)),
                "velocity": int(max(1, min(127, int(candidate.get("velocity", 75))))),
            }
        )
    filtered.sort(key=lambda item: (item["startTime"], item["pitch"]))
    merged: list[dict[str, Any]] = []
    for candidate in filtered:
        if merged:
            previous = merged[-1]
            previous_end = previous["startTime"] + previous["duration"]
            gap = candidate["startTime"] - previous_end
            if candidate["pitch"] == previous["pitch"] and 0 <= gap <= gap_limit:
                previous["duration"] = max(
                    previous_end, candidate["startTime"] + candidate["duration"]
                ) - previous["startTime"]
                previous["confidence"] = max(
                    previous["confidence"], candidate["confidence"]
                )
                previous["velocity"] = max(previous["velocity"], candidate["velocity"])
                continue
        merged.append(candidate)

    bpm = max(60, min(180, int(tempo_bpm or 120)))
    grid = 60.0 / bpm / 2.0
    cleaned: list[dict[str, Any]] = []
    for candidate in merged:
        start = round(candidate["startTime"] / grid) * grid
        end = round((candidate["startTime"] + candidate["duration"]) / grid) * grid
        duration = max(grid, end - start)
        cleaned.append(
            {
                **candidate,
                "startTime": round(max(0.0, start), 6),
                "duration": round(duration, 6),
            }
        )
    return cleaned


def _onset_groups(
    candidates: Sequence[dict[str, Any]], *, tolerance: float = 0.08
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        if not groups or float(candidate["startTime"]) - float(groups[-1][0]["startTime"]) > tolerance:
            groups.append([candidate])
        else:
            groups[-1].append(candidate)
    return groups


def _select_melody(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one conservative line per onset, rewarding continuity."""

    selected: list[dict[str, Any]] = []
    previous_pitch: int | None = None
    for group in _onset_groups(candidates):
        def score(candidate: dict[str, Any]) -> tuple[float, float, float]:
            pitch = int(candidate["pitch"])
            continuity = 0.0
            if previous_pitch is not None:
                continuity = max(0.0, 1.0 - abs(pitch - previous_pitch) / 12.0)
            return (
                float(candidate.get("confidence", 0.0)) * 0.55 + continuity * 0.30,
                float(candidate.get("duration", 0.0)),
                -abs(pitch - (previous_pitch if previous_pitch is not None else 60)),
            )

        choice = max(group, key=score)
        selected.append(choice)
        previous_pitch = int(choice["pitch"])
    return selected


def _select_simplified(
    candidates: Sequence[dict[str, Any]], density: float
) -> list[dict[str, Any]]:
    max_per_onset = max(1, min(4, int(round(1.0 + float(density) * 3.0))))
    simplified: list[dict[str, Any]] = []
    for group in _onset_groups(candidates):
        ranked = sorted(
            group,
            key=lambda candidate: (
                -float(candidate.get("confidence", 0.0)),
                -float(candidate.get("duration", 0.0)),
                int(candidate["pitch"]),
            ),
        )
        simplified.extend(ranked[:max_per_onset])
    return simplified


def apply_extraction_controls(
    candidates: Sequence[dict[str, Any]],
    *,
    tempo_bpm: int = 120,
    controls: ExtractionControls | None = None,
    mode: str = "auto",
    min_note_duration: float = 0.08,
    noise_threshold: float = 0.0,
    quantize_strength: float = 1.0,
    simplify_density: float = 0.5,
    pitch_min: int = _MIN_PITCH,
    pitch_max: int = _MAX_PITCH,
) -> list[dict[str, Any]]:
    """Apply bounded musical extraction controls deterministically.

    This operates only on temporary analysis candidates. It intentionally does
    not touch ProjectState; the controller commits the final list as one
    history transaction after the user chooses Import.
    """

    normalized = controls or normalize_extraction_controls(
        mode=mode,
        min_note_duration=min_note_duration,
        noise_threshold=noise_threshold,
        quantize_strength=quantize_strength,
        simplify_density=simplify_density,
        pitch_min=pitch_min,
        pitch_max=pitch_max,
    )
    normalized_bpm = max(60, min(180, int(tempo_bpm or 120)))
    grid = 60.0 / normalized_bpm / 2.0
    prepared: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        try:
            pitch = int(raw["pitch"])
            start = float(raw.get("startTime", raw.get("start", 0.0)))
            duration = float(raw["duration"])
            confidence = float(raw.get("confidence", 1.0))
            velocity = int(raw.get("velocity", 75))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not normalized.pitch_min <= pitch <= normalized.pitch_max
            or not math.isfinite(start)
            or not math.isfinite(duration)
            or not math.isfinite(confidence)
            or start < 0
            or duration <= 0
            or duration < normalized.min_note_duration
            or confidence < normalized.noise_threshold
        ):
            continue
        start = max(0.0, start)
        end = start + duration
        strength = normalized.quantize_strength
        if strength:
            quantized_start = round(start / grid) * grid
            quantized_end = round(end / grid) * grid
            start += (quantized_start - start) * strength
            end += (quantized_end - end) * strength
        duration = max(normalized.min_note_duration, end - start)
        prepared.append(
            {
                "pitch": pitch,
                "startTime": round(start, 6),
                "duration": round(duration, 6),
                "confidence": round(max(0.0, min(1.0, confidence)), 6),
                "velocity": max(1, min(127, velocity)),
            }
        )

    prepared.sort(key=lambda item: (item["startTime"], item["pitch"], -item["confidence"]))
    merged: list[dict[str, Any]] = []
    for candidate in prepared:
        if merged:
            previous = merged[-1]
            previous_end = previous["startTime"] + previous["duration"]
            gap = candidate["startTime"] - previous_end
            if candidate["pitch"] == previous["pitch"] and 0 <= gap <= grid * 0.5:
                previous["duration"] = round(
                    max(previous_end, candidate["startTime"] + candidate["duration"])
                    - previous["startTime"],
                    6,
                )
                previous["confidence"] = max(
                    previous["confidence"], candidate["confidence"]
                )
                previous["velocity"] = max(previous["velocity"], candidate["velocity"])
                continue
        merged.append(candidate)

    if normalized.mode == "melody":
        result = _select_melody(merged)
    elif normalized.mode == "bass":
        bass_candidates = [candidate for candidate in merged if candidate["pitch"] <= 60]
        result = [min(group, key=lambda candidate: (candidate["pitch"], -candidate["confidence"]))
                 for group in _onset_groups(bass_candidates)]
    elif normalized.mode == "simplified":
        result = _select_simplified(merged, normalized.simplify_density)
    else:
        result = merged
    result.sort(key=lambda item: (item["startTime"], item["pitch"]))
    return result


def analyze_pcm(
    samples: Sequence[float] | np.ndarray,
    sample_rate: int,
    *,
    sensitivity: str = "standard",
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Analyze monophonic PCM locally and return compact note candidates."""
    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    raw_frames = _raw_pitch_frames(
        array,
        int(sample_rate),
        cancel_check=cancel_check,
    )
    raw_candidates = _merge_frames(raw_frames)
    tempo = estimate_tempo_from_onsets([item["startTime"] for item in raw_candidates])
    cleaned = cleanup_candidates(raw_candidates, tempo_bpm=tempo, sensitivity=sensitivity)
    confidence = (
        float(np.mean([item["confidence"] for item in raw_candidates]))
        if raw_candidates
        else 0.0
    )
    pitches = [int(item["pitch"]) for item in cleaned]
    return {
        "durationSeconds": round(array.size / max(1, int(sample_rate)), 6),
        "sampleRate": int(sample_rate),
        "channels": 1,
        "tempoEstimate": tempo,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "range": {
            "lowest": min(pitches) if pitches else None,
            "highest": max(pitches) if pitches else None,
            "lowestName": _pitch_name(min(pitches)) if pitches else "",
            "highestName": _pitch_name(max(pitches)) if pitches else "",
        },
        "rawDetectedNotes": raw_candidates,
        "detectedNotes": cleaned,
    }


def _decode_buffer(buffer: Any) -> np.ndarray:
    from PySide6.QtMultimedia import QAudioFormat

    fmt = buffer.format()
    channels = max(1, int(fmt.channelCount()))
    sample_format = fmt.sampleFormat()
    raw = bytes(buffer.constData())
    if sample_format == QAudioFormat.SampleFormat.Int16:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_format == QAudioFormat.SampleFormat.Int32:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sample_format == QAudioFormat.SampleFormat.Float:
        samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    elif sample_format == QAudioFormat.SampleFormat.UInt8:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError("unsupported decoded sample format")
    if channels > 1:
        frame_count = samples.size // channels
        samples = samples[: frame_count * channels].reshape(frame_count, channels).mean(axis=1)
    return samples


def _decode_wave_pcm(raw: bytes, channels: int, sample_width: int) -> np.ndarray:
    """Decode a PCM WAV chunk into mono float samples without Qt Multimedia."""
    channels = max(1, int(channels))
    width = int(sample_width)
    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        packed = np.frombuffer(raw, dtype=np.uint8)
        packed = packed[: packed.size - (packed.size % 3)].reshape(-1, 3)
        values = (
            packed[:, 0].astype(np.int32)
            | (packed[:, 1].astype(np.int32) << 8)
            | (packed[:, 2].astype(np.int32) << 16)
        )
        values[values & 0x800000 != 0] -= 1 << 24
        samples = values.astype(np.float32) / 8_388_608.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2_147_483_648.0
    else:
        raise ValueError(f"unsupported WAV sample width: {width}")
    if channels > 1:
        frame_count = samples.size // channels
        samples = samples[: frame_count * channels].reshape(frame_count, channels).mean(axis=1)
    return samples


class _DecoderWorker(QObject):
    stageChanged = Signal(str)
    progressChanged = Signal(float)
    ready = Signal(dict)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, path: str, sensitivity: str) -> None:
        super().__init__()
        self._path = path
        self._sensitivity = sensitivity
        self._decoder: Any | None = None
        self._samples: list[np.ndarray] = []
        self._cancelled = False
        self._terminal = False
        self._pending_failure: tuple[str, str] | None = None
        self._retire_scheduled = False
        self._wave_handle: wave.Wave_read | None = None
        self._wave_sample_rate = 0
        self._wave_channels = 0
        self._wave_total_frames = 0
        self._wave_read_frames = 0

    @Slot()
    def run(self) -> None:
        try:
            if self._is_cancel_requested():
                self._finish_cancelled()
                return
            self._stage("读取音频")
            if Path(self._path).suffix.lower() == ".wav":
                self._run_wave_decode()
                return
            from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

            # Keep the decoder owned by the worker so it is destroyed on the
            # decoder thread, before the thread itself is retired.
            self._decoder = QAudioDecoder(self)
            requested = QAudioFormat()
            requested.setSampleRate(ANALYSIS_SAMPLE_RATE)
            requested.setChannelCount(1)
            requested.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self._decoder.setAudioFormat(requested)
            self._decoder.bufferReady.connect(self._on_buffer)
            self._decoder.error.connect(self._on_error)
            self._decoder.durationChanged.connect(self._on_duration_changed)
            self._decoder.finished.connect(self._on_finished)
            self._decoder.setSource(QUrl.fromLocalFile(self._path))
            self._decoder.start()
        except Exception:
            logger.exception("Audio decoder worker failed to start")
            self._fail("analysis_dependency_missing", describe_audio_error("analysis_dependency_missing"))

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True
        if self._wave_handle is not None:
            self._finish_cancelled()
            return
        # QAudioDecoder's FFmpeg backend can access its internal demuxer while
        # stop() is unwinding. Mark cancellation and drain the already queued
        # buffers instead; the decoder finishes quickly without blocking the
        # UI, and the worker then retires normally.
        if self._decoder is None or not self._decoder.isDecoding():
            self._finish_cancelled()

    def _is_cancel_requested(self) -> bool:
        return self._cancelled or QThread.currentThread().isInterruptionRequested()

    def _run_wave_decode(self) -> None:
        try:
            handle = wave.open(self._path, "rb")
        except (OSError, EOFError, ValueError, wave.Error):
            logger.exception("WAV header decode failed")
            self._fail("decode_failed", describe_audio_error("decode_failed"))
            return
        self._wave_handle = handle
        self._wave_sample_rate = int(handle.getframerate())
        self._wave_channels = int(handle.getnchannels())
        self._wave_total_frames = int(handle.getnframes())
        self._wave_read_frames = 0
        if self._wave_sample_rate <= 0 or self._wave_channels <= 0:
            self._fail("decode_failed", describe_audio_error("decode_failed"))
            return
        duration_seconds = self._wave_total_frames / self._wave_sample_rate
        if duration_seconds > MAX_AUDIO_DURATION_SECONDS:
            self._fail("file_too_long", describe_audio_error("file_too_long"))
            return
        self._samples = []
        try:
            while not self._terminal:
                if self._is_cancel_requested():
                    self._finish_cancelled()
                    return
                raw = handle.readframes(16_384)
                if not raw:
                    break
                self._samples.append(
                    _decode_wave_pcm(raw, self._wave_channels, handle.getsampwidth())
                )
                self._wave_read_frames = int(handle.tell())
                self._stage("分析音高")
                progress = self._wave_read_frames / max(1, self._wave_total_frames)
                self.progressChanged.emit(min(0.9, max(0.0, progress)))
            self._close_wave()
            self._finish_wave_analysis()
        except Exception:
            logger.exception("WAV decoding failed")
            self._close_wave()
            self._fail("decode_failed", describe_audio_error("decode_failed"))

    def _finish_wave_analysis(self) -> None:
        if self._terminal:
            return
        if self._is_cancel_requested():
            self._finish_cancelled()
            return
        try:
            samples = (
                np.concatenate(self._samples)
                if self._samples
                else np.empty(0, dtype=np.float32)
            )
            self._stage("生成音符")
            result = analyze_pcm(
                samples,
                self._wave_sample_rate,
                sensitivity=self._sensitivity,
                cancel_check=self._is_cancel_requested,
            )
            if self._is_cancel_requested():
                self._finish_cancelled()
                return
            self._stage("整理结果")
            result["channels"] = self._wave_channels
            result["sampleRate"] = self._wave_sample_rate
            if not result["detectedNotes"]:
                self._fail("analysis_failed", describe_audio_error("analysis_failed"))
                return
            self._terminal = True
            self.ready.emit(result)
            self.progressChanged.emit(1.0)
            self._dispose_decoder()
        except _AnalysisCancelled:
            self._finish_cancelled()
        except Exception:
            logger.exception("WAV analysis failed")
            self._fail("analysis_failed", describe_audio_error("analysis_failed"))

    def _close_wave(self) -> None:
        handle = self._wave_handle
        self._wave_handle = None
        if handle is not None:
            handle.close()

    @Slot()
    def _on_buffer(self) -> None:
        if self._decoder is None or self._terminal:
            return
        try:
            while self._decoder.bufferAvailable():
                buffer = self._decoder.read()
                if buffer.isValid() and not self._is_cancel_requested():
                    self._samples.append(_decode_buffer(buffer))
            if self._decoder.duration() > 0:
                self._stage("分析音高")
                self.progressChanged.emit(
                    min(0.9, max(0.0, self._decoder.position() / self._decoder.duration()))
                )
        except Exception:
            logger.exception("Audio buffer decoding failed")
            self._fail("decode_failed", describe_audio_error("decode_failed"))

    @Slot(int)
    def _on_duration_changed(self, duration_ms: int) -> None:
        if duration_ms and duration_ms / 1000.0 > MAX_AUDIO_DURATION_SECONDS:
            self._fail("file_too_long", describe_audio_error("file_too_long"))

    @Slot(object)
    def _on_error(self, _error: object) -> None:
        if self._pending_failure is not None and not self._terminal:
            code, message = self._pending_failure
            self._pending_failure = None
            self._complete_failure(code, message)
        elif self._is_cancel_requested() and not self._terminal:
            self._finish_cancelled()
        elif not self._terminal:
            self._complete_failure("decode_failed", describe_audio_error("decode_failed"))

    @Slot()
    def _on_finished(self) -> None:
        if self._terminal:
            return
        if self._is_cancel_requested():
            if self._pending_failure is not None:
                code, message = self._pending_failure
                self._pending_failure = None
                self._complete_failure(code, message)
            else:
                self._finish_cancelled()
            return
        try:
            samples = np.concatenate(self._samples) if self._samples else np.empty(0, dtype=np.float32)
            if self._pending_failure is not None:
                code, message = self._pending_failure
                self._pending_failure = None
                self._complete_failure(code, message)
                return
            self._stage("生成音符")
            result = analyze_pcm(
                samples,
                ANALYSIS_SAMPLE_RATE,
                sensitivity=self._sensitivity,
                cancel_check=self._is_cancel_requested,
            )
            if self._is_cancel_requested():
                self._finish_cancelled()
                return
            self._stage("整理结果")
            duration_ms = self._decoder.duration() if self._decoder is not None else 0
            result["durationSeconds"] = round(
                max(float(result["durationSeconds"]), float(duration_ms) / 1000.0), 6
            )
            result["channels"] = 1
            result["sampleRate"] = ANALYSIS_SAMPLE_RATE
            if not result["detectedNotes"]:
                self._fail("analysis_failed", describe_audio_error("analysis_failed"))
                return
            self.ready.emit(result)
            self.progressChanged.emit(1.0)
            self._terminal = True
            self._dispose_decoder()
        except _AnalysisCancelled:
            self._finish_cancelled()
        except Exception:
            logger.exception("Audio analysis failed")
            self._fail("analysis_failed", describe_audio_error("analysis_failed"))

    def _stage(self, value: str) -> None:
        self.stageChanged.emit(value)

    def _fail(self, code: str, message: str) -> None:
        if self._terminal or self._pending_failure is not None:
            return
        if self._decoder is not None and self._decoder.isDecoding():
            self._pending_failure = (code, message)
            self._cancelled = True
            return
        self._complete_failure(code, message)

    def _complete_failure(self, code: str, message: str) -> None:
        if self._terminal:
            return
        self._terminal = True
        self._close_wave()
        self.failed.emit(code, message)
        self._dispose_decoder()

    def _finish_cancelled(self) -> None:
        if self._terminal:
            return
        self._terminal = True
        self._close_wave()
        self.cancelled.emit()
        self._dispose_decoder()

    def _dispose_decoder(self) -> None:
        if self._retire_scheduled:
            return
        self._retire_scheduled = True
        decoder = self._decoder
        if decoder is None:
            self.finished.emit()
            return
        # The decoder is parented to the worker. Let worker teardown own the
        # final QObject destruction, but wait for its destroyed signal before
        # allowing the thread to quit.
        decoder.destroyed.connect(self._on_decoder_destroyed)
        decoder.deleteLater()

    @Slot()
    def _on_decoder_destroyed(self) -> None:
        self._decoder = None
        self.finished.emit()


class AudioImportSession(QObject):
    """A temporary, cancellable local audio analysis session."""

    statusChanged = Signal()
    progressChanged = Signal()
    stageChanged = Signal()
    resultChanged = Signal()
    errorChanged = Signal()
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._status = AudioImportStatus.CANCELLED.value
        self._stage = "等待选择音频"
        self._progress = -1.0
        self._source_display_name = ""
        self._duration_seconds = 0.0
        self._sample_rate = 0
        self._channels = 0
        self._notes: list[dict[str, Any]] = []
        self._base_notes: list[dict[str, Any]] = []
        self._raw_notes: list[dict[str, Any]] = []
        self._tempo_estimate = 0
        self._confidence = 0.0
        self._range: dict[str, Any] = {}
        self._target_import_range: dict[str, Any] = {
            "mode": "project_start",
            "startSeconds": 0.0,
        }
        self._error_code = ""
        self._error_message = ""
        self._controls = normalize_extraction_controls()
        self._extraction_mode = self._controls.mode
        self._thread: QThread | None = None
        self._worker: _DecoderWorker | None = None
        self._pending_start: tuple[str, str, str] | None = None
        self._shutting_down = False
        self._run_generation = 0

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=stageChanged)
    def stage(self) -> str:
        return self._stage

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=statusChanged)
    def sourceDisplayName(self) -> str:
        return self._source_display_name

    @Property(float, notify=resultChanged)
    def durationSeconds(self) -> float:
        return self._duration_seconds

    @Property(int, notify=resultChanged)
    def sampleRate(self) -> int:
        return self._sample_rate

    @Property(int, notify=resultChanged)
    def channels(self) -> int:
        return self._channels

    @Property("QVariantList", notify=resultChanged)
    def detectedNotes(self) -> list[dict[str, Any]]:
        return list(self._notes)

    @Property("QVariantList", notify=resultChanged)
    def rawDetectedNotes(self) -> list[dict[str, Any]]:
        return list(self._raw_notes)

    @Property(str, notify=resultChanged)
    def extractionMode(self) -> str:
        return self._extraction_mode

    @Property(float, notify=resultChanged)
    def minimumNoteDuration(self) -> float:
        return self._controls.min_note_duration

    @Property(float, notify=resultChanged)
    def noiseThreshold(self) -> float:
        return self._controls.noise_threshold

    @Property(float, notify=resultChanged)
    def quantizeStrength(self) -> float:
        return self._controls.quantize_strength

    @Property(float, notify=resultChanged)
    def simplifyDensity(self) -> float:
        return self._controls.simplify_density

    @Property(int, notify=resultChanged)
    def pitchMin(self) -> int:
        return self._controls.pitch_min

    @Property(int, notify=resultChanged)
    def pitchMax(self) -> int:
        return self._controls.pitch_max

    @Property(int, notify=resultChanged)
    def tempoEstimate(self) -> int:
        return self._tempo_estimate

    @Property(float, notify=resultChanged)
    def confidence(self) -> float:
        return self._confidence

    @Property("QVariantMap", notify=resultChanged)
    def pitchRange(self) -> dict[str, Any]:
        return dict(self._range)

    @Property("QVariantMap", notify=resultChanged)
    def targetImportRange(self) -> dict[str, Any]:
        """The user-visible destination chosen for the temporary result."""
        return dict(self._target_import_range)

    @Property(str, notify=errorChanged)
    def errorCode(self) -> str:
        return self._error_code

    @Property(str, notify=errorChanged)
    def errorMessage(self) -> str:
        return self._error_message

    @Slot(str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def start(
        self, source: str, sensitivity: str = "standard", mode: str = "auto"
    ) -> bool:
        path = self._local_path(source)
        if path is None:
            self._set_failure("unsupported_format")
            return False
        file_path = Path(path)
        suffix = file_path.suffix.lower()
        if suffix not in SUPPORTED_AUDIO_SUFFIXES:
            self._set_failure("unsupported_format")
            return False
        try:
            size = file_path.stat().st_size
        except OSError:
            self._set_failure("decode_failed")
            return False
        if size > MAX_AUDIO_FILE_BYTES:
            self._set_failure("file_too_large")
            return False
        self._controls = normalize_extraction_controls(
            mode=mode,
            min_note_duration=self._controls.min_note_duration,
            noise_threshold=self._controls.noise_threshold,
            quantize_strength=self._controls.quantize_strength,
            simplify_density=self._controls.simplify_density,
            pitch_min=self._controls.pitch_min,
            pitch_max=self._controls.pitch_max,
        )
        self._extraction_mode = self._controls.mode
        next_start = (str(file_path), str(sensitivity), self._controls.mode)
        if self._thread is not None:
            # Never replace references to a live worker. A new request waits
            # for the old QThread and its deferred QObject cleanup to finish.
            self._pending_start = next_start
            self._cancel_current()
            return True
        self._source_display_name = file_path.name
        self._duration_seconds = 0.0
        self._sample_rate = 0
        self._channels = 0
        self._notes = []
        self._base_notes = []
        self._raw_notes = []
        self._tempo_estimate = 0
        self._confidence = 0.0
        self._range = {}
        self._error_code = ""
        self._error_message = ""
        self._progress = -1.0
        self._set_status(AudioImportStatus.LOADING.value)
        self._run_generation += 1
        generation = self._run_generation
        thread = QThread(self)
        worker = _DecoderWorker(str(file_path), sensitivity)
        self._thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stageChanged.connect(self._on_stage)
        worker.progressChanged.connect(self._on_progress)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.destroyed.connect(
            lambda *_args, run=generation: self._on_worker_destroyed(run)
        )
        thread.destroyed.connect(
            lambda *_args, run=generation: self._on_thread_destroyed(run)
        )
        thread.destroyed.connect(
            lambda *_args, run=generation: self._emit_finished(run)
        )
        thread.finished.connect(thread.deleteLater)
        # Enter the observable analysis state before the worker can complete
        # and queue READY. Fast local WAV analysis must not make callers miss
        # the ANALYZING lifecycle state.
        self._set_status(AudioImportStatus.ANALYZING.value)
        thread.start()
        return True

    @Slot(str, float, float, float, float, int, int, result=bool)
    def setExtractionControls(
        self,
        mode: str = "auto",
        min_note_duration: float = 0.08,
        noise_threshold: float = 0.0,
        quantize_strength: float = 1.0,
        simplify_density: float = 0.5,
        pitch_min: int = _MIN_PITCH,
        pitch_max: int = _MAX_PITCH,
    ) -> bool:
        controls = normalize_extraction_controls(
            mode=mode,
            min_note_duration=min_note_duration,
            noise_threshold=noise_threshold,
            quantize_strength=quantize_strength,
            simplify_density=simplify_density,
            pitch_min=pitch_min,
            pitch_max=pitch_max,
        )
        changed = controls != self._controls
        self._controls = controls
        self._extraction_mode = controls.mode
        if self._base_notes:
            self._reprocess_notes()
        if changed:
            self.resultChanged.emit()
        return True

    @Slot()
    def cancel(self) -> None:
        self._pending_start = None
        self._cancel_current()
        if self._thread is None and self._status in {
            AudioImportStatus.LOADING.value,
            AudioImportStatus.ANALYZING.value,
            AudioImportStatus.READY.value,
        }:
            self._set_status(AudioImportStatus.CANCELLED.value)
            self._stage = "已取消"
            self.stageChanged.emit()

    @Slot()
    def shutdown(self) -> None:
        """Cancel an active decode before the application destroys its QML tree."""
        self._shutting_down = True
        self._pending_start = None
        self._cancel_current()
        thread = self._thread
        if thread is None or not thread.isRunning():
            return
        # aboutToQuit is emitted while the application event loop is still
        # alive. Drain this one worker teardown so the parent QML tree never
        # destroys a live QThread. The timeout keeps application exit bounded
        # even if a third-party decoder backend misbehaves.
        loop = QEventLoop()
        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        self.finished.connect(loop.quit)
        timeout.start(3_000)
        loop.exec()
        timeout.stop()
        timeout.deleteLater()
        try:
            self.finished.disconnect(loop.quit)
        except (RuntimeError, TypeError):
            pass
        current_thread = self._thread
        if current_thread is not None and current_thread.isRunning():
            current_thread.quit()
            current_thread.wait(1_000)

    def _cancel_current(self) -> None:
        if self._worker is None or self._thread is None:
            return
        self._thread.requestInterruption()
        if not self._thread.isRunning():
            self._thread.deleteLater()
            return
        QMetaObject.invokeMethod(
            self._worker,
            "cancel",
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot(result=bool)
    def markImported(self) -> bool:
        if self._status != AudioImportStatus.READY.value:
            return False
        self._set_status(AudioImportStatus.IMPORTED.value)
        self._stage = "已导入工程"
        self.stageChanged.emit()
        return True

    @Slot(str, float, result=bool)
    def setTargetImportRange(self, mode: str, start_seconds: float = 0.0) -> bool:
        if mode not in {"project_start", "playhead"}:
            return False
        try:
            value = float(start_seconds)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value) or value < 0:
            return False
        next_range = {"mode": mode, "startSeconds": value if mode == "playhead" else 0.0}
        if next_range != self._target_import_range:
            self._target_import_range = next_range
            self.resultChanged.emit()
        return True

    def _local_path(self, source: str) -> str | None:
        value = str(source or "")
        if value.startswith("file:"):
            value = QUrl(value).toLocalFile()
        return value or None

    def _set_status(self, value: str) -> None:
        if value != self._status:
            self._status = value
            self.statusChanged.emit()

    @Slot(str)
    def _on_stage(self, value: str) -> None:
        self._stage = value
        self.stageChanged.emit()
        if self._status == AudioImportStatus.LOADING.value:
            self._set_status(AudioImportStatus.ANALYZING.value)

    @Slot(float)
    def _on_progress(self, value: float) -> None:
        self._progress = float(value)
        self.progressChanged.emit()

    @Slot(dict)
    def _on_ready(self, result: dict[str, Any]) -> None:
        self._duration_seconds = float(result.get("durationSeconds", 0.0))
        self._sample_rate = int(result.get("sampleRate", 0))
        self._channels = int(result.get("channels", 0))
        self._base_notes = list(result.get("detectedNotes", []))
        self._raw_notes = list(result.get("rawDetectedNotes", []))
        if not self._base_notes:
            self._base_notes = list(self._raw_notes)
        self._reprocess_notes()
        self._tempo_estimate = int(result.get("tempoEstimate", 0))
        self._confidence = float(result.get("confidence", 0.0))
        self._range = dict(result.get("range", {}))
        self._set_status(AudioImportStatus.READY.value)
        self.resultChanged.emit()

    def _reprocess_notes(self) -> None:
        self._notes = apply_extraction_controls(
            self._base_notes,
            tempo_bpm=self._tempo_estimate or 120,
            controls=self._controls,
        )

    @Slot(str, str)
    def _on_failed(self, code: str, message: str) -> None:
        self._set_failure(code, message)

    @Slot()
    def _on_cancelled(self) -> None:
        self._set_status(AudioImportStatus.CANCELLED.value)
        self._stage = "已取消"
        self.stageChanged.emit()

    def _set_failure(self, code: str, message: str | None = None) -> None:
        self._error_code = str(code)
        self._error_message = message or describe_audio_error(code)
        self._set_status(AudioImportStatus.FAILED.value)
        self.errorChanged.emit()

    def _on_worker_destroyed(self, generation: int) -> None:
        if generation == self._run_generation:
            self._worker = None

    def _on_thread_destroyed(self, generation: int) -> None:
        if generation == self._run_generation:
            self._thread = None

    def _emit_finished(self, generation: int) -> None:
        # A child QThread can be destroyed during QObject parent teardown.
        # The lambda keeps the Python wrapper alive, not its C++ signal source.
        if not isValid(self) or generation != self._run_generation:
            return
        pending = self._pending_start
        self._pending_start = None
        self.finished.emit()
        if pending is not None and not self._shutting_down:
            self.start(*pending)
