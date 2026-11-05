from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tempfile
import time

from ai_piano_studio.startup_profiler import startup_profiler

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from ai_piano_studio.qml_engine import ApplicationQmlEngine as QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMessageBox

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.import_controller import AudioImportController
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2
from ai_piano_studio.audio.source import ContinuousPianoToneSource
from ai_piano_studio.audio.tone import PianoToneCoordinator
from ai_piano_studio.audio.tone_settings import PianoToneSettings
from ai_piano_studio.audio.visualizer import AudioVisualizerController, PcmSnapshotTap
from ai_piano_studio.appearance.scene_controller import SceneController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.help_content import HarmonyHelpContent
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.editor.midi_import_controller import MidiImportController
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.keyboard.settings import PlayingSettings
from ai_piano_studio.persistence.autosave import default_recovery_store
from ai_piano_studio.persistence.save_preferences import SavePreferences
from ai_piano_studio.templates.library import TemplateMusicLibrary
from ai_piano_studio.window_state import restore_main_window, save_main_window


logger = logging.getLogger(__name__)
_startup = startup_profiler()
_startup.mark("imports_complete")
_qt_style_configured = False


def _mark_startup_once(phase: str) -> None:
    if not any(marker["phase"] == phase for marker in _startup.markers):
        _startup.mark(phase)


def configure_logging(*args, **kwargs):
    """Load logging only when the application is ready to configure it."""
    from ai_piano_studio.logging_config import configure_logging as implementation

    return implementation(*args, **kwargs)


def package_root() -> Path:
    module_root = Path(__file__).resolve().parent
    frozen_package_root = module_root / "ai_piano_studio"
    return frozen_package_root if frozen_package_root.is_dir() else module_root


def piano_resource_root() -> Path:
    packaged = package_root() / "resources" / "piano"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "resources" / "piano"


def smoke_resource_paths() -> tuple[Path, ...]:
    root = package_root()
    paths: list[Path] = [
        root / "resources" / "help" / "harmony_beginner_guide.md"
    ]
    paths.extend(sorted((root / "qml" / "icons").glob("*.svg")))
    for template_dir in sorted((root / "resources" / "templates").iterdir()):
        if not template_dir.is_dir():
            continue
        paths.extend(
            (
                template_dir / "metadata.json",
                template_dir / "template.pianoai.json",
                template_dir / "thumbnail.png",
            )
        )
    return tuple(paths)


def _configure_dark_qt_surface(app: QApplication, ui_variant: str = "classic") -> None:
    """Keep Qt-owned dialogs and controls in the same dark visual family."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#07111E"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#DCE8F5"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0B1A2A"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#0E2032"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#0E2032"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#DCE8F5"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#DCE8F5"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#0E2032"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#DCE8F5"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#7BE7FF"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#49D7FF"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#061824"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6E859A"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#7BE7FF"))
    if ui_variant == "nocturne":
        for role, value in {
            QPalette.Window: "#030604", QPalette.WindowText: "#EEEEE8",
            QPalette.Base: "#111A15", QPalette.AlternateBase: "#151D18",
            QPalette.ToolTipBase: "#151D18", QPalette.ToolTipText: "#EEEEE8",
            QPalette.Text: "#EEEEE8", QPalette.Button: "#151D18",
            QPalette.ButtonText: "#EEEEE8", QPalette.Highlight: "#DCE5CF",
            QPalette.HighlightedText: "#142019", QPalette.Link: "#E5F1D6",
            QPalette.PlaceholderText: "#A2AAA5", QPalette.BrightText: "#E5F1D6",
        }.items():
            palette.setColor(role, QColor(value))
    app.setPalette(palette)
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)


def validate_smoke_resources() -> None:
    paths = smoke_resource_paths()
    missing = [str(path) for path in paths if not path.is_file()]
    template_count = sum(path.name == "template.pianoai.json" for path in paths)
    if missing or template_count != 6 or not any(path.suffix == ".svg" for path in paths):
        raise RuntimeError(
            "Packaged resources are incomplete: "
            f"templates={template_count}, missing={missing}"
        )


def build_engine(
    *,
    controller: EditorController | None = None,
    experience_settings: ExperienceSettings | None = None,
    harmony_settings: HarmonySettings | None = None,
    save_preferences: SavePreferences | None = None,
    appearance_settings: QSettings | None = None,
    piano_tone_settings: QSettings | None = None,
    support_settings: QSettings | None = None,
    defer_noncritical: bool | None = None,
    ui_variant: str = "classic",
    studio_appearance: QObject | None = None,
    playing_settings: PlayingSettings | None = None,
    assistant_history_path: Path | None = None,
) -> tuple[
    QApplication,
    QQmlApplicationEngine,
    EditorController,
    PlaybackController,
    KeyboardController,
]:
    if ui_variant not in {"classic", "nocturne", "studio"}:
        raise ValueError(f"Unknown UI variant: {ui_variant}")
    if defer_noncritical is None:
        defer_noncritical = os.environ.get(
            "AI_PIANO_DEFER_NONCRITICAL_STARTUP", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtGui import QIcon
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources" / "brand" / "flowing-piano.ico")))
    _startup.mark("app_created")
    _configure_dark_qt_surface(app, ui_variant)
    global _qt_style_configured
    if not _qt_style_configured:
        QQuickStyle.setStyle("Basic")
        _qt_style_configured = True
    app.setApplicationName(
        "AI Piano Studio Rebuild" if ui_variant == "studio" else
        "AI Piano Studio Nocturne" if ui_variant == "nocturne" else "AI Piano Studio"
    )
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/seguisym.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    font = QFont("Microsoft YaHei UI" if "Microsoft YaHei UI" in families else "Segoe UI")
    font.setFamilies([font.family(), "Segoe UI Symbol"])
    app.setFont(font)
    engine = QQmlApplicationEngine()
    preferences = (
        save_preferences
        if save_preferences is not None
        else SavePreferences(parent=engine)
    )
    if preferences.parent() is None:
        preferences.setParent(engine)
    recovery_store = default_recovery_store()
    controller = controller or EditorController(recovery_store=recovery_store)
    recovery_store = getattr(controller, "_recovery", recovery_store)

    def apply_autosave_policy() -> None:
        controller.setAutosavePolicy(
            preferences.autosaveEnabled,
            preferences.autosaveDelaySeconds,
        )

    preferences.changed.connect(apply_autosave_policy)
    apply_autosave_policy()
    _startup.mark("settings_loaded")
    scene = SceneController(parent=engine, settings=appearance_settings)
    experience = experience_settings or ExperienceSettings(parent=engine)
    if experience.parent() is None:
        experience.setParent(engine)
    harmony = harmony_settings or HarmonySettings(parent=engine)
    if harmony.parent() is None:
        harmony.setParent(engine)
    piano_profile = ACOUSTIC_PIANO_V2
    piano_root = piano_resource_root()
    _startup.mark("resources_resolved")
    _startup.mark("project_library_initialized")
    piano_mixer = PianoVoiceMixer(profile=piano_profile, parent=engine)
    visualizer_tap = PcmSnapshotTap()
    piano_mixer.set_visualizer_tap(visualizer_tap)
    audio_visualizer = AudioVisualizerController(
        visualizer_tap,
        parent=engine,
        initial_enabled=scene.visualizerEnabled,
    )

    def apply_visualizer_settings() -> None:
        audio_visualizer.setEnabled(scene.visualizerEnabled)
        audio_visualizer.setConfig(
            scene.visualizerSensitivity,
            scene.visualizerSmoothness,
            scene.visualizerBandCount,
        )

    scene.visualizerEnabledChanged.connect(apply_visualizer_settings)
    scene.visualizerSensitivityChanged.connect(apply_visualizer_settings)
    scene.visualizerSmoothnessChanged.connect(apply_visualizer_settings)
    scene.visualizerBandCountChanged.connect(apply_visualizer_settings)
    apply_visualizer_settings()
    tone_coordinator = PianoToneCoordinator(
        piano_root / "banks.json"
    )
    piano_source = ContinuousPianoToneSource(tone_coordinator, piano_mixer)
    piano_tone = PianoToneSettings(
        parent=engine,
        settings=piano_tone_settings,
        coordinator=tone_coordinator,
    )
    piano_tone.setValue(piano_tone.value)
    playback = PlaybackController(
        controller,
        parent=engine,
        piano_profile=piano_profile,
        piano_source=piano_source,
    )
    # P0 optional instrument audition. It never replaces the current mixer or
    # saved sound selection. A source checkout can use its verified staging
    # pack; installed builds look only under their own user sound library.
    sfz_preview = None
    if ui_variant == "studio":
        from ai_piano_studio.audio.sfz_preview import SfzPreviewController

        profile_pack = controller._projects.root.parent / "sound_library" / "upright-kw"
        checkout_pack = Path(__file__).resolve().parents[2] / "artifacts" / "sound-library-p0-20261104"
        selected_pack = profile_pack if (profile_pack / "native" / "sfizz.dll").is_file() else checkout_pack
        playback.configure_project_sound(selected_pack, settings=piano_tone_settings)
        app.aboutToQuit.connect(playback.shutdown_project_sound)
        engine.add_shutdown_callback(playback.shutdown_project_sound)
        sfz_preview = SfzPreviewController(controller, playback, selected_pack, parent=engine)
        app.aboutToQuit.connect(sfz_preview.shutdown)
        engine.add_shutdown_callback(sfz_preview.shutdown)
    engine.rootContext().setContextProperty("sfzPreview", sfz_preview)

    assistant_holder: dict[str, object | None] = {"value": None}

    def record_audio_import_summary(summary: str) -> None:
        current = assistant_holder["value"]
        if current is not None:
            current.recordAudioImportSummary(summary)

    assistant = None
    if not defer_noncritical:
        from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController

        assistant = MusicAssistantController(controller, playback=playback, parent=engine, history_path=assistant_history_path,
                                             automatic_music=True, appearance=studio_appearance,
                                             require_song_confirmation=studio_appearance is not None,
                                             model_driven_assistant=studio_appearance is not None,
                                             creator_enabled=studio_appearance is not None)
        assistant_holder["value"] = assistant
    audio_import = AudioImportController(
        controller,
        playback,
        assistant_summary=record_audio_import_summary,
        parent=engine,
    )
    midi_import = MidiImportController(controller, parent=engine)
    _startup.mark("audio_initialized")
    app.aboutToQuit.connect(audio_import.shutdown)
    app.aboutToQuit.connect(audio_visualizer.shutdown)
    app.aboutToQuit.connect(piano_tone.shutdown)
    engine.add_shutdown_callback(audio_visualizer.shutdown)
    engine.add_shutdown_callback(piano_tone.shutdown)
    support = None
    if assistant is not None:
        from ai_piano_studio.experience.support import ApplicationSupportController

        support = ApplicationSupportController(
            parent=engine,
            settings=support_settings,
            recovery_store=recovery_store,
            credential_reset=assistant.clearSavedCredentials,
        )
    _startup.mark("appearance_initialized")
    keyboard = KeyboardController(controller, playback, parent=engine, settings=playing_settings)
    harmony_controller = HarmonySuggestionController(
        controller,
        harmony,
        experience=experience,
        playback=playback,
        keyboard=keyboard,
        parent=engine,
        piano_profile=piano_profile,
        piano_source=piano_source,
    )
    harmony_help = HarmonyHelpContent(parent=engine)
    templates = TemplateMusicLibrary(
        project_library=getattr(controller, "_projects", None),
        parent=engine,
        piano_profile=piano_profile,
        piano_source=piano_source,
    )
    app.installEventFilter(keyboard)
    score_lab = None
    if ui_variant == "studio":
        from ai_piano_studio.score_lab.controller import ScoreLabController

        score_lab = ScoreLabController(controller._projects.root.parent, playback, assistant=assistant, parent=engine)
        if assistant is not None:
            assistant.bind_song_recovery(score_lab)
        app.aboutToQuit.connect(score_lab.shutdown)
        engine.add_shutdown_callback(score_lab.shutdown)
    engine.rootContext().setContextProperty("scoreLab", score_lab)
    creative = None
    if ui_variant == 'studio':
        from ai_piano_studio.creative.controller import CreativeController
        from ai_piano_studio.creative.studio_apply import append_theme

        studio_busy = lambda: playback.playing or playback.preparing or keyboard.recording or bool(keyboard.heldKeys)

        creative = CreativeController(
            controller._projects.root.parent, piano_source, piano_mixer,
            studio_busy,
            apply_theme=lambda state: append_theme(controller, state, studio_busy),
            parent=engine,
        )
        app.aboutToQuit.connect(creative.shutdown)
        engine.add_shutdown_callback(creative.shutdown)
    engine.rootContext().setContextProperty('creative', creative)
    engine.rootContext().setContextProperty("editor", controller)
    engine.rootContext().setContextProperty("studioUiVariant", ui_variant)
    if studio_appearance is not None:
        studio_appearance.setParent(engine)
    engine.rootContext().setContextProperty("studioAppearance", studio_appearance)
    if ui_variant == "studio":
        from ai_piano_studio.audio.studio_feedback import StudioFeedbackController

        engine.rootContext().setContextProperty(
            "studioFeedback", StudioFeedbackController(playback, engine)
        )
    engine.rootContext().setContextProperty("playback", playback)
    engine.rootContext().setContextProperty("audioVisualizer", audio_visualizer)
    engine.rootContext().setContextProperty("pianoTone", piano_tone)
    engine.rootContext().setContextProperty("keyboard", keyboard)
    # Keep the legacy context name for C1/C2 QML compatibility while the
    # explicit scene bridge owns the UI-only scene state for C3.5.
    engine.rootContext().setContextProperty("appearance", scene)
    engine.rootContext().setContextProperty("scene", scene)
    engine.rootContext().setContextProperty("experience", experience)
    engine.rootContext().setContextProperty("harmonySettings", harmony)
    engine.rootContext().setContextProperty("harmony", harmony_controller)
    engine.rootContext().setContextProperty("harmonyPreview", harmony_controller.previewModel)
    engine.rootContext().setContextProperty("harmonyHelp", harmony_help)
    engine.rootContext().setContextProperty("savePreferences", preferences)
    engine.rootContext().setContextProperty("templates", templates)
    engine.rootContext().setContextProperty("assistant", assistant)
    engine.rootContext().setContextProperty("audioImport", audio_import)
    engine.rootContext().setContextProperty("midiImport", midi_import)
    engine.rootContext().setContextProperty("support", support)
    engine.rootContext().setContextProperty(
        "startupDeferNoncritical", bool(defer_noncritical)
    )
    _startup.mark("qml_engine_created")
    qml_path = package_root() / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    _startup.mark("main_qml_loaded")
    if not engine.rootObjects():
        raise RuntimeError(f"Unable to load UI from {qml_path}")
    root = engine.rootObjects()[0]
    # Tests and embedded callers may close the root window without quitting
    # the QApplication. Stop the analyzer before QML teardown in that case.
    root.exitAccepted.connect(audio_visualizer.shutdown)
    if creative is not None:
        creative.attach_session_guard(app, root)
        playback.transportInterrupted.connect(creative.suspend)
        from PySide6.QtMultimedia import QMediaDevices
        creative._media_devices = QMediaDevices(creative)
        creative._media_devices.audioOutputsChanged.connect(creative.suspend)
        root.exitAccepted.connect(creative.shutdown)
    root.destroyed.connect(lambda _destroyed=None: audio_visualizer.shutdown())
    _startup.mark("main_window_created")
    if _startup.enabled:
        visibility_deadline = time.monotonic() + 30.0

        def mark_window_visible() -> None:
            if bool(root.isVisible()):
                _mark_startup_once("main_window_visible")
            elif time.monotonic() < visibility_deadline:
                QTimer.singleShot(5, mark_window_visible)

        QTimer.singleShot(0, mark_window_visible)

    if defer_noncritical:
        engine._deferred_startup_state = {
            "assistant_history_path": assistant_history_path,
            "studio_appearance": studio_appearance,
            "assistant_holder": assistant_holder,
            "controller": controller,
            "playback": playback,
            "support_settings": support_settings,
            "recovery_store": recovery_store,
        }
    return app, engine, controller, playback, keyboard


def _initialize_deferred_services(
    engine: QQmlApplicationEngine, state: dict[str, object]
) -> object:
    assistant_holder = state["assistant_holder"]
    if not isinstance(assistant_holder, dict):
        raise RuntimeError("Deferred startup state is invalid")
    current = assistant_holder["value"]
    if current is not None:
        return current
    controller = state["controller"]
    playback = state["playback"]
    support_settings = state["support_settings"]
    recovery_store = state["recovery_store"]
    root = engine.rootObjects()[0]
    from ai_piano_studio.experience.support import ApplicationSupportController
    from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController

    created_assistant = MusicAssistantController(
        controller, playback=playback, parent=engine, history_path=state.get("assistant_history_path"),
        automatic_music=True, appearance=state.get('studio_appearance'),
        require_song_confirmation=state.get('studio_appearance') is not None,
        model_driven_assistant=state.get('studio_appearance') is not None,
        creator_enabled=state.get('studio_appearance') is not None,
    )
    score_lab = engine.rootContext().contextProperty('scoreLab')
    if score_lab is not None:
        score_lab._assistant = created_assistant
        created_assistant.bind_song_recovery(score_lab)
    assistant_holder["value"] = created_assistant
    engine.rootContext().setContextProperty("assistant", created_assistant)
    created_support = ApplicationSupportController(
        parent=engine,
        settings=support_settings,
        recovery_store=recovery_store,
        credential_reset=created_assistant.clearSavedCredentials,
    )
    engine.rootContext().setContextProperty("support", created_support)
    state["assistant"] = created_assistant
    state["support"] = created_support
    root.setProperty("deferredAssistantController", created_assistant)
    root.setProperty("deferredSupportController", created_support)
    root.setProperty("deferredAssistantReady", True)
    root.setProperty("deferredSupportReady", True)
    root.setProperty("deferredAppearanceReady", True)
    return created_assistant


def initialize_deferred_services(engine: QQmlApplicationEngine) -> object | None:
    """Finish optional startup services after the first window is usable."""
    state = getattr(engine, "_deferred_startup_state", None)
    return _initialize_deferred_services(engine, state) if isinstance(state, dict) else None


def _benchmark_controller(home: Path) -> EditorController:
    """Build a controller isolated from the user's settings and project files."""
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.persistence.autosave import NullRecoveryStore
    from ai_piano_studio.persistence.project_library import ProjectLibrary

    return EditorController(
        ProjectState(),
        project_library=ProjectLibrary(home / "Projects"),
        recovery_store=NullRecoveryStore(),
    )


def _schedule_startup_benchmark(
    app: QApplication,
    engine: QQmlApplicationEngine,
    controller: EditorController,
    playback: PlaybackController,
    benchmark_home: Path,
) -> None:
    """Exercise the editor's minimum usable path in an isolated process."""
    root = engine.rootObjects()[0]
    deadline = time.monotonic() + 30.0

    def enter_editor() -> None:
        try:
            from ai_piano_studio.domain.project import ProjectState
            from ai_piano_studio.persistence.project_json import save_project

            controller.newProject()
            root.setProperty("pageState", "editor")
            probe_path = benchmark_home / "Projects" / "startup-probe.pianoai.json"
            probe_path.parent.mkdir(parents=True, exist_ok=True)
            save_project(probe_path, ProjectState())
            controller.loadProject(str(probe_path))
            QTimer.singleShot(0, poll_editor)
        except Exception:
            logger.exception("Startup benchmark functional setup failed")
            _startup.mark("startup_complete")
            app.exit(2)

    def poll_editor() -> None:
        timeline = root.findChild(QObject, "timeline")
        if (
            timeline is not None
            and bool(timeline.property("visible"))
        ):
            try:
                controller.createNoteAt(100.0, 100.0)
                controller.synchronizeNow()
                playback.play()
                if not playback.playing:
                    QTimer.singleShot(100, poll_playback)
                    return
                finish_playback_probe()
            except Exception:
                logger.exception("Startup benchmark functional probe failed")
                _startup.mark("startup_complete")
                app.exit(2)
                return
        if time.monotonic() >= deadline:
            logger.error("Startup benchmark did not reach editor readiness")
            _startup.mark("startup_complete")
            app.exit(2)
            return
        QTimer.singleShot(5, poll_editor)

    def finish_playback_probe() -> None:
        playback.stop()
        playback.stopAudition("stop")
        mixer = getattr(playback, "piano_source", None)
        mixer = getattr(mixer, "mixer", None)
        if mixer is not None:
            mixer.mix_frames(480)
        voice_pool = getattr(getattr(playback, "_player", None), "_voice_pool", None)
        if voice_pool is not None:
            voice_pool.dispose()
        _startup.mark("editor_ready")
        _startup.mark("startup_complete")
        app.quit()

    def poll_playback() -> None:
        try:
            if playback.playing:
                finish_playback_probe()
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("playback did not enter the playing state")
            QTimer.singleShot(100, poll_playback)
        except Exception:
            logger.exception("Startup benchmark functional probe failed")
            _startup.mark("startup_complete")
            app.exit(2)
            return

    QTimer.singleShot(0, enter_editor)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    smoke_test = "--smoke-test" in arguments
    startup_benchmark = "--startup-benchmark" in arguments
    QApplication.setOrganizationName("AIPianoStudio")

    def report_uncaught(exc_type, exc_value, traceback) -> None:
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, traceback))
        QMessageBox.critical(None, "AI Piano Studio", "应用遇到错误，详情已写入日志。")

    sys.excepthook = report_uncaught
    benchmark_temp: tempfile.TemporaryDirectory[str] | None = None
    previous_defer_setting = os.environ.get("AI_PIANO_DEFER_NONCRITICAL_STARTUP")
    try:
        os.environ["AI_PIANO_DEFER_NONCRITICAL_STARTUP"] = "1"
        if startup_benchmark:
            raw_home = os.environ.get("AI_PIANO_STARTUP_BENCHMARK_HOME", "").strip()
            if raw_home:
                benchmark_home = Path(raw_home).resolve()
            else:
                benchmark_temp = tempfile.TemporaryDirectory(
                    prefix="ai-piano-startup-benchmark-"
                )
                benchmark_home = Path(benchmark_temp.name)
            benchmark_home.mkdir(parents=True, exist_ok=True)
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(
                QSettings.IniFormat,
                QSettings.Scope.UserScope,
                str(benchmark_home),
            )
            app, engine, controller, playback, keyboard = build_engine(
                controller=_benchmark_controller(benchmark_home)
            )
        else:
            app, engine, controller, playback, keyboard = build_engine()
        _keep_alive = (engine, controller, playback, keyboard)
        root = engine.rootObjects()[0]
        window_settings = QSettings("AIPianoStudio", "AI Piano Studio")
        restore_main_window(root, window_settings)
        root.closing.connect(lambda: save_main_window(root, window_settings))

        def configure_logging_after_window() -> None:
            try:
                configure_logging()
            except Exception:
                logger.exception("Application logging setup failed")

        if not startup_benchmark:
            QTimer.singleShot(0, configure_logging_after_window)
        if smoke_test:
            validate_smoke_resources()
            QTimer.singleShot(0, app.quit)
        elif startup_benchmark:
            _schedule_startup_benchmark(
                app,
                engine,
                controller,
                playback,
                benchmark_home,
            )
        else:
            QTimer.singleShot(0, lambda: initialize_deferred_services(engine))
        exit_code = app.exec()
        if not any(marker["phase"] == "startup_complete" for marker in _startup.markers):
            _startup.mark("startup_complete")
        return exit_code
    except Exception:
        try:
            configure_logging()
        except Exception:
            pass
        logger.exception("Application startup failed")
        return 1
    finally:
        if previous_defer_setting is None:
            os.environ.pop("AI_PIANO_DEFER_NONCRITICAL_STARTUP", None)
        else:
            os.environ["AI_PIANO_DEFER_NONCRITICAL_STARTUP"] = previous_defer_setting
        if benchmark_temp is not None:
            benchmark_temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
