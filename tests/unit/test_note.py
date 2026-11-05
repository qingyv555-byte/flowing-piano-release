import pytest

from ai_piano_studio.domain.note import Note, midi_to_name
from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID


def test_midi_to_name_uses_scientific_pitch_notation() -> None:
    assert midi_to_name(60) == "C4"
    assert midi_to_name(61) == "C#4"
    assert midi_to_name(0) == "C-1"


def test_create_uses_canonical_fields_and_defaults() -> None:
    note = Note.create(pitch=60, start_time=1.2, duration=0.8)

    assert note.note_name == "C4"
    assert note.start_time == 1.2
    assert note.velocity == 75
    assert note.instrument == "piano"
    assert note.layer_id == DEFAULT_LAYER_ID
    assert note.id


def test_note_serializes_with_camel_case_and_round_trips() -> None:
    note = Note.create(
        id="note_001",
        pitch=60,
        start_time=1.5,
        duration=0.8,
        velocity=80,
        instrument="piano",
    )

    payload = note.to_dict()

    assert payload == {
        "id": "note_001",
        "pitch": 60,
        "noteName": "C4",
        "startTime": 1.5,
        "duration": 0.8,
        "velocity": 80,
        "instrument": "piano",
        "status": "saved",
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
        "releaseMode": "natural",
        "releaseMs": None,
        "layerId": DEFAULT_LAYER_ID,
    }
    assert Note.from_dict(payload) == note


def test_note_round_trips_optional_midi_routing_metadata() -> None:
    note = Note.create(
        id="midi-note",
        pitch=60,
        start_time=0.0,
        duration=0.5,
        track=2,
        channel=9,
    )

    payload = note.to_dict()

    assert payload["track"] == 2
    assert payload["channel"] == 9
    assert Note.from_dict(payload) == note


def test_note_round_trips_music_layer_membership() -> None:
    note = Note.create(
        id="layered-note", pitch=60, start_time=0.0, duration=0.5,
        layer_id="layer-melody",
    )

    assert note.to_dict()["layerId"] == "layer-melody"
    assert Note.from_dict(note.to_dict()) == note


def test_note_rejects_blank_music_layer_membership() -> None:
    with pytest.raises(ValueError, match="layer_id"):
        Note.create(pitch=60, start_time=0.0, duration=0.5, layer_id=" ")


def test_from_dict_derives_note_name_from_pitch() -> None:
    note = Note.from_dict(
        {
            "id": "note_001",
            "pitch": 64,
            "noteName": "wrong",
            "startTime": 0.0,
            "duration": 0.5,
            "velocity": 75,
            "instrument": "piano",
        }
    )

    assert note.note_name == "E4"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pitch", -1),
        ("pitch", 128),
        ("pitch", True),
        ("start_time", -0.1),
        ("start_time", float("nan")),
        ("start_time", float("inf")),
        ("duration", 0),
        ("duration", float("nan")),
        ("duration", float("inf")),
        ("velocity", -1),
        ("velocity", 128),
        ("instrument", " "),
        ("track", -1),
        ("track", True),
        ("channel", -1),
        ("channel", 16),
    ],
)
def test_create_rejects_invalid_values(field: str, value: object) -> None:
    values = {
        "pitch": 60,
        "start_time": 0.0,
        "duration": 0.5,
        "velocity": 75,
        "instrument": "piano",
    }
    values[field] = value

    with pytest.raises(ValueError):
        Note.create(**values)


def test_with_changes_returns_new_validated_note() -> None:
    original = Note.create(pitch=60, start_time=0.0, duration=0.5)

    changed = original.with_changes(pitch=64, velocity=90)

    assert changed.id == original.id
    assert changed.note_name == "E4"
    assert changed.velocity == 90
    assert original.note_name == "C4"


def test_with_changes_rejects_invalid_value() -> None:
    note = Note.create(pitch=60, start_time=0.0, duration=0.5)

    with pytest.raises(ValueError):
        note.with_changes(duration=0)
