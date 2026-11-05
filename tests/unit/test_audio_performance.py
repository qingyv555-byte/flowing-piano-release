from __future__ import annotations

from dataclasses import dataclass
from statistics import quantiles
from time import perf_counter

import pytest

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2
from ai_piano_studio.audio.realtime import RealtimeVoicePool
from ai_piano_studio.audio.sample_piano import SamplePianoEngine, SampleVoice, decode_wav
from ai_piano_studio.audio.source import ContinuousPianoToneSource
from ai_piano_studio.audio.synth import render_project
from ai_piano_studio.audio.tone import PianoToneSelection
from ai_piano_studio.domain.note import Note
from pathlib import Path
from typing import Callable


@dataclass
class FakeClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def chord_fixture(count: int) -> tuple[Note, ...]:
    return tuple(
        Note.create(
            id=f"performance-{index}",
            pitch=60,
            start_time=0.0,
            duration=0.35,
            velocity=127,
        )
        for index in range(count)
    )


def _p50_p95_p99(measurements: list[float]) -> tuple[float, float, float]:
    ordered = sorted(measurements)
    return (
        quantiles(ordered, n=100, method="inclusive")[49],
        quantiles(ordered, n=100, method="inclusive")[94],
        quantiles(ordered, n=100, method="inclusive")[98],
    )


def _warmed_real_sample_mixer() -> PianoVoiceMixer:
    project_root = Path(__file__).parents[2]
    engine = SamplePianoEngine(
        project_root / "resources/piano/banks/layers-06-11/manifest.json",
        scheduler=lambda _job: None,
        preload_c3_c5_on_init=False,
    )
    pitches = tuple(range(48, 72))
    assert engine.preload_roots_sync({engine.nearest_root(pitch) for pitch in pitches})
    selection = PianoToneSelection(0.0, engine, 1.0, None, 0.0, "layers-06-11")

    class Coordinator:
        def selection(self) -> PianoToneSelection:
            return selection

        def commit(self, _position: float) -> PianoToneSelection:
            return selection

    mixer = PianoVoiceMixer(sample_rate=48_000, max_voices=24)
    source = ContinuousPianoToneSource(Coordinator(), mixer)
    voices = tuple(
        source.start_voice(f"sample-{index}", pitch, 64)
        for index, pitch in enumerate(pitches)
    )
    assert all(
        all(isinstance(bank_voice, SampleVoice) for _engine, bank_voice in voice.bank_voices)
        for voice in voices
    )
    assert all(
        all(
            bank_voice.soft_gain > 0.0 and bank_voice.hard_gain > 0.0
            for _engine, bank_voice in voice.bank_voices
        )
        for voice in voices
    )
    mixer.mix_frames(480)
    assert len(mixer.active_voice_ids) == 24
    assert all(len(voice.contributions) == 2 for voice in mixer._voices)
    return mixer


def test_warmed_real_sample_voice_path_meets_ten_millisecond_realtime_budget() -> None:
    """Fails if actual warmed SamplePianoEngine voices exceed one 10-ms audio block."""
    mixer = _warmed_real_sample_mixer()
    measurements: list[float] = []

    for _ in range(21):
        started = perf_counter()
        payload = mixer.mix_frames(480)
        measurements.append(perf_counter() - started)
        assert len(payload) == 480 * 4

    _p50, p95, _p99 = _p50_p95_p99(measurements)
    assert p95 < 0.010


@pytest.mark.parametrize(
    ("count", "expected_output_bytes", "expected_peak"),
    [(1, 92672, 21071), (3, 92672, 30145), (8, 92672, 30145)],
)
def test_v2_chord_render_keeps_pcm_within_reference_budget(
    count: int, expected_output_bytes: int, expected_peak: int
) -> None:
    """Fails if v2 rendering loses its bounded offline PCM contract."""
    started = perf_counter()
    samples = render_project(chord_fixture(count), profile=ACOUSTIC_PIANO_V2)
    elapsed = perf_counter() - started

    assert elapsed < 2.0
    assert samples
    assert len(samples.tobytes()) == expected_output_bytes
    assert max(abs(value) for value in samples) == expected_peak
    assert expected_peak <= int(32767 * ACOUSTIC_PIANO_V2.peak_amplitude)


def test_rapid_repeated_realtime_notes_stay_within_global_voice_bound(
    qapp, monkeypatch
) -> None:
    """Repeated notes preserve overlap while honoring the global voice pool."""
    clock = FakeClock()
    mixer = PianoVoiceMixer(sample_rate=1_000, clock=clock)
    monkeypatch.setattr(mixer, "start_audio", lambda: None)
    pool = RealtimeVoicePool(mixer)
    source = (12_000,) * 4_096

    for _ in range(3 * 24):
        pool.start(60, 100, source, source_sample_rate=1_000)
        mixer.mix_frames(1)
        assert len(mixer.active_voice_ids) <= 24

    assert len(mixer.active_voice_ids) <= 24


def test_intermediate_tone_uses_only_two_warm_banks_without_decoding_on_note_on() -> None:
    """Fails if a committed blend selects a third bank or decodes on the input path."""
    project_root = Path(__file__).parents[2]
    decoder_calls: list[Path] = []
    scheduled: list[Callable[[], None]] = []

    def decoder(path: Path, *, sample_rate: int, channels: int, frame_count: int):
        decoder_calls.append(path)
        return decode_wav(
            path,
            sample_rate=sample_rate,
            channels=channels,
            frame_count=frame_count,
        )

    first = SamplePianoEngine(
        project_root / "resources/piano/banks/layers-06-11/manifest.json",
        decoder=decoder,
        scheduler=scheduled.append,
    )
    second = SamplePianoEngine(
        project_root / "resources/piano/banks/layers-07-12/manifest.json",
        decoder=decoder,
        scheduler=scheduled.append,
    )
    for job in scheduled:
        job()
    decoder_calls.clear()
    selection = PianoToneSelection(
        0.25,
        first,
        2**-0.5,
        second,
        2**-0.5,
        "layers-06-11",
        "layers-07-12",
    )

    class Coordinator:
        def selection(self) -> PianoToneSelection:
            return selection

        def commit(self, position: float) -> PianoToneSelection:
            assert position == 0.25
            return selection

    source = ContinuousPianoToneSource(Coordinator(), PianoVoiceMixer(sample_rate=32_000))
    assert source.set_tone(0.25).bank_ids == ("layers-06-11", "layers-07-12")

    voice = source.start_voice("hot", 60, 80)

    assert len(voice.bank_voices) == 2
    assert all(isinstance(bank_voice, SampleVoice) for _, bank_voice in voice.bank_voices)
    assert decoder_calls == []


@pytest.mark.parametrize("voice_count", (1, 3, 8, 16, 24))
def test_mixer_handles_target_polyphony_without_clipping(voice_count: int) -> None:
    """Fails if 24 simultaneous deterministic voices exceed the PCM range."""
    mixer = PianoVoiceMixer(sample_rate=1_000, max_voices=24)
    source = (32_767,) * 256
    for index in range(voice_count):
        mixer.start_voice(
            f"chord-{index}",
            48 + index,
            127,
            source,
            source_sample_rate=1_000,
            owner_tag="performance-test",
        )

    payload = mixer.mix_frames(64)
    samples = memoryview(payload).cast("h")

    assert len(mixer.active_voice_ids) <= 24
    assert max(abs(sample) for sample in samples) <= 32_767
