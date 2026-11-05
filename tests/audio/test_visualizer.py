from __future__ import annotations

import math

import numpy as np
import pytest

from ai_piano_studio.audio.visualizer import (
    AudioVisualizerWorker,
    VisualAudioSnapshot,
    analyze_pcm,
    smooth_snapshot,
)


SAMPLE_RATE = 48_000
FFT_SIZE = 1_024


def _stereo_tone_payload(
    frequency: float,
    amplitude: float = 0.5,
    *,
    sample_rate: int = SAMPLE_RATE,
    frame_count: int = FFT_SIZE,
) -> bytes:
    timeline = np.arange(frame_count, dtype=np.float64) / sample_rate
    wave = np.rint(
        np.sin(2.0 * math.pi * frequency * timeline) * amplitude * 32_767
    ).astype("<i2")
    stereo = np.column_stack((wave, wave))
    return stereo.astype("<i2", copy=False).tobytes()


def test_analyze_pcm_maps_low_mid_and_high_tones_to_expected_bands() -> None:
    low = analyze_pcm(_stereo_tone_payload(80.0), SAMPLE_RATE, FFT_SIZE, 32)
    mid = analyze_pcm(_stereo_tone_payload(1_000.0), SAMPLE_RATE, FFT_SIZE, 32)
    high = analyze_pcm(_stereo_tone_payload(8_000.0), SAMPLE_RATE, FFT_SIZE, 32)

    assert low.low > low.mid
    assert low.low > low.high
    assert mid.mid > mid.low
    assert mid.mid > mid.high
    assert high.high > high.low
    assert high.high > high.mid


def test_analyze_pcm_returns_zero_for_silence_without_fake_motion() -> None:
    silence = bytes(FFT_SIZE * 4)

    snapshot = analyze_pcm(silence, SAMPLE_RATE, FFT_SIZE, 32)

    assert snapshot.rms == 0.0
    assert snapshot.peak == 0.0
    assert snapshot.low == 0.0
    assert snapshot.mid == 0.0
    assert snapshot.high == 0.0
    assert snapshot.bands == (0.0,) * 32


def test_analyze_pcm_energy_is_monotonic_for_increasing_amplitude() -> None:
    snapshots = [
        analyze_pcm(_stereo_tone_payload(440.0, amplitude), SAMPLE_RATE, FFT_SIZE, 32)
        for amplitude in (0.1, 0.5, 1.0)
    ]

    assert snapshots[0].rms < snapshots[1].rms < snapshots[2].rms
    assert snapshots[0].peak < snapshots[1].peak < snapshots[2].peak
    assert snapshots[0].mid < snapshots[1].mid < snapshots[2].mid


def test_analyze_pcm_returns_immutable_bounded_snapshot() -> None:
    snapshot = analyze_pcm(_stereo_tone_payload(440.0), SAMPLE_RATE, FFT_SIZE, 24)

    assert isinstance(snapshot, VisualAudioSnapshot)
    assert len(snapshot.bands) == 24
    assert all(0.0 <= value <= 1.0 for value in snapshot.bands)
    assert 0.0 <= snapshot.rms <= 1.0
    assert 0.0 <= snapshot.peak <= 1.0
    with pytest.raises(AttributeError):
        snapshot.rms = 0.0  # type: ignore[misc]


def test_smooth_snapshot_attacks_fast_and_releases_slowly() -> None:
    empty = VisualAudioSnapshot.zero(sample_rate=SAMPLE_RATE, band_count=32)
    loud = VisualAudioSnapshot(
        rms=1.0,
        peak=1.0,
        low=1.0,
        mid=1.0,
        high=1.0,
        bands=(1.0,) * 32,
        sample_rate=SAMPLE_RATE,
        timestamp=1.0,
    )

    attack = smooth_snapshot(empty, loud, attack=0.5, release=0.1)
    release = smooth_snapshot(loud, empty, attack=0.5, release=0.1)

    assert attack.rms == pytest.approx(0.5)
    assert release.rms == pytest.approx(0.9)
    assert attack.bands == (0.5,) * 32
    assert release.bands == (0.9,) * 32


def test_smoothing_silent_snapshots_decay_to_near_zero() -> None:
    loud = VisualAudioSnapshot(
        rms=1.0,
        peak=1.0,
        low=1.0,
        mid=1.0,
        high=1.0,
        bands=(1.0,) * 32,
        sample_rate=SAMPLE_RATE,
        timestamp=1.0,
    )
    empty = VisualAudioSnapshot.zero(sample_rate=SAMPLE_RATE, band_count=32)

    current = loud
    for _ in range(40):
        current = smooth_snapshot(current, empty, attack=0.5, release=0.18)

    assert current.rms < 0.001
    assert max(current.bands) < 0.001


def test_worker_emits_real_pcm_snapshot_and_decays_when_input_is_silent() -> None:
    from ai_piano_studio.audio.visualizer import PcmSnapshotTap

    tap = PcmSnapshotTap()
    worker = AudioVisualizerWorker(tap, sample_rate=SAMPLE_RATE, band_count=32)
    snapshots: list[VisualAudioSnapshot] = []
    worker.snapshotReady.connect(snapshots.append)

    tap.publish(_stereo_tone_payload(1_000.0, 0.8))
    worker.process_once()
    loud = snapshots[-1]
    for _ in range(45):
        tap.publish(bytes(FFT_SIZE * 4))
        worker.process_once()
    quiet = snapshots[-1]

    assert loud.rms > 0.1
    assert loud.mid > loud.low
    assert quiet.rms < 0.001
    assert quiet.bands == pytest.approx((0.0,) * 32, abs=0.001)


def test_worker_disabled_does_not_read_or_emit_pcm() -> None:
    from ai_piano_studio.audio.visualizer import PcmSnapshotTap

    tap = PcmSnapshotTap()
    worker = AudioVisualizerWorker(tap, sample_rate=SAMPLE_RATE, band_count=32)
    snapshots: list[VisualAudioSnapshot] = []
    worker.snapshotReady.connect(snapshots.append)
    worker.set_enabled(False)
    tap.publish(_stereo_tone_payload(1_000.0))

    worker.process_once()

    assert snapshots[-1].rms == 0.0
    assert tap.publish_count == 0
