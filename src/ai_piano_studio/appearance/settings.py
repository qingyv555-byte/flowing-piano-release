from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Property, QSettings, QUrl, Signal, Slot
from PySide6.QtGui import QColor


_BACKGROUND_PRESETS = frozenset({"clean", "rhythm", "stage"})
_STAGE_BACKGROUND_PATH = Path(__file__).resolve().parents[1] / "qml" / "assets" / "stage-lake.png"
_RHYTHM_BACKGROUND_PATH = Path(__file__).resolve().parents[1] / "qml" / "assets" / "launch-cosmos.webp"
_BACKGROUND_PRESET_DEFAULTS = {
    "clean": {"opacity": 0.0, "blur": 0.0, "darkness": 0.70, "glow": 0.0, "motion": 0.0},
    "rhythm": {"opacity": 0.24, "blur": 0.34, "darkness": 0.62, "glow": 0.34, "motion": 0.18},
    "stage": {"opacity": 0.38, "blur": 0.42, "darkness": 0.58, "glow": 0.55, "motion": 0.28},
}

_VISUALIZER_PRESETS = frozenset({"minimal", "balanced", "immersive", "custom"})
_VISUALIZER_HEIGHTS = frozenset({"low", "mid", "high"})
_VISUALIZER_SPREADS = frozenset({"low", "mid", "wide"})
_VISUALIZER_WIDTHS = frozenset({"thin", "standard"})
_VISUALIZER_COUNTS = frozenset({"few", "standard", "many"})
_VISUALIZER_SPEEDS = frozenset({"slow", "standard", "fast"})
_VISUALIZER_BAND_COUNTS = frozenset({24, 32, 48})
_VISUALIZER_COLOR_MODES = frozenset({"frequency", "monochrome", "custom"})
_VISUALIZER_PRESENTATION_REVISION = 1

_VISUALIZER_PRESET_DEFAULTS = {
    "minimal": {
        "intensity": 22,
        "sensitivity": 50,
        "smoothness": 76,
        "spectrumEnabled": True,
        "spectrumStrength": 32,
        "spectrumHeight": "low",
        "spectrumOpacity": 42,
        "bandCount": 24,
        "barWidth": "thin",
        "glowEnabled": False,
        "glowIntensity": 0,
        "glowSpread": "low",
        "ribbonEnabled": False,
        "ribbonIntensity": 0,
        "ribbonCount": "few",
        "ribbonSpeed": "slow",
        "beatEnabled": False,
        "beatIntensity": 0,
    },
    "balanced": {
        "intensity": 38,
        "sensitivity": 50,
        "smoothness": 68,
        "spectrumEnabled": True,
        "spectrumStrength": 55,
        "spectrumHeight": "mid",
        "spectrumOpacity": 68,
        "bandCount": 32,
        "barWidth": "thin",
        "glowEnabled": True,
        "glowIntensity": 22,
        "glowSpread": "low",
        "ribbonEnabled": True,
        "ribbonIntensity": 18,
        "ribbonCount": "few",
        "ribbonSpeed": "slow",
        "beatEnabled": True,
        "beatIntensity": 15,
    },
    "immersive": {
        "intensity": 68,
        "sensitivity": 58,
        "smoothness": 54,
        "spectrumEnabled": True,
        "spectrumStrength": 78,
        "spectrumHeight": "high",
        "spectrumOpacity": 82,
        "bandCount": 48,
        "barWidth": "standard",
        "glowEnabled": True,
        "glowIntensity": 48,
        "glowSpread": "mid",
        "ribbonEnabled": True,
        "ribbonIntensity": 38,
        "ribbonCount": "standard",
        "ribbonSpeed": "standard",
        "beatEnabled": True,
        "beatIntensity": 20,
    },
}

_VISUALIZER_COMMON_DEFAULTS = {
    "enabled": False,
    "lowGain": 100,
    "midGain": 100,
    "highGain": 100,
    "colorMode": "frequency",
    "lowColor": "#45D6C5",
    "midColor": "#6EA8FF",
    "highColor": "#B88CFF",
    "saturation": 35,
    "readabilityProtection": True,
}


class AppearanceSettings(QObject):
    """Small QSettings-backed preference bridge for QML appearance only."""

    themeIdChanged = Signal()
    dynamicBackgroundEnabledChanged = Signal()
    motionLevelChanged = Signal()
    powerSaveEnabledChanged = Signal()
    focusModeEnabledChanged = Signal()
    backgroundEnabledChanged = Signal()
    backgroundPresetChanged = Signal()
    customBackgroundPathChanged = Signal()
    backgroundSourceChanged = Signal()
    backgroundOpacityChanged = Signal()
    backgroundBlurChanged = Signal()
    backgroundDarknessChanged = Signal()
    backgroundGlowChanged = Signal()
    backgroundMotionChanged = Signal()
    visualizerEnabledChanged = Signal()
    visualizerPresetChanged = Signal()
    visualizerIntensityChanged = Signal()
    visualizerSensitivityChanged = Signal()
    visualizerSmoothnessChanged = Signal()
    visualizerSpectrumEnabledChanged = Signal()
    visualizerSpectrumStrengthChanged = Signal()
    visualizerSpectrumHeightChanged = Signal()
    visualizerSpectrumOpacityChanged = Signal()
    visualizerBandCountChanged = Signal()
    visualizerBarWidthChanged = Signal()
    visualizerGlowEnabledChanged = Signal()
    visualizerGlowIntensityChanged = Signal()
    visualizerGlowSpreadChanged = Signal()
    visualizerRibbonEnabledChanged = Signal()
    visualizerRibbonIntensityChanged = Signal()
    visualizerRibbonCountChanged = Signal()
    visualizerRibbonSpeedChanged = Signal()
    visualizerBeatEnabledChanged = Signal()
    visualizerBeatIntensityChanged = Signal()
    visualizerLowGainChanged = Signal()
    visualizerMidGainChanged = Signal()
    visualizerHighGainChanged = Signal()
    visualizerColorModeChanged = Signal()
    visualizerLowColorChanged = Signal()
    visualizerMidColorChanged = Signal()
    visualizerHighColorChanged = Signal()
    visualizerSaturationChanged = Signal()
    visualizerReadabilityProtectionChanged = Signal()

    _THEMES = frozenset({"dream-meadow", "cosmic-pulse", "sunset-haven"})
    _MOTION_LEVELS = frozenset({"low", "medium", "high"})

    def __init__(self, parent=None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("AIPianoStudio", "AI Piano Studio")
        theme = str(self._settings.value("appearance/themeId", "dream-meadow"))
        motion = str(self._settings.value("appearance/motionLevel", "medium"))
        self._theme_id = theme if theme in self._THEMES else "dream-meadow"
        self._dynamic_background_enabled = self._settings.value(
            "appearance/dynamicBackgroundEnabled", True, type=bool
        )
        self._motion_level = motion if motion in self._MOTION_LEVELS else "medium"
        self._power_save_enabled = self._settings.value(
            "appearance/powerSaveEnabled", False, type=bool
        )
        self._focus_mode_enabled = self._settings.value(
            "appearance/focusModeEnabled", False, type=bool
        )
        self._background_enabled = self._settings.value(
            "appearance/backgroundEnabled", True, type=bool
        )
        background_preset = str(self._settings.value("appearance/backgroundPreset", "stage"))
        self._background_preset = (
            background_preset if background_preset in _BACKGROUND_PRESETS else "stage"
        )
        self._custom_background_path = self._normalize_local_path(
            self._settings.value("appearance/customBackgroundPath", "")
        )
        defaults = _BACKGROUND_PRESET_DEFAULTS[self._background_preset]
        self._background_opacity = self._read_background_parameter(
            "opacity", defaults["opacity"]
        )
        self._background_blur = self._read_background_parameter("blur", defaults["blur"])
        self._background_darkness = self._read_background_parameter(
            "darkness", defaults["darkness"]
        )
        self._background_glow = self._read_background_parameter("glow", defaults["glow"])
        self._background_motion = self._read_background_parameter("motion", defaults["motion"])
        self._retire_legacy_visualizer_presentation()
        stored_visualizer_preset = str(
            self._settings.value("appearance/visualizerPreset", "balanced")
        )
        self._visualizer_preset = (
            stored_visualizer_preset
            if stored_visualizer_preset in _VISUALIZER_PRESETS
            else "balanced"
        )
        visualizer_defaults = _VISUALIZER_PRESET_DEFAULTS.get(
            self._visualizer_preset,
            _VISUALIZER_PRESET_DEFAULTS["balanced"],
        )
        self._visualizer_enabled = self._read_visualizer_bool(
            "Enabled", _VISUALIZER_COMMON_DEFAULTS["enabled"]
        )
        self._visualizer_intensity = self._read_visualizer_int(
            "Intensity", visualizer_defaults["intensity"]
        )
        self._visualizer_sensitivity = self._read_visualizer_int(
            "Sensitivity", visualizer_defaults["sensitivity"]
        )
        self._visualizer_smoothness = self._read_visualizer_int(
            "Smoothness", visualizer_defaults["smoothness"]
        )
        self._visualizer_spectrum_enabled = self._read_visualizer_bool(
            "SpectrumEnabled", visualizer_defaults["spectrumEnabled"]
        )
        self._visualizer_spectrum_strength = self._read_visualizer_int(
            "SpectrumStrength", visualizer_defaults["spectrumStrength"]
        )
        self._visualizer_spectrum_height = self._read_visualizer_choice(
            "SpectrumHeight", visualizer_defaults["spectrumHeight"], _VISUALIZER_HEIGHTS
        )
        self._visualizer_spectrum_opacity = self._read_visualizer_int(
            "SpectrumOpacity", visualizer_defaults["spectrumOpacity"]
        )
        self._visualizer_band_count = self._read_visualizer_band_count(
            visualizer_defaults["bandCount"]
        )
        self._visualizer_bar_width = self._read_visualizer_choice(
            "BarWidth", visualizer_defaults["barWidth"], _VISUALIZER_WIDTHS
        )
        self._visualizer_glow_enabled = self._read_visualizer_bool(
            "GlowEnabled", visualizer_defaults["glowEnabled"]
        )
        self._visualizer_glow_intensity = self._read_visualizer_int(
            "GlowIntensity", visualizer_defaults["glowIntensity"]
        )
        self._visualizer_glow_spread = self._read_visualizer_choice(
            "GlowSpread", visualizer_defaults["glowSpread"], _VISUALIZER_SPREADS
        )
        self._visualizer_ribbon_enabled = self._read_visualizer_bool(
            "RibbonEnabled", visualizer_defaults["ribbonEnabled"]
        )
        self._visualizer_ribbon_intensity = self._read_visualizer_int(
            "RibbonIntensity", visualizer_defaults["ribbonIntensity"]
        )
        self._visualizer_ribbon_count = self._read_visualizer_choice(
            "RibbonCount", visualizer_defaults["ribbonCount"], _VISUALIZER_COUNTS
        )
        self._visualizer_ribbon_speed = self._read_visualizer_choice(
            "RibbonSpeed", visualizer_defaults["ribbonSpeed"], _VISUALIZER_SPEEDS
        )
        self._visualizer_beat_enabled = self._read_visualizer_bool(
            "BeatEnabled", visualizer_defaults["beatEnabled"]
        )
        self._visualizer_beat_intensity = self._read_visualizer_int(
            "BeatIntensity", visualizer_defaults["beatIntensity"]
        )
        self._visualizer_low_gain = self._read_visualizer_int(
            "LowGain", _VISUALIZER_COMMON_DEFAULTS["lowGain"], maximum=200
        )
        self._visualizer_mid_gain = self._read_visualizer_int(
            "MidGain", _VISUALIZER_COMMON_DEFAULTS["midGain"], maximum=200
        )
        self._visualizer_high_gain = self._read_visualizer_int(
            "HighGain", _VISUALIZER_COMMON_DEFAULTS["highGain"], maximum=200
        )
        self._visualizer_color_mode = self._read_visualizer_choice(
            "ColorMode", _VISUALIZER_COMMON_DEFAULTS["colorMode"], _VISUALIZER_COLOR_MODES
        )
        self._visualizer_low_color = self._read_visualizer_text(
            "LowColor", _VISUALIZER_COMMON_DEFAULTS["lowColor"]
        )
        self._visualizer_mid_color = self._read_visualizer_text(
            "MidColor", _VISUALIZER_COMMON_DEFAULTS["midColor"]
        )
        self._visualizer_high_color = self._read_visualizer_text(
            "HighColor", _VISUALIZER_COMMON_DEFAULTS["highColor"]
        )
        self._visualizer_saturation = self._read_visualizer_int(
            "Saturation", _VISUALIZER_COMMON_DEFAULTS["saturation"]
        )
        self._visualizer_readability_protection = self._read_visualizer_bool(
            "ReadabilityProtection", _VISUALIZER_COMMON_DEFAULTS["readabilityProtection"]
        )

    @Property(str, notify=themeIdChanged)
    def themeId(self) -> str:
        return self._theme_id

    @Property(bool, notify=dynamicBackgroundEnabledChanged)
    def dynamicBackgroundEnabled(self) -> bool:
        return self._dynamic_background_enabled

    @Property(str, notify=motionLevelChanged)
    def motionLevel(self) -> str:
        return self._motion_level

    @Property(bool, notify=powerSaveEnabledChanged)
    def powerSaveEnabled(self) -> bool:
        return self._power_save_enabled

    @Property(bool, notify=focusModeEnabledChanged)
    def focusModeEnabled(self) -> bool:
        return self._focus_mode_enabled

    @Property(bool, notify=backgroundEnabledChanged)
    def backgroundEnabled(self) -> bool:
        return self._background_enabled

    @Property(str, notify=backgroundPresetChanged)
    def backgroundPreset(self) -> str:
        return self._background_preset

    @Property(str, notify=customBackgroundPathChanged)
    def customBackgroundPath(self) -> str:
        return self._custom_background_path

    @Property(str, notify=customBackgroundPathChanged)
    def customBackgroundUrl(self) -> str:
        if not self._custom_background_path:
            return ""
        return QUrl.fromLocalFile(self._custom_background_path).toString()

    @Property(str, notify=backgroundSourceChanged)
    def backgroundSource(self) -> str:
        if self._custom_background_path:
            custom_path = Path(self._custom_background_path)
            if custom_path.is_file():
                return QUrl.fromLocalFile(str(custom_path)).toString()
            return QUrl.fromLocalFile(str(_STAGE_BACKGROUND_PATH)).toString()

        if self._background_preset == "clean":
            return ""
        asset_path = (
            _RHYTHM_BACKGROUND_PATH
            if self._background_preset == "rhythm"
            else _STAGE_BACKGROUND_PATH
        )
        return QUrl.fromLocalFile(str(asset_path)).toString()

    @Property(float, notify=backgroundOpacityChanged)
    def backgroundOpacity(self) -> float:
        return self._background_opacity

    @Property(float, notify=backgroundBlurChanged)
    def backgroundBlur(self) -> float:
        return self._background_blur

    @Property(float, notify=backgroundDarknessChanged)
    def backgroundDarkness(self) -> float:
        return self._background_darkness

    @Property(float, notify=backgroundGlowChanged)
    def backgroundGlow(self) -> float:
        return self._background_glow

    @Property(float, notify=backgroundMotionChanged)
    def backgroundMotion(self) -> float:
        return self._background_motion

    @Property(bool, notify=visualizerEnabledChanged)
    def visualizerEnabled(self) -> bool:
        return self._visualizer_enabled

    @Property(str, notify=visualizerPresetChanged)
    def visualizerPreset(self) -> str:
        return self._visualizer_preset

    @Property(int, notify=visualizerIntensityChanged)
    def visualizerIntensity(self) -> int:
        return self._visualizer_intensity

    @Property(int, notify=visualizerSensitivityChanged)
    def visualizerSensitivity(self) -> int:
        return self._visualizer_sensitivity

    @Property(int, notify=visualizerSmoothnessChanged)
    def visualizerSmoothness(self) -> int:
        return self._visualizer_smoothness

    @Property(bool, notify=visualizerSpectrumEnabledChanged)
    def visualizerSpectrumEnabled(self) -> bool:
        return self._visualizer_spectrum_enabled

    @Property(int, notify=visualizerSpectrumStrengthChanged)
    def visualizerSpectrumStrength(self) -> int:
        return self._visualizer_spectrum_strength

    @Property(str, notify=visualizerSpectrumHeightChanged)
    def visualizerSpectrumHeight(self) -> str:
        return self._visualizer_spectrum_height

    @Property(int, notify=visualizerSpectrumOpacityChanged)
    def visualizerSpectrumOpacity(self) -> int:
        return self._visualizer_spectrum_opacity

    @Property(int, notify=visualizerBandCountChanged)
    def visualizerBandCount(self) -> int:
        return self._visualizer_band_count

    @Property(str, notify=visualizerBarWidthChanged)
    def visualizerBarWidth(self) -> str:
        return self._visualizer_bar_width

    @Property(bool, notify=visualizerGlowEnabledChanged)
    def visualizerGlowEnabled(self) -> bool:
        return self._visualizer_glow_enabled

    @Property(int, notify=visualizerGlowIntensityChanged)
    def visualizerGlowIntensity(self) -> int:
        return self._visualizer_glow_intensity

    @Property(str, notify=visualizerGlowSpreadChanged)
    def visualizerGlowSpread(self) -> str:
        return self._visualizer_glow_spread

    @Property(bool, notify=visualizerRibbonEnabledChanged)
    def visualizerRibbonEnabled(self) -> bool:
        return self._visualizer_ribbon_enabled

    @Property(int, notify=visualizerRibbonIntensityChanged)
    def visualizerRibbonIntensity(self) -> int:
        return self._visualizer_ribbon_intensity

    @Property(str, notify=visualizerRibbonCountChanged)
    def visualizerRibbonCount(self) -> str:
        return self._visualizer_ribbon_count

    @Property(str, notify=visualizerRibbonSpeedChanged)
    def visualizerRibbonSpeed(self) -> str:
        return self._visualizer_ribbon_speed

    @Property(bool, notify=visualizerBeatEnabledChanged)
    def visualizerBeatEnabled(self) -> bool:
        return self._visualizer_beat_enabled

    @Property(int, notify=visualizerBeatIntensityChanged)
    def visualizerBeatIntensity(self) -> int:
        return self._visualizer_beat_intensity

    @Property(int, notify=visualizerLowGainChanged)
    def visualizerLowGain(self) -> int:
        return self._visualizer_low_gain

    @Property(int, notify=visualizerMidGainChanged)
    def visualizerMidGain(self) -> int:
        return self._visualizer_mid_gain

    @Property(int, notify=visualizerHighGainChanged)
    def visualizerHighGain(self) -> int:
        return self._visualizer_high_gain

    @Property(str, notify=visualizerColorModeChanged)
    def visualizerColorMode(self) -> str:
        return self._visualizer_color_mode

    @Property(str, notify=visualizerLowColorChanged)
    def visualizerLowColor(self) -> str:
        return self._visualizer_low_color

    @Property(str, notify=visualizerMidColorChanged)
    def visualizerMidColor(self) -> str:
        return self._visualizer_mid_color

    @Property(str, notify=visualizerHighColorChanged)
    def visualizerHighColor(self) -> str:
        return self._visualizer_high_color

    @Property(int, notify=visualizerSaturationChanged)
    def visualizerSaturation(self) -> int:
        return self._visualizer_saturation

    @Property(bool, notify=visualizerReadabilityProtectionChanged)
    def visualizerReadabilityProtection(self) -> bool:
        return self._visualizer_readability_protection

    @Property(str, notify=visualizerPresetChanged)
    def visualizerSettingsSummary(self) -> str:
        state = "开启" if self._visualizer_enabled else "关闭"
        return f"{state} · {self._visualizer_preset} · 强度 {self._visualizer_intensity}%"

    @Slot(str)
    def setThemeId(self, theme_id: str) -> None:
        value = theme_id if theme_id in self._THEMES else "dream-meadow"
        if value == self._theme_id:
            return
        self._theme_id = value
        self._settings.setValue("appearance/themeId", value)
        self.themeIdChanged.emit()

    @Slot(bool)
    def setDynamicBackgroundEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._dynamic_background_enabled:
            return
        self._dynamic_background_enabled = value
        self._settings.setValue("appearance/dynamicBackgroundEnabled", value)
        self.dynamicBackgroundEnabledChanged.emit()

    @Slot(str)
    def setMotionLevel(self, motion_level: str) -> None:
        value = motion_level if motion_level in self._MOTION_LEVELS else "medium"
        if value == self._motion_level:
            return
        self._motion_level = value
        self._settings.setValue("appearance/motionLevel", value)
        self.motionLevelChanged.emit()

    @Slot(bool)
    def setPowerSaveEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._power_save_enabled:
            return
        self._power_save_enabled = value
        self._settings.setValue("appearance/powerSaveEnabled", value)
        self.powerSaveEnabledChanged.emit()

    @Slot(bool)
    def setFocusModeEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._focus_mode_enabled:
            return
        self._focus_mode_enabled = value
        self._settings.setValue("appearance/focusModeEnabled", value)
        self.focusModeEnabledChanged.emit()

    @Slot(bool)
    def setBackgroundEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._background_enabled:
            return
        self._background_enabled = value
        self._settings.setValue("appearance/backgroundEnabled", value)
        self.backgroundEnabledChanged.emit()

    @Slot(str)
    def setBackgroundPreset(self, background_preset: str) -> None:
        value = background_preset if background_preset in _BACKGROUND_PRESETS else "stage"
        custom_changed = self._set_custom_background_path("")
        preset_changed = value != self._background_preset
        if preset_changed:
            self._background_preset = value
            self._settings.setValue("appearance/backgroundPreset", value)
            self.backgroundPresetChanged.emit()
        self._apply_background_preset_defaults(value)
        if custom_changed:
            self.customBackgroundPathChanged.emit()
        if preset_changed or custom_changed:
            self.backgroundSourceChanged.emit()

    @Slot(str)
    def setCustomBackgroundPath(self, path: str) -> None:
        if not self._set_custom_background_path(path):
            return
        self.customBackgroundPathChanged.emit()
        self.backgroundSourceChanged.emit()

    @Slot()
    def clearCustomBackground(self) -> None:
        self.setCustomBackgroundPath("")

    @Slot(float)
    def setBackgroundOpacity(self, value: float) -> None:
        self._set_background_parameter(
            "opacity", value, "_background_opacity", "backgroundOpacityChanged"
        )

    @Slot(float)
    def setBackgroundBlur(self, value: float) -> None:
        self._set_background_parameter("blur", value, "_background_blur", "backgroundBlurChanged")

    @Slot(float)
    def setBackgroundDarkness(self, value: float) -> None:
        self._set_background_parameter(
            "darkness", value, "_background_darkness", "backgroundDarknessChanged"
        )

    @Slot(float)
    def setBackgroundGlow(self, value: float) -> None:
        self._set_background_parameter("glow", value, "_background_glow", "backgroundGlowChanged")

    @Slot(float)
    def setBackgroundMotion(self, value: float) -> None:
        self._set_background_parameter(
            "motion", value, "_background_motion", "backgroundMotionChanged"
        )

    @Slot(bool)
    def setVisualizerEnabled(self, enabled: bool) -> None:
        self._set_visualizer_field(
            "Enabled",
            bool(enabled),
            "_visualizer_enabled",
            "visualizerEnabledChanged",
            mark_custom=False,
        )

    @Slot(str)
    def setVisualizerPreset(self, preset: str) -> None:
        value = preset if preset in _VISUALIZER_PRESETS else "balanced"
        if value != self._visualizer_preset:
            self._visualizer_preset = value
            self._settings.setValue("appearance/visualizerPreset", value)
            self.visualizerPresetChanged.emit()
        if value != "custom":
            self._apply_visualizer_preset_defaults(value)

    @Slot(int)
    def setVisualizerIntensity(self, value: int) -> None:
        self._set_visualizer_int_field(
            "Intensity", value, "_visualizer_intensity", "visualizerIntensityChanged"
        )

    @Slot(int)
    def setVisualizerSensitivity(self, value: int) -> None:
        self._set_visualizer_int_field(
            "Sensitivity", value, "_visualizer_sensitivity", "visualizerSensitivityChanged"
        )

    @Slot(int)
    def setVisualizerSmoothness(self, value: int) -> None:
        self._set_visualizer_int_field(
            "Smoothness", value, "_visualizer_smoothness", "visualizerSmoothnessChanged"
        )

    @Slot(bool)
    def setVisualizerSpectrumEnabled(self, value: bool) -> None:
        self._set_visualizer_field(
            "SpectrumEnabled", bool(value), "_visualizer_spectrum_enabled",
            "visualizerSpectrumEnabledChanged"
        )

    @Slot(int)
    def setVisualizerSpectrumStrength(self, value: int) -> None:
        self._set_visualizer_int_field(
            "SpectrumStrength", value, "_visualizer_spectrum_strength",
            "visualizerSpectrumStrengthChanged"
        )

    @Slot(str)
    def setVisualizerSpectrumHeight(self, value: str) -> None:
        self._set_visualizer_choice_field(
            "SpectrumHeight", value, "_visualizer_spectrum_height",
            "visualizerSpectrumHeightChanged", _VISUALIZER_HEIGHTS, "mid"
        )

    @Slot(int)
    def setVisualizerSpectrumOpacity(self, value: int) -> None:
        self._set_visualizer_int_field(
            "SpectrumOpacity", value, "_visualizer_spectrum_opacity",
            "visualizerSpectrumOpacityChanged"
        )

    @Slot(int)
    def setVisualizerBandCount(self, value: int) -> None:
        try:
            bounded = int(value)
        except (TypeError, ValueError):
            bounded = 32
        if bounded not in _VISUALIZER_BAND_COUNTS:
            bounded = 32
        self._set_visualizer_field(
            "BandCount", bounded, "_visualizer_band_count", "visualizerBandCountChanged"
        )

    @Slot(str)
    def setVisualizerBarWidth(self, value: str) -> None:
        self._set_visualizer_choice_field(
            "BarWidth", value, "_visualizer_bar_width", "visualizerBarWidthChanged",
            _VISUALIZER_WIDTHS, "thin"
        )

    @Slot(bool)
    def setVisualizerGlowEnabled(self, value: bool) -> None:
        self._set_visualizer_field(
            "GlowEnabled", bool(value), "_visualizer_glow_enabled",
            "visualizerGlowEnabledChanged"
        )

    @Slot(int)
    def setVisualizerGlowIntensity(self, value: int) -> None:
        self._set_visualizer_int_field(
            "GlowIntensity", value, "_visualizer_glow_intensity",
            "visualizerGlowIntensityChanged"
        )

    @Slot(str)
    def setVisualizerGlowSpread(self, value: str) -> None:
        self._set_visualizer_choice_field(
            "GlowSpread", value, "_visualizer_glow_spread", "visualizerGlowSpreadChanged",
            _VISUALIZER_SPREADS, "low"
        )

    @Slot(bool)
    def setVisualizerRibbonEnabled(self, value: bool) -> None:
        self._set_visualizer_field(
            "RibbonEnabled", bool(value), "_visualizer_ribbon_enabled",
            "visualizerRibbonEnabledChanged"
        )

    @Slot(int)
    def setVisualizerRibbonIntensity(self, value: int) -> None:
        self._set_visualizer_int_field(
            "RibbonIntensity", value, "_visualizer_ribbon_intensity",
            "visualizerRibbonIntensityChanged"
        )

    @Slot(str)
    def setVisualizerRibbonCount(self, value: str) -> None:
        self._set_visualizer_choice_field(
            "RibbonCount", value, "_visualizer_ribbon_count", "visualizerRibbonCountChanged",
            _VISUALIZER_COUNTS, "few"
        )

    @Slot(str)
    def setVisualizerRibbonSpeed(self, value: str) -> None:
        self._set_visualizer_choice_field(
            "RibbonSpeed", value, "_visualizer_ribbon_speed", "visualizerRibbonSpeedChanged",
            _VISUALIZER_SPEEDS, "slow"
        )

    @Slot(bool)
    def setVisualizerBeatEnabled(self, value: bool) -> None:
        self._set_visualizer_field(
            "BeatEnabled", bool(value), "_visualizer_beat_enabled",
            "visualizerBeatEnabledChanged"
        )

    @Slot(int)
    def setVisualizerBeatIntensity(self, value: int) -> None:
        self._set_visualizer_int_field(
            "BeatIntensity", value, "_visualizer_beat_intensity",
            "visualizerBeatIntensityChanged"
        )

    @Slot(int)
    def setVisualizerLowGain(self, value: int) -> None:
        self._set_visualizer_int_field(
            "LowGain", value, "_visualizer_low_gain", "visualizerLowGainChanged", maximum=200
        )

    @Slot(int)
    def setVisualizerMidGain(self, value: int) -> None:
        self._set_visualizer_int_field(
            "MidGain", value, "_visualizer_mid_gain", "visualizerMidGainChanged", maximum=200
        )

    @Slot(int)
    def setVisualizerHighGain(self, value: int) -> None:
        self._set_visualizer_int_field(
            "HighGain", value, "_visualizer_high_gain", "visualizerHighGainChanged", maximum=200
        )

    @Slot(str)
    def setVisualizerColorMode(self, value: str) -> None:
        self._set_visualizer_choice_field(
            "ColorMode", value, "_visualizer_color_mode", "visualizerColorModeChanged",
            _VISUALIZER_COLOR_MODES, "frequency"
        )

    @Slot(str)
    def setVisualizerLowColor(self, value: str) -> None:
        normalized = self._normalize_visualizer_color(value)
        if normalized is not None:
            self._set_visualizer_text_field("LowColor", normalized, "_visualizer_low_color", "visualizerLowColorChanged")

    @Slot(str)
    def setVisualizerMidColor(self, value: str) -> None:
        normalized = self._normalize_visualizer_color(value)
        if normalized is not None:
            self._set_visualizer_text_field("MidColor", normalized, "_visualizer_mid_color", "visualizerMidColorChanged")

    @Slot(str)
    def setVisualizerHighColor(self, value: str) -> None:
        normalized = self._normalize_visualizer_color(value)
        if normalized is not None:
            self._set_visualizer_text_field("HighColor", normalized, "_visualizer_high_color", "visualizerHighColorChanged")

    @Slot(int)
    def setVisualizerSaturation(self, value: int) -> None:
        self._set_visualizer_int_field(
            "Saturation", value, "_visualizer_saturation", "visualizerSaturationChanged"
        )

    @Slot(bool)
    def setVisualizerReadabilityProtection(self, value: bool) -> None:
        self._set_visualizer_field(
            "ReadabilityProtection", bool(value), "_visualizer_readability_protection",
            "visualizerReadabilityProtectionChanged"
        )

    @Slot()
    def resetVisualizerDefaults(self) -> None:
        if self._visualizer_preset != "balanced":
            self._visualizer_preset = "balanced"
            self._settings.setValue("appearance/visualizerPreset", "balanced")
            self.visualizerPresetChanged.emit()
        self._apply_visualizer_preset_defaults("balanced")
        common_fields = (
            ("Enabled", _VISUALIZER_COMMON_DEFAULTS["enabled"], "_visualizer_enabled", "visualizerEnabledChanged"),
            ("LowGain", _VISUALIZER_COMMON_DEFAULTS["lowGain"], "_visualizer_low_gain", "visualizerLowGainChanged"),
            ("MidGain", _VISUALIZER_COMMON_DEFAULTS["midGain"], "_visualizer_mid_gain", "visualizerMidGainChanged"),
            ("HighGain", _VISUALIZER_COMMON_DEFAULTS["highGain"], "_visualizer_high_gain", "visualizerHighGainChanged"),
            ("ColorMode", _VISUALIZER_COMMON_DEFAULTS["colorMode"], "_visualizer_color_mode", "visualizerColorModeChanged"),
            ("LowColor", _VISUALIZER_COMMON_DEFAULTS["lowColor"], "_visualizer_low_color", "visualizerLowColorChanged"),
            ("MidColor", _VISUALIZER_COMMON_DEFAULTS["midColor"], "_visualizer_mid_color", "visualizerMidColorChanged"),
            ("HighColor", _VISUALIZER_COMMON_DEFAULTS["highColor"], "_visualizer_high_color", "visualizerHighColorChanged"),
            ("Saturation", _VISUALIZER_COMMON_DEFAULTS["saturation"], "_visualizer_saturation", "visualizerSaturationChanged"),
            ("ReadabilityProtection", _VISUALIZER_COMMON_DEFAULTS["readabilityProtection"], "_visualizer_readability_protection", "visualizerReadabilityProtectionChanged"),
        )
        for key, default, attribute, signal_name in common_fields:
            self._set_visualizer_field(
                key, default, attribute, signal_name, mark_custom=False
            )

    def _retire_legacy_visualizer_presentation(self) -> None:
        """Disable the temporary reactive band once for existing installs.

        The renderer remains opt-in for development while a later, professional
        playback presentation replaces it.  A revision marker keeps a user who
        explicitly turns it back on from being reset on every startup.
        """
        value = self._settings.value("appearance/visualizerPresentationRevision", 0)
        try:
            revision = int(value)
        except (TypeError, ValueError):
            revision = 0
        if revision >= _VISUALIZER_PRESENTATION_REVISION:
            return
        self._settings.setValue("appearance/visualizerEnabled", False)
        self._settings.setValue(
            "appearance/visualizerPresentationRevision",
            _VISUALIZER_PRESENTATION_REVISION,
        )

    def _apply_visualizer_preset_defaults(self, preset: str) -> None:
        defaults = _VISUALIZER_PRESET_DEFAULTS[preset]
        fields = {
            "intensity": ("Intensity", "_visualizer_intensity", "visualizerIntensityChanged"),
            "sensitivity": ("Sensitivity", "_visualizer_sensitivity", "visualizerSensitivityChanged"),
            "smoothness": ("Smoothness", "_visualizer_smoothness", "visualizerSmoothnessChanged"),
            "spectrumEnabled": ("SpectrumEnabled", "_visualizer_spectrum_enabled", "visualizerSpectrumEnabledChanged"),
            "spectrumStrength": ("SpectrumStrength", "_visualizer_spectrum_strength", "visualizerSpectrumStrengthChanged"),
            "spectrumHeight": ("SpectrumHeight", "_visualizer_spectrum_height", "visualizerSpectrumHeightChanged"),
            "spectrumOpacity": ("SpectrumOpacity", "_visualizer_spectrum_opacity", "visualizerSpectrumOpacityChanged"),
            "bandCount": ("BandCount", "_visualizer_band_count", "visualizerBandCountChanged"),
            "barWidth": ("BarWidth", "_visualizer_bar_width", "visualizerBarWidthChanged"),
            "glowEnabled": ("GlowEnabled", "_visualizer_glow_enabled", "visualizerGlowEnabledChanged"),
            "glowIntensity": ("GlowIntensity", "_visualizer_glow_intensity", "visualizerGlowIntensityChanged"),
            "glowSpread": ("GlowSpread", "_visualizer_glow_spread", "visualizerGlowSpreadChanged"),
            "ribbonEnabled": ("RibbonEnabled", "_visualizer_ribbon_enabled", "visualizerRibbonEnabledChanged"),
            "ribbonIntensity": ("RibbonIntensity", "_visualizer_ribbon_intensity", "visualizerRibbonIntensityChanged"),
            "ribbonCount": ("RibbonCount", "_visualizer_ribbon_count", "visualizerRibbonCountChanged"),
            "ribbonSpeed": ("RibbonSpeed", "_visualizer_ribbon_speed", "visualizerRibbonSpeedChanged"),
            "beatEnabled": ("BeatEnabled", "_visualizer_beat_enabled", "visualizerBeatEnabledChanged"),
            "beatIntensity": ("BeatIntensity", "_visualizer_beat_intensity", "visualizerBeatIntensityChanged"),
        }
        for key, value in defaults.items():
            label, attribute, signal_name = fields[key]
            if key == "bandCount":
                bounded = value if value in _VISUALIZER_BAND_COUNTS else 32
            elif key.endswith("Enabled"):
                bounded = bool(value)
            elif key.endswith("Height"):
                bounded = value if value in _VISUALIZER_HEIGHTS else "mid"
            elif key.endswith("Spread"):
                bounded = value if value in _VISUALIZER_SPREADS else "low"
            elif key == "barWidth":
                bounded = value if value in _VISUALIZER_WIDTHS else "thin"
            elif key == "ribbonCount":
                bounded = value if value in _VISUALIZER_COUNTS else "few"
            elif key == "ribbonSpeed":
                bounded = value if value in _VISUALIZER_SPEEDS else "slow"
            else:
                bounded = max(0, min(100, int(value)))
            self._set_visualizer_field(label, bounded, attribute, signal_name, mark_custom=False)

    def _set_visualizer_int_field(
        self,
        key: str,
        value: int,
        attribute: str,
        signal_name: str,
        *,
        maximum: int = 100,
    ) -> None:
        try:
            bounded = int(value)
        except (TypeError, ValueError):
            bounded = 0
        self._set_visualizer_field(
            key, max(0, min(maximum, bounded)), attribute, signal_name
        )

    def _set_visualizer_choice_field(
        self,
        key: str,
        value: str,
        attribute: str,
        signal_name: str,
        choices: frozenset[str],
        fallback: str,
    ) -> None:
        bounded = value if value in choices else fallback
        self._set_visualizer_field(key, bounded, attribute, signal_name)

    def _set_visualizer_text_field(
        self, key: str, value: str, attribute: str, signal_name: str
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        self._set_visualizer_field(key, text, attribute, signal_name)

    def _set_visualizer_field(
        self,
        key: str,
        value: object,
        attribute: str,
        signal_name: str,
        *,
        mark_custom: bool = True,
    ) -> None:
        if getattr(self, attribute) == value:
            return
        if mark_custom:
            self._mark_visualizer_custom()
        setattr(self, attribute, value)
        self._settings.setValue(f"appearance/visualizer{key}", value)
        getattr(self, signal_name).emit()

    def _mark_visualizer_custom(self) -> None:
        if self._visualizer_preset == "custom":
            return
        self._visualizer_preset = "custom"
        self._settings.setValue("appearance/visualizerPreset", "custom")
        self.visualizerPresetChanged.emit()

    def _read_visualizer_bool(self, key: str, default: bool) -> bool:
        return self._settings.value(
            f"appearance/visualizer{key}", default, type=bool
        )

    def _read_visualizer_int(self, key: str, default: int, *, maximum: int = 100) -> int:
        value = self._settings.value(f"appearance/visualizer{key}", default)
        try:
            return max(0, min(maximum, int(value)))
        except (TypeError, ValueError):
            return default

    def _read_visualizer_band_count(self, default: int) -> int:
        value = self._settings.value("appearance/visualizerBandCount", default)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 32
        return value if value in _VISUALIZER_BAND_COUNTS else 32

    def _read_visualizer_choice(
        self, key: str, default: str, choices: frozenset[str]
    ) -> str:
        value = str(self._settings.value(f"appearance/visualizer{key}", default))
        return value if value in choices else default

    def _read_visualizer_text(self, key: str, default: str) -> str:
        value = str(self._settings.value(f"appearance/visualizer{key}", default)).strip()
        return value if value and QColor(value).isValid() else default

    @staticmethod
    def _normalize_visualizer_color(value: str) -> str | None:
        text = str(value or "").strip()
        return text if text and QColor(text).isValid() else None

    @staticmethod
    def _normalize_local_path(value: object) -> str:
        raw_value = str(value or "").strip()
        if not raw_value:
            return ""
        url = QUrl(raw_value)
        if url.isLocalFile():
            raw_value = url.toLocalFile()
        return str(Path(raw_value).expanduser().resolve(strict=False))

    def _set_custom_background_path(self, path: str) -> bool:
        value = self._normalize_local_path(path)
        if value == self._custom_background_path:
            return False
        self._custom_background_path = value
        self._settings.setValue("appearance/customBackgroundPath", value)
        return True

    def _read_background_parameter(self, name: str, default: float) -> float:
        value = self._settings.value(f"appearance/background{name.capitalize()}", default)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default

    def _apply_background_preset_defaults(self, preset: str) -> None:
        defaults = _BACKGROUND_PRESET_DEFAULTS[preset]
        self._set_background_parameter(
            "opacity", defaults["opacity"], "_background_opacity", "backgroundOpacityChanged"
        )
        self._set_background_parameter(
            "blur", defaults["blur"], "_background_blur", "backgroundBlurChanged"
        )
        self._set_background_parameter(
            "darkness", defaults["darkness"], "_background_darkness", "backgroundDarknessChanged"
        )
        self._set_background_parameter(
            "glow", defaults["glow"], "_background_glow", "backgroundGlowChanged"
        )
        self._set_background_parameter(
            "motion", defaults["motion"], "_background_motion", "backgroundMotionChanged"
        )

    def _set_background_parameter(
        self, name: str, value: float, attribute: str, signal_name: str
    ) -> None:
        bounded = max(0.0, min(1.0, float(value)))
        if abs(getattr(self, attribute) - bounded) < 0.0001:
            return
        setattr(self, attribute, bounded)
        self._settings.setValue(f"appearance/background{name.capitalize()}", bounded)
        getattr(self, signal_name).emit()
