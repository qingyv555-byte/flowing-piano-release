"""Real offscreen layout and optional overview, always using temporary profiles."""
import pytest
from PySide6.QtCore import QObject, QPointF, QSettings, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from ai_piano_studio.experience.settings import ExperienceSettings
from tests.qt.test_studio_shell import (
    click, find_visual_item, preserved_qsettings_state, studio_session,
)


@pytest.mark.parametrize("size", [(2200, 900), (1440, 900), (1000, 640)])
def test_home_content_starts_near_top_and_actions_fit(studio_session, qtbot, size):
    app, engine, root, editor, playback = studio_session
    root.resize(*size)
    QTest.qWait(60)
    heading = root.findChild(QObject, "studioProductHeading")
    position = heading.mapToScene(QPointF(0, 0))
    assert 80 <= position.y() <= 190
    for name in ("studioCreateButton", "studioLibraryButton", "studioTemplatesButton", "studioImportButton",
                 "studioAiButton", "studioAppearanceButton"):
        item = root.findChild(QObject, name)
        origin = item.mapToScene(QPointF(0, 0))
        assert origin.x() >= 0 and origin.y() >= 0
        assert origin.x() + item.width() <= root.width()
        assert origin.y() + item.height() <= root.height()
    create = root.findChild(QObject, "studioCreateButton")
    library = root.findChild(QObject, "studioLibraryButton")
    templates = root.findChild(QObject, "studioTemplatesButton")
    # Integrated navigation (28d8e459) uses a matching 200x48 action row.
    assert create.width() == 200 and create.height() == 48
    assert templates.mapToScene(QPointF(0, 0)).y() > create.mapToScene(QPointF(0, create.height())).y()
    assert abs(library.mapToScene(QPointF(0, 0)).y()
               - templates.mapToScene(QPointF(0, 0)).y()) < 1
    assert len(root.findChildren(QObject, "studioLibraryButton")) == 1
    assert len(root.findChildren(QObject, "studioTemplatesButton")) == 1
    assert engine.rootContext().contextProperty("templates")._preview_player is None
    assert not engine.rootContext().contextProperty("audioVisualizer").enabled
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")


def test_new_profile_overview_is_visible_and_view_menu_can_persist_hide(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    experience = engine.rootContext().contextProperty("experience")
    minimap = find_visual_item(root, "miniMap")
    assert experience.miniMapDisplayMode == "visible"
    # Empty Studio projects hide the overview without changing its preference.
    assert not minimap.isVisible()
    editor.createNoteAt(0, 0)
    qtbot.waitUntil(minimap.isVisible)
    click(root, "studioViewMenuButton")
    menu = root.findChild(QObject, "studioViewMenu")
    qtbot.waitUntil(lambda: menu.property("opened"))
    expression = QQmlExpression(QQmlEngine.contextForObject(menu), menu,
        "(function() { for (var i = 0; i < count; ++i) { var item = itemAt(i); "
        "if (item.objectName === 'studioOverviewAction') return item; } return null; })()")
    item, undefined = expression.evaluate()
    assert not expression.hasError() and not undefined
    assert item.property("text") == "缩略图"
    assert item.property("checked") is True
    QTest.mouseClick(root, Qt.LeftButton,
                    pos=item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint())
    qtbot.waitUntil(lambda: not minimap.isVisible())
    store = QSettings(str(tmp_path / "settings/experience.ini"), QSettings.IniFormat)
    assert ExperienceSettings(settings=store).miniMapDisplayMode == "hidden"
    assert menu.property("opened")
    assert item.property("text") == "缩略图"
    assert item.property("checked") is False
    QTest.mouseClick(root, Qt.LeftButton,
                    pos=item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint())
    qtbot.waitUntil(minimap.isVisible)
    assert ExperienceSettings(settings=store).miniMapDisplayMode == "visible"


@pytest.mark.parametrize("saved, expected", [
    ({"miniMapDisplayMode": "hidden"}, "hidden"),
    ({"miniMapDisplayMode": "weak"}, "weak"),
    ({"miniMapDisplayMode": "visible"}, "visible"),
    ({"miniMapVisible": False}, "hidden"),
    ({"miniMapVisible": True}, "visible"),
])
def test_existing_overview_preferences_are_preserved(
        qapp, tmp_path, preserved_qsettings_state, saved, expected):
    from ai_piano_studio.studio import build_studio_engine

    store = QSettings(str(tmp_path / "settings/experience.ini"), QSettings.IniFormat)
    for key, value in saved.items():
        store.setValue("experience/" + key, value)
    store.sync()
    app, engine, editor, playback, keyboard = build_studio_engine(tmp_path)
    root = engine.rootObjects()[0]
    try:
        assert engine.rootContext().contextProperty("experience").miniMapDisplayMode == expected
        for key, value in saved.items():
            assert store.value("experience/" + key) == value
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
