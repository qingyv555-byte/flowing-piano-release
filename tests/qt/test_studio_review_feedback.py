"""User review regressions, real offscreen UI with temporary projects."""
from PySide6.QtCore import QObject, QPointF

from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_workflows import (
    no_audio_device, click, find_item, view_menu, create_notes, submit_name,
)
from ai_piano_studio.__main__ import initialize_deferred_services


def test_requested_panels_and_note_actions_remain_available_without_focus_mode(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    create_notes(root, editor, qtbot, 1)
    before, depth = editor.state.content, editor.historyUndoDepth
    experience = engine.rootContext().contextProperty("experience")
    experience.setFocusMode(True)
    assert not root.property("focusMode")
    click(root, "studioArrangement")
    panel = root.findChild(QObject, "arrangementPanel")
    assert panel.isVisible(), "Focus must not reject the explicit show-parts request"
    click(root, "studioInspector")
    inspector = root.findChild(QObject, "inspector")
    assert inspector.property("panelExpanded") and inspector.isVisible()
    assert not root.property("focusMode")
    # The selection palette can still be explicitly opened after note creation.
    toggle = root.findChild(QObject, "editorActionRailCollapsedToggle")
    assert toggle.isVisible() and toggle.isEnabled()
    click(root, "editorActionRailCollapsedToggle")
    assert root.findChild(QObject, "selectionActions").property("expanded")
    click(root, "studioArrangement")
    assert not panel.isVisible()
    assert not root.property("focusMode")
    assert inspector.isVisible() and not panel.isVisible()
    assert editor.state.content == before and editor.historyUndoDepth == depth


def test_template_back_is_explicit_and_preserves_unsaved_work(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, "studioTemplatesButton")
    templates = engine.rootContext().contextProperty("templates")
    identifier = templates.templates[0]["id"]
    qtbot.waitUntil(lambda: find_item(root, "templateAdaptButton-" + identifier) is not None)
    click(root, "templateAdaptButton-" + identifier)
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    button = find_item(root, "studioHomeNav")
    assert button.property("text") == "返回示例"
    editor.createNoteAt(250, 180)
    before = editor.state.content
    click(root, "studioHomeNav")
    dialog = root.findChild(QObject, "unsavedExitDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    click(root, "unsavedExitCancelAction")
    assert root.property("pageState") == "editor" and editor.state.content == before
    click(root, "studioSave")
    qtbot.waitUntil(lambda: not editor.isSaving and not editor.isDirty)
    click(root, "studioHomeNav")
    qtbot.waitUntil(lambda: root.property("pageState") == "templates")
    click(root, "templateLibraryBackButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "launch")


def test_library_scrollbar_only_shows_overflow_at_window_edge(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(2200, 900)
    create_notes(root, editor, qtbot, 1)
    click(root, "studioSave")
    submit_name(root, editor, qtbot, "滚动条回归")
    click(root, "studioHomeNav")
    click(root, "studioLibraryButton")
    listing = root.findChild(QObject, "recentList")
    qtbot.waitUntil(lambda: listing.isVisible() and listing.property("count") == 1)
    from tests.qt.test_studio_workflows import qml
    scrollbar = qml(listing, "ScrollBar.vertical")
    assert not scrollbar.isVisible(), "A single fitting row must not show a full-height thumb"
    library = root.findChild(QObject, "welcomeLibraryView")
    # Repeated actual library projections exercise a full page without user files.
    entries = editor.libraryEntries
    library.setProperty("entries", entries * 20)
    qtbot.waitUntil(lambda: listing.property("contentHeight") > listing.height())
    assert scrollbar.isVisible()
    right = scrollbar.mapToScene(QPointF(scrollbar.width(), 0)).x()
    assert 12 <= root.width() - right <= 24
    from tests.qt.test_studio_workflows import click_item
    click_item(root, scrollbar, y=scrollbar.height() - 20)
    qtbot.waitUntil(lambda: listing.property("contentY") > 0)
    root.resize(1000, 640)
    qtbot.waitUntil(lambda: listing.width() == 1000)
    right = scrollbar.mapToScene(QPointF(scrollbar.width(), 0)).x()
    assert 12 <= root.width() - right <= 24
    library.setProperty("entries", entries)
    qtbot.waitUntil(lambda: listing.property("contentHeight") < listing.height())
    assert not scrollbar.isVisible()
