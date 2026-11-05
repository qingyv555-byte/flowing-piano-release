from __future__ import annotations

from dataclasses import dataclass

from ai_piano_studio.domain.tempo import TempoMap


@dataclass(frozen=True, slots=True)
class MelodyNoteSnapshot:
    note_id: str
    pitch: int
    start_tick: int
    duration_ticks: int
    velocity: int


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    project_identity: str
    project_revision: int
    generation_id: int
    mode: str
    melody: tuple[MelodyNoteSnapshot, ...]
    tempo: TempoMap
    completed_measure_indices: frozenset[int]
    current_tick: int
    min_pitch: int
    max_pitch: int
    scope: str = "live"


@dataclass(frozen=True, slots=True)
class KeyEstimate:
    tonic_pitch_class: int | None
    tonic_name: str | None
    mode: str | None
    confidence: float
    low_confidence: bool
    temporary: bool


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    strong_beat_match: float
    sustained_end_match: float
    short_non_chord_penalty: float
    semitone_conflict_penalty: float
    common_tones: float
    voice_movement: float
    register_safety: float
    cadence: float
    melody_match: float
    total: float


@dataclass(frozen=True, slots=True)
class ChordCandidate:
    chord_symbol: str
    quality: str
    diatonic_degree: int
    pitches: tuple[int, int, int]
    velocity: int
    score: ScoreBreakdown


@dataclass(frozen=True, slots=True)
class MeasureSuggestion:
    measure_index: int
    start_tick: int
    duration_ticks: int
    temporary: bool
    candidates: tuple[ChordCandidate, ...]
    message: str | None = None


@dataclass(frozen=True, slots=True)
class HarmonyAnalysisResult:
    project_identity: str
    project_revision: int
    generation_id: int
    mode: str
    key_estimate: KeyEstimate
    suggestions: tuple[MeasureSuggestion, ...]
    message: str | None = None
    scope: str = "live"
