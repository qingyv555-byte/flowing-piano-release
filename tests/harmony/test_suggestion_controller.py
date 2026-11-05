from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, get_ident

import pytest
from PySide6.QtCore import QSettings, QTimer

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.controller import (
    ANALYSIS_DEBOUNCE_MS,
    MANUAL_TAIL_IDLE_MS,
)
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.suggestion_service import HarmonySuggestionService
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    HarmonyAnalysisResult,
    KeyEstimate,
)
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary


@dataclass
class PendingAnalysis:
    request: AnalysisRequest
    release: Event
    completed: Event
    result: object | None = None
    error: Exception | None = None


class ControlledService:
    """A thread-safe service double that holds each worker until the test releases it."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: list[PendingAnalysis] = []
        self.worker_thread_ids: list[int] = []

    def analyze(self, request: AnalysisRequest) -> HarmonyAnalysisResult:
        pending = PendingAnalysis(request, Event(), Event())
        with self._lock:
            self.worker_thread_ids.append(get_ident())
            self._pending.append(pending)
        try:
            pending.release.wait()
            if pending.error is not None:
                raise pending.error
            assert pending.result is not None
            return pending.result
        finally:
            pending.completed.set()

    def count(self) -> int:
        with self._lock:
            return len(self._pending)

    def take(self, index: int) -> PendingAnalysis:
        with self._lock:
            return self._pending[index]

    def release_all(self) -> None:
        with self._lock:
            pending = tuple(self._pending)
        for item in pending:
            item.release.set()


def result_for(request: AnalysisRequest) -> HarmonyAnalysisResult:
    return HarmonyAnalysisResult(
        project_identity=request.project_identity,
        project_revision=request.project_revision,
        generation_id=request.generation_id,
        mode=request.mode,
        key_estimate=KeyEstimate(None, None, None, 0.0, True, False),
        suggestions=(),
        scope=request.scope,
    )


def c_major_editor_notes() -> tuple[Note, ...]:
    return (
        Note.create(id="c", pitch=72, start_time=0.0, duration=0.5),
        Note.create(id="b", pitch=83, start_time=0.5, duration=0.25),
        Note.create(id="e", pitch=76, start_time=0.75, duration=0.25),
        Note.create(id="g", pitch=79, start_time=1.0, duration=0.5),
        Note.create(id="f", pitch=77, start_time=1.5, duration=0.25),
        Note.create(id="a", pitch=81, start_time=1.75, duration=0.125),
        Note.create(id="c-end", pitch=72, start_time=1.875, duration=0.125),
    )


@pytest.fixture
def service() -> ControlledService:
    controlled = ControlledService()
    yield controlled
    controlled.release_all()


@pytest.fixture
def controller(qtbot, tmp_path, service) -> HarmonySuggestionController:
    note = Note.create(id="seed", pitch=60, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    instance = HarmonySuggestionController(editor, settings, service=service)
    yield instance
    instance.close()
    service.release_all()


def wait_for_request(qtbot, service: ControlledService, index: int) -> PendingAnalysis:
    qtbot.waitUntil(lambda: service.count() > index, timeout=2_000)
    return service.take(index)


def wait_for_receiver(qtbot, controller: HarmonySuggestionController, pending: PendingAnalysis) -> None:
    qtbot.waitUntil(pending.completed.is_set)
    qtbot.waitUntil(
        lambda: not any(
            job._request is pending.request for job in controller._jobs.values()
        )
    )


def test_state_burst_is_coalesced_then_offers_preview_tail_after_idle(
    controller, service, qtbot
) -> None:
    before_debounce = Event()
    before_tail = Event()
    tail_due = Event()
    no_extra_work_due = Event()
    QTimer.singleShot(ANALYSIS_DEBOUNCE_MS - 50, before_debounce.set)
    QTimer.singleShot(MANUAL_TAIL_IDLE_MS - 50, before_tail.set)
    QTimer.singleShot(MANUAL_TAIL_IDLE_MS + 100, tail_due.set)
    QTimer.singleShot(MANUAL_TAIL_IDLE_MS + ANALYSIS_DEBOUNCE_MS + 100, no_extra_work_due.set)
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)

    controller.editor.createNoteAt(120, 20)
    controller.editor.createNoteAt(160, 20)

    qtbot.waitUntil(before_debounce.is_set)
    assert service.count() == 0
    first = wait_for_request(qtbot, service, 0)
    assert service.count() == 1
    latest_end = max(
        note.start_time + note.duration for note in controller.editor.state.notes
    )
    assert first.request.current_tick > controller.editor.state.tempo.seconds_to_ticks(latest_end)
    expected_tail_tick = controller.editor.state.tempo.seconds_to_ticks(latest_end)
    qtbot.waitUntil(before_tail.is_set)
    assert service.count() == 1
    qtbot.waitUntil(tail_due.is_set)
    assert service.count() == 2
    tail = service.take(1)
    assert tail.request.current_tick == expected_tail_tick
    qtbot.waitUntil(no_extra_work_due.is_set)
    assert service.count() == 2

    first.result = result_for(first.request)
    first.release.set()
    wait_for_receiver(qtbot, controller, first)
    assert accepted == []
    tail.result = result_for(tail.request)
    tail.release.set()
    wait_for_receiver(qtbot, controller, tail)
    assert accepted == [tail.result]
    assert controller.latestResult is tail.result


def test_stale_worker_result_cannot_replace_new_revision(controller, service, qtbot) -> None:
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)
    controller.requestAnalysis()
    old = wait_for_request(qtbot, service, 0)

    revision = controller.revision
    controller.editor.createNoteAt(120, 20)
    assert controller.revision == revision + 1
    old.result = result_for(old.request)
    old.release.set()
    wait_for_receiver(qtbot, controller, old)

    assert accepted == []
    assert controller.latestResult is None
    assert controller.analysisPending is False

    wait_for_request(qtbot, service, 1)
    assert controller.latestResult is None
    assert controller.analysisPending is True


def test_project_switch_clears_result_rejects_prior_worker_and_reanalyzes(
    controller, service, qtbot
) -> None:
    accepted_results: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted_results.append)
    controller.requestAnalysis()
    accepted = wait_for_request(qtbot, service, 0)
    accepted.result = result_for(accepted.request)
    accepted.release.set()
    wait_for_receiver(qtbot, controller, accepted)
    assert accepted_results == [accepted.result]

    controller.requestAnalysis()
    stale = wait_for_request(qtbot, service, 1)
    controller.editor.newProject()
    replacement = wait_for_request(qtbot, service, 2)
    stale.result = result_for(stale.request)
    stale.release.set()
    wait_for_receiver(qtbot, controller, stale)

    assert accepted_results == [accepted.result]
    assert controller.latestResult is None
    assert controller.analysisPending is True

    replacement.result = result_for(replacement.request)
    replacement.release.set()
    wait_for_receiver(qtbot, controller, replacement)

    assert accepted_results == [accepted.result, replacement.result]
    assert controller.latestResult is replacement.result
    assert controller.analysisPending is False


def test_identical_project_session_switch_reanalyzes_and_rejects_old_result(
    service, qtbot, tmp_path
) -> None:
    state = ProjectState(
        (Note.create(id="same-note", pitch=60, start_time=0.0, duration=0.5),),
        tempo=TempoMap(bpm=144, beats_per_bar=4, beat_unit=4),
    )
    first_path = tmp_path / "first.pianoai.json"
    second_path = tmp_path / "second.pianoai.json"
    save_project(first_path, state)
    save_project(second_path, state)
    editor = EditorController()
    editor.openProject(str(first_path))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    controller = HarmonySuggestionController(editor, settings, service=service)
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)
    try:
        controller.requestAnalysis()
        stale = wait_for_request(qtbot, service, 0)
        assert stale.request.project_identity == str(first_path)

        editor.openProject(str(second_path))
        replacement = wait_for_request(qtbot, service, 1)

        assert replacement.request.project_identity == str(second_path)
        stale.result = result_for(stale.request)
        stale.release.set()
        wait_for_receiver(qtbot, controller, stale)
        assert accepted == []

        replacement.result = result_for(replacement.request)
        replacement.release.set()
        wait_for_receiver(qtbot, controller, replacement)
        assert accepted == [replacement.result]
        assert controller.latestResult is replacement.result
    finally:
        controller.close()
        service.release_all()


def test_whole_project_snapshot_uses_all_occupied_measures_without_changing_mode(
    controller
) -> None:
    melody = (
        Note.create(id="measure-0", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="measure-1", pitch=64, start_time=2.0, duration=0.5),
        Note.create(id="measure-3", pitch=67, start_time=6.0, duration=0.5),
    )
    controller.editor._execute(ProjectState(melody, frozenset({"measure-0"})))
    original_mode = controller.mode

    request = controller._snapshot_request(
        include_manual_tail=False,
        scope="whole_project",
    )
    live_request = controller._snapshot_request(
        include_manual_tail=False,
        scope="live",
    )

    assert request.scope == "whole_project"
    assert request.completed_measure_indices == frozenset({0, 1, 3})
    assert live_request.scope == "live"
    assert live_request.completed_measure_indices == frozenset()
    assert controller.mode == original_mode


@pytest.mark.parametrize("mode", ("off", "preview", "auto"))
def test_whole_project_command_submits_in_every_mode_without_changing_settings(
    controller, service, qtbot, mode: str
) -> None:
    controller.setMode(mode)
    requests_before = 1 if mode == "auto" else service.count()
    if mode == "auto":
        wait_for_request(qtbot, service, 0)

    controller.generateWholeProjectHarmony()

    pending = wait_for_request(qtbot, service, requests_before)
    assert pending.request.scope == "whole_project"
    assert pending.request.mode == mode
    assert controller.settings.mode == mode
    assert controller.mode == mode
    assert controller.wholeProjectPending is True
    assert controller.harmonyStatusText == "正在检测整首…"


def test_whole_project_command_reports_empty_melody_without_starting_a_job(
    controller, service
) -> None:
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    controller.setMode("off")
    controller.editor.newProject()

    controller.generateWholeProjectHarmony()

    assert service.count() == 0
    assert controller.wholeProjectPending is False
    assert controller.harmonyStatusText == "没有添加和弦：工程里还没有旋律音符。"
    assert feedback == ["没有添加和弦：工程里还没有旋律音符。"]


def test_whole_project_pending_last_only_for_its_current_job(controller, service, qtbot) -> None:
    controller.setMode("off")

    controller.generateWholeProjectHarmony()
    pending = wait_for_request(qtbot, service, 0)
    assert controller.wholeProjectPending is True

    pending.result = result_for(pending.request)
    pending.release.set()
    wait_for_receiver(qtbot, controller, pending)

    assert controller.wholeProjectPending is False


def test_whole_project_start_failure_terminates_state_and_reports_once(
    controller, monkeypatch
) -> None:
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)

    def fail_snapshot(*, include_manual_tail: bool, scope: str = "live"):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(controller, "_snapshot_request", fail_snapshot)

    controller.generateWholeProjectHarmony()

    expected = "整首和弦检测失败，请稍后再试。"
    assert controller.analysisPending is False
    assert controller.wholeProjectPending is False
    assert controller._active_serial is None
    assert controller._whole_project_serial is None
    assert controller.harmonyStatusText == expected
    assert feedback == [expected]


def test_whole_project_worker_failure_terminates_state_and_reports_once(
    controller, service, qtbot
) -> None:
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    controller.generateWholeProjectHarmony()
    pending = wait_for_request(qtbot, service, 0)

    pending.error = RuntimeError("analysis failed")
    pending.release.set()
    wait_for_receiver(qtbot, controller, pending)

    expected = "整首和弦检测失败，请稍后再试。"
    assert controller.analysisPending is False
    assert controller.wholeProjectPending is False
    assert controller._active_serial is None
    assert controller._whole_project_serial is None
    assert controller.harmonyStatusText == expected
    assert feedback == [expected]


@pytest.mark.parametrize("cancellation", ("edit", "off"))
def test_cancelled_whole_project_job_clears_all_state_and_rejects_late_result(
    controller, service, qtbot, cancellation: str
) -> None:
    notes = c_major_editor_notes()
    if cancellation == "edit":
        controller.setMode("off")
    controller.editor._execute(ProjectState(notes, frozenset({note.id for note in notes})))
    controller.generateWholeProjectHarmony()
    pending = wait_for_request(qtbot, service, 0)
    pending.result = HarmonySuggestionService().analyze(pending.request)
    assert pending.result.suggestions

    if cancellation == "edit":
        controller.editor._execute(
            controller.editor.state.add_note(
                Note.create(id="late-edit", pitch=65, start_time=2.5, duration=0.5),
                select=False,
            )
        )
    else:
        controller.setMode("off")

    expected_state = controller.editor.state
    assert controller.analysisPending is False
    assert controller.wholeProjectPending is False
    assert controller._active_serial is None
    assert controller._whole_project_serial is None
    assert controller.harmonyStatusText == ""

    pending.release.set()
    wait_for_receiver(qtbot, controller, pending)

    assert controller.analysisPending is False
    assert controller.wholeProjectPending is False
    assert controller._active_serial is None
    assert controller._whole_project_serial is None
    assert controller.harmonyStatusText == ""
    assert controller.latestResult is None
    assert controller.editor.state == expected_state
    assert controller.editor.harmony_metadata.is_empty


def test_switching_off_clears_a_completed_whole_project_status(controller) -> None:
    controller._set_harmony_status_text(
        "检测了 1 个有旋律的小节，已在第 1 小节加入和弦。"
    )

    controller.setMode("off")

    assert controller.harmonyStatusText == ""


def test_whole_project_repeated_clicks_start_one_current_job(controller, service, qtbot) -> None:
    controller.setMode("off")

    controller.generateWholeProjectHarmony()
    controller.generateWholeProjectHarmony()
    pending = wait_for_request(qtbot, service, 0)

    assert service.count() == 1
    assert pending.request.scope == "whole_project"
    assert controller.wholeProjectPending is True


def test_stale_whole_project_result_cannot_apply_after_edit_or_replace_project(
    controller, service, qtbot
) -> None:
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)
    controller.setMode("off")

    controller.generateWholeProjectHarmony()
    edited = wait_for_request(qtbot, service, 0)
    controller.editor._execute(
        controller.editor.state.add_note(
            Note.create(id="edit", pitch=64, start_time=1.0, duration=0.5),
            select=False,
        )
    )
    assert controller.wholeProjectPending is False
    edited.result = result_for(edited.request)
    edited.release.set()
    wait_for_receiver(qtbot, controller, edited)

    controller.generateWholeProjectHarmony()
    replaced = wait_for_request(qtbot, service, 1)
    controller.editor.newProject()
    controller.generateWholeProjectHarmony()
    assert controller.harmonyStatusText == "没有添加和弦：工程里还没有旋律音符。"
    replaced.result = result_for(replaced.request)
    replaced.release.set()
    wait_for_receiver(qtbot, controller, replaced)

    assert accepted == []
    assert controller.latestResult is None
    assert controller.harmonyStatusText == "没有添加和弦：工程里还没有旋律音符。"


def test_completed_whole_project_preview_result_applies_without_entering_live_preview(
    controller, service, qtbot
) -> None:
    notes = c_major_editor_notes()
    controller.editor._execute(ProjectState(notes, frozenset({note.id for note in notes})))

    controller.generateWholeProjectHarmony()
    pending = wait_for_request(qtbot, service, 0)
    pending.result = HarmonySuggestionService().analyze(pending.request)
    assert pending.result.suggestions
    pending.release.set()
    wait_for_receiver(qtbot, controller, pending)

    assert controller.wholeProjectPending is False
    assert controller.latestResult is None
    assert controller.previewModel.count == 0
    groups = controller.editor.harmony_metadata.groups
    assert len(groups) == 1
    assert groups[0].application_origin == "whole-project"
    assert controller.harmonyStatusText == (
        "检测了 1 个有旋律的小节，已在第 1 小节加入和弦。"
        "已有和弦或你自己叠加的音符都没有改动。"
    )


def test_completed_whole_project_auto_result_cannot_be_applied_later(
    controller, service, qtbot
) -> None:
    notes = c_major_editor_notes()
    controller.editor._execute(ProjectState(notes, frozenset({note.id for note in notes})))
    controller.setMode("auto")
    live = wait_for_request(qtbot, service, 0)
    live.result = result_for(live.request)
    live.release.set()
    wait_for_receiver(qtbot, controller, live)

    controller.generateWholeProjectHarmony()
    whole = wait_for_request(qtbot, service, 1)
    whole.result = HarmonySuggestionService().analyze(whole.request)
    assert whole.result.suggestions
    whole.release.set()
    wait_for_receiver(qtbot, controller, whole)

    whole_project_groups = controller.editor.harmony_metadata.groups
    assert len(whole_project_groups) == 1
    assert whole_project_groups[0].application_origin == "whole-project"

    controller._update_completed_frontier(1920)
    controller.applyCompletedPart()

    assert controller.wholeProjectPending is False
    assert controller.latestResult is live.result
    assert controller.previewModel.count == 0
    assert controller.editor.harmony_metadata.groups == whole_project_groups
    assert controller.harmonyStatusText == (
        "检测了 1 个有旋律的小节，已在第 1 小节加入和弦。"
        "已有和弦或你自己叠加的音符都没有改动。"
    )


@pytest.mark.parametrize("mode", ("off", "auto"))
def test_real_whole_project_low_confidence_result_never_leaks_live_pending_copy(
    qtbot, tmp_path, mode: str
) -> None:
    notes = (
        Note.create(id="c", pitch=72, start_time=0.0, duration=0.25),
        Note.create(id="c-sharp", pitch=73, start_time=0.25, duration=0.25),
    )
    editor = EditorController(ProjectState(notes, frozenset({note.id for note in notes})))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / f"harmony-{mode}.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode(mode)
    instance = HarmonySuggestionController(editor, settings)
    feedback: list[str] = []
    instance.feedbackRequested.connect(feedback.append)
    try:
        instance.generateWholeProjectHarmony()
        qtbot.waitUntil(lambda: not instance.wholeProjectPending, timeout=2_000)

        expected = "没有添加和弦：旋律里的线索还不够明确。再补几拍后重试。"
        assert instance.analysisPending is False
        assert instance.harmonyStatusText == expected
        assert "正在判断" not in instance.harmonyStatusText
        assert feedback == [expected]
    finally:
        instance.close()


def test_whole_project_job_defers_live_requests_and_timers_until_it_finishes(
    controller, service, qtbot
) -> None:
    controller.generateWholeProjectHarmony()
    whole = wait_for_request(qtbot, service, 0)
    serial = controller._active_serial

    controller.requestAnalysis()
    controller._start_debounced_analysis()
    controller._start_manual_tail_analysis()

    assert service.count() == 1
    assert controller._active_serial == serial
    assert controller.wholeProjectPending is True

    whole.result = result_for(whole.request)
    whole.release.set()
    wait_for_receiver(qtbot, controller, whole)
    controller.requestAnalysis()
    live = wait_for_request(qtbot, service, 1)

    assert live.request.scope == "live"


def test_save_as_and_rename_keep_same_session_preview_identity(
    service, qtbot, tmp_path
) -> None:
    note = Note.create(id="same-session-note", pitch=60, start_time=0.0, duration=0.5)
    state = ProjectState((note,), tempo=TempoMap(bpm=120, beats_per_bar=4, beat_unit=4))
    first_path = tmp_path / "first.pianoai.json"
    second_path = tmp_path / "second.pianoai.json"
    save_project(first_path, state)
    editor = EditorController(project_library=ProjectLibrary(tmp_path / "projects"))
    editor.openProject(str(first_path))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    controller = HarmonySuggestionController(editor, settings, service=service)
    try:
        session_id = editor.projectSessionId
        controller.requestAnalysis()
        pending = wait_for_request(qtbot, service, 0)
        assert pending.request.project_identity == str(first_path)

        editor.saveProject(str(second_path))
        qtbot.waitUntil(
            lambda: editor.currentFilePath == str(second_path) and not editor.isSaving,
            timeout=2_000,
        )
        editor.renameProject(str(second_path), "Renamed project")

        assert editor.projectSessionId == session_id
        assert controller._project_identity == str(first_path)
        pending.result = HarmonySuggestionService().analyze(pending.request)
        assert len(pending.result.suggestions) == 1
        pending.release.set()
        wait_for_receiver(qtbot, controller, pending)

        assert controller.latestResult is pending.result
        assert controller.previewModel.count == 1
    finally:
        controller.close()
        service.release_all()


def test_mode_change_delegates_to_settings_and_invalidates_queued_result(
    controller, service, qtbot
) -> None:
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)
    controller.requestAnalysis()
    stale = wait_for_request(qtbot, service, 0)
    generation = controller.analysisGeneration

    controller.setMode("auto")
    replacement = wait_for_request(qtbot, service, 1)
    stale.result = result_for(stale.request)
    stale.release.set()
    wait_for_receiver(qtbot, controller, stale)

    assert controller.mode == "auto"
    assert controller.settings.mode == "auto"
    assert controller.analysisGeneration == generation + 1
    assert controller.latestResult is None
    assert controller.analysisPending is True
    assert accepted == []

    replacement.result = result_for(replacement.request)
    replacement.release.set()
    wait_for_receiver(qtbot, controller, replacement)

    assert controller.analysisPending is False
    assert accepted == [replacement.result]
    assert controller.latestResult is replacement.result


def test_mode_change_starts_analysis_for_the_existing_melody(
    controller, service, qtbot
) -> None:
    controller.setMode("auto")

    pending = wait_for_request(qtbot, service, 0)

    assert pending.request.mode == "auto"
    assert pending.request.melody[0].note_id == "seed"


def test_experience_mode_change_reanalyzes_with_the_advanced_pitch_range(
    service, qtbot, tmp_path
) -> None:
    note = Note.create(id="advanced-seed", pitch=96, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    experience = ExperienceSettings(
        settings=QSettings(str(tmp_path / "experience.ini"), QSettings.Format.IniFormat)
    )
    # The shipped editor now starts in the professional/advanced range. Move
    # through the compatibility mode first so this test still exercises a real
    # range transition instead of setting the current value to itself.
    experience.setMode("beginner")
    controller = HarmonySuggestionController(
        editor, settings, service=service, experience=experience
    )
    try:
        experience.setMode("advanced")

        pending = wait_for_request(qtbot, service, 0)

        assert (pending.request.min_pitch, pending.request.max_pitch) == (12, 108)
        pending.result = result_for(pending.request)
        pending.release.set()
        wait_for_receiver(qtbot, controller, pending)
        assert controller.latestResult is pending.result
    finally:
        controller.close()
        service.release_all()


def test_lifecycle_invalidation_preserves_same_project_result_only_while_pending(
    controller, service, qtbot
) -> None:
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)
    controller.requestAnalysis()
    first = wait_for_request(qtbot, service, 0)
    first.result = result_for(first.request)
    first.release.set()
    wait_for_receiver(qtbot, controller, first)
    assert accepted == [first.result]

    controller.requestAnalysis()
    replacement = wait_for_request(qtbot, service, 1)
    assert controller.latestResult is first.result

    controller.windowDeactivated()
    replacement.result = result_for(replacement.request)
    replacement.release.set()
    wait_for_receiver(qtbot, controller, replacement)
    assert controller.latestResult is first.result
    assert accepted == [first.result]
    assert controller.analysisPending is False

    controller.leaveEditor()
    assert controller.latestResult is None


def test_worker_runs_off_main_thread_and_does_not_block_main_event_loop(
    controller, service, qtbot
) -> None:
    main_thread = get_ident()
    accepted_threads: list[int] = []
    controller.analysisAccepted.connect(lambda _: accepted_threads.append(get_ident()))

    controller.requestAnalysis()
    pending = wait_for_request(qtbot, service, 0)
    main_event_processed = Event()
    QTimer.singleShot(0, main_event_processed.set)
    qtbot.waitUntil(main_event_processed.is_set)

    assert len(service.worker_thread_ids) == 1
    assert service.worker_thread_ids[0] != main_thread
    pending.result = result_for(pending.request)
    pending.release.set()
    wait_for_receiver(qtbot, controller, pending)
    assert accepted_threads == [main_thread]


def test_close_invalidates_and_deletion_drops_late_worker_result(controller, service, qtbot) -> None:
    accepted: list[HarmonyAnalysisResult] = []
    controller.analysisAccepted.connect(accepted.append)
    controller.requestAnalysis()
    pending = wait_for_request(qtbot, service, 0)
    controller.close()
    assert controller.analysisPending is False
    assert controller.latestResult is None

    destroyed = Event()
    controller.destroyed.connect(destroyed.set)
    controller.deleteLater()
    qtbot.waitUntil(destroyed.is_set)
    pending.result = result_for(pending.request)
    pending.release.set()
    qtbot.waitUntil(pending.completed.is_set)
    receiver_settled = Event()
    QTimer.singleShot(0, receiver_settled.set)
    qtbot.waitUntil(receiver_settled.is_set)
    assert accepted == []


def test_invalid_worker_result_finishes_current_request_with_feedback(
    controller, service, qtbot, caplog
) -> None:
    accepted: list[HarmonyAnalysisResult] = []
    feedback: list[str] = []
    controller.analysisAccepted.connect(accepted.append)
    controller.feedbackRequested.connect(feedback.append)
    caplog.set_level("ERROR")

    controller.requestAnalysis()
    pending = wait_for_request(qtbot, service, 0)
    pending.result = object()
    pending.release.set()
    wait_for_receiver(qtbot, controller, pending)

    assert controller.analysisPending is False
    assert controller.latestResult is None
    assert accepted == []
    assert feedback == ["和弦分析失败，请稍后重试"]
    assert "Harmony analysis returned an invalid result" in caplog.text


def test_snapshot_failure_is_logged_and_reports_short_feedback(
    controller, caplog, monkeypatch
) -> None:
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    caplog.set_level("ERROR")

    def fail_snapshot(*, include_manual_tail: bool) -> AnalysisRequest:
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(controller, "_snapshot_request", fail_snapshot)

    controller.requestAnalysis()

    assert "Harmony analysis could not start" in caplog.text
    assert feedback == ["和弦分析失败，请稍后重试"]
