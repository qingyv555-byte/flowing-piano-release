from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, Signal, Slot

from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, PianoVoiceProfile, ReleaseMode
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.audition import HarmonyAuditionPlayer
from ai_piano_studio.harmony.application import (
    harmony_application_block_reason,
    plan_harmony_candidate_application,
)
from ai_piano_studio.harmony.project_metadata import (
    HarmonyProjectMetadata,
)
from ai_piano_studio.harmony.preview_model import HarmonyPreviewModel
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.suggestion_service import HarmonySuggestionService, measure_ticks
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    ChordCandidate,
    HarmonyAnalysisResult,
    MelodyNoteSnapshot,
    MeasureSuggestion,
)


logger = logging.getLogger(__name__)

ANALYSIS_DEBOUNCE_MS = 400
MANUAL_TAIL_IDLE_MS = 600
DEFAULT_MIN_PITCH = 36
DEFAULT_MAX_PITCH = 84
AUTO_MIN_SCORE = 0.55
AUTO_FEEDBACK_MS = 500
WHOLE_PROJECT_PENDING_TEXT = "正在检测整首…"
WHOLE_PROJECT_FAILURE_TEXT = "整首和弦检测失败，请稍后再试。"
PREVIEW_PENDING_TEXT = "正在听你的旋律…"
PREVIEW_EMPTY_MELODY_TEXT = "先画几个旋律音符，我才能推荐和弦。"
PREVIEW_LOW_CONFIDENCE_TEXT = (
    "旋律里的线索还不够明确。再补几拍，或者点“检测整首并生成”。"
)
PREVIEW_NO_CANDIDATE_TEXT = (
    "这段暂时没有合适的和弦建议。你可以继续写旋律，或检测整首。"
)
WHOLE_PROJECT_LOW_CONFIDENCE_TEXT = (
    "没有添加和弦：旋律里的线索还不够明确。再补几拍后重试。"
)


class _AnalysisSignals(QObject):
    succeeded = Signal(object, int)
    failed = Signal(tuple, int)


class _AnalysisJob(QRunnable):
    def __init__(
        self,
        request: AnalysisRequest,
        service: HarmonySuggestionService,
        serial: int,
    ) -> None:
        super().__init__()
        self._request = request
        self._service = service
        self._serial = serial
        self.signals = _AnalysisSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.analyze(self._request)
        except Exception:
            logger.exception("Harmony analysis worker failed")
            self.signals.failed.emit(self._identity, self._serial)
        else:
            self.signals.succeeded.emit(result, self._serial)

    @property
    def _identity(self) -> tuple[str, int, int, str]:
        return (
            self._request.project_identity,
            self._request.project_revision,
            self._request.generation_id,
            self._request.mode,
        )

    @property
    def request(self) -> AnalysisRequest:
        return self._request


class HarmonySuggestionController(QObject):
    """Coordinates background harmony analysis without exposing editor QObjects to workers."""

    changed = Signal()
    analysisAccepted = Signal(object)
    feedbackRequested = Signal(str)
    temporaryConfirmationRequested = Signal(int)
    clearConfirmationRequested = Signal()
    helpRequested = Signal(str)

    def __init__(
        self,
        editor: EditorController,
        settings: HarmonySettings,
        service: HarmonySuggestionService | None = None,
        preview_model: HarmonyPreviewModel | None = None,
        experience=None,
        playback=None,
        keyboard=None,
        audition_player: HarmonyAuditionPlayer | None = None,
        parent=None,
        piano_profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
        piano_source=None,
    ) -> None:
        super().__init__(parent)
        self.editor = editor
        self.settings = settings
        self.previewModel = preview_model or HarmonyPreviewModel(editor, parent=self)
        self.piano_profile = piano_profile
        self.piano_source = piano_source
        self.auditionPlayer = audition_player or HarmonyAuditionPlayer(
            parent=self,
            profile=piano_profile,
            piano_source=piano_source,
        )
        self._experience = experience
        self._playback = playback
        self._keyboard = keyboard
        self._service = service or HarmonySuggestionService()
        self._mode = settings.mode
        self._revision = 0
        self._generation = 0
        self._draft_index = 0
        self._project_identity = self._current_project_identity()
        self.previewModel.set_project_identity_provider(lambda: self._project_identity)
        self._editor_session_id = editor.projectSessionId
        self._analysis_range = self._analysis_pitch_range()
        self._analysis_input = self._analysis_input_fingerprint()
        self._latest_result: HarmonyAnalysisResult | None = None
        self._analysis_pending = False
        self._whole_project_pending = False
        self._whole_project_serial: int | None = None
        self._harmony_status_text = ""
        self._next_serial = 0
        self._active_serial: int | None = None
        self._jobs: dict[int, _AnalysisJob] = {}
        self._closed = False
        self._completed_frontier_tick = 0
        self._auto_application_cache: set[
            tuple[str, tuple[object, tuple[tuple[object, ...], ...]], int, str]
        ] = set()
        self._last_auto_undo_depth: int | None = None
        self._auto_feedback: list[tuple[int, str, tuple[int, int, int]]] = []
        self._recording_was_active = bool(getattr(keyboard, "recording", False))
        self._recording_frontier_pending = False
        self._playback_position_signal = getattr(playback, "positionChanged", None)
        self._record_time_signal = getattr(keyboard, "recordTimeChanged", None)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(ANALYSIS_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._start_debounced_analysis)
        self._tail_timer = QTimer(self)
        self._tail_timer.setSingleShot(True)
        self._tail_timer.setInterval(MANUAL_TAIL_IDLE_MS)
        self._tail_timer.timeout.connect(self._start_manual_tail_analysis)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.setInterval(AUTO_FEEDBACK_MS)
        self._feedback_timer.timeout.connect(self._flush_auto_feedback)

        editor.stateChanged.connect(self._editor_state_changed)
        editor.currentFileChanged.connect(self._editor_project_changed)
        settings.changed.connect(self._settings_changed)
        if experience is not None:
            experience.changed.connect(self._experience_changed)
        failed = getattr(self.auditionPlayer, "failed", None)
        if failed is not None:
            failed.connect(self.feedbackRequested)
        if playback is not None:
            playback.playingChanged.connect(self._playback_state_changed)
            if self._playback_position_signal is not None:
                self._playback_position_signal.connect(self._cursor_position_changed)
        if keyboard is not None:
            keyboard.recordingChanged.connect(self._recording_state_changed)
            if self._record_time_signal is not None:
                self._record_time_signal.connect(self._record_cursor_changed)

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Property(int, notify=changed)
    def revision(self) -> int:
        return self._revision

    @Property(int, notify=changed)
    def analysisGeneration(self) -> int:
        return self._generation

    @Property(bool, notify=changed)
    def analysisPending(self) -> bool:
        return self._analysis_pending

    @Property(bool, notify=changed)
    def wholeProjectPending(self) -> bool:
        return self._whole_project_pending

    @Property(str, notify=changed)
    def harmonyStatusText(self) -> str:
        return self._harmony_status_text

    @Property(str, notify=changed)
    def previewStatusText(self) -> str:
        if self._whole_project_pending:
            return WHOLE_PROJECT_PENDING_TEXT
        if self._analysis_pending:
            return PREVIEW_PENDING_TEXT
        if self._harmony_status_text:
            return self._harmony_status_text
        if not self.editor.harmony_analysis_notes:
            return PREVIEW_EMPTY_MELODY_TEXT
        if self._latest_result is not None:
            if self._latest_result.key_estimate.low_confidence:
                return PREVIEW_LOW_CONFIDENCE_TEXT
            if self._latest_result.message:
                return self._latest_result.message
        return PREVIEW_NO_CANDIDATE_TEXT

    @Property(object, notify=changed)
    def latestResult(self) -> HarmonyAnalysisResult | None:
        return self._latest_result

    @Property(bool, notify=changed)
    def canAudition(self) -> bool:
        return (
            not self._closed
            and not bool(getattr(self._playback, "playing", False))
            and not bool(getattr(self._keyboard, "recording", False))
        )

    @Property(bool, notify=changed)
    def hasGeneratedHarmony(self) -> bool:
        return bool(self.editor.harmony_metadata.groups)

    @Slot(str)
    def setMode(self, mode: str) -> None:
        """The sole mode-changing entry point exposed to QML."""
        self.settings.setMode(mode)

    @Slot(str)
    def invalidate(self, reason: str) -> None:
        self._invalidate(
            reason,
            clear_result=reason in {"close", "leave_editor", "off", "project_switch"},
        )

    @Slot()
    def leaveEditor(self) -> None:
        self._stop_audition()
        self._invalidate("leave_editor", clear_result=True)

    @Slot()
    def windowDeactivated(self) -> None:
        self._stop_audition()
        self._invalidate("window_deactivated", clear_result=False)

    @Slot()
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_audition = getattr(self.auditionPlayer, "close", None)
        if close_audition is not None:
            try:
                close_audition()
            except Exception:
                logger.exception("Harmony audition close failed")
        else:
            self._stop_audition(ReleaseMode.STOP)
        self._invalidate("close", clear_result=True)
        self.editor.stateChanged.disconnect(self._editor_state_changed)
        self.editor.currentFileChanged.disconnect(self._editor_project_changed)
        self.settings.changed.disconnect(self._settings_changed)
        if self._experience is not None:
            self._experience.changed.disconnect(self._experience_changed)
        if self._playback is not None:
            self._playback.playingChanged.disconnect(self._playback_state_changed)
            if self._playback_position_signal is not None:
                self._playback_position_signal.disconnect(self._cursor_position_changed)
        if self._keyboard is not None:
            self._keyboard.recordingChanged.disconnect(self._recording_state_changed)
            if self._record_time_signal is not None:
                self._record_time_signal.disconnect(self._record_cursor_changed)

    @Slot(int, int)
    def auditionCandidate(self, measure_index: int, candidate_index: int) -> None:
        if bool(getattr(self._playback, "playing", False)):
            self.feedbackRequested.emit("请先暂停播放，再试听和弦")
            return
        if bool(getattr(self._keyboard, "recording", False)):
            self.feedbackRequested.emit("请先停止录制，再试听和弦")
            return
        pitches = self.previewModel.candidate_pitches(measure_index, candidate_index)
        if pitches is None:
            return
        try:
            self.auditionPlayer.audition(
                list(pitches),
                self._candidate_velocity(measure_index, pitches),
                float(getattr(self._playback, "volume", 0.65)),
            )
        except Exception:
            logger.exception("Harmony audition could not start")
            self.feedbackRequested.emit("无法试听和弦")

    @Slot(int, int)
    def selectCandidate(self, measure_index: int, candidate_index: int) -> None:
        """Select a preview candidate and audition it; preview state remains display-only."""
        self.previewModel.selectCandidate(measure_index, candidate_index)
        self.auditionCandidate(measure_index, candidate_index)

    @Slot(int)
    def dismissMeasurePreview(self, measure_index: int) -> None:
        self.previewModel.dismissMeasure(measure_index)

    @Slot(int)
    def requestManualAdd(self, measure_index: int) -> None:
        suggestion = self._suggestion(measure_index)
        if suggestion is None:
            return
        if suggestion.temporary:
            self.temporaryConfirmationRequested.emit(measure_index)
            return
        self.applySuggestion(measure_index)

    @Slot(int)
    def applySuggestion(self, measure_index: int) -> None:
        result = self._latest_result
        suggestion = self._suggestion(measure_index)
        candidate = self._selected_candidate(suggestion)
        if result is None or suggestion is None or candidate is None:
            return
        if not self._application_allowed(result, suggestion, candidate, explicit=False):
            return
        if self._apply_candidate(result, suggestion, candidate, origin="preview-manual"):
            self.feedbackRequested.emit(
                f"已为第 {measure_index + 1} 小节加入 {candidate.chord_symbol} 和弦"
            )

    @Slot(int)
    def confirmTemporarySuggestion(self, measure_index: int) -> None:
        result = self._latest_result
        suggestion = self._suggestion(measure_index)
        candidate = self._selected_candidate(suggestion)
        if result is None or suggestion is None or candidate is None:
            return
        if not self._application_allowed(result, suggestion, candidate, explicit=True):
            return
        if self._apply_candidate(result, suggestion, candidate, origin="preview-manual"):
            self.feedbackRequested.emit(
                f"已应用第 {measure_index + 1} 小节的临时和弦建议"
            )

    @Slot()
    def applyCompletedPart(self) -> None:
        advanced = self._update_completed_frontier(self._current_cursor_tick())
        if (
            self._mode == "auto"
            and self._latest_result is not None
            and self._latest_result.scope == "live"
        ):
            self._apply_auto_result(self._latest_result)
        if advanced and self._mode != "off":
            self.requestAnalysis()

    @Slot()
    def requestClearGeneratedHarmony(self) -> None:
        if self.editor.harmony_metadata.groups:
            self.clearConfirmationRequested.emit()

    @Slot()
    def clearGeneratedHarmony(self) -> None:
        metadata = self.editor.harmony_metadata
        if not metadata.groups:
            return
        state = self._state_without_generated_ids(
            self.editor.state, metadata.generated_note_ids
        )
        if self.editor.apply_harmony_transaction(
            state, HarmonyProjectMetadata((), metadata._unknown_json)
        ):
            self._auto_application_cache.clear()
            self._last_auto_undo_depth = None
            self.changed.emit()
            self.feedbackRequested.emit("已清除和弦生成的音符")

    @Slot(int)
    def regenerateMeasure(self, measure_index: int) -> None:
        result = self._latest_result
        suggestion = self._suggestion(measure_index)
        candidate = self._selected_candidate(suggestion)
        if result is None or suggestion is None or candidate is None:
            self.requestAnalysis()
            return
        if not self._application_allowed(
            result,
            suggestion,
            candidate,
            explicit=True,
            replacing_measure=True,
        ):
            return
        if self._apply_candidate(
            result,
            suggestion,
            candidate,
            origin="preview-manual",
            replace_measure=True,
        ):
            self.feedbackRequested.emit(f"已重新生成第 {measure_index + 1} 小节的和弦")

    @Slot()
    def undoLastAutoHarmony(self) -> None:
        if self._last_auto_undo_depth is None:
            return
        if self.editor._history.undo_depth != self._last_auto_undo_depth:
            self._last_auto_undo_depth = None
            return
        self.editor.undo()
        self._last_auto_undo_depth = None
        self.feedbackRequested.emit("已撤销刚才自动加入的和弦")

    @Slot(int)
    def requestExplanation(self, measure_index: int) -> None:
        self.helpRequested.emit("chord-selection")

    @Slot()
    def requestAnalysis(self) -> None:
        self._request_analysis(include_manual_tail=True)

    @Slot()
    def generateWholeProjectHarmony(self) -> None:
        if self._closed or self._whole_project_pending:
            return
        if not self.editor.harmony_analysis_notes:
            self._set_harmony_status_text("没有添加和弦：工程里还没有旋律音符。")
            self.feedbackRequested.emit("没有添加和弦：工程里还没有旋律音符。")
            return
        self._set_harmony_status_text(WHOLE_PROJECT_PENDING_TEXT)
        try:
            request = self._snapshot_request(
                include_manual_tail=False,
                scope="whole_project",
            )
            self._start_analysis_job(request, whole_project=True)
        except Exception:
            logger.exception("Harmony analysis could not start")
            self._clear_active_pending()
            self._finish_whole_project(
                None,
                status=WHOLE_PROJECT_FAILURE_TEXT,
                emit_feedback=True,
                force=True,
            )

    @Slot()
    def _start_debounced_analysis(self) -> None:
        self._request_analysis(include_manual_tail=False)

    @Slot()
    def _start_manual_tail_analysis(self) -> None:
        if self._mode == "preview":
            self._request_analysis(include_manual_tail=True)

    def _request_analysis(self, *, include_manual_tail: bool) -> None:
        if self._closed or self._mode == "off" or self._whole_project_pending:
            return
        try:
            request = self._snapshot_request(include_manual_tail=include_manual_tail)
            self._start_analysis_job(request, whole_project=False)
        except Exception:
            logger.exception("Harmony analysis could not start")
            self.feedbackRequested.emit("和弦分析失败，请稍后重试")

    def _start_analysis_job(self, request: AnalysisRequest, *, whole_project: bool) -> None:
        self._next_serial += 1
        serial = self._next_serial
        try:
            job = _AnalysisJob(request, self._service, serial)
            job.signals.succeeded.connect(self._analysis_succeeded)
            job.signals.failed.connect(self._analysis_failed)
            self._jobs[serial] = job
            self._activate_serial(serial)
            self._set_analysis_pending(True)
            if whole_project:
                self._whole_project_serial = serial
                self._set_whole_project_pending(True)
            QThreadPool.globalInstance().start(job)
        except Exception:
            self._jobs.pop(serial, None)
            if serial == self._active_serial:
                self._clear_active_pending()
            raise

    def _snapshot_request(
        self,
        *,
        include_manual_tail: bool,
        scope: str = "live",
    ) -> AnalysisRequest:
        """Read editor state only on the controller's main thread.

        Generated harmony note ids are filtered here; workers only receive the
        frozen request built below.
        """
        state = self.editor.state
        tempo = state.tempo
        melody = tuple(
            MelodyNoteSnapshot(
                note.id,
                note.pitch,
                tempo.seconds_to_ticks(note.start_time),
                tempo.seconds_to_ticks(note.duration),
                note.velocity,
            )
            for note in self.editor.harmony_analysis_notes
        )
        latest_end_tick = max(
            (item.start_tick + item.duration_ticks for item in melody), default=0
        )
        ticks_per_measure = measure_ticks(tempo)
        cursor_tick = self._current_cursor_tick()
        if self._mode == "preview":
            current_tick = (
                latest_end_tick
                if include_manual_tail
                else latest_end_tick + ticks_per_measure
            )
        else:
            current_tick = cursor_tick
        if scope == "whole_project":
            occupied: set[int] = set()
            for note in melody:
                if note.duration_ticks <= 0:
                    continue
                first = note.start_tick // ticks_per_measure
                last = max(
                    note.start_tick,
                    note.start_tick + note.duration_ticks - 1,
                ) // ticks_per_measure
                occupied.update(range(first, last + 1))
            completed_measure_indices = frozenset(occupied)
        else:
            completed_measure_indices = frozenset(
                range(max(0, self._completed_frontier_tick // ticks_per_measure))
            )
        min_pitch, max_pitch = self._analysis_range
        return AnalysisRequest(
            project_identity=self._project_identity,
            project_revision=self._revision,
            generation_id=self._generation,
            mode=self._mode,
            melody=melody,
            tempo=tempo,
            completed_measure_indices=completed_measure_indices,
            current_tick=current_tick,
            min_pitch=min_pitch,
            max_pitch=max_pitch,
            scope=scope,
        )

    def _analysis_pitch_range(self) -> tuple[int, int]:
        if getattr(self._experience, "mode", "beginner") == "advanced":
            return 12, 108
        return DEFAULT_MIN_PITCH, DEFAULT_MAX_PITCH

    @Slot()
    def _editor_state_changed(self) -> None:
        if self._closed:
            return
        session_id = self.editor.projectSessionId
        if session_id != self._editor_session_id:
            self._editor_project_changed()
        fingerprint = self._analysis_input_fingerprint()
        if fingerprint == self._analysis_input:
            return
        self._analysis_input = fingerprint
        self._revision += 1
        self._clear_active_pending()
        self.changed.emit()
        self._schedule_analysis_after_edit()

    @Slot()
    def _experience_changed(self) -> None:
        if self._closed:
            return
        analysis_range = self._analysis_pitch_range()
        if analysis_range == self._analysis_range:
            return
        self._analysis_range = analysis_range
        self._revision += 1
        self._clear_active_pending()
        self.changed.emit()
        self._schedule_analysis_after_edit()

    @Slot()
    def _editor_project_changed(self) -> None:
        if self._closed:
            return
        self._stop_audition()
        session_id = self.editor.projectSessionId
        if session_id == self._editor_session_id:
            # Draft promotion, Save As, and rename change the path without replacing
            # the active project. Keep accepted-result identity stable across them.
            self.changed.emit()
            return
        self._editor_session_id = session_id
        self._draft_index += 1
        self._project_identity = self._current_project_identity()
        self._set_harmony_status_text("")
        self._completed_frontier_tick = 0
        self._recording_frontier_pending = False
        self._auto_application_cache.clear()
        self._last_auto_undo_depth = None
        self._analysis_input = self._analysis_input_fingerprint()
        self._invalidate("project_switch", clear_result=True)
        if self._mode != "off":
            self.requestAnalysis()

    @Slot()
    def _settings_changed(self) -> None:
        if self._closed:
            return
        self._mode = self.settings.mode
        if self._mode == "off":
            self._stop_audition()
        if self._mode != "preview":
            self.previewModel.clear()
        self._invalidate(
            "off" if self._mode == "off" else "mode_change",
            clear_result=self._mode == "off",
        )
        if self._mode == "off":
            self._set_harmony_status_text("")
        if self._mode != "off":
            self.requestAnalysis()

    @Slot()
    def _playback_state_changed(self) -> None:
        if bool(getattr(self._playback, "playing", False)):
            self._stop_audition()
        self.changed.emit()

    @Slot()
    def _recording_state_changed(self) -> None:
        recording = bool(getattr(self._keyboard, "recording", False))
        if recording:
            self._stop_audition()
        elif self._recording_was_active:
            advanced = self._update_completed_frontier(self._current_cursor_tick())
            if (advanced or self._recording_frontier_pending) and self._mode != "off":
                self.requestAnalysis()
            self._recording_frontier_pending = False
        self._recording_was_active = recording
        self.changed.emit()

    @Slot()
    def _cursor_position_changed(self) -> None:
        advanced = self._update_completed_frontier(self._current_cursor_tick())
        if advanced and self._mode != "off":
            self.requestAnalysis()

    @Slot()
    def _record_cursor_changed(self) -> None:
        if self._update_completed_frontier(self._current_cursor_tick()):
            self._recording_frontier_pending = True

    def _stop_audition(self, mode: ReleaseMode = ReleaseMode.SAFE) -> None:
        try:
            self.auditionPlayer.stop(mode)
        except TypeError:
            try:
                self.auditionPlayer.stop()
            except Exception:
                logger.exception("Harmony audition legacy stop failed")
        except Exception:
            logger.exception("Harmony audition stop failed")

    def _candidate_velocity(self, measure_index: int, pitches: tuple[int, ...]) -> int:
        if self._latest_result is not None:
            for suggestion in self._latest_result.suggestions:
                if suggestion.measure_index != measure_index:
                    continue
                for candidate in suggestion.candidates:
                    if candidate.pitches == pitches:
                        return candidate.velocity
        return 75

    @Slot(object, int)
    def _analysis_succeeded(self, result: object, serial: int) -> None:
        job = self._jobs.pop(serial, None)
        if not isinstance(result, HarmonyAnalysisResult):
            logger.error("Harmony analysis returned an invalid result")
            self._analysis_failed(job._identity if job is not None else (), serial)
            return
        if not self._result_is_current(result, serial):
            return
        self._active_serial = None
        self._set_analysis_pending(False)
        whole_project = job is not None and job.request.scope == "whole_project"
        self._clear_whole_project_pending(serial)
        if whole_project:
            self._apply_whole_project_result(
                result, job.request.completed_measure_indices
            )
            return
        self._latest_result = result
        if result.mode == "preview":
            self.previewModel.apply_result(result)
        else:
            self.previewModel.clear()
            if job is None or job.request.scope == "live":
                self._apply_auto_result(result)
        self.changed.emit()
        self.analysisAccepted.emit(result)

    @Slot(tuple, int)
    def _analysis_failed(self, identity: tuple, serial: int) -> None:
        self._jobs.pop(serial, None)
        if not self._identity_is_current(identity, serial):
            return
        self._active_serial = None
        self._set_analysis_pending(False)
        whole_project = serial == self._whole_project_serial
        if whole_project:
            self._finish_whole_project(
                serial,
                status=WHOLE_PROJECT_FAILURE_TEXT,
                emit_feedback=True,
            )
        else:
            self.feedbackRequested.emit("和弦分析失败，请稍后重试")
        logger.warning("Harmony analysis failed for current request")

    def _result_is_current(self, result: HarmonyAnalysisResult, serial: int) -> bool:
        return self._identity_is_current(
            (
                result.project_identity,
                result.project_revision,
                result.generation_id,
                result.mode,
            ),
            serial,
        )

    def _identity_is_current(self, identity: tuple, serial: int) -> bool:
        return (
            not self._closed
            and serial == self._active_serial
            and identity
            == (
                self._project_identity,
                self._revision,
                self._generation,
                self._mode,
            )
        )

    def _invalidate(self, reason: str, *, clear_result: bool) -> None:
        self._generation += 1
        self._debounce_timer.stop()
        self._tail_timer.stop()
        self._clear_active_pending()
        if clear_result:
            self._latest_result = None
            self.previewModel.clear()
        logger.debug("Harmony analysis invalidated: %s", reason)
        self.changed.emit()

    def _clear_active_pending(self) -> None:
        serial = self._active_serial
        self._active_serial = None
        self._set_analysis_pending(False)
        self._clear_whole_project_pending(serial)

    def _activate_serial(self, serial: int) -> None:
        self._clear_whole_project_pending(self._active_serial)
        self._active_serial = serial

    def _set_analysis_pending(self, value: bool) -> None:
        if value != self._analysis_pending:
            self._analysis_pending = value
            self.changed.emit()

    def _clear_whole_project_pending(self, serial: int | None) -> None:
        self._finish_whole_project(serial)

    def _finish_whole_project(
        self,
        serial: int | None,
        *,
        status: str | None = None,
        emit_feedback: bool = False,
        force: bool = False,
    ) -> bool:
        if not force and (serial is None or serial != self._whole_project_serial):
            return False
        self._whole_project_serial = None
        self._set_whole_project_pending(False)
        if status is not None:
            self._set_harmony_status_text(status)
        elif self._harmony_status_text == WHOLE_PROJECT_PENDING_TEXT:
            self._set_harmony_status_text("")
        if emit_feedback and status is not None:
            self.feedbackRequested.emit(status)
        return True

    def _set_whole_project_pending(self, value: bool) -> None:
        if value != self._whole_project_pending:
            self._whole_project_pending = value
            self.changed.emit()

    def _set_harmony_status_text(self, value: str) -> None:
        if value != self._harmony_status_text:
            self._harmony_status_text = value
            self.changed.emit()

    def _schedule_analysis_after_edit(self) -> None:
        if self._mode != "off":
            self._debounce_timer.start()
            self._tail_timer.start()

    def _analysis_input_fingerprint(self) -> tuple[object, tuple[tuple[object, ...], ...]]:
        state = self.editor.state
        return (
            state.tempo,
            tuple(
                (
                    note.id,
                    note.pitch,
                    note.start_time,
                    note.duration,
                    note.velocity,
                )
                for note in self.editor.harmony_analysis_notes
            ),
        )

    def _current_cursor_tick(self) -> int:
        tempo = self.editor.state.tempo
        if bool(getattr(self._keyboard, "recording", False)):
            seconds = float(getattr(self._keyboard, "recordCursorSeconds", 0.0))
        else:
            seconds = float(getattr(self._playback, "positionSeconds", 0.0))
        return max(0, tempo.seconds_to_ticks(seconds))

    def _update_completed_frontier(self, cursor_tick: int) -> bool:
        ticks = measure_ticks(self.editor.state.tempo)
        frontier = max(0, int(cursor_tick) // ticks * ticks)
        if frontier <= self._completed_frontier_tick:
            return False
        self._completed_frontier_tick = frontier
        self.changed.emit()
        return True

    def _suggestion(self, measure_index: int) -> MeasureSuggestion | None:
        if self._latest_result is None:
            return None
        return next(
            (
                suggestion
                for suggestion in self._latest_result.suggestions
                if suggestion.measure_index == measure_index
            ),
            None,
        )

    def _selected_candidate(
        self, suggestion: MeasureSuggestion | None
    ) -> ChordCandidate | None:
        if suggestion is None or not suggestion.candidates:
            return None
        selected_key = self.previewModel.selected_candidate_key(suggestion.measure_index)
        if selected_key is None:
            return None
        return next(
            (
                candidate
                for candidate in suggestion.candidates
                if (candidate.chord_symbol, candidate.quality, candidate.pitches)
                == selected_key
            ),
            None,
        )

    def _application_allowed(
        self,
        result: HarmonyAnalysisResult,
        suggestion: MeasureSuggestion,
        candidate: ChordCandidate,
        *,
        explicit: bool,
        replacing_measure: bool = False,
    ) -> bool:
        return (
            self._application_block_reason(
                result,
                suggestion,
                candidate,
                explicit=explicit,
                replacing_measure=replacing_measure,
            )
            is None
        )

    def _application_block_reason(
        self,
        result: HarmonyAnalysisResult,
        suggestion: MeasureSuggestion,
        candidate: ChordCandidate,
        *,
        explicit: bool,
        replacing_measure: bool = False,
        whole_project: bool = False,
    ) -> str | None:
        if result.project_identity != self._project_identity:
            return "stale"
        if result.project_revision != self._revision:
            return "stale"
        if result.generation_id != self._generation or result.mode != self._mode:
            return "stale"
        measure_complete = (
            suggestion.start_tick + suggestion.duration_ticks
            <= self._completed_frontier_tick
        )
        if not explicit and not whole_project and not measure_complete:
            return "incomplete"
        if not explicit and (
            suggestion.temporary
            or result.key_estimate.temporary
            or result.key_estimate.low_confidence
            or result.key_estimate.confidence <= 0
        ):
            return "low_confidence"
        if not explicit and candidate.score.total < AUTO_MIN_SCORE:
            return "weak_candidate"
        min_pitch, max_pitch = self._analysis_range
        return harmony_application_block_reason(
            self.editor.state,
            self.editor.harmony_metadata,
            suggestion,
            candidate,
            min_pitch=min_pitch,
            max_pitch=max_pitch,
            replacing_measure=replacing_measure,
        )

    def _apply_candidate(
        self,
        result: HarmonyAnalysisResult,
        suggestion: MeasureSuggestion,
        candidate: ChordCandidate,
        *,
        origin: str,
        replace_measure: bool = False,
    ) -> bool:
        state, next_metadata = plan_harmony_candidate_application(
            self.editor.state,
            self.editor.harmony_metadata,
            suggestion,
            candidate,
            origin=origin,
            replace_measure=replace_measure,
            layer_id=self.editor.activeLayerId,
        )
        committed = self.editor.apply_harmony_transaction(
            state,
            next_metadata,
            emit_failure_feedback=origin != "whole-project",
        )
        if committed:
            self.changed.emit()
        return committed

    def _apply_whole_project_result(
        self,
        result: HarmonyAnalysisResult,
        scanned_measure_indices: frozenset[int],
    ) -> None:
        applied_indices: list[int] = []
        protected = 0
        low_confidence = 0
        weak_candidate = 0
        other_not_safe = 0
        write_failures = 0
        for suggestion in result.suggestions:
            candidate = suggestion.candidates[0] if suggestion.candidates else None
            if candidate is None:
                other_not_safe += 1
                continue
            reason = self._application_block_reason(
                result,
                suggestion,
                candidate,
                explicit=False,
                whole_project=True,
            )
            if reason is not None:
                if reason in {
                    "existing_generated_harmony",
                    "user_written_harmony",
                    "duplicate_notes",
                }:
                    protected += 1
                elif reason == "low_confidence":
                    low_confidence += 1
                elif reason == "weak_candidate":
                    weak_candidate += 1
                else:
                    other_not_safe += 1
                continue
            if self._apply_candidate(
                result, suggestion, candidate, origin="whole-project"
            ):
                applied_indices.append(suggestion.measure_index)
            else:
                write_failures += 1

        not_safe = low_confidence + weak_candidate + other_not_safe
        if applied_indices:
            measures = "、".join(str(index + 1) for index in applied_indices)
            skipped: list[str] = []
            if protected:
                skipped.append(
                    f"{protected} 个小节已有和弦或手动叠音，已为你保留"
                )
            if not_safe:
                skipped.append(f"{not_safe} 个小节暂时没有足够稳妥的和弦建议")
            if write_failures:
                skipped.append(f"{write_failures} 个小节写入失败，请重试")
            if skipped:
                skipped[0] = f"另外 {skipped[0]}"
                summary = f"已在第 {measures} 小节加入和弦；{'；'.join(skipped)}。"
            else:
                summary = (
                    f"检测了 {len(scanned_measure_indices)} 个有旋律的小节，已在第 {measures} "
                    "小节加入和弦。已有和弦或你自己叠加的音符都没有改动。"
                )
        elif not result.suggestions and (
            result.key_estimate.low_confidence
            or result.key_estimate.tonic_pitch_class is None
        ):
            summary = WHOLE_PROJECT_LOW_CONFIDENCE_TEXT
        elif (
            not result.suggestions
            and result.message
            and result.message.startswith("没有添加和弦：")
        ):
            summary = result.message
        elif write_failures and not protected and not not_safe:
            summary = f"没有添加和弦：有 {write_failures} 个小节写入失败，请重试。"
        elif low_confidence and not protected and low_confidence == not_safe:
            summary = "没有添加和弦：旋律里的线索还不够明确。再补几拍后重试。"
        elif protected and not not_safe and not write_failures:
            summary = "没有添加和弦：合适的位置已经有和弦或你自己叠加的音符，软件不会覆盖它们。"
        elif not_safe and not protected and not write_failures:
            summary = "没有添加和弦：这次没有找到足够稳妥的搭配，软件没有硬塞一个可能不合适的和弦。"
        else:
            skipped = []
            if protected:
                skipped.append(
                    f"{protected} 个小节已有和弦或手动叠音，已为你保留"
                )
            if not_safe:
                skipped.append(f"{not_safe} 个小节暂时没有足够稳妥的和弦建议")
            if write_failures:
                skipped.append(f"{write_failures} 个小节写入失败，请重试")
            if skipped:
                summary = f"没有添加和弦：{'；'.join(skipped)}。"
            else:
                summary = (
                    "没有添加和弦：这次没有找到足够稳妥的搭配，"
                    "软件没有硬塞一个可能不合适的和弦。"
                )
        self._set_harmony_status_text(summary)
        self.feedbackRequested.emit(summary)

    @staticmethod
    def _state_without_generated_ids(state, note_ids: frozenset[str]):
        if not note_ids:
            return state
        selected = state.selected_ids
        state = state.select_ids(note_ids).delete_selected()
        return state.select_ids(selected)

    def _apply_auto_result(self, result: HarmonyAnalysisResult) -> None:
        if (
            result.scope != "live"
            or self._mode != "auto"
            or bool(getattr(self._keyboard, "recording", False))
        ):
            return
        fingerprint = self._analysis_input_fingerprint()
        for suggestion in result.suggestions:
            candidate = suggestion.candidates[0] if suggestion.candidates else None
            if candidate is None:
                continue
            key = (
                self._project_identity,
                fingerprint,
                suggestion.measure_index,
                candidate.chord_symbol,
            )
            if key in self._auto_application_cache:
                continue
            if not self._application_allowed(
                result, suggestion, candidate, explicit=False
            ):
                continue
            if not self._apply_candidate(
                result, suggestion, candidate, origin="auto"
            ):
                continue
            self._auto_application_cache.add(key)
            self._last_auto_undo_depth = self.editor._history.undo_depth
            self._auto_feedback.append(
                (suggestion.measure_index, candidate.chord_symbol, candidate.pitches)
            )
            self._feedback_timer.start()

    @Slot()
    def _flush_auto_feedback(self) -> None:
        applied = tuple(self._auto_feedback)
        self._auto_feedback.clear()
        if not applied:
            return
        if len(applied) > 1:
            self.feedbackRequested.emit(f"已为 {len(applied)} 个小节加入和弦")
            return
        measure_index, symbol, pitches = applied[0]
        self.feedbackRequested.emit(
            f"已为第 {measure_index + 1} 小节加入 {symbol} 和弦"
            f"（{self._solfege(pitches)}）"
        )

    @staticmethod
    def _solfege(pitches: tuple[int, int, int]) -> str:
        names = ("哆", "升哆", "来", "升来", "咪", "发", "升发", "索", "升索", "拉", "升拉", "西")
        return "-".join(names[pitch % 12] for pitch in pitches)

    def _current_project_identity(self) -> str:
        current_path = self.editor.currentFilePath
        if current_path:
            return current_path
        return f"untitled:{id(self.editor)}:{self._draft_index}"
