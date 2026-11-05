"""Appearance transactions and resource boundaries with real Qt image decoding."""
import base64
import importlib.util
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QSize, QUrl
from PySide6.QtGui import QImage, QColor, QFontDatabase, QImageWriter, QImageIOHandler
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression, qmlContext
from PySide6.QtTest import QTest


@pytest.fixture
def appearance(qapp, tmp_path):
    module = "ai_piano_studio.appearance.studio_settings"
    assert importlib.util.find_spec(module) is not None, "Studio appearance service is missing"
    from ai_piano_studio.appearance.studio_settings import StudioAppearance
    settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.IniFormat)
    return StudioAppearance(settings, tmp_path / "assets")


def png(path, width=48, height=32):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#eabd58"))
    assert image.save(str(path))
    return path


def gif(path):
    path.write_bytes(base64.b64decode(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAAKAAAALAAAAAABAAEAAAICRAEA"
        "IfkEAAoAAAAsAAAAAAEAAQAAAgJMAQA7"))
    return path


def visual_child(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_child(child, name)
        if found is not None:
            return found
    return None


def test_preview_cancel_and_apply_survive_reopen(appearance):
    assert appearance.values["themeId"] == "paper"
    appearance.beginPreview()
    appearance.setValue("themeId", "ink")
    assert appearance.values["themeId"] == "ink"
    assert appearance._settings.allKeys() == []
    appearance.cancelPreview()
    assert appearance.values["themeId"] == "paper"
    appearance.beginPreview()
    appearance.setValue("themeId", "ink")
    assert appearance.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["themeId"] == "ink"
    appearance.beginPreview()
    appearance.resetPreview()
    assert appearance.values["themeId"] == "paper"
    appearance.cancelPreview()
    assert appearance.values["themeId"] == "ink"


def test_normalizes_numeric_and_boolean_settings(appearance):
    appearance.setValue("homeOpacity", 3)
    appearance.setValue("editorDim", -1)
    appearance.setValue("noteFeedback", "false")
    appearance.setValue("reducedMotion", "true")
    appearance.setValue("motionFps", 999)
    appearance.setValue("themeId", "unrecognized")
    appearance.setValue("backgroundImportSize", "enormous")
    assert appearance.values["homeOpacity"] == 1
    assert appearance.values["editorDim"] == 0
    assert appearance.values["noteFeedback"] is False
    assert appearance.values["reducedMotion"] is True
    assert appearance.values["motionFps"] == 30
    assert appearance.values["themeId"] == "paper"
    assert appearance.values["backgroundImportSize"] == "1440"
    appearance.setValue("homeOpacity", float("nan"))
    assert 0 <= appearance.values["homeOpacity"] <= 1


def test_feedback_styles_and_strength_share_the_formal_preview_transaction(appearance):
    assert appearance.values["feedbackStyle"] == "glass"
    assert appearance.values["feedbackStrength"] == "balanced"
    appearance.beginPreview()
    appearance.setValue("feedbackStyle", "string")
    appearance.setValue("feedbackStrength", "gentle")
    assert appearance.values["feedbackStyle"] == "string"
    assert appearance._settings.allKeys() == []
    appearance.cancelPreview()
    assert appearance.values["feedbackStyle"] == "glass"
    assert appearance.values["feedbackStrength"] == "balanced"
    appearance.beginPreview()
    appearance.setValue("feedbackStyle", "weave")
    appearance.setValue("feedbackStrength", "vivid")
    appearance.setValue("feedbackLevel", "off")
    assert appearance.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["feedbackStyle"] == "weave"
    assert reopened.values["feedbackStrength"] == "vivid"
    assert reopened.values["feedbackLevel"] == "off"
    reopened.setValue("feedbackStyle", "random")
    reopened.setValue("feedbackStrength", "random")
    assert reopened.values["feedbackStyle"] == "glass"
    assert reopened.values["feedbackStrength"] == "balanced"


def test_import_copies_and_bounds_static_image_without_persisting(appearance, tmp_path, qtbot):
    original = png(tmp_path / "large.png", 3000, 1600)
    appearance.beginPreview()
    appearance.importBackground("home", str(original))
    assert appearance.importBusy
    qtbot.waitUntil(lambda: not appearance.importBusy)
    url = appearance.values["homeSource"]
    managed = Path(QUrl(url).toLocalFile())
    assert managed.is_relative_to(tmp_path / "assets") and managed != original
    image = QImage(str(managed))
    assert image.size() == QSize(2560, 1365)
    assert "2560 × 1365" in appearance.importNotice
    assert appearance._settings.allKeys() == []
    assert appearance.applyPreview()
    original.unlink()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["homeSource"] == url


def test_camera_rotation_keeps_imported_image_inside_size_budget(appearance, tmp_path, qtbot):
    path = tmp_path / "rotated.jpg"
    original = QImage(3000, 1000, QImage.Format_RGB32)
    original.fill(QColor("gold"))
    writer = QImageWriter(str(path), b"jpeg")
    writer.setTransformation(QImageIOHandler.TransformationRotate90)
    assert writer.write(original)
    appearance.importBackground("home", str(path))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    image = QImage(QUrl(appearance.values["homeSource"]).toLocalFile())
    assert not image.isNull()
    assert image.width() <= 2560 and image.height() <= 1440


def test_rejects_bad_or_external_images_and_keeps_previous_background(appearance, tmp_path, qtbot):
    invalid = tmp_path / "broken.png"
    invalid.write_text("This is not an image")
    appearance.importBackground("editor", str(invalid))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert appearance.values["editorSource"] == "builtin"
    assert appearance.importError
    appearance.setValue("homeSource", "https://example.com/image.png")
    assert appearance.values["homeSource"] == "builtin"


@pytest.mark.parametrize("quality,expected", [
    ("1080", QSize(1920, 21)),
    ("1440", QSize(2560, 28)),
    ("2160", QSize(3840, 42)),
])
def test_oversized_static_image_is_scaled_to_selected_import_quality(
    appearance, tmp_path, qtbot, quality, expected
):
    path = png(tmp_path / f"oversized-{quality}.png", 9000, 100)
    appearance.setValue("backgroundImportSize", quality)
    appearance.importBackground("home", str(path))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    managed = QUrl(appearance.values["homeSource"]).toLocalFile()
    assert not appearance.importError
    assert QImage(managed).size() == expected
    assert "已自动缩小" in appearance.importNotice


def test_import_quality_persists_and_never_enlarges_small_image(appearance, tmp_path, qtbot):
    appearance.setValue("backgroundImportSize", "2160")
    appearance.importBackground("editor", str(png(tmp_path / "small.png", 320, 180)))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    managed = QUrl(appearance.values["editorSource"]).toLocalFile()
    assert QImage(managed).size() == QSize(320, 180)
    assert "保持原尺寸" in appearance.importNotice
    assert appearance.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["backgroundImportSize"] == "2160"


@pytest.mark.parametrize("case", ["file-size", "pathological-dimensions", "gif-frames"])
def test_resource_limits_reject_before_installing_background(appearance, tmp_path, qtbot, case):
    path = tmp_path / "over-budget.png"
    if case == "file-size":
        with path.open("wb") as stream:
            stream.truncate(24 * 1024 * 1024 + 1)
    elif case == "pathological-dimensions":
        png(path, 65536, 1)
    else:
        path = gif(tmp_path / "over-budget.gif")
        original = path.read_bytes()
        first = original.index(b"\x21\xf9")
        second = original.index(b"\x21\xf9", first + 1)
        path.write_bytes(original[:first] + original[first:second] * 181 + b"\x3b")
    appearance.importBackground("home", str(path))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert appearance.values["homeSource"] == "builtin"
    assert appearance.importError
    assert not list((tmp_path / "assets").glob("studio-*"))


def test_cancel_invalidates_in_flight_import(appearance, tmp_path, qtbot):
    appearance.beginPreview()
    appearance.importBackground("home", str(png(tmp_path / "first.png")))
    appearance.cancelPreview()
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert appearance.values["homeSource"] == "builtin"
    assert not list((tmp_path / "assets").glob("studio-*"))


def test_builtin_choice_cancels_pending_import_even_if_already_selected(appearance, tmp_path, qtbot):
    appearance.importBackground("home", str(png(tmp_path / "pending.png")))
    appearance.setValue("homeSource", "builtin")
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert appearance.values["homeSource"] == "builtin"


def test_failed_apply_does_not_leave_new_values_in_settings_cache(appearance, tmp_path):
    blocked = tmp_path / "not-a-settings-file"
    blocked.mkdir()
    appearance._settings = QSettings(str(blocked), QSettings.IniFormat)
    appearance.beginPreview()
    appearance.setValue("themeId", "ink")
    assert not appearance.applyPreview()
    assert appearance.importError
    assert appearance._settings.allKeys() == []
    appearance.cancelPreview()
    assert appearance.values["themeId"] == "paper"


def test_latest_import_wins_and_orphans_are_removed(appearance, tmp_path, qtbot):
    appearance.importBackground("home", str(png(tmp_path / "first.png", 20, 10)))
    appearance.importBackground("home", str(png(tmp_path / "second.png", 35, 25)))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    managed = QUrl(appearance.values["homeSource"]).toLocalFile()
    assert QImage(managed).width() == 35
    assert len(list((tmp_path / "assets").glob("studio-*"))) == 1


def test_gif_has_valid_managed_animation_and_static_cover(appearance, tmp_path, qtbot):
    appearance.importBackground("home", str(gif(tmp_path / "motion.gif")))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert not appearance.importError
    managed = Path(QUrl(appearance.values["homeSource"]).toLocalFile())
    assert managed.suffix == ".gif"
    assert not QImage(str(managed.with_suffix(".png"))).isNull()


def test_gif_rate_limit_survives_apply_and_reopen(appearance, tmp_path, qtbot):
    path = gif(tmp_path / "fast.gif")
    path.write_bytes(path.read_bytes().replace(b"\x21\xf9\x04\x00\x0a\x00", b"\x21\xf9\x04\x00\x02\x00"))
    appearance.importBackground("editor", str(path))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    source = appearance.values["editorSource"]
    assert appearance.animationSpeed(source, 15) == pytest.approx(0.3)
    assert appearance.animationSpeed(source, 60) == 1
    assert appearance.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.animationSpeed(source, 30) == pytest.approx(0.6)


def test_missing_saved_resource_falls_back(appearance, tmp_path, qtbot):
    appearance.importBackground("home", str(png(tmp_path / "image.png")))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert appearance.applyPreview()
    Path(QUrl(appearance.values["homeSource"]).toLocalFile()).unlink()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["homeSource"] == "builtin"


@pytest.fixture
def appearance_window(appearance, qtbot, qapp):
    qml = Path(__file__).resolve().parents[2] / "src/ai_piano_studio/qml"
    assert (qml / "StudioAppearanceDialog.qml").is_file(), "Appearance dialog is missing"
    assert (qml / "StudioBackground.qml").is_file(), "Studio background is missing"
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("testAppearance", appearance)
    engine.rootContext().setContextProperty("studioAppearance", appearance)
    engine.loadData(b'''
import QtQuick
import QtQuick.Controls
ApplicationWindow {
    width: 960; height: 720; visible: true
    Theme { id: theme; objectName: "testTheme"; studio: true }
    StudioBackground {
        objectName: "testBackground"; anchors.fill: parent
        theme: theme; settings: testAppearance; target: "home"
    }
    StudioAppearanceDialog { theme: theme; settings: testAppearance }
}
''', QUrl.fromLocalFile(str(qml / "AppearanceTest.qml")))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    yield root, appearance
    root.close()
    engine.deleteLater()
    qapp.processEvents()
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


def test_dialog_escape_rolls_back_and_opaque_body_renders(appearance_window, qtbot):
    root, appearance = appearance_window
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    appearance.setValue("themeId", "ink")
    assert root.findChild(QObject, "studioAppearanceBody").property("color").alphaF() == 1
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert appearance.values["themeId"] == "paper"
    assert not root.grabWindow().isNull()


def test_background_unloads_when_inactive_and_reduced_motion_uses_cover(appearance_window, tmp_path, qtbot):
    root, appearance = appearance_window
    appearance.importBackground("home", str(gif(tmp_path / "lifecycle.gif")))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    background = root.findChild(QObject, "testBackground")
    loader = background.findChild(QObject, "studioBackgroundLoader")
    def value(expression):
        query = QQmlExpression(qmlContext(loader), loader, expression)
        result, undefined = query.evaluate()
        assert not query.hasError(), query.error().toString()
        assert not undefined
        return result
    qtbot.waitUntil(lambda: value("item !== null && item.children[0].frameCount === 2"))
    assert value("item.children[0].playing") is True
    first_frame = value("item.children[0].currentFrame")
    qtbot.waitUntil(lambda: value("item.children[0].currentFrame") != first_frame)
    background.setProperty("motionActive", False)
    qtbot.waitUntil(lambda: value('item.objectName === "studioBackgroundStill"'))
    background.setProperty("motionActive", True)
    qtbot.waitUntil(lambda: value('item.objectName === "studioBackgroundMotion"'))
    appearance.setValue("reducedMotion", True)
    qtbot.waitUntil(lambda: value('item.objectName === "studioBackgroundStill"'))
    assert value("item.children[0].source.toString()").endswith(".png")
    background.setProperty("active", False)
    qtbot.waitUntil(lambda: value("item === null"))


def test_dialog_apply_button_commits_preview(appearance_window, qtbot):
    root, appearance = appearance_window
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    appearance.setValue("themeId", "paper")
    button = root.findChild(QObject, "studioAppearanceApply")
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, pos=point)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["themeId"] == "paper"


def test_background_import_quality_buttons_preview_and_apply(appearance_window, qtbot):
    root, appearance = appearance_window
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.setProperty("page", 1)
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert appearance.values["backgroundImportSize"] == "1440"
    root.grabWindow()
    for quality in ("1080", "2160"):
        button = visual_child(root.contentItem(), "studioBackgroundImportSize-" + quality)
        assert button is not None and button.property("visible")
        # Theme and background now share one scrollable page. Reveal the real
        # control before retaining the original viewport/click assertions.
        from PySide6.QtQml import QQmlExpression, qmlContext
        scroll = root.findChild(QObject, "studioAppearanceScroll")
        flick, undefined = QQmlExpression(qmlContext(scroll), scroll, "contentItem").evaluate()
        assert not undefined
        y = button.mapToItem(flick, QPointF()).y()
        maximum = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", min(maximum, max(0, flick.property("contentY") + y - 30)))
        root.grabWindow()
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        assert 0 <= point.x() < root.width() and 0 <= point.y() < root.height(), point
        QTest.mouseClick(root, Qt.LeftButton, pos=point)
        assert appearance.values["backgroundImportSize"] == quality
    apply = root.findChild(QObject, "studioAppearanceApply")
    point = apply.mapToScene(QPointF(apply.width() / 2, apply.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, pos=point)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["backgroundImportSize"] == "2160"


def test_music_feedback_buttons_preview_cancel_and_apply(appearance_window, qtbot):
    root, appearance = appearance_window
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.setProperty("page", 2)

    def click(name):
        root.grabWindow()  # Settle the newly visible page's Qt Quick layout.
        button = visual_child(root.contentItem(), name)
        assert button is not None
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        assert 0 <= point.x() < root.width() and 0 <= point.y() < root.height()
        QTest.mouseClick(root, Qt.LeftButton, pos=point)

    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert visual_child(root.contentItem(), "studioFeedbackStyle-legacy") is None
    assert visual_child(root.contentItem(), "studioFeedbackStyle-string") is None
    glass_button = visual_child(root.contentItem(), "studioFeedbackStyle-glass")
    weave_button = visual_child(root.contentItem(), "studioFeedbackStyle-weave")
    assert glass_button is not None and weave_button is not None
    assert abs(glass_button.width() - weave_button.width()) <= 1
    click("studioFeedbackStyle-weave")
    assert appearance.values["feedbackStyle"] == "weave"
    click("studioFeedbackStrength-gentle")
    assert appearance.values["feedbackStrength"] == "gentle"
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert appearance.values["feedbackStyle"] == "glass"
    assert appearance.values["feedbackStrength"] == "balanced"
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    click("studioFeedbackStyle-weave")
    click("studioFeedbackStrength-vivid")
    click("studioFeedbackSwitch")
    assert appearance.values["feedbackLevel"] == "off"
    click("studioAppearanceApply")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values["feedbackStyle"] == "weave"
    assert reopened.values["feedbackStrength"] == "vivid"
    assert reopened.values["feedbackLevel"] == "off"


def test_music_feedback_scrollbar_keeps_clear_of_values(appearance_window, qtbot):
    root, _appearance = appearance_window
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.setProperty("page", 2)
    dialog.setProperty("advancedFeedback", True)
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    root.grabWindow()

    scroll = visual_child(root.contentItem(), "studioAppearanceScroll")
    bar = visual_child(root.contentItem(), "studioAppearanceScrollBar")
    value_label = visual_child(root.contentItem(), "glassPositionYValueLabel")
    assert scroll is not None and bar is not None and value_label is not None
    qtbot.waitUntil(lambda: bar.property("visible"))
    bar_left = bar.mapToScene(QPointF(0, 0)).x()
    bar_right = bar.mapToScene(QPointF(bar.width(), 0)).x()
    label_right = value_label.mapToScene(QPointF(value_label.width(), 0)).x()
    scroll_right = scroll.mapToScene(QPointF(scroll.width(), 0)).x()
    assert label_right <= bar_left - 8
    assert scroll_right - bar_right <= 1


def test_background_fit_focus_and_opacity_are_local(appearance_window, tmp_path, qtbot):
    root, appearance = appearance_window
    appearance.importBackground("home", str(png(tmp_path / "wide.png", 200, 100)))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    background = root.findChild(QObject, "testBackground")
    loader = background.findChild(QObject, "studioBackgroundLoader")
    def image_value(expression):
        query = QQmlExpression(qmlContext(loader), loader, "item.children[0]." + expression)
        result, _ = query.evaluate()
        assert not query.hasError(), query.error().toString()
        return result
    qtbot.waitUntil(lambda: image_value("status") == 1)
    assert image_value("width") == 1440
    assert image_value("x") == -240
    appearance.setValue("homeFocusX", 1)
    assert image_value("x") == -480
    appearance.setValue("homeFit", "contain")
    assert image_value("width") == 960
    assert image_value("height") == 480
    assert image_value("y") == 120
    appearance.setValue("homeOpacity", 0.2)
    assert loader.property("opacity") == pytest.approx(0.2)
    assert background.property("opacity") == 1
    assert background.findChild(QObject, "studioBackgroundFloor").property("color").alphaF() == 1


@pytest.mark.parametrize("theme_id,pattern", [
    ("ink", "white"), ("paper", "black"), ("ink", "checker"), ("paper", "checker"),
])
def test_extreme_background_pixels_keep_normal_text_readable(appearance_window, tmp_path, qtbot, theme_id, pattern):
    root, appearance = appearance_window
    appearance.setValue("themeId", theme_id)
    appearance.setValue("homeOpacity", 1)
    appearance.setValue("homeDim", 0)
    original = QImage(64, 64, QImage.Format_RGB32)
    original.fill(QColor("white" if pattern == "checker" else pattern))
    if pattern == "checker":
        for y in range(64):
            for x in range(64):
                if (x < 32) != (y < 32):
                    original.setPixelColor(x, y, QColor("black"))
    path = tmp_path / "contrast.png"
    assert original.save(str(path))
    appearance.importBackground("home", str(path))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    background = root.findChild(QObject, "testBackground")
    loader = background.findChild(QObject, "studioBackgroundLoader")
    def loaded():
        query = QQmlExpression(qmlContext(loader), loader, "item !== null && item.children[0].status === 1")
        return query.evaluate()[0]
    qtbot.waitUntil(loaded)
    captured = root.grabWindow()
    assert not captured.isNull()
    theme = root.findChild(QObject, "testTheme")
    def luminance(color):
        def linear(channel):
            return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        return (0.2126 * linear(color.redF()) + 0.7152 * linear(color.greenF())
                + 0.0722 * linear(color.blueF()))
    for x, y in ((240, 180), (720, 180), (240, 540), (720, 540)):
        pixel = captured.pixelColor(x, y)
        assert pixel.alpha() == 255
        for role in ("textPrimary", "textSecondary", "textMuted"):
            a, b = luminance(pixel), luminance(theme.property(role))
            ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            assert ratio >= 4.5, (theme_id, pattern, role, pixel.name(), ratio)
    assert captured.save(str(tmp_path / f"background-contrast-{theme_id}-{pattern}.png"))
    # The upper dim range still lets users fully remove image variation.
    appearance.setValue("homeDim", 1)
    opaque = root.grabWindow().pixelColor(240, 180)
    assert opaque == theme.property("appBackground")


def test_all_dialog_pages_render_with_footer_visible_on_narrow_window(appearance_window, tmp_path, qtbot, qtlog):
    root, appearance = appearance_window
    root.resize(640, 560)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("width") <= 608)
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    for theme in ("ink", "paper"):
        appearance.setValue("themeId", theme)
        for page in range(4):
            dialog.setProperty("page", page)
            assert root.grabWindow().save(str(tmp_path / f"appearance-{theme}-{page}.png"))
            button = root.findChild(QObject, "studioAppearanceApply")
            point = button.mapToScene(QPointF(button.width(), button.height()))
            assert 0 < point.x() <= 640 and 0 < point.y() <= 560
    assert not [record.message for record in qtlog.records if any(
        word in record.message for word in ("TypeError", "ReferenceError", "Binding loop"))]
