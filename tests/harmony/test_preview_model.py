from __future__ import annotations

from dataclasses import replace

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.note import Note
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.preview_model import HarmonyPreviewModel
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.types import (
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    ScoreBreakdown,
)
from PySide6.QtTest import QSignalSpy
from PySide6.QtCore import QSettings


def _candidate(symbol: str, pitches: tuple[int, int, int], score: float) -> ChordCandidate:
    return ChordCandidate(
        chord_symbol=symbol,
        quality="major",
        diatonic_degree=1,
        pitches=pitches,
        velocity=72,
        score=ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, score),
    )


class _AuditionProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[list[int], int, float]] = []

    def audition(self, pitches, velocity: int, volume: float) -> None:
        self.calls.append((list(pitches), velocity, volume))

    def stop(self) -> None:
        pass


def test_preview_model_exposes_at_most_three_candidates_per_measure() -> None:
    editor = EditorController(ProjectState())
    model = HarmonyPreviewModel(editor)
    result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=2,
                start_tick=3_840,
                duration_ticks=1_920,
                temporary=False,
                candidates=(
                    _candidate("C", (48, 52, 55), 0.9),
                    _candidate("F", (53, 57, 60), 0.8),
                    _candidate("G", (55, 59, 62), 0.7),
                    _candidate("Am", (57, 60, 64), 0.6),
                ),
            ),
        ),
    )

    model.apply_result(result)

    candidates = model.data(model.index(0, 0), model.candidatesRole)
    assert model.rowCount() == 1
    assert [item["chordSymbol"] for item in candidates] == ["C", "F", "G"]


def test_preview_model_exposes_the_required_display_roles() -> None:
    model = HarmonyPreviewModel(EditorController(ProjectState()))

    assert {bytes(name).decode() for name in model.roleNames().values()} == {
        "measureIndex",
        "startSeconds",
        "durationSeconds",
        "complete",
        "temporary",
        "selectedCandidateIndex",
        "chordSymbol",
        "chineseName",
        "notesText",
        "candidates",
        "reason",
    }


def test_preview_model_derives_display_row_and_virtual_notes() -> None:
    editor = EditorController(ProjectState())
    model = HarmonyPreviewModel(editor)
    result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
                message="Cadence supports C major",
            ),
        ),
    )

    model.apply_result(result)

    index = model.index(0, 0)
    assert model.count == 1
    assert model.data(index, model.measureIndexRole) == 1
    assert model.data(index, model.startSecondsRole) == 2.0
    assert model.data(index, model.durationSecondsRole) == 2.0
    assert model.data(index, model.completeRole) is True
    assert model.data(index, model.temporaryRole) is False
    assert model.data(index, model.selectedCandidateIndexRole) == 0
    assert model.data(index, model.chordSymbolRole) == "C"
    assert model.data(index, model.chineseNameRole) == "大三和弦"
    assert model.data(index, model.notesTextRole) == "C3 E3 G3"
    assert model.data(index, model.reasonRole) == "Cadence supports C major"
    assert model.virtualNotes == [
        {
            "measureIndex": 1,
            "startSeconds": 2.0,
            "durationSeconds": 2.0,
            "pitch": 48,
            "temporary": False,
        },
        {
            "measureIndex": 1,
            "startSeconds": 2.0,
            "durationSeconds": 2.0,
            "pitch": 52,
            "temporary": False,
        },
        {
            "measureIndex": 1,
            "startSeconds": 2.0,
            "durationSeconds": 2.0,
            "pitch": 55,
            "temporary": False,
        },
    ]


def test_select_candidate_changes_only_preview_model_data() -> None:
    note = Note.create(id="melody", pitch=60, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    model = HarmonyPreviewModel(editor)
    result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(
                    _candidate("C", (48, 52, 55), 0.9),
                    _candidate("F", (53, 57, 60), 0.8),
                ),
            ),
        ),
    )
    model.apply_result(result)
    content_before = editor.state.content
    history_depth_before = editor._history.undo_depth
    dirty_before = editor.isDirty
    duration_before = editor.estimatedDurationSeconds
    model_count_before = editor.noteModel.rowCount()
    reset_spy = QSignalSpy(model.modelReset)
    changed_spy = QSignalSpy(model.dataChanged)

    model.selectCandidate(1, 1)

    assert reset_spy.count() == 0
    assert changed_spy.count() == 1
    assert model.data(model.index(0, 0), model.selectedCandidateIndexRole) == 1
    assert model.data(model.index(0, 0), model.chordSymbolRole) == "F"
    assert [item["pitch"] for item in model.virtualNotes] == [53, 57, 60]
    assert editor.state.content == content_before
    assert editor._history.undo_depth == history_depth_before
    assert editor.isDirty is dirty_before
    assert editor.estimatedDurationSeconds == duration_before
    assert editor.noteModel.rowCount() == model_count_before


def test_candidate_order_stays_stable_for_near_equal_scores() -> None:
    model = HarmonyPreviewModel(EditorController(ProjectState()))
    first = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(
                    _candidate("C", (48, 52, 55), 0.900),
                    _candidate("F", (53, 57, 60), 0.890),
                ),
            ),
        ),
    )
    replacement = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=5,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(
                    _candidate("F", (53, 57, 60), 0.905),
                    _candidate("C", (48, 52, 55), 0.895),
                ),
            ),
        ),
    )
    model.apply_result(first)

    model.apply_result(replacement)

    candidates = model.data(model.index(0, 0), model.candidatesRole)
    assert [candidate["chordSymbol"] for candidate in candidates] == ["C", "F"]


def test_stable_visible_candidate_is_the_one_auditioned_and_applied(tmp_path) -> None:
    melody = Note.create(id="melody", pitch=72, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((melody,)))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    audition = _AuditionProbe()
    controller = HarmonySuggestionController(
        editor,
        settings,
        audition_player=audition,
    )

    def result(candidates: tuple[ChordCandidate, ...]) -> HarmonyAnalysisResult:
        return HarmonyAnalysisResult(
            project_identity=controller._project_identity,
            project_revision=controller.revision,
            generation_id=controller.analysisGeneration,
            mode="preview",
            key_estimate=KeyEstimate(0, "C", "major", 0.9, False, True),
            suggestions=(MeasureSuggestion(0, 0, 1_920, True, candidates),),
        )

    c_major = _candidate("C", (48, 52, 55), 0.900)
    f_major = _candidate("F", (53, 57, 60), 0.890)
    try:
        controller._active_serial = 1
        controller._analysis_succeeded(result((c_major, f_major)), 1)
        controller.selectCandidate(0, 1)

        controller._active_serial = 2
        controller._analysis_succeeded(
            result(
                (
                    _candidate("F", (53, 57, 60), 0.905),
                    _candidate("C", (48, 52, 55), 0.895),
                ),
            ),
            2,
        )
        controller.selectCandidate(0, 1)

        visible = controller.previewModel.data(
            controller.previewModel.index(0, 0),
            controller.previewModel.chordSymbolRole,
        )
        assert visible == "F"
        assert audition.calls[-1][0] == [53, 57, 60]

        controller.confirmTemporarySuggestion(0)

        applied = editor.harmony_metadata.groups[-1]
        assert applied.chord_symbol == "F"
        assert applied.voicing_pitches == (53, 57, 60)
    finally:
        controller.close()


def test_preview_model_rejects_a_result_from_a_different_project() -> None:
    model = HarmonyPreviewModel(EditorController(ProjectState()))
    current = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
        ),
    )
    stale = HarmonyAnalysisResult(
        project_identity="project-b",
        project_revision=1,
        generation_id=0,
        mode="preview",
        key_estimate=KeyEstimate(5, "F", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=0,
                start_tick=0,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("F", (53, 57, 60), 0.9),),
            ),
        ),
    )
    model.apply_result(current)
    reset_spy = QSignalSpy(model.modelReset)

    model.apply_result(stale)

    assert reset_spy.count() == 0
    assert model.count == 1
    assert model.data(model.index(0, 0), model.chordSymbolRole) == "C"


def test_replacing_existing_measures_emits_data_changed_without_a_reset() -> None:
    model = HarmonyPreviewModel(EditorController(ProjectState()))
    first = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
        ),
    )
    replacement = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=5,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(5, "F", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("F", (53, 57, 60), 0.9),),
            ),
        ),
    )
    model.apply_result(first)
    reset_spy = QSignalSpy(model.modelReset)
    changed_spy = QSignalSpy(model.dataChanged)

    model.apply_result(replacement)

    assert reset_spy.count() == 0
    assert changed_spy.count() == 1
    assert model.data(model.index(0, 0), model.chordSymbolRole) == "F"


def test_clear_only_resets_preview_data_and_leaves_editor_invariants_unchanged() -> None:
    note = Note.create(id="melody", pitch=60, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    model = HarmonyPreviewModel(editor)
    result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
        ),
    )
    model.apply_result(result)
    content_before = editor.state.content
    history_depth_before = editor._history.undo_depth
    dirty_before = editor.isDirty
    duration_before = editor.estimatedDurationSeconds
    active_before = editor.state.active_notes
    note_model_count_before = editor.noteModel.rowCount()
    reset_spy = QSignalSpy(model.modelReset)

    model.clear()

    assert reset_spy.count() == 1
    assert model.count == 0
    assert model.virtualNotes == []
    assert editor.state.content == content_before
    assert editor._history.undo_depth == history_depth_before
    assert editor.isDirty is dirty_before
    assert editor.estimatedDurationSeconds == duration_before
    assert editor.state.active_notes == active_before
    assert editor.noteModel.rowCount() == note_model_count_before


def test_dismiss_measure_removes_only_its_display_only_preview() -> None:
    model = HarmonyPreviewModel(EditorController(ProjectState()))
    result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
            MeasureSuggestion(
                measure_index=2,
                start_tick=3_840,
                duration_ticks=1_920,
                temporary=True,
                candidates=(_candidate("F", (53, 57, 60), 0.9),),
            ),
        ),
    )
    model.apply_result(result)

    model.dismissMeasure(1)

    assert model.count == 1
    assert model.data(model.index(0, 0), model.measureIndexRole) == 2
    assert [item["pitch"] for item in model.virtualNotes] == [53, 57, 60]


def test_accepted_preview_result_is_applied_to_the_preview_model(tmp_path) -> None:
    editor = EditorController(ProjectState())
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    preview_model = HarmonyPreviewModel(editor)
    controller = HarmonySuggestionController(editor, settings, preview_model=preview_model)
    result = HarmonyAnalysisResult(
        project_identity=controller._project_identity,
        project_revision=controller.revision,
        generation_id=controller.analysisGeneration,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
        ),
    )
    controller._active_serial = 1
    try:
        controller._analysis_succeeded(result, 1)

        assert preview_model.count == 1
        assert preview_model.data(preview_model.index(0, 0), preview_model.chordSymbolRole) == "C"
    finally:
        controller.close()


def test_non_preview_result_clears_the_display_only_model() -> None:
    model = HarmonyPreviewModel(EditorController(ProjectState()))
    preview_result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
        ),
    )
    model.apply_result(preview_result)

    model.apply_result(replace(preview_result, mode="auto"))

    assert model.count == 0
    assert model.virtualNotes == []


def test_model_rejects_a_cleared_preview_from_the_prior_editor_project(tmp_path) -> None:
    editor = EditorController(ProjectState())
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    preview_model = HarmonyPreviewModel(editor)
    controller = HarmonySuggestionController(editor, settings, preview_model=preview_model)
    result = HarmonyAnalysisResult(
        project_identity=controller._project_identity,
        project_revision=controller.revision,
        generation_id=controller.analysisGeneration,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(_candidate("C", (48, 52, 55), 0.9),),
            ),
        ),
    )
    try:
        preview_model.apply_result(result)
        assert preview_model.count == 1

        editor.newProject()
        assert preview_model.count == 0

        preview_model.apply_result(result)

        assert preview_model.count == 0
    finally:
        controller.close()


def test_tempo_change_recomputes_tick_based_preview_geometry_without_reset() -> None:
    editor = EditorController(ProjectState())
    model = HarmonyPreviewModel(editor)
    result = HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=4,
        generation_id=2,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.8, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=1,
                start_tick=1_920,
                duration_ticks=1_920,
                temporary=False,
                candidates=(
                    _candidate("C", (48, 52, 55), 0.9),
                    _candidate("F", (53, 57, 60), 0.8),
                ),
            ),
        ),
    )
    model.apply_result(result)
    reset_spy = QSignalSpy(model.modelReset)
    changed_spy = QSignalSpy(model.dataChanged)
    preview_changed_spy = QSignalSpy(model.changed)

    editor.setBpm(60)

    index = model.index(0, 0)
    assert reset_spy.count() == 0
    assert changed_spy.count() == 1
    assert preview_changed_spy.count() == 1
    assert model.data(index, model.startSecondsRole) == 4.0
    assert model.data(index, model.durationSecondsRole) == 4.0
    assert [candidate["chordSymbol"] for candidate in model.data(index, model.candidatesRole)] == [
        "C",
        "F",
    ]
    assert {note["startSeconds"] for note in model.virtualNotes} == {4.0}
    assert {note["durationSeconds"] for note in model.virtualNotes} == {4.0}
