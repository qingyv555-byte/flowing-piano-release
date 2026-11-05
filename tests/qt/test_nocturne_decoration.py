from __future__ import annotations

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
from PySide6.QtGui import QWindow

from ai_piano_studio.__main__ import initialize_deferred_services
from tests.qt.test_nocturne import preserved_qsettings_state, session


def assert_visualizer_runtime(engine, enabled):
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    assert visualizer.enabled is enabled
    assert visualizer._tap.enabled is enabled
    assert visualizer._worker is not None
    assert visualizer._worker._timer is not None
    assert visualizer._worker._timer.isActive() is enabled


def test_nocturne_visualizer_worker_runs_only_in_active_editor(session):
    app, engine, root, _editor, _playback, _templates = session
    scene = engine.rootContext().contextProperty("scene")
    assert scene.visualizerEnabled is True
    assert root.property("pageState") == "launch"
    assert_visualizer_runtime(engine, False)

    root.setProperty("pageState", "editor")
    root.showNormal()
    app.processEvents()
    assert_visualizer_runtime(engine, True)

    root.setProperty("focusMode", True)
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    assert scene.visualizerEnabled is True
    root.setProperty("focusMode", False)

    scene.setFocusModeEnabled(True)
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    scene.setFocusModeEnabled(False)

    scene.setPowerSaveEnabled(True)
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    scene.setPowerSaveEnabled(False)
    app.processEvents()
    assert_visualizer_runtime(engine, True)

    root.showMinimized()
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    root.showNormal()
    app.processEvents()
    assert_visualizer_runtime(engine, True)

    root.hide()
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    saved_sensitivity = scene.visualizerSensitivity
    scene.setVisualizerSensitivity(
        41 if saved_sensitivity != 41 else 42
    )
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    assert scene.visualizerEnabled is True
    root.showNormal()
    app.processEvents()
    assert_visualizer_runtime(engine, True)
    assert scene.visualizerEnabled is True


def test_nocturne_visualizer_runtime_never_overrides_saved_off_state(session):
    app, engine, root, _editor, _playback, _templates = session
    scene = engine.rootContext().contextProperty("scene")
    root.setProperty("pageState", "editor")
    root.showNormal()
    scene.setVisualizerEnabled(False)
    app.processEvents()
    assert_visualizer_runtime(engine, False)

    root.hide()
    root.showNormal()
    app.processEvents()
    assert_visualizer_runtime(engine, False)
    assert scene.visualizerEnabled is False


def test_nocturne_decorations_pause_with_window_and_restore_saved_state(session):
    app, engine, root, _editor, _playback, _templates = session
    scene = engine.rootContext().contextProperty("scene")
    scene.setBackgroundEnabled(True)
    scene.setDynamicEnabled(True)
    scene.setMotionLevel("medium")
    scene.setBackgroundMotion(0.5)
    scene.setVisualizerEnabled(True)
    root.setProperty("pageState", "editor")
    root.showNormal()
    app.processEvents()

    background = root.findChild(QObject, "editorBackgroundLayer")
    rhythm = root.findChild(QObject, "reactiveRhythmLayer")
    assert background is not None and rhythm is not None
    assert background.property("visible") is True
    assert background.property("ambientRunning") is True
    assert rhythm.property("decorationRunning") is True

    root.showMinimized()
    app.processEvents()
    assert root.visibility() == QWindow.Visibility.Minimized
    assert background.property("ambientRunning") is False
    assert rhythm.property("decorationRunning") is False
    assert scene.backgroundEnabled is True
    assert scene.visualizerEnabled is True

    root.showNormal()
    app.processEvents()
    assert background.property("ambientRunning") is True
    assert rhythm.property("decorationRunning") is True

    root.hide()
    app.processEvents()
    assert root.isVisible() is False
    assert background.property("ambientRunning") is False
    assert rhythm.property("decorationRunning") is False

    root.showNormal()
    app.processEvents()
    assert background.property("ambientRunning") is True
    assert rhythm.property("decorationRunning") is True
    assert scene.backgroundEnabled is True
    assert scene.visualizerEnabled is True


def test_nocturne_off_paths_hide_only_decorations(session):
    app, engine, root, _editor, _playback, _templates = session
    scene = engine.rootContext().contextProperty("scene")
    scene.setVisualizerEnabled(True)
    root.setProperty("pageState", "editor")
    app.processEvents()
    rhythm = root.findChild(QObject, "reactiveRhythmLayer")
    timeline = root.findChild(QObject, "timeline")
    playhead = root.findChild(QObject, "playhead")
    assert rhythm is not None and timeline is not None and playhead is not None

    scene.setVisualizerEnabled(False)
    app.processEvents()
    assert rhythm.property("visible") is False
    assert timeline.property("visible") is True
    assert playhead.property("enabled") is True

    scene.setVisualizerEnabled(True)
    root.setProperty("focusMode", True)
    app.processEvents()
    assert rhythm.property("visible") is False
    assert timeline.property("visible") is True
    assert playhead.property("enabled") is True
    assert rhythm.property("enabled") is False


def test_nocturne_background_dialog_has_independent_music_rhythm_switch(session):
    app, engine, root, _editor, _playback, _templates = session
    scene = engine.rootContext().contextProperty("scene")
    initialize_deferred_services(engine)
    assert QMetaObject.invokeMethod(
        root,
        "openAppearancePopup",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", None),
    )
    app.processEvents()
    toggle = root.findChild(QObject, "nocturneVisualizerToggle")
    assert toggle is not None
    assert toggle.property("visible") is True

    scene.setBackgroundEnabled(False)
    scene.setVisualizerEnabled(True)
    app.processEvents()
    assert toggle.property("checked") is True

    toggle.toggled.emit(False)
    app.processEvents()
    assert scene.backgroundEnabled is False
    assert scene.visualizerEnabled is False
