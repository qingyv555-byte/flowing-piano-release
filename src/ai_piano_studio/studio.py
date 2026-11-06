"""Opt-in rebuilt workbench. Run in its own process with an isolated profile."""
from __future__ import annotations

import sys

# Same executable, isolated worker mode. This must precede Qt/audio/log setup;
# the default Studio entry and normal profile behavior remain unchanged.
if __name__ == '__main__' and sys.argv[1:2] == ['--desktop-capture-helper']:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    from ai_piano_studio.creative.capture_helper import main as capture_main
    raise SystemExit(capture_main())

import argparse
import logging
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtGui import QColor, QPalette, QWindow
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
from ai_piano_studio.keyboard.settings import PlayingSettings


def build_studio_engine(profile_home: Path, *, defer_noncritical: bool = True):
    from ai_piano_studio.appearance.studio_settings import StudioAppearance

    home = profile_home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    QApplication.setOrganizationName("AIPianoStudio")
    QApplication.setApplicationName("AI Piano Studio Rebuild")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(home / "settings"))

    def settings(name: str) -> QSettings:
        result = QSettings(str(home / "settings" / f"{name}.ini"), QSettings.IniFormat)
        result.setFallbacksEnabled(False)
        return result

    experience_store = settings("experience")
    for key, value in {
        "experience/sidebarMode": "icon",
        "experience/inspectorPanelMode": "collapsed",
    }.items():
        if not experience_store.contains(key):
            experience_store.setValue(key, value)
    # ExperienceSettings defaults the overview to visible and honors both saved
    # mode and legacy visibility. Old Studio auto-hidden values are indistinguishable
    # from an explicit choice, so leave them intact; the view menu can restore it.
    appearance = StudioAppearance(settings=settings("studio"), asset_home=home / "backgrounds")
    controller = EditorController(
        project_library=ProjectLibrary(home / "Projects"),
        recovery_store=RecoveryStore(home / "recovery"),
        recent_files=RecentFiles(settings("recent")),
    )
    session = build_engine(
        controller=controller, ui_variant="studio", studio_appearance=appearance,
        defer_noncritical=defer_noncritical, appearance_settings=settings("appearance"),
        experience_settings=ExperienceSettings(settings=experience_store),
        harmony_settings=HarmonySettings(settings=settings("harmony")),
        save_preferences=SavePreferences(settings=settings("save")),
        piano_tone_settings=settings("tone"), support_settings=settings("support"),
        playing_settings=PlayingSettings(home / 'settings' / 'playing.json'),
        assistant_history_path=home / 'assistant' / 'conversation.sqlite3',
    )
    app, engine, editor, playback, keyboard = session
    playback_store = settings("playback")
    for key, fallback, setter in (
        ("volume", playback.volume, playback.setVolume),
        ("rate", playback.playbackRate, playback.setPlaybackRate),
    ):
        try:
            value = float(playback_store.value(key, fallback))
        except (TypeError, ValueError):
            value = fallback
        setter(value if math.isfinite(value) else fallback)
    # Persist the effective controller values, including changes made through
    # transport controls. Dialog drafts never reach these signals until Apply.
    playback.volumeChanged.connect(lambda: playback_store.setValue("volume", playback.volume))
    playback.playbackRateChanged.connect(lambda: playback_store.setValue("rate", playback.playbackRate))
    root = engine.rootObjects()[0]
    root.closing.connect(playback_store.sync)
    scene = engine.rootContext().contextProperty("scene")
    visualizer = engine.rootContext().contextProperty("audioVisualizer")

    def update_runtime(*_args):
        if visualizer._shutdown:
            return
        visualizer.setEnabled(bool(root.property("studioAnalysisRequested")))

    def update_palette():
        paper = appearance.values["themeId"] == "paper"
        palette = QPalette(app.palette())
        for roles, value in (
            ([QPalette.Window], "#E8EDF0" if paper else "#111F28"),
            ([QPalette.Base, QPalette.Button, QPalette.ToolTipBase], "#F8FAF9" if paper else "#1A2B34"),
            ([QPalette.AlternateBase], "#F0F5F4" if paper else "#21333D"),
            ([QPalette.Light], "#DBE6E6" if paper else "#2A4149"),
            ([QPalette.Midlight], "#CBDADA" if paper else "#15262F"),
            ([QPalette.Mid, QPalette.Dark], "#93ADB4" if paper else "#577078"),
            ([QPalette.Shadow], "#243E49" if paper else "#0C171E"),
            ([QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText], "#243E49" if paper else "#E4EAE5"),
            ([QPalette.PlaceholderText], "#465E68" if paper else "#ADBFBD"),
            ([QPalette.Highlight], "#315F64" if paper else "#D9E6DF"),
            ([QPalette.HighlightedText], "#FFFFFF" if paper else "#182B32"),
            ([QPalette.Link, QPalette.BrightText], "#315F64" if paper else "#B9DAD0"),
            ([QPalette.LinkVisited], "#465E68" if paper else "#ADBFBD"),
        ):
            for role in roles:
                surface = role in (QPalette.Window, QPalette.Base, QPalette.Button,
                                   QPalette.ToolTipBase, QPalette.AlternateBase,
                                   QPalette.Light, QPalette.Midlight)
                palette.setColor(role, appearance.tintSurfaceColor(value) if surface
                                 else appearance.tintColor(value))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, appearance.tintColor("#7C8B91" if paper else "#788F95"))
        app.setPalette(palette)
        update_runtime()

    appearance.changed.connect(update_palette)
    visualizer.setConfig(scene.visualizerSensitivity, scene.visualizerSmoothness, 32)
    for signal in (root.studioAnalysisRequestedChanged, scene.visualizerEnabledChanged,
                   scene.visualizerSensitivityChanged, scene.visualizerSmoothnessChanged,
                   scene.visualizerBandCountChanged):
        signal.connect(update_runtime)
    update_palette()
    return session


def resolve_profile_home(explicit: Path | None = None, *, configuration_root: Path | None = None) -> Path:
    """Honor an explicit CLI profile, then an optional machine-local relocation."""
    if explicit is not None:
        return explicit
    root = configuration_root or (
        Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
        else Path(__file__).resolve().parents[2])
    setting = root / '.studio-profile-home'
    if setting.is_file():
        if setting.stat().st_size > 32768:
            raise ValueError('本机资料目录配置过长')
        value = setting.read_text(encoding='utf-8-sig').strip()
        if not value or not Path(value).is_absolute():
            raise ValueError('本机资料目录配置必须为绝对路径')
        return Path(value)
    return Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)) / 'AI Piano Studio Rebuild'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flowing Piano")
    parser.add_argument("--profile-home", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)
    if args.smoke_test and args.profile_home is None:
        with TemporaryDirectory(prefix="flowing-piano-smoke-") as profile:
            try:
                return main(["--smoke-test", "--profile-home", profile])
            finally:
                logging.shutdown()
    app = QApplication.instance() or QApplication(sys.argv)
    home = resolve_profile_home(args.profile_home)
    configure_logging(home / "logs")
    app, engine, editor, playback, keyboard = build_studio_engine(home)
    root = engine.rootObjects()[0]
    store = QSettings(str(home / "window.ini"), QSettings.IniFormat)
    restore_main_window(root, store)
    root.closing.connect(lambda: save_main_window(root, store))
    if args.smoke_test:
        from ai_piano_studio.__main__ import validate_smoke_resources
        validate_smoke_resources()
        QTimer.singleShot(0, app.quit)
    else:
        QTimer.singleShot(0, lambda: initialize_deferred_services(engine))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
