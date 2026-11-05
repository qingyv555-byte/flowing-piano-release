from pathlib import Path
from threading import Event, Lock

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QSettings, Qt
from PySide6.QtGui import QAccessible, QColor
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor import controller as controller_module
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_json import load_project, save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles
from ai_piano_studio.persistence.save_preferences import SavePreferences
from tests.qt.test_project_save import block_project_document_writes


def _settings(tmp_path: Path, name: str) -> QSettings:
    settings = QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _load_editor(tmp_path: Path, *, saved_path: Path | None = None):
    projects = ProjectLibrary(tmp_path / "projects")
    editor = EditorController(
        ProjectState(),
        recent_files=RecentFiles(settings=_settings(tmp_path, "recent")),
        project_library=projects,
        recovery_store=RecoveryStore(tmp_path / "recovery"),
    )
    if saved_path is not None:
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        save_project(saved_path, ProjectState())
        editor.loadProject(str(saved_path))

    experience = ExperienceSettings(settings=_settings(tmp_path, "experience"))
    experience.completeFirstRun()
    harmony = HarmonySettings(settings=_settings(tmp_path, "harmony"))
    preferences = SavePreferences(settings=_settings(tmp_path, "save"))
    app, engine, editor, _playback, _keyboard = build_engine(
        controller=editor,
        experience_settings=experience,
        harmony_settings=harmony,
        save_preferences=preferences,
        appearance_settings=_settings(tmp_path, "appearance"),
    )
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    root.requestActivate()
    app.processEvents()
    app.processEvents()
    assert preferences.autosaveEnabled is False
    return engine, root, editor


def _load_saved_editor(tmp_path: Path):
    target = tmp_path / "projects" / "project-001" / "project.pianoai.json"
    engine, root, editor = _load_editor(tmp_path, saved_path=target)
    assert editor.currentFilePath == str(target.resolve())
    assert editor.state.tempo.bpm == 120
    assert editor.isDirty is False
    return engine, root, editor


def _request_library(root: QObject) -> QObject:
    opener = root.findChild(QObject, "backToLibraryButton")
    assert opener is not None
    opener.clicked.emit()
    return opener


def _open_dirty_exit(root: QObject, editor: EditorController, qapp):
    editor.setBpm(90)
    opener = _request_library(root)
    dialog = root.findChild(QObject, "unsavedExitDialog")
    assert dialog is not None
    qapp.processEvents()
    return opener, dialog


def _block_sequential_project_document_writes(monkeypatch, count: int):
    entered = [Event() for _ in range(count)]
    releases = [Event() for _ in range(count)]
    original = controller_module.save_project_document
    next_index = 0
    index_lock = Lock()

    def wait_then_save(path, document) -> None:
        nonlocal next_index
        with index_lock:
            index = next_index
            next_index += 1
        if index >= count:
            raise AssertionError("unexpected extra project save")
        entered[index].set()
        if not releases[index].wait(2):
            raise TimeoutError(f"test did not release save {index + 1}")
        original(path, document)

    monkeypatch.setattr(controller_module, "save_project_document", wait_then_save)
    return entered, releases


def test_clean_library_request_navigates_without_a_dialog(qapp, tmp_path) -> None:
    engine, root, editor = _load_editor(tmp_path)

    _request_library(root)
    qapp.processEvents()

    assert root.property("pageState") == "library"
    assert root.findChild(QObject, "unsavedExitDialog").property("opened") is False
    root.close()


def test_selecting_current_project_from_library_returns_to_editor_without_reload(
    qapp, tmp_path
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    current_path = editor.currentFilePath
    _request_library(root)
    qapp.processEvents()
    assert root.property("pageState") == "library"
    Path(current_path).unlink()
    welcome = root.findChild(QObject, "welcomeOverlay")

    assert QMetaObject.invokeMethod(
        welcome,
        "recentChosen",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QString", current_path),
    )
    qapp.processEvents()

    assert root.property("pageState") == "editor"
    assert editor.currentFilePath == current_path
    assert editor.isDirty is False
    root.close()


def test_dirty_library_request_opens_dialog_and_escape_restores_focus(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    opener = root.findChild(QObject, "backToLibraryButton")
    opener.forceActiveFocus()
    editor.setBpm(90)

    opener.clicked.emit()
    dialog = root.findChild(QObject, "unsavedExitDialog")
    cancel = root.findChild(QObject, "unsavedExitCancelAction")
    qtbot.waitUntil(lambda: dialog.property("opened"))

    assert cancel.property("activeFocus") is True
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert root.property("pageState") == "editor"
    assert editor.isDirty is True
    assert opener.property("activeFocus") is True

    with qtbot.waitSignal(editor.saveSucceeded, timeout=2_000):
        editor.saveProject(editor.currentFilePath)
    assert root.property("pageState") == "editor"
    root.close()


def test_unsaved_exit_dialog_uses_exact_copy_theme_and_accessible_actions(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor = _load_editor(tmp_path)
    _, dialog = _open_dirty_exit(root, editor, qapp)
    qtbot.waitUntil(lambda: dialog.property("opened"))

    title = dialog.property("header")
    message = dialog.property("contentItem")
    theme = root.property("appTheme")
    assert title.property("text") == "保存对工程的修改吗？"
    assert message.property("text") == "不保存会丢弃从上次保存后所做的修改。"
    assert QColor(title.property("color")) == QColor(theme.property("textPrimary"))
    assert QColor(message.property("color")) == QColor(theme.property("textSecondary"))

    for object_name, accessible_name in (
        ("unsavedExitCancelAction", "取消"),
        ("unsavedExitDiscardAction", "不保存"),
        ("unsavedExitSaveAction", "保存"),
    ):
        action = root.findChild(QObject, object_name)
        interface = QAccessible.queryAccessibleInterface(action)
        assert interface is not None
        assert interface.text(QAccessible.Text.Name) == accessible_name
    root.findChild(QObject, "unsavedExitCancelAction").clicked.emit()
    root.close()


def test_dialog_tabs_and_backtabs_in_visual_order_and_enter_activates_cancel(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor = _load_editor(tmp_path)
    opener, dialog = _open_dirty_exit(root, editor, qapp)
    cancel = root.findChild(QObject, "unsavedExitCancelAction")
    discard = root.findChild(QObject, "unsavedExitDiscardAction")
    save = root.findChild(QObject, "unsavedExitSaveAction")
    qtbot.waitUntil(lambda: cancel.property("activeFocus"))

    for expected in (discard, save, cancel):
        QTest.keyClick(root, Qt.Key_Tab)
        qapp.processEvents()
        assert expected.property("activeFocus") is True
    for expected in (save, discard, cancel):
        QTest.keyClick(root, Qt.Key_Backtab)
        qapp.processEvents()
        assert expected.property("activeFocus") is True

    QTest.keyClick(root, Qt.Key_Return)
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert root.property("pageState") == "editor"
    assert editor.isDirty is True
    assert opener.property("activeFocus") is True
    root.close()


def test_space_activates_the_focused_discard_action(qapp, qtbot, tmp_path) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    _, dialog = _open_dirty_exit(root, editor, qapp)
    discard = root.findChild(QObject, "unsavedExitDiscardAction")
    discard.forceActiveFocus()

    QTest.keyClick(root, Qt.Key_Space)
    qtbot.waitUntil(lambda: root.property("pageState") == "library")

    assert dialog.property("opened") is False
    assert editor.isDirty is False
    assert editor.state.tempo.bpm == 120
    root.close()


def test_discard_action_reloads_saved_content_before_navigating(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    editor.setBpm(90)
    _request_library(root)

    root.findChild(QObject, "unsavedExitDiscardAction").clicked.emit()
    qtbot.waitUntil(lambda: root.property("pageState") == "library")

    assert editor.state.tempo.bpm == 120
    assert editor.isDirty is False
    root.close()


def test_discarding_an_unnamed_draft_creates_no_project_file(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor = _load_editor(tmp_path)
    editor.setBpm(90)
    _request_library(root)

    root.findChild(QObject, "unsavedExitDiscardAction").clicked.emit()
    qtbot.waitUntil(lambda: root.property("pageState") == "library")

    assert editor.currentFilePath == ""
    assert editor.isDirty is False
    assert list((tmp_path / "projects").rglob("*.pianoai.json")) == []
    root.close()


def test_save_action_navigates_only_after_save_success(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    entered, release = block_project_document_writes(monkeypatch)
    editor.setBpm(90)
    _request_library(root)

    root.findChild(QObject, "unsavedExitSaveAction").clicked.emit()
    qtbot.waitUntil(entered.is_set)
    assert root.property("pageState") == "editor"
    assert root.property("libraryExitSavePending") is True

    release.set()
    qtbot.waitUntil(lambda: root.property("pageState") == "library")
    assert editor.isDirty is False
    assert load_project(editor.currentFilePath).tempo.bpm == 90
    root.close()


def test_editing_during_first_save_queues_latest_snapshot_before_navigation(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    entered, releases = _block_sequential_project_document_writes(monkeypatch, 2)
    editor.setBpm(90)
    _request_library(root)
    root.findChild(QObject, "unsavedExitSaveAction").clicked.emit()
    qtbot.waitUntil(entered[0].is_set)

    editor.setBpm(100)
    releases[0].set()
    qtbot.waitUntil(entered[1].is_set)

    assert root.property("pageState") == "editor"
    assert root.property("pendingLibraryExitPage") == "library"
    assert root.property("libraryExitSavePending") is True
    assert editor.isDirty is True
    assert load_project(editor.currentFilePath).tempo.bpm == 90

    releases[1].set()
    qtbot.waitUntil(lambda: root.property("pageState") == "library")
    assert editor.isDirty is False
    assert load_project(editor.currentFilePath).tempo.bpm == 100
    root.close()


def test_unnamed_save_dialog_reject_clears_navigation_and_later_save_stays_put(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor = _load_editor(tmp_path)
    opener, dialog = _open_dirty_exit(root, editor, qapp)

    root.findChild(QObject, "unsavedExitSaveAction").clicked.emit()
    save_dialog = root.findChild(QObject, "projectNameDialog")
    qtbot.waitUntil(lambda: save_dialog.property("opened"))
    assert root.property("pageState") == "editor"

    assert QMetaObject.invokeMethod(
        save_dialog, "reject", Qt.ConnectionType.DirectConnection
    )
    qtbot.waitUntil(lambda: not save_dialog.property("opened"))
    assert root.property("pendingLibraryExitPage") == ""
    assert root.property("libraryExitSavePending") is False
    assert root.property("pageState") == "editor"
    assert editor.isDirty is True
    assert opener.property("activeFocus") is True

    later_path = tmp_path / "later.pianoai.json"
    with qtbot.waitSignal(editor.saveSucceeded, timeout=2_000):
        editor.saveProject(str(later_path))
    assert root.property("pageState") == "editor"
    root.close()


def test_external_save_choice_uses_internal_surface_and_cancel_preserves_dirty_state(
    qapp, qtbot, tmp_path
) -> None:
    _engine, root, editor = _load_editor(
        tmp_path, saved_path=tmp_path / "outside" / "external.pianoai.json"
    )
    editor.setBpm(90)
    editor.saveCurrent()
    choice = root.findChild(QObject, "externalSaveChoiceDialog")
    library_action = root.findChild(QObject, "externalSaveLibraryButton")
    assert choice is not None
    assert library_action is not None
    qtbot.waitUntil(lambda: choice.property("opened"))
    qtbot.waitUntil(lambda: library_action.property("activeFocus"))
    assert root.findChild(QObject, "saveDialog") is None

    assert QMetaObject.invokeMethod(
        choice, "reject", Qt.ConnectionType.DirectConnection
    )
    qtbot.waitUntil(lambda: not choice.property("opened"))
    assert editor.isDirty is True
    root.close()


def test_save_failure_clears_stale_navigation_and_retry_reestablishes_it(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    original = controller_module.save_project_document
    failed = Event()

    def fail_write(_path, _document) -> None:
        failed.set()
        raise OSError("disk unavailable")

    monkeypatch.setattr(controller_module, "save_project_document", fail_write)
    editor.setBpm(90)
    _request_library(root)
    root.findChild(QObject, "unsavedExitSaveAction").clicked.emit()
    qtbot.waitUntil(failed.is_set)
    qtbot.waitUntil(lambda: not editor.isSaving)

    host = root.findChild(QObject, "feedbackHost")
    assert root.property("pageState") == "editor"
    assert editor.isDirty is True
    assert root.property("pendingLibraryExitPage") == ""
    assert root.property("libraryExitSavePending") is False
    assert host.property("kind") == "danger"
    assert host.property("actionLabel") == "重试"

    retry_entered = Event()
    retry_release = Event()

    def block_retry(path, document) -> None:
        retry_entered.set()
        if not retry_release.wait(2):
            raise TimeoutError("test did not release retry save")
        original(path, document)

    monkeypatch.setattr(controller_module, "save_project_document", block_retry)
    assert QMetaObject.invokeMethod(
        host, "triggerAction", Qt.ConnectionType.DirectConnection
    )
    qtbot.waitUntil(retry_entered.is_set)
    assert root.property("pendingLibraryExitPage") == "library"
    assert root.property("libraryExitSavePending") is True
    assert root.property("pageState") == "editor"

    retry_release.set()
    qtbot.waitUntil(lambda: root.property("pageState") == "library")
    root.close()


def test_save_failure_does_not_let_an_unrelated_later_save_navigate(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    original = controller_module.save_project_document
    failed = Event()

    def fail_write(_path, _document) -> None:
        failed.set()
        raise OSError("disk unavailable")

    monkeypatch.setattr(controller_module, "save_project_document", fail_write)
    editor.setBpm(90)
    _request_library(root)
    root.findChild(QObject, "unsavedExitSaveAction").clicked.emit()
    qtbot.waitUntil(failed.is_set)
    qtbot.waitUntil(lambda: not editor.isSaving)

    monkeypatch.setattr(controller_module, "save_project_document", original)
    with qtbot.waitSignal(editor.saveSucceeded, timeout=2_000):
        editor.saveProject(editor.currentFilePath)

    assert editor.isDirty is False
    assert root.property("pageState") == "editor"
    root.close()


def test_ordinary_save_failure_does_not_consume_an_unrelated_surface_opener(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    opener = root.findChild(QObject, "moreMenuButton")
    popup = root.findChild(QObject, "backgroundSettingsDialog")
    opener.forceActiveFocus()
    root.openSurface(popup, opener)
    qtbot.waitUntil(lambda: popup.property("opened"))

    def fail_write(_path, _document) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(controller_module, "save_project_document", fail_write)
    editor.setBpm(90)
    with qtbot.waitSignal(editor.saveFailed, timeout=2_000):
        editor.saveProject(editor.currentFilePath)

    assert popup.property("opened") is True
    assert root.property("surfaceFocusReturnTarget") == opener
    popup.close()
    qtbot.waitUntil(lambda: not popup.property("opened"))
    assert opener.property("activeFocus") is True
    root.close()


def test_unrelated_save_failure_while_dialog_is_open_preserves_its_target(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    _, dialog = _open_dirty_exit(root, editor, qapp)
    assert root.property("pendingLibraryExitPage") == "library"
    assert root.property("libraryExitSavePending") is False

    def fail_write(_path, _document) -> None:
        raise OSError("autosave disk unavailable")

    monkeypatch.setattr(controller_module, "save_project_document", fail_write)
    with qtbot.waitSignal(editor.saveFailed, timeout=2_000):
        editor.saveProject(editor.currentFilePath)

    assert dialog.property("opened") is True
    assert root.property("pendingLibraryExitPage") == "library"
    assert root.property("libraryExitSavePending") is False
    root.findChild(QObject, "unsavedExitDiscardAction").clicked.emit()
    qtbot.waitUntil(lambda: root.property("pageState") == "library")
    assert editor.isDirty is False
    root.close()


def test_return_request_during_save_stays_in_editor_with_feedback(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_saved_editor(tmp_path)
    entered, release = block_project_document_writes(monkeypatch)
    editor.setBpm(90)
    editor.saveProject(editor.currentFilePath)
    qtbot.waitUntil(entered.is_set)

    root.setProperty("templateEditorMode", True)
    qapp.processEvents()
    opener = root.findChild(QObject, "templateEditorBackButton")
    opener.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)

    host = root.findChild(QObject, "feedbackHost")
    assert root.property("pageState") == "editor"
    assert root.findChild(QObject, "unsavedExitDialog").property("opened") is False
    assert host.property("kind") == "warning"
    assert host.property("message") != ""
    release.set()
    qtbot.waitUntil(lambda: not editor.isSaving)
    root.close()


def test_return_request_during_recycle_stays_in_editor_with_feedback(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    engine, root, editor = _load_editor(tmp_path)
    library = editor._projects
    target = library.next_project_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    save_project(target, ProjectState())
    entered = Event()
    release = Event()
    original = library.recycle

    def delayed_recycle(path: Path) -> None:
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test did not release project recycle")
        original(path)

    monkeypatch.setattr(library, "recycle", delayed_recycle)
    editor.recycleProjects([str(target)])
    qtbot.waitUntil(entered.is_set)
    assert editor.recycleInProgress is True

    root.setProperty("templateEditorMode", True)
    qapp.processEvents()
    opener = root.findChild(QObject, "templateEditorBackButton")
    opener.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)

    host = root.findChild(QObject, "feedbackHost")
    assert root.property("pageState") == "editor"
    assert root.findChild(QObject, "unsavedExitDialog").property("opened") is False
    assert host.property("kind") == "warning"
    assert host.property("message") != ""
    release.set()
    qtbot.waitUntil(lambda: not editor.recycleInProgress)
    root.close()


@pytest.mark.parametrize(
    (
        "opener_name", "template_mode", "target_page", "window_width",
        "compact_state", "expand_template_rail",
    ),
    (
        ("backToLibraryButton", False, "library", 1600, False, False),
        ("templateBackButton", True, "launch", 1600, False, True),
        ("templateCompactBackButton", True, "launch", 1280, True, False),
        ("templateEditorBackButton", True, "templates", 1600, True, False),
    ),
)
def test_every_return_entry_passes_its_actual_opener_to_the_safe_path(
    qapp, qtbot, tmp_path, opener_name, template_mode, target_page,
    window_width, compact_state, expand_template_rail
) -> None:
    engine, root, editor = _load_editor(tmp_path)
    root.setProperty("width", window_width)
    root.setProperty("templateEditorMode", template_mode)
    qapp.processEvents()
    editor.setBpm(90)
    opener = root.findChild(QObject, opener_name)
    assert opener is not None
    rail = root.findChild(QObject, "projectRail")
    if expand_template_rail:
        expand = root.findChild(QObject, "templateCompactRailToggle")
        assert rail.property("compact") is True
        assert expand.property("visible") is True
        assert expand.property("enabled") is True
        expand.forceActiveFocus(Qt.TabFocusReason)
        assert expand.property("activeFocus") is True
        QTest.keyClick(root, Qt.Key_Return)
        qtbot.waitUntil(lambda: rail.property("compact") is False)
        collapse = root.findChild(QObject, "projectRailToggle")
        assert collapse.property("visible") is True
        root.setProperty("width", 1280)
        qapp.processEvents()
        assert rail.property("compact") is True
        assert collapse.property("visible") is False
        root.setProperty("width", window_width)
        qapp.processEvents()
        # Responsive pressure must not unexpectedly reopen a panel after the
        # user has returned to a wider window. Reopen through the explicit
        # affordance and verify that path remains keyboard operable.
        assert rail.property("compact") is True
        expand = root.findChild(QObject, "templateCompactRailToggle")
        expand.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(root, Qt.Key_Return)
        qtbot.waitUntil(lambda: rail.property("compact") is False)
    assert rail.property("compact") is compact_state
    if template_mode:
        full_return = root.findChild(QObject, "templateBackButton")
        compact_return = root.findChild(QObject, "templateCompactBackButton")
        assert full_return.property("visible") is (not compact_state)
        assert compact_return.property("visible") is compact_state
    assert opener.property("visible") is True
    assert opener.property("enabled") is True
    opener.forceActiveFocus(Qt.TabFocusReason)
    assert opener.property("activeFocus") is True

    QTest.keyClick(root, Qt.Key_Return)
    dialog = root.findChild(QObject, "unsavedExitDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))

    assert root.property("pendingLibraryExitPage") == target_page
    assert root.property("surfaceFocusReturnTarget") == opener
    root.findChild(QObject, "unsavedExitCancelAction").clicked.emit()
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert root.property("pageState") == "editor"
    root.close()
