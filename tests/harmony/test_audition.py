from __future__ import annotations

from array import array
from dataclasses import replace
from pathlib import Path
import wave

import pytest
from PySide6.QtCore import QObject, Signal

from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2
from ai_piano_studio.harmony.audition import HarmonyAuditionPlayer


class FakeEffect(QObject):
    playingChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.sources = []
        self.volumes = []
        self.play_calls = 0
        self.stop_calls = 0
        self._playing = False

    def setSource(self, source) -> None:
        self.sources.append(source)

    def setVolume(self, volume: float) -> None:
        self.volumes.append(volume)

    def play(self) -> None:
        self.play_calls += 1
        self._playing = True
        self.playingChanged.emit()

    def stop(self) -> None:
        self.stop_calls += 1
        self._playing = False
        self.playingChanged.emit()

    def isPlaying(self) -> bool:
        return self._playing


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize(
    ("pitches", "expected"),
    [
        ([60, 64, 67], [(60, 0.0, 1.2, 91), (64, 0.0, 1.2, 91), (67, 0.0, 1.2, 91)]),
        ([57, 60, 64], [(57, 0.0, 1.2, 91), (60, 0.0, 1.2, 91), (64, 0.0, 1.2, 91)]),
        ([65, 69, 72], [(65, 0.0, 1.2, 91), (69, 0.0, 1.2, 91), (72, 0.0, 1.2, 91)]),
    ],
    ids=["c-major", "a-minor", "f-major"],
)
def test_audition_renders_common_triads_simultaneously(qapp, pitches, expected) -> None:
    rendered = []
    player = HarmonyAuditionPlayer(
        effect=FakeEffect(),
        renderer=lambda notes: rendered.extend(notes) or array("h", [1, -1]),
    )

    player.audition(pitches, 91, 0.35)

    assert [(note.pitch, note.start_time, note.duration, note.velocity) for note in rendered] == expected


def test_audition_renders_three_simultaneous_temporary_notes_for_1_2_seconds(qapp) -> None:
    effect = FakeEffect()
    rendered = []

    def renderer(notes):
        rendered.extend(notes)
        return array("h", [1, -1])

    player = HarmonyAuditionPlayer(effect=effect, renderer=renderer)

    player.audition([60, 64, 67], 91, 0.35)

    assert [(note.pitch, note.start_time, note.duration, note.velocity) for note in rendered] == [
        (60, 0.0, 1.2, 91),
        (64, 0.0, 1.2, 91),
        (67, 0.0, 1.2, 91),
    ]
    assert effect.play_calls == 1
    # The current 4x master gain saturates this QSoundEffect volume at unity.
    assert effect.volumes == [1.0]
    assert player.playing is True
    assert Path(effect.sources[0].toLocalFile()).read_bytes()[:4] == b"RIFF"


def test_audition_renders_through_one_injected_piano_source_snapshot(qapp) -> None:
    """Fails if Harmony audition falls back to its module-level synth renderer."""
    effect = FakeEffect()
    rendered = []

    class Snapshot:
        output_channels = 2

        def render_project(self, notes):
            rendered.extend(notes)
            return array("h", [7, -7])

    class Source:
        def __init__(self) -> None:
            self.snapshot_calls = 0

        def snapshot_renderer(self):
            self.snapshot_calls += 1
            return Snapshot()

    source = Source()
    player = HarmonyAuditionPlayer(effect=effect, piano_source=source)

    player.audition([60, 64, 67], 88, 0.4)

    assert source.snapshot_calls == 1
    assert [note.pitch for note in rendered] == [60, 64, 67]
    assert effect.play_calls == 1
    with wave.open(effect.sources[0].toLocalFile(), "rb") as rendered_wav:
        assert rendered_wav.getnchannels() == 2


def test_stop_safely_retires_only_the_audition_effect(qtbot) -> None:
    effect = FakeEffect()
    player = HarmonyAuditionPlayer(effect=effect, renderer=lambda _notes: array("h", [0]))

    player.audition([60, 64, 67], 80, 0.8)
    player.stop()

    qtbot.waitUntil(lambda: effect.stop_calls == 1, timeout=500)
    assert effect.stop_calls == 1
    assert player.playing is False


def test_replacing_audition_safely_retires_old_effect_without_accumulating_sound(qtbot) -> None:
    effects = []

    def make_effect():
        effect = FakeEffect()
        effects.append(effect)
        return effect

    player = HarmonyAuditionPlayer(
        effect=make_effect(),
        effect_factory=make_effect,
        profile=replace(ACOUSTIC_PIANO_V2, safe_release_seconds=0.02),
        renderer=lambda _notes: array("h", [0]),
    )

    player.audition([60, 64, 67], 80, 0.8)
    player.audition([62, 65, 69], 80, 0.8)

    assert len(effects) == 2
    assert effects[0].isPlaying() is True
    assert effects[1].isPlaying() is True
    assert player.effectCount == 2

    player.audition([65, 69, 72], 80, 0.8)

    assert player.effectCount == 2
    assert effects[0].stop_calls == 1
    qtbot.waitUntil(lambda: player.effectCount == 1, timeout=500)
    assert sum(effect.isPlaying() for effect in effects) == 1


def test_repeated_replacements_reuse_retired_effect_resources(qapp) -> None:
    effects = []
    clock = FakeClock()

    def make_effect():
        effect = FakeEffect()
        effects.append(effect)
        return effect

    player = HarmonyAuditionPlayer(
        effect=make_effect(),
        effect_factory=make_effect,
        profile=replace(ACOUSTIC_PIANO_V2, safe_release_seconds=0.02),
        clock=clock,
        renderer=lambda _notes: array("h", [0]),
    )

    player.audition([60, 64, 67], 80, 0.8)
    for pitches in ([62, 65, 69], [65, 69, 72], [67, 71, 74], [69, 72, 76]):
        player.audition(pitches, 80, 0.8)
        clock.advance(0.02)
        player.process_retirements()

    assert len(effects) == 2
    assert player.effectCount == 1
    assert sum(effect.isPlaying() for effect in effects) == 1


def test_close_stop_fades_before_releasing_effect_resources(qapp) -> None:
    effects = []
    clock = FakeClock()

    def make_effect():
        effect = FakeEffect()
        effects.append(effect)
        return effect

    player = HarmonyAuditionPlayer(
        effect=make_effect(),
        effect_factory=make_effect,
        profile=replace(ACOUSTIC_PIANO_V2, stop_fade_seconds=0.04),
        clock=clock,
        renderer=lambda _notes: array("h", [0]),
    )
    player.audition([60, 64, 67], 80, 0.8)
    player.audition([62, 65, 69], 80, 0.8)

    player.close()

    assert len(effects) == 2
    assert all(effect.stop_calls == 0 for effect in effects)
    clock.advance(0.02)
    player.process_retirements()
    assert [effect.volumes[-1] for effect in effects] == pytest.approx([0.5, 0.5])
    assert all(effect.stop_calls == 0 for effect in effects)
    clock.advance(0.02)
    player.process_retirements()
    assert player.playing is False
    assert player.effectCount == 0
    assert all(effect.stop_calls == 1 for effect in effects)
    assert all(effect.sources[-1].isEmpty() for effect in effects)


def test_render_failure_logs_and_emits_concise_feedback(qapp, caplog) -> None:
    effect = FakeEffect()
    player = HarmonyAuditionPlayer(
        effect=effect,
        renderer=lambda _notes: (_ for _ in ()).throw(RuntimeError("renderer failed")),
    )
    feedback = []
    player.failed.connect(feedback.append)
    caplog.set_level("ERROR")

    player.audition([60, 64, 67], 80, 0.8)

    assert feedback == ["无法试听和弦"]
    assert effect.play_calls == 0
    assert player.playing is False
    assert "Harmony audition failed" in caplog.text
