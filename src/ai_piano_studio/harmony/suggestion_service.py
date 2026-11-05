from __future__ import annotations

from dataclasses import dataclass, replace
from math import floor
from statistics import median

from ai_piano_studio.domain.tempo import PPQ, TempoMap
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    MelodyNoteSnapshot,
    ScoreBreakdown,
)


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_MAJOR_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
_MINOR_INTERVALS = (0, 2, 3, 5, 7, 8, 10)
_MAJOR_QUALITIES = ("major", "minor", "minor", "major", "major", "minor", "diminished")
_MINOR_QUALITIES = ("minor", "diminished", "major", "minor", "minor", "major", "major")


def measure_ticks(tempo: TempoMap) -> int:
    return round(PPQ * tempo.beats_per_bar * 4 / tempo.beat_unit)


@dataclass(frozen=True, slots=True)
class _KeyOption:
    tonic: int
    mode: str
    intervals: tuple[int, ...]
    score: float


class HarmonySuggestionService:
    def analyze(self, request: AnalysisRequest) -> HarmonyAnalysisResult:
        estimate = self._estimate_key(request)
        suggestions: list[MeasureSuggestion] = []
        previous_best: ChordCandidate | None = None
        previous_measure_index: int | None = None
        if estimate.tonic_pitch_class is not None and (
            not estimate.low_confidence or estimate.temporary
        ):
            for index, temporary in self._measure_indices(request):
                notes = self._notes_in_measure(request, index)
                if not notes:
                    continue
                previous = (
                    previous_best
                    if previous_measure_index is not None
                    and index == previous_measure_index + 1
                    else None
                )
                candidates = self._candidates(request, estimate, index, notes, previous)
                if candidates:
                    suggestions.append(
                        MeasureSuggestion(
                            measure_index=index,
                            start_tick=index * measure_ticks(request.tempo),
                            duration_ticks=measure_ticks(request.tempo),
                            temporary=temporary or estimate.temporary,
                            candidates=candidates,
                        )
                    )
                    previous_best = candidates[0]
                    previous_measure_index = index
                elif request.mode == "preview":
                    suggestions.append(
                        MeasureSuggestion(
                            measure_index=index,
                            start_tick=index * measure_ticks(request.tempo),
                            duration_ticks=measure_ticks(request.tempo),
                            temporary=True,
                            candidates=(),
                            message="当前音区过低，暂未找到清晰的和弦排列",
                        )
                    )

        message = None
        if estimate.low_confidence and not estimate.temporary:
            message = "再弹几拍，我正在判断适合的和弦"
        return HarmonyAnalysisResult(
            project_identity=request.project_identity,
            project_revision=request.project_revision,
            generation_id=request.generation_id,
            mode=request.mode,
            key_estimate=estimate,
            suggestions=tuple(suggestions),
            message=message,
            scope=request.scope,
        )

    def _estimate_key(self, request: AnalysisRequest) -> KeyEstimate:
        notes = tuple(note for note in request.melody if note.duration_ticks > 0)
        if not notes:
            return KeyEstimate(None, None, None, 0.0, True, False)

        ticks = measure_ticks(request.tempo)
        strong_positions = (
            {0, ticks // 2} if request.tempo.beats_per_bar in {4, 6} else {0}
        )
        first_by_measure: dict[int, MelodyNoteSnapshot] = {}
        last_by_measure: dict[int, MelodyNoteSnapshot] = {}
        pitch_class_counts: dict[int, int] = {}
        for item in notes:
            index = max(0, item.start_tick // ticks)
            current_first = first_by_measure.get(index)
            if current_first is None or (item.start_tick, item.note_id) < (
                current_first.start_tick,
                current_first.note_id,
            ):
                first_by_measure[index] = item
            current_last = last_by_measure.get(index)
            if current_last is None or (
                item.start_tick + item.duration_ticks,
                item.note_id,
            ) > (
                current_last.start_tick + current_last.duration_ticks,
                current_last.note_id,
            ):
                last_by_measure[index] = item
            pitch_class = item.pitch % 12
            pitch_class_counts[pitch_class] = pitch_class_counts.get(pitch_class, 0) + 1

        weighted_notes: list[tuple[MelodyNoteSnapshot, float]] = []
        for item in notes:
            index = max(0, item.start_tick // ticks)
            weight = max(0.25, min(item.duration_ticks, 2 * PPQ) / PPQ)
            if item.start_tick - index * ticks in strong_positions:
                weight += 1.5
            if first_by_measure[index].note_id == item.note_id:
                weight += 0.5
            if last_by_measure[index].note_id == item.note_id:
                weight += 0.75
            weight += 0.1 * (pitch_class_counts[item.pitch % 12] - 1)
            weighted_notes.append((item, weight))

        options: list[_KeyOption] = []
        first_pitch_class = min(notes, key=lambda item: (item.start_tick, item.note_id)).pitch % 12
        last_pitch_class = max(
            notes, key=lambda item: (item.start_tick + item.duration_ticks, item.note_id)
        ).pitch % 12
        for tonic in range(12):
            for mode, intervals in (("major", _MAJOR_INTERVALS), ("minor", _MINOR_INTERVALS)):
                scale = {(tonic + interval) % 12 for interval in intervals}
                third = (tonic + intervals[2]) % 12
                fifth = (tonic + intervals[4]) % 12
                score = 0.0
                for item, weight in weighted_notes:
                    pitch_class = item.pitch % 12
                    score += weight if pitch_class in scale else -1.5 * weight
                    if pitch_class == tonic:
                        score += 0.75 * weight
                    elif pitch_class in {third, fifth}:
                        score += 0.25 * weight
                if first_pitch_class == tonic:
                    score += 1.2
                elif first_pitch_class in {third, fifth}:
                    score += 0.3
                if last_pitch_class == tonic:
                    score += 2.4
                elif last_pitch_class in {third, fifth}:
                    score += 0.5
                options.append(_KeyOption(tonic, mode, intervals, score))

        options.sort(key=lambda item: (-item.score, item.tonic, item.mode))
        best, runner_up = options[:2]
        score_range = best.score - options[-1].score
        gap = (best.score - runner_up.score) / score_range if score_range else 0.0
        coverage = max(item.start_tick + item.duration_ticks for item in notes) - min(
            item.start_tick for item in notes
        )
        low_confidence = len(notes) < 3 or coverage < 2 * PPQ or gap < 0.12
        all_in_c_major = all(item.pitch % 12 in {0, 2, 4, 5, 7, 9, 11} for item in notes)
        temporary = low_confidence and request.mode == "preview" and all_in_c_major
        if temporary:
            return KeyEstimate(0, "C", "major", gap, True, True)
        return KeyEstimate(
            best.tonic,
            _NOTE_NAMES[best.tonic],
            best.mode,
            gap,
            low_confidence,
            False,
        )

    @staticmethod
    def _measure_indices(request: AnalysisRequest) -> tuple[tuple[int, bool], ...]:
        indices = [
            (index, False)
            for index in sorted(request.completed_measure_indices)
            if index >= 0
        ]
        if request.mode == "preview" and request.melody:
            ticks = measure_ticks(request.tempo)
            tail = max(0, request.current_tick // ticks)
            if tail not in request.completed_measure_indices and any(
                item.start_tick < (tail + 1) * ticks
                and item.start_tick + item.duration_ticks > tail * ticks
                for item in request.melody
            ):
                indices.append((tail, True))
        return tuple(indices)

    @staticmethod
    def _notes_in_measure(
        request: AnalysisRequest, measure_index: int
    ) -> tuple[MelodyNoteSnapshot, ...]:
        ticks = measure_ticks(request.tempo)
        start = measure_index * ticks
        end = start + ticks
        return tuple(
            item
            for item in request.melody
            if item.start_tick < end and item.start_tick + item.duration_ticks > start
        )

    def _candidates(
        self,
        request: AnalysisRequest,
        estimate: KeyEstimate,
        measure_index: int,
        notes: tuple[MelodyNoteSnapshot, ...],
        previous: ChordCandidate | None,
    ) -> tuple[ChordCandidate, ...]:
        assert estimate.tonic_pitch_class is not None
        intervals = _MAJOR_INTERVALS if estimate.mode == "major" else _MINOR_INTERVALS
        qualities = _MAJOR_QUALITIES if estimate.mode == "major" else _MINOR_QUALITIES
        important_pitches = [
            item.pitch
            for item in notes
            if item.duration_ticks >= PPQ // 2
            or (item.start_tick - measure_index * measure_ticks(request.tempo))
            in {0, measure_ticks(request.tempo) // 2}
        ]
        lowest_important = min(important_pitches or [item.pitch for item in notes])
        velocity = self._suggestion_velocity(request.tempo, measure_index, notes)
        candidates: list[ChordCandidate] = []
        for degree, quality in enumerate(qualities):
            if quality == "diminished":
                continue
            root = (estimate.tonic_pitch_class + intervals[degree]) % 12
            chord_intervals = (0, 4, 7) if quality == "major" else (0, 3, 7)
            pitch_classes = {(root + interval) % 12 for interval in chord_intervals}
            breakdown = self._basic_score(request.tempo, measure_index, notes, pitch_classes)
            symbol = _NOTE_NAMES[root] + ("m" if quality == "minor" else "")
            pitches = self._select_voicing(
                root,
                chord_intervals,
                request.min_pitch,
                request.max_pitch,
                lowest_important,
                previous.pitches if previous is not None else None,
            )
            if pitches is None:
                continue
            safe_distance = lowest_important - max(pitches)
            density_penalty = (
                sum(
                    max(0, 5 - distance)
                    for distance in (
                        pitches[1] - pitches[0],
                        pitches[2] - pitches[1],
                    )
                )
                if pitches[0] < 40
                else 0
            )
            register_safety = max(0.0, min(1.0, safe_distance / 5.0)) * max(
                0.0, 1.0 - density_penalty / 6.0
            )
            breakdown = replace(
                breakdown,
                register_safety=register_safety,
                total=min(1.0, breakdown.total + 0.08 * register_safety),
            )
            if previous is not None:
                previous_classes = {pitch % 12 for pitch in previous.pitches}
                common = len(previous_classes & pitch_classes) / 3.0
                movement = sum(
                    abs(current - prior)
                    for current, prior in zip(
                        sorted(pitches), sorted(previous.pitches), strict=True
                    )
                )
                movement_reward = max(0.0, 1.0 - movement / 36.0)
                cadence = (
                    0.08
                    if degree == 0 and previous.diatonic_degree in {3, 4}
                    else 0.0
                )
                breakdown = replace(
                    breakdown,
                    common_tones=common,
                    voice_movement=movement_reward,
                    cadence=cadence,
                    total=min(
                        1.0,
                        breakdown.total
                        + 0.06 * common
                        + 0.06 * movement_reward
                        + cadence,
                    ),
                )
            candidates.append(
                ChordCandidate(
                    chord_symbol=symbol,
                    quality=quality,
                    diatonic_degree=degree,
                    pitches=pitches,
                    velocity=velocity,
                    score=breakdown,
                )
            )
        candidates.sort(
            key=lambda item: (-item.score.total, -item.score.melody_match, item.diatonic_degree)
        )
        return tuple(candidates[:3])

    @staticmethod
    def _suggestion_velocity(
        tempo: TempoMap,
        measure_index: int,
        notes: tuple[MelodyNoteSnapshot, ...],
    ) -> int:
        ticks = measure_ticks(tempo)
        start = measure_index * ticks
        end = start + ticks
        strong = {0, ticks // 2} if tempo.beats_per_bar in {4, 6} else {0}
        center = float(median(item.velocity for item in notes))
        lower = center - 24.0
        upper = center + 24.0
        weighted_total = 0.0
        total_weight = 0.0
        for item in notes:
            overlap = max(
                0,
                min(end, item.start_tick + item.duration_ticks) - max(start, item.start_tick),
            )
            weight = overlap / PPQ
            if overlap <= PPQ // 4:
                weight *= 0.35
            if (item.start_tick - start) in strong:
                weight *= 1.5
            clamped_velocity = min(upper, max(lower, float(item.velocity)))
            weighted_total += clamped_velocity * weight
            total_weight += weight
        average = weighted_total / total_weight if total_weight else center
        rounded = floor(average * 0.62 + 0.5)
        return min(88, max(36, rounded))

    @staticmethod
    def _basic_score(
        tempo: TempoMap,
        measure_index: int,
        notes: tuple[MelodyNoteSnapshot, ...],
        pitch_classes: set[int],
    ) -> ScoreBreakdown:
        ticks = measure_ticks(tempo)
        start = measure_index * ticks
        end = start + ticks
        strong = {0, ticks // 2} if tempo.beats_per_bar in {4, 6} else {0}
        raw = 0.0
        strong_match = 0.0
        sustained_end_match = 0.0
        short_penalty = 0.0
        conflict_penalty = 0.0
        for item in notes:
            overlap = max(
                0,
                min(end, item.start_tick + item.duration_ticks)
                - max(start, item.start_tick),
            )
            if item.pitch % 12 not in pitch_classes:
                pitch_class = item.pitch % 12
                semitone_conflict = any(
                    min((pitch_class - chord_pitch) % 12, (chord_pitch - pitch_class) % 12) == 1
                    for chord_pitch in pitch_classes
                )
                if semitone_conflict and (
                    (item.start_tick - start) in strong or overlap >= PPQ
                ):
                    conflict_penalty += 0.35
                if overlap <= PPQ // 4:
                    short_penalty += 0.1 * (1.0 - overlap / (2 * PPQ))
                continue
            raw += overlap / PPQ
            if (item.start_tick - start) in strong:
                raw += 1.5
                strong_match += 1.0
            if overlap >= PPQ:
                sustained_end_match += 1.0
            if item.start_tick + item.duration_ticks >= end:
                raw += 1.0
                sustained_end_match += 1.0
        normalized = min(1.0, raw / 10.0)
        short_penalty = min(0.25, short_penalty)
        conflict_penalty = min(0.75, conflict_penalty)
        return ScoreBreakdown(
            strong_beat_match=min(1.0, strong_match / 2.0),
            sustained_end_match=min(1.0, sustained_end_match / 3.0),
            short_non_chord_penalty=short_penalty,
            semitone_conflict_penalty=conflict_penalty,
            common_tones=0.0,
            voice_movement=0.0,
            register_safety=0.0,
            cadence=0.0,
            melody_match=normalized,
            total=max(0.0, normalized - short_penalty - conflict_penalty),
        )

    @staticmethod
    def _select_voicing(
        root: int,
        intervals: tuple[int, int, int],
        minimum: int,
        maximum: int,
        lowest_important: int,
        previous: tuple[int, int, int] | None,
    ) -> tuple[int, int, int] | None:
        pitch_classes = tuple((root + interval) % 12 for interval in intervals)
        pitches_by_class = tuple(
            tuple(pitch for pitch in range(minimum, maximum + 1) if pitch % 12 == pitch_class)
            for pitch_class in pitch_classes
        )
        voicings = {
            tuple(sorted((first, second, third)))
            for first in pitches_by_class[0]
            for second in pitches_by_class[1]
            for third in pitches_by_class[2]
            if len({first, second, third}) == 3
            and max(first, second, third) <= lowest_important - 3
        }
        if not voicings:
            return None

        def rank(voicing: tuple[int, int, int]) -> tuple[float, ...]:
            safety = -float(min(5, lowest_important - voicing[2]))
            movement = 0.0
            common = 0.0
            if previous is not None:
                movement = float(
                    sum(
                        abs(current - prior)
                        for current, prior in zip(voicing, sorted(previous), strict=True)
                    )
                )
                common = float(len(set(voicing) & set(previous)))
            intervals_between_voices = (voicing[1] - voicing[0], voicing[2] - voicing[1])
            low_density = (
                float(sum(max(0, 5 - distance) for distance in intervals_between_voices))
                if voicing[0] < 40
                else 0.0
            )
            center = abs(sum(voicing) / 3.0 - 52.0)
            span = float(voicing[2] - voicing[0])
            return (
                safety,
                movement,
                -common,
                low_density,
                center,
                span,
                *map(float, voicing),
            )

        return min(voicings, key=rank)
