from __future__ import annotations

from ai_piano_studio.domain.project import ProjectState


class History:
    def __init__(self, initial: ProjectState, *, limit: int = 100) -> None:
        if limit < 1:
            raise ValueError("history limit must be positive")
        self._state = initial
        self._limit = limit
        self._undo: list[ProjectState] = []
        self._redo: list[ProjectState] = []
        self._gesture_origin: ProjectState | None = None

    @property
    def state(self) -> ProjectState:
        return self._state

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    def execute(self, state: ProjectState) -> ProjectState:
        if self._gesture_origin is not None:
            raise RuntimeError("cannot execute during a gesture")
        if state == self._state:
            return self._state
        if state.content == self._state.content:
            self._state = state
            return self._state
        self._push_undo(self._state)
        self._state = state
        self._redo.clear()
        return self._state

    def replace_current(self, state: ProjectState) -> ProjectState:
        """Apply a system-only state transition without changing undo history."""
        if self._gesture_origin is not None:
            raise RuntimeError("cannot replace state during a gesture")
        self._state = state
        return self._state

    def begin_gesture(self) -> None:
        if self._gesture_origin is not None:
            raise RuntimeError("gesture already active")
        self._gesture_origin = self._state

    def preview(self, state: ProjectState) -> ProjectState:
        if self._gesture_origin is None:
            raise RuntimeError("no gesture is active")
        self._state = state
        return self._state

    def commit_gesture(self) -> ProjectState:
        if self._gesture_origin is None:
            raise RuntimeError("no gesture is active")
        origin = self._gesture_origin
        self._gesture_origin = None
        if origin.content != self._state.content:
            self._push_undo(origin)
            self._redo.clear()
        return self._state

    def cancel_gesture(self) -> ProjectState:
        if self._gesture_origin is None:
            return self._state
        self._state = self._gesture_origin
        self._gesture_origin = None
        return self._state

    def undo(self) -> ProjectState:
        if not self._undo:
            return self._state
        self._redo.append(self._state)
        self._state = self._undo.pop()
        return self._state

    def redo(self) -> ProjectState:
        if not self._redo:
            return self._state
        self._push_undo(self._state)
        self._state = self._redo.pop()
        return self._state

    def _push_undo(self, state: ProjectState) -> None:
        self._undo.append(state)
        if len(self._undo) > self._limit:
            del self._undo[0]
