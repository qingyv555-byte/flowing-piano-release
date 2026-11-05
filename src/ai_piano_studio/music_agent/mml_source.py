"""Bounded, data-only Mabinogi MML import; no scripts or loop expansion.

Syntax reference: https://3ml.mabi.tw/en/guide/mml
Supported: MML@ voices ;, a-g/+/#/-, r, n0..95, o0..7, </>,
l and note lengths 1/2/4/8/16/32/64/128, up to three dots, t32..300,
v0..15 and same-pitch ties. Defaults: o4, l4, t120, v8.
Silent v0 notes advance time and are omitted. Unsupported commands fail closed.
Shared tempo is integrated before projecting into the application's single-BPM
tick coordinates, preserving playback seconds even across tempo changes.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from fractions import Fraction

from ai_piano_studio.persistence.midi import MidiImportResult, MidiNoteEvent

MAX_MML_BYTES = 128 * 1024
MAX_VOICES = 16
MAX_NOTES = 30_000  # Includes tied segments and silent notes, bounding parser work.
MAX_DURATION_SECONDS = 3600
_PPQ = 15360
_LENGTHS = frozenset((1, 2, 4, 8, 16, 32, 64, 128))
_PITCHES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


@dataclass(frozen=True)
class _Note:
    start: Fraction
    end: Fraction
    pitch: int
    velocity: int
    track: int


class _Voice:
    def __init__(self, text: str, track: int):
        self.text = text
        self.track = track
        self.pos = 0
        self.octave = 4
        self.length = 4
        self.dots = 0
        self.volume = 8
        self.beat = Fraction(0)
        self.notes: list[_Note] = []
        self.tempos: list[tuple[Fraction, int, int]] = []
        self.count = 0

    def error(self, message: str) -> ValueError:
        return ValueError(f"MML voice {self.track + 1}, offset {self.pos}: {message}")

    def number(self, *, required: bool = True) -> int | None:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in "0123456789":
            self.pos += 1
        if start == self.pos:
            if required:
                raise self.error("expected number")
            return None
        if self.pos - start > 5:
            raise self.error("number exceeds supported bounds")
        return int(self.text[start:self.pos])

    def dot_count(self) -> int:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] == ".":
            self.pos += 1
        count = self.pos - start
        if count > 3:
            raise self.error("at most three dots are supported")
        return count

    def duration(self, *, absolute: bool = False) -> Fraction:
        length = None if absolute else self.number(required=False)
        dots = self.dot_count()
        if length is None:
            length = self.length
            dots = dots or self.dots
        if length not in _LENGTHS:
            raise self.error("unsupported note length")
        return Fraction(4, length) * (2 - Fraction(1, 2**dots))

    def parse(self) -> None:
        tied = False
        previous_note = False
        while self.pos < len(self.text):
            command = self.text[self.pos]
            self.pos += 1
            if command == "&":
                if tied or not previous_note:
                    raise self.error("tie needs an adjacent note")
                tied = True
                continue
            if command in "<>":
                self.octave += 1 if command == ">" else -1
                if not 0 <= self.octave <= 7:
                    raise self.error("octave outside 0..7")
                continue
            if command in "tolv":
                value = self.number()
                if command == "t":
                    if not 32 <= value <= 300:
                        raise self.error("tempo outside 32..300 BPM")
                    self.tempos.append((self.beat, self.track, value))
                elif command == "o":
                    if not 0 <= value <= 7:
                        raise self.error("octave outside 0..7")
                    self.octave = value
                elif command == "v":
                    if not 0 <= value <= 15:
                        raise self.error("volume outside 0..15")
                    self.volume = value
                else:
                    if value not in _LENGTHS:
                        raise self.error("unsupported default length")
                    self.length = value
                    self.dots = self.dot_count()
                continue
            if command not in "abcdefgnr":
                raise self.error(f"unsupported command {command!r}")
            pitch = None
            if command == "n":
                value = self.number()
                if not 0 <= value <= 95:
                    raise self.error("absolute pitch outside 0..95")
                pitch = value + 12
            elif command != "r":
                pitch = 12 * (self.octave + 1) + _PITCHES[command]
                if self.pos < len(self.text) and self.text[self.pos] in "+#-":
                    pitch += -1 if self.text[self.pos] == "-" else 1
                    self.pos += 1
                if not 12 <= pitch <= 107:
                    raise self.error("pitch outside Mabinogi range")
            end = self.beat + self.duration(absolute=command == "n")
            # Even the fastest allowed tempo cannot fit this beat span in an hour.
            if end > MAX_DURATION_SECONDS * 5:
                raise self.error("duration exceeds one hour")
            if pitch is None:
                if tied:
                    raise self.error("cannot tie to a rest")
                previous_note = False
            else:
                self.count += 1
                if self.count > MAX_NOTES:
                    raise self.error("note count exceeds limit")
                if tied:
                    last = self.notes[-1]
                    if pitch != last.pitch or last.end != self.beat:
                        raise self.error("tie pitch mismatch")
                    self.notes[-1] = replace(last, end=end)
                else:
                    velocity = round(self.volume * 127 / 15)
                    self.notes.append(_Note(self.beat, end, pitch, velocity, self.track))
                tied = False
                previous_note = True
            self.beat = end
        if tied:
            raise self.error("unfinished tie")


def parse_mml(text: str) -> MidiImportResult:
    """Parse a complete MML@...; score into timed, separately routed voices.

    Raises ValueError for unsupported syntax, malformed data or resource bounds.
    Tempo changes are flattened into equivalent tick positions at the initial
    (project-supported) BPM, with an explicit ``mml_tempo_flattened`` warning.
    """
    if not isinstance(text, str):
        raise ValueError("MML source must be text")
    if len(text) > MAX_MML_BYTES or len(text.encode("utf-8")) > MAX_MML_BYTES:
        raise ValueError("MML source exceeds 128 KiB")
    compact = "".join(text.split()).lower()
    if not compact.startswith("mml@") or not compact.endswith(";"):
        raise ValueError("expected complete MML@...; score")
    parts = compact[4:-1].split(",")
    if len(parts) > MAX_VOICES:
        raise ValueError("MML source exceeds 16 voices")
    voices = [_Voice(part, index) for index, part in enumerate(parts)]
    count = 0
    for voice in voices:
        voice.parse()
        count += voice.count
        if count > MAX_NOTES:
            raise ValueError("MML note count exceeds limit")

    # Last command at a position in one voice wins; lowest voice index wins ties.
    merged: dict[Fraction, tuple[int, int]] = {}
    for voice in voices:
        for beat, track, bpm in voice.tempos:
            if beat not in merged or track <= merged[beat][0]:
                merged[beat] = (track, bpm)
    points = {Fraction(0): 120}
    points.update({beat: value[1] for beat, value in merged.items()})
    beats = sorted(points)
    bpms = [points[beat] for beat in beats]
    elapsed = [Fraction(0)]
    for index in range(1, len(beats)):
        elapsed.append(elapsed[-1] + (beats[index] - beats[index - 1]) * 60 / bpms[index - 1])

    def seconds(beat: Fraction) -> Fraction:
        index = bisect_right(beats, beat) - 1
        return elapsed[index] + (beat - beats[index]) * 60 / bpms[index]

    if max(seconds(voice.beat) for voice in voices) > MAX_DURATION_SECONDS:
        raise ValueError("MML duration exceeds one hour")
    project_bpm = max(60, min(300, bpms[0]))

    def tick(beat: Fraction) -> int:
        return round(seconds(beat) * project_bpm * _PPQ / 60)

    notes = tuple(sorted((
        MidiNoteEvent(tick(note.start), tick(note.end), note.pitch,
                      note.velocity, 0, note.track)
        for voice in voices for note in voice.notes if note.velocity
    ), key=lambda note: (note.start_tick, note.pitch, note.track, note.end_tick)))
    if not notes:
        raise ValueError("MML source contains no sounding notes")
    flattened = len(set(bpms)) > 1 or project_bpm != bpms[0]
    return MidiImportResult(
        notes=notes, ppq=_PPQ, bpm=project_bpm, beats_per_bar=4, beat_unit=4,
        track_count=len(voices), note_track_count=len({note.track for note in notes}),
        tempo_event_count=len(beats),
        warnings=("mml_tempo_flattened",) if flattened else (),
        track_names=tuple(f"MML Voice {index + 1}" for index in range(len(voices))),
    )
