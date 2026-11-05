"""Actual Qt shell regression; offscreen evidence is not native compositor QA."""
from pathlib import Path
from datetime import datetime
import importlib.util
import os

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from tests.qt.test_nocturne import preserved_qsettings_state
from ai_piano_studio.nocturne import build_nocturne_engine
from ai_piano_studio.__main__ import initialize_deferred_services


@pytest.fixture
def studio_session(qapp, qtbot, tmp_path, qtlog, preserved_qsettings_state, monkeypatch):
    from ai_piano_studio.studio import build_studio_engine

    # UI regressions never consult the developer machine's installed CLI/login.
    monkeypatch.setattr('ai_piano_studio.music_agent.assistant_controller.detect_codex',
                        lambda **kwargs: {"installed": False, "version": "", "login": "", "supported": False, "message": "未找到 Codex CLI。"})

    app, engine, editor, playback, keyboard = build_studio_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioCreateButton") is not None)
    try:
        yield app, engine, root, editor, playback
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
    errors = [r.message for r in qtlog.records if any(marker in r.message for marker in
              ("TypeError", "ReferenceError", "Binding loop", "Cannot assign", "not found"))]
    assert not errors


def click(root, name):
    item = root.findChild(QObject, name)
    assert item is not None and item.isVisible() and item.isEnabled(), name
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    assert 0 <= point.x() < root.width() and 0 <= point.y() < root.height(), name
    QTest.mouseClick(root, Qt.LeftButton, pos=point)


def find_visual_item(root, name):
    pending = [root.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    return None


def test_studio_home_is_idle_and_profile_is_isolated(studio_session, tmp_path):
    app, engine, root, editor, playback = studio_session
    assert root.property("studioMode") is True
    assert root.color().alphaF() == 1.0
    assert editor._projects.root == tmp_path / "Projects"
    assert editor._recovery._directory == tmp_path / "recovery"
    templates = engine.rootContext().contextProperty("templates")
    assert templates._preview_player is None
    assert templates.previewing is False
    assert engine.rootContext().contextProperty("audioVisualizer").enabled is False
    assert root.findChild(QObject, "nocturneHome") is None


def test_studio_create_selection_and_undo_use_real_editor(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1000, 700)
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    timeline = root.findChild(QObject, "timeline")
    qtbot.waitUntil(lambda: timeline.width() > 700)
    before = (timeline.width(), timeline.height(), timeline.mapToScene(QPointF(0, 0)))
    editor.createNoteAt(0, 0)
    assert editor.noteModel.totalCount == 1
    assert editor.canUndo
    assert (timeline.width(), timeline.height(), timeline.mapToScene(QPointF(0, 0))) == before
    click(root, "studioUndo")
    assert editor.noteModel.totalCount == 0
    click(root, "studioRedo")
    assert editor.noteModel.totalCount == 1


def test_theme_preview_rolls_back_without_changing_music(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, "studioAppearanceButton")
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    appearance = engine.rootContext().contextProperty("studioAppearance")
    initial = root.color()
    appearance.setValue("themeId", "ink")
    assert root.color() != initial
    assert root.color().alphaF() == 1.0
    assert editor.isDirty is False
    dialog.close()
    assert root.color() == initial


def test_each_blank_click_creates_a_note_without_dismissing_a_palette(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    canvas = root.findChild(QObject, "drawNoteArea")
    qtbot.waitUntil(lambda: canvas.width() > 800)
    origin = canvas.mapToScene(QPointF(0, 0))
    for count, x in enumerate((180, 260, 340), start=1):
        QTest.mouseClick(root, Qt.LeftButton, pos=(origin + QPointF(x, 180)).toPoint())
        assert editor.noteModel.totalCount == count
    assert root.findChild(QObject, "selectionActions").property("expanded") is False
    for count in (2, 1, 0):
        click(root, "studioUndo")
        assert editor.noteModel.totalCount == count


def test_studio_ignores_legacy_focus_mode_and_keeps_transport_opaque(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    appearance = engine.rootContext().contextProperty("studioAppearance")
    appearance.beginPreview()
    appearance.setValue("themeId", "paper")
    appearance.applyPreview()
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert root.color().name() == "#e8edf0"
    click(root, "studioViewMenuButton")
    menu = root.findChild(QObject, "studioViewMenu")
    qtbot.waitUntil(lambda: menu.property("opened"))
    expression = QQmlExpression(QQmlEngine.contextForObject(menu), menu, "itemAt(0)")
    item, undefined = expression.evaluate()
    assert not expression.hasError() and not undefined
    assert item.property("text") == "跟随播放"
    QTest.keyClick(root, Qt.Key_Escape)
    experience = engine.rootContext().contextProperty("experience")
    experience.setFocusMode(True)
    assert not root.findChild(QObject, "workspaceShell").property("focusMode")
    QTest.keyClick(root, Qt.Key_F, Qt.ControlModifier | Qt.ShiftModifier)
    assert not root.property("focusMode")
    assert not root.findChild(QObject, "focusModeShortcut").property("enabled")
    transport = root.findChild(QObject, "bottomTransportBar")
    assert transport.isVisible()
    assert transport.opacity() == 1.0


def test_recent_work_displays_recorded_last_opened_time(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    with qtbot.waitSignal(editor.saveSucceeded, timeout=5000):
        assert editor.saveNamedProject("近期时间回归")
    recorded = editor.recentEntries[0]
    assert Path(recorded["path"]).is_file()
    # Modification time is distinct from the recorded recent-open event.
    os.utime(recorded["path"], (946684800, 946684800))
    editor._invalidate_library_entries()
    editor.recentFilesChanged.emit()
    expected = datetime.fromisoformat(recorded["lastOpened"]).astimezone().strftime("%m-%d %H:%M")
    qtbot.waitUntil(lambda: find_visual_item(root, "studioRecentMetadata-0") is not None)
    label = find_visual_item(root, "studioRecentMetadata-0")
    qtbot.waitUntil(lambda: label.isVisible())
    assert label.property("text") == "钢琴工程 · 打开于 " + expected


def test_window_has_an_opaque_floor(qapp, qtbot, tmp_path, preserved_qsettings_state):
    app, engine, editor, playback, keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    try:
        assert root.color().alphaF() == 1.0
        assert root.opacity() == 1.0
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


def test_studio_has_an_independent_entry():
    assert importlib.util.find_spec("ai_piano_studio.studio") is not None


def test_creative_playback_yields_while_studio_prepares_samples(studio_session, monkeypatch):
    from ai_piano_studio.domain.note import Note
    app, engine, root, editor, playback = studio_session
    creative = engine.rootContext().contextProperty('creative')
    monkeypatch.setattr(playback._player, 'is_preparing', lambda: True)
    starts = []
    monkeypatch.setattr(creative._arbiter, '_start', lambda *args: starts.append(args))
    lease = creative._arbiter.play('preview', (Note.create(pitch=60, start_time=0, duration=.5),))
    assert lease is None
    assert not starts


def test_creative_window_is_opt_in_and_does_not_touch_main_project(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    creative = engine.rootContext().contextProperty('creative')
    before = editor.state
    assert creative.assistant is None
    creative.open()
    qtbot.waitUntil(lambda: any(w.objectName() == 'creativeWindow' and w.isVisible() for w in app.allWindows()))
    window = next(w for w in app.allWindows() if w.objectName() == 'creativeWindow')
    assert creative.assistant is not None
    assert creative.companionState == 'off'
    assert editor.state is before
    assert not playback.playing
    evidence = os.environ.get('PIANO_CREATIVE_EVIDENCE')
    if evidence:
        directory = Path(evidence)
        directory.mkdir(parents=True, exist_ok=True)
        qtbot.wait(100)
        assert window.grabWindow().save(str(directory / 'creative-default.png'))
        window.setWidth(640)
        window.setHeight(600)
        qtbot.wait(100)
        assert window.grabWindow().save(str(directory / 'creative-small.png'))
        scroll = window.findChild(QObject, 'creativeScroll')
        flickable = scroll.property('contentItem')
        flickable.setProperty('contentY', 570)
        qtbot.wait(100)
        assert window.grabWindow().save(str(directory / 'creative-actions.png'))
        flickable.setProperty('contentY', flickable.property('contentHeight') - flickable.property('height'))
        qtbot.wait(100)
        assert window.grabWindow().save(str(directory / 'creative-memory.png'))
    window.close()
    assert creative.companionState == 'paused'
