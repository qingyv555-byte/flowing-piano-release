"""Pointer-level settings regression, with isolated profiles and no audio IO."""
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest

from tests.qt.qml_test_utils import load_inline_qml
from tests.qt.test_studio_appearance import appearance, appearance_window, visual_child
from tests.qt.test_studio_workflows import qml
from tests.qt.test_studio_workflows import click_item, find_item
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_character_skins import local_skin_artwork
from tests.qt.test_nocturne import preserved_qsettings_state
from tools.studio_evidence import install_memory_output


@pytest.fixture(autouse=True)
def memory_audio(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def reveal(root, name):
    # Force Qt's pending layout/polish before measuring a control after page or
    # content changes. An offscreen synthetic click must use the rendered frame.
    root.grabWindow()
    item = find_item(root, name)
    assert item is not None, name
    scroll = find_item(root, "studioAppearanceScroll")
    if not name.startswith("studioSettingsCategory") and scroll is not None:
        top = scroll.mapToScene(QPointF(0, 0)).y()
        pos = item.mapToScene(QPointF(0, 0)).y()
        if pos < top or pos + item.height() > top + scroll.height():
            delta = pos - top - (scroll.height() - item.height()) / 2
            qml(scroll, f"contentItem.contentY = Math.max(0, Math.min(contentItem.contentHeight - contentItem.height, contentItem.contentY + {delta})); true")
            QTest.qWait(30)
            root.grabWindow()
    return item


def press(root, name):
    item = reveal(root, name)
    click_item(root, item)
    QTest.qWait(20)
    return item


def slider_to(root, name, fraction):
    item = reveal(root, name)
    click_item(root, item, max(1, min(item.width() - 1, item.width() * fraction)))
    QTest.qWait(20)
    return item


def open_appearance(appearance_window, qtbot, page):
    root, settings = appearance_window
    root.resize(1440, 960)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.open()
    qtbot.waitUntil(lambda: bool(dialog.property("opened")))
    qtbot.waitUntil(lambda: dialog.property("width") == 1120 and dialog.property("height") == 820)
    root.grabWindow()
    press(root, "studioSettingsCategory" + str(0 if page == 1 else page))
    return root, dialog, settings


def test_slider_keeps_drag_when_pointer_leaves_inside_scrollview(qapp):
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true; width: 640; height: 500
            property real result: 0.25
            property int commits: 0
            Theme { id: theme }
            Flickable {
                objectName: "scroller"
                anchors.fill: parent; contentHeight: 1000
                StyledSlider {
                    objectName: "slider"; theme: theme
                    x: 70; y: 150; width: 460
                    modelValue: result
                    onValueEdited: value => result = value
                    onValueCommitted: commits += 1
                }
            }
        }
    """)
    slider = root.findChild(QObject, "slider")
    scroller = root.findChild(QObject, "scroller")
    QTest.qWait(50)
    start = slider.mapToScene(QPointF(slider.width() * .25, slider.height() / 2)).toPoint()
    QTest.mousePress(root, Qt.LeftButton, pos=start)
    QTest.mouseMove(root, start + QPoint(2, 65), delay=30)
    QTest.mouseMove(root, start + QPoint(240, 65), delay=30)
    QTest.mouseRelease(root, Qt.LeftButton, pos=start + QPoint(240, 65))
    assert root.property("result") > .7
    assert root.property("commits") == 1
    assert scroller.property("contentY") == 0
    assert slider.hasActiveFocus()
    root.close()


def test_slider_thumb_press_does_not_jump(qapp):
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true; width: 640; height: 150
            Theme { id: theme }
            StyledSlider {
                objectName: "slider"; theme: theme
                x: 40; y: 40; width: 500; modelValue: 0.1
            }
        }
    """)
    slider = root.findChild(QObject, "slider")
    QTest.qWait(120)
    handle = slider.findChild(QObject, "sliderHandle")
    point = handle.mapToScene(QPointF(handle.width() / 2, handle.height() / 2)).toPoint()
    QTest.mousePress(root, Qt.LeftButton, pos=point)
    assert slider.property("value") == pytest.approx(.1, abs=.002)
    QTest.mouseRelease(root, Qt.LeftButton, pos=point)
    root.close()


def test_background_controls_edit_editor_by_default_and_apply(appearance_window, qtbot, tmp_path):
    from PySide6.QtCore import QUrl
    from tests.qt.test_studio_appearance import png

    root, dialog, settings = open_appearance(appearance_window, qtbot, 1)
    assert dialog.property("backgroundTarget") == "editor"
    settings.applyCharacterSkin("luna")
    preview = find_item(root, "studioBackgroundPreview")
    qtbot.waitUntil(lambda: find_item(root, "studioBackgroundImage") is not None)
    for key in ("Opacity", "Dim", "FocusX", "FocusY"):
        control = slider_to(root, "editor" + key + "Slider", .22)
        assert settings.values["editor" + key] == control.property("value")
        assert settings.values["editor" + key] < .3
        assert preview.property({"Opacity": "imageOpacity", "Dim": "effectiveDim",
                                 "FocusX": "focusX", "FocusY": "focusY"}[key]) == pytest.approx(control.property("value"))
    press(root, "studioBackgroundFit-contain")
    assert settings.values["editorFit"] == "contain" and preview.property("fit") == "contain"
    press(root, "studioBackgroundFit-cover")
    assert preview.property("fit") == "cover"
    for source in ("none", "builtin"):
        press(root, "studioBackgroundSource-" + source)
        assert settings.values["editorSource"] == source
        assert preview.property("sourceName") == source
    for quality in ("1080", "1440", "2160"):
        press(root, "studioBackgroundImportSize-" + quality)
        assert settings.values["backgroundImportSize"] == quality
    picker = root.findChild(QObject, "studioBackgroundImagePicker")
    qml(picker, "options = FileDialog.DontUseNativeDialog; true")
    press(root, "studioBackgroundChooseImage")
    qtbot.waitUntil(lambda: bool(picker.property("visible")))
    path = png(tmp_path / "chosen.png", 220, 100)
    picker.setProperty("selectedFile", QUrl.fromLocalFile(str(path)))
    # Substitute only the OS file-picker completion, retaining the real import.
    picker.accepted.emit()
    picker.close()
    qtbot.waitUntil(lambda: not settings.importBusy)
    assert settings.values["editorSource"].startswith("file:")
    press(root, "studioAdaptImageTheme")
    assert settings.values["themeImageTarget"] == "editor"
    editor_before = dict(settings.values)
    press(root, "studioBackgroundTarget-home")
    assert dialog.property("backgroundTarget") == "home"
    slider_to(root, "homeOpacitySlider", .6)
    assert settings.values["editorOpacity"] == editor_before["editorOpacity"]
    press(root, "studioBackgroundTarget-editor")
    press(root, "studioAppearanceApply")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    reopened = type(settings)(settings._settings, settings._asset_home)
    assert reopened.values == settings.values
    dialog.open()
    qtbot.waitUntil(lambda: bool(dialog.property("opened")))
    assert dialog.property("backgroundTarget") == "editor"
    press(root, "studioBackgroundTarget-home")
    press(root, "studioAppearanceCancel")
    dialog.open()
    qtbot.waitUntil(lambda: bool(dialog.property("opened")))
    assert dialog.property("backgroundTarget") == "editor"
    press(root, "studioAppearanceClose")


def test_theme_cards_and_restore_are_real_clicks(appearance_window, qtbot, tmp_path):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    original = settings.values
    for key in ("paper", "ink"):
        press(root, "studioTheme-" + key)
        assert settings.values["themeId"] == key
    for key in ("luna", "hack", "bee", "gale", "traveler"):
        press(root, "studioPalette-" + key)
        assert settings.values["themePalette"] == key
        assert settings.values["characterSkin"] == "none"
    theme_values = {k: settings.values[k] for k in ("themePalette", "themeId", "themeSeedSource", "adaptiveTheme")}
    for key in ("luna", "hack", "bee", "gale", "traveler", "none"):
        press(root, "studioNotePalette-" + key)
        assert settings.values["characterSkin"] == key
        assert {k: settings.values[k] for k in theme_values} == theme_values
    press(root, "studioCustomNoteColor")
    picker = root.findChild(QObject, "studioNoteColorPicker")
    qtbot.waitUntil(lambda: picker.property("visible"))
    from PySide6.QtGui import QColor
    picker.setProperty("selectedColor", QColor("#dc45a8"))
    picker.accepted.emit()
    picker.close()
    assert settings.values["customNoteColor"] == "#dc45a8"
    assert settings.values["noteColorSource"] == "custom"
    for key in ("ink", "paper"):
        press(root, "studioTheme-" + key)
        assert settings.values["noteColorSource"] == "custom"
        assert settings.values["customNoteColor"] == "#dc45a8"
    reveal(root, "studioCustomNoteHex")
    assert root.grabWindow().save(str(tmp_path / "independent-note-settings.png"))
    press(root, "studioAppearanceReset")
    assert settings.values == original
    press(root, "studioAppearanceCancel")


def test_feedback_and_motion_controls_update_and_cancel(appearance_window, qtbot):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 2)
    original = settings.values
    for style in ("weave", "glass"):
        press(root, "studioFeedbackStyle-" + style)
        assert settings.values["feedbackStyle"] == style
    for strength in ("gentle", "balanced", "vivid"):
        press(root, "studioFeedbackStrength-" + strength)
        assert settings.values["feedbackStrength"] == strength
    press(root, "studioFeedbackPreviewButton")
    assert dialog.property("previewAnimation") is True
    press(root, "studioFeedbackPreviewButton")
    assert dialog.property("previewAnimation") is False
    slider_to(root, "glassWidthSlider", .5)
    assert settings.values["glassWidth"] < 1
    slider_to(root, "glassPositionXSlider", .2)
    press(root, "studioFeedbackCustomize")
    for name, hue in (("雾青", .46), ("琥珀", .1), ("灰蓝", .58)):
        press(root, "studioFeedbackHue-" + name)
        assert settings.values["glassHue"] == hue
    for key in ("glassHue", "glassSaturation", "glassHeight", "glassPositionY"):
        item = slider_to(root, key + "Slider", .65)
        assert settings.values[key] == item.property("value")
    for key in ("noteFeedback", "keyFeedback", "spectrum", "atmosphere"):
        previous = settings.values[key]
        press(root, key + "Check")
        assert settings.values[key] is not previous
    press(root, "studioFeedbackSwitch")
    assert settings.values["feedbackLevel"] == "off"
    assert not find_item(root, "glassWidthSlider").isEnabled()
    press(root, "studioFeedbackSwitch")
    assert settings.values["feedbackLevel"] == "standard"
    press(root, "studioSettingsCategory3")
    for key in ("homeAmbient", "reducedMotion"):
        previous = settings.values[key]
        press(root, key + "Check")
        assert settings.values[key] is not previous
    for fps in (15, 30, 60):
        press(root, "studioMotionFps-" + str(fps))
        assert settings.values["motionFps"] == fps
    press(root, "studioAppearanceCancel")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert settings.values == original


def open_actual_settings(session, qtbot):
    from ai_piano_studio.__main__ import initialize_deferred_services

    _, engine, root, _, _ = session
    root.resize(1440, 960)
    initialize_deferred_services(engine)
    qtbot.waitUntil(lambda: bool(root.property("deferredAppearanceReady")))
    qml(root, "openAppearancePopup(contentItem); true")
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioAppearanceDialog") is not None)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: bool(dialog.property("opened")))
    return root, dialog, engine.rootContext().contextProperty("studioAppearance")


def test_background_sliders_change_real_editor_rendering(studio_session, qtbot):
    root = studio_session[2]
    press(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    root, dialog, settings = open_actual_settings(studio_session, qtbot)
    press(root, "studioSettingsCategory0")
    assert dialog.property("backgroundTarget") == "editor"
    settings.applyCharacterSkin("luna")
    background = find_item(root, "studioEditorBackground")
    assert background is not None

    def loaded():
        image = visual_child(background, "studioBackgroundImage")
        return image is not None and bool(qml(image, "status === Image.Ready"))

    qtbot.waitUntil(loaded)
    QTest.qWait(80)
    original = root.grabWindow().copy(90, 250, 50, 300)
    slider_to(root, "editorOpacitySlider", 0)
    assert background.property("imageOpacity") == 0
    assert root.grabWindow().copy(90, 250, 50, 300) != original
    slider_to(root, "editorOpacitySlider", 1)
    qtbot.waitUntil(loaded)
    QTest.qWait(50)
    original = root.grabWindow().copy(90, 250, 50, 300)
    slider_to(root, "editorDimSlider", 1)
    assert background.property("effectiveDim") == 1
    assert root.grabWindow().copy(90, 250, 50, 300) != original
    for key in ("FocusX", "FocusY"):
        slider_to(root, "editor" + key + "Slider", .8)
        assert background.property("focus" + key[-1]) == settings.values["editor" + key]
    press(root, "studioBackgroundFit-contain")
    assert background.property("fit") == "contain"
    press(root, "studioAppearanceApply")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert type(settings)(settings._settings, settings._asset_home).values == settings.values


def select_last(root, name, qtbot):
    box = press(root, name)
    qtbot.waitUntil(lambda: bool(qml(box, "popup.opened")))
    QTest.keyClick(root, Qt.Key_End)
    QTest.keyClick(root, Qt.Key_Return)
    qtbot.waitUntil(lambda: not bool(qml(box, "popup.visible")))
    return box


def test_preferences_all_edit_controls_apply_to_real_controllers(studio_session, qtbot, monkeypatch):
    root, dialog, _ = open_actual_settings(studio_session, qtbot)
    _, engine, _, editor, playback = studio_session
    experience = engine.rootContext().contextProperty("experience")
    saves = engine.rootContext().contextProperty("savePreferences")
    tone = engine.rootContext().contextProperty("pianoTone")
    before = editor.state.content
    press(root, "studioSettingsCategory4")
    volume = slider_to(root, "preferencesVolume", .3).property("value")
    qtbot.waitUntil(lambda: tone.available)
    tone_value = slider_to(root, "preferencesTone", .8).property("value")
    original_tone = playback.projectSoundBrightness
    auditions = []
    # Source-independent tuning replaces the old sample-only tone preview.
    monkeypatch.setattr(playback, "auditionCurrentSound", lambda: auditions.append(playback.piano_source.mixer._sound_effects.parameters))
    press(root, "preferencesTonePreview")
    assert auditions and playback.projectSoundBrightness == original_tone
    assert auditions[0].brightness == pytest.approx(tone_value)
    sound_values = {}
    for key in ("response", "reverb", "width"):
        sound_values[key] = slider_to(root, "preferencesSound_" + key, .7).property("value")
    trim = slider_to(root, "preferencesSourceTrim", .6).property("value")
    select_last(root, "preferencesRate", qtbot)
    assert qml(dialog, "preferencesDraft.rate") == 2
    press(root, "studioSettingsCategory5")
    for name in ("preferencesNoteLength", "preferencesPreviewLength", "preferencesDragPreview"):
        select_last(root, name, qtbot)
    velocity = reveal(root, "preferencesVelocity")
    click_item(root, qml(velocity, "contentItem"))
    QTest.keyClick(root, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(root, Qt.Key_9)
    QTest.keyClick(root, Qt.Key_0)
    QTest.keyClick(root, Qt.Key_Return)
    assert qml(dialog, "preferencesDraft.velocity") == 90
    press(root, "studioSettingsCategory6")
    if qml(dialog, "preferencesDraft.autosave"):
        press(root, "preferencesAutosave")
    assert not find_item(root, "preferencesSaveDelay").isEnabled()
    press(root, "preferencesAutosave")
    select_last(root, "preferencesSaveDelay", qtbot)
    assert not find_item(root, "preferencesRecovery").isEnabled()
    press(root, "studioAppearanceApply")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert playback.volume == pytest.approx(volume)
    assert playback.playbackRate == 2
    assert playback.projectSoundBrightness == pytest.approx(tone_value)
    assert playback.projectSoundTrim == pytest.approx(trim)
    for key, value in sound_values.items():
        assert getattr(playback.project_sound_parameters(), key) == pytest.approx(value)
    assert not playback._sound_parameter_preview
    assert experience.newNoteLengthBeats == 4
    assert experience.pianoPreviewLengthBeats == 4
    assert experience.newNoteVelocity == 90
    assert experience.dragPreviewMode == "continuous"
    assert saves.autosaveEnabled is True and saves.autosaveDelaySeconds == 300
    assert editor.state.content == before


@pytest.mark.parametrize("page,button,signal", [
    (6, "preferencesRecovery", "recoveryRequested"),
    # AI services moved to the assistant; its real controls/leave guard are
    # covered by test_assistant_unified and test_studio_workflow_ux.
])
def test_settings_navigation_buttons_emit_actions(appearance_window, qtbot, page, button, signal):
    root, _ = appearance_window
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.setProperty("preferencesAvailable", True)
    dialog.setProperty("recoveryAvailable", True)
    dialog.setProperty("preferences", dict(volume=.5, tone=.5, rate=1, noteLength=1,
                                            previewLength=1, velocity=75,
                                            dragPreview="grid", autosave=True, saveDelay=30))
    root, dialog, _ = open_appearance(appearance_window, qtbot, page)
    assert dialog.property("page") == page
    if signal is None:
        # AI service setup now lives inside settings rather than opening the
        # retired assistant-settings window. Keep checking the real UI entry.
        qtbot.waitUntil(lambda: find_item(root, button) is not None)
        assert find_item(root, button).isVisible()
        assert dialog.property("visible")
        return
    spy = QSignalSpy(getattr(dialog, signal))
    press(root, button)
    assert spy.count() == 1
    qtbot.waitUntil(lambda: not dialog.property("visible"))
