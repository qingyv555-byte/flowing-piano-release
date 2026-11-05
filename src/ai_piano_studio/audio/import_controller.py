from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot

from ai_piano_studio.audio.importer import AudioImportSession
from ai_piano_studio.domain.note import Note
from ai_piano_studio.editor.controller import EditorController


logger = logging.getLogger(__name__)


class AudioImportController(QObject):
    """QML bridge for a temporary local analysis result and one-click import."""

    imported = Signal(str)
    feedbackRequested = Signal(str)
    changed = Signal()

    def __init__(
        self,
        editor: EditorController,
        playback: object | None = None,
        parent: QObject | None = None,
        *,
        assistant_summary: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._playback = playback
        self._assistant_summary = assistant_summary
        self._session = AudioImportSession(self)
        self._preview_active = False
        self._analysis_project_session_id: int | None = None
        self._session.statusChanged.connect(self.changed)
        self._session.resultChanged.connect(self.changed)
        self._session.errorChanged.connect(self.changed)
        self._editor.stateChanged.connect(self._on_editor_state_changed)
        # The application may dispose its QML engine without quitting QApplication
        # (embedded callers and repeated windows). Cancel/join before native
        # QObject parent destruction reaches the session's running QThread.
        register_shutdown = getattr(parent, "add_shutdown_callback", None)
        if register_shutdown is not None:
            register_shutdown(self.shutdown)

    @Property(QObject, constant=True)
    def session(self) -> AudioImportSession:
        return self._session

    @Property(bool, notify=changed)
    def previewActive(self) -> bool:
        return self._preview_active

    @Property("QVariantMap", notify=changed)
    def analysisSummary(self) -> dict[str, Any]:
        pitch_range = self._session.pitchRange
        return {
            "durationSeconds": self._session.durationSeconds,
            "tempoEstimate": self._session.tempoEstimate,
            "detectedNotes": len(self._session.detectedNotes),
            "rawDetectedNotes": len(self._session.rawDetectedNotes),
            "range": pitch_range,
            "sampleRate": self._session.sampleRate,
            "channels": self._session.channels,
            "extractionMode": self._session.extractionMode,
        }

    @Slot(str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def startAnalysis(
        self, source: str, sensitivity: str = "standard", mode: str = "auto"
    ) -> bool:
        self.stopPreview()
        started = self._session.start(source, sensitivity, mode)
        self._analysis_project_session_id = (
            self._editor.projectSessionId if started else None
        )
        return started

    @Slot(str, float, float, float, float, int, int, result=bool)
    def setExtractionControls(
        self,
        mode: str = "auto",
        min_note_duration: float = 0.08,
        noise_threshold: float = 0.0,
        quantize_strength: float = 1.0,
        simplify_density: float = 0.5,
        pitch_min: int = 21,
        pitch_max: int = 108,
    ) -> bool:
        return self._session.setExtractionControls(
            mode,
            min_note_duration,
            noise_threshold,
            quantize_strength,
            simplify_density,
            pitch_min,
            pitch_max,
        )

    @Slot()
    def cancel(self) -> None:
        self.stopPreview()
        self._analysis_project_session_id = None
        self._session.cancel()

    @Slot()
    def shutdown(self) -> None:
        """Stop local decoding before the application tears down QML objects."""
        self.stopPreview()
        self._analysis_project_session_id = None
        self._session.shutdown()

    @Slot(result=bool)
    def previewResult(self) -> bool:
        notes = self._candidate_notes()
        if not notes or self._playback is None:
            self.feedbackRequested.emit("没有可试听的识别结果")
            return False
        try:
            self._playback.play_detached_notes(tuple(notes))
            self._preview_active = True
            self.changed.emit()
            return True
        except Exception:
            logger.exception("Audio import preview failed")
            self.feedbackRequested.emit("无法试听识别结果")
            return False

    @Slot()
    def stopPreview(self) -> None:
        if self._preview_active and self._playback is not None:
            try:
                self._playback.stop_detached_preview()
            except Exception:
                logger.exception("Stopping audio import preview failed")
        if self._preview_active:
            self._preview_active = False
            self.changed.emit()

    @Slot(str, float, str, result=bool)
    def importResult(
        self,
        start_mode: str = "project_start",
        start_seconds: float = 0.0,
        tempo_choice: str = "keep",
    ) -> bool:
        if self._analysis_project_session_id != self._editor.projectSessionId:
            self.feedbackRequested.emit("工程已切换，音频分析结果已失效")
            return False
        if self._session.status != "ready":
            self.feedbackRequested.emit("请先完成音频分析")
            return False
        tempo = self._session.tempoEstimate if tempo_choice == "detected" else 0
        try:
            imported = self._editor.importDetectedNotes(
                self._session.detectedNotes,
                start_mode,
                float(start_seconds),
                int(tempo),
            )
        except Exception:
            logger.exception("Audio import controller failed")
            imported = False
        if not imported:
            return False
        self._session.setTargetImportRange(start_mode, float(start_seconds))
        summary = self._summary_text()
        self._session.markImported()
        self._analysis_project_session_id = None
        self.stopPreview()
        if self._assistant_summary is not None:
            self._assistant_summary(summary)
        self.imported.emit(summary)
        self.changed.emit()
        return True

    @Slot()
    def _on_editor_state_changed(self) -> None:
        if self._analysis_project_session_id is None:
            return
        if self._analysis_project_session_id == self._editor.projectSessionId:
            return
        self._analysis_project_session_id = None
        self.stopPreview()
        self._session.cancel()

    def _candidate_notes(self) -> list[Note]:
        notes: list[Note] = []
        for raw in self._session.detectedNotes:
            try:
                notes.append(
                    Note.create(
                        pitch=int(raw["pitch"]),
                        start_time=float(raw["startTime"]),
                        duration=float(raw["duration"]),
                        velocity=int(raw.get("velocity", 75)),
                        instrument="piano",
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return notes

    def _summary_text(self) -> str:
        summary = self.analysisSummary
        pitch_range = summary.get("range") or {}
        range_text = "未知"
        if pitch_range.get("lowestName") and pitch_range.get("highestName"):
            range_text = f"{pitch_range['lowestName']}–{pitch_range['highestName']}"
        return (
            "Audio analysis:\n"
            f"duration: {summary['durationSeconds']:.1f} sec\n"
            f"tempo estimate: {summary['tempoEstimate']}\n"
            f"detected notes: {summary['detectedNotes']}\n"
            f"extraction mode: {summary['extractionMode']}\n"
            f"range: {range_text}"
        )
