from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, QObject, Property, Signal, Slot

from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import sustain_snapshot, SoundingNote
from ai_piano_studio.editor.controller import EditorController

if TYPE_CHECKING:
    from ai_piano_studio.audio.controller import PlaybackController


_CONTEXT_PADDING_SECONDS = 0.5
logger = logging.getLogger(__name__)


class SelectionAuditionController(QObject):
    """Play temporary, rebased note snapshots without touching project state."""

    playingChanged = Signal()
    positionChanged = Signal()
    loopSelectionChanged = Signal()
    soloSelectionChanged = Signal()
    modeChanged = Signal()

    def __init__(
        self,
        editor: EditorController,
        playback: PlaybackController,
        player: object,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._playback = playback
        self._player = player
        self._playing = False
        self._position_seconds = 0.0
        self._position_origin_seconds = 0.0
        self._loop_selection = False
        self._mode = "selection"
        self._loop_restart_pending = False
        temporary_playing = getattr(player, "temporaryPlayingChanged", None)
        if temporary_playing is not None:
            temporary_playing.connect(self._on_temporary_playing)
        temporary_position = getattr(player, "temporaryPositionChanged", None)
        if temporary_position is not None:
            temporary_position.connect(self._on_temporary_position)
        temporary_finished = getattr(player, "temporaryFinished", None)
        if temporary_finished is not None:
            temporary_finished.connect(self._on_temporary_finished)
        self._editor.stateChanged.connect(self._on_editor_state_changed)

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(float, notify=positionChanged)
    def positionSeconds(self) -> float:
        return self._position_seconds

    @Property(bool, notify=loopSelectionChanged)
    def loopSelection(self) -> bool:
        return self._loop_selection

    @Property(bool, notify=soloSelectionChanged)
    def soloSelection(self) -> bool:
        return bool(getattr(self._playback, "soloSelection", False))

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._mode

    @Slot()
    def playSelection(self) -> None:
        self._play_mode("selection")

    @Slot()
    def playContext(self) -> None:
        self._play_mode("context")

    @Slot()
    def stop(self) -> None:
        self._loop_restart_pending = False
        stop = getattr(self._playback, "_stop_temporary_audition", None)
        if callable(stop):
            stop(ReleaseMode.STOP)
        else:
            stop = getattr(self._player, "stop_temporary_notes", None)
            if callable(stop):
                try:
                    stop(ReleaseMode.STOP)
                except TypeError:
                    stop()
        self._set_playing(False)
        self._set_position(0.0)

    @Slot(bool)
    def setLoopSelection(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._loop_selection:
            return
        self._loop_selection = value
        self.loopSelectionChanged.emit()

    @Slot(bool)
    def setSoloSelection(self, enabled: bool) -> None:
        setter = getattr(self._playback, "setSoloSelection", None)
        if callable(setter):
            setter(bool(enabled))

    def _play_mode(self, mode: str) -> None:
        snapshot = self._snapshot_for_mode(mode)
        if not snapshot:
            self.stop()
            return
        if self._mode != mode:
            self._mode = mode
            self.modeChanged.emit()
        self._loop_restart_pending = False
        self._position_origin_seconds = self._origin_for_mode(mode)
        self._set_position(self._position_origin_seconds)
        self._start_snapshot(snapshot)

    def _start_snapshot(self, snapshot: tuple[Note, ...]) -> None:
        try:
            starter = getattr(self._playback, "_start_temporary_audition", None)
            if callable(starter):
                starter(snapshot)
            else:
                self._player.play_temporary_notes(snapshot)
        except Exception:
            logger.exception("Selection audition start failed")
            self.stop()
            self._set_playing(False)
            feedback = getattr(self._playback, "feedbackRequested", None)
            if feedback is not None:
                feedback.emit("选中试听失败，请检查音频输出")

    def _snapshot_for_mode(self, mode: str) -> tuple[Note, ...]:
        state = self._editor.state
        selected = tuple(n for n in state.effective_notes() if n.id in state.selected_ids)
        if not selected:
            return ()
        if mode == "selection":
            origin = min(note.start_time for note in selected)
            return _rebase_notes(sustain_snapshot(selected, state.effective_controls()), origin)

        selected_start = min(note.start_time for note in selected)
        selected_end = max(
            note.start_time + note.duration for note in selected
        )
        origin = max(0.0, selected_start - _CONTEXT_PADDING_SECONDS)
        end = selected_end + _CONTEXT_PADDING_SECONDS
        context_notes = tuple(
            replace(note, start_time=max(origin, note.start_time),
                    duration=min(end, note.start_time + note.duration)
                    - max(origin, note.start_time))
            for note in self._editor.state.active_notes
            if note.start_time < end
            and note.start_time + note.duration > origin
        )
        return _rebase_notes(sustain_snapshot(context_notes, state.effective_controls()), origin)

    def _on_temporary_playing(self, playing: bool) -> None:
        self._set_playing(bool(playing))

    def _on_temporary_position(self, position: float) -> None:
        self._set_position(self._position_origin_seconds + max(0.0, float(position)))

    def _on_temporary_finished(self) -> None:
        if not self._loop_selection:
            finish = getattr(self._playback, "_finish_temporary_audition", None)
            if callable(finish):
                finish()
            self._set_playing(False)
            self._set_position(0.0)
            return
        if self._loop_restart_pending:
            return
        snapshot = self._snapshot_for_mode(self._mode)
        if not snapshot:
            self._set_playing(False)
            self._set_position(0.0)
            return
        self._loop_restart_pending = True
        finish = getattr(self._playback, "_finish_temporary_audition", None)
        if callable(finish):
            finish()
        else:
            stop = getattr(self._player, "stop_temporary_notes", None)
            if callable(stop):
                try:
                    stop(ReleaseMode.STOP)
                except TypeError:
                    stop()
        # The old session can still deliver a queued completion edge while it
        # is being stopped. Defer the replacement to the next event turn and
        # coalesce those stale edges into one restart.
        QTimer.singleShot(0, self._restart_loop_snapshot)

    def _restart_loop_snapshot(self) -> None:
        if not self._loop_restart_pending:
            return
        self._loop_restart_pending = False
        if not self._loop_selection:
            self._set_playing(False)
            self._set_position(0.0)
            return
        snapshot = self._snapshot_for_mode(self._mode)
        if not snapshot:
            self._set_playing(False)
            self._set_position(0.0)
            return
        self._position_origin_seconds = self._origin_for_mode(self._mode)
        self._set_position(self._position_origin_seconds)
        self._start_snapshot(snapshot)

    def _on_editor_state_changed(self) -> None:
        # A loop always rebuilds its snapshot at completion.  This keeps
        # moved/edited notes current without mutating the active generation.
        return None

    def _set_playing(self, playing: bool) -> None:
        value = bool(playing)
        if value == self._playing:
            return
        self._playing = value
        self.playingChanged.emit()

    def _set_position(self, position: float) -> None:
        value = max(0.0, float(position))
        if value == self._position_seconds:
            return
        self._position_seconds = value
        self.positionChanged.emit()

    def _origin_for_mode(self, mode: str) -> float:
        selected = self._editor.state.selected_notes
        if not selected:
            return 0.0
        selected_start = min(note.start_time for note in selected)
        if mode == "selection":
            return selected_start
        return max(0.0, selected_start - _CONTEXT_PADDING_SECONDS)


def _rebase_notes(notes: tuple[Note, ...], origin: float) -> tuple[Note, ...]:
    return tuple(
        sorted(
            (
                note.with_changes(start_time=max(0.0, note.start_time - origin))
                if isinstance(note, SoundingNote) else replace(note, start_time=max(0.0, note.start_time - origin))
                for note in notes
            ),
            key=lambda note: (note.start_time, note.id),
        )
    )
