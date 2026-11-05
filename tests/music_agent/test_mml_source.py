from collections import Counter

import pytest

from ai_piano_studio.music_agent.mml_source import parse_mml
from ai_piano_studio.persistence.midi import materialize_midi_import


def seconds(result):
    notes, _ = materialize_midi_import(result)
    return sorted(notes, key=lambda n: (n.track, n.start_time))


def test_pitch_octave_absolute_notes_and_voice_identity():
    result = parse_mml("MML@O4 C C+ D- >C <C N48 D, o2c, ,o7c+;")
    assert [n.pitch for n in seconds(result)][:7] == [60, 61, 61, 72, 60, 60, 62]
    assert Counter(n.track for n in result.notes) == {0: 7, 1: 1, 3: 1}
    assert result.track_count == 4
    assert result.note_track_count == 3
    assert result.bpm == 120
    assert result.track_names == tuple(f"MML Voice {i}" for i in range(1, 5))


def test_default_dots_do_not_compound_and_explicit_length_resets_dots():
    notes = seconds(parse_mml("MML@l4.c.c.c8c8..c64;"))
    assert [n.duration for n in notes] == pytest.approx([.75, .75, .25, .4375, .03125])
    assert [n.start_time for n in notes] == pytest.approx([0, .75, 1.5, 1.75, 2.1875])


def test_ties_merge_same_pitch_across_default_changes_and_keep_attack_velocity():
    notes = seconds(parse_mml("MML@v15c4&l8v5c.&n48d;"))
    assert len(notes) == 2
    assert notes[0].duration == pytest.approx(1.125)
    assert notes[0].velocity == 127
    assert notes[1].start_time == pytest.approx(1.125)


def test_shared_tempo_from_other_voice_and_tie_crossing_tempo_change():
    result = parse_mml("MML@c4&c4d4,r4t60e4f4;")
    notes = seconds(result)
    assert [(n.start_time, n.duration) for n in notes] == pytest.approx(
        [(0, 1.5), (1.5, 1), (.5, 1), (1.5, 1)]
    )
    assert "mml_tempo_flattened" in result.warnings


def test_simultaneous_tempo_lowest_voice_wins_last_command_in_voice_wins():
    notes = seconds(parse_mml("MML@t100t120c4r4t60d4,t80e4f4g4;"))
    assert [n.start_time for n in notes] == pytest.approx([0, 1, 0, .5, 1])
    assert [n.duration for n in notes] == pytest.approx([.5, 1, .5, .5, 1])


def test_silent_volume_advances_time_without_creating_zero_velocity_note_on():
    notes = seconds(parse_mml("MML@v0c4&c4v15d4;"))
    assert len(notes) == 1
    assert notes[0].start_time == pytest.approx(1)
    assert notes[0].velocity == 127


@pytest.mark.parametrize("text", [
    "", "cdef", "MML@c", "MML@c;;", "MML@c;alert(1)",
    "MML@[cd]4;", "MML@q4c;", "MML@c0;", "MML@l3c;", "MML@c....;",
    "MML@o8c;", "MML@o0<c;", "MML@n96;", "MML@v16c;", "MML@t0c;",
    "MML@c&d;", "MML@c&r;", "MML@&c;", "MML@c&;", "MML@c&&c;",
    "MML@r1;", "MML@v0c;", "MML@t9999999999999999999999999c;",
])
def test_rejects_unsupported_or_malformed_scores(text):
    with pytest.raises(ValueError):
        parse_mml(text)


def test_bounded_input_voices_notes_and_duration():
    for text in ("MML@" + " " * 131072 + "c;", "MML@" + "," * 16 + "c;",
                 "MML@" + "c" * 30001 + ";", "MML@t60" + "r1" * 901 + "c;"):
        with pytest.raises(ValueError):
            parse_mml(text)


def test_low_source_tempo_preserves_seconds_with_supported_project_bpm():
    result = parse_mml("MML@t32c4;")
    assert result.bpm == 60
    assert seconds(result)[0].duration == pytest.approx(1.875)


def test_rejects_non_text():
    with pytest.raises(ValueError):
        parse_mml(b"MML@c;")
