from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
import zlib

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.persistence.project_json import project_to_json


_CREATED_AT = "2026-01-01T00:00:00+00:00"


@dataclass(frozen=True, slots=True)
class ScoreEvent:
    pitch: int
    start_beat: float
    duration_beats: float
    velocity: int = 78
    voice: str = "melody"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.pitch, int)
            or isinstance(self.pitch, bool)
            or not 0 <= self.pitch <= 127
        ):
            raise ValueError("pitch must be an integer from 0 to 127")
        if (
            not isinstance(self.start_beat, (int, float))
            or not math.isfinite(self.start_beat)
            or self.start_beat < 0
        ):
            raise ValueError("start_beat must be a finite nonnegative number")
        if (
            not isinstance(self.duration_beats, (int, float))
            or not math.isfinite(self.duration_beats)
            or self.duration_beats <= 0
        ):
            raise ValueError("duration_beats must be a finite positive number")
        if (
            not isinstance(self.velocity, int)
            or isinstance(self.velocity, bool)
            or not 1 <= self.velocity <= 127
        ):
            raise ValueError("velocity must be an integer from 1 to 127")
        if not isinstance(self.voice, str) or not self.voice.strip():
            raise ValueError("voice must be a nonempty string")


def _events_to_notes(
    template_id: str,
    events: tuple[ScoreEvent, ...],
    tempo: TempoMap,
) -> tuple[Note, ...]:
    keys: set[tuple[str, float, int]] = set()
    voice_counts: dict[str, int] = {}
    notes: list[Note] = []
    ordered = sorted(
        events,
        key=lambda event: (
            event.start_beat,
            event.voice,
            event.pitch,
            event.duration_beats,
            event.velocity,
        ),
    )
    for event in ordered:
        key = (event.voice, event.start_beat, event.pitch)
        if key in keys:
            raise ValueError(f"duplicate score event: {key}")
        keys.add(key)
        voice_index = voice_counts.get(event.voice, 0)
        voice_counts[event.voice] = voice_index + 1
        notes.append(
            Note.create(
                id=f"{template_id}-{event.voice}-{voice_index:03d}",
                pitch=event.pitch,
                start_time=round(tempo.beats_to_seconds(event.start_beat), 6),
                duration=round(tempo.beats_to_seconds(event.duration_beats), 6),
                velocity=event.velocity,
                instrument="piano",
                created_at=_CREATED_AT,
                updated_at=_CREATED_AT,
            )
        )
    return tuple(notes)


def _section(
    pitches: tuple[int, ...], duration: float, *, hold_last: bool = False
) -> tuple[tuple[int, float], ...]:
    if hold_last and len(pitches) >= 2 and pitches[-1] == pitches[-2]:
        return tuple((pitch, duration) for pitch in pitches[:-2]) + (
            (pitches[-1], duration * 2),
        )
    return tuple((pitch, duration) for pitch in pitches)


def _ode_to_joy_events() -> tuple[ScoreEvent, ...]:
    return (
        ScoreEvent(64, 0.0, 1.0, 84, "melody"),
        ScoreEvent(64, 1.0, 1.0, 80, "melody"),
        ScoreEvent(65, 2.0, 1.0, 82, "melody"),
        ScoreEvent(67, 3.0, 1.0, 86, "melody"),
        ScoreEvent(67, 4.0, 1.0, 84, "melody"),
        ScoreEvent(65, 5.0, 1.0, 80, "melody"),
        ScoreEvent(64, 6.0, 1.0, 80, "melody"),
        ScoreEvent(62, 7.0, 1.0, 78, "melody"),
        ScoreEvent(60, 8.0, 1.0, 82, "melody"),
        ScoreEvent(60, 9.0, 1.0, 78, "melody"),
        ScoreEvent(62, 10.0, 1.0, 80, "melody"),
        ScoreEvent(64, 11.0, 1.0, 84, "melody"),
        ScoreEvent(64, 12.0, 1.5, 84, "melody"),
        ScoreEvent(62, 13.5, 0.5, 76, "melody"),
        ScoreEvent(62, 14.0, 2.0, 80, "melody"),
        ScoreEvent(64, 16.0, 1.0, 84, "melody"),
        ScoreEvent(64, 17.0, 1.0, 80, "melody"),
        ScoreEvent(65, 18.0, 1.0, 82, "melody"),
        ScoreEvent(67, 19.0, 1.0, 86, "melody"),
        ScoreEvent(67, 20.0, 1.0, 84, "melody"),
        ScoreEvent(65, 21.0, 1.0, 80, "melody"),
        ScoreEvent(64, 22.0, 1.0, 80, "melody"),
        ScoreEvent(62, 23.0, 1.0, 78, "melody"),
        ScoreEvent(60, 24.0, 1.0, 82, "melody"),
        ScoreEvent(60, 25.0, 1.0, 78, "melody"),
        ScoreEvent(62, 26.0, 1.0, 80, "melody"),
        ScoreEvent(64, 27.0, 1.0, 84, "melody"),
        ScoreEvent(62, 28.0, 1.5, 82, "melody"),
        ScoreEvent(60, 29.5, 0.5, 76, "melody"),
        ScoreEvent(60, 30.0, 2.0, 82, "melody"),
        ScoreEvent(62, 32.0, 1.0, 82, "melody"),
        ScoreEvent(62, 33.0, 1.0, 78, "melody"),
        ScoreEvent(64, 34.0, 1.0, 82, "melody"),
        ScoreEvent(60, 35.0, 1.0, 78, "melody"),
        ScoreEvent(62, 36.0, 1.0, 82, "melody"),
        ScoreEvent(64, 37.0, 0.5, 80, "melody"),
        ScoreEvent(65, 37.5, 0.5, 84, "melody"),
        ScoreEvent(64, 38.0, 1.0, 82, "melody"),
        ScoreEvent(60, 39.0, 1.0, 76, "melody"),
        ScoreEvent(62, 40.0, 1.0, 82, "melody"),
        ScoreEvent(64, 41.0, 0.5, 80, "melody"),
        ScoreEvent(65, 41.5, 0.5, 84, "melody"),
        ScoreEvent(64, 42.0, 1.0, 82, "melody"),
        ScoreEvent(62, 43.0, 1.0, 78, "melody"),
        ScoreEvent(60, 44.0, 1.0, 80, "melody"),
        ScoreEvent(62, 45.0, 1.0, 82, "melody"),
        ScoreEvent(55, 46.0, 2.0, 76, "melody"),
        ScoreEvent(64, 48.0, 1.0, 84, "melody"),
        ScoreEvent(64, 49.0, 1.0, 80, "melody"),
        ScoreEvent(65, 50.0, 1.0, 82, "melody"),
        ScoreEvent(67, 51.0, 1.0, 86, "melody"),
        ScoreEvent(67, 52.0, 1.0, 84, "melody"),
        ScoreEvent(65, 53.0, 1.0, 80, "melody"),
        ScoreEvent(64, 54.0, 1.0, 80, "melody"),
        ScoreEvent(62, 55.0, 1.0, 78, "melody"),
        ScoreEvent(60, 56.0, 1.0, 82, "melody"),
        ScoreEvent(60, 57.0, 1.0, 78, "melody"),
        ScoreEvent(62, 58.0, 1.0, 80, "melody"),
        ScoreEvent(64, 59.0, 1.0, 84, "melody"),
        ScoreEvent(62, 60.0, 1.5, 82, "melody"),
        ScoreEvent(60, 61.5, 0.5, 76, "melody"),
        ScoreEvent(60, 62.0, 2.0, 84, "melody"),
        ScoreEvent(48, 0.0, 4.0, 62, "bass"),
        ScoreEvent(55, 0.0, 4.0, 58, "harmony"),
        ScoreEvent(48, 8.0, 4.0, 60, "bass"),
        ScoreEvent(55, 8.0, 4.0, 56, "harmony"),
        ScoreEvent(48, 16.0, 4.0, 62, "bass"),
        ScoreEvent(55, 16.0, 4.0, 58, "harmony"),
        ScoreEvent(43, 24.0, 4.0, 60, "bass"),
        ScoreEvent(50, 24.0, 4.0, 56, "harmony"),
        ScoreEvent(43, 32.0, 4.0, 62, "bass"),
        ScoreEvent(50, 32.0, 4.0, 58, "harmony"),
        ScoreEvent(43, 40.0, 4.0, 60, "bass"),
        ScoreEvent(50, 40.0, 4.0, 56, "harmony"),
        ScoreEvent(48, 48.0, 4.0, 62, "bass"),
        ScoreEvent(55, 48.0, 4.0, 58, "harmony"),
        ScoreEvent(48, 56.0, 2.0, 60, "bass"),
        ScoreEvent(55, 56.0, 2.0, 56, "harmony"),
        ScoreEvent(43, 60.0, 2.0, 62, "bass"),
        ScoreEvent(50, 60.0, 2.0, 58, "harmony"),
        ScoreEvent(48, 62.0, 2.0, 66, "bass"),
        ScoreEvent(55, 62.0, 2.0, 62, "harmony"),
    )


def _twinkle_star_events() -> tuple[ScoreEvent, ...]:
    return (
        ScoreEvent(60, 0.0, 1.0, 82, "melody"),
        ScoreEvent(60, 1.0, 1.0, 78, "melody"),
        ScoreEvent(67, 2.0, 1.0, 84, "melody"),
        ScoreEvent(67, 3.0, 1.0, 80, "melody"),
        ScoreEvent(69, 4.0, 1.0, 84, "melody"),
        ScoreEvent(69, 5.0, 1.0, 80, "melody"),
        ScoreEvent(67, 6.0, 2.0, 84, "melody"),
        ScoreEvent(65, 8.0, 1.0, 82, "melody"),
        ScoreEvent(65, 9.0, 1.0, 78, "melody"),
        ScoreEvent(64, 10.0, 1.0, 82, "melody"),
        ScoreEvent(64, 11.0, 1.0, 78, "melody"),
        ScoreEvent(62, 12.0, 1.0, 80, "melody"),
        ScoreEvent(62, 13.0, 1.0, 76, "melody"),
        ScoreEvent(60, 14.0, 2.0, 84, "melody"),
        ScoreEvent(67, 16.0, 1.0, 82, "melody"),
        ScoreEvent(67, 17.0, 1.0, 78, "melody"),
        ScoreEvent(65, 18.0, 1.0, 82, "melody"),
        ScoreEvent(65, 19.0, 1.0, 78, "melody"),
        ScoreEvent(64, 20.0, 1.0, 80, "melody"),
        ScoreEvent(64, 21.0, 1.0, 76, "melody"),
        ScoreEvent(62, 22.0, 2.0, 82, "melody"),
        ScoreEvent(67, 24.0, 1.0, 82, "melody"),
        ScoreEvent(67, 25.0, 1.0, 78, "melody"),
        ScoreEvent(65, 26.0, 1.0, 82, "melody"),
        ScoreEvent(65, 27.0, 1.0, 78, "melody"),
        ScoreEvent(64, 28.0, 1.0, 80, "melody"),
        ScoreEvent(64, 29.0, 1.0, 76, "melody"),
        ScoreEvent(62, 30.0, 2.0, 82, "melody"),
        ScoreEvent(60, 32.0, 1.0, 82, "melody"),
        ScoreEvent(60, 33.0, 1.0, 78, "melody"),
        ScoreEvent(67, 34.0, 1.0, 84, "melody"),
        ScoreEvent(67, 35.0, 1.0, 80, "melody"),
        ScoreEvent(69, 36.0, 1.0, 84, "melody"),
        ScoreEvent(69, 37.0, 1.0, 80, "melody"),
        ScoreEvent(67, 38.0, 2.0, 84, "melody"),
        ScoreEvent(65, 40.0, 1.0, 82, "melody"),
        ScoreEvent(65, 41.0, 1.0, 78, "melody"),
        ScoreEvent(64, 42.0, 1.0, 82, "melody"),
        ScoreEvent(64, 43.0, 1.0, 78, "melody"),
        ScoreEvent(62, 44.0, 1.0, 80, "melody"),
        ScoreEvent(62, 45.0, 1.0, 76, "melody"),
        ScoreEvent(60, 46.0, 2.0, 86, "melody"),
        ScoreEvent(48, 0.0, 2.0, 60, "bass"),
        ScoreEvent(55, 0.0, 2.0, 56, "harmony"),
        ScoreEvent(41, 8.0, 2.0, 58, "bass"),
        ScoreEvent(48, 8.0, 2.0, 54, "harmony"),
        ScoreEvent(43, 16.0, 2.0, 60, "bass"),
        ScoreEvent(50, 16.0, 2.0, 56, "harmony"),
        ScoreEvent(43, 24.0, 2.0, 58, "bass"),
        ScoreEvent(50, 24.0, 2.0, 54, "harmony"),
        ScoreEvent(48, 32.0, 2.0, 60, "bass"),
        ScoreEvent(55, 32.0, 2.0, 56, "harmony"),
        ScoreEvent(41, 40.0, 2.0, 58, "bass"),
        ScoreEvent(48, 40.0, 2.0, 54, "harmony"),
        ScoreEvent(43, 44.0, 2.0, 60, "bass"),
        ScoreEvent(50, 44.0, 2.0, 56, "harmony"),
        ScoreEvent(48, 46.0, 2.0, 64, "bass"),
        ScoreEvent(55, 46.0, 2.0, 60, "harmony"),
    )


def _voice_events(
    voice: str,
    velocity: int,
    entries: tuple[tuple[int, float, float], ...],
) -> tuple[ScoreEvent, ...]:
    return tuple(
        ScoreEvent(pitch, start_beat, duration_beats, velocity, voice)
        for pitch, start_beat, duration_beats in entries
    )


def _minuet_in_g_events() -> tuple[ScoreEvent, ...]:
    return (
        *_voice_events(
            "melody",
            84,
            (
                (74, 0.0, 1.0), (67, 1.0, 0.5), (69, 1.5, 0.5),
                (71, 2.0, 0.5), (72, 2.5, 0.5),
                (74, 3.0, 1.0), (67, 4.0, 1.0), (67, 5.0, 1.0),
                (76, 6.0, 1.0), (72, 7.0, 0.5), (74, 7.5, 0.5),
                (76, 8.0, 0.5), (78, 8.5, 0.5),
                (79, 9.0, 1.0), (67, 10.0, 1.0), (67, 11.0, 1.0),
                (72, 12.0, 1.0), (74, 13.0, 0.5), (72, 13.5, 0.5),
                (71, 14.0, 0.5), (69, 14.5, 0.5),
                (71, 15.0, 1.0), (72, 16.0, 0.5), (71, 16.5, 0.5),
                (69, 17.0, 0.5), (67, 17.5, 0.5),
                (66, 18.0, 1.0), (67, 19.0, 0.5), (69, 19.5, 0.5),
                (71, 20.0, 0.5), (67, 20.5, 0.5),
                (69, 21.0, 3.0),
                (74, 24.0, 1.0), (67, 25.0, 0.5), (69, 25.5, 0.5),
                (71, 26.0, 0.5), (72, 26.5, 0.5),
                (74, 27.0, 1.0), (67, 28.0, 1.0), (67, 29.0, 1.0),
                (76, 30.0, 1.0), (72, 31.0, 0.5), (74, 31.5, 0.5),
                (76, 32.0, 0.5), (78, 32.5, 0.5),
                (79, 33.0, 1.0), (67, 34.0, 1.0), (67, 35.0, 1.0),
                (72, 36.0, 1.0), (74, 37.0, 0.5), (72, 37.5, 0.5),
                (71, 38.0, 0.5), (69, 38.5, 0.5),
                (71, 39.0, 1.0), (72, 40.0, 0.5), (71, 40.5, 0.5),
                (69, 41.0, 0.5), (67, 41.5, 0.5),
                (69, 42.0, 1.0), (71, 43.0, 0.5), (69, 43.5, 0.5),
                (67, 44.0, 0.5), (66, 44.5, 0.5),
                (67, 45.0, 3.0),
            ),
        ),
        *_voice_events(
            "bass",
            62,
            (
                (55, 0.0, 2.0), (59, 0.0, 2.0), (62, 0.0, 2.0),
                (57, 2.0, 1.0), (59, 3.0, 3.0), (60, 6.0, 3.0),
                (59, 9.0, 3.0), (57, 12.0, 3.0), (55, 15.0, 3.0),
                (62, 18.0, 1.0), (59, 19.0, 1.0), (55, 20.0, 1.0),
                (62, 21.0, 1.0), (50, 22.0, 0.5), (60, 22.5, 0.5),
                (59, 23.0, 0.5), (57, 23.5, 0.5),
                (59, 24.0, 2.0), (57, 26.0, 1.0),
                (55, 27.0, 1.0), (59, 28.0, 1.0), (55, 29.0, 1.0),
                (60, 30.0, 3.0),
                (59, 33.0, 1.0), (60, 34.0, 0.5), (59, 34.5, 0.5),
                (57, 35.0, 0.5), (55, 35.5, 0.5),
                (57, 36.0, 2.0), (54, 38.0, 1.0),
                (55, 39.0, 2.0), (59, 41.0, 1.0),
                (60, 42.0, 1.0), (62, 43.0, 1.0), (50, 44.0, 1.0),
                (55, 45.0, 2.0), (43, 47.0, 1.0),
            ),
        ),
    )


def _canon_in_d_events() -> tuple[ScoreEvent, ...]:
    return (
        *_voice_events(
            "bass",
            64,
            (
                (50, 0.0, 2.0), (45, 2.0, 2.0), (47, 4.0, 2.0),
                (42, 6.0, 2.0), (43, 8.0, 2.0), (50, 10.0, 2.0),
                (43, 12.0, 2.0), (45, 14.0, 2.0),
                (50, 16.0, 2.0), (45, 18.0, 2.0), (47, 20.0, 2.0),
                (42, 22.0, 2.0), (43, 24.0, 2.0), (50, 26.0, 2.0),
                (43, 28.0, 2.0), (45, 30.0, 2.0),
                (50, 32.0, 2.0), (45, 34.0, 2.0), (47, 36.0, 2.0),
                (42, 38.0, 2.0), (43, 40.0, 2.0), (50, 42.0, 2.0),
                (43, 44.0, 2.0), (45, 46.0, 2.0),
            ),
        ),
        *_voice_events(
            "arpeggio",
            62,
            (
                (54, 0.5, 0.5), (57, 1.0, 0.5), (62, 1.5, 0.5),
                (49, 2.5, 0.5), (52, 3.0, 0.5), (57, 3.5, 0.5),
                (50, 4.5, 0.5), (54, 5.0, 0.5), (59, 5.5, 0.5),
                (45, 6.5, 0.5), (49, 7.0, 0.5), (54, 7.5, 0.5),
                (47, 8.5, 0.5), (50, 9.0, 0.5), (55, 9.5, 0.5),
                (54, 10.5, 0.5), (57, 11.0, 0.5), (62, 11.5, 0.5),
                (47, 12.5, 0.5), (50, 13.0, 0.5), (55, 13.5, 0.5),
                (49, 14.5, 0.5), (52, 15.0, 0.5), (57, 15.5, 0.5),
                (54, 16.5, 0.5), (57, 17.0, 0.5), (62, 17.5, 0.5),
                (49, 18.5, 0.5), (52, 19.0, 0.5), (57, 19.5, 0.5),
                (50, 20.5, 0.5), (54, 21.0, 0.5), (59, 21.5, 0.5),
                (45, 22.5, 0.5), (49, 23.0, 0.5), (54, 23.5, 0.5),
                (47, 24.5, 0.5), (50, 25.0, 0.5), (55, 25.5, 0.5),
                (54, 26.5, 0.5), (57, 27.0, 0.5), (62, 27.5, 0.5),
                (47, 28.5, 0.5), (50, 29.0, 0.5), (55, 29.5, 0.5),
                (49, 30.5, 0.5), (52, 31.0, 0.5), (57, 31.5, 0.5),
                (54, 32.5, 0.5), (57, 33.0, 0.5), (62, 33.5, 0.5),
                (49, 34.5, 0.5), (52, 35.0, 0.5), (57, 35.5, 0.5),
                (50, 36.5, 0.5), (54, 37.0, 0.5), (59, 37.5, 0.5),
                (45, 38.5, 0.5), (49, 39.0, 0.5), (54, 39.5, 0.5),
                (47, 40.5, 0.5), (50, 41.0, 0.5), (55, 41.5, 0.5),
                (54, 42.5, 0.5), (57, 43.0, 0.5), (62, 43.5, 0.5),
                (47, 44.5, 0.5), (50, 45.0, 0.5), (55, 45.5, 0.5),
                (49, 46.5, 0.5), (52, 47.0, 0.5), (57, 47.5, 0.5),
            ),
        ),
        *_voice_events(
            "melody",
            82,
            (
                (78, 16.0, 2.0), (76, 18.0, 2.0),
                (74, 20.0, 2.0), (73, 22.0, 2.0),
                (71, 24.0, 2.0), (69, 26.0, 2.0),
                (71, 28.0, 2.0), (73, 30.0, 2.0),
                (78, 32.0, 2.0), (76, 34.0, 2.0),
                (74, 36.0, 2.0), (73, 38.0, 2.0),
                (71, 40.0, 2.0), (69, 42.0, 2.0),
                (71, 44.0, 2.0), (73, 46.0, 2.0),
            ),
        ),
        *_voice_events(
            "harmony",
            70,
            (
                (74, 32.0, 2.0), (73, 34.0, 2.0),
                (71, 36.0, 2.0), (69, 38.0, 2.0),
                (67, 40.0, 2.0), (66, 42.0, 2.0),
                (67, 44.0, 2.0), (69, 46.0, 2.0),
            ),
        ),
    )


def _fur_elise_events() -> tuple[ScoreEvent, ...]:
    # Opening strain, repeated; the quarter-rest aligns its eighth-note pickup.
    # Mutopia 931 (Breitkopf & Haertel, 1888): E4-C5-B4 at the cadence.
    phrase = (
        ScoreEvent(76, 0.0, 0.25, 84, "melody"),
        ScoreEvent(75, 0.25, 0.25, 84, "melody"),
        ScoreEvent(76, 0.5, 0.25, 84, "melody"),
        ScoreEvent(75, 0.75, 0.25, 84, "melody"),
        ScoreEvent(76, 1.0, 0.25, 84, "melody"),
        ScoreEvent(71, 1.25, 0.25, 84, "melody"),
        ScoreEvent(74, 1.5, 0.25, 84, "melody"),
        ScoreEvent(72, 1.75, 0.25, 84, "melody"),
        ScoreEvent(69, 2.0, 0.5, 84, "melody"),
        ScoreEvent(60, 2.75, 0.25, 84, "melody"),
        ScoreEvent(64, 3.0, 0.25, 84, "melody"),
        ScoreEvent(69, 3.25, 0.25, 84, "melody"),
        ScoreEvent(71, 3.5, 0.5, 84, "melody"),
        ScoreEvent(64, 4.25, 0.25, 84, "melody"),
        ScoreEvent(68, 4.5, 0.25, 84, "melody"),
        ScoreEvent(71, 4.75, 0.25, 84, "melody"),
        ScoreEvent(72, 5.0, 0.5, 84, "melody"),
        ScoreEvent(64, 5.75, 0.25, 84, "melody"),
        ScoreEvent(76, 6.0, 0.25, 84, "melody"),
        ScoreEvent(75, 6.25, 0.25, 84, "melody"),
        ScoreEvent(76, 6.5, 0.25, 84, "melody"),
        ScoreEvent(75, 6.75, 0.25, 84, "melody"),
        ScoreEvent(76, 7.0, 0.25, 84, "melody"),
        ScoreEvent(71, 7.25, 0.25, 84, "melody"),
        ScoreEvent(74, 7.5, 0.25, 84, "melody"),
        ScoreEvent(72, 7.75, 0.25, 84, "melody"),
        ScoreEvent(69, 8.0, 0.5, 84, "melody"),
        ScoreEvent(60, 8.75, 0.25, 84, "melody"),
        ScoreEvent(64, 9.0, 0.25, 84, "melody"),
        ScoreEvent(69, 9.25, 0.25, 84, "melody"),
        ScoreEvent(71, 9.5, 0.5, 84, "melody"),
        ScoreEvent(64, 10.25, 0.25, 84, "melody"),
        ScoreEvent(72, 10.5, 0.25, 84, "melody"),
        ScoreEvent(71, 10.75, 0.25, 84, "melody"),
        ScoreEvent(69, 11.0, 1.0, 84, "melody"),
        ScoreEvent(45, 2.0, 0.25, 62, "bass"),
        ScoreEvent(40, 3.5, 0.25, 62, "bass"),
        ScoreEvent(45, 5.0, 0.25, 62, "bass"),
        ScoreEvent(45, 8.0, 0.25, 62, "bass"),
        ScoreEvent(40, 9.5, 0.25, 62, "bass"),
        ScoreEvent(45, 11.0, 0.25, 62, "bass"),
        ScoreEvent(52, 2.25, 0.25, 66, "arpeggio"),
        ScoreEvent(57, 2.5, 0.25, 66, "arpeggio"),
        ScoreEvent(52, 3.75, 0.25, 66, "arpeggio"),
        ScoreEvent(56, 4.0, 0.25, 66, "arpeggio"),
        ScoreEvent(52, 5.25, 0.25, 66, "arpeggio"),
        ScoreEvent(57, 5.5, 0.25, 66, "arpeggio"),
        ScoreEvent(52, 8.25, 0.25, 66, "arpeggio"),
        ScoreEvent(57, 8.5, 0.25, 66, "arpeggio"),
        ScoreEvent(52, 9.75, 0.25, 66, "arpeggio"),
        ScoreEvent(56, 10.0, 0.25, 66, "arpeggio"),
        ScoreEvent(52, 11.25, 0.25, 66, "arpeggio"),
        ScoreEvent(57, 11.5, 0.25, 66, "arpeggio"),
    )
    return tuple(
        ScoreEvent(event.pitch, event.start_beat + 1 + repeat * 12,
                   event.duration_beats, event.velocity, event.voice)
        for repeat in range(2)
        for event in phrase
    )


def _moonlight_events() -> tuple[ScoreEvent, ...]:
    triplet_pitches = (
        *((56, 61, 64) * 8),
        *((57, 61, 64) * 2), *((57, 62, 66) * 2),
        56, 60, 66, 56, 61, 64, 56, 61, 63, 54, 60, 63,
        52, 56, 61, *((56, 61, 64) * 3),
        *((56, 63, 66) * 4),
        *((56, 61, 64) * 2), *((57, 61, 66) * 2),
        *((56, 59, 64) * 2), *((57, 59, 63) * 2),
        *((56, 59, 64) * 4),
    )
    triplets = tuple(
        ScoreEvent(pitch, index / 3, 1 / 3, 58, "triplet")
        for index, pitch in enumerate(triplet_pitches)
    )
    return (
        *triplets,
        *_voice_events(
            "upper",
            82,
            (
                (68, 19.0, 0.75), (68, 19.75, 0.25),
                (68, 20.0, 3.0), (68, 23.0, 0.75), (68, 23.75, 0.25),
                (68, 24.0, 2.0), (69, 26.0, 2.0),
                (68, 28.0, 2.0), (66, 30.0, 1.0), (71, 31.0, 1.0),
                (64, 32.0, 1.0),
            ),
        ),
        *_voice_events(
            "bass",
            54,
            (
                (37, 0.0, 4.0), (49, 0.0, 4.0),
                (35, 4.0, 4.0), (47, 4.0, 4.0),
                (33, 8.0, 2.0), (45, 8.0, 2.0),
                (30, 10.0, 2.0), (42, 10.0, 2.0),
                (32, 12.0, 2.0), (44, 12.0, 2.0),
                (32, 14.0, 2.0), (44, 14.0, 2.0),
                (37, 16.0, 4.0), (44, 16.0, 4.0), (49, 16.0, 4.0),
                (36, 20.0, 4.0), (44, 20.0, 4.0), (48, 20.0, 4.0),
                (37, 24.0, 2.0), (49, 24.0, 2.0),
                (30, 26.0, 2.0), (42, 26.0, 2.0),
                (35, 28.0, 2.0), (47, 28.0, 2.0),
                (35, 30.0, 2.0), (47, 30.0, 2.0),
                (40, 32.0, 4.0), (52, 32.0, 4.0),
            ),
        ),
    )


TEMPLATE_SPECS = (
    {
        "id": "ode-to-joy",
        "name": "Ode to Joy / 欢乐颂",
        "composer": "Beethoven",
        "description": "明亮、温暖、适合第一次体验",
        "difficulty": "beginner",
        "tags": ["明亮", "温暖", "入门"],
        "bpm": 116,
        "meter": (4, 4),
        "events": _ode_to_joy_events(),
    },
    {
        "id": "twinkle-star",
        "name": "Twinkle Star / 小星星",
        "composer": "Traditional",
        "description": "梦幻、轻松、容易上手",
        "difficulty": "beginner",
        "tags": ["梦幻", "轻松", "入门"],
        "bpm": 96,
        "meter": (4, 4),
        "events": _twinkle_star_events(),
    },
    {
        "id": "fur-elise",
        "name": "Für Elise / 致爱丽丝",
        "composer": "Beethoven",
        "description": "主题节选，保留弱起与反复，回到主音结束",
        "difficulty": "advanced",
        "tags": ["优雅", "神秘", "旋律"],
        "bpm": 88,
        "meter": (3, 8),
        "events": _fur_elise_events(),
    },
    {
        "id": "canon-progression",
        "name": "Canon Progression / 卡农进行",
        "composer": "Pachelbel",
        "description": "基于卡农低音进行的钢琴简编，并非完整原曲",
        "difficulty": "intermediate",
        "tags": ["温暖", "治愈", "和声"],
        "bpm": 92,
        "meter": (4, 4),
        "events": _canon_in_d_events(),
    },
    {
        "id": "minuet-in-g",
        "name": "Minuet in G / G大调小步舞曲",
        "composer": "Petzold",
        "description": "前16小节主题，省略装饰音与反复",
        "difficulty": "intermediate",
        "tags": ["古典", "轻快", "节奏"],
        "bpm": 96,
        "meter": (3, 4),
        "events": _minuet_in_g_events(),
    },
    {
        "id": "moonlight-theme",
        "name": "Moonlight Theme / 月光主题",
        "composer": "Beethoven",
        "description": "第一乐章前9小节，保留三连音与旋律声部",
        "difficulty": "advanced",
        "tags": ["宁静", "夜色", "氛围"],
        "bpm": 72,
        "meter": (2, 2),
        "events": _moonlight_events(),
    },
)

def _notes(spec: dict[str, object], tempo: TempoMap | None = None) -> tuple[Note, ...]:
    if "events" in spec:
        if tempo is None:
            beats_per_bar, beat_unit = spec.get("meter", (4, 4))
            tempo = TempoMap(
                bpm=spec["bpm"],
                beats_per_bar=beats_per_bar,
                beat_unit=beat_unit,
            )
        return _events_to_notes(str(spec["id"]), spec["events"], tempo)
    notes = []
    start = 0.0
    sequence = tuple(note for section in spec["sections"] for note in section)
    for index, (pitch, duration) in enumerate(sequence):
        notes.append(
            Note.create(
                id=f"{spec['id']}-{index:03d}",
                pitch=pitch,
                start_time=start,
                duration=duration,
                velocity=78 + (index % 3) * 6,
                instrument="piano",
                created_at=_CREATED_AT,
                updated_at=_CREATED_AT,
            )
        )
        start = round(start + duration, 6)
    return tuple(notes)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _thumbnail_png(notes: tuple[Note, ...], width: int = 640, height: int = 320) -> bytes:
    pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            index = (y * width + x) * 3
            pixels[index:index + 3] = bytes((12 + y // 18, 20 + x // 32, 42 + (x + y) // 48))
    duration = max((note.start_time + note.duration for note in notes), default=1.0)
    for note in notes:
        x0 = int(note.start_time / duration * (width - 20)) + 10
        x1 = max(x0 + 2, int((note.start_time + note.duration) / duration * (width - 20)) + 10)
        y = height - 18 - int((note.pitch / 127) * (height - 36))
        for yy in range(max(0, y - 3), min(height, y + 4)):
            for xx in range(max(0, x0), min(width, x1)):
                index = (yy * width + xx) * 3
                pixels[index:index + 3] = bytes((96, 212, 194))
    rows = b"".join(b"\x00" + bytes(pixels[row * width * 3:(row + 1) * width * 3]) for row in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b"")


def generate_templates(output_root: str | Path | None = None) -> Path:
    root = Path(output_root) if output_root is not None else Path(__file__).parents[1] / "src" / "ai_piano_studio" / "resources" / "templates"
    root.mkdir(parents=True, exist_ok=True)
    for spec in TEMPLATE_SPECS:
        beats_per_bar, beat_unit = spec.get("meter", (4, 4))
        tempo = TempoMap(
            bpm=spec["bpm"],
            beats_per_bar=beats_per_bar,
            beat_unit=beat_unit,
        )
        notes = _notes(spec, tempo)
        state = ProjectState(notes, tempo=tempo, playback_notes=notes, playback_tempo=tempo)
        target = root / str(spec["id"])
        target.mkdir(parents=True, exist_ok=True)
        metadata = {
            key: spec[key]
            for key in ("id", "name", "composer", "description", "difficulty", "tags")
        }
        metadata["arrangement"] = "AI Piano Studio simplified arrangement"
        metadata["durationSeconds"] = state.playback_duration
        metadata["noteCount"] = len(state.playback_notes)
        (target / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (target / "template.pianoai.json").write_text(project_to_json(state), encoding="utf-8")
        (target / "thumbnail.png").write_bytes(_thumbnail_png(notes))
    return root


if __name__ == "__main__":
    generate_templates()
