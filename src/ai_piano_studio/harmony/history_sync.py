from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ai_piano_studio.domain.history import History
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _HarmonyHistoryCheckpoint:
    state: ProjectState
    state_undo: tuple[ProjectState, ...]
    state_redo: tuple[ProjectState, ...]
    state_gesture_origin: ProjectState | None
    metadata: HarmonyProjectMetadata
    metadata_undo: tuple[HarmonyProjectMetadata, ...]
    metadata_redo: tuple[HarmonyProjectMetadata, ...]
    metadata_gesture_origin_state: ProjectState | None
    metadata_gesture_origin: HarmonyProjectMetadata | None


class HarmonyHistoryCoordinator:
    """Keep harmony metadata aligned with an existing project history."""

    def __init__(
        self,
        history: History,
        metadata: HarmonyProjectMetadata | None = None,
        *,
        limit: int = 100,
    ) -> None:
        self._history = history
        try:
            if limit < 1:
                raise ValueError("history limit must be positive")
            if history._limit != limit:
                raise ValueError(
                    "coordinator limit must match wrapped history limit"
                )
            initial_metadata = (
                metadata if metadata is not None else HarmonyProjectMetadata.empty()
            )
            self._metadata = initial_metadata.validated(history.state, logger)
        except Exception:
            logger.exception("Harmony history initialization failed")
            raise
        self._limit = limit
        self._undo: list[HarmonyProjectMetadata] = []
        self._redo: list[HarmonyProjectMetadata] = []
        self._gesture_origin_state: ProjectState | None = None
        self._gesture_origin_metadata: HarmonyProjectMetadata | None = None

    @property
    def metadata(self) -> HarmonyProjectMetadata:
        return self._metadata

    def _checkpoint(self) -> _HarmonyHistoryCheckpoint:
        return _HarmonyHistoryCheckpoint(
            state=self._history._state,
            state_undo=tuple(self._history._undo),
            state_redo=tuple(self._history._redo),
            state_gesture_origin=self._history._gesture_origin,
            metadata=self._metadata,
            metadata_undo=tuple(self._undo),
            metadata_redo=tuple(self._redo),
            metadata_gesture_origin_state=self._gesture_origin_state,
            metadata_gesture_origin=self._gesture_origin_metadata,
        )

    def _restore(self, checkpoint: _HarmonyHistoryCheckpoint) -> None:
        self._history._state = checkpoint.state
        self._history._undo = list(checkpoint.state_undo)
        self._history._redo = list(checkpoint.state_redo)
        self._history._gesture_origin = checkpoint.state_gesture_origin
        self._metadata = checkpoint.metadata
        self._undo = list(checkpoint.metadata_undo)
        self._redo = list(checkpoint.metadata_redo)
        self._gesture_origin_state = checkpoint.metadata_gesture_origin_state
        self._gesture_origin_metadata = checkpoint.metadata_gesture_origin

    def execute(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata | None = None,
    ) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            target = self._prepare_metadata(state, metadata)
            content_changed = state.content != self._history.state.content
            next_undo = (
                self._limited((*self._undo, self._metadata))
                if content_changed
                else self._undo
            )
            next_redo = [] if content_changed else self._redo
            result = self._history.execute(state)
            self._metadata = target
            self._undo = next_undo
            self._redo = next_redo
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history execute failed")
            raise

    def replace_current(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata | None = None,
    ) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            if self._gesture_origin_state is not None:
                raise RuntimeError("cannot replace state during a gesture")
            target = self._prepare_metadata(state, metadata)
            result = self._history.replace_current(state)
            self._metadata = target
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history replace failed")
            raise

    def begin_gesture(self) -> None:
        try:
            if self._gesture_origin_state is not None:
                raise RuntimeError("gesture already active")
            self._history.begin_gesture()
            self._gesture_origin_state = self._history.state
            self._gesture_origin_metadata = self._metadata
        except Exception:
            logger.exception("Harmony history gesture begin failed")
            raise

    def preview(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata | None = None,
    ) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            if self._gesture_origin_state is None:
                raise RuntimeError("no gesture is active")
            target = self._prepare_metadata(state, metadata)
            result = self._history.preview(state)
            self._metadata = target
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history gesture preview failed")
            raise

    def commit_gesture(self) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            if self._gesture_origin_state is None or self._gesture_origin_metadata is None:
                raise RuntimeError("no gesture is active")
            content_changed = (
                self._gesture_origin_state.content != self._history.state.content
            )
            next_undo = (
                self._limited((*self._undo, self._gesture_origin_metadata))
                if content_changed
                else self._undo
            )
            next_redo = [] if content_changed else self._redo
            result = self._history.commit_gesture()
            self._undo = next_undo
            self._redo = next_redo
            self._gesture_origin_state = None
            self._gesture_origin_metadata = None
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history gesture commit failed")
            raise

    def cancel_gesture(self) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            if self._gesture_origin_state is None:
                return self._history.state, self._metadata
            origin_metadata = self._gesture_origin_metadata
            if origin_metadata is None:
                raise RuntimeError("harmony gesture metadata is out of sync")
            result = self._history.cancel_gesture()
            self._metadata = origin_metadata
            self._gesture_origin_state = None
            self._gesture_origin_metadata = None
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history gesture cancel failed")
            raise

    def undo(self) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            if self._history.can_undo != bool(self._undo):
                raise RuntimeError("harmony history undo stack is out of sync")
            if not self._undo:
                return self._history.state, self._metadata
            target = self._undo[-1]
            next_undo = self._undo[:-1]
            next_redo = [*self._redo, self._metadata]
            result = self._history.undo()
            self._metadata = target
            self._undo = next_undo
            self._redo = next_redo
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history undo failed")
            raise

    def reset(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata | None = None,
    ) -> tuple[ProjectState, HarmonyProjectMetadata]:
        """Reset both histories while preserving the wrapped ``History`` identity."""
        try:
            initial_metadata = (
                metadata if metadata is not None else HarmonyProjectMetadata.empty()
            )
            target = initial_metadata.validated(state, logger)
            History.__init__(self._history, state, limit=self._limit)
            self._metadata = target
            self._undo = []
            self._redo = []
            self._gesture_origin_state = None
            self._gesture_origin_metadata = None
            return self._history.state, self._metadata
        except Exception:
            logger.exception("Harmony history reset failed")
            raise

    def _prepare_metadata(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata | None,
    ) -> HarmonyProjectMetadata:
        if metadata is not None:
            return metadata.validated(state, logger)
        reconciled = self._metadata.reconciled(state, logger)
        previous_active_ids = {note.id for note in self._history.state.active_notes}
        added_notes = tuple(
            note for note in state.active_notes if note.id not in previous_active_ids
        )
        if not added_notes or not reconciled.groups:
            return reconciled
        owned_ids = {
            note_id
            for group in reconciled.groups
            for note_id in group.generated_note_ids
        }
        manual_notes = tuple(note for note in added_notes if note.id not in owned_ids)
        if not manual_notes:
            return reconciled
        groups = tuple(
            replace(group, detached=True)
            if not group.detached
            and any(
                self._manual_note_conflicts(note, group, state)
                for note in manual_notes
            )
            else group
            for group in reconciled.groups
        )
        return replace(reconciled, groups=groups)

    @staticmethod
    def _manual_note_conflicts(
        note: Note,
        group: HarmonyGroup,
        state: ProjectState,
    ) -> bool:
        note_start = state.tempo.seconds_to_ticks(note.start_time)
        note_end = state.tempo.seconds_to_ticks(note.start_time + note.duration)
        group_end = group.start_tick + group.duration_ticks
        overlaps = note_start < group_end and note_end > group.start_tick
        pitch_classes = {pitch % 12 for pitch in group.voicing_pitches}
        return overlaps and note.pitch % 12 in pitch_classes

    def _limited(
        self, items: tuple[HarmonyProjectMetadata, ...]
    ) -> list[HarmonyProjectMetadata]:
        return list(items[-self._limit :])

    def redo(self) -> tuple[ProjectState, HarmonyProjectMetadata]:
        try:
            if self._history.can_redo != bool(self._redo):
                raise RuntimeError("harmony history redo stack is out of sync")
            if not self._redo:
                return self._history.state, self._metadata
            target = self._redo[-1]
            next_redo = self._redo[:-1]
            next_undo = self._limited((*self._undo, self._metadata))
            result = self._history.redo()
            self._metadata = target
            self._undo = next_undo
            self._redo = next_redo
            return result, self._metadata
        except Exception:
            logger.exception("Harmony history redo failed")
            raise
