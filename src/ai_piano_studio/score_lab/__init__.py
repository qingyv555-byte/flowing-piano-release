"""Opt-in score search/transcription, isolated from the current editor."""
from .contracts import LabCandidate, LabDraft, LabFailure
from .service import ScoreLabService

__all__ = ['LabCandidate', 'LabDraft', 'LabFailure', 'ScoreLabService']
