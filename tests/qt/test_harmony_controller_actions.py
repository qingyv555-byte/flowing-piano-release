from __future__ import annotations

from PySide6.QtCore import QSettings

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.suggestion_service import measure_ticks
from ai_piano_studio.harmony.types import (
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    ScoreBreakdown,
)


def _controller(tmp_path) -> HarmonySuggestionController:
    notes = (
        Note.create(id="melody-1", pitch=72, start_time=0.0, duration=0.5),
        Note.create(id="melody-2", pitch=74, start_time=0.75, duration=0.5),
        Note.create(id="melody-3", pitch=76, start_time=1.5, duration=0.25),
    )
    editor = EditorController(ProjectState(notes))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("auto")
    return HarmonySuggestionController(editor, settings)


def _result(controller: HarmonySuggestionController, measure_index: int = 0) -> HarmonyAnalysisResult:
    ticks = measure_ticks(controller.editor.state.tempo)
    score = ScoreBreakdown(0.2, 0.2, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1, 0.8, 0.8)
    candidate = ChordCandidate("C", "major", 1, (48, 52, 55), 70, score)
    return HarmonyAnalysisResult(
        controller._project_identity,
        controller.revision,
        controller.analysisGeneration,
        controller.mode,
        KeyEstimate(0, "C", "major", 0.9, False, False),
        (MeasureSuggestion(measure_index, measure_index * ticks, ticks, False, (candidate,)),),
    )


def test_clear_action_requires_confirmation_before_mutating(tmp_path) -> None:
    controller = _controller(tmp_path)
    confirmations: list[object] = []
    controller.clearConfirmationRequested.connect(lambda: confirmations.append(object()))
    try:
        controller._completed_frontier_tick = measure_ticks(controller.editor.state.tempo)
        controller._apply_auto_result(_result(controller))
        before = (controller.editor.state, controller.editor.harmony_metadata)

        controller.requestClearGeneratedHarmony()

        assert len(confirmations) == 1
        assert (controller.editor.state, controller.editor.harmony_metadata) == before
    finally:
        controller.close()


def test_undo_last_auto_harmony_only_undoes_one_transaction(tmp_path) -> None:
    controller = _controller(tmp_path)
    try:
        ticks = measure_ticks(controller.editor.state.tempo)
        controller._completed_frontier_tick = ticks
        controller._apply_auto_result(_result(controller))
        assert len(controller.editor.harmony_metadata.groups) == 1

        controller.undoLastAutoHarmony()

        assert controller.editor.harmony_metadata.is_empty
        assert len(controller.editor.state.active_notes) == 3
        controller.undoLastAutoHarmony()
        assert len(controller.editor.state.active_notes) == 3
    finally:
        controller.close()


def test_auto_feedback_batches_text_but_keeps_one_history_entry_per_measure(
    tmp_path, qtbot
) -> None:
    controller = _controller(tmp_path)
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        ticks = measure_ticks(controller.editor.state.tempo)
        controller._completed_frontier_tick = 2 * ticks
        first = _result(controller, 0).suggestions[0]
        second = _result(controller, 1).suggestions[0]
        result = HarmonyAnalysisResult(
            controller._project_identity,
            controller.revision,
            controller.analysisGeneration,
            controller.mode,
            KeyEstimate(0, "C", "major", 0.9, False, False),
            (first, second),
        )
        before_depth = controller.editor._history.undo_depth

        controller._apply_auto_result(result)
        qtbot.waitUntil(lambda: bool(feedback), timeout=1_000)

        assert feedback == ["已为 2 个小节加入和弦"]
        assert controller.editor._history.undo_depth == before_depth + 2
        controller.undoLastAutoHarmony()
        assert [group.measure_index for group in controller.editor.harmony_metadata.groups] == [0]
    finally:
        controller.close()


def test_whole_project_status_persists_after_an_empty_preview_result(tmp_path) -> None:
    controller = _controller(tmp_path)
    try:
        empty_result = HarmonyAnalysisResult(
            controller._project_identity,
            controller.revision,
            controller.analysisGeneration,
            controller.mode,
            KeyEstimate(None, None, None, 0.0, True, False),
            (),
            "没有添加和弦：旋律里的线索还不够明确。",
            "whole_project",
        )
        controller._apply_whole_project_result(empty_result, frozenset({0}))
        controller.previewModel.clear()

        assert controller.harmonyStatusText == (
            "没有添加和弦：旋律里的线索还不够明确。再补几拍后重试。"
        )
    finally:
        controller.close()


def test_preview_status_text_covers_empty_pending_no_candidate_and_whole_result(
    tmp_path
) -> None:
    controller = _controller(tmp_path)
    try:
        general_result = HarmonyAnalysisResult(
            controller._project_identity,
            controller.revision,
            controller.analysisGeneration,
            "preview",
            KeyEstimate(0, "C", "major", 0.9, False, False),
            (),
        )
        controller._latest_result = general_result
        assert getattr(controller, "previewStatusText", "") == (
            "这段暂时没有合适的和弦建议。你可以继续写旋律，或检测整首。"
        )

        controller._set_harmony_status_text("整首结果应持续显示")
        assert getattr(controller, "previewStatusText", "") == "整首结果应持续显示"

        controller._set_analysis_pending(True)
        assert getattr(controller, "previewStatusText", "") == "正在听你的旋律…"
        controller._set_analysis_pending(False)

        controller._set_harmony_status_text("")
        controller.setMode("off")
        controller.editor.newProject()
        assert getattr(controller, "previewStatusText", "") == (
            "先画几个旋律音符，我才能推荐和弦。"
        )
    finally:
        controller.close()
