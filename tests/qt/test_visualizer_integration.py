from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QMetaObject, QObject, QSettings, Q_ARG, Qt
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.audio.visualizer import (
    AudioVisualizerController,
    PcmSnapshotTap,
    analyze_pcm,
)


def _tone_payload(frequency: float = 1_000.0, amplitude: float = 0.8) -> bytes:
    timeline = np.arange(1_024, dtype=np.float64) / 48_000.0
    wave = np.rint(
        np.sin(2.0 * math.pi * frequency * timeline) * amplitude * 32_767
    ).astype("<i2")
    return np.column_stack((wave, wave)).astype("<i2").tobytes()


def test_controller_publishes_real_snapshot_and_turning_it_off_clears_state(qapp) -> None:
    tap = PcmSnapshotTap()
    controller = AudioVisualizerController(tap, auto_start=False)
    try:
        tap.publish(_tone_payload())
        controller.process_once()

        assert controller.enabled is True
        assert controller.rms > 0.1
        assert controller.mid > controller.low
        assert len(controller.bands) == 32

        controller.setEnabled(False)

        assert controller.enabled is False
        assert tap.enabled is False
        assert controller.rms == 0.0
        assert controller.bands == [0.0] * 32
    finally:
        controller.shutdown()


def test_controller_can_be_reenabled_without_restarting_the_public_api(qapp) -> None:
    tap = PcmSnapshotTap()
    controller = AudioVisualizerController(tap, auto_start=False)
    try:
        controller.setEnabled(False)
        controller.setEnabled(True)
        tap.publish(_tone_payload(8_000.0))
        controller.process_once()

        assert controller.enabled is True
        assert tap.enabled is True
        assert controller.high > controller.low
    finally:
        controller.shutdown()


def test_controller_ignores_a_queued_snapshot_after_turning_visualizer_off(qapp) -> None:
    tap = PcmSnapshotTap()
    controller = AudioVisualizerController(tap, auto_start=False)
    try:
        tap.publish(_tone_payload())
        controller.process_once()
        stale_snapshot = controller.snapshot
        assert stale_snapshot.rms > 0.0

        controller.setEnabled(False)
        controller._on_snapshot(stale_snapshot)

        assert controller.enabled is False
        assert controller.rms == 0.0
        assert controller.bands == [0.0] * 32
    finally:
        controller.shutdown()


def test_live_controller_runs_analyzer_off_ui_thread_and_stops_off_work(qapp) -> None:
    tap = PcmSnapshotTap()
    controller = AudioVisualizerController(tap)
    try:
        assert controller._thread is not None
        assert controller._thread.isRunning()

        controller.setEnabled(False)
        qapp.processEvents()
        before = tap.publish_count
        tap.publish(_tone_payload())
        assert tap.publish_count == before
        assert controller.rms == 0.0
    finally:
        controller.shutdown()


def test_controller_updates_sensitivity_smoothness_and_band_count_live(qapp) -> None:
    tap = PcmSnapshotTap()
    controller = AudioVisualizerController(tap, auto_start=False)
    try:
        controller.setConfig(100, 40, 24)
        tap.publish(_tone_payload(8_000.0))
        controller.process_once()
        assert len(controller.bands) == 24

        controller.setConfig(20, 85, 48)
        tap.publish(_tone_payload(80.0))
        controller.process_once()
        assert len(controller.bands) == 48
        assert controller.low > controller.high
    finally:
        controller.shutdown()


def test_build_engine_exposes_one_visualizer_tap_for_the_realtime_mixer(
    qapp, tmp_path
) -> None:
    _app, engine, _editor, playback, _keyboard = build_engine(
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        )
    )
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    root = engine.rootObjects()[0]
    try:
        assert isinstance(visualizer, AudioVisualizerController)
        assert visualizer._tap is playback.piano_source.mixer._visualizer_tap
    finally:
        if isinstance(visualizer, AudioVisualizerController):
            visualizer.shutdown()
        root.close()


def test_final_mixer_pcm_reaches_live_visualizer_worker(qapp, tmp_path) -> None:
    appearance = QSettings(
        str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
    )
    appearance.setValue("appearance/visualizerPresentationRevision", 1)
    appearance.setValue("appearance/visualizerEnabled", True)
    _app, engine, _editor, playback, _keyboard = build_engine(
        appearance_settings=appearance
    )
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    root = engine.rootObjects()[0]
    mixer = playback.piano_source.mixer
    try:
        mixer.start_voice(
            "visualizer-smoke",
            60,
            110,
            (12_000, -12_000) * 4_000,
            source_sample_rate=48_000,
            owner_tag="test",
        )
        mixer._read_output_frames(1_024)
        assert visualizer._tap.publish_count > 0
        payload = visualizer._tap.read_latest()
        assert payload is not None
        assert len(payload) > 0
        assert analyze_pcm(payload).rms > 0.0
        assert visualizer._worker is not None
        assert visualizer._worker._timer is not None
        assert visualizer._worker._timer.isActive()
        for _ in range(10):
            if (
                visualizer._worker._previous.rms > 0.0
                and visualizer.rms > 0.0
            ):
                break
            visualizer._tap.publish(payload)
            QTest.qWait(40)
        assert visualizer._worker._previous.rms > 0.0
        assert visualizer.rms > 0.0
        assert max(visualizer.bands) > 0.0
    finally:
        visualizer.shutdown()
        root.close()


def test_build_engine_applies_persistent_visualizer_settings_to_runtime(
    qapp, tmp_path
) -> None:
    appearance = QSettings(
        str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
    )
    appearance.setValue("appearance/visualizerEnabled", False)
    appearance.setValue("appearance/visualizerSensitivity", 12)
    appearance.setValue("appearance/visualizerSmoothness", 88)
    appearance.setValue("appearance/visualizerBandCount", 24)
    _app, engine, _editor, _playback, _keyboard = build_engine(
        appearance_settings=appearance
    )
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    root = engine.rootObjects()[0]
    try:
        root.setProperty("pageState", "editor")
        qapp.processEvents()
        assert visualizer.enabled is False
        assert visualizer._tap.enabled is False
        assert visualizer._worker is not None
        assert visualizer._worker._timer is not None
        assert visualizer._worker._timer.isActive() is False
        assert len(visualizer.bands) == 24
    finally:
        visualizer.shutdown()
        root.close()


def test_existing_visualizer_setting_is_retired_on_upgrade(qapp, tmp_path) -> None:
    """The legacy reactive band never reappears for an existing installation."""
    appearance = QSettings(
        str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
    )
    appearance.setValue("appearance/visualizerEnabled", True)

    _app, engine, _editor, _playback, _keyboard = build_engine(
        appearance_settings=appearance
    )
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    root = engine.rootObjects()[0]
    try:
        root.setProperty("pageState", "editor")
        qapp.processEvents()

        assert engine.rootContext().contextProperty("scene").visualizerEnabled is False
        assert visualizer.enabled is False
        assert root.findChild(object, "reactiveRhythmLayer").property("visible") is False
    finally:
        visualizer.shutdown()
        root.close()


def test_reactive_layer_contract_uses_pcm_snapshot_not_note_metrics() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "ai_piano_studio"
        / "qml"
        / "ReactiveRhythmLayer.qml"
    ).read_text(encoding="utf-8")

    assert "visualizer.bands" in source
    assert "visualEnergy" not in source
    assert "visualNoteDensity" not in source
    assert "visualPitchBand" not in source
    assert "scene.dynamicEnabled" not in source
    assert "visualizerSpectrumOpacity" in source
    assert "visualizerSpectrumHeight" in source
    assert "visualizerGlowSpread" in source
    assert "visualizerRibbonCount" in source
    assert "visualizerRibbonSpeed" in source
    assert "enabled: false" in source


def test_build_engine_keeps_reactive_layer_hidden_by_default(qapp, tmp_path) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine(
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        )
    )
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    root = engine.rootObjects()[0]
    try:
        root.setProperty("pageState", "editor")
        qapp.processEvents()
        reactive = root.findChild(object, "reactiveRhythmLayer")
        assert reactive is not None
        assert reactive.property("enabled") is False
        assert reactive.property("visible") is False
        assert reactive.property("effectHeight") <= reactive.height() * 0.40 + 0.1
        assert reactive.findChild(object, "reactiveAtmosphere") is not None
        band_names = {item.objectName() for item in reactive.childItems()}
        assert "spectrumBand-0" in band_names
        assert len([name for name in band_names if name.startswith("spectrumBand-")]) == 32
        assert visualizer.enabled is False
    finally:
        visualizer.shutdown()
        root.close()


def test_background_settings_omits_legacy_visualizer_controls(qapp, tmp_path) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine(
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        )
    )
    root = engine.rootObjects()[0]
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    try:
        assert QMetaObject.invokeMethod(
            root,
            "openAppearancePopup",
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", None),
        )
        qapp.processEvents()
        popup = root.findChild(QObject, "backgroundSettingsDialog")
        assert popup is not None
        for object_name in (
            "visualizerEnabledToggle",
            "visualizerPresetPicker",
            "visualizerIntensitySlider",
            "visualizerSensitivitySlider",
            "visualizerSmoothnessSlider",
            "visualizerAdvancedOptionsToggle",
            "visualizerAdvancedOptions",
            "visualizerSpectrumToggle",
            "visualizerSpectrumStrengthSlider",
            "visualizerSpectrumHeightPicker",
            "visualizerSpectrumOpacitySlider",
            "visualizerBandCountPicker",
            "visualizerBarWidthPicker",
            "visualizerReadabilityToggle",
            "visualizerGlowSpreadPicker",
            "visualizerRibbonIntensitySlider",
            "visualizerRibbonCountPicker",
            "visualizerRibbonSpeedPicker",
            "visualizerBeatIntensitySlider",
            "visualizerColorModePicker",
            "visualizerLowGainSlider",
            "visualizerMidGainSlider",
            "visualizerHighGainSlider",
            "visualizerSaturationSlider",
            "visualizerLowColorField",
            "visualizerMidColorField",
            "visualizerHighColorField",
            "visualizerResetButton",
        ):
            assert popup.findChild(QObject, object_name) is None, object_name
    finally:
        visualizer.shutdown()
        root.close()


def test_legacy_visualizer_settings_remain_persistent_without_background_ui(
    qapp, tmp_path
) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine(
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        )
    )
    root = engine.rootObjects()[0]
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    scene = engine.rootContext().contextProperty("scene")
    try:
        assert QMetaObject.invokeMethod(
            root,
            "openAppearancePopup",
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", None),
        )
        qapp.processEvents()
        popup = root.findChild(QObject, "backgroundSettingsDialog")
        assert popup is not None

        scene.setVisualizerIntensity(75)
        scene.setVisualizerBandCount(48)
        scene.setVisualizerColorMode("custom")
        scene.setVisualizerLowColor("#123456")
        qapp.processEvents()

        assert popup.findChild(QObject, "visualizerIntensitySlider") is None
        assert scene.visualizerBandCount == 48
        assert scene.visualizerColorMode == "custom"
        assert scene.visualizerLowColor == "#123456"
        assert popup.property("x") >= 0
        assert popup.property("y") >= 0
        assert popup.property("x") + popup.property("width") <= root.width()
        assert popup.property("y") + popup.property("height") <= root.height()
    finally:
        visualizer.shutdown()
        root.close()


def test_appearance_slider_persists_only_after_pointer_release(qapp, tmp_path) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine(
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        )
    )
    root = engine.rootObjects()[0]
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    scene = engine.rootContext().contextProperty("scene")
    try:
        assert QMetaObject.invokeMethod(
            root,
            "openAppearancePopup",
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", None),
        )
        qapp.processEvents()
        # Legacy visualizer sliders were retired; background opacity still uses
        # the same preview-then-commit persistence contract.
        slider = root.findChild(QObject, "backgroundOpacitySlider")
        assert slider is not None
        slider_control = next(
            child
            for child in slider.findChildren(QObject)
            if child.property("accessibleName") == "背景强度"
        )
        original = scene.backgroundOpacity
        preview = min(1.0, original + 0.1)

        slider_control.previewValue(preview)
        qapp.processEvents()

        assert slider.property("value") == preview
        assert scene.backgroundOpacity == original

        slider_control.valueCommitted.emit(preview)
        qapp.processEvents()
        assert scene.backgroundOpacity == preview
    finally:
        visualizer.shutdown()
        root.close()
