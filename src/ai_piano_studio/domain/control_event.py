"""Authoritative CC events and their derived sustain release boundary."""
from bisect import bisect_right
from dataclasses import dataclass, replace
from math import isfinite
from uuid import uuid4

from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID
from ai_piano_studio.domain.note import Note, _normalize_midi_routing


@dataclass(frozen=True, slots=True)
class ControlEvent:
    id: str
    time: float
    value: int
    layer_id: str = DEFAULT_LAYER_ID
    channel: int | None = None
    track: int | None = None
    order: int = 0
    controller: int = 64

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError('control id is required')
        if type(self.time) not in (int, float) or not isfinite(self.time) or self.time < 0:
            raise ValueError('control time must be finite and nonnegative')
        if type(self.value) is not int or not 0 <= self.value <= 127:
            raise ValueError('control value must be 0–127')
        if type(self.controller) is not int or self.controller != 64:
            raise ValueError('only CC64 is supported')
        if type(self.order) is not int or self.order < 0:
            raise ValueError('control order must be nonnegative')
        if not isinstance(self.layer_id, str) or not self.layer_id:
            raise ValueError('control layer is required')
        _normalize_midi_routing(self.track, self.channel)

    @classmethod
    def create(cls, **values):
        values.setdefault('id', str(uuid4()))
        return cls(**values)

    def with_changes(self, **changes):
        return replace(self, **changes)

    def to_dict(self):
        return {'id': self.id, 'time': self.time, 'value': self.value,
                'layerId': self.layer_id, 'channel': self.channel, 'track': self.track,
                'order': self.order, 'controller': self.controller}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError('control event must be an object')
        if not {'id', 'time', 'value'} <= raw.keys():
            raise ValueError('control event is missing id, time or value')
        return cls(id=raw['id'], time=raw['time'], value=raw['value'],
                   layer_id=raw.get('layerId', DEFAULT_LAYER_ID), channel=raw.get('channel'),
                   track=raw.get('track'), order=raw.get('order', 0),
                   controller=raw.get('controller', 64))


def control_route(item):
    # MIDI channels are shared across file tracks; locally authored layers are
    # independent unless they explicitly carry a channel route.
    return ('channel', item.channel) if item.channel is not None else ('layer', item.layer_id)


def event_key(time, track, order):
    return (time, track if track is not None else 0, order)


def release_times(notes, controls):
    """Resolve release conditions, never modify key durations or stored events.

    Same-time file events merge by source track then source order. Authored note
    offs without source order precede CC actions at that instant. Missing pedal
    up is bounded by the musical end; samples are never looped for sustain.
    """
    notes, controls = tuple(notes), tuple(controls)
    end = max([0.0] + [n.start_time + n.duration for n in notes] + [e.time for e in controls])
    routes = {}
    for event in sorted(controls, key=lambda e: (*event_key(e.time, e.track, e.order), e.id)):
        routes.setdefault(control_route(event), []).append(event)
    indexes = {}
    for route, events in routes.items():
        keys = [event_key(e.time, e.track, e.order) for e in events]
        ups = [(i, e.time) for i, e in enumerate(events) if e.value < 64]
        indexes[route] = (events, keys, [i for i, _ in ups], [t for _, t in ups])
    result = {}
    for note in notes:
        off = note.start_time + note.duration
        result[note.id] = off
        route = indexes.get(control_route(note))
        if route is None:
            continue
        events, keys, up_indices, up_times = route
        order = getattr(note, 'off_order', None)
        i = bisect_right(keys, event_key(off, note.track, -1 if order is None else order)) - 1
        if i >= 0 and events[i].value >= 64:
            up = bisect_right(up_indices, i)
            result[note.id] = max(off, up_times[up] if up < len(up_times) else end)
    return result


@dataclass(frozen=True, slots=True)
class SoundingNote:
    """Audio-only projection. The Note and its physical key duration stay intact."""
    note: Note
    release_time: float

    def __getattr__(self, name):
        return getattr(self.note, name)

    @property
    def sounding_duration(self):
        return self.release_time - self.note.start_time

    def with_changes(self, **changes):
        changed = self.note.with_changes(**changes)
        return SoundingNote(changed, self.release_time + changed.start_time - self.note.start_time)


def sustain_snapshot(notes, controls):
    notes, controls = tuple(notes), tuple(controls)
    if not controls:
        return notes
    releases = release_times(notes, controls)
    return tuple(SoundingNote(n, releases[n.id]) for n in notes)


def sounding_duration(note):
    return getattr(note, 'sounding_duration', note.duration)
