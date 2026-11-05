from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QSettings, Signal

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.application import (
    harmony_application_block_reason,
    plan_harmony_candidate_application,
)
from ai_piano_studio.harmony.controller import _AnalysisJob, HarmonySuggestionController
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
    NoteSignature,
)
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.suggestion_service import measure_ticks
from ai_piano_studio.harmony.types import (
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    ScoreBreakdown,
)


class FakePlayback(QObject):
    playingChanged = Signal()
    positionChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.playing = False
        self.positionSeconds = 0.0
        self.volume = 0.8


class FakeKeyboard(QObject):
    recordingChanged = Signal()
    recordTimeChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.recording = False
        self.recordCursorSeconds = 0.0


def _score(total: float = 0.8) -> ScoreBreakdown:
    return ScoreBreakdown(0.2, 0.2, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1, 0.8, total)


def _candidate(*, pitches: tuple[int, int, int] = (48, 52, 55), total: float = 0.8) -> ChordCandidate:
    return ChordCandidate("C", "major", 1, pitches, 70, _score(total))


def test_candidate_application_plan_preserves_existing_note_and_metadata_semantics() -> None:
    """Break caught: a second Harmony application path generates different notes or metadata."""
    state = ProjectState()
    candidate = _candidate(pitches=(48, 52, 55))
    suggestion = MeasureSuggestion(0, 0, measure_ticks(state.tempo), False, (candidate,))

    next_state, next_metadata = plan_harmony_candidate_application(
        state,
        HarmonyProjectMetadata.empty(),
        suggestion,
        candidate,
        origin="preview-manual",
    )

    generated = next_state.active_notes
    assert [note.pitch for note in generated] == [48, 52, 55]
    assert {note.start_time for note in generated} == {0.0}
    assert {note.duration for note in generated} == {2.0}
    assert {note.velocity for note in generated} == {70}
    assert next_metadata.groups[0].generated_note_ids == tuple(note.id for note in generated)
    assert next_metadata.groups[0].voicing_pitches == (48, 52, 55)
    assert next_metadata.groups[0].application_origin == "preview-manual"
    assert next_metadata.groups[0].replace_policy == "explicit-only"


def test_shared_harmony_safety_gate_rejects_a_second_group_in_the_same_measure() -> None:
    """Break caught: an alternate caller bypasses the existing Harmony collision guard."""
    state = ProjectState()
    candidate = _candidate()
    suggestion = MeasureSuggestion(0, 0, measure_ticks(state.tempo), False, (candidate,))
    generated_state, generated_metadata = plan_harmony_candidate_application(
        state,
        HarmonyProjectMetadata.empty(),
        suggestion,
        candidate,
        origin="preview-manual",
    )

    reason = harmony_application_block_reason(
        generated_state,
        generated_metadata,
        suggestion,
        candidate,
        min_pitch=36,
        max_pitch=84,
    )

    assert reason == "existing_generated_harmony"


def _result(
    controller: HarmonySuggestionController,
    *,
    suggestions: tuple[MeasureSuggestion, ...] | None = None,
    candidate: ChordCandidate | None = None,
    temporary: bool = False,
    low_confidence: bool = False,
    confidence: float = 0.9,
    revision: int | None = None,
    identity: str | None = None,
    scope: str = "live",
) -> HarmonyAnalysisResult:
    ticks = measure_ticks(controller.editor.state.tempo)
    if suggestions is None:
        suggestions = (
            MeasureSuggestion(0, 0, ticks, temporary, (candidate or _candidate(),)),
        )
    return HarmonyAnalysisResult(
        project_identity=(
            controller._project_identity if identity is None else identity
        ),
        project_revision=controller.revision if revision is None else revision,
        generation_id=controller.analysisGeneration,
        mode=controller.mode,
        key_estimate=KeyEstimate(0, "C", "major", confidence, low_confidence, temporary),
        suggestions=suggestions,
        scope=scope,
    )


def _controller(
    tmp_path, *, mode: str = "auto", experience_mode: str = "beginner"
) -> tuple[HarmonySuggestionController, FakePlayback, FakeKeyboard]:
    melody = (
        Note.create(id="melody-1", pitch=72, start_time=0.0, duration=0.5),
        Note.create(id="melody-2", pitch=74, start_time=0.75, duration=0.5),
        Note.create(id="melody-3", pitch=76, start_time=1.5, duration=0.25),
    )
    editor = EditorController(ProjectState(melody))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode(mode)
    experience = ExperienceSettings(
        settings=QSettings(str(tmp_path / "experience.ini"), QSettings.Format.IniFormat)
    )
    experience.setMode(experience_mode)
    playback = FakePlayback()
    keyboard = FakeKeyboard()
    controller = HarmonySuggestionController(
        editor,
        settings,
        experience=experience,
        playback=playback,
        keyboard=keyboard,
    )
    return controller, playback, keyboard


def _accept(controller: HarmonySuggestionController, result: HarmonyAnalysisResult) -> None:
    controller._active_serial = 41
    controller._analysis_succeeded(result, 41)


def _advance_one_measure(controller: HarmonySuggestionController, playback: FakePlayback) -> None:
    ticks = measure_ticks(controller.editor.state.tempo)
    playback.positionSeconds = controller.editor.state.tempo.ticks_to_seconds(ticks)
    playback.positionChanged.emit()


def _managed_group(
    editor: EditorController, *, detached: bool, measure_index: int = 0
) -> None:
    start_time = 2.0 * measure_index
    generated = tuple(
        Note.create(
            id=f"old-{measure_index}-{pitch}",
            pitch=pitch,
            start_time=start_time,
            duration=2.0,
            velocity=68,
        )
        for pitch in (48, 52, 55)
    )
    state = editor.state
    for note in generated:
        state = state.add_note(note, select=False)
    group = HarmonyGroup(
        group_id="existing-group",
        measure_index=measure_index,
        start_tick=measure_index * measure_ticks(state.tempo),
        duration_ticks=measure_ticks(state.tempo),
        chord_symbol="C",
        voicing_pitches=(48, 52, 55),
        generated_note_ids=tuple(note.id for note in generated),
        original_signatures={note.id: NoteSignature.from_note(note) for note in generated},
        detached=detached,
        application_origin="auto",
        replace_policy="automatic",
    )
    editor._execute_harmony_transaction(
        state, HarmonyProjectMetadata((*editor.harmony_metadata.groups, group))
    )


def _deliver_whole_project_result(
    controller: HarmonySuggestionController,
    result: HarmonyAnalysisResult,
    scanned_measure_indices: frozenset[int],
) -> None:
    request = replace(
        controller._snapshot_request(include_manual_tail=False, scope="whole_project"),
        completed_measure_indices=scanned_measure_indices,
    )
    serial = 41
    controller._jobs[serial] = _AnalysisJob(request, controller._service, serial)
    controller._active_serial = serial
    controller._whole_project_serial = serial
    controller._whole_project_pending = True
    controller._analysis_succeeded(result, serial)


def _harmony_snapshot(controller: HarmonySuggestionController) -> tuple[object, ...]:
    return (
        controller.editor.state,
        controller.editor.harmony_metadata,
        tuple(controller.editor._history._undo),
        tuple(controller.editor._history._redo),
        tuple(controller.editor._harmony_history._undo),
        tuple(controller.editor._harmony_history._redo),
    )


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    (
        ("stale", "stale"),
        ("incomplete", "incomplete"),
        ("low_confidence", "low_confidence"),
        ("weak_candidate", "weak_candidate"),
        ("unsafe_voicing", "unsafe_voicing"),
        ("existing_generated_harmony", "existing_generated_harmony"),
        ("user_written_harmony", "user_written_harmony"),
        ("duplicate_notes", "duplicate_notes"),
    ),
)
def test_application_block_reason_preserves_each_safety_gate(
    tmp_path, case: str, expected_reason: str
) -> None:
    """Changing the named safety branch to acceptance must fail this case."""
    controller, playback, _keyboard = _controller(tmp_path)
    try:
        candidate = _candidate()
        result_options: dict[str, object] = {}
        if case != "incomplete":
            _advance_one_measure(controller, playback)
        if case == "stale":
            result_options["revision"] = controller.revision - 1
        elif case == "low_confidence":
            result_options.update(low_confidence=True, confidence=0.2)
        elif case == "weak_candidate":
            candidate = _candidate(total=0.54)
        elif case == "unsafe_voicing":
            candidate = _candidate(pitches=(12, 16, 19))
        elif case == "existing_generated_harmony":
            _managed_group(controller.editor, detached=True)
        elif case == "user_written_harmony":
            for note in (
                Note.create(id="user-c", pitch=60, start_time=0.1, duration=0.8),
                Note.create(id="user-e", pitch=64, start_time=0.2, duration=0.8),
            ):
                controller.editor._execute(controller.editor.state.add_note(note, select=False))
        elif case == "duplicate_notes":
            ticks = measure_ticks(controller.editor.state.tempo)
            controller._completed_frontier_tick = 2 * ticks
            result_options["suggestions"] = (
                MeasureSuggestion(1, ticks, ticks, False, (candidate,)),
            )
            duplicate = Note.create(id="duplicate-c", pitch=48, start_time=2.0, duration=2.0)
            controller.editor._execute(controller.editor.state.add_note(duplicate, select=False))
        result = _result(controller, candidate=candidate, **result_options)

        assert (
            controller._application_block_reason(
                result, result.suggestions[0], candidate, explicit=False
            )
            == expected_reason
        )
    finally:
        controller.close()


@pytest.mark.parametrize(
    ("candidate", "result_options", "expected_reason"),
    (
        (_candidate(), {}, None),
        (_candidate(), {"low_confidence": True, "confidence": 0.2}, "low_confidence"),
        (_candidate(total=0.54), {}, "weak_candidate"),
        (_candidate(pitches=(12, 16, 19)), {}, "unsafe_voicing"),
    ),
)
def test_whole_project_bypasses_only_the_live_frontier_gate(
    tmp_path,
    candidate: ChordCandidate,
    result_options: dict[str, object],
    expected_reason: str | None,
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path)
    try:
        result = _result(
            controller, candidate=candidate, scope="whole_project", **result_options
        )

        assert (
            controller._application_block_reason(
                result,
                result.suggestions[0],
                candidate,
                explicit=False,
                whole_project=True,
            )
            == expected_reason
        )
    finally:
        controller.close()


def test_whole_project_applies_safe_measures_and_preserves_protected_ones(
    tmp_path,
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        ticks = measure_ticks(controller.editor.state.tempo)
        _managed_group(controller.editor, detached=False, measure_index=1)
        _managed_group(controller.editor, detached=True, measure_index=2)
        for note in (
            Note.create(id="manual-3-c", pitch=60, start_time=6.1, duration=0.8),
            Note.create(id="manual-3-e", pitch=64, start_time=6.2, duration=0.8),
        ):
            controller.editor._execute(controller.editor.state.add_note(note, select=False))
        existing_groups = controller.editor.harmony_metadata.groups
        manual_ids = {"manual-3-c", "manual-3-e"}
        candidate = _candidate()
        result = _result(
            controller,
            scope="whole_project",
            suggestions=tuple(
                MeasureSuggestion(index, index * ticks, ticks, False, (candidate,))
                for index in range(5)
            ),
        )
        before_depth = controller.editor._history.undo_depth

        _deliver_whole_project_result(controller, result, frozenset(range(5)))

        groups = controller.editor.harmony_metadata.groups
        assert [group.measure_index for group in groups] == [1, 2, 0, 4]
        assert groups[:2] == existing_groups
        assert [group.application_origin for group in groups[2:]] == [
            "whole-project",
            "whole-project",
        ]
        assert manual_ids <= {note.id for note in controller.editor.state.active_notes}
        assert controller.editor._history.undo_depth == before_depth + 2
        assert "第 1、5 小节" in controller.harmonyStatusText
        assert "已为你保留" in controller.harmonyStatusText
        assert feedback == [controller.harmonyStatusText]

        controller.editor.undo()
        assert [group.measure_index for group in controller.editor.harmony_metadata.groups] == [1, 2, 0]
        controller.editor.undo()
        assert controller.editor.harmony_metadata.groups == existing_groups
        controller.editor.redo()
        controller.editor.redo()
        assert [group.application_origin for group in controller.editor.harmony_metadata.groups[2:]] == [
            "whole-project",
            "whole-project",
        ]
    finally:
        controller.close()


@pytest.mark.parametrize(
    ("candidate", "result_options", "expected_status"),
    (
        (
            _candidate(),
            {"low_confidence": True, "confidence": 0.2},
            "没有添加和弦：旋律里的线索还不够明确。再补几拍后重试。",
        ),
        (
            _candidate(total=0.54),
            {},
            "没有添加和弦：这次没有找到足够稳妥的搭配，软件没有硬塞一个可能不合适的和弦。",
        ),
    ),
)
def test_whole_project_reports_the_primary_zero_add_reason(
    tmp_path,
    candidate: ChordCandidate,
    result_options: dict[str, object],
    expected_status: str,
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        result = _result(
            controller,
            candidate=candidate,
            scope="whole_project",
            **result_options,
        )
        before = (controller.editor.state, controller.editor.harmony_metadata)

        _deliver_whole_project_result(controller, result, frozenset({0}))

        assert (controller.editor.state, controller.editor.harmony_metadata) == before
        assert controller.harmonyStatusText == expected_status
        assert feedback == [expected_status]
    finally:
        controller.close()


def test_invalidated_whole_project_result_cannot_add_notes(tmp_path) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    try:
        result = _result(controller, scope="whole_project")
        controller.editor._execute(
            controller.editor.state.add_note(
                Note.create(id="edit", pitch=64, start_time=3.0, duration=0.5),
                select=False,
            )
        )
        before = (controller.editor.state, controller.editor.harmony_metadata)

        _deliver_whole_project_result(controller, result, frozenset({0}))

        assert (controller.editor.state, controller.editor.harmony_metadata) == before
    finally:
        controller.close()


@pytest.mark.parametrize("stale_field", ("identity", "revision"))
def test_stale_whole_project_identity_or_revision_writes_nothing(
    tmp_path, stale_field: str
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        options: dict[str, object] = {}
        if stale_field == "identity":
            options["identity"] = "different-project"
        else:
            options["revision"] = controller.revision - 1
        result = _result(controller, scope="whole_project", **options)
        before = _harmony_snapshot(controller)

        _deliver_whole_project_result(controller, result, frozenset({0}))

        assert _harmony_snapshot(controller) == before
        assert controller.harmonyStatusText == ""
        assert feedback == []
    finally:
        controller.close()


@pytest.mark.parametrize(
    ("case", "expected_status"),
    (
        (
            "existing_generated_harmony",
            "没有添加和弦：合适的位置已经有和弦或你自己叠加的音符，软件不会覆盖它们。",
        ),
        (
            "detached_harmony",
            "没有添加和弦：合适的位置已经有和弦或你自己叠加的音符，软件不会覆盖它们。",
        ),
        (
            "manual_overlap",
            "没有添加和弦：合适的位置已经有和弦或你自己叠加的音符，软件不会覆盖它们。",
        ),
        (
            "duplicate_notes",
            "没有添加和弦：合适的位置已经有和弦或你自己叠加的音符，软件不会覆盖它们。",
        ),
        (
            "low_confidence",
            "没有添加和弦：旋律里的线索还不够明确。再补几拍后重试。",
        ),
        (
            "weak_candidate",
            "没有添加和弦：这次没有找到足够稳妥的搭配，软件没有硬塞一个可能不合适的和弦。",
        ),
        (
            "unsafe_voicing",
            "没有添加和弦：这次没有找到足够稳妥的搭配，软件没有硬塞一个可能不合适的和弦。",
        ),
        (
            "missing_candidate",
            "没有添加和弦：这次没有找到足够稳妥的搭配，软件没有硬塞一个可能不合适的和弦。",
        ),
        (
            "failed_transaction",
            "没有添加和弦：有 1 个小节写入失败，请重试。",
        ),
    ),
)
def test_whole_project_safety_skip_preserves_project_and_reports_one_result(
    tmp_path,
    monkeypatch,
    case: str,
    expected_status: str,
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        ticks = measure_ticks(controller.editor.state.tempo)
        candidate = _candidate()
        suggestions: tuple[MeasureSuggestion, ...] | None = None
        result_options: dict[str, object] = {}
        scanned = frozenset({0})
        if case in {"existing_generated_harmony", "detached_harmony"}:
            _managed_group(
                controller.editor,
                detached=case == "detached_harmony",
            )
        elif case == "manual_overlap":
            for note in (
                Note.create(id="manual-c", pitch=60, start_time=0.1, duration=0.8),
                Note.create(id="manual-e", pitch=64, start_time=0.2, duration=0.8),
            ):
                controller.editor._execute(
                    controller.editor.state.add_note(note, select=False)
                )
        elif case == "duplicate_notes":
            suggestions = (
                MeasureSuggestion(1, ticks, ticks, False, (candidate,)),
            )
            scanned = frozenset({1})
            controller.editor._execute(
                controller.editor.state.add_note(
                    Note.create(
                        id="duplicate-c",
                        pitch=48,
                        start_time=2.0,
                        duration=2.0,
                    ),
                    select=False,
                )
            )
        elif case == "low_confidence":
            result_options.update(low_confidence=True, confidence=0.2)
        elif case == "weak_candidate":
            candidate = _candidate(total=0.54)
        elif case == "unsafe_voicing":
            candidate = _candidate(pitches=(12, 16, 19))
        elif case == "missing_candidate":
            suggestions = (MeasureSuggestion(0, 0, ticks, False, ()),)
        elif case == "failed_transaction":
            def fail_refresh(**_kwargs) -> None:
                raise OSError("refresh failed")

            monkeypatch.setattr(
                controller.editor,
                "_refresh",
                fail_refresh,
            )
        result = _result(
            controller,
            candidate=candidate,
            suggestions=suggestions,
            scope="whole_project",
            **result_options,
        )
        before = _harmony_snapshot(controller)

        _deliver_whole_project_result(controller, result, scanned)

        assert _harmony_snapshot(controller) == before
        assert controller.harmonyStatusText == expected_status
        assert feedback == [expected_status]
    finally:
        controller.close()


def test_whole_project_mixed_result_counts_every_skip_and_write_failure(
    tmp_path, monkeypatch
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        ticks = measure_ticks(controller.editor.state.tempo)
        _managed_group(controller.editor, detached=False, measure_index=1)
        _managed_group(controller.editor, detached=True, measure_index=2)
        for note in (
            Note.create(id="manual-3-c", pitch=60, start_time=6.1, duration=0.8),
            Note.create(id="manual-3-e", pitch=64, start_time=6.2, duration=0.8),
            Note.create(id="duplicate-4-c", pitch=48, start_time=8.0, duration=2.0),
        ):
            controller.editor._execute(
                controller.editor.state.add_note(note, select=False)
            )
        safe = _candidate()
        weak = _candidate(total=0.54)
        unsafe = _candidate(pitches=(12, 16, 19))
        suggestions = (
            MeasureSuggestion(0, 0, ticks, False, (safe,)),
            MeasureSuggestion(1, ticks, ticks, False, (safe,)),
            MeasureSuggestion(2, 2 * ticks, ticks, False, (safe,)),
            MeasureSuggestion(3, 3 * ticks, ticks, False, (safe,)),
            MeasureSuggestion(4, 4 * ticks, ticks, False, (safe,)),
            MeasureSuggestion(5, 5 * ticks, ticks, False, (weak,)),
            MeasureSuggestion(6, 6 * ticks, ticks, False, (unsafe,)),
            MeasureSuggestion(7, 7 * ticks, ticks, False, ()),
            MeasureSuggestion(8, 8 * ticks, ticks, True, (safe,)),
            MeasureSuggestion(9, 9 * ticks, ticks, False, (safe,)),
        )
        result = _result(
            controller,
            suggestions=suggestions,
            scope="whole_project",
        )
        original_refresh = controller.editor._refresh
        refresh_calls = 0

        def fail_second_bulk_write(*, selection_changed: bool = True) -> None:
            nonlocal refresh_calls
            refresh_calls += 1
            if refresh_calls == 2:
                raise OSError("second bulk write failed")
            original_refresh(selection_changed=selection_changed)

        monkeypatch.setattr(controller.editor, "_refresh", fail_second_bulk_write)
        protected_before = controller.editor.harmony_metadata.groups
        notes_before = {note.id for note in controller.editor.state.active_notes}
        history_before = controller.editor._history.undo_depth
        expected_status = (
            "已在第 1 小节加入和弦；另外 4 个小节已有和弦或手动叠音，已为你保留；"
            "4 个小节暂时没有足够稳妥的和弦建议；1 个小节写入失败，请重试。"
        )

        _deliver_whole_project_result(controller, result, frozenset(range(10)))

        assert controller.editor.harmony_metadata.groups[:2] == protected_before
        assert {"manual-3-c", "manual-3-e", "duplicate-4-c"} <= {
            note.id for note in controller.editor.state.active_notes
        }
        assert notes_before <= {note.id for note in controller.editor.state.active_notes}
        assert [group.measure_index for group in controller.editor.harmony_metadata.groups[2:]] == [0]
        assert controller.editor._history.undo_depth == history_before + 1
        assert controller.harmonyStatusText == expected_status
        assert feedback == [expected_status]
    finally:
        controller.close()


def test_whole_project_empty_result_without_message_uses_safe_fallback(
    tmp_path,
) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="off")
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    try:
        result = _result(
            controller,
            suggestions=(),
            scope="whole_project",
        )
        expected_status = (
            "没有添加和弦：这次没有找到足够稳妥的搭配，"
            "软件没有硬塞一个可能不合适的和弦。"
        )

        _deliver_whole_project_result(controller, result, frozenset({0}))

        assert controller.harmonyStatusText == expected_status
        assert feedback == [expected_status]
    finally:
        controller.close()


@pytest.mark.parametrize(
    "gate",
    [
        "incomplete",
        "low_confidence",
        "low_score",
        "user_chord",
        "system_group",
        "detached",
        "duplicate_note",
        "unsafe_voicing",
        "stale_revision",
    ],
)
def test_auto_apply_gate_never_changes_project(tmp_path, gate: str) -> None:
    control, control_playback, _control_keyboard = _controller(tmp_path / "control")
    try:
        _advance_one_measure(control, control_playback)
        _accept(control, _result(control))
        assert len(control.editor.harmony_metadata.groups) == 1
    finally:
        control.close()

    controller, playback, _keyboard = _controller(tmp_path)
    try:
        candidate = _candidate()
        result_options: dict[str, object] = {}
        if gate != "incomplete":
            _advance_one_measure(controller, playback)
        if gate == "low_confidence":
            result_options.update(low_confidence=True, confidence=0.2)
        elif gate == "low_score":
            candidate = _candidate(total=0.54)
        elif gate == "user_chord":
            for note in (
                Note.create(id="user-c", pitch=60, start_time=0.1, duration=0.8),
                Note.create(id="user-e", pitch=64, start_time=0.2, duration=0.8),
            ):
                controller.editor._execute(controller.editor.state.add_note(note, select=False))
        elif gate in {"system_group", "detached"}:
            _managed_group(controller.editor, detached=gate == "detached")
        elif gate == "duplicate_note":
            duplicate = Note.create(id="duplicate-c", pitch=48, start_time=0.0, duration=2.0)
            controller.editor._execute(controller.editor.state.add_note(duplicate, select=False))
        elif gate == "unsafe_voicing":
            candidate = _candidate(pitches=(12, 16, 19))
        elif gate == "stale_revision":
            result_options["revision"] = controller.revision - 1
        before = (
            controller.editor.state,
            controller.editor.harmony_metadata,
            controller.editor._history.undo_depth,
        )

        _accept(controller, _result(controller, candidate=candidate, **result_options))

        assert (
            controller.editor.state,
            controller.editor.harmony_metadata,
            controller.editor._history.undo_depth,
        ) == before
    finally:
        controller.close()


def test_playhead_boundary_allows_one_atomic_auto_chord(tmp_path) -> None:
    controller, playback, _keyboard = _controller(tmp_path)
    try:
        _advance_one_measure(controller, playback)
        before_depth = controller.editor._history.undo_depth

        _accept(controller, _result(controller))

        group = controller.editor.harmony_metadata.groups[0]
        generated = [note for note in controller.editor.state.active_notes if note.id in group.generated_note_ids]
        assert [note.pitch for note in generated] == [48, 52, 55]
        assert {note.start_time for note in generated} == {0.0}
        assert {note.duration for note in generated} == {2.0}
        assert {note.velocity for note in generated} == {70}
        assert group.application_origin == "auto"
        assert group.replace_policy == "automatic"
        assert controller.editor._history.undo_depth == before_depth + 1
    finally:
        controller.close()


def test_advanced_mode_auto_applies_a_voicing_inside_its_full_pitch_range(
    tmp_path,
) -> None:
    controller, playback, _keyboard = _controller(
        tmp_path, experience_mode="advanced"
    )
    try:
        _advance_one_measure(controller, playback)

        _accept(controller, _result(controller, candidate=_candidate(pitches=(16, 24, 31))))

        group = controller.editor.harmony_metadata.groups[0]
        generated = [
            note
            for note in controller.editor.state.active_notes
            if note.id in group.generated_note_ids
        ]
        assert [note.pitch for note in generated] == [16, 24, 31]
    finally:
        controller.close()


def test_temporary_confirmation_is_explicit_only_and_undoable(tmp_path) -> None:
    controller, _playback, _keyboard = _controller(tmp_path, mode="preview")
    try:
        _accept(controller, _result(controller, temporary=True, low_confidence=True))

        controller.confirmTemporarySuggestion(0)

        group = controller.editor.harmony_metadata.groups[0]
        assert group.application_origin == "preview-manual"
        assert group.replace_policy == "explicit-only"
        generated_ids = group.generated_note_ids
        controller.editor.undo()
        assert all(note.id not in generated_ids for note in controller.editor.state.active_notes)
        assert controller.editor.harmony_metadata.is_empty
        controller.editor.redo()
        assert controller.editor.harmony_metadata.groups[0].generated_note_ids == generated_ids
        assert {
            note.id for note in controller.editor.state.active_notes if note.id in generated_ids
        } == set(generated_ids)
    finally:
        controller.close()


def test_clear_removes_managed_and_detached_generated_notes_but_keeps_user_notes(tmp_path) -> None:
    controller, _playback, _keyboard = _controller(tmp_path)
    try:
        _managed_group(controller.editor, detached=True)
        user_ids = {note.id for note in controller.editor.harmony_analysis_notes}

        controller.clearGeneratedHarmony()

        assert {note.id for note in controller.editor.state.active_notes} == user_ids
        assert controller.editor.harmony_metadata.is_empty
        controller.editor.undo()
        assert controller.editor.harmony_metadata.groups[0].detached is True
        assert user_ids < {note.id for note in controller.editor.state.active_notes}
    finally:
        controller.close()


def test_only_explicit_regenerate_replaces_detached_group(tmp_path) -> None:
    controller, playback, _keyboard = _controller(tmp_path, mode="preview")
    try:
        _managed_group(controller.editor, detached=True)
        old_ids = controller.editor.harmony_metadata.groups[0].generated_note_ids
        _advance_one_measure(controller, playback)
        _accept(controller, _result(controller, candidate=_candidate(pitches=(50, 53, 57))))

        controller.applySuggestion(0)
        assert controller.editor.harmony_metadata.groups[0].generated_note_ids == old_ids

        controller.regenerateMeasure(0)

        group = controller.editor.harmony_metadata.groups[0]
        assert group.generated_note_ids != old_ids
        assert group.voicing_pitches == (50, 53, 57)
        assert not any(note.id in old_ids for note in controller.editor.state.active_notes)
    finally:
        controller.close()


def test_same_auto_result_cannot_feed_back_into_a_second_chord(tmp_path) -> None:
    controller, playback, _keyboard = _controller(tmp_path)
    try:
        _advance_one_measure(controller, playback)
        result = _result(controller)
        _accept(controller, result)
        first_state = controller.editor.state
        first_metadata = controller.editor.harmony_metadata

        controller._apply_auto_result(result)

        assert controller.editor.state == first_state
        assert controller.editor.harmony_metadata == first_metadata
        assert len(first_metadata.groups) == 1
    finally:
        controller.close()


def test_recording_cursor_crossing_waits_for_stop_before_auto_application(tmp_path, monkeypatch) -> None:
    controller, _playback, keyboard = _controller(tmp_path)
    requested: list[object] = []
    monkeypatch.setattr(controller, "requestAnalysis", lambda: requested.append(object()))
    try:
        keyboard.recording = True
        controller._recording_was_active = True
        keyboard.recordCursorSeconds = 2.1
        keyboard.recordTimeChanged.emit()
        result = _result(controller)

        controller._apply_auto_result(result)
        assert controller.editor.harmony_metadata.is_empty

        keyboard.recording = False
        keyboard.recordingChanged.emit()
        controller._apply_auto_result(result)

        assert len(requested) == 1
        assert len(controller.editor.harmony_metadata.groups) == 1
    finally:
        controller.close()


def test_explicit_completed_frontier_applies_only_measures_before_cursor(tmp_path, monkeypatch) -> None:
    controller, playback, _keyboard = _controller(tmp_path)
    monkeypatch.setattr(controller, "requestAnalysis", lambda: None)
    try:
        ticks = measure_ticks(controller.editor.state.tempo)
        candidate = _candidate()
        controller._latest_result = _result(
            controller,
            suggestions=(
                MeasureSuggestion(0, 0, ticks, False, (candidate,)),
                MeasureSuggestion(1, ticks, ticks, False, (candidate,)),
            ),
        )
        playback.positionSeconds = controller.editor.state.tempo.ticks_to_seconds(ticks)

        controller.applyCompletedPart()

        assert [group.measure_index for group in controller.editor.harmony_metadata.groups] == [0]
    finally:
        controller.close()
