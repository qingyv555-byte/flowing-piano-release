"""Actual Studio/assistant palette propagation and clear-background rendering."""
import pytest
from PySide6.QtCore import QObject, QMetaObject, QSettings
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtQuick import QQuickItem

from ai_piano_studio.appearance.image_palette import image_seed, luminance, tint_color
from ai_piano_studio.appearance.studio_settings import StudioAppearance
from ai_piano_studio.__main__ import initialize_deferred_services
from tests.qt.test_studio_shell import studio_session, click
from tests.qt.test_studio_workflows import qml
from tests.qt.test_nocturne import preserved_qsettings_state


def picture(path, color):
    image = QImage(64, 48, QImage.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path))
    return str(path)


@pytest.mark.parametrize("seed", ["#e85840", "#458ad5", "#52a072"])
def test_tint_preserves_luminance_and_text_contrast(seed):
    seed = QColor(seed)
    for value in ("#111F28", "#21333D", "#E4EAE5", "#ADBFBD", "#E8EDF0", "#243E49", "#465E68"):
        assert abs(luminance(QColor(value)) - luminance(tint_color(value, seed))) < 0.0001
    for ink, background in (("#ADBFBD", "#21333D"), ("#465E68", "#E8EDF0")):
        a, b = luminance(tint_color(ink, seed)), luminance(tint_color(background, seed))
        assert (max(a, b) + 0.05) / (min(a, b) + 0.05) >= 4.5


def test_neutral_image_keeps_brand_palette(tmp_path):
    assert image_seed(picture(tmp_path / "neutral.png", "#888888")).alpha() == 0


def test_image_palette_reaches_assistant_and_rolls_back(studio_session, tmp_path, qtbot):
    app, engine, root, editor, playback = studio_session
    settings = engine.rootContext().contextProperty("studioAppearance")
    initialize_deferred_services(engine)
    QMetaObject.invokeMethod(root, "openAssistantWindow")
    # Studio embeds the shared assistant workspace as a dock.
    qtbot.waitUntil(lambda: root.findChild(QQuickItem, "assistantWorkspace") is not None)
    assistant = root.findChild(QQuickItem, "assistantWorkspace")
    qtbot.waitUntil(assistant.isVisible)
    original = root.color()
    assert qml(assistant, "theme.appBackground") == original
    settings.beginPreview()
    settings.importBackground("home", picture(tmp_path / "warm.png", "#dc7850"))
    qtbot.waitUntil(lambda: not settings.importBusy)
    settings.setValue("adaptiveTheme", True)
    qtbot.waitUntil(lambda: settings.themeSeed.alpha() > 0)
    qtbot.waitUntil(lambda: root.color() != original)
    assert qml(assistant, "theme.appBackground") == root.color() == app.palette().color(QPalette.Window)
    themed = root.color()
    settings.cancelPreview()
    assert root.color() == qml(assistant, "theme.appBackground") == original
    settings.beginPreview()
    settings.importBackground("editor", picture(tmp_path / "editor.png", "#dc7850"))
    qtbot.waitUntil(lambda: not settings.importBusy)
    settings.setValue("themeImageTarget", "editor")
    settings.setValue("adaptiveTheme", True)
    qtbot.waitUntil(lambda: root.color() == themed)
    assert settings.applyPreview()
    reopened = StudioAppearance(QSettings(settings._settings.fileName(), QSettings.IniFormat), settings._asset_home)
    qtbot.waitUntil(lambda: reopened.themeSeed.alpha() > 0)
    # Paper surfaces use their own tint; text still preserves exact luminance.
    assert reopened.tintSurfaceColor(original.name()) == root.color()
    settings.setValue("editorSource", "none")
    assert root.color() == qml(assistant, "theme.appBackground") == original
    assistant.closeRequested.emit()


def test_clear_background_is_full_strength_with_local_text_protection(studio_session, tmp_path, qtbot):
    app, engine, root, editor, playback = studio_session
    settings = engine.rootContext().contextProperty("studioAppearance")
    root.resize(1366, 768)
    settings.importBackground("home", picture(tmp_path / "white.png", "#ffffff"))
    qtbot.waitUntil(lambda: not settings.importBusy)
    settings.setValue("homeOpacity", 1)
    settings.setValue("homeDim", 0)
    qtbot.wait(120)
    image = root.grabWindow()
    # The far edge contains no text backing or native chrome.
    assert image.pixelColor(2, 350) == QColor("white")
    assert image.pixelColor(2, 350).alpha() == 255
    pending, protections = [root.contentItem()], []
    while pending:
        item = pending.pop()
        if item.objectName() == "studioHomeTextProtection":
            protections.append(item)
        pending.extend(item.childItems())
    assert protections and all(p.property("opacity") >= .95 for p in protections)
    settings.importBackground("editor", picture(tmp_path / "green.png", "#52a072"))
    qtbot.waitUntil(lambda: not settings.importBusy)
    settings.setValue("editorOpacity", 1)
    settings.setValue("editorDim", 0)
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    qtbot.wait(120)
    image = root.grabWindow()
    # Away from grid boundaries: the custom image is not capped by an 82% veil.
    color = image.pixelColor(700, 403)
    assert abs(color.green() - 160) < 15
    assert color.alpha() == 255
