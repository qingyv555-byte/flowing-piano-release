import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.editor.geometry import (
    Rect,
    note_rect,
    notes_intersecting_rect,
    pitch_to_y,
    snap_time,
    time_to_x,
    x_to_time,
    y_to_pitch,
)


def test_time_coordinate_round_trip() -> None:
    x = time_to_x(1.2, pixels_per_second=80, scroll_seconds=0.2)
    assert x == pytest.approx(80)
    assert x_to_time(x, pixels_per_second=80, scroll_seconds=0.2) == pytest.approx(1.2)


def test_pitch_coordinate_round_trip() -> None:
    y = pitch_to_y(60, row_height=18, highest_pitch=72)
    assert y == 216
    assert y_to_pitch(y, row_height=18, highest_pitch=72) == 60


def test_coordinate_conversion_clamps_domain() -> None:
    assert x_to_time(-100, pixels_per_second=80, scroll_seconds=0) == 0
    assert y_to_pitch(-100, row_height=18, highest_pitch=127) == 127
    assert y_to_pitch(9999, row_height=18, highest_pitch=127) == 0


def test_snap_time_can_be_bypassed() -> None:
    assert snap_time(1.23, step=0.25, enabled=True) == 1.25
    assert snap_time(1.23, step=0.25, enabled=False) == 1.23


def test_note_rect_maps_canonical_time_pitch_and_duration() -> None:
    note = Note.create(pitch=60, start_time=1.0, duration=0.5)
    rect = note_rect(note, pixels_per_second=100, row_height=18, highest_pitch=72)
    assert rect == Rect(x=100, y=216, width=50, height=18)


def test_marquee_includes_notes_touching_edges() -> None:
    first = Note.create(id="a", pitch=60, start_time=1.0, duration=0.5)
    second = Note.create(id="b", pitch=64, start_time=3.0, duration=0.5)
    selected = notes_intersecting_rect(
        (first, second), Rect(x=50, y=200, width=50, height=40),
        pixels_per_second=100, row_height=18, highest_pitch=72,
    )
    assert selected == frozenset({"a"})
