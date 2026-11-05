from __future__ import annotations

from dataclasses import dataclass


PPQ = 480
MIN_BPM = 60
MAX_BPM = 300
DEFAULT_BPM = 120

# Supported time signatures: (beats_per_bar, beat_unit).
TIME_SIGNATURES = ((4, 4), (3, 4), (6, 8), (3, 8), (2, 2))
DEFAULT_TIME_SIGNATURE = (4, 4)

# Snap divisions expressed as note values: 1/4, 1/8, 1/16 of a whole note.
SNAP_DIVISIONS = (4, 8, 16)
DEFAULT_SNAP_DIVISION = 8


def validate_bpm(bpm: int) -> int:
    if not isinstance(bpm, int) or isinstance(bpm, bool) or not MIN_BPM <= bpm <= MAX_BPM:
        raise ValueError(f"bpm must be an integer from {MIN_BPM} to {MAX_BPM}")
    return bpm


def validate_time_signature(beats_per_bar: int, beat_unit: int) -> tuple[int, int]:
    if any(not isinstance(value, int) or isinstance(value, bool)
           for value in (beats_per_bar, beat_unit)):
        raise ValueError("time signature values must be integers")
    if (beats_per_bar, beat_unit) not in TIME_SIGNATURES:
        raise ValueError("time signature must be one of 4/4, 3/4, 6/8, 3/8, 2/2")
    return beats_per_bar, beat_unit


def validate_snap_division(division: int) -> int:
    if division not in SNAP_DIVISIONS:
        raise ValueError("snap division must be one of 4, 8, 16")
    return division


@dataclass(frozen=True, slots=True)
class TempoMap:
    """Converts between seconds, beats and MIDI ticks for one tempo.

    Notes stay anchored to beats: changing the BPM rescales their seconds
    so playback speed follows the project tempo.
    """

    bpm: int = DEFAULT_BPM
    beats_per_bar: int = DEFAULT_TIME_SIGNATURE[0]
    beat_unit: int = DEFAULT_TIME_SIGNATURE[1]

    def __post_init__(self) -> None:
        validate_bpm(self.bpm)
        validate_time_signature(self.beats_per_bar, self.beat_unit)

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    @property
    def beats_per_bar_length(self) -> float:
        """Bar length measured in quarter-note beats."""
        return self.beats_per_bar * 4.0 / self.beat_unit

    @property
    def bar_duration_seconds(self) -> float:
        return self.beats_per_bar_length * self.seconds_per_beat

    def beats_to_seconds(self, beats: float) -> float:
        return beats * self.seconds_per_beat

    def seconds_to_beats(self, seconds: float) -> float:
        return seconds / self.seconds_per_beat

    def beats_to_ticks(self, beats: float, ppq: int = PPQ) -> int:
        return round(beats * ppq)

    def seconds_to_ticks(self, seconds: float, ppq: int = PPQ) -> int:
        return self.beats_to_ticks(self.seconds_to_beats(seconds), ppq)

    def ticks_to_seconds(self, ticks: int, ppq: int = PPQ) -> float:
        return self.beats_to_seconds(ticks / ppq)

    def snap_step_seconds(self, division: int) -> float:
        """Step length in seconds for a note-value division (4/8/16)."""
        validate_snap_division(division)
        return self.beats_to_seconds(4.0 / division)

    def rescale_seconds(self, seconds: float, old: TempoMap) -> float:
        """Re-anchor a seconds value from an old tempo to this tempo."""
        return self.beats_to_seconds(old.seconds_to_beats(seconds))
