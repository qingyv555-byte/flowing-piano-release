import logging
from pathlib import Path

from PySide6.QtCore import (
    QMetaObject,
    QObject,
    Qt,
    Q_ARG,
    qCritical,
    qInstallMessageHandler,
    qWarning,
)

from PySide6.QtCore import QSettings

from ai_piano_studio.__main__ import build_engine, initialize_deferred_services
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.logging_config import configure_logging
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.recent_files import RecentFiles
from ai_piano_studio.persistence.save_preferences import SavePreferences


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_configure_logging_writes_rotating_log(tmp_path, preserved_logging_handlers) -> None:
    path = configure_logging(tmp_path)
    logging.getLogger("test.probe").error("probe message")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert path.exists()
    assert "probe message" in path.read_text(encoding="utf-8")


def test_configure_logging_forwards_qt_messages_and_chains_handler(tmp_path, preserved_logging_handlers) -> None:
    seen = []
    original = qInstallMessageHandler(
        lambda message_type, context, message: seen.append(message)
    )
    try:
        path = configure_logging(tmp_path)
        qWarning("qt warning probe")
        qCritical("qt critical probe")
        _flush_root_handlers()
        content = path.read_text(encoding="utf-8")
        assert "WARNING qt qt warning probe" in content
        assert "ERROR qt qt critical probe" in content
        assert "qt warning probe" in seen
        assert "qt critical probe" in seen
    finally:
        qInstallMessageHandler(original)


def test_build_engine_starts_empty_project_with_welcome_overlay(qapp, qtlog, tmp_path) -> None:
    appearance_settings = QSettings(
        str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
    )
    appearance_settings.clear()
    harmony_settings = HarmonySettings(
        settings=QSettings(
            str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat
        )
    )
    app, engine, controller, playback, keyboard = build_engine(
        appearance_settings=appearance_settings,
        harmony_settings=harmony_settings,
    )

    assert app is qapp
    assert playback.playing is False
    assert keyboard.recording is False
    assert keyboard.octave == 4
    assert app.font().family() == "Microsoft YaHei UI"
    assert "Segoe UI Symbol" in app.font().families()
    assert len(engine.rootObjects()) == 1
    assert controller.noteModel.rowCount() == 0
    assert controller.isDirty is False
    assert controller.currentFilePath == ""
    appearance = engine.rootContext().contextProperty("appearance")
    assert appearance.themeId in {"dream-meadow", "cosmic-pulse", "sunset-haven"}
    assert appearance.dynamicBackgroundEnabled is True
    harmony = engine.rootContext().contextProperty("harmonySettings")
    assert isinstance(harmony, HarmonySettings)
    assert harmony.mode == "off"
    harmony_controller = engine.rootContext().contextProperty("harmony")
    assert isinstance(harmony_controller, HarmonySuggestionController)
    root = engine.rootObjects()[0]
    welcome = root.findChild(QObject, "welcomeOverlay")
    assert welcome is not None
    assert welcome.property("visible") is True
    assert not [
        record for record in qtlog.records
        if "ReferenceError" in record.message or "TypeError" in record.message
    ]
    root.close()


def test_deferred_startup_services_attach_after_the_first_window(qapp, qtlog, tmp_path) -> None:
    """Secondary AI/settings surfaces must load on demand without losing their bindings."""
    app, engine, controller, playback, keyboard = build_engine(
        defer_noncritical=True,
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        ),
        support_settings=QSettings(
            str(tmp_path / "support.ini"), QSettings.Format.IniFormat
        ),
    )
    root = engine.rootObjects()[0]
    try:
        assert engine.rootContext().contextProperty("assistant") is None
        assert engine.rootContext().contextProperty("support") is None
        assert root.findChild(QObject, "assistantWindow") is None
        assert root.findChild(QObject, "supportDialog") is None
        assert root.findChild(QObject, "backgroundSettingsDialog") is None
        pending_prompt = "请帮我生成一小段钢琴旋律"
        assert QMetaObject.invokeMethod(
            root,
            "openAssistantWithPrompt",
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", pending_prompt),
        )
        assert root.findChild(QObject, "feedbackHost").property("message") == (
            "AI助手正在加载，请稍候"
        )

        initialize_deferred_services(engine)
        qapp.processEvents()

        assert engine.rootContext().contextProperty("assistant") is not None
        assert engine.rootContext().contextProperty("support") is not None
        # A click made while optional services were still loading must be
        # replayed once the service boundary becomes ready.
        assistant_window = root.findChild(QObject, "assistantWindow")
        assert assistant_window is not None
        qapp.processEvents()
        assert assistant_window.findChild(QObject, "assistantInput").property("text") == pending_prompt
        assert root.findChild(QObject, "supportDialog") is None
        assert root.findChild(QObject, "backgroundSettingsDialog") is None
        assistant_window.close()

        assert QMetaObject.invokeMethod(
            root,
            "openSupportDialog",
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", None),
        )
        qapp.processEvents()
        support_dialog = root.findChild(QObject, "supportDialog")
        assert support_dialog is not None
        assert support_dialog.findChild(QObject, "diagnosticsPreview") is not None
        assert support_dialog.findChild(QObject, "copyDiagnosticsPreviewButton") is not None
        assert support_dialog.findChild(QObject, "feedbackButton") is not None
        support_dialog.close()

        assert QMetaObject.invokeMethod(
            root,
            "openAppearancePopup",
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", None),
        )
        qapp.processEvents()
        theme_picker = root.findChild(QObject, "backgroundSettingsDialog")
        assert theme_picker is not None
        theme_picker.close()

        # The native file chooser owns the URL argument; exercise the same
        # deferred loader path without opening a platform file dialog here.
        root.setProperty("audioImportDialogRequested", True)
        qapp.processEvents()
        audio_dialog = root.findChild(QObject, "audioImportDialog")
        assert audio_dialog is not None
        assert QMetaObject.invokeMethod(
            audio_dialog, "open", Qt.ConnectionType.DirectConnection
        )
        audio_dialog.close()
        assert not [
            record for record in qtlog.records
            if "ReferenceError" in record.message or "TypeError" in record.message
        ]
    finally:
        root.close()


def test_build_engine_exposes_user_visible_support_and_home_actions(
    qapp, qtlog, tmp_path
) -> None:
    experience = ExperienceSettings(
        settings=QSettings(str(tmp_path / "first-run.ini"), QSettings.Format.IniFormat)
    )
    app, engine, controller, playback, keyboard = build_engine(
        experience_settings=experience,
        support_settings=QSettings(
            str(tmp_path / "support.ini"), QSettings.Format.IniFormat
        ),
    )
    root = engine.rootObjects()[0]

    try:
        support = engine.rootContext().contextProperty("support")
        assert support is not None
        assert root.findChild(QObject, "supportDialog") is not None
        # The compact command-bar update moved help to the home navigation.
        # Verify the current user path rather than the removed settings button.
        help_button = root.findChild(QObject, "homeNavHelp")
        assert help_button is not None and help_button.property("visible")
        help_button.clicked.emit()
        qapp.processEvents()
        support_dialog = root.findChild(QObject, "supportDialog")
        assert support_dialog.property("opened") is True
        support_dialog.close()
        audio_import = engine.rootContext().contextProperty("audioImport")
        assert audio_import is not None
        audio_dialog = root.findChild(QObject, "audioImportDialog")
        assert audio_dialog is not None
        assert root.findChild(QObject, "audioImportFileDialog") is not None
        assert root.findChild(QObject, "audioReanalyzeButton") is not None
        for object_name in (
            "audioExtractionModeBox",
            "audioMinimumDurationBox",
            "audioNoiseThresholdBox",
            "audioQuantizeStrengthSlider",
            "audioSimplifyDensitySlider",
            "audioPitchMinBox",
            "audioPitchMaxBox",
        ):
            assert audio_dialog.findChild(QObject, object_name) is not None
        assert audio_import.setExtractionControls(
            "bass", 0.12, 0.45, 0.5, 0.25, 28, 72
        ) is True
        assert audio_import.session.extractionMode == "bass"
        assert audio_import.session.minimumNoteDuration == 0.12
        assert audio_import.session.pitchMax == 72
        top_bar_source = (Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml" / "EditorTopBar.qml").read_text(encoding="utf-8")
        assert 'objectName: "importMenu"' in top_bar_source
        assert 'objectName: "exportMenu"' in top_bar_source
        assert 'objectName: "importButton"' in top_bar_source
        assert 'objectName: "exportButton"' in top_bar_source
        assert 'text: "导入"' in top_bar_source
        assert 'text: topBar.audioExporting ? "导出中..." : "导出"' in top_bar_source
        assert 'objectName: "importAudioButton"' not in top_bar_source

        welcome = root.findChild(QObject, "welcomeOverlay")
        launch = root.findChild(QObject, "welcomeLaunchView")
        assert welcome.property("firstRun") is True
        assert launch.findChild(QObject, "firstRunGuide") is None
        assert launch.findChild(QObject, "homeBrowseExamples").property("text") == "浏览示例"
        assert launch.findChild(QObject, "homePrimaryCreate").property("text") == "开始创作"

        experience.completeFirstRun()
        qapp.processEvents()
        assert welcome.property("firstRun") is False
        assert not [
            record for record in qtlog.records
            if "ReferenceError" in record.message or "TypeError" in record.message
        ]
    finally:
        root.close()


def test_build_engine_injects_one_profile_into_all_audio_routes(qapp, tmp_path) -> None:
    app, engine, controller, playback, keyboard = build_engine(
        controller=EditorController(ProjectState()),
    )
    harmony = engine.rootContext().contextProperty("harmony")
    templates = engine.rootContext().contextProperty("templates")

    try:
        assert playback.piano_profile is templates.piano_profile
        assert playback.piano_profile is harmony.auditionPlayer.piano_profile
    finally:
        engine.rootObjects()[0].close()


def test_build_engine_injects_one_continuous_source_and_tone_settings(qapp, tmp_path) -> None:
    """Fails if any application audio route constructs its own piano source."""
    tone_settings = QSettings(
        str(tmp_path / "piano-tone.ini"), QSettings.Format.IniFormat
    )
    tone_settings.clear()
    tone_settings.sync()
    app, engine, controller, playback, keyboard = build_engine(
        controller=EditorController(ProjectState()),
        piano_tone_settings=tone_settings,
    )
    tone = engine.rootContext().contextProperty("pianoTone")
    templates = engine.rootContext().contextProperty("templates")
    harmony = engine.rootContext().contextProperty("harmony")

    try:
        source = playback.piano_source
        assert source is not None
        assert engine.rootContext().contextProperty("pianoSource") is None
        assert playback._player._mixer is source.mixer
        assert templates.piano_source is source
        assert harmony.auditionPlayer.piano_source is source
        assert tone.value == 0.5
    finally:
        engine.rootObjects()[0].close()


def test_build_engine_injects_save_preferences_and_updates_controller_policy(
    qapp, tmp_path
) -> None:
    preferences = SavePreferences(
        settings=QSettings(str(tmp_path / "save.ini"), QSettings.Format.IniFormat)
    )
    app, engine, controller, playback, keyboard = build_engine(
        save_preferences=preferences,
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        ),
    )

    assert engine.rootContext().contextProperty("savePreferences") is preferences
    assert controller._autosave_enabled is False
    preferences.setAutosaveEnabled(True)
    preferences.setAutosaveDelaySeconds(120)
    assert controller._autosave_enabled is True
    assert controller._autosave_timer.interval() == 120_000
    engine.rootObjects()[0].close()


def test_build_engine_keeps_recent_project_closed_on_normal_start(qapp, tmp_path) -> None:
    project = tmp_path / "latest.pianoai.json"
    save_project(project, ProjectState((Note.create(pitch=67, start_time=0.0, duration=0.5),)))
    recents = RecentFiles(settings=QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat))
    recents.remember(str(project))
    controller = EditorController(recent_files=recents)
    experience = ExperienceSettings(settings=QSettings(str(tmp_path / "experience.ini"), QSettings.Format.IniFormat))
    experience.completeFirstRun()

    app, engine, loaded, playback, keyboard = build_engine(
        controller=controller, experience_settings=experience
    )

    root = engine.rootObjects()[0]
    welcome = root.findChild(QObject, "welcomeOverlay")

    assert loaded.currentFilePath == ""
    assert loaded.noteModel.rowCount() == 0
    assert welcome.property("visible") is True
    assert welcome.property("pageState") == "launch"
    root.close()


def test_build_engine_harmony_uses_the_full_advanced_pitch_range(
    qapp, tmp_path
) -> None:
    note = Note.create(id="advanced-seed", pitch=96, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((note,), frozenset({note.id})))
    experience = ExperienceSettings(
        settings=QSettings(str(tmp_path / "advanced.ini"), QSettings.Format.IniFormat)
    )
    experience.setMode("advanced")
    harmony_settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    harmony_settings.setMode("preview")

    app, engine, loaded, playback, keyboard = build_engine(
        controller=controller,
        experience_settings=experience,
        harmony_settings=harmony_settings,
    )
    root = engine.rootObjects()[0]
    harmony = engine.rootContext().contextProperty("harmony")
    try:
        loaded.setPitchRange(72, 60)
        request = harmony._snapshot_request(include_manual_tail=True)

        assert (request.min_pitch, request.max_pitch) == (12, 108)
    finally:
        harmony.close()
        root.close()
