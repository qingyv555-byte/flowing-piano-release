import struct

import pytest

from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID, MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.tempo import PPQ, TempoMap
from ai_piano_studio.editor.composite import CompositeGroupingMode, CompositeNoteGrouper
from ai_piano_studio.persistence.midi import (
    _varlen,
    encode_midi,
    export_midi,
    load_midi,
    materialize_midi_import,
    parse_midi,
)


DEFAULT_TEMPO = TempoMap()
TICKS_PER_SECOND = DEFAULT_TEMPO.seconds_to_ticks(1.0)


@pytest.mark.parametrize("signature", [(3, 8), (2, 2)])
def test_score_meters_roundtrip_without_changing_note_ticks(signature):
    tempo = TempoMap(bpm=88, beats_per_bar=signature[0], beat_unit=signature[1])
    notes = (Note.create(id="pickup", pitch=76, start_time=tempo.beats_to_seconds(1),
                         duration=tempo.beats_to_seconds(0.25)),)
    result = parse_midi(encode_midi(notes, tempo=tempo))
    assert (result.beats_per_bar, result.beat_unit) == signature
    assert "unsupported_time_signature" not in result.warnings
    assert result.notes[0].start_tick == PPQ
    assert result.notes[0].end_tick == PPQ + PPQ // 4


def midi_file(*tracks: bytes, fmt: int | None = None, division: int = PPQ) -> bytes:
    format_type = 0 if len(tracks) == 1 else 1 if fmt is None else fmt
    header = b"MThd" + struct.pack(">IHHH", 6, format_type, len(tracks), division)
    return header + b"".join(
        b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks
    )


def note_track(*events: bytes) -> bytes:
    return b"".join(events) + b"\x00\xff\x2f\x00"


def note_on(delta: int, pitch: int, velocity: int, status: int = 0x90) -> bytes:
    return _varlen(delta) + bytes((status, pitch, velocity))


def note_off(delta: int, pitch: int, velocity: int = 0, status: int = 0x80) -> bytes:
    return _varlen(delta) + bytes((status, pitch, velocity))


def meta(delta: int, kind: int, payload: bytes) -> bytes:
    return _varlen(delta) + bytes((0xFF, kind, len(payload))) + payload


def parse_events(payload: bytes) -> list[tuple[int, bytes]]:
    assert payload[:4] == b"MThd"
    _, fmt, tracks, division = struct.unpack(">IHHH", payload[4:14])
    assert (fmt, tracks, division) == (0, 1, PPQ)
    assert payload[14:18] == b"MTrk"
    track_length = struct.unpack(">I", payload[18:22])[0]
    track = payload[22 : 22 + track_length]

    events: list[tuple[int, bytes]] = []
    position = 0
    tick = 0
    while position < len(track):
        delta = 0
        while True:
            byte = track[position]
            position += 1
            delta = (delta << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        tick += delta
        status = track[position]
        if status == 0xFF:
            meta_type = track[position + 1]
            length = track[position + 2]
            data = track[position + 3 : position + 3 + length]
            events.append((tick, bytes((status, meta_type)) + data))
            position += 3 + length
            if meta_type == 0x2F:
                break
        else:
            events.append((tick, track[position : position + 3]))
            position += 3
    return events


def test_varlen_encoding() -> None:
    assert _varlen(0) == b"\x00"
    assert _varlen(0x7F) == b"\x7f"
    assert _varlen(0x80) == b"\x81\x00"
    assert _varlen(0x2000) == b"\xc0\x00"


def test_encode_midi_writes_tempo_and_note_events() -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.5, duration=0.5, velocity=90),
        Note.create(id="b", pitch=64, start_time=1.0, duration=0.25, velocity=70),
    )
    events = parse_events(encode_midi(notes))

    tempo = events[0]
    assert tempo[0] == 0
    assert tempo[1][:2] == b"\xff\x51"
    assert int.from_bytes(tempo[1][2:], "big") == 500_000

    signature = events[1]
    assert signature[1][:2] == b"\xff\x58"
    assert signature[1][2:] == bytes((4, 2, 24, 8))

    channel = [event for event in events if event[1][0] in (0x80, 0x90)]
    assert channel == [
        (int(0.5 * TICKS_PER_SECOND), bytes((0x90, 60, 90))),
        (int(1.0 * TICKS_PER_SECOND), bytes((0x80, 60, 0))),
        (int(1.0 * TICKS_PER_SECOND), bytes((0x90, 64, 70))),
        (int(1.25 * TICKS_PER_SECOND), bytes((0x80, 64, 0))),
    ]
    assert events[-1][1][:2] == b"\xff\x2f"


def test_encode_midi_note_off_precedes_note_on_at_same_tick() -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=1.0),
        Note.create(id="b", pitch=62, start_time=1.0, duration=1.0),
    )
    events = parse_events(encode_midi(notes))
    channel = [event for event in events if event[1][0] in (0x80, 0x90)]
    at_tick = [event for event in channel if event[0] == TICKS_PER_SECOND]
    assert [event[1][0] for event in at_tick] == [0x80, 0x90]


def test_encode_midi_uses_project_bpm_for_tempo_and_ticks() -> None:
    slow = TempoMap(bpm=60)
    notes = (Note.create(id="a", pitch=60, start_time=0.5, duration=0.5, velocity=90),)
    events = parse_events(encode_midi(notes, slow))

    tempo = events[0]
    assert int.from_bytes(tempo[1][2:], "big") == 1_000_000

    channel = [event for event in events if event[1][0] in (0x80, 0x90)]
    assert channel == [
        (slow.seconds_to_ticks(0.5), bytes((0x90, 60, 90))),
        (slow.seconds_to_ticks(1.0), bytes((0x80, 60, 0))),
    ]


def test_encode_midi_writes_project_time_signature() -> None:
    events = parse_events(encode_midi((), TempoMap(beats_per_bar=6, beat_unit=8)))
    signature = [event for event in events if event[1][:2] == b"\xff\x58"]
    assert len(signature) == 1
    assert signature[0][1][2:] == bytes((6, 3, 24, 8))


def test_encode_midi_meta_events_match_custom_project_tempo() -> None:
    waltz = TempoMap(bpm=90, beats_per_bar=3, beat_unit=4)
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=1.0),)
    events = parse_events(encode_midi(notes, waltz))

    tempo = events[0]
    assert tempo[0] == 0
    assert tempo[1][:2] == b"\xff\x51"
    assert len(tempo[1][2:]) == 3
    assert int.from_bytes(tempo[1][2:], "big") == 60_000_000 // 90

    signature = events[1]
    assert signature[0] == 0
    assert signature[1][:2] == b"\xff\x58"
    assert len(signature[1][2:]) == 4
    assert signature[1][2:] == bytes((3, 2, 24, 8))


def test_encode_midi_empty_notes_still_valid_file() -> None:
    events = parse_events(encode_midi(()))
    assert events[0][1][:2] == b"\xff\x51"
    assert events[-1][1][:2] == b"\xff\x2f"


def test_five_repeated_children_stay_five_in_smart_view_and_midi() -> None:
    notes = tuple(
        Note.create(id=str(index), pitch=60, start_time=index * 0.1, duration=0.4)
        for index in range(5)
    )

    group = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)[0]
    parsed = parse_midi(encode_midi(notes))

    assert group.trigger_count == 5
    assert group.child_note_ids == tuple(str(index) for index in range(5))
    assert len(parsed.notes) == 5


def test_parse_midi_preserves_identical_events_on_different_tracks() -> None:
    payload = midi_file(
        note_track(note_on(0, 60, 80), note_off(120, 60)),
        note_track(note_on(0, 60, 80), note_off(120, 60)),
    )

    result = parse_midi(payload)

    assert [
        (note.track, note.channel, note.start_tick, note.end_tick, note.pitch)
        for note in result.notes
    ] == [
        (0, 0, 0, 120, 60),
        (1, 0, 0, 120, 60),
    ]


def test_parse_midi_preserves_duplicate_same_track_polyphonic_events() -> None:
    payload = midi_file(
        note_track(
            note_on(0, 60, 80),
            note_on(0, 60, 80),
            note_off(120, 60),
            note_off(0, 60),
        )
    )

    result = parse_midi(payload)

    assert len(result.notes) == 2
    assert all(note.track == 0 and note.channel == 0 for note in result.notes)


def test_encode_midi_round_trips_track_and_channel_metadata() -> None:
    notes = (
        Note.create(
            id="track-0",
            pitch=60,
            start_time=0.0,
            duration=0.5,
            velocity=80,
            track=0,
            channel=1,
        ),
        Note.create(
            id="track-1",
            pitch=60,
            start_time=0.0,
            duration=0.5,
            velocity=80,
            track=1,
            channel=9,
        ),
    )

    parsed = parse_midi(encode_midi(notes))

    assert [(note.track, note.channel, note.pitch) for note in parsed.notes] == [
        (0, 1, 60),
        (1, 9, 60),
    ]


def test_export_midi_writes_file(tmp_path) -> None:
    target = tmp_path / "song.mid"
    export_midi(target, (Note.create(id="a", pitch=69, start_time=0.0, duration=1.0),))
    assert target.read_bytes()[:4] == b"MThd"


def test_parse_midi_reads_notes_tempo_and_time_signature() -> None:
    payload = midi_file(
        note_track(
            meta(0, 0x51, (500_000).to_bytes(3, "big")),
            meta(0, 0x58, bytes((3, 2, 24, 8))),
            note_on(0, 60, 90),
            note_off(240, 60),
        )
    )

    result = parse_midi(payload)

    assert result.ppq == PPQ
    assert result.bpm == 120
    assert (result.beats_per_bar, result.beat_unit) == (3, 4)
    assert result.track_count == 1
    assert result.note_track_count == 1
    assert result.tempo_event_count == 1
    assert result.notes[0].start_tick == 0
    assert result.notes[0].end_tick == 240
    assert result.notes[0].pitch == 60
    assert result.notes[0].velocity == 90


def test_parse_midi_supports_running_status_and_velocity_zero_note_off() -> None:
    result = parse_midi(
        midi_file(note_track(note_on(0, 64, 70), _varlen(37) + bytes((64, 0))))
    )

    assert [(note.start_tick, note.end_tick) for note in result.notes] == [(0, 37)]


def test_parse_midi_pairs_same_pitch_retriggers_fifo() -> None:
    result = parse_midi(
        midi_file(
            note_track(
                note_on(0, 60, 40),
                note_on(10, 60, 80),
                note_off(10, 60),
                note_off(10, 60),
            )
        )
    )

    assert [(note.start_tick, note.end_tick, note.velocity) for note in result.notes] == [
        (0, 20, 40),
        (10, 30, 80),
    ]


def test_parse_midi_merges_format_one_and_ignores_meta_only_tracks() -> None:
    result = parse_midi(
        midi_file(
            note_track(note_on(0, 67, 70), note_off(120, 67)),
            note_track(note_on(60, 72, 80), note_off(60, 72)),
            meta(0, 0x01, b"tempo map only") + b"\x00\xff\x2f\x00",
        )
    )

    assert result.track_count == 3
    assert result.note_track_count == 2
    assert [(note.track, note.start_tick, note.end_tick) for note in result.notes] == [
        (0, 0, 120),
        (1, 60, 120),
    ]


def test_parse_midi_warns_about_multiple_tempos_and_falls_back_for_signature() -> None:
    result = parse_midi(
        midi_file(
            note_track(
                meta(0, 0x51, (500_000).to_bytes(3, "big")),
                meta(100, 0x51, (1_000_000).to_bytes(3, "big")),
                meta(0, 0x58, bytes((5, 2, 24, 8))),
            )
        )
    )

    assert result.bpm == 120
    assert (result.beats_per_bar, result.beat_unit) == (4, 4)
    assert result.tempo_event_count == 2
    assert "multi_tempo" in result.warnings
    assert "unsupported_time_signature" in result.warnings


def test_parse_midi_preserves_non_quantized_tick_ranges() -> None:
    result = parse_midi(
        midi_file(note_track(note_on(13, 61, 55), note_off(29, 61)))
    )

    assert (result.notes[0].start_tick, result.notes[0].end_tick) == (13, 42)


def test_parse_midi_reports_unmatched_off_and_closes_dangling_note() -> None:
    result = parse_midi(
        midi_file(note_track(note_off(0, 60), note_on(10, 62, 80)))
    )

    assert len(result.notes) == 1
    assert result.notes[0].start_tick == 10
    assert result.notes[0].end_tick > result.notes[0].start_tick
    assert "unmatched_note_off" in result.warnings
    assert "dangling_note_on" in result.warnings


@pytest.mark.parametrize(
    "payload",
    [b"", b"MThd\x00\x00\x00\x06\x00\x00", b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"],
)
def test_parse_midi_rejects_malformed_or_unsupported_data(payload: bytes) -> None:
    with pytest.raises(ValueError):
        parse_midi(payload)


def test_load_midi_reads_a_real_file(tmp_path) -> None:
    target = tmp_path / "round-trip.mid"
    target.write_bytes(
        midi_file(note_track(note_on(0, 69, 100), note_off(PPQ, 69)))
    )

    result = load_midi(target)

    assert result.notes[0].pitch == 69
    assert result.notes[0].end_tick == PPQ


def test_materialize_single_track_midi_uses_one_default_layer() -> None:
    result = parse_midi(
        midi_file(
            note_track(
                meta(0, 0x03, "主旋律".encode("utf-8")),
                note_on(0, 60, 80),
                note_off(PPQ, 60),
            )
        )
    )

    notes, layers = materialize_midi_import(result, TempoMap(bpm=result.bpm))

    assert [(layer.id, layer.name, layer.order) for layer in layers] == [
        (DEFAULT_LAYER_ID, "主旋律", 0)
    ]
    assert notes[0].layer_id == DEFAULT_LAYER_ID
    assert (notes[0].track, notes[0].channel) == (0, 0)


def test_materialize_multitrack_midi_creates_stable_route_layers() -> None:
    result = parse_midi(
        midi_file(
            note_track(
                meta(0, 0x03, b"Lead"),
                note_on(0, 67, 90, 0x91),
                note_off(PPQ, 67, status=0x81),
            ),
            note_track(
                meta(0, 0x03, b"Bass"),
                note_on(0, 40, 70, 0x92),
                note_off(PPQ, 40, status=0x82),
            ),
        )
    )

    notes, layers = materialize_midi_import(result, TempoMap(bpm=result.bpm))

    assert [(layer.id, layer.name, layer.order) for layer in layers] == [
        ("layer-midi-0-1", "Lead", 0),
        ("layer-midi-1-2", "Bass", 1),
    ]
    assert [note.layer_id for note in notes] == ["layer-midi-0-1", "layer-midi-1-2"]


def test_encode_midi_with_layers_emits_layer_order_and_names() -> None:
    bass = MusicLayer.create(id="bass", name="低音", role="bass", order=0)
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody", order=1)
    notes = (
        Note.create(
            id="lead",
            pitch=72,
            start_time=0.0,
            duration=0.5,
            track=7,
            channel=3,
            layer_id="melody",
        ),
        Note.create(
            id="low",
            pitch=36,
            start_time=0.0,
            duration=0.5,
            track=4,
            channel=2,
            layer_id="bass",
        ),
    )

    parsed = parse_midi(encode_midi(notes, layers=(bass, melody)))

    assert parsed.track_names == ("低音", "主旋律")
    assert [(note.track, note.channel, note.pitch) for note in parsed.notes] == [
        (0, 2, 36),
        (1, 3, 72),
    ]
