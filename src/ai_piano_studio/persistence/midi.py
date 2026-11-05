from __future__ import annotations

import struct
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID, MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.tempo import (
    DEFAULT_BPM,
    DEFAULT_TIME_SIGNATURE,
    MAX_BPM,
    MIN_BPM,
    PPQ,
    TIME_SIGNATURES,
    TempoMap,
)
from ai_piano_studio.persistence.atomic import atomic_write_bytes


@dataclass(frozen=True, slots=True)
class MidiNoteEvent:
    """A note range in the source file's native tick/PPQ coordinate system."""

    start_tick: int
    end_tick: int
    pitch: int
    velocity: int
    channel: int
    track: int
    on_order: int | None = None
    off_order: int | None = None


@dataclass(frozen=True, slots=True)
class MidiControlEvent:
    tick: int
    value: int
    channel: int
    track: int
    order: int


@dataclass(frozen=True, slots=True)
class MidiImportResult:
    """Parsed MIDI content without converting away the source timing precision."""

    notes: tuple[MidiNoteEvent, ...]
    ppq: int
    bpm: int
    beats_per_bar: int
    beat_unit: int
    track_count: int
    note_track_count: int
    tempo_event_count: int
    warnings: tuple[str, ...]
    track_names: tuple[str, ...] = ()
    controls: tuple[MidiControlEvent, ...] = ()


def _read_vlq(payload: bytes, position: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if position >= len(payload):
            raise ValueError("truncated MIDI variable-length value")
        byte = payload[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("MIDI variable-length value is too long")


def _read_bytes(payload: bytes, position: int, length: int) -> tuple[bytes, int]:
    end = position + length
    if length < 0 or end > len(payload):
        raise ValueError("truncated MIDI event payload")
    return payload[position:end], end


def _decode_track(
    payload: bytes,
    track_index: int,
) -> tuple[
    list[MidiNoteEvent],
    list[tuple[int, int]],
    list[tuple[int, int, int]],
    bool,
    int,
    set[str],
    str,
    list[MidiControlEvent],
]:
    position = 0
    tick = 0
    running_status: int | None = None
    active: defaultdict[tuple[int, int], deque[tuple[int, int, int]]] = defaultdict(deque)
    controls: list[MidiControlEvent] = []
    event_order = 0
    notes: list[MidiNoteEvent] = []
    tempo_events: list[tuple[int, int]] = []
    signatures: list[tuple[int, int, int, int]] = []
    warnings: set[str] = set()
    has_note_event = False
    reached_end = False
    track_name = ""

    while position < len(payload):
        event_order += 1
        delta, position = _read_vlq(payload, position)
        tick += delta
        if position >= len(payload):
            raise ValueError("truncated MIDI event status")
        first = payload[position]
        position += 1
        if first < 0x80:
            if running_status is None or running_status >= 0xF0:
                raise ValueError("MIDI data byte has no running status")
            status = running_status
            first_data = first
        else:
            status = first
            first_data = None
            if status < 0xF0:
                running_status = status
            else:
                running_status = None

        if status == 0xFF:
            if position >= len(payload):
                raise ValueError("truncated MIDI meta event type")
            meta_type = payload[position]
            position += 1
            length, position = _read_vlq(payload, position)
            data, position = _read_bytes(payload, position, length)
            if meta_type == 0x51 and len(data) == 3:
                microseconds = int.from_bytes(data, "big")
                if microseconds <= 0:
                    raise ValueError("MIDI tempo must be positive")
                tempo_events.append((tick, microseconds))
            elif meta_type == 0x58 and len(data) >= 2:
                denominator_power = data[1]
                if denominator_power > 7:
                    beat_unit = 1 << 7
                else:
                    beat_unit = 1 << denominator_power
                signatures.append((tick, data[0], beat_unit))
            elif meta_type == 0x03 and not track_name:
                track_name = data.decode("utf-8", errors="replace").strip()
            if meta_type == 0x2F:
                reached_end = True
                break
            continue

        if status in (0xF0, 0xF7):
            length, position = _read_vlq(payload, position)
            _, position = _read_bytes(payload, position, length)
            continue

        if status < 0x80 or status >= 0xF0:
            raise ValueError(f"unsupported MIDI status 0x{status:02x}")
        message_type = status >> 4
        channel = status & 0x0F
        data_length = 1 if message_type in (0xC, 0xD) else 2
        data = bytearray()
        if first_data is not None:
            data.append(first_data)
        remaining = data_length - len(data)
        rest, position = _read_bytes(payload, position, remaining)
        data.extend(rest)
        if any(byte > 0x7F for byte in data):
            raise ValueError("invalid MIDI channel data byte")

        if message_type == 0xB and data[0] == 64:
            controls.append(MidiControlEvent(tick, data[1], channel, track_index, event_order))
        elif message_type == 0xB and data[0] in (66, 67):
            warnings.add('unsupported_pedals')
        elif message_type == 0xB and data[0] >= 120:
            warnings.add('unsupported_channel_modes')
        if message_type not in (0x8, 0x9):
            continue
        pitch = data[0]
        velocity = data[1]
        key = (channel, pitch)
        has_note_event = True
        if message_type == 0x9 and velocity:
            active[key].append((tick, velocity, event_order))
            continue
        if not active[key]:
            warnings.add("unmatched_note_off")
            continue
        start_tick, start_velocity, on_order = active[key].popleft()
        if tick <= start_tick:
            warnings.add("zero_length_note")
            continue
        notes.append(
            MidiNoteEvent(
                start_tick=start_tick,
                end_tick=tick,
                pitch=pitch,
                velocity=start_velocity,
                channel=channel,
                track=track_index,
                on_order=on_order,
                off_order=event_order,
            )
        )

    if not reached_end and position >= len(payload):
        raise ValueError("MIDI track is missing End Of Track")
    for (channel, pitch), pending in active.items():
        while pending:
            start_tick, velocity, on_order = pending.popleft()
            end_tick = max(tick, start_tick + 1)
            notes.append(
                MidiNoteEvent(
                    start_tick=start_tick,
                    end_tick=end_tick,
                    pitch=pitch,
                    velocity=velocity,
                    channel=channel,
                    track=track_index,
                    on_order=on_order,
                    off_order=event_order + 1,
                )
            )
            warnings.add("dangling_note_on")
    return notes, tempo_events, signatures, has_note_event, tick, warnings, track_name, controls


def parse_midi(data: bytes) -> MidiImportResult:
    """Parse a format 0/1 Standard MIDI file into exact tick-based note events."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("MIDI data must be bytes")
    payload = bytes(data)
    if len(payload) < 14 or payload[:4] != b"MThd":
        raise ValueError("invalid MIDI header")
    header_length = struct.unpack_from(">I", payload, 4)[0]
    if header_length < 6 or 8 + header_length > len(payload):
        raise ValueError("truncated MIDI header")
    format_type, track_count, division = struct.unpack_from(">HHH", payload, 8)
    if format_type not in (0, 1):
        raise ValueError("only MIDI format 0 and 1 are supported")
    if track_count <= 0:
        raise ValueError("MIDI file must contain at least one track")
    if division & 0x8000 or division == 0:
        raise ValueError("SMPTE or invalid MIDI division is not supported")

    position = 8 + header_length
    all_notes: list[MidiNoteEvent] = []
    all_controls: list[MidiControlEvent] = []
    all_tempos: list[tuple[int, int, int]] = []
    all_signatures: list[tuple[int, int, int, int]] = []
    warnings: set[str] = set()
    note_track_count = 0
    track_names: list[str] = []
    for track_index in range(track_count):
        if position + 8 > len(payload) or payload[position : position + 4] != b"MTrk":
            raise ValueError("invalid or truncated MIDI track chunk")
        track_length = struct.unpack_from(">I", payload, position + 4)[0]
        track_start = position + 8
        track_end = track_start + track_length
        if track_end > len(payload):
            raise ValueError("truncated MIDI track data")
        notes, tempos, signatures, has_note_event, _, track_warnings, track_name, controls = _decode_track(
            payload[track_start:track_end], track_index
        )
        all_notes.extend(notes)
        all_controls.extend(controls)
        all_tempos.extend((tick, track_index, value) for tick, value in tempos)
        all_signatures.extend(
            (tick, track_index, beats_per_bar, beat_unit)
            for tick, beats_per_bar, beat_unit in signatures
        )
        warnings.update(track_warnings)
        note_track_count += has_note_event
        track_names.append(track_name)
        position = track_end

    all_tempos.sort(key=lambda event: (event[0], event[1]))
    all_signatures.sort(key=lambda event: (event[0], event[1]))
    if len({e[2:] for e in all_signatures}) > 1:
        warnings.add("multi_time_signature")
    pedal_state = {}
    for event in sorted(all_controls, key=lambda e: (e.tick, e.track, e.order)):
        pedal_state[event.channel] = event.value >= 64
    if any(pedal_state.values()):
        warnings.add("dangling_sustain")
    tempo_event_count = len(all_tempos)
    if tempo_event_count > 1:
        warnings.add("multi_tempo")
    microseconds_per_quarter = all_tempos[0][2] if all_tempos else 60_000_000 // DEFAULT_BPM
    bpm = round(60_000_000 / microseconds_per_quarter)
    if bpm < MIN_BPM or bpm > MAX_BPM:
        bpm = min(MAX_BPM, max(MIN_BPM, bpm))
        warnings.add("tempo_clamped")

    beats_per_bar, beat_unit = DEFAULT_TIME_SIGNATURE
    for _, _, candidate_beats, candidate_unit in all_signatures:
        if (candidate_beats, candidate_unit) in TIME_SIGNATURES:
            beats_per_bar, beat_unit = candidate_beats, candidate_unit
            break
    if all_signatures and not any(
        (candidate_beats, candidate_unit) in TIME_SIGNATURES
        for _, _, candidate_beats, candidate_unit in all_signatures
    ):
        warnings.add("unsupported_time_signature")

    # Do not deduplicate note ranges here. Identical ranges can be legitimate
    # layered voices, and the source track/channel are part of their identity.
    notes = tuple(
        sorted(
            all_notes,
            key=lambda note: (
                note.start_tick,
                note.pitch,
                note.channel,
                note.track,
                note.end_tick,
            ),
        )
    )
    return MidiImportResult(
        notes=notes,
        ppq=division,
        bpm=bpm,
        beats_per_bar=beats_per_bar,
        beat_unit=beat_unit,
        track_count=track_count,
        note_track_count=note_track_count,
        tempo_event_count=tempo_event_count,
        warnings=tuple(sorted(warnings)),
        track_names=tuple(track_names),
        controls=tuple(sorted(all_controls, key=lambda e: (e.tick, e.track, e.order))),
    )


def load_midi(path: str | Path) -> MidiImportResult:
    """Read and parse a local Standard MIDI file."""

    return parse_midi(Path(path).read_bytes())


def materialize_midi_import(
    result: MidiImportResult,
    tempo: TempoMap | None = None,
    *,
    status: str = "pending",
) -> tuple[tuple[Note, ...], tuple[MusicLayer, ...]]:
    """Convert source MIDI routes into stable first-class layers and notes."""
    if not isinstance(result, MidiImportResult):
        raise ValueError("result must be MidiImportResult")
    tempo = tempo or TempoMap(
        bpm=result.bpm,
        beats_per_bar=result.beats_per_bar,
        beat_unit=result.beat_unit,
    )
    routes = tuple(sorted({(event.track, event.channel) for event in (*result.notes, *result.controls)}))
    if not routes:
        return (), (MusicLayer.default(),)

    single_route = len(routes) == 1
    route_order = {route: index for index, route in enumerate(routes)}
    layers: list[MusicLayer] = []
    route_layer_ids: dict[tuple[int, int], str] = {}
    used_names: set[str] = set()
    for order, (track, channel) in enumerate(routes):
        layer_id = DEFAULT_LAYER_ID if single_route else f"layer-midi-{track}-{channel}"
        source_name = (
            result.track_names[track].strip()
            if track < len(result.track_names) and result.track_names[track].strip()
            else ""
        )
        name = source_name or ("默认音层" if single_route else f"音乐层 {order + 1}")
        if name in used_names:
            name = f"{name} · 通道 {channel + 1}"
        used_names.add(name)
        role = "drums" if channel == 9 else "other"
        layers.append(
            MusicLayer.create(
                id=layer_id,
                name=name,
                role=role,
                color_role=role,
                order=order,
            )
        )
        route_layer_ids[(track, channel)] = layer_id

    notes = tuple(
        Note.create(
            pitch=event.pitch,
            start_time=tempo.ticks_to_seconds(event.start_tick, result.ppq),
            duration=tempo.ticks_to_seconds(
                event.end_tick - event.start_tick, result.ppq
            ),
            velocity=event.velocity,
            instrument="piano",
            status=status,
            track=event.track,
            channel=event.channel,
            layer_id=route_layer_ids[(event.track, event.channel)],
            on_order=event.on_order,
            off_order=event.off_order,
        )
        for event in sorted(
            result.notes,
            key=lambda item: (
                route_order[(item.track, item.channel)],
                item.start_tick,
                item.pitch,
                item.end_tick,
            ),
        )
        if event.end_tick > event.start_tick
    )
    return notes, tuple(layers)


def materialize_midi_controls(result: MidiImportResult, tempo: TempoMap | None = None) -> tuple[ControlEvent, ...]:
    tempo = tempo or TempoMap(bpm=result.bpm, beats_per_bar=result.beats_per_bar, beat_unit=result.beat_unit)
    routes = {(e.track, e.channel) for e in (*result.notes, *result.controls)}
    return tuple(ControlEvent.create(
        time=tempo.ticks_to_seconds(e.tick, result.ppq), value=e.value,
        channel=e.channel, track=e.track, order=e.order,
        layer_id=DEFAULT_LAYER_ID if len(routes) == 1 else f"layer-midi-{e.track}-{e.channel}",
    ) for e in result.controls)


def _varlen(value: int) -> bytes:
    if value < 0:
        raise ValueError("delta time cannot be negative")
    bytes_reversed = [value & 0x7F]
    value >>= 7
    while value:
        bytes_reversed.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(bytes_reversed))


def _encode_midi_track(
    events: Iterable[tuple[int, int, int, bytes]],
    tempo: TempoMap,
    *,
    include_tempo: bool,
    track_name: str = "",
) -> bytes:
    track = bytearray()
    if track_name:
        encoded_name = track_name.encode("utf-8")[:255]
        track += b"\x00\xff\x03" + _varlen(len(encoded_name)) + encoded_name
    if include_tempo:
        microseconds_per_quarter = 60_000_000 // tempo.bpm
        track += b"\x00\xff\x51\x03" + microseconds_per_quarter.to_bytes(3, "big")
        # Time signature meta event: nn dd cc bb (dd as power of two).
        track += b"\x00\xff\x58\x04" + bytes(
            (tempo.beats_per_bar, tempo.beat_unit.bit_length() - 1, 24, 8)
        )

    previous_tick = 0
    for tick, _, _, message in sorted(events, key=lambda event: (event[0], event[1], event[2])):
        track += _varlen(tick - previous_tick)
        track += message
        previous_tick = tick
    track += b"\x00\xff\x2f\x00"
    return bytes(track)


def encode_midi(
    notes: Iterable[Note],
    tempo: TempoMap | None = None,
    *,
    layers: Iterable[MusicLayer] | None = None,
    controls: Iterable[ControlEvent] = (),
) -> bytes:
    """Encode notes while retaining optional source track/channel routing."""
    tempo = tempo or TempoMap()
    note_list = tuple(notes)
    control_list = tuple(controls)
    # Explicit MIDI channels remain shared across tracks. Local layers with
    # pedal data need distinct channels, including layers with no CC events.
    local_channels = {}
    if control_list:
        used = {e.channel for e in (*note_list, *control_list) if e.channel is not None}
        available = iter(ch for ch in range(16) if ch not in used and ch != 9)
        for layer_id in dict.fromkeys(e.layer_id for e in (*note_list, *control_list) if e.channel is None):
            channel = next(available, None)
            if channel is None:
                raise ValueError("MIDI channels exhausted: independent pedal layers cannot be merged")
            local_channels[layer_id] = channel
    layer_list = tuple(sorted(layers or (), key=lambda layer: layer.order))
    layer_tracks = {layer.id: index for index, layer in enumerate(layer_list)}
    routed = bool(layer_list) or any(
        note.track is not None or note.channel is not None for note in (*note_list, *control_list)
    )
    events_by_track: dict[int, list[tuple[int, int, int, bytes]]] = defaultdict(list)
    layer_names = {layer.id: layer.name for layer in layer_list}
    names_by_track = defaultdict(list)

    def remember_layer_name(track, layer_id):
        name = layer_names.get(layer_id)
        if name and name not in names_by_track[track]:
            names_by_track[track].append(name)

    for sequence, note in enumerate(note_list):
        track = (
            note.track if control_list and note.track is not None else layer_tracks[note.layer_id]
            if layer_list and note.layer_id in layer_tracks
            else 0 if note.track is None else note.track
        )
        remember_layer_name(track, note.layer_id)
        channel = local_channels.get(note.layer_id, 0) if note.channel is None else note.channel
        on_tick = tempo.seconds_to_ticks(note.start_time, PPQ)
        off_tick = tempo.seconds_to_ticks(note.start_time + note.duration, PPQ)
        if off_tick <= on_tick:
            off_tick = on_tick + 1
        events_by_track[track].append(
            (on_tick, note.on_order if note.on_order is not None else 1, sequence, bytes((0x90 | channel, note.pitch, note.velocity)))
        )
        events_by_track[track].append(
            (off_tick, note.off_order if note.off_order is not None else -1, sequence, bytes((0x80 | channel, note.pitch, 0)))
        )

    for sequence, event in enumerate(control_list, len(note_list)):
        track = event.track if event.track is not None else layer_tracks.get(event.layer_id, 0)
        remember_layer_name(track, event.layer_id)
        channel = local_channels.get(event.layer_id, 0) if event.channel is None else event.channel
        events_by_track[track].append((tempo.seconds_to_ticks(event.time, PPQ), event.order, sequence, bytes((0xB0 | channel, 64, event.value))))

    if not routed:
        track = _encode_midi_track(
            events_by_track.get(0, ()), tempo, include_tempo=True
        )
        header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ)
        return header + b"MTrk" + struct.pack(">I", len(track)) + track

    max_track = max(events_by_track, default=0)
    track_numbers = range(max(len(layer_list), max_track + 1))
    encoded_tracks = [
        _encode_midi_track(
            events_by_track.get(track_number, ()),
            tempo,
            include_tempo=track_number == 0,
            track_name=(
                ' / '.join(names_by_track[track_number]) if control_list else layer_list[track_number].name
                if track_number < len(layer_list)
                else ""
            ),
        )
        for track_number in track_numbers
    ]
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(encoded_tracks), PPQ)
    return header + b"".join(
        b"MTrk" + struct.pack(">I", len(track)) + track
        for track in encoded_tracks
    )


def export_midi(
    path: str | Path,
    notes: Iterable[Note],
    tempo: TempoMap | None = None,
    *,
    layers: Iterable[MusicLayer] | None = None,
    controls: Iterable[ControlEvent] = (),
) -> None:
    atomic_write_bytes(path, encode_midi(notes, tempo, layers=layers, controls=controls))
