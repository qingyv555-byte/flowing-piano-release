"""Deterministic, non-destructive grouping for dense Piano Roll presentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable

from ai_piano_studio.domain.note import Note


class CompositeGroupingMode(StrEnum):
    """Presentation grouping policies supported by the Piano Roll."""

    STRICT = "strict"
    SMART = "smart"


@dataclass(frozen=True, slots=True)
class CompositeGroupingConfig:
    """Centralized thresholds for deriving presentation groups."""

    same_pitch_gap_seconds: float = 0.150
    near_pitch_gap_seconds: float = 0.080
    max_pitch_delta_semitones: int = 1
    min_child_count: int = 2
    max_group_duration_seconds: float = 8.0
    overlap_threshold_seconds: float = 0.0
    micro_fragment_seconds: float = 0.320

    def __post_init__(self) -> None:
        for name in (
            "same_pitch_gap_seconds",
            "near_pitch_gap_seconds",
            "max_group_duration_seconds",
            "overlap_threshold_seconds",
            "micro_fragment_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
            if float(value) < 0:
                raise ValueError(f"{name} must be greater than or equal to 0")

        if self.max_group_duration_seconds <= 0:
            raise ValueError("max_group_duration_seconds must be greater than 0")
        if not isinstance(self.max_pitch_delta_semitones, int) or isinstance(
            self.max_pitch_delta_semitones, bool
        ):
            raise ValueError("max_pitch_delta_semitones must be an integer")
        if self.max_pitch_delta_semitones < 0:
            raise ValueError("max_pitch_delta_semitones must be greater than or equal to 0")
        if not isinstance(self.min_child_count, int) or isinstance(self.min_child_count, bool):
            raise ValueError("min_child_count must be an integer")
        if self.min_child_count < 1:
            raise ValueError("min_child_count must be at least 1")


DEFAULT_COMPOSITE_GROUPING = CompositeGroupingConfig()


@dataclass(frozen=True, slots=True)
class CompositeGroup:
    """Derived range and summary for one or more real Note IDs."""

    group_id: str
    child_note_ids: tuple[str, ...]
    start_time: float
    end_time: float
    representative_pitch: int
    pitch_min: int
    pitch_max: int
    trigger_count: int
    has_pitch_variation: bool
    is_composite: bool


def _compatibility_key(note: Note) -> tuple[object, ...]:
    """Keep a derived group inside one actual layer and MIDI route."""

    return (note.layer_id, note.instrument, note.track, note.channel)


def _note_end(note: Note) -> float:
    return note.start_time + note.duration


def _group_id(notes: tuple[Note, ...]) -> str:
    return "composite:" + ",".join(note.id for note in notes)


def _as_group(notes: tuple[Note, ...], *, min_child_count: int) -> CompositeGroup:
    pitches = tuple(note.pitch for note in notes)
    pitch_min = min(pitches)
    pitch_max = max(pitches)
    return CompositeGroup(
        group_id=_group_id(notes),
        child_note_ids=tuple(note.id for note in notes),
        start_time=min(note.start_time for note in notes),
        end_time=max(_note_end(note) for note in notes),
        representative_pitch=sorted(pitches)[(len(pitches) - 1) // 2],
        pitch_min=pitch_min,
        pitch_max=pitch_max,
        trigger_count=len(notes),
        has_pitch_variation=pitch_min != pitch_max,
        is_composite=len(notes) >= min_child_count,
    )


class CompositeNoteGrouper:
    """Build presentation groups without changing any source Note."""

    def __init__(self) -> None:
        self._run_summaries: dict[int, tuple[tuple[Note, ...], tuple[CompositeGroup, ...]]] = {}
        self._summary_min_child_count = 0
        self._buckets = {}
        self._bucket_policy = None

    def group(
        self,
        notes: Iterable[Note],
        mode: CompositeGroupingMode | str = CompositeGroupingMode.SMART,
        config: CompositeGroupingConfig | None = DEFAULT_COMPOSITE_GROUPING,
    ) -> tuple[CompositeGroup, ...]:
        grouping_mode = CompositeGroupingMode(mode)
        grouping_config = DEFAULT_COMPOSITE_GROUPING if config is None else config
        if not isinstance(grouping_config, CompositeGroupingConfig):
            raise TypeError("config must be a CompositeGroupingConfig")

        active_notes = tuple(note for note in notes if note.status != "deleted")
        for note in active_notes:
            if not isinstance(note, Note):
                raise TypeError("notes must contain Note instances")

        buckets: dict[tuple[object, ...], list[Note]] = {}
        for note in active_notes:
            buckets.setdefault(_compatibility_key(note), []).append(note)

        groups: list[CompositeGroup] = []
        previous = (self._run_summaries if self._summary_min_child_count == grouping_config.min_child_count
                    else {})
        summaries = {}
        policy = (grouping_mode, grouping_config)
        previous_buckets = self._buckets if policy == self._bucket_policy else {}
        next_buckets = {}
        for key, bucket in buckets.items():
            ordered = sorted(bucket, key=lambda note: (note.start_time, note.id))
            runs = self._incremental_runs(ordered, grouping_mode, grouping_config, previous_buckets.get(key))
            next_buckets[key] = (tuple(ordered), runs)
            for run in runs:
                # An unchanged first Note locates the prior run cheaply. Check
                # the complete immutable tuple too: the restarted boundary run
                # may have gained/lost children even when its first note stays.
                # Retaining the tuple prevents object-id reuse in this cache.
                cached = previous.get(id(run[0]))
                run_groups = (cached[1] if cached is not None and (cached[0] is run or cached[0] == run)
                              else tuple(_as_group(candidate, min_child_count=grouping_config.min_child_count)
                                         for candidate in self._emit_candidate(run, grouping_config.min_child_count)))
                summaries[id(run[0])] = (run, run_groups)
                groups.extend(run_groups)

        # Recompute affected greedy runs and reuse unchanged immutable range
        # summaries. Retain at most this score, never a history of scores.
        self._run_summaries = summaries
        self._summary_min_child_count = grouping_config.min_child_count
        self._buckets, self._bucket_policy = next_buckets, policy

        return tuple(sorted(groups, key=lambda group: (group.start_time, group.group_id)))

    @staticmethod
    def _incremental_runs(notes, mode, config, previous):
        """Reuse only unchanged prefixes and suffixes at real greedy reset boundaries.

        Raw runs are retained before min_child_count splits them: a singleton
        display row is not necessarily a grouping boundary. Storage is bounded
        to the immediately previous score, not the undo history.
        """
        old_notes, old_runs = previous if previous is not None else ((), ())
        prefix = 0
        shared_length = min(len(notes), len(old_notes))
        while prefix < shared_length and (notes[prefix] is old_notes[prefix] or notes[prefix] == old_notes[prefix]):
            prefix += 1
        if prefix == len(notes) == len(old_notes):
            return old_runs
        suffix = 0
        while suffix < shared_length - prefix and (notes[-1-suffix] is old_notes[-1-suffix]
                                                   or notes[-1-suffix] == old_notes[-1-suffix]):
            suffix += 1
        boundaries = {}
        position = 0
        output = []
        start = 0
        for index, run in enumerate(old_runs):
            boundaries[position] = index
            end = position + len(run)
            # Restart the preceding run too: an inserted note could join it.
            if end < prefix:
                output.append(run)
                start = end
            position = end
        if start >= len(notes):
            return tuple(output)
        candidate = [notes[start]]
        end = _note_end(notes[start])
        low = high = notes[start].pitch
        duration = notes[start].duration
        for index in range(start + 1, len(notes)):
            note = notes[index]
            if CompositeNoteGrouper._can_append(candidate, note, mode, config, (end, low, high, duration)):
                candidate.append(note)
                end = max(end, _note_end(note))
                low, high = min(low, note.pitch), max(high, note.pitch)
                duration = max(duration, note.duration)
                continue
            output.append(tuple(candidate))
            old_index = index + len(old_notes) - len(notes)
            if index >= len(notes) - suffix and old_index in boundaries:
                output.extend(old_runs[boundaries[old_index]:])
                return tuple(output)
            candidate = [note]
            end, low, high, duration = _note_end(note), note.pitch, note.pitch, note.duration
        output.append(tuple(candidate))
        return tuple(output)

    @staticmethod
    def _group_bucket(
        notes: list[Note],
        mode: CompositeGroupingMode,
        config: CompositeGroupingConfig,
    ) -> list[tuple[Note, ...]]:
        if not notes:
            return []

        output: list[tuple[Note, ...]] = []
        candidate: list[Note] = [notes[0]]
        end, low, high, duration = _note_end(notes[0]), notes[0].pitch, notes[0].pitch, notes[0].duration
        for note in notes[1:]:
            if CompositeNoteGrouper._can_append(candidate, note, mode, config, (end, low, high, duration)):
                candidate.append(note)
                end = max(end, _note_end(note))
                low, high = min(low, note.pitch), max(high, note.pitch)
                duration = max(duration, note.duration)
                continue
            output.extend(CompositeNoteGrouper._emit_candidate(candidate, config.min_child_count))
            candidate = [note]
            end, low, high, duration = _note_end(note), note.pitch, note.pitch, note.duration

        output.extend(CompositeNoteGrouper._emit_candidate(candidate, config.min_child_count))
        return output

    @staticmethod
    def _can_append(
        candidate: list[Note],
        note: Note,
        mode: CompositeGroupingMode,
        config: CompositeGroupingConfig,
        summary: tuple[float, int, int, float] | None = None,
    ) -> bool:
        previous = candidate[-1]
        pitch_delta = abs(note.pitch - previous.pitch)
        if pitch_delta == 0:
            gap_limit = config.same_pitch_gap_seconds
        elif mode is CompositeGroupingMode.SMART and pitch_delta <= config.max_pitch_delta_semitones:
            gap_limit = config.near_pitch_gap_seconds
        else:
            return False

        # A pitch-changing group can contain only micro fragments. Reject the
        # adjacent pair before scanning the existing candidate's summaries.
        if pitch_delta != 0 and (
            previous.duration > config.micro_fragment_seconds
            or note.duration > config.micro_fragment_seconds
        ):
            return False

        gap = max(0.0, note.start_time - _note_end(previous))
        overlaps = note.start_time <= _note_end(previous) + config.overlap_threshold_seconds
        if not overlaps and gap > gap_limit:
            return False

        if summary is None:
            summary = (max(_note_end(item) for item in candidate),
                       min(item.pitch for item in candidate), max(item.pitch for item in candidate),
                       max(item.duration for item in candidate))
        candidate_end, pitch_low, pitch_high, longest_duration = summary
        if max(candidate_end, _note_end(note)) - candidate[0].start_time > config.max_group_duration_seconds:
            return False

        candidate_has_pitch_variation = pitch_low != pitch_high
        if pitch_delta != 0:
            if max(pitch_high, note.pitch) - min(pitch_low, note.pitch) > config.max_pitch_delta_semitones:
                return False
            candidate_has_pitch_variation = True
        if candidate_has_pitch_variation and max(longest_duration, note.duration) > config.micro_fragment_seconds:
            return False
        return True

    @staticmethod
    def _emit_candidate(candidate: list[Note], min_child_count: int) -> list[tuple[Note, ...]]:
        if len(candidate) >= min_child_count:
            return [tuple(candidate)]
        return [(note,) for note in candidate]
