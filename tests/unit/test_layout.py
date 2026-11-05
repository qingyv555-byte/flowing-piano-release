from ai_piano_studio.keyboard.layout import (
    DEFAULT_OCTAVE,
    MAX_OCTAVE,
    MIN_OCTAVE,
    clamp_octave,
    pitch_for_key,
)


def test_white_keys_map_to_c_major_scale() -> None:
    pitches = [pitch_for_key(ord(key), DEFAULT_OCTAVE) for key in "ASDFGHJ"]
    assert pitches == [60, 62, 64, 65, 67, 69, 71]


def test_black_keys_map_to_sharps() -> None:
    pitches = [pitch_for_key(ord(key), DEFAULT_OCTAVE) for key in "WETYU"]
    assert pitches == [61, 63, 66, 68, 70]


def test_black_keys_follow_octave_shift() -> None:
    assert pitch_for_key(ord("W"), 5) == 73
    assert pitch_for_key(ord("U"), 3) == 58


def test_octave_shift_moves_pitch_by_twelve() -> None:
    assert pitch_for_key(ord("A"), 5) == 72
    assert pitch_for_key(ord("A"), 3) == 48


def test_non_note_keys_return_none() -> None:
    assert pitch_for_key(ord("Q"), DEFAULT_OCTAVE) is None
    assert pitch_for_key(ord("1"), DEFAULT_OCTAVE) is None
    assert pitch_for_key(0x01000013, DEFAULT_OCTAVE) is None  # Qt.Key_Up


def test_clamp_octave_stays_within_bounds() -> None:
    assert clamp_octave(MIN_OCTAVE - 1) == MIN_OCTAVE
    assert clamp_octave(MAX_OCTAVE + 1) == MAX_OCTAVE
    assert clamp_octave(DEFAULT_OCTAVE) == DEFAULT_OCTAVE
    assert pitch_for_key(ord("J"), MAX_OCTAVE) == 12 * (MAX_OCTAVE + 1) + 11
