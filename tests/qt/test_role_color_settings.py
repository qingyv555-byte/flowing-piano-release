"""End-user role colors and theme-following are separate, transactional."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor

from tests.qt.test_studio_appearance import appearance, appearance_window
from tests.qt.test_settings_interactions import open_appearance, press, reveal
from tests.qt.test_studio_workflows import qml


def test_uniform_green_is_shared_by_all_roles_and_can_be_changed(appearance_window, qtbot):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    theme = root.findChild(QObject, 'testTheme')
    roles = ('melody', 'accompaniment', 'bass', 'drums', 'other')
    for theme_id, color in (('paper', '#36775c'), ('ink', '#89c1a6')):
        settings.setValue('themeId', theme_id)
        assert {qml(theme, f'layerRoleColor("{role}")').name() for role in roles} == {color}
    settings.applyCustomNoteColor('#36775c')
    assert {qml(theme, f'layerRoleColor("{role}")').name() for role in roles} == {'#36775c'}
    settings.applyNotePalette('gale')
    assert {qml(theme, f'layerRoleColor("{role}")').name() for role in roles} == {'#c77d62'}
    press(root, 'studioRoleColorsAuto')
    assert len({qml(theme, f'layerRoleColor("{role}")').name() for role in roles}) == len(roles)
    press(root, 'studioRoleColorsUniform')
    assert len({qml(theme, f'layerRoleColor("{role}")').name() for role in roles}) == 1
    dialog.close()


def test_old_default_migrates_without_overwriting_explicit_new_mode(appearance):
    import json
    original = dict(appearance.values)
    original.pop('noteColorDefaultsVersion')
    original['roleColorMode'] = 'auto'
    appearance._settings.setValue('studioAppearance/values', json.dumps(original))
    migrated = type(appearance)(appearance._settings, appearance._asset_home)
    assert migrated.values['roleColorMode'] == 'uniform'
    assert json.loads(appearance._settings.value('studioAppearance/values')) == original
    migrated.beginPreview()
    migrated.setValue('roleColorMode', 'auto')
    assert migrated.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values['roleColorMode'] == 'auto'


def test_role_color_controls_are_real_and_preserve_user_colors(
        appearance_window, qtbot):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    original = dict(settings.values)
    assert original["roleColorMode"] == "uniform"
    assert settings._settings.allKeys() == []

    settings.applyCustomNoteColor("#DC45A8")
    assert settings.values["noteColorSource"] == "custom"
    press(root, "studioRoleColorsAuto")
    assert settings.values["noteColorSource"] == "palette"
    assert settings.values["customNoteColor"] == "#dc45a8"

    press(root, "studioRoleColorsCustom")
    assert settings.values["roleColorMode"] == "custom"
    press(root, "studioRolePick-roleMelodyColor")
    picker = root.findChild(QObject, "studioRoleNoteColorPicker")
    qtbot.waitUntil(lambda: picker.property("visible"))
    picker.setProperty("selectedColor", QColor("#E8A14D"))
    picker.accepted.emit()
    picker.close()
    assert settings.values["roleMelodyColor"] == "#e8a14d"

    field = reveal(root, "studioRoleHex-roleAccompanimentColor")
    field.setProperty("text", "#2A85B6")
    field.editingFinished.emit()
    assert settings.values["roleAccompanimentColor"] == "#2a85b6"
    assert settings.values["roleBassColor"] == original["roleBassColor"]

    for theme in ("paper", "ink"):
        press(root, "studioTheme-" + theme)
        assert settings.values["roleMelodyColor"] == "#e8a14d"
        assert settings.values["roleAccompanimentColor"] == "#2a85b6"
        assert settings.values["roleColorMode"] == "custom"
    press(root, "studioRoleColorsAuto")
    assert settings.values["roleColorMode"] == "auto"
    assert settings.values["roleMelodyColor"] == "#e8a14d"
    press(root, "studioRoleColorsCustom")
    assert settings.values["roleMelodyColor"] == "#e8a14d"

    # Preview cannot silently rewrite the user's settings.
    assert settings._settings.allKeys() == []
    press(root, "studioAppearanceCancel")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert settings.values == original


def test_role_colors_apply_and_reload_without_losing_palette(
        appearance_window, qtbot):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    press(root, "studioRoleColorsCustom")
    settings.setValue("roleMelodyColor", "#A974F0")
    settings.setValue("roleBassColor", "#45A797")
    press(root, "studioAppearanceApply")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    reopened = type(settings)(settings._settings, settings._asset_home)
    assert reopened.values["roleColorMode"] == "custom"
    assert reopened.values["roleMelodyColor"] == "#a974f0"
    assert reopened.values["roleBassColor"] == "#45a797"
    reopened.setValue("roleColorMode", "auto")
    assert reopened.values["roleColorMode"] == "auto"
    assert reopened.values["roleMelodyColor"] == "#a974f0"
    reopened.setValue("roleMelodyColor", "#bad-value")
    assert reopened.values["roleMelodyColor"] == "#a974f0"
