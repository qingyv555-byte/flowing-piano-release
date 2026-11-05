import json
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QObject, QThreadPool, Signal

from ai_piano_studio.audio import player as player_module
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.synth import render_project as render_default_project
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_json import load_project
from ai_piano_studio.templates.library import TemplateMusicLibrary


TEMPLATE_IDS = (
    "ode-to-joy",
    "twinkle-star",
    "fur-elise",
    "canon-progression",
    "minuet-in-g",
    "moonlight-theme",
)

TEMPLATE_RESOURCE_CONTRACTS = {
    "ode-to-joy": (116, (4, 4), "beginner"),
    "twinkle-star": (96, (4, 4), "beginner"),
    "fur-elise": (88, (3, 8), "advanced"),
    "canon-progression": (92, (4, 4), "intermediate"),
    "minuet-in-g": (96, (3, 4), "intermediate"),
    "moonlight-theme": (72, (2, 2), "advanced"),
}

RESOURCE_BEAT_CHECKPOINTS = {
    "ode-to-joy": {
        ("ode-to-joy-melody-012", 64, 12.0, 1.5),
        ("ode-to-joy-melody-013", 62, 13.5, 0.5),
        ("ode-to-joy-melody-014", 62, 14.0, 2.0),
    },
    "twinkle-star": {
        ("twinkle-star-melody-000", 60, 0.0, 1.0),
        ("twinkle-star-melody-001", 60, 1.0, 1.0),
        ("twinkle-star-melody-006", 67, 6.0, 2.0),
    },
    "fur-elise": {
        ("fur-elise-melody-000", 76, 1.0, 0.25),
        ("fur-elise-melody-001", 75, 1.25, 0.25),
        ("fur-elise-melody-008", 69, 3.0, 0.5),
        ("fur-elise-melody-009", 60, 3.75, 0.25),
        ("fur-elise-bass-000", 45, 3.0, 0.25),
        ("fur-elise-arpeggio-000", 52, 3.25, 0.25),
    },
    "canon-progression": {
        ("canon-progression-bass-000", 50, 0.0, 2.0),
        ("canon-progression-arpeggio-000", 54, 0.5, 0.5),
        ("canon-progression-melody-000", 78, 16.0, 2.0),
    },
    "minuet-in-g": {
        ("minuet-in-g-melody-000", 74, 0.0, 1.0),
        ("minuet-in-g-melody-001", 67, 1.0, 0.5),
        ("minuet-in-g-bass-000", 55, 0.0, 2.0),
    },
    "moonlight-theme": {
        ("moonlight-theme-triplet-000", 56, 0.0, 1 / 3),
        ("moonlight-theme-triplet-001", 61, 1 / 3, 1 / 3),
        ("moonlight-theme-upper-000", 68, 19.0, 0.75),
        ("moonlight-theme-upper-001", 68, 19.75, 0.25),
        ("moonlight-theme-upper-002", 68, 20.0, 3.0),
        ("moonlight-theme-bass-000", 37, 0.0, 4.0),
    },
}


def test_score_events_convert_beats_rests_polyphony_and_voice_ids() -> None:
    from tools.generate_builtin_templates import ScoreEvent, _events_to_notes

    tempo = TempoMap(bpm=120)
    events = (
        ScoreEvent(60, 0.0, 1.0, 80, "melody"),
        ScoreEvent(48, 0.0, 2.0, 66, "bass"),
        ScoreEvent(62, 1.5, 0.5, 80, "melody"),
    )

    notes = _events_to_notes("fixture", events, tempo)

    assert [(note.pitch, note.start_time, note.duration) for note in notes] == [
        (48, 0.0, 1.0),
        (60, 0.0, 0.5),
        (62, 0.75, 0.25),
    ]
    assert [note.id for note in notes] == [
        "fixture-bass-000",
        "fixture-melody-000",
        "fixture-melody-001",
    ]
    assert notes[0].start_time == notes[1].start_time
    assert notes[1].start_time + notes[1].duration < notes[2].start_time


@pytest.mark.parametrize(
    "values",
    (
        {"pitch": -1, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80, "voice": "melody"},
        {"pitch": 128, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80, "voice": "melody"},
        {"pitch": 60, "start_beat": -0.5, "duration_beats": 1.0, "velocity": 80, "voice": "melody"},
        {"pitch": 60, "start_beat": 0.0, "duration_beats": 0.0, "velocity": 80, "voice": "melody"},
        {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 0, "voice": "melody"},
        {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 128, "voice": "melody"},
        {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80, "voice": ""},
    ),
)
def test_score_event_rejects_invalid_musical_values(values) -> None:
    from tools.generate_builtin_templates import ScoreEvent

    with pytest.raises(ValueError):
        ScoreEvent(**values)


def test_score_event_conversion_rejects_duplicate_voice_onsets() -> None:
    from tools.generate_builtin_templates import ScoreEvent, _events_to_notes

    duplicate = (
        ScoreEvent(60, 0.0, 1.0, 80, "melody"),
        ScoreEvent(60, 0.0, 0.5, 72, "melody"),
    )

    with pytest.raises(ValueError, match="duplicate score event"):
        _events_to_notes("fixture", duplicate, TempoMap(bpm=120))


def _score_spec(template_id: str):
    from tools.generate_builtin_templates import TEMPLATE_SPECS

    return next(spec for spec in TEMPLATE_SPECS if spec["id"] == template_id)


def _event_signature(event) -> tuple[str, int, float, float]:
    return event.voice, event.pitch, event.start_beat, event.duration_beats


def test_ode_to_joy_uses_source_rhythm_and_sparse_harmony() -> None:
    spec = _score_spec("ode-to-joy")
    events = spec["events"]
    signatures = {_event_signature(event) for event in events}

    assert spec["meter"] == (4, 4)
    assert spec["bpm"] == 116
    assert spec["difficulty"] == "beginner"
    assert {
        ("melody", 64, 0.0, 1.0),
        ("melody", 64, 1.0, 1.0),
        ("melody", 65, 2.0, 1.0),
        ("melody", 67, 3.0, 1.0),
        ("melody", 64, 12.0, 1.5),
        ("melody", 62, 13.5, 0.5),
        ("melody", 62, 14.0, 2.0),
        ("melody", 64, 37.0, 0.5),
        ("melody", 65, 37.5, 0.5),
        ("bass", 48, 0.0, 4.0),
        ("harmony", 55, 0.0, 4.0),
    } <= signatures
    melody = [event for event in events if event.voice == "melody"]
    support = [event for event in events if event.voice != "melody"]
    assert max(event.start_beat + event.duration_beats for event in melody) == 64.0
    assert len(support) < len(melody) / 2
    assert any(
        first.start_beat == second.start_beat
        for first in support
        for second in support
        if first.voice != second.voice
    )


def test_twinkle_star_preserves_complete_theme_attacks_and_held_endings() -> None:
    spec = _score_spec("twinkle-star")
    events = spec["events"]
    melody = [event for event in events if event.voice == "melody"]
    phrase_pitches = (
        (60, 60, 67, 67, 69, 69, 67),
        (65, 65, 64, 64, 62, 62, 60),
        (67, 67, 65, 65, 64, 64, 62),
        (67, 67, 65, 65, 64, 64, 62),
        (60, 60, 67, 67, 69, 69, 67),
        (65, 65, 64, 64, 62, 62, 60),
    )

    assert spec["meter"] == (4, 4)
    assert spec["bpm"] == 96
    assert spec["difficulty"] == "beginner"
    assert tuple(event.pitch for event in melody) == tuple(
        pitch for phrase in phrase_pitches for pitch in phrase
    )
    assert len(melody) == 42
    assert [event.start_beat for event in melody[:7]] == [0, 1, 2, 3, 4, 5, 6]
    assert melody[0].pitch == melody[1].pitch == 60
    assert melody[0].duration_beats == melody[1].duration_beats == 1.0
    assert [event.start_beat for event in melody if event.duration_beats == 2.0] == [
        6,
        14,
        22,
        30,
        38,
        46,
    ]
    assert max(event.start_beat + event.duration_beats for event in melody) == 48.0
    assert {event.voice for event in events} == {"bass", "harmony", "melody"}
    assert sum(event.voice != "melody" for event in events) < len(melody) / 2


def test_minuet_in_g_preserves_three_four_dance_pulse_and_cadence() -> None:
    spec = _score_spec("minuet-in-g")
    events = spec["events"]
    signatures = {_event_signature(event) for event in events}

    assert spec["meter"] == (3, 4)
    assert spec["bpm"] == 96
    assert spec["difficulty"] == "intermediate"
    assert {
        ("melody", 74, 0.0, 1.0),
        ("melody", 67, 1.0, 0.5),
        ("melody", 69, 1.5, 0.5),
        ("melody", 71, 2.0, 0.5),
        ("melody", 72, 2.5, 0.5),
        ("bass", 55, 0.0, 2.0),
        ("bass", 59, 0.0, 2.0),
        ("bass", 62, 0.0, 2.0),
        ("melody", 67, 45.0, 3.0),
        ("bass", 43, 47.0, 1.0),
    } <= signatures
    assert max(event.start_beat + event.duration_beats for event in events) == 48.0
    assert {event.voice for event in events} == {"bass", "melody"}
    assert any(
        first.start_beat == second.start_beat and first.pitch != second.pitch
        for first in events
        for second in events
        if first.voice == second.voice == "bass"
    )


def test_canon_in_d_preserves_ground_bass_and_delayed_upper_entry() -> None:
    spec = _score_spec("canon-progression")
    events = spec["events"]
    bass = sorted(
        (event for event in events if event.voice == "bass"),
        key=lambda event: event.start_beat,
    )
    first_cycle = tuple((event.pitch, event.start_beat) for event in bass[:8])

    assert spec["meter"] == (4, 4)
    assert spec["bpm"] == 92
    assert spec["difficulty"] == "intermediate"
    assert first_cycle == (
        (50, 0.0),
        (45, 2.0),
        (47, 4.0),
        (42, 6.0),
        (43, 8.0),
        (50, 10.0),
        (43, 12.0),
        (45, 14.0),
    )
    assert tuple(event.pitch for event in bass[8:16]) == tuple(
        event.pitch for event in bass[:8]
    )
    assert tuple(event.pitch for event in bass[16:24]) == tuple(
        event.pitch for event in bass[:8]
    )
    melody = [event for event in events if event.voice == "melody"]
    arpeggio = [event for event in events if event.voice == "arpeggio"]
    assert min(event.start_beat for event in melody) == 16.0
    assert any(event.start_beat == 16.0 for event in bass)
    assert any(event.start_beat == 0.5 for event in arpeggio)
    assert max(event.start_beat + event.duration_beats for event in events) == 48.0


def test_fur_elise_preserves_pickup_rests_and_broken_accompaniment() -> None:
    spec = _score_spec("fur-elise")
    events = spec["events"]
    melody = sorted(
        (event for event in events if event.voice == "melody"),
        key=lambda event: event.start_beat,
    )
    signatures = {_event_signature(event) for event in events}

    assert spec["meter"] == (3, 8)
    assert spec["bpm"] == 88
    assert spec["difficulty"] == "advanced"
    assert [
        (event.pitch, event.start_beat, event.duration_beats)
        for event in melody[:9]
    ] == [
        (76, 1.0, 0.25),
        (75, 1.25, 0.25),
        (76, 1.5, 0.25),
        (75, 1.75, 0.25),
        (76, 2.0, 0.25),
        (71, 2.25, 0.25),
        (74, 2.5, 0.25),
        (72, 2.75, 0.25),
        (69, 3.0, 0.5),
    ]
    assert melody[9].pitch == 60
    assert melody[9].start_beat == 3.75
    assert melody[9].start_beat - (
        melody[8].start_beat + melody[8].duration_beats
    ) == pytest.approx(0.25)
    assert {
        ("bass", 45, 3.0, 0.25),
        ("arpeggio", 52, 3.25, 0.25),
        ("arpeggio", 57, 3.5, 0.25),
        ("bass", 40, 4.5, 0.25),
        ("arpeggio", 52, 4.75, 0.25),
        ("arpeggio", 56, 5.0, 0.25),
    } <= signatures
    assert {event.voice for event in events} == {"arpeggio", "bass", "melody"}
    assert max(event.start_beat + event.duration_beats for event in events) == 25.0


def test_moonlight_preserves_triplets_upper_attacks_and_bass_support() -> None:
    spec = _score_spec("moonlight-theme")
    events = spec["events"]
    triplets = sorted(
        (event for event in events if event.voice == "triplet"),
        key=lambda event: event.start_beat,
    )
    signatures = {_event_signature(event) for event in events}

    assert spec["meter"] == (2, 2)
    assert spec["bpm"] == 72
    assert spec["difficulty"] == "advanced"
    assert [event.pitch for event in triplets[:12]] == [56, 61, 64] * 4
    assert [event.start_beat for event in triplets] == pytest.approx(
        [index / 3 for index in range(108)]
    )
    assert all(
        event.duration_beats == pytest.approx(1 / 3)
        for event in triplets
    )
    assert {
        ("upper", 68, 19.0, 0.75),
        ("upper", 68, 19.75, 0.25),
        ("upper", 68, 20.0, 3.0),
        ("bass", 37, 0.0, 4.0),
        ("bass", 49, 0.0, 4.0),
        ("bass", 37, 16.0, 4.0),
    } <= signatures
    sounding_at_19 = {
        event.voice
        for event in events
        if event.start_beat <= 19.0
        < event.start_beat + event.duration_beats
    }
    assert sounding_at_19 == {"bass", "triplet", "upper"}
    assert {event.voice for event in events} == {"bass", "triplet", "upper"}
    assert max(event.start_beat + event.duration_beats for event in events) == pytest.approx(36.0)


def _is_exact_repeat_of_shorter_phrase(notes) -> bool:
    phrase = tuple((note.pitch, note.duration) for note in notes)
    for phrase_length in range(1, len(phrase)):
        if len(phrase) % phrase_length == 0 and phrase == phrase[:phrase_length] * (len(phrase) // phrase_length):
            return True
    return False


def test_builtin_template_resources_are_complete_and_valid() -> None:
    resource_root = (
        Path(__file__).parents[2]
        / "src"
        / "ai_piano_studio"
        / "resources"
        / "templates"
    )
    for template_id in TEMPLATE_IDS:
        template_dir = resource_root / template_id
        metadata_path = template_dir / "metadata.json"
        project_path = template_dir / "template.pianoai.json"
        thumbnail_path = template_dir / "thumbnail.png"
        assert metadata_path.is_file(), template_id
        assert project_path.is_file(), template_id
        assert thumbnail_path.is_file(), template_id

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        state = load_project(project_path)
        expected_bpm, expected_meter, expected_difficulty = (
            TEMPLATE_RESOURCE_CONTRACTS[template_id]
        )
        notes = state.playback_notes
        assert metadata["id"] == template_id
        assert metadata["difficulty"] == expected_difficulty
        assert state.playback_tempo.bpm == expected_bpm
        assert (
            state.playback_tempo.beats_per_bar,
            state.playback_tempo.beat_unit,
        ) == expected_meter
        assert metadata["noteCount"] == len(notes)
        assert metadata["durationSeconds"] == state.playback_duration
        assert notes
        # Musical phrase boundaries replace the old arbitrary 30-34 second crop.
        expected_beats = {"fur-elise": 25, "ode-to-joy": 64, "moonlight-theme": 36}.get(template_id, 48)
        assert state.playback_duration == pytest.approx(expected_beats * 60 / expected_bpm, abs=1e-5)
        # The published Elise strain has a repeat sign; repetition is intentional.
        assert _is_exact_repeat_of_shorter_phrase(notes) == (template_id == "fur-elise")
        assert all(0 <= note.pitch <= 127 for note in notes)
        assert all(note.start_time >= 0 and note.duration > 0 for note in notes)
        assert [note.start_time for note in notes] == sorted(
            note.start_time for note in notes
        )
        assert len({note.id for note in notes}) == len(notes)
        assert all(note.id.startswith(f"{template_id}-") for note in notes)
        assert len({note.start_time for note in notes}) < len(notes)
        expected_conflicts = 1 if template_id == "moonlight-theme" else 0
        assert (
            EditorController(state).validateTimeline()["conflictCount"]
            == expected_conflicts
        )

        notes_by_id = {note.id: note for note in notes}
        for note_id, pitch, start_beat, duration_beats in (
            RESOURCE_BEAT_CHECKPOINTS[template_id]
        ):
            note = notes_by_id[note_id]
            assert note.pitch == pitch
            assert note.start_time * expected_bpm / 60 == pytest.approx(
                start_beat,
                abs=1e-5,
            )
            assert note.duration * expected_bpm / 60 == pytest.approx(
                duration_beats,
                abs=1e-5,
            )


def test_checked_in_template_resources_match_fresh_generation(tmp_path) -> None:
    from tools.generate_builtin_templates import generate_templates

    generated_root = generate_templates(tmp_path / "generated")
    checked_in_root = (
        Path(__file__).parents[2]
        / "src"
        / "ai_piano_studio"
        / "resources"
        / "templates"
    )
    for template_id in TEMPLATE_IDS:
        for filename in ("metadata.json", "template.pianoai.json", "thumbnail.png"):
            assert (
                checked_in_root / template_id / filename
            ).read_bytes() == (
                generated_root / template_id / filename
            ).read_bytes()


def test_builtin_template_generation_is_deterministic(tmp_path) -> None:
    from tools.generate_builtin_templates import generate_templates

    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_templates(first)
    generate_templates(second)
    first_files = sorted(path.relative_to(first) for path in first.rglob("*"))
    second_files = sorted(path.relative_to(second) for path in second.rglob("*"))
    assert first_files == second_files
    for relative in first_files:
        if (first / relative).is_file():
            assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_twinkle_star_phrase_endings_are_held_without_merging_repeated_attacks() -> None:
    project_path = (
        Path(__file__).parents[2]
        / "src"
        / "ai_piano_studio"
        / "resources"
        / "templates"
        / "twinkle-star"
        / "template.pianoai.json"
    )
    notes = tuple(
        note
        for note in load_project(project_path).playback_notes
        if note.id.startswith("twinkle-star-melody-")
    )

    assert notes[0].pitch == notes[1].pitch == 60
    assert notes[0].start_time == 0.0
    assert notes[1].start_time == 0.625
    assert notes[0].duration == notes[1].duration == 0.625

    phrase_ending_starts = (3.75, 8.75, 13.75, 18.75, 23.75, 28.75)
    held = {
        round(note.start_time, 2): note
        for note in notes
        if note.duration > 0.625
    }
    assert set(held) >= set(phrase_ending_starts)
    assert all(held[start].duration == 1.25 for start in phrase_ending_starts)


class FakePreviewPlayer(QObject):
    playingChanged = Signal(bool)
    positionChanged = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.played_notes = []
        self.stop_count = 0

    def play_notes(self, notes) -> None:
        self.played_notes.append(tuple(notes))
        self.playingChanged.emit(True)

    def stop(
        self, mode: ReleaseMode = ReleaseMode.STOP, *, after_stopped=None
    ) -> None:
        self.stop_count += 1
        self.playingChanged.emit(False)
        if after_stopped is not None:
            after_stopped()


def test_template_preview_isolated_and_adaptation_creates_new_project(tmp_path) -> None:
    player = FakePreviewPlayer()
    project_library = ProjectLibrary(tmp_path / "Projects")
    library = TemplateMusicLibrary(
        project_library=project_library,
        preview_player=player,
    )
    original = load_project(
        Path(__file__).parents[2]
        / "src"
        / "ai_piano_studio"
        / "resources"
        / "templates"
        / "ode-to-joy"
        / "template.pianoai.json"
    )

    library.preview("ode-to-joy")
    assert library.activeTemplateId == "ode-to-joy"
    assert library.previewing is True
    assert player.played_notes[-1] == original.playback_notes
    library.preview("twinkle-star")
    assert library.activeTemplateId == "twinkle-star"
    assert len(player.played_notes) == 2

    created: list[str] = []
    library.projectCreated.connect(created.append)
    library.adapt("ode-to-joy")
    assert len(created) == 1
    created_path = Path(created[0])
    assert created_path.is_file()
    assert created_path.parent.parent == project_library.root
    assert load_project(created_path).content == original.content
    assert library.activeTemplateId == ""
    assert library.previewing is False


def test_template_adaptation_uses_unique_name_when_template_was_already_adapted(
    tmp_path,
) -> None:
    project_library = ProjectLibrary(tmp_path / "Projects")
    library = TemplateMusicLibrary(project_library=project_library)
    created: list[str] = []
    errors: list[str] = []
    library.projectCreated.connect(created.append)
    library.errorRaised.connect(errors.append)

    library.adapt("canon-progression")
    library.adapt("canon-progression")

    assert errors == []
    assert len(created) == 2
    names = [project_library.display_name_for(Path(path)) for path in created]
    assert names[0] == "Canon Progression _ 卡农进行 改编"
    assert names[1] == "Canon Progression _ 卡农进行 改编 2"


def test_template_preview_player_is_created_only_on_first_preview(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    render_started = Event()

    def render_with_start_signal(notes, *, should_cancel=None):
        render_started.set()
        return render_default_project(notes, should_cancel=should_cancel)

    monkeypatch.setattr(player_module, "render_project", render_with_start_signal)
    library = TemplateMusicLibrary(project_library=ProjectLibrary(tmp_path / "Projects"))
    assert library._preview_player is None
    library.preview("moonlight-theme")
    assert library._preview_player is not None
    assert library._preview_player.piano_source is None
    qtbot.waitUntil(
        lambda: bool(library._preview_player._project_render_jobs), timeout=500
    )
    qtbot.waitUntil(
        lambda: QThreadPool.globalInstance().activeThreadCount() > 0, timeout=500
    )
    qtbot.waitUntil(render_started.is_set, timeout=500)
    library.stopPreview()
    assert QThreadPool.globalInstance().waitForDone(1000)
