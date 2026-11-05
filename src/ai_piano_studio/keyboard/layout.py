from __future__ import annotations


MIN_OCTAVE = 1
MAX_OCTAVE = 7
DEFAULT_OCTAVE = 4

# Computer-key (ASCII code) -> semitone offset from C within one octave.
# A S D F G H J = C D E F G A B (white keys)
# W E   T Y U   = C# D# F# G# A# (black keys)
KEY_SEMITONES = {
    ord("A"): 0,
    ord("W"): 1,
    ord("S"): 2,
    ord("E"): 3,
    ord("D"): 4,
    ord("F"): 5,
    ord("T"): 6,
    ord("G"): 7,
    ord("Y"): 8,
    ord("H"): 9,
    ord("U"): 10,
    ord("J"): 11,
}


def clamp_octave(octave: int) -> int:
    return max(MIN_OCTAVE, min(MAX_OCTAVE, octave))


def pitch_for_key(key: int, octave: int) -> int | None:
    """Map a computer key code to a MIDI pitch, or None if it is not a note key."""
    semitone = KEY_SEMITONES.get(key)
    if semitone is None:
        return None
    # C of octave N is MIDI pitch 12 * (N + 1); C4 = 60.
    return 12 * (clamp_octave(octave) + 1) + semitone
