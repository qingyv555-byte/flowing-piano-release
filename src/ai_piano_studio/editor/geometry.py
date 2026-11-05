from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Iterable

from ai_piano_studio.domain.note import Note


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def intersects(self, other: Rect) -> bool:
        return not (
            self.right < other.x
            or other.right < self.x
            or self.bottom < other.y
            or other.bottom < self.y
        )


def _positive(value: float, name: str) -> float:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def time_to_x(time: float, pixels_per_second: float, scroll_seconds: float = 0.0) -> float:
    _positive(pixels_per_second, "pixels_per_second")
    return (time - scroll_seconds) * pixels_per_second


def x_to_time(x: float, pixels_per_second: float, scroll_seconds: float = 0.0) -> float:
    _positive(pixels_per_second, "pixels_per_second")
    return max(0.0, x / pixels_per_second + scroll_seconds)


def pitch_to_y(pitch: int, row_height: float, highest_pitch: int = 84) -> float:
    _positive(row_height, "row_height")
    return (highest_pitch - max(0, min(127, pitch))) * row_height


def y_to_pitch(y: float, row_height: float, highest_pitch: int = 84) -> int:
    _positive(row_height, "row_height")
    pitch = highest_pitch - floor(max(0.0, y) / row_height)
    return max(0, min(127, pitch))


def snap_time(time: float, *, step: float, enabled: bool = True) -> float:
    _positive(step, "step")
    if not enabled:
        return max(0.0, time)
    return max(0.0, round(time / step) * step)


def note_rect(
    note: Note,
    *,
    pixels_per_second: float,
    row_height: float,
    highest_pitch: int = 84,
    scroll_seconds: float = 0.0,
) -> Rect:
    return Rect(
        x=time_to_x(note.start_time, pixels_per_second, scroll_seconds),
        y=pitch_to_y(note.pitch, row_height, highest_pitch),
        width=note.duration * _positive(pixels_per_second, "pixels_per_second"),
        height=_positive(row_height, "row_height"),
    )


def notes_intersecting_rect(
    notes: Iterable[Note],
    marquee: Rect,
    *,
    pixels_per_second: float,
    row_height: float,
    highest_pitch: int = 84,
    scroll_seconds: float = 0.0,
) -> frozenset[str]:
    return frozenset(
        note.id
        for note in notes
        if note_rect(
            note,
            pixels_per_second=pixels_per_second,
            row_height=row_height,
            highest_pitch=highest_pitch,
            scroll_seconds=scroll_seconds,
        ).intersects(marquee)
    )
