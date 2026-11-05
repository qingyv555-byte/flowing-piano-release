"""Opt-in Nocturne entry using production services and a separate local profile.

Run in its own process: QSettings routing is process-wide. Classic startup is
unchanged. Existing projects can be imported deliberately through the editor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication

from ai_piano_studio.__main__ import build_engine, initialize_deferred_services
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.logging_config import configure_logging
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles
from ai_piano_studio.persistence.save_preferences import SavePreferences
from ai_piano_studio.window_state import restore_main_window, save_main_window


def build_nocturne_engine(profile_home: Path, *, defer_noncritical: bool = True):
    """Build the opt-in application; never discover the classic project library."""
    home = profile_home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    QApplication.setOrganizationName("AIPianoStudio")
    QApplication.setApplicationName("AI Piano Studio Nocturne")
    # Redirect unnamed settings (AI provider/window preferences). Bridges with
    # explicit organization names need explicit INI injection on Windows.
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(home / "settings"))

    def settings(name: str) -> QSettings:
        store = QSettings(str(home / "settings" / f"{name}.ini"), QSettings.IniFormat)
        store.setFallbacksEnabled(False)
        return store

    appearance_store = settings("appearance")
    experience_store = settings("experience")
    visualizer_choice_exists = appearance_store.contains(
        "appearance/visualizerEnabled"
    )
    for key, value in {
        "experience/sidebarMode": "icon",
        "experience/inspectorPanelMode": "collapsed",
        "experience/miniMapDisplayMode": "hidden",
    }.items():
        if not experience_store.contains(key):
            experience_store.setValue(key, value)

    controller = EditorController(
        project_library=ProjectLibrary(home / "Projects"),
        recovery_store=RecoveryStore(home / "recovery"),
        recent_files=RecentFiles(QSettings(str(home / "recent.ini"), QSettings.IniFormat)),
    )
    session = build_engine(
        controller=controller, ui_variant="nocturne", defer_noncritical=defer_noncritical,
        appearance_settings=appearance_store,
        experience_settings=ExperienceSettings(settings=experience_store),
        harmony_settings=HarmonySettings(settings=settings("harmony")),
        save_preferences=SavePreferences(settings=settings("save")),
        piano_tone_settings=settings("tone"),
        support_settings=settings("support"),
    )
    app, engine, editor, playback, keyboard = session
    scene = engine.rootContext().contextProperty("scene")
    if not visualizer_choice_exists:
        scene.setVisualizerEnabled(True)
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    root = engine.rootObjects()[0]

    def update_visualizer_runtime(*_args) -> None:
        if visualizer._shutdown:
            return
        visualizer.setEnabled(
            scene.visualizerEnabled
            and root.isVisible()
            and root.visibility() != QWindow.Visibility.Minimized
            and root.property("pageState") == "editor"
            and not root.property("focusMode")
            and not scene.focusModeEnabled
            and not scene.powerSaveEnabled
        )

    for signal in (
        scene.visualizerEnabledChanged,
        scene.visualizerSensitivityChanged,
        scene.visualizerSmoothnessChanged,
        scene.visualizerBandCountChanged,
        scene.focusModeEnabledChanged,
        scene.powerSaveEnabledChanged,
        root.visibleChanged,
        root.visibilityChanged,
        root.pageStateChanged,
        root.focusModeChanged,
    ):
        signal.connect(update_visualizer_runtime)
    update_visualizer_runtime()
    templates = engine.rootContext().contextProperty("templates")
    engine.rootObjects()[0].closing.connect(templates.stopPreview)
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="夜庭 · 独立数据的正式后端预览")
    parser.add_argument("--profile-home", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv)
    home = args.profile_home or (
        Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation))
        / "AI Piano Studio Nocturne"
    )
    configure_logging(home / "logs")
    app, engine, controller, playback, keyboard = build_nocturne_engine(home)
    root = engine.rootObjects()[0]
    settings = QSettings(str(home / "window.ini"), QSettings.IniFormat)
    restore_main_window(root, settings)
    root.closing.connect(lambda: save_main_window(root, settings))
    if args.smoke_test:
        QTimer.singleShot(0, app.quit)
    else:
        QTimer.singleShot(0, lambda: initialize_deferred_services(engine))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
