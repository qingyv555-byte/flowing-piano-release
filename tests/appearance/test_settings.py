from pathlib import Path

from PySide6.QtCore import QSettings, QUrl

from ai_piano_studio.appearance.settings import AppearanceSettings
from ai_piano_studio.appearance.scene_controller import SceneController


def test_appearance_settings_persist_valid_preferences(tmp_path) -> None:
    path = tmp_path / "appearance.ini"
    settings = AppearanceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    custom_path = Path(__file__).resolve().parents[2] / "src" / "ai_piano_studio" / "qml" / "assets" / "launch-cosmos.webp"

    settings.setThemeId("cosmic-pulse")
    settings.setDynamicBackgroundEnabled(False)
    settings.setFocusModeEnabled(True)
    settings.setMotionLevel("low")
    settings.setPowerSaveEnabled(True)
    settings.setBackgroundEnabled(False)
    settings.setBackgroundPreset("rhythm")
    settings.setCustomBackgroundPath(str(custom_path))
    settings.setBackgroundOpacity(0.41)
    settings.setBackgroundBlur(0.37)
    settings.setBackgroundDarkness(0.63)
    settings.setBackgroundGlow(0.52)
    settings.setBackgroundMotion(0.29)

    restored = AppearanceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert restored.themeId == "cosmic-pulse"
    assert restored.dynamicBackgroundEnabled is False
    assert restored.focusModeEnabled is True
    assert restored.motionLevel == "low"
    assert restored.powerSaveEnabled is True
    assert restored.backgroundEnabled is False
    assert restored.backgroundPreset == "rhythm"
    assert restored.customBackgroundPath == str(custom_path)
    assert restored.customBackgroundUrl == QUrl.fromLocalFile(str(custom_path)).toString()
    assert restored.backgroundSource == QUrl.fromLocalFile(str(custom_path)).toString()
    assert restored.backgroundOpacity == 0.41
    assert restored.backgroundBlur == 0.37
    assert restored.backgroundDarkness == 0.63
    assert restored.backgroundGlow == 0.52
    assert restored.backgroundMotion == 0.29


def test_appearance_settings_reject_invalid_values(tmp_path) -> None:
    settings = AppearanceSettings(settings=QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat))

    settings.setThemeId("invalid")
    settings.setMotionLevel("turbo")
    settings.setBackgroundPreset("invalid")
    settings.setCustomBackgroundPath(str(tmp_path / "missing.png"))

    assert settings.themeId == "dream-meadow"
    assert settings.motionLevel == "medium"
    assert settings.powerSaveEnabled is False
    assert settings.focusModeEnabled is False
    assert settings.backgroundPreset == "stage"
    assert settings.backgroundSource.endswith("stage-lake.png")


def test_background_preset_clears_custom_path_and_clear_restores_preset(tmp_path) -> None:
    settings = AppearanceSettings(
        settings=QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    )
    custom_path = Path(__file__).resolve().parents[2] / "src" / "ai_piano_studio" / "qml" / "assets" / "launch-cosmos.webp"

    settings.setCustomBackgroundPath(str(custom_path))
    assert settings.customBackgroundPath == str(custom_path)
    settings.setBackgroundPreset("rhythm")
    assert settings.customBackgroundPath == ""
    assert settings.backgroundSource.endswith("launch-cosmos.webp")

    settings.setCustomBackgroundPath(str(custom_path))
    settings.clearCustomBackground()
    assert settings.customBackgroundPath == ""
    assert settings.backgroundSource.endswith("launch-cosmos.webp")


def test_background_visual_parameters_are_bounded_and_presets_have_defaults(tmp_path) -> None:
    settings = AppearanceSettings(
        settings=QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    )

    settings.setBackgroundOpacity(2)
    settings.setBackgroundBlur(-1)
    settings.setBackgroundDarkness(0.6)
    settings.setBackgroundGlow(0.5)
    settings.setBackgroundMotion(0.4)
    assert settings.backgroundOpacity == 1.0
    assert settings.backgroundBlur == 0.0
    assert settings.backgroundDarkness == 0.6
    assert settings.backgroundGlow == 0.5
    assert settings.backgroundMotion == 0.4

    settings.setBackgroundPreset("stage")
    assert settings.backgroundOpacity == 0.38
    assert settings.backgroundBlur == 0.42
    assert settings.backgroundDarkness == 0.58
    assert settings.backgroundGlow == 0.55
    assert settings.backgroundMotion == 0.28


def test_scene_controller_exposes_ui_only_scene_controls(tmp_path) -> None:
    settings = QSettings(str(tmp_path / "scene.ini"), QSettings.Format.IniFormat)
    scene = SceneController(settings=settings)

    scene.setSceneId("sunset-haven")
    scene.setDynamicEnabled(False)
    scene.setMotionLevel("low")

    assert scene.sceneId == "sunset-haven"
    assert scene.dynamicEnabled is False
    assert scene.motionLevel == "low"


def test_visualizer_preferences_persist_and_reset_independently_from_background(
    tmp_path,
) -> None:
    path = tmp_path / "appearance.ini"
    settings = AppearanceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    settings.setBackgroundOpacity(0.17)
    settings.setVisualizerEnabled(False)
    settings.setVisualizerPreset("immersive")
    settings.setVisualizerIntensity(82)
    settings.setVisualizerSensitivity(23)
    settings.setVisualizerSmoothness(91)
    settings.setVisualizerBandCount(48)
    settings.setVisualizerSpectrumHeight("high")
    settings.setVisualizerGlowSpread("wide")
    settings.setVisualizerLowGain(155)
    settings.setVisualizerReadabilityProtection(False)

    restored = AppearanceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    assert restored.visualizerEnabled is False
    assert restored.visualizerPreset == "custom"
    assert restored.visualizerIntensity == 82
    assert restored.visualizerSensitivity == 23
    assert restored.visualizerSmoothness == 91
    assert restored.visualizerBandCount == 48
    assert restored.visualizerSpectrumHeight == "high"
    assert restored.visualizerGlowSpread == "wide"
    assert restored.visualizerLowGain == 155
    assert restored.visualizerReadabilityProtection is False
    assert restored.backgroundOpacity == 0.17

    restored.resetVisualizerDefaults()

    assert restored.visualizerPreset == "balanced"
    assert restored.visualizerEnabled is False
    assert restored.visualizerIntensity == 38
    assert restored.visualizerBandCount == 32
    assert restored.visualizerReadabilityProtection is True
    assert restored.backgroundOpacity == 0.17


def test_visualizer_manual_edit_switches_to_custom_and_invalid_values_are_bounded(
    tmp_path,
) -> None:
    settings = AppearanceSettings(
        settings=QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    )

    settings.setVisualizerPreset("balanced")
    settings.setVisualizerIntensity(150)
    settings.setVisualizerSensitivity(-10)
    settings.setVisualizerBandCount(99)
    settings.setVisualizerSpectrumHeight("giant")
    settings.setVisualizerGlowSpread("giant")

    assert settings.visualizerPreset == "custom"
    assert settings.visualizerIntensity == 100
    assert settings.visualizerSensitivity == 0
    assert settings.visualizerBandCount == 32
    assert settings.visualizerSpectrumHeight == "mid"
    assert settings.visualizerGlowSpread == "low"


def test_visualizer_color_preferences_accept_only_valid_colors(tmp_path) -> None:
    settings = AppearanceSettings(
        settings=QSettings(
            str(tmp_path / "appearance-colors.ini"), QSettings.Format.IniFormat
        )
    )

    original = settings.visualizerLowColor
    settings.setVisualizerLowColor("not-a-color")
    assert settings.visualizerLowColor == original

    settings.setVisualizerLowColor("#123456")
    assert settings.visualizerLowColor == "#123456"
