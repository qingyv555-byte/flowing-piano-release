"""Production Nocturne routing, data isolation and stable editor geometry."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlExpression

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.nocturne import build_nocturne_engine
from ai_piano_studio.persistence.project_document import load_project_document
from tools.studio_evidence import install_memory_output


def snapshot_ini_paths():
    return {
        scope: Path(
            QSettings(
                QSettings.IniFormat,
                scope,
                "AIPianoStudioNocturneTestSentinel",
                "SettingsPathProbe",
            ).fileName()
        ).parents[1]
        for scope in (QSettings.UserScope, QSettings.SystemScope)
    }


@pytest.fixture
def preserved_qsettings_state(qapp):
    old_format = QSettings.defaultFormat()
    old_org, old_app = qapp.organizationName(), qapp.applicationName()
    old_ini_paths = snapshot_ini_paths()
    try:
        yield
    finally:
        for scope, path in old_ini_paths.items():
            QSettings.setPath(QSettings.IniFormat, scope, str(path))
        QSettings.setDefaultFormat(old_format)
        qapp.setOrganizationName(old_org)
        qapp.setApplicationName(old_app)


@pytest.fixture
def session(qapp, qtbot, tmp_path, qtlog, preserved_qsettings_state):
    app, engine, editor, playback, keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    templates = engine.rootContext().contextProperty("templates")
    templates._ensure_preview_player().set_volume(0)
    qtbot.waitUntil(lambda: root.findChild(QObject, "nocturneCreateButton") is not None)
    try:
        yield app, engine, root, editor, playback, templates
    finally:
        templates.stopPreview()
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
    assert not [r.message for r in qtlog.records if any(
        marker in r.message for marker in ("TypeError", "ReferenceError", "Binding loop")
    )]


def click(root, name):
    item = root.findChild(QObject, name)
    assert item is not None and item.isVisible() and item.isEnabled()
    position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    assert 0 <= position.x() < root.width() and 0 <= position.y() < root.height()
    QTest.mouseClick(root, Qt.LeftButton, pos=position.toPoint())


def capture(root, name):
    directory = os.environ.get("NOCTURNE_CAPTURE_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        assert root.grabWindow().save(str(path / f"{name}.png"))


def test_profile_owns_settings_projects_and_recovery(session, tmp_path):
    app, engine, root, editor, playback, templates = session
    assert root.property("nocturneMode") is True
    assert editor._projects.root == tmp_path / "Projects"
    assert editor._recovery._directory == tmp_path / "recovery"
    assert templates._project_library is editor._projects
    initialize_deferred_services(engine)
    for name in ("appearance", "experience", "harmonySettings", "savePreferences", "pianoTone", "support"):
        bridge = engine.rootContext().contextProperty(name)
        assert Path(bridge._settings.fileName()).is_relative_to(tmp_path)
    assistant = engine.rootContext().contextProperty("assistant")
    assert Path(assistant._window_settings.fileName()).is_relative_to(tmp_path)
    assert playback.playing is False and templates.previewing is False
    assert "ui-next.prototypes.model" not in __import__("sys").modules


def test_fresh_profile_prioritizes_canvas_and_enables_real_visualizer(session):
    app, engine, root, editor, playback, templates = session
    experience = engine.rootContext().contextProperty("experience")
    scene = engine.rootContext().contextProperty("scene")

    assert experience.sidebarMode == "icon"
    assert experience.inspectorPanelMode == "collapsed"
    assert experience.miniMapDisplayMode == "hidden"
    assert scene.visualizerEnabled is True


def test_existing_profile_preserves_layout_and_visualizer_choices(
    qapp, qtbot, tmp_path, preserved_qsettings_state
):
    experience_store = QSettings(
        str(tmp_path / "settings" / "experience.ini"), QSettings.IniFormat
    )
    experience_store.setValue("experience/sidebarMode", "expanded")
    experience_store.setValue("experience/inspectorPanelMode", "expanded")
    experience_store.setValue("experience/miniMapDisplayMode", "visible")
    appearance_store = QSettings(
        str(tmp_path / "settings" / "appearance.ini"), QSettings.IniFormat
    )
    appearance_store.setValue("appearance/visualizerEnabled", False)
    experience_store.sync()
    appearance_store.sync()

    app, engine, editor, playback, keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    templates = engine.rootContext().contextProperty("templates")
    qtbot.waitUntil(lambda: root.findChild(QObject, "nocturneCreateButton") is not None)
    try:
        experience = engine.rootContext().contextProperty("experience")
        scene = engine.rootContext().contextProperty("scene")
        assert experience.sidebarMode == "expanded"
        assert experience.inspectorPanelMode == "expanded"
        assert experience.miniMapDisplayMode == "visible"
        assert scene.visualizerEnabled is False
    finally:
        templates.stopPreview()
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


def test_background_dialog_uses_the_same_bundled_scene(session, qtbot):
    app, engine, root, editor, playback, templates = session
    initialize_deferred_services(engine)
    click(root, "nocturneBackgroundButton")
    dialog = root.findChild(QObject, "backgroundSettingsDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    expr = QQmlExpression(engine.rootContext(), dialog, "theme.backgroundSourceFor(scene)")
    source, _ = expr.evaluate()
    assert str(source.toLocalFile()).endswith("nocturne-garden.png")
    assert Path(source.toLocalFile()).is_file()
    dialog.close()


def test_home_score_is_the_real_template_playback_projection(session):
    app, engine, root, editor, playback, templates = session
    template_id = templates.templates[0]["id"]
    original = templates._document_for(template_id)
    projection = templates.previewNotes(template_id)
    assert len(projection) == len(original.state.playback_notes)
    assert projection[0] == {
        "pitch": original.state.playback_notes[0].pitch,
        "start": original.state.playback_notes[0].start_time,
        "duration": original.state.playback_notes[0].duration,
    }
    projection[0]["pitch"] = -1
    assert templates.previewNotes(template_id)[0]["pitch"] >= 0
    assert templates._document_for(template_id) == original
    assert templates.previewNotes("missing") == []


@pytest.mark.parametrize("width,height", [(1000, 640), (1366, 768), (1920, 1080)])
def test_home_actions_fit_and_open_real_editor(session, qtbot, width, height):
    app, engine, root, editor, playback, templates = session
    root.setWidth(width)
    root.setHeight(height)
    app.processEvents()
    scene_image = root.findChild(QObject, "nocturneHomeScene")
    ready = QQmlExpression(engine.rootContext(), scene_image, "status === Image.Ready")
    qtbot.waitUntil(lambda: ready.evaluate()[0])
    capture(root, f"home-{width}")
    card = root.findChild(QObject, "nocturneMusicCard")
    assert card.mapToScene(QPointF(0, card.height())).y() < height - 40
    click(root, "nocturneLibraryButton")
    assert root.property("pageState") == "library"
    root.findChild(QObject, "welcomeOverlay").showLaunch()
    app.processEvents()
    click(root, "nocturneCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert editor.noteModel.rowCount() == 0
    assert not playback.playing


def test_adaptation_save_reopen_and_selection_geometry(session, qtbot, tmp_path):
    app, engine, root, editor, playback, templates = session
    root.setWidth(1366)
    root.setHeight(768)
    app.processEvents()
    click(root, "nocturneAdaptButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    project_path = Path(editor.currentFilePath)
    assert project_path.is_relative_to(tmp_path / "Projects")
    assert editor.noteModel.rowCount() > 0
    timeline = root.findChild(QObject, "timeline")
    assert timeline is not None
    app.processEvents()

    def geometry():
        point = timeline.mapToScene(QPointF(0, 0))
        return point.x(), point.y(), timeline.width(), timeline.height()

    baseline = geometry()
    notes = editor.state.notes
    for ids in ([notes[0].id], [notes[1].id], [n.id for n in notes[:3]], []):
        editor.selectNotes(ids)
        app.processEvents()
        assert geometry() == baseline
        assert root.findChild(QObject, "selectionActions").property("hasSelection") == bool(ids)
    original = editor.state.content
    editor.selectNotes([notes[0].id])
    editor.moveSelectedByKeyboard(1, 1)
    assert editor.state.content != original
    editor.undo()
    assert editor.state.content == original
    editor.setBpm(93)
    assert editor.saveProject(str(project_path))
    qtbot.waitUntil(lambda: not editor.isSaving)
    assert load_project_document(project_path).state.tempo.bpm == 93
    capture(root, "editor-1366")
    root.requestLibraryExit("launch", None)
    app.processEvents()
    assert root.property("pageState") == "launch"
    assert "继续《" in root.findChild(QObject, "nocturneCreateButton").property("text")
    click(root, "nocturneCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert editor.currentFilePath == str(project_path)
    assert editor.state.tempo.bpm == 93


def test_home_preview_stops_when_leaving_and_does_not_edit_project(session, qtbot, monkeypatch):
    install_memory_output(monkeypatch.setattr)
    app, engine, root, editor, playback, templates = session
    content = editor.state.content
    click(root, "nocturnePreviewButton")
    qtbot.waitUntil(lambda: templates.previewing, timeout=5000)
    assert editor.state.content == content and not editor.isDirty
    # A real page transition must retire the template session before editing.
    root.setPage("editor", False)
    assert not templates.previewing
    assert not playback.playing


def test_closing_home_retires_template_preview(session, qtbot, monkeypatch):
    install_memory_output(monkeypatch.setattr)
    app, engine, root, editor, playback, templates = session
    click(root, "nocturnePreviewButton")
    qtbot.waitUntil(lambda: templates.previewing, timeout=5000)
    root.close()
    assert not templates.previewing


def test_background_off_and_focus_hide_home_and_editor_decoration(session, qtbot):
    app, engine, root, editor, playback, templates = session
    scene = engine.rootContext().contextProperty("scene")
    image = root.findChild(QObject, "nocturneHomeScene")
    scene.setBackgroundEnabled(False)
    app.processEvents()
    assert not image.isVisible()
    scene.setBackgroundEnabled(True)
    scene.setFocusModeEnabled(True)
    app.processEvents()
    assert not image.isVisible()
    scene.setFocusModeEnabled(False)
    click(root, "nocturneCreateButton")
    qtbot.waitUntil(lambda: root.findChild(QObject, "editorBackgroundLayer") is not None)
    root.setProperty("focusMode", True)
    app.processEvents()
    assert not root.findChild(QObject, "editorBackgroundLayer").isVisible()
