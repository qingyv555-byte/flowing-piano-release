from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping
from uuid import uuid4

from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID
from ai_piano_studio.domain.release import (
    DEFAULT_CUSTOM_RELEASE_MS,
    DEFAULT_RELEASE_MODE,
    MAX_RELEASE_MS,
    NOTE_RELEASE_MODES,
    normalize_release,
)


_PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
NOTE_STATUSES = frozenset({"saved", "pending", "modified", "deleted"})
# MIDI file headers store the number of tracks as an unsigned 16-bit value.
# A routed note at index 65,535 would require 65,536 tracks, which cannot be
# represented, so keep the highest valid zero-based track index one lower.
MAX_MIDI_TRACK = 65_534
MIDI_CHANNELS = 16


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def midi_to_name(pitch: int) -> str:
    if not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127:
        raise ValueError("pitch must be an integer from 0 to 127")
    return f"{_PITCH_CLASSES[pitch % 12]}{pitch // 12 - 1}"


def _normalize_midi_routing(
    track: int | None, channel: int | None
) -> tuple[int | None, int | None]:
    if track is not None and (
        isinstance(track, bool)
        or not isinstance(track, int)
        or not 0 <= track <= MAX_MIDI_TRACK
    ):
        raise ValueError(f"track must be an integer from 0 to {MAX_MIDI_TRACK}")
    if channel is not None and (
        isinstance(channel, bool)
        or not isinstance(channel, int)
        or not 0 <= channel < MIDI_CHANNELS
    ):
        raise ValueError(f"channel must be an integer from 0 to {MIDI_CHANNELS - 1}")
    return track, channel


@dataclass(frozen=True, slots=True)
class Note:
    id: str
    pitch: int
    note_name: str
    start_time: float
    duration: float
    velocity: int
    instrument: str
    status: str
    created_at: str
    updated_at: str
    release_mode: str = DEFAULT_RELEASE_MODE
    release_ms: float | None = None
    track: int | None = None
    channel: int | None = None
    layer_id: str = DEFAULT_LAYER_ID
    on_order: int | None = None
    off_order: int | None = None

    @classmethod
    def create(
        cls,
        *,
        pitch: int,
        start_time: float,
        duration: float,
        velocity: int = 75,
        instrument: str = "piano",
        id: str | None = None,
        status: str = "saved",
        created_at: str | None = None,
        updated_at: str | None = None,
        release_mode: str = DEFAULT_RELEASE_MODE,
        release_ms: float | None = None,
        track: int | None = None,
        channel: int | None = None,
        layer_id: str = DEFAULT_LAYER_ID,
        on_order: int | None = None,
        off_order: int | None = None,
    ) -> Note:
        midi_to_name(pitch)
        release_mode, release_ms = normalize_release(release_mode, release_ms)
        track, channel = _normalize_midi_routing(track, channel)
        if any(v is not None and (type(v) is not int or v < 0) for v in (on_order, off_order)):
            raise ValueError('MIDI event order must be nonnegative')
        if (
            isinstance(start_time, bool)
            or not isinstance(start_time, (int, float))
            or not isfinite(float(start_time))
            or start_time < 0
        ):
            raise ValueError("start_time must be a number greater than or equal to 0")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not isfinite(float(duration))
            or duration <= 0
        ):
            raise ValueError("duration must be a number greater than 0")
        if not isinstance(velocity, int) or isinstance(velocity, bool) or not 0 <= velocity <= 127:
            raise ValueError("velocity must be an integer from 0 to 127")
        if not isinstance(instrument, str) or not instrument.strip():
            raise ValueError("instrument cannot be empty")
        if id is not None and (not isinstance(id, str) or not id.strip()):
            raise ValueError("id cannot be empty")
        if status not in NOTE_STATUSES:
            raise ValueError(f"status must be one of {sorted(NOTE_STATUSES)}")
        if created_at is not None and (not isinstance(created_at, str) or not created_at.strip()):
            raise ValueError("created_at cannot be empty")
        if updated_at is not None and (not isinstance(updated_at, str) or not updated_at.strip()):
            raise ValueError("updated_at cannot be empty")
        if not isinstance(layer_id, str) or not layer_id.strip():
            raise ValueError("layer_id cannot be empty")
        created = created_at or _timestamp()
        updated = updated_at or created

        return cls(
            id=id or str(uuid4()),
            pitch=pitch,
            note_name=midi_to_name(pitch),
            start_time=float(start_time),
            duration=float(duration),
            velocity=velocity,
            instrument=instrument,
            status=status,
            created_at=created,
            updated_at=updated,
            release_mode=release_mode,
            release_ms=release_ms,
            track=track,
            channel=channel,
            layer_id=layer_id.strip(),
            on_order=on_order, off_order=off_order,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Note:
        return cls.create(
            id=data["id"],
            pitch=data["pitch"],
            start_time=data["startTime"],
            duration=data["duration"],
            velocity=data["velocity"],
            instrument=data["instrument"],
            status=data.get("status", "saved"),
            created_at=data.get("createdAt"),
            updated_at=data.get("updatedAt"),
            release_mode=data.get("releaseMode", DEFAULT_RELEASE_MODE),
            release_ms=data.get("releaseMs"),
            track=data.get("track"),
            channel=data.get("channel"),
            layer_id=data.get("layerId", DEFAULT_LAYER_ID),
            on_order=data.get('onOrder'), off_order=data.get('offOrder'),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "pitch": self.pitch,
            "noteName": self.note_name,
            "startTime": self.start_time,
            "duration": self.duration,
            "velocity": self.velocity,
            "instrument": self.instrument,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "releaseMode": self.release_mode,
            "releaseMs": self.release_ms,
            "layerId": self.layer_id,
        }
        if self.track is not None:
            payload["track"] = self.track
        if self.channel is not None:
            payload["channel"] = self.channel
        if self.on_order is not None:
            payload['onOrder'] = self.on_order
        if self.off_order is not None:
            payload['offOrder'] = self.off_order
        return payload

    def with_changes(self, **changes: Any) -> Note:
        values = {
            "id": self.id,
            "pitch": self.pitch,
            "start_time": self.start_time,
            "duration": self.duration,
            "velocity": self.velocity,
            "instrument": self.instrument,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": _timestamp(),
            "release_mode": self.release_mode,
            "release_ms": self.release_ms,
            "track": self.track,
            "channel": self.channel,
            "layer_id": self.layer_id,
            "on_order": self.on_order, "off_order": self.off_order,
        }
        values.update(changes)
        values.pop("note_name", None)
        return Note.create(**values)
