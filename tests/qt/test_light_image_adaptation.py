from PySide6.QtGui import QColor, QPalette

from ai_piano_studio.appearance.image_palette import luminance
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import qml
from tests.qt.test_studio_image_palette import picture


def test_paper_surfaces_visibly_follow_warm_and_cool_images_and_cancel(studio_session, tmp_path, qtbot):
    app, engine, root, editor, playback = studio_session
    settings = engine.rootContext().contextProperty("studioAppearance")
    original = root.color()
    settings.beginPreview()
    settings.setValue("themeId", "paper")
    settings.setValue("themeSeedSource", "image")
    settings.setValue("themeImageTarget", "editor")
    settings.setValue("adaptiveTheme", True)
    colors = []
    whites = []
    for name, color in (("warm", "#e85840"), ("cool", "#458ad5")):
        settings.importBackground("editor", picture(tmp_path / f"{name}.png", color))
        qtbot.waitUntil(lambda: not settings.importBusy)
        qtbot.waitUntil(lambda: settings.themeSeed.name() == color)
        surface = QColor(qml(root, "appTheme.popupBackground"))
        white = QColor(qml(root, "appTheme.elevatedSurface"))
        text = QColor(qml(root, "appTheme.textPrimary"))
        assert luminance(surface) >= .75 and luminance(white) >= .85
        assert (luminance(surface) + .05) / (luminance(text) + .05) >= 4.5
        assert app.palette().color(QPalette.Window) == root.color()
        colors.append(surface)
        whites.append(white)
    assert whites[0] != whites[1], "Pure-white surfaces ignored the image hue"
    distance = sum(abs(getattr(colors[0], channel)() - getattr(colors[1], channel)())
                   for channel in ("red", "green", "blue"))
    assert distance >= 24, f"Paper surface tint is barely visible: RGB distance {distance}"
    settings.setValue("themeId", "ink")
    assert QColor(qml(root, "appTheme.popupBackground")) == settings.tintColor("#21333D")
    settings.setValue("themeId", "paper")
    settings.setValue("adaptiveTheme", False)
    assert QColor(qml(root, "appTheme.elevatedSurface")) == QColor("white")
    settings.cancelPreview()
    assert root.color() == original == app.palette().color(QPalette.Window)
