from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import numpy as np
from PySide6.QtCore import QObject, Property, QThread, QTimer, Signal, Slot


_DEFAULT_SAMPLE_RATE = 48_000
_DEFAULT_FFT_SIZE = 1_024
_SUPPORTED_FFT_SIZES = frozenset({1_024, 2_048})
_SUPPORTED_BAND_COUNTS = frozenset({24, 32, 48})
_MIN_BAND_FREQUENCY = 30.0
_MAX_BAND_FREQUENCY = 20_000.0
_INT16_SCALE = 32_768.0


class PcmSnapshotTap:
    """Bounded single-producer/single-consumer handoff for final PCM bytes.

    The mixer is the sole producer and the analyzer is the sole consumer.
    The handoff deliberately uses CPython's atomic reference replacement
    instead of a blocking lock in the audio pull callback.  A generation
    counter makes enable/disable transitions invalidate an in-flight publish
    without making the callback wait for the UI or analyzer thread.
    """

    def __init__(self, *, enabled: bool = True, max_bytes: int = 16_384) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._max_bytes = max(4, max_bytes - max_bytes % 4)
        self._enabled = bool(enabled)
        self._latest: bytes | None = None
        self._publish_count = 0
        self._generation = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def publish_count(self) -> int:
        return self._publish_count

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._generation += 1
        self._latest = None

    def publish(self, payload: bytes) -> None:
        if not self._enabled:
            return
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        generation = self._generation
        if len(payload) > self._max_bytes:
            payload = payload[-self._max_bytes :]
        if not self._enabled or generation != self._generation:
            return
        self._latest = payload
        if not self._enabled or generation != self._generation:
            self._latest = None
            return
        self._publish_count += 1

    def read_latest(self) -> bytes | None:
        return self._latest

    def take_latest(self) -> bytes | None:
        payload = self._latest
        self._latest = None
        return payload

    def clear(self) -> None:
        self._latest = None


@dataclass(frozen=True, slots=True)
class VisualAudioSnapshot:
    rms: float
    peak: float
    low: float
    mid: float
    high: float
    bands: tuple[float, ...]
    sample_rate: int
    timestamp: float

    @classmethod
    def zero(
        cls,
        *,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        band_count: int = 32,
        timestamp: float | None = None,
    ) -> VisualAudioSnapshot:
        _validate_analysis_shape(sample_rate, _DEFAULT_FFT_SIZE, band_count)
        return cls(
            rms=0.0,
            peak=0.0,
            low=0.0,
            mid=0.0,
            high=0.0,
            bands=(0.0,) * band_count,
            sample_rate=sample_rate,
            timestamp=monotonic() if timestamp is None else float(timestamp),
        )


def analyze_pcm(
    payload: bytes,
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
    fft_size: int = _DEFAULT_FFT_SIZE,
    band_count: int = 32,
) -> VisualAudioSnapshot:
    """Analyze final little-endian stereo Int16 PCM outside the audio callback."""
    _validate_analysis_shape(sample_rate, fft_size, band_count)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like stereo Int16 PCM")

    raw = np.frombuffer(payload, dtype="<i2")
    frame_count = raw.size // 2
    if frame_count == 0:
        return VisualAudioSnapshot.zero(
            sample_rate=sample_rate,
            band_count=band_count,
        )
    stereo = raw[: frame_count * 2].reshape(frame_count, 2)
    mono = stereo.astype(np.float32, copy=False).mean(axis=1) / _INT16_SCALE
    if mono.size > fft_size:
        mono = mono[-fft_size:]
    if mono.size < fft_size:
        padded = np.zeros(fft_size, dtype=np.float32)
        padded[-mono.size :] = mono
        mono = padded

    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak <= 1e-9:
        return VisualAudioSnapshot.zero(
            sample_rate=sample_rate,
            band_count=band_count,
        )

    window = np.hanning(fft_size).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(mono * window))
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)
    normalization = max(1.0, float(np.sum(window)) / 2.0)
    edges = np.geomspace(
        _MIN_BAND_FREQUENCY,
        min(_MAX_BAND_FREQUENCY, sample_rate / 2.0),
        band_count + 1,
    )
    values: list[float] = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        mask = (frequencies >= lower) & (frequencies < upper)
        if not np.any(mask):
            values.append(0.0)
            continue
        values.append(_compress_spectrum_value(float(np.max(spectrum[mask]) / normalization)))

    bands = tuple(values)
    low = _range_peak(frequencies, spectrum, 30.0, 250.0, normalization)
    mid = _range_peak(frequencies, spectrum, 250.0, 4_000.0, normalization)
    high = _range_peak(frequencies, spectrum, 4_000.0, 20_000.0, normalization)
    return VisualAudioSnapshot(
        rms=min(1.0, max(0.0, rms)),
        peak=min(1.0, max(0.0, peak)),
        low=low,
        mid=mid,
        high=high,
        bands=bands,
        sample_rate=sample_rate,
        timestamp=monotonic(),
    )


def smooth_snapshot(
    previous: VisualAudioSnapshot,
    current: VisualAudioSnapshot,
    *,
    attack: float,
    release: float,
) -> VisualAudioSnapshot:
    """Apply fast attack/slower release independently to scalar and band values."""
    if not isinstance(previous, VisualAudioSnapshot) or not isinstance(
        current, VisualAudioSnapshot
    ):
        raise TypeError("previous and current must be VisualAudioSnapshot instances")
    attack_value = _bounded_coefficient(attack, "attack")
    release_value = _bounded_coefficient(release, "release")

    def blend(old: float, new: float) -> float:
        coefficient = attack_value if new >= old else release_value
        return min(1.0, max(0.0, old + (new - old) * coefficient))

    band_count = len(current.bands)
    old_bands = previous.bands
    bands = tuple(
        blend(old_bands[index] if index < len(old_bands) else 0.0, value)
        for index, value in enumerate(current.bands)
    )
    return VisualAudioSnapshot(
        rms=blend(previous.rms, current.rms),
        peak=blend(previous.peak, current.peak),
        low=blend(previous.low, current.low),
        mid=blend(previous.mid, current.mid),
        high=blend(previous.high, current.high),
        bands=bands,
        sample_rate=current.sample_rate,
        timestamp=current.timestamp,
    )


def _range_peak(
    frequencies: np.ndarray,
    spectrum: np.ndarray,
    lower: float,
    upper: float,
    normalization: float,
) -> float:
    mask = (frequencies >= lower) & (frequencies < upper)
    if not np.any(mask):
        return 0.0
    return _compress_spectrum_value(float(np.max(spectrum[mask]) / normalization))


def _compress_spectrum_value(value: float) -> float:
    """Keep quiet tones visible without allowing strong peaks to dominate."""
    bounded = min(1.0, max(0.0, float(value)))
    return float(np.log1p(9.0 * bounded) / np.log1p(9.0))


def _validate_analysis_shape(sample_rate: int, fft_size: int, band_count: int) -> None:
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    if fft_size not in _SUPPORTED_FFT_SIZES:
        raise ValueError("fft_size must be 1024 or 2048")
    if band_count not in _SUPPORTED_BAND_COUNTS:
        raise ValueError("band_count must be 24, 32, or 48")
    if sample_rate / 2.0 <= _MIN_BAND_FREQUENCY:
        raise ValueError("sample_rate is too low for visualizer bands")


def _bounded_coefficient(value: float, name: str) -> float:
    try:
        coefficient = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be finite and between 0 and 1") from error
    if not np.isfinite(coefficient) or not 0.0 <= coefficient <= 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")
    return coefficient


def _scale_snapshot(snapshot: VisualAudioSnapshot, gain: float) -> VisualAudioSnapshot:
    bounded_gain = max(0.0, float(gain))

    def scale(value: float) -> float:
        return min(1.0, max(0.0, value * bounded_gain))

    return VisualAudioSnapshot(
        rms=scale(snapshot.rms),
        peak=scale(snapshot.peak),
        low=scale(snapshot.low),
        mid=scale(snapshot.mid),
        high=scale(snapshot.high),
        bands=tuple(scale(value) for value in snapshot.bands),
        sample_rate=snapshot.sample_rate,
        timestamp=snapshot.timestamp,
    )


class AudioVisualizerWorker(QObject):
    """Background PCM analyzer; all NumPy work stays off the audio/UI threads."""

    snapshotReady = Signal(object)

    def __init__(
        self,
        tap: PcmSnapshotTap,
        *,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        fft_size: int = _DEFAULT_FFT_SIZE,
        band_count: int = 32,
        poll_interval_ms: int = 33,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(tap, PcmSnapshotTap):
            raise TypeError("tap must be a PcmSnapshotTap")
        if isinstance(poll_interval_ms, bool) or not isinstance(poll_interval_ms, int):
            raise ValueError("poll_interval_ms must be a positive integer")
        if poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be a positive integer")
        _validate_analysis_shape(sample_rate, fft_size, band_count)
        self._tap = tap
        self._sample_rate = sample_rate
        self._fft_size = fft_size
        self._band_count = band_count
        self._poll_interval_ms = poll_interval_ms
        self._enabled = bool(tap.enabled)
        self._sensitivity = 50
        self._smoothness = 68
        self._previous = VisualAudioSnapshot.zero(
            sample_rate=sample_rate,
            band_count=band_count,
        )
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(self._poll_interval_ms)
            self._timer.timeout.connect(self.process_once)
        if self._enabled:
            self._tap.set_enabled(True)
            self._timer.start()

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._enabled = False
        self._tap.set_enabled(False)
        self._previous = VisualAudioSnapshot.zero(
            sample_rate=self._sample_rate,
            band_count=self._band_count,
        )

    @Slot(bool)
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._tap.set_enabled(self._enabled)
        if not self._enabled:
            if self._timer is not None:
                self._timer.stop()
            self._previous = VisualAudioSnapshot.zero(
                sample_rate=self._sample_rate,
                band_count=self._band_count,
            )
        elif self._timer is not None:
            self._timer.start()

    @Slot(int, int, int)
    def set_config(self, sensitivity: int, smoothness: int, band_count: int) -> None:
        self._sensitivity = max(0, min(100, int(sensitivity)))
        self._smoothness = max(0, min(100, int(smoothness)))
        if band_count not in _SUPPORTED_BAND_COUNTS:
            band_count = 32
        if band_count == self._band_count:
            return
        self._band_count = band_count
        self._previous = VisualAudioSnapshot.zero(
            sample_rate=self._sample_rate,
            band_count=self._band_count,
        )

    @Slot()
    def process_once(self) -> None:
        if not self._enabled:
            self.snapshotReady.emit(self._previous)
            return
        payload = self._tap.take_latest() or b""
        current = analyze_pcm(
            payload,
            self._sample_rate,
            self._fft_size,
            self._band_count,
        )
        current = _scale_snapshot(
            current,
            0.5 + self._sensitivity / 100.0 * 1.5,
        )
        attack = 0.52 - self._smoothness / 100.0 * 0.15
        release = 0.08 + self._smoothness / 100.0 * 0.22
        self._previous = smooth_snapshot(
            self._previous,
            current,
            attack=attack,
            release=release,
        )
        self.snapshotReady.emit(self._previous)


class AudioVisualizerController(QObject):
    """QML-facing snapshot bridge with explicit lifecycle and OFF behavior."""

    enabledChanged = Signal()
    snapshotChanged = Signal()
    rmsChanged = Signal()
    peakChanged = Signal()
    lowChanged = Signal()
    midChanged = Signal()
    highChanged = Signal()
    bandsChanged = Signal()
    _enabledRequested = Signal(bool)
    _configRequested = Signal(int, int, int)

    def __init__(
        self,
        tap: PcmSnapshotTap,
        parent: QObject | None = None,
        *,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        fft_size: int = _DEFAULT_FFT_SIZE,
        band_count: int = 32,
        auto_start: bool = True,
        initial_enabled: bool | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(tap, PcmSnapshotTap):
            raise TypeError("tap must be a PcmSnapshotTap")
        _validate_analysis_shape(sample_rate, fft_size, band_count)
        self._tap = tap
        self._snapshot = VisualAudioSnapshot.zero(
            sample_rate=sample_rate,
            band_count=band_count,
        )
        self._enabled = bool(tap.enabled if initial_enabled is None else initial_enabled)
        self._tap.set_enabled(self._enabled)
        self._thread: QThread | None = None
        self._worker: AudioVisualizerWorker | None = None
        self._shutdown = False
        self._sensitivity = 50
        self._smoothness = 68
        self._band_count = band_count
        if auto_start:
            self._thread = QThread(self)
            self._worker = AudioVisualizerWorker(
                tap,
                sample_rate=sample_rate,
                fft_size=fft_size,
                band_count=band_count,
            )
            self._worker.moveToThread(self._thread)
            self._worker.snapshotReady.connect(self._on_snapshot)
            self._enabledRequested.connect(self._worker.set_enabled)
            self._configRequested.connect(self._worker.set_config)
            self._thread.started.connect(self._worker.start)
            self._thread.finished.connect(self._worker.deleteLater)
            self._thread.start()
        else:
            self._worker = AudioVisualizerWorker(
                tap,
                sample_rate=sample_rate,
                fft_size=fft_size,
                band_count=band_count,
                parent=self,
            )
            self._worker.snapshotReady.connect(self._on_snapshot)

    @Property(bool, notify=enabledChanged)
    def enabled(self) -> bool:
        return self._enabled

    @Property(float, notify=rmsChanged)
    def rms(self) -> float:
        return self._snapshot.rms

    @Property(float, notify=peakChanged)
    def peak(self) -> float:
        return self._snapshot.peak

    @Property(float, notify=lowChanged)
    def low(self) -> float:
        return self._snapshot.low

    @Property(float, notify=midChanged)
    def mid(self) -> float:
        return self._snapshot.mid

    @Property(float, notify=highChanged)
    def high(self) -> float:
        return self._snapshot.high

    @Property("QVariantList", notify=bandsChanged)
    def bands(self) -> list[float]:
        return list(self._snapshot.bands)

    @Property(object, notify=snapshotChanged)
    def snapshot(self) -> VisualAudioSnapshot:
        return self._snapshot

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._enabled:
            return
        self._enabled = value
        self._tap.set_enabled(value)
        if not value:
            self._replace_snapshot(
                VisualAudioSnapshot.zero(
                    sample_rate=self._snapshot.sample_rate,
                    band_count=len(self._snapshot.bands),
                )
            )
        if self._thread is None:
            assert self._worker is not None
            self._worker.set_enabled(value)
        else:
            self._enabledRequested.emit(value)
        self.enabledChanged.emit()

    @Slot()
    def process_once(self) -> None:
        """Synchronous test/diagnostic hook; live UI uses the worker thread."""
        if self._thread is not None:
            return
        assert self._worker is not None
        self._worker.process_once()

    @Slot(int, int, int)
    def setConfig(self, sensitivity: int, smoothness: int, band_count: int) -> None:
        self._sensitivity = max(0, min(100, int(sensitivity)))
        self._smoothness = max(0, min(100, int(smoothness)))
        if band_count not in _SUPPORTED_BAND_COUNTS:
            band_count = 32
        self._band_count = band_count
        if len(self._snapshot.bands) != band_count:
            self._replace_snapshot(
                VisualAudioSnapshot.zero(
                    sample_rate=self._snapshot.sample_rate,
                    band_count=band_count,
                )
            )
        if self._thread is None:
            assert self._worker is not None
            self._worker.set_config(self._sensitivity, self._smoothness, band_count)
        else:
            self._configRequested.emit(self._sensitivity, self._smoothness, band_count)

    @Slot()
    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._tap.set_enabled(False)
        thread = self._thread
        if thread is None:
            if self._worker is not None:
                self._worker.stop()
            return
        self._thread = None
        thread.quit()
        if not thread.wait(1_000):
            thread.requestInterruption()
            thread.quit()
            thread.wait(1_000)

    @Slot(object)
    def _on_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, VisualAudioSnapshot):
            return
        if not self._enabled:
            return
        self._replace_snapshot(snapshot)

    def _replace_snapshot(self, snapshot: VisualAudioSnapshot) -> None:
        previous = self._snapshot
        self._snapshot = snapshot
        self.snapshotChanged.emit()
        if previous.rms != snapshot.rms:
            self.rmsChanged.emit()
        if previous.peak != snapshot.peak:
            self.peakChanged.emit()
        if previous.low != snapshot.low:
            self.lowChanged.emit()
        if previous.mid != snapshot.mid:
            self.midChanged.emit()
        if previous.high != snapshot.high:
            self.highChanged.emit()
        if previous.bands != snapshot.bands:
            self.bandsChanged.emit()
