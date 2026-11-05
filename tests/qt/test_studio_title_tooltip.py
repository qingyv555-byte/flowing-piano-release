"""Project title tips must not cover the otherwise empty title-bar area."""
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_json import save_project
from tests.qt.test_studio_shell import preserved_qsettings_state, studio_session
from tests.qt.test_studio_workflows import click, qml
from tests.qt.test_studio_workflow_ux import memory_audio


def test_blank_title_bar_click_does_not_show_a_saved_tip_or_save(studio_session, qtbot):
    _app, _engine, root, editor, _playback = studio_session
    root.resize(2200, 900)
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    title = root.findChild(QObject, "studioProjectTitle")
    qtbot.waitUntil(lambda: title.width() > qml(title, "contentWidth") + 80)
    assert not title.property("truncated")
    point = title.mapToScene(QPointF(title.width() - 20, title.height() / 2)).toPoint()
    saves = QSignalSpy(editor.saveSucceeded)
    before, depth, path = editor.state.content, editor.historyUndoDepth, editor.currentFilePath
    QTest.mouseMove(root, point)
    qtbot.waitUntil(lambda: bool(qml(title, "hoveredTitle.hovered")))
    QTest.qWait(650)
    assert not qml(title, "ToolTip.visible"), "空白标题栏悬停不应弹出工程／已保存提示"
    QTest.mouseClick(root, Qt.LeftButton, pos=point)
    QTest.qWait(650)
    assert not qml(title, "ToolTip.visible"), "空白标题栏不应弹出工程／已保存提示"
    assert saves.count() == 0
    assert editor.state.content == before and editor.historyUndoDepth == depth
    assert editor.currentFilePath == path


def test_truncated_title_tip_only_shows_the_full_project_name(studio_session, qtbot, tmp_path):
    _app, _engine, root, editor, _playback = studio_session
    source = tmp_path / ("很长的工程标题" * 10 + ".pianoai.json")
    save_project(source, ProjectState(notes=(Note.create(pitch=64, start_time=0, duration=.5),)))
    editor.openProject(str(source))
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    root.resize(1000, 700)
    root.requestActivate()
    qtbot.waitUntil(root.isActive)
    QTest.qWait(60)
    title = root.findChild(QObject, "studioProjectTitle")
    qtbot.waitUntil(lambda: title.property("truncated"))
    assert qml(title, "ToolTip.text") == editor.currentFileName
    point = title.mapToScene(QPointF(title.width() / 2, title.height() / 2)).toPoint()
    QTest.mouseMove(root, QPointF(400, 300).toPoint(), delay=20)
    QTest.mouseMove(root, point)
    qtbot.waitUntil(lambda: bool(qml(title, "hoveredTitle.hovered")))
    qtbot.waitUntil(lambda: bool(qml(title, "ToolTip.visible")))
    assert qml(title, "ToolTip.text") == editor.currentFileName
    assert "已保存" not in qml(title, "ToolTip.text")
    QTest.mouseMove(root, QPointF(400, 300).toPoint())
    qtbot.waitUntil(lambda: not qml(title, "ToolTip.visible"))
