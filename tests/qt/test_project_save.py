import json
from gc import collect
from threading import Event
from weakref import ref

from PySide6.QtTest import QSignalSpy

from ai_piano_studio.editor import controller as controller_module
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata
from ai_piano_studio.persistence.project_json import save_project


def controller_with_note(**kwargs) -> EditorController:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    return EditorController(ProjectState((note,), frozenset({"a"})), **kwargs)


def block_project_document_writes(monkeypatch, *, written: Event | None = None):
    """Pause real background writes at a deterministic point for lifecycle tests."""
    entered = Event()
    release = Event()
    save_document = controller_module.save_project_document

    def wait_then_save(path, document) -> None:
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test did not release the background save")
        save_document(path, document)
        if written is not None:
            written.set()

    monkeypatch.setattr(controller_module, "save_project_document", wait_then_save)
    return entered, release


def test_edit_marks_dirty_and_async_save_clears_it(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    controller.setSelectedProperty("velocity", 90)
    assert controller.isDirty is True
    target = tmp_path / "saved.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))
    assert controller.isSaving is False
    assert controller.isDirty is False
    assert json.loads(target.read_text(encoding="utf-8"))["notes"][0]["velocity"] == 90


def test_save_failure_emits_signal_and_preserves_dirty_state(qtbot, tmp_path) -> None:
    controller = controller_with_note()
    controller.setSelectedProperty("velocity", 90)
    failed = QSignalSpy(controller.saveFailed)
    with qtbot.waitSignal(controller.saveFailed, timeout=2000):
        controller.saveProject(str(tmp_path / "missing" / "saved.pianoai.json"))
    assert failed.count() == 1
    assert controller.isSaving is False
    assert controller.isDirty is True


def test_edit_during_an_active_autosave_triggers_a_follow_up_save(qtbot, tmp_path, monkeypatch) -> None:
    """An older frozen snapshot must not leave a later edit dirty on disk."""
    controller = controller_with_note(autosave_enabled=True)
    controller._autosave_timer.setInterval(20)
    target = tmp_path / "active-autosave.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))

    entered, release = block_project_document_writes(monkeypatch)
    completed = QSignalSpy(controller.saveSucceeded)
    controller.setBpm(75)
    try:
        qtbot.waitUntil(entered.is_set, timeout=1000)
        assert controller.isSaving is True

        controller.setBpm(80)
        release.set()
        qtbot.waitUntil(lambda: completed.count() == 2, timeout=2000)
    finally:
        release.set()

    assert controller.isSaving is False
    assert controller.isDirty is False
    assert json.loads(target.read_text(encoding="utf-8"))["bpm"] == 80


def test_edit_during_pending_project_open_is_saved_before_navigation(
    qtbot, tmp_path, monkeypatch
) -> None:
    current = tmp_path / "current.pianoai.json"
    target = tmp_path / "next.pianoai.json"
    save_project(target, ProjectState((Note.create(id="next", pitch=67, start_time=1.0, duration=0.5),)))
    controller = controller_with_note()
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(current))

    entered = [Event(), Event()]
    releases = [Event(), Event()]
    original = controller_module.save_project_document
    calls = []

    def wait_then_save(path, document) -> None:
        index = len(calls)
        calls.append(index)
        entered[index].set()
        if not releases[index].wait(2):
            raise TimeoutError(f"test did not release pending-open save {index + 1}")
        original(path, document)

    monkeypatch.setattr(controller_module, "save_project_document", wait_then_save)
    controller.setBpm(90)
    controller.openProject(str(target))
    qtbot.waitUntil(entered[0].is_set, timeout=1000)

    controller.setBpm(100)
    releases[0].set()
    qtbot.waitUntil(entered[1].is_set, timeout=2000)
    assert controller.currentFilePath == str(current)
    assert controller.isDirty is True

    releases[1].set()
    qtbot.waitUntil(lambda: controller.currentFilePath == str(target), timeout=2000)
    assert controller.state.notes[0].id == "next"
    assert controller.isDirty is False
    assert json.loads(current.read_text(encoding="utf-8"))["bpm"] == 100


def test_stale_save_completion_does_not_rebind_a_replaced_project(
    qtbot, tmp_path, monkeypatch
) -> None:
    """A save from an older project session cannot update the replacement UI."""
    controller = controller_with_note(autosave_enabled=True)
    controller._autosave_timer.setInterval(20)
    old_path = tmp_path / "old.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(old_path))

    entered, release = block_project_document_writes(monkeypatch)
    stale_saves = QSignalSpy(controller.saveSucceeded)
    controller.setBpm(75)
    qtbot.waitUntil(entered.is_set, timeout=1000)

    replacement = ProjectState((Note.create(id="new", pitch=67, start_time=1.0, duration=0.5),))
    replacement_path = tmp_path / "replacement.pianoai.json"
    metadata = HarmonyProjectMetadata.empty()
    controller._replace_project(
        replacement,
        saved_content=replacement.content,
        path=str(replacement_path),
        harmony_metadata=metadata,
        saved_harmony_metadata=metadata,
    )
    try:
        release.set()
        qtbot.waitUntil(lambda: not controller.isSaving, timeout=2000)
        qtbot.wait(50)
    finally:
        release.set()

    assert stale_saves.count() == 0
    assert controller.currentFilePath == str(replacement_path)
    assert [note.id for note in controller.state.notes] == ["new"]
    assert controller.isDirty is False
    assert json.loads(old_path.read_text(encoding="utf-8"))["bpm"] == 75
    assert replacement_path.exists() is False


def test_destroying_a_controller_with_a_running_save_drops_its_completion(
    qtbot, tmp_path, monkeypatch
) -> None:
    """An active save owns its pool and completes after its controller is gone."""
    controller = controller_with_note(autosave_enabled=True)
    controller._autosave_timer.setInterval(20)
    target = tmp_path / "closed-controller.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))

    written = Event()
    entered, release = block_project_document_writes(monkeypatch, written=written)
    destroyed = QSignalSpy(controller.destroyed)
    pool_ref = ref(controller._save_pool)
    controller.setBpm(75)
    qtbot.waitUntil(entered.is_set, timeout=1000)

    try:
        controller.deleteLater()
        assert destroyed.wait(1000)
        del destroyed
        del controller
        collect()
        assert pool_ref() is not None
        release.set()
        qtbot.waitUntil(written.is_set, timeout=1000)
    finally:
        release.set()

    assert json.loads(target.read_text(encoding="utf-8"))["bpm"] == 75


def test_failed_save_cancels_a_pending_project_open(qtbot, tmp_path, monkeypatch) -> None:
    current = tmp_path / "current.pianoai.json"
    target = tmp_path / "next.pianoai.json"
    save_project(target, ProjectState((Note.create(id="next", pitch=65, start_time=1.0, duration=0.5),)))
    controller = controller_with_note()
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(current))
    controller.setBpm(90)

    def fail_save(*_args) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(controller_module, "save_project_document", fail_save)
    with qtbot.waitSignal(controller.saveFailed, timeout=2000):
        controller.openProject(str(target))

    assert controller.currentFilePath == str(current)
    assert [note.id for note in controller.state.notes] == ["a"]
    assert controller._pending_open_path is None
