import json
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QRunnable, QThreadPool, QUrl
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles


def controller_with_note(**kwargs) -> EditorController:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    return EditorController(ProjectState((note,), frozenset({"a"})), **kwargs)


def test_new_project_starts_as_an_unsaved_empty_draft(qtbot, tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)
    target = tmp_path / "song.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))
    assert controller.currentFilePath == str(target)

    controller.newProject()

    assert controller.state.notes == ()
    assert controller.currentFilePath == ""
    assert controller.currentFileName == "未命名工程"
    assert controller.isDirty is False
    assert controller.canUndo is False
    assert not [
        entry for entry in controller.libraryEntries
        if str(entry["path"]).startswith(str(tmp_path / "Projects"))
    ]


def test_enabled_autosave_does_not_promote_an_unnamed_draft(qtbot, tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(
        project_library=library,
        autosave_enabled=True,
        autosave_interval_ms=30,
    )
    controller.setBpm(66)
    qtbot.wait(80)
    assert controller.currentFilePath == ""
    assert list(library.root.glob("**/*.pianoai.json")) == []


def test_save_remembers_current_path_and_recent_files(qtbot, tmp_path) -> None:
    recent = RecentFiles()
    controller = controller_with_note(recent_files=recent)
    target = tmp_path / "song.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))

    assert controller.currentFilePath == str(target)
    assert controller.currentFileName == "song.pianoai.json"
    assert controller.recentFiles == [str(target)]

    controller.setBpm(90)
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveCurrent()
    assert json.loads(target.read_text(encoding="utf-8"))["bpm"] == 90


def test_save_current_requests_a_path_for_an_unnamed_draft() -> None:
    controller = controller_with_note()
    requested = QSignalSpy(controller.savePathRequired)
    controller.saveCurrent()
    assert requested.count() == 1
    assert controller.currentFilePath == ""


def test_validate_project_name_reports_normalized_value_and_collision(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    first = library.next_project_path()
    first.parent.mkdir(parents=True)
    save_project(first, ProjectState())
    library.set_display_name(first, "已有工程")
    controller = controller_with_note(project_library=library)

    result = controller.validateProjectName("  已有工程  ", "")

    assert result["valid"] is False
    assert result["normalized"] == "已有工程"
    assert result["suggestion"] == "已有工程 2"


def test_named_save_promotes_draft_to_library_and_sets_display_name(qtbot, tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)

    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        assert controller.saveNamedProject("夜曲练习") is True

    assert controller.currentProjectIsLibrary is True
    assert controller.currentFileName == "夜曲练习"
    assert library.display_name_for(Path(controller.currentFilePath)) == "夜曲练习"
    assert controller.isDirty is False


def test_failed_named_save_removes_reserved_empty_library_folder(
    qtbot, tmp_path, monkeypatch
) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)

    def fail_write(*_args) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("ai_piano_studio.editor.controller.save_project_document", fail_write)
    with qtbot.waitSignal(controller.saveFailed, timeout=2000):
        assert controller.saveNamedProject("未写入工程") is True

    assert not (library.root / "project-001").exists()


def test_external_first_save_can_keep_original_without_reprompt(qtbot, tmp_path) -> None:
    external = tmp_path / "outside.pianoai.json"
    save_project(external, ProjectState())
    controller = controller_with_note()
    controller.openProject(str(external))
    requested = QSignalSpy(controller.externalSaveChoiceRequired)

    controller.setBpm(90)
    controller.saveCurrent()

    assert requested.count() == 1
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        assert controller.chooseExternalSaveDestination("original") is True

    controller.setBpm(100)
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveCurrent()
    assert requested.count() == 1
    assert json.loads(external.read_text(encoding="utf-8"))["bpm"] == 100


def test_save_copy_uses_dirty_in_memory_state_and_opens_copy(qtbot, tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)

    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        assert controller.saveNamedProject("原稿") is True
    original = controller.currentFilePath

    controller.setBpm(97)
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        assert controller.saveCopy("试听副本") is True

    copied = Path(controller.currentFilePath)
    assert copied != Path(original)
    assert controller.currentFileName == "试听副本"
    assert json.loads(copied.read_text(encoding="utf-8"))["bpm"] == 97
    assert json.loads(Path(original).read_text(encoding="utf-8"))["bpm"] != 97
    assert controller.isDirty is False


def test_project_export_does_not_change_current_identity_or_dirty_state(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    original = tmp_path / "original.pianoai.json"
    export = tmp_path / "exported.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(original))
    controller.setBpm(101)
    assert controller.isDirty is True

    with qtbot.waitSignal(controller.projectExported, timeout=2000):
        controller.exportProjectFile(str(export))

    assert export.exists()
    assert controller.currentFilePath == str(original)
    assert controller.isDirty is True
    assert json.loads(export.read_text(encoding="utf-8"))["bpm"] == 101


def test_discard_unsaved_changes_restores_the_saved_document(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    target = tmp_path / "discard.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2_000):
        controller.saveProject(str(target))
    saved_bytes = target.read_bytes()
    controller.setBpm(90)
    controller.createNoteAt(67, 1.0)
    assert controller.isDirty is True

    assert controller.discardUnsavedChanges() is True

    assert controller.currentFilePath == str(target)
    assert controller.state.tempo.bpm == 120
    assert [note.pitch for note in controller.state.notes] == [60]
    assert controller.isDirty is False
    assert controller.canUndo is False
    assert target.read_bytes() == saved_bytes


def test_discard_unnamed_draft_resets_memory_without_creating_a_file(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)
    controller.setBpm(90)

    assert controller.discardUnsavedChanges() is True

    assert controller.currentFilePath == ""
    assert controller.state.notes == ()
    assert controller.isDirty is False
    assert list(library.root.glob("**/*.pianoai.json")) == []


def test_failed_discard_preserves_the_dirty_in_memory_project(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    target = tmp_path / "missing-after-save.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2_000):
        controller.saveProject(str(target))
    controller.setBpm(90)
    target.unlink()
    before = (
        controller.state.content,
        controller.currentFilePath,
        controller.isDirty,
    )

    assert controller.discardUnsavedChanges() is False

    assert (
        controller.state.content,
        controller.currentFilePath,
        controller.isDirty,
    ) == before


def test_load_project_sets_current_path_and_recent(qtbot, tmp_path) -> None:
    recent = RecentFiles()
    target = tmp_path / "loaded.pianoai.json"
    save_project(target, ProjectState((Note.create(pitch=62, start_time=1.0, duration=0.5),)))

    controller = controller_with_note(recent_files=recent)
    controller.loadProject(str(target))

    assert controller.currentFilePath == str(target)
    assert controller.recentFiles == [str(target)]
    assert [note.pitch for note in controller.state.notes] == [62]
    assert controller.isDirty is False


def test_open_recent_delegates_to_load(qtbot, tmp_path) -> None:
    target = tmp_path / "recent.pianoai.json"
    save_project(target, ProjectState((Note.create(pitch=65, start_time=0.0, duration=0.5),)))

    controller = controller_with_note()
    spy = QSignalSpy(controller.loadSucceeded)
    controller.openRecent(str(target))
    assert spy.count() == 1
    assert controller.currentFilePath == str(target)


def test_open_project_saves_dirty_project_before_switching(qtbot, tmp_path) -> None:
    current = tmp_path / "current.pianoai.json"
    target = tmp_path / "next.pianoai.json"
    save_project(target, ProjectState((Note.create(pitch=65, start_time=1.0, duration=0.5),)))
    controller = controller_with_note()
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(current))
    controller.setBpm(90)

    with qtbot.waitSignal(controller.loadSucceeded, timeout=2000):
        controller.openProject(str(target))

    assert controller.currentFilePath == str(target)
    assert json.loads(current.read_text(encoding="utf-8"))["bpm"] == 90


def test_dirty_edit_writes_recovery_snapshot(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    controller = controller_with_note(
        recovery_store=store, recovery_interval_ms=50
    )
    with qtbot.waitSignal(controller.recoveryAvailableChanged, timeout=2000):
        controller.setBpm(90)
    assert store.available is True
    restored = store.read()
    assert restored is not None
    assert restored[0].tempo.bpm == 90


def test_returning_to_clean_state_clears_recovery(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    controller = controller_with_note(
        recovery_store=store, recovery_interval_ms=50
    )
    with qtbot.waitSignal(controller.recoveryAvailableChanged, timeout=2000):
        controller.setBpm(90)
    assert store.available is True

    controller.undo()
    assert controller.isDirty is False
    assert store.available is False


def test_restore_recovery_loads_snapshot_as_dirty(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    original = ProjectState((Note.create(id="r", pitch=67, start_time=2.0, duration=1.0),))
    store.write(original, "C:/projects/draft.pianoai.json")

    controller = controller_with_note(recovery_store=store)
    assert controller.recoveryAvailable is True
    controller.restoreRecovery()

    assert [note.pitch for note in controller.state.notes] == [67]
    assert controller.isDirty is True
    assert controller.currentFilePath == "C:/projects/draft.pianoai.json"
    assert controller.recoveryAvailable is False


def test_restore_recovery_reports_whether_navigation_may_enter_editor(tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    controller = controller_with_note(recovery_store=store)
    assert controller.restoreRecovery() is False

    store.write(ProjectState(), None)
    controller.recoveryAvailableChanged.emit()
    assert controller.restoreRecovery() is True


def test_discard_recovery_clears_snapshot(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    store.write(ProjectState(), None)

    controller = controller_with_note(recovery_store=store)
    assert controller.recoveryAvailable is True
    controller.discardRecovery()
    assert controller.recoveryAvailable is False
    assert store.available is False


def test_autosave_writes_current_file_after_idle(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    controller = controller_with_note(
        recovery_store=store,
        autosave_enabled=True,
        autosave_interval_ms=50,
        recovery_interval_ms=50,
    )
    target = tmp_path / "auto.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))

    spy = QSignalSpy(controller.saveSucceeded)
    controller.setBpm(75)
    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    assert controller.isDirty is False
    assert json.loads(target.read_text(encoding="utf-8"))["bpm"] == 75


def test_autosave_uses_a_queue_isolated_from_unrelated_global_workers(qtbot, tmp_path) -> None:
    """Autosave must not be starved by unrelated global-pool work."""
    store = RecoveryStore(tmp_path / "recovery")
    controller = controller_with_note(
        recovery_store=store,
        autosave_enabled=True,
        autosave_interval_ms=20,
        recovery_interval_ms=20,
    )
    target = tmp_path / "delayed-auto.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))
    qtbot.wait(50)

    release_workers = Event()

    class BlockingJob(QRunnable):
        def __init__(self, entered: Event) -> None:
            super().__init__()
            self._entered = entered

        def run(self) -> None:
            self._entered.set()
            assert release_workers.wait(10)

    pool = QThreadPool.globalInstance()
    entered = [Event() for _ in range(pool.maxThreadCount())]
    blockers = [BlockingJob(event) for event in entered]
    try:
        for blocker in blockers:
            pool.start(blocker)
        qtbot.waitUntil(lambda: all(event.is_set() for event in entered), timeout=1000)

        autosave_timer_fired = QSignalSpy(controller._autosave_timer.timeout)
        saving_changed = QSignalSpy(controller.savingChanged)
        completed = QSignalSpy(controller.saveSucceeded)
        controller.setBpm(75)
        qtbot.waitUntil(lambda: autosave_timer_fired.count() == 1, timeout=2000)
        qtbot.waitUntil(lambda: saving_changed.count() >= 1, timeout=2000)
        assert release_workers.is_set() is False
        qtbot.waitUntil(lambda: completed.count() == 1, timeout=2000)
        assert release_workers.is_set() is False, (
            f"autosave did not finish (isSaving={controller.isSaving}, "
            f"isDirty={controller.isDirty}, jobs={len(controller._save_jobs)}, "
            f"savePoolActive={controller._save_pool.activeThreadCount()}, "
            f"timerFired={autosave_timer_fired.count() == 1})"
        )
    finally:
        release_workers.set()
        assert pool.waitForDone(2000)

    assert controller.isSaving is False
    assert controller.isDirty is False
    assert json.loads(target.read_text(encoding="utf-8"))["bpm"] == 75


def test_first_edit_does_not_create_a_project_when_autosave_is_disabled(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(
        recovery_store=store,
        project_library=library,
        autosave_interval_ms=30,
        recovery_interval_ms=30,
    )
    controller.setBpm(66)
    qtbot.waitUntil(lambda: store.available, timeout=2_000)
    qtbot.wait(80)
    assert controller.currentFilePath == ""
    assert list(library.root.glob("**/*.pianoai.json")) == []
    assert controller.isDirty is True


def test_changing_autosave_policy_restarts_or_stops_the_idle_timer(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    target = tmp_path / "policy.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2_000):
        controller.saveProject(str(target))
    controller.setBpm(75)
    assert controller._autosave_timer.isActive() is False
    controller.setAutosavePolicy(True, 120)
    assert controller._autosave_timer.interval() == 120_000
    assert controller._autosave_timer.isActive() is True
    controller.setAutosavePolicy(False, 120)
    assert controller._autosave_timer.isActive() is False


@pytest.mark.parametrize("enabled", [False, True])
def test_autosave_policy_change_preserves_existing_crash_recovery(
    tmp_path, enabled
) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    recovered = ProjectState(
        (Note.create(id="recovered", pitch=67, start_time=1.0, duration=0.5),)
    )
    store.write(recovered, "C:/projects/recovered.pianoai.json")
    controller = controller_with_note(recovery_store=store)
    assert controller.isDirty is False
    assert controller.recoveryAvailable is True

    controller.setAutosavePolicy(enabled, 60)

    assert controller.recoveryAvailable is True
    restored = store.read()
    assert restored is not None
    assert restored[0] == recovered
    assert restored[1] == "C:/projects/recovered.pianoai.json"


def test_enabled_autosave_uses_the_default_60_second_idle_delay(qtbot, tmp_path) -> None:
    controller = controller_with_note(autosave_enabled=True)
    target = tmp_path / "default-policy.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2_000):
        controller.saveProject(str(target))
    controller.setBpm(75)
    assert controller._autosave_timer.interval() == 60_000
    assert controller._autosave_timer.isActive() is True


def test_autosave_policy_rejects_fractional_delays(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    target = tmp_path / "fractional-policy.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2_000):
        controller.saveProject(str(target))
    controller.setBpm(75)
    controller.setAutosavePolicy(True, 15.9)
    assert controller._autosave_timer.interval() == 60_000
    assert controller._autosave_timer.isActive() is True
    controller.setAutosavePolicy(True, "15.9")
    assert controller._autosave_timer.interval() == 60_000


def test_recent_entries_expose_card_metadata(qtbot, tmp_path) -> None:
    recent = RecentFiles()
    controller = controller_with_note(recent_files=recent)
    target = tmp_path / "card_song.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))

    entries = controller.recentEntries
    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "card_song"
    assert entry["noteCount"] == 1
    assert entry["durationSeconds"] == 0.5
    assert entry["exists"] is True


def test_remove_recent_updates_entries_and_emits(qtbot, tmp_path) -> None:
    recent = RecentFiles()
    controller = controller_with_note(recent_files=recent)
    target = tmp_path / "to_remove.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))
    assert controller.recentFiles != []

    with qtbot.waitSignal(controller.recentFilesChanged, timeout=500):
        controller.removeRecent(str(target))
    assert controller.recentFiles == []
    assert controller.recentEntries == []


def test_recovery_info_is_exposed_for_welcome_card(qtbot, tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    original = ProjectState((Note.create(id="r", pitch=67, start_time=2.0, duration=1.0),))
    store.write(original, "C:/projects/draft.pianoai.json")

    controller = controller_with_note(recovery_store=store)
    info = controller.recoveryInfo
    assert info["name"] == "draft"
    assert info["noteCount"] == 1
    assert info["durationSeconds"] == 3.0

    controller.discardRecovery()
    assert controller.recoveryInfo == {}


def test_recycling_dirty_current_project_saves_then_recycles(qtbot, tmp_path, monkeypatch) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)
    target = library.next_project_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))
    recycled = []
    monkeypatch.setattr(library, "recycle", lambda path: recycled.append(path))
    controller.setBpm(90)

    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.recycleProject(str(target))

    assert recycled == [target]
    assert controller.currentFilePath != str(target)


def test_recycling_a_url_saved_current_project_uses_canonical_path(qtbot, tmp_path, monkeypatch) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = controller_with_note(project_library=library)
    target = tmp_path / "current.pianoai.json"
    file_url = QUrl.fromLocalFile(str(target)).toString()
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(file_url)
    recycled: list[Path] = []
    monkeypatch.setattr(library, "recycle", lambda path: recycled.append(path))

    controller.recycleProject(str(target))

    assert recycled == [target]
    assert controller.currentFilePath == ""
    assert controller.state.notes == ()


def test_bulk_recycle_reports_progress_and_skips_the_current_project(
    qtbot, tmp_path, monkeypatch
) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    paths = []
    for index in range(3):
        path = library.next_project_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_project(path, ProjectState((Note.create(
            id=f"note-{index}", pitch=60 + index, start_time=0.0, duration=0.5,
        ),)))
        paths.append(path)
    controller = controller_with_note(project_library=library)
    controller._set_current_path(str(paths[1]))
    recycled: list[Path] = []
    monkeypatch.setattr(library, "recycle", recycled.append)

    with qtbot.waitSignal(controller.recycleFinished, timeout=2000) as completion:
        controller.recycleProjects([str(paths[0]), str(paths[1]), str(paths[2]), str(paths[0])])

    assert completion.args == [2, 0]
    assert recycled == [paths[0], paths[2]]
    assert controller.recycleInProgress is False
    assert (controller.recycleProgress, controller.recycleTotal) == (2, 2)


def test_bulk_recycle_blocks_opening_a_queued_project(
    qtbot, tmp_path, monkeypatch
) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    paths = []
    for index in range(2):
        path = library.next_project_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_project(path, ProjectState((Note.create(
            id=f"note-{index}", pitch=60 + index, start_time=0.0, duration=0.5,
        ),)))
        paths.append(path)
    controller = controller_with_note(project_library=library)
    controller._set_current_path(str(paths[1]))
    recycle_started = Event()
    allow_recycle = Event()

    def delayed_recycle(path: Path) -> None:
        recycle_started.set()
        assert allow_recycle.wait(timeout=2)

    monkeypatch.setattr(library, "recycle", delayed_recycle)

    with qtbot.waitSignal(controller.recycleFinished, timeout=2000):
        controller.recycleProjects([str(paths[0])])
        qtbot.waitUntil(recycle_started.is_set, timeout=1000)
        try:
            assert controller.recycleInProgress is True
            controller.openProject(str(paths[0]))
            assert controller.currentFilePath == str(paths[1])
        finally:
            allow_recycle.set()
