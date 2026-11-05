from __future__ import annotations

from array import array
from dataclasses import dataclass
import logging
from statistics import quantiles
from time import perf_counter

import pytest

from ai_piano_studio.audio.mixer import PCMContribution, PianoVoiceMixer
from ai_piano_studio.audio import mixer as mixer_module
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, ReleaseMode


SAMPLE_RATE = 1_000
PCM = (12_000,) * 4_096


@dataclass
class FakeClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _mixer(clock: FakeClock | None = None) -> PianoVoiceMixer:
    return PianoVoiceMixer(
        profile=ACOUSTIC_PIANO_V2,
        sample_rate=SAMPLE_RATE,
        max_voices=24,
        clock=clock or FakeClock(),
    )


def _start(
    mixer: PianoVoiceMixer,
    voice_id: str,
    pitch: int = 60,
    velocity: int = 127,
    owner_tag: str = "current",
) -> None:
    mixer.start_voice(
        voice_id,
        pitch,
        velocity,
        PCM,
        source_sample_rate=SAMPLE_RATE,
        owner_tag=owner_tag,
        pan=0.0,
    )


def _samples(payload: bytes) -> array[int]:
    result = array("h")
    result.frombytes(payload)
    return result


def test_zero_frame_mix_and_subframe_device_read_are_true_noops() -> None:
    """Fails if an empty pull removes a queued voice or advances mixer lifecycle state."""
    mixer = _mixer()
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)
    _start(mixer, "held")

    assert mixer.mix_frames(0) == b""
    assert mixer.active_voice_ids == ("held",)
    assert mixer._output_frame == 0
    assert finished == []

    assert mixer.io_device.readData(3) == b"\0\0\0"
    assert mixer.active_voice_ids == ("held",)
    assert mixer._output_frame == 0
    assert finished == []


def _contribution(
    samples: memoryview | tuple[int, ...],
    *,
    channels: int = 1,
    gain: float = 1.0,
    pan: float = 0.0,
) -> PCMContribution:
    return PCMContribution(samples, SAMPLE_RATE, channels, gain, pan)


def _realtime_percentiles(measurements: list[float]) -> tuple[float, float, float]:
    """Return deterministic nearest-rank P50/P95/P99 wall-clock percentiles."""
    ordered = sorted(measurements)
    return (
        quantiles(ordered, n=100, method="inclusive")[49],
        quantiles(ordered, n=100, method="inclusive")[94],
        quantiles(ordered, n=100, method="inclusive")[98],
    )


def _warmed_realtime_mixer(*, sample_voice: bool) -> PianoVoiceMixer:
    mixer = PianoVoiceMixer(sample_rate=48_000, max_voices=24)
    frames = 48_000
    stereo_pcm = memoryview(
        array("h", (value for frame in range(frames) for value in (frame % 8_000, -(frame % 8_000))))
    ).toreadonly()
    legacy_pcm = memoryview(array("h", (frame % 8_000 for frame in range(frames)))).toreadonly()
    for index in range(24):
        if sample_voice:
            contributions = tuple(
                PCMContribution(
                    stereo_pcm,
                    44_100 + (index % 2) * 3_900,
                    2,
                    0.25,
                    -0.12 + 0.08 * layer,
                )
                for layer in range(4)
            )
        else:
            contributions = (PCMContribution(legacy_pcm, 48_000, 1, 1.0, 0.0),)
        mixer.start_contributions(
            f"benchmark-{index}",
            48 + index,
            96,
            contributions,
            owner_tag="benchmark",
        )
    mixer.mix_frames(480)
    return mixer


def test_warmed_24_voice_four_layer_mixer_stays_within_diagnostic_budget() -> None:
    """Lower-level four-layer diagnostic; the real Sample path owns the 10-ms target."""
    sample_mixer = _warmed_realtime_mixer(sample_voice=True)
    legacy_mixer = _warmed_realtime_mixer(sample_voice=False)
    sample_measurements: list[float] = []
    legacy_measurements: list[float] = []

    for _ in range(21):
        started = perf_counter()
        sample_mixer.mix_frames(480)
        sample_measurements.append(perf_counter() - started)
        started = perf_counter()
        legacy_mixer.mix_frames(480)
        legacy_measurements.append(perf_counter() - started)

    sample_p50, sample_p95, sample_p99 = _realtime_percentiles(sample_measurements)
    legacy_p50, legacy_p95, legacy_p99 = _realtime_percentiles(legacy_measurements)
    assert sample_p50 >= 0.0
    assert sample_p99 >= sample_p95
    assert legacy_p50 >= 0.0
    assert legacy_p99 >= legacy_p95
    assert sample_p95 < 0.020, (
        f"24 Sample voices P50/P95/P99={sample_p50:.6f}/{sample_p95:.6f}/{sample_p99:.6f}s; "
        f"equivalent Legacy P50/P95/P99={legacy_p50:.6f}/{legacy_p95:.6f}/{legacy_p99:.6f}s"
    )


def test_contribution_voice_keeps_shared_buffers_and_sums_four_layers() -> None:
    """Fails if multi-bank/layer PCM is copied or only one contribution is mixed."""
    mixer = _mixer()
    buffers = tuple(
        memoryview(array("h", [1_000] * 64)).toreadonly() for _ in range(4)
    )

    mixer.start_contributions(
        "four",
        60,
        127,
        tuple(_contribution(buffer, gain=0.25) for buffer in buffers),
        owner_tag="tone",
    )
    mixed = _samples(mixer.mix_frames(20))

    voice = mixer._voices[0]
    assert len(voice.contributions) == 4
    assert tuple(item.samples for item in voice.contributions) == buffers
    assert mixed[-2] == pytest.approx(2828, abs=2)
    assert mixed[-1] == pytest.approx(2828, abs=2)


def test_contribution_stereo_interpolation_and_pan_are_applied_per_channel() -> None:
    """Fails if stereo frames are read as mono samples or pan ignores channel layout."""
    mixer = _mixer()
    stereo = memoryview(
        array("h", [0, 4_000] + [2_000, 6_000] * 63)
    ).toreadonly()
    mixer.start_contributions(
        "stereo",
        60,
        127,
        (_contribution(stereo, channels=2, pan=-1.0),),
        owner_tag="tone",
    )

    mixed = _samples(mixer.mix_frames(20))

    assert mixed[-2] == pytest.approx(8_000, abs=2)
    assert mixed[-1] == 0


def test_full_scale_interpolation_keeps_the_signed_int16_delta() -> None:
    """Fails if vector interpolation subtracts int16 samples before widening them."""
    mixer = _mixer()
    mixer.start_contributions(
        "full-scale",
        60,
        127,
        (PCMContribution((32_767, -32_768), 500, 1, 1.0, 0.0),),
        owner_tag="tone",
    )

    mixed = _samples(mixer.mix_frames(2))

    # Halfway between signed int16 extremes is -0.5; the 4x output gain
    # now rounds this attack-frame value to -1 rather than zero.
    assert mixed[-2:] == array("h", [-1, -1])


def test_looped_interpolation_wraps_from_the_last_frame_to_the_first() -> None:
    """Fails if short fallback loops hold the final sample at every wrap boundary."""
    samples = memoryview(array("h", [1_000, -1_000])).toreadonly()

    left, right = mixer_module._interpolated_frame(samples, 1.5, 1, looping=True)

    assert (left, right) == (0.0, 0.0)


@pytest.mark.parametrize("ending", ("exhaustion", "natural", "safe", "stop"))
def test_finish_voice_emits_exactly_once_for_pcm_end_and_releases(ending: str) -> None:
    """Fails if a normal completion path omits or duplicates lifecycle notification."""
    mixer = _mixer()
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)
    pcm = (12_000,) * (8 if ending == "exhaustion" else 4_096)
    mixer.start_contributions(
        "voice", 60, 127, (_contribution(pcm),), owner_tag="tone"
    )
    mixer.mix_frames(8)
    if ending != "exhaustion":
        mode = {
            "natural": ReleaseMode.NATURAL,
            "safe": ReleaseMode.SAFE,
            "stop": ReleaseMode.STOP,
        }[ending]
        mixer.note_off("voice", mode)
        mixer.mix_frames({"natural": 770, "safe": 120, "stop": 12}[ending])
    mixer.mix_frames(2)

    assert finished == ["voice"]


def test_finish_voice_emits_for_replacement_and_voice_steal_without_priority_change() -> None:
    """Fails if replacement/theft leak ownership or alter oldest-victim selection."""
    clock = FakeClock()
    mixer = _mixer(clock)
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)
    _start(mixer, "replacement")
    mixer.mix_frames(1)
    _start(mixer, "replacement")
    mixer.mix_frames(1)
    for index in range(23):
        clock.advance(0.01)
        _start(mixer, f"held-{index}", pitch=30 + index, velocity=100)
    mixer.mix_frames(20)
    clock.advance(0.01)
    _start(mixer, "new", pitch=90, velocity=100)
    mixer.mix_frames(1)

    assert finished.count("replacement") == 2
    assert "new" in mixer.active_voice_ids
    assert len(mixer.active_voice_ids) == 24


def test_same_pitch_replacement_finishes_exact_oldest_victim_once() -> None:
    """Same-pitch retriggers keep every voice until the global pool is full."""
    clock = FakeClock()
    mixer = _mixer(clock)
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)
    for index in range(3):
        _start(mixer, f"same-{index}", pitch=60)
        mixer.mix_frames(1)
        clock.advance(0.01)

    _start(mixer, "same-new", pitch=60)
    mixer.mix_frames(1)
    mixer.mix_frames(1)

    assert mixer.active_voice_ids == ("same-0", "same-1", "same-2", "same-new")
    assert finished == []


def test_ten_same_pitch_retriggers_keep_every_voice_until_its_own_release() -> None:
    mixer = _mixer()
    for index in range(10):
        _start(mixer, f"repeat-{index}", pitch=60)
        mixer.mix_frames(100)

    assert len(mixer.active_voice_ids) == 10
    assert set(mixer.active_voice_ids) == {f"repeat-{index}" for index in range(10)}

    for index in range(10):
        mixer.note_off(
            f"repeat-{index}", ReleaseMode.NATURAL, release_seconds=0.3
        )
    mixer.mix_frames(300)

    assert mixer.active_voice_ids == ()


def test_custom_release_uses_override_and_cut_still_finishes_quickly() -> None:
    mixer = _mixer()
    _start(mixer, "custom")
    mixer.mix_frames(8)

    mixer.note_off("custom", ReleaseMode.NATURAL, release_seconds=0.42)
    mixer.mix_frames(419)
    assert "custom" in mixer.active_voice_ids
    mixer.mix_frames(2)
    assert "custom" not in mixer.active_voice_ids


def test_24_voice_limit_finishes_exact_oldest_victim_once() -> None:
    """Fails if hard-cap theft removes or reports a voice other than the oldest."""
    clock = FakeClock()
    mixer = _mixer(clock)
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)
    for index in range(24):
        _start(mixer, f"voice-{index}", pitch=30 + index, velocity=100)
        clock.advance(0.01)
    mixer.mix_frames(20)

    _start(mixer, "new", pitch=90, velocity=100)
    mixer.mix_frames(1)
    mixer.mix_frames(1)

    assert "voice-0" not in mixer.active_voice_ids
    assert len(mixer.active_voice_ids) == 24
    assert finished == ["voice-0"]


def test_constructor_validation_logs_before_raising(caplog: pytest.LogCaptureFixture) -> None:
    """Fails if invalid mixer setup raises without the required diagnostic log."""
    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.mixer"):
        with pytest.raises(ValueError, match="sample_rate"):
            PianoVoiceMixer(sample_rate=0)

    assert "Failed to construct piano voice mixer" in caplog.text


def test_constructor_rejects_voice_limits_above_hard_24_cap() -> None:
    """Fails if callers can bypass the required 24-voice ceiling."""
    with pytest.raises(ValueError, match="max_voices"):
        PianoVoiceMixer(max_voices=25)


def test_natural_note_off_keeps_tail_until_hand_calculated_release_end() -> None:
    """Fails if note_off cuts PCM or disposes before C4's 770-frame tail."""
    mixer = _mixer()
    _start(mixer, "natural")
    mixer.mix_frames(8)

    mixer.note_off("natural", ReleaseMode.NATURAL)
    tail_before_end = _samples(mixer.mix_frames(769))

    assert any(tail_before_end)
    assert mixer.active_voice_ids == ("natural",)

    final_frame = _samples(mixer.mix_frames(1))

    assert len(final_frame) == 2
    assert mixer.active_voice_ids == ()


def test_safe_and_stop_releases_use_120_and_12_frames_exactly() -> None:
    """Fails if SAFE ignores the profile or STOP is not the 12 ms repair fade."""
    mixer = _mixer()
    _start(mixer, "safe")
    _start(mixer, "stop", pitch=61)
    mixer.mix_frames(8)

    mixer.note_off("safe", ReleaseMode.SAFE)
    mixer.note_off("stop", ReleaseMode.STOP)
    mixer.mix_frames(11)

    assert set(mixer.active_voice_ids) == {"safe", "stop"}

    mixer.mix_frames(1)

    assert mixer.active_voice_ids == ("safe",)

    mixer.mix_frames(107)

    assert mixer.active_voice_ids == ("safe",)

    mixer.mix_frames(1)

    assert mixer.active_voice_ids == ()


def test_release_all_stop_escalates_a_natural_tail_to_twelve_frames() -> None:
    """Fails if global STOP leaves a longer NATURAL envelope in flight."""
    mixer = _mixer()
    _start(mixer, "natural")
    _start(mixer, "held", pitch=64)
    mixer.mix_frames(8)
    mixer.note_off("natural", ReleaseMode.NATURAL)
    mixer.mix_frames(1)

    mixer.release_all(ReleaseMode.STOP)
    mixer.mix_frames(11)

    assert set(mixer.active_voice_ids) == {"natural", "held"}

    mixer.mix_frames(1)

    assert mixer.active_voice_ids == ()


def test_voice_limit_prefers_quiet_releasing_voice() -> None:
    """Fails if a louder/held voice is stolen before a quiet release tail."""
    mixer = _mixer()
    for index in range(22):
        _start(mixer, f"held-{index}", pitch=30 + index, velocity=100)
    _start(mixer, "loud-release", pitch=80, velocity=100)
    _start(mixer, "quiet-release", pitch=81, velocity=1)
    mixer.mix_frames(20)
    mixer.note_off("loud-release", ReleaseMode.NATURAL)
    mixer.note_off("quiet-release", ReleaseMode.NATURAL)
    mixer.mix_frames(1)

    _start(mixer, "new", pitch=82)
    mixer.mix_frames(1)

    assert "quiet-release" not in mixer.active_voice_ids
    assert "loud-release" in mixer.active_voice_ids
    assert "new" in mixer.active_voice_ids
    assert len(mixer.active_voice_ids) == 24


def test_full_pool_same_pitch_retrigger_still_prefers_quiet_release_victim() -> None:
    """The global pool still prefers a quiet release over a same-pitch voice."""
    mixer = _mixer()
    for index in range(20):
        _start(mixer, f"held-{index}", pitch=30 + index, velocity=100)
    for index in range(3):
        _start(mixer, f"same-{index}", pitch=60, velocity=100)
    _start(mixer, "quiet-release", pitch=80, velocity=1)
    mixer.mix_frames(20)
    mixer.note_off("quiet-release", ReleaseMode.NATURAL)
    mixer.mix_frames(1)

    _start(mixer, "same-new", pitch=60, velocity=100)
    mixer.mix_frames(1)

    same_pitch_ids = {"same-0", "same-1", "same-2", "same-new"}
    assert "quiet-release" not in mixer.active_voice_ids
    assert "same-new" in mixer.active_voice_ids
    assert len(same_pitch_ids.intersection(mixer.active_voice_ids)) == 4
    assert len(mixer.active_voice_ids) <= 24


def test_voice_limit_then_prefers_oldest_releasing_voice() -> None:
    """Fails if release age is ignored when no release tail is quiet."""
    clock = FakeClock()
    mixer = _mixer(clock)
    for index in range(22):
        _start(mixer, f"held-{index}", pitch=30 + index, velocity=100)
    _start(mixer, "older-release", pitch=80, velocity=100)
    clock.advance(0.1)
    _start(mixer, "newer-release", pitch=81, velocity=100)
    mixer.mix_frames(20)
    mixer.note_off("older-release", ReleaseMode.NATURAL)
    clock.advance(0.1)
    mixer.note_off("newer-release", ReleaseMode.NATURAL)
    mixer.mix_frames(1)

    _start(mixer, "new", pitch=82)
    mixer.mix_frames(1)

    assert "older-release" not in mixer.active_voice_ids
    assert "newer-release" in mixer.active_voice_ids
    assert "new" in mixer.active_voice_ids


def test_voice_limit_then_prefers_quiet_non_current_owner() -> None:
    """Fails if a current-owner voice is stolen before quiet stale ownership."""
    mixer = _mixer()
    for index in range(22):
        _start(mixer, f"held-{index}", pitch=30 + index, velocity=100)
    _start(mixer, "quiet-current", pitch=80, velocity=1)
    _start(mixer, "quiet-previous", pitch=81, velocity=1, owner_tag="previous")
    mixer.mix_frames(20)

    _start(mixer, "new", pitch=82, owner_tag="current")
    mixer.mix_frames(1)

    assert "quiet-previous" not in mixer.active_voice_ids
    assert "quiet-current" in mixer.active_voice_ids
    assert "new" in mixer.active_voice_ids


def test_voice_limit_final_fallback_steals_oldest_voice() -> None:
    """Fails if the fallback is not stable by voice age or steals the new voice."""
    clock = FakeClock()
    mixer = _mixer(clock)
    for index in range(24):
        _start(mixer, f"voice-{index}", pitch=30 + index, velocity=100)
        clock.advance(0.01)
    mixer.mix_frames(20)

    _start(mixer, "new", pitch=90, velocity=100)
    mixer.mix_frames(1)

    assert "voice-0" not in mixer.active_voice_ids
    assert "voice-1" in mixer.active_voice_ids
    assert "new" in mixer.active_voice_ids
    assert len(mixer.active_voice_ids) == 24


def test_adjacent_same_pitch_starts_overlap_but_rapid_repeats_stay_bounded() -> None:
    """Retriggers overlap without cutting attacks and respect the global pool."""
    mixer = _mixer()
    _start(mixer, "first")
    mixer.mix_frames(1)
    _start(mixer, "second")
    mixer.mix_frames(1)

    assert set(mixer.active_voice_ids) == {"first", "second"}

    for index in range(30):
        voice_id = f"repeat-{index}"
        _start(mixer, voice_id)
        mixer.mix_frames(1)
        assert len(mixer.active_voice_ids) <= 24
        assert voice_id in mixer.active_voice_ids


def test_mix_is_stereo_peak_protected_and_continuous_at_stop_boundary() -> None:
    """Fails on mono/invalid output, clipping, or an abrupt 12 ms stop cutoff."""
    mixer = _mixer()
    loud_pcm = (32_767,) * 4_096
    for index in range(24):
        mixer.start_voice(
            f"loud-{index}",
            30 + index,
            127,
            loud_pcm,
            source_sample_rate=SAMPLE_RATE,
            owner_tag="current",
            pan=(-1.0 + 2.0 * index / 23.0),
        )

    mixed = _samples(mixer.mix_frames(20))
    peak = int(32_767 * ACOUSTIC_PIANO_V2.peak_amplitude)

    assert len(mixed) == 40
    assert max(abs(sample) for sample in mixed) <= peak
    assert all(isinstance(sample, int) for sample in mixed)

    stop_mixer = _mixer()
    _start(stop_mixer, "stop")
    before = _samples(stop_mixer.mix_frames(20))
    stop_mixer.note_off("stop", ReleaseMode.STOP)
    boundary = _samples(stop_mixer.mix_frames(13))
    left_channel = [before[-2], *boundary[0::2]]

    assert max(abs(right - left) for left, right in zip(left_channel, left_channel[1:])) <= 32_767
    assert boundary[-2:] == array("h", [0, 0])
