from pathlib import Path
from time import perf_counter

import pytest
from PySide6.QtCore import (
    Q_ARG,
    QCoreApplication,
    QMetaObject,
    QObject,
    Property,
    QPoint,
    QPointF,
    QSettings,
    Signal,
    Slot,
    QTemporaryDir,
    Qt,
    QUrl,
)
from PySide6.QtGui import QColor, QWheelEvent
from ai_piano_studio.qml_engine import ApplicationQmlEngine as QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.appearance.scene_controller import SceneController
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.editor.composite import CompositeGroupingMode, CompositeNoteGrouper
from ai_piano_studio.editor.midi_import_controller import MidiImportController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.help_content import HarmonyHelpContent
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.persistence.save_preferences import SavePreferences
from ai_piano_studio.persistence.midi import encode_midi


QML_DIR = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"


class FakePianoTone(QObject):
    changed = Signal()
    availabilityChanged = Signal()

    def __init__(
        self,
        parent=None,
        *,
        value=0.5,
        available=True,
        status_message="Sample Piano ready",
    ):
        super().__init__(parent)
        self._value = value
        self._available = available
        self._status_message = status_message
        self.committed_values = []

    @Property(float, notify=changed)
    def value(self):
        return self._value

    @Property(bool, notify=availabilityChanged)
    def available(self):
        return self._available

    @Property(str, notify=availabilityChanged)
    def statusMessage(self):
        return self._status_message

    @Slot(float)
    def setValue(self, value):
        self.committed_values.append(value)
        self._value = value
        self.changed.emit()


class DeterministicPlaybackPlayer(QObject):
    positionChanged = Signal(float)
    playingChanged = Signal(bool)
    playbackFailed = Signal(str)
    temporaryPlayingChanged = Signal(bool)
    temporaryPositionChanged = Signal(float)
    temporaryFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.temporary_notes = None
        self.temporary_play_calls = 0
        self.stop_temporary_calls = 0
        self.finish_temporary_calls = 0

    def set_volume(self, _volume):
        pass

    def set_playback_rate(self, _rate):
        pass

    def play_notes(self, _notes):
        self.playingChanged.emit(True)

    def pause(self):
        self.playingChanged.emit(False)

    def stop(self, *_args, **_kwargs):
        self.playingChanged.emit(False)
        self.positionChanged.emit(0.0)

    def seek(self, seconds):
        self.positionChanged.emit(float(seconds))

    def audition(self, *_args, **_kwargs):
        return 1

    def release_audition(self, *_args, **_kwargs):
        pass

    def stop_audition(self, *_args, **_kwargs):
        pass

    def play_temporary_notes(self, notes):
        self.temporary_notes = notes
        self.temporary_play_calls += 1
        self.temporaryPlayingChanged.emit(True)

    def stop_temporary_notes(self, *_args, **_kwargs):
        self.temporary_notes = None
        self.stop_temporary_calls += 1
        self.temporaryPlayingChanged.emit(False)

    def finish_temporary_notes(self):
        self.temporary_notes = None
        self.finish_temporary_calls += 1

    def finish_temporary(self):
        self.temporaryPlayingChanged.emit(False)
        self.temporaryFinished.emit()


class FakeKeyboardClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def load_shell(
    qapp,
    notes=None,
    *,
    player=None,
    piano_tone=None,
    keyboard_clock=None,
    experience_settings_file=None,
    clear_experience_settings=True,
    selected_ids=None,
):
    notes = notes or (Note.create(id="demo", pitch=60, start_time=1.2, duration=0.8),)
    controller = EditorController(ProjectState(notes, frozenset(note.id for note in notes)
                                               if selected_ids is None else selected_ids))
    engine = QQmlApplicationEngine()
    save_directory = QTemporaryDir()
    assert save_directory.isValid()
    engine._temporary_save_preferences_directory = save_directory
    save_preferences = SavePreferences(
        parent=engine,
        settings=QSettings(
            str(Path(save_directory.path()) / "save.ini"),
            QSettings.Format.IniFormat,
        ),
    )
    playback = PlaybackController(controller, player=player, parent=engine)
    keyboard = KeyboardController(
        controller,
        playback,
        clock=keyboard_clock,
        parent=engine,
    )
    appearance = SceneController(parent=engine, settings=QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        "AI Piano Studio Tests",
        "qml-smoke",
    ))
    appearance.setThemeId("dream-meadow")
    appearance.setDynamicBackgroundEnabled(True)
    appearance.setMotionLevel("medium")
    appearance.setPowerSaveEnabled(False)
    appearance.setFocusModeEnabled(False)
    appearance.setBackgroundEnabled(True)
    appearance.setBackgroundPreset("stage")
    appearance.clearCustomBackground()
    if experience_settings_file is None:
        experience_settings = QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            "AI Piano Studio Tests",
            "qml-smoke-experience",
        )
    else:
        experience_settings = QSettings(
            str(experience_settings_file), QSettings.Format.IniFormat
        )
    if clear_experience_settings:
        experience_settings.clear()
    harmony_store = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        "AI Piano Studio Tests",
        "qml-smoke-harmony",
    )
    harmony_store.clear()
    experience = ExperienceSettings(parent=engine, settings=experience_settings)
    harmony_settings = HarmonySettings(parent=engine, settings=harmony_store)
    harmony = HarmonySuggestionController(
        controller,
        harmony_settings,
        experience=experience,
        playback=playback,
        keyboard=keyboard,
        parent=engine,
    )
    harmony_help = HarmonyHelpContent(parent=engine)
    midi_import = MidiImportController(controller, parent=engine)
    engine.rootContext().setContextProperty("editor", controller)
    engine.rootContext().setContextProperty("playback", playback)
    engine.rootContext().setContextProperty("keyboard", keyboard)
    engine.rootContext().setContextProperty("appearance", appearance)
    engine.rootContext().setContextProperty("scene", appearance)
    engine.rootContext().setContextProperty("experience", experience)
    engine.rootContext().setContextProperty("harmonySettings", harmony_settings)
    engine.rootContext().setContextProperty("harmony", harmony)
    engine.rootContext().setContextProperty("harmonyPreview", harmony.previewModel)
    engine.rootContext().setContextProperty("harmonyHelp", harmony_help)
    engine.rootContext().setContextProperty("midiImport", midi_import)
    engine.rootContext().setContextProperty("savePreferences", save_preferences)
    piano_tone = piano_tone or FakePianoTone(parent=engine)
    if piano_tone.parent() is None:
        piano_tone.setParent(engine)
    engine._piano_tone = piano_tone
    engine.rootContext().setContextProperty("pianoTone", piano_tone)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    qapp.processEvents()
    assert len(engine.rootObjects()) == 1
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    return engine, root, controller


def _visual_item(root_item: QQuickItem, object_name: str) -> QQuickItem:
    pending = [root_item]
    while pending:
        item = pending.pop()
        if item.objectName() == object_name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"missing visual item: {object_name}")


@pytest.mark.parametrize('status', ['saved', 'pending'])
def test_main_stop_button_can_stop_selection_audition_at_project_origin(qapp, status):
    from test_selection_audition import FakeTemporaryPlayer
    player = FakeTemporaryPlayer()
    notes = (Note.create(id='a', pitch=60, start_time=0, duration=2, status=status),)
    engine, root, controller = load_shell(qapp, player=player, notes=notes)
    playback = engine.rootContext().contextProperty('playback')
    playback.selectionAudition.playSelection()
    qapp.processEvents()
    button = _visual_item(root.contentItem(), 'stopButton')
    assert button.isEnabled()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton,
                     pos=button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint())
    assert not playback.selectionAudition.playing


def test_timeline_canvas_font_does_not_emit_invalid_family_warnings(qapp, qtlog):
    from PySide6.QtGui import QFontDatabase
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    engine, root, controller = load_shell(qapp)
    root.grabWindow()
    QTest.qWait(30)
    assert not [r.message for r in qtlog.records if 'font families specified are invalid' in r.message]
    root.close()


def test_action_rail_initial_settings_have_no_reentrant_bindings(qapp, qtlog):
    engine, root, controller = load_shell(qapp, selected_ids=frozenset())
    controller.clearSelection()
    experience = engine.rootContext().contextProperty('experience')
    experience.setMode('beginner')
    qapp.processEvents()
    assert not [r.message for r in qtlog.records if 'Binding loop' in r.message]
    root.close()


def test_restored_collapsed_inspector_has_no_binding_loop(qapp, qtlog, tmp_path):
    path = tmp_path / 'experience.ini'
    experience = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    experience.setInspectorPanelMode('collapsed')
    engine, root, controller = load_shell(qapp, experience_settings_file=path,
                                          clear_experience_settings=False)
    assert not [r.message for r in qtlog.records if 'Binding loop' in r.message]
    root.close()


def test_lightweight_shell_loads_only_required_editor_regions(qapp, qtlog) -> None:
    engine, root, controller = load_shell(qapp)
    assert root.objectName() == "mainWindow"
    assert root.property("appTextColor") == QColor("#DCE8F5")
    required = (
        "commandBar", "timeline", "inspector", "statusBar", "saveButton",
        "undoButton", "redoButton", "pitchField", "startTimeField",
        "durationField", "velocityField", "instrumentField", "pianoCanvas",
        "marqueeSelection", "transportPlayPauseButton", "stopButton",
        "transportPositionLabel", "playhead", "keyboardBar",
        "recordButton", "octaveLabel", "openButton",
        "importButton", "exportButton", "moreMenuButton", "moreOpenAction",
        "moreBackgroundSettingsAction", "moreHarmonyAction", "tempoBar", "bpmField", "timeSignatureBox",
        "snapToggle", "snapDivisionBox", "gridLabel", "barRuler",
        "welcomeOverlay", "welcomeLibraryView", "libraryBackButton", "recentList",
        "projectRail", "libraryProjectList", "libraryNewProjectButton",
        "projectRailOpenButton", "projectRailSearchField",
    )
    for name in required:
        assert root.findChild(object, name) is not None
    assert root.findChild(object, "musicSettingsButton") is None
    assert root.findChild(object, "musicSettingsPopup") is None
    forbidden = (
        "toolRail", "trackPanel", "aiCommand", "effectsPanel",
        "modeSwitch", "basicModeSwitch", "velocityLane", "transportGroup",
        "pauseButton", "playButton", "positionLabel", "projectButton",
    )
    for name in forbidden:
        assert root.findChild(object, name) is None
    assert root.findChild(object, "moreAdvancedAction") is None
    texts = [
        str(item.property("text"))
        for item in root.findChildren(QObject)
        if item.property("text") is not None
    ]
    for forbidden_text in ("AI 指令", "力度通道"):
        assert not any(forbidden_text in text for text in texts)
    assert not [
        record for record in qtlog.records
        if "ReferenceError" in record.message or "TypeError" in record.message
    ]
    root.close()


def test_beginner_controls_explain_and_adjust_velocity_and_volume(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    velocity_slider = root.findChild(object, "beginnerVelocitySlider")
    velocity_value = root.findChild(object, "velocityValueLabel")
    volume_slider = root.findChild(object, "transportVolumeSlider")
    volume_value = root.findChild(object, "volumePercentLabel")

    assert velocity_slider is not None
    assert velocity_slider.property("value") == 75
    assert velocity_value.property("text") == "自然 · 75 / 127"
    assert volume_slider is not None
    assert volume_value.property("text") == "65%"
    root.close()


def test_transport_exposes_playback_rate_control(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    rate_control = root.findChild(object, "transportPlaybackRateControl")
    rate_label = root.findChild(object, "playbackRateLabel")

    assert rate_control is not None
    assert rate_control.property("currentValue") == pytest.approx(1.0)
    assert rate_label.property("text") == "1.0×"
    root.close()


def test_editor_top_bar_keeps_keyboard_hint_clear_of_rhythm_controls(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(2200)
    root.setHeight(720)
    qapp.processEvents()

    hint = root.findChild(object, "keyboardHint")
    keyboard_bar = root.findChild(object, "keyboardBar")
    rhythm = root.findChild(object, "rhythmGroup")
    assert hint is not None
    assert keyboard_bar is not None
    assert rhythm is not None
    hint_right = keyboard_bar.property("x") + hint.property("x") + hint.property("width")
    assert hint_right + 8 <= rhythm.property("x")
    root.close()


def test_editor_icons_are_tinted_for_dark_theme_contrast(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    undo_button = root.findChild(object, "undoButton")
    assert undo_button is not None
    icon_tint = undo_button.findChild(object, "iconTint")
    assert icon_tint is not None
    assert icon_tint.property("color") == QColor("#DCE8F5")
    root.close()


def test_timeline_canvas_uses_height_below_ruler(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline = root.findChild(object, "timeline")
    canvas = root.findChild(object, "pianoCanvas")
    ruler = root.findChild(object, "barRuler")
    selection = root.findChild(object, "selectionActions")
    assert canvas.property("y") == pytest.approx(
        ruler.property("y") + ruler.property("height")
    )
    assert canvas.property("height") == pytest.approx(
        timeline.property("height")
        - ruler.property("height")
    )
    root.close()


def test_timeline_loads_a_default_mini_map_overlay(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    mini_map = root.findChild(object, "miniMap")

    assert mini_map is not None
    assert mini_map.property("visible") is True
    assert mini_map.property("width") == pytest.approx(280)
    assert mini_map.property("height") == pytest.approx(160)
    assert root.findChild(object, "miniMapMoveArea") is not None
    assert root.findChild(object, "miniMapResizeHandle") is not None
    assert root.findChild(object, "miniMapSettingsButton") is not None
    root.close()


def test_mini_map_clicking_its_header_preserves_the_panel_position(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    mini_map = root.findChild(object, "miniMap")
    experience = engine.rootContext().contextProperty("experience")

    experience.setMiniMapPosition(96.0, 72.0)
    qapp.processEvents()
    original_position = (mini_map.property("x"), mini_map.property("y"))

    assert QMetaObject.invokeMethod(mini_map, "beginMove", Qt.ConnectionType.DirectConnection)
    qapp.processEvents()
    assert QMetaObject.invokeMethod(mini_map, "finishMove", Qt.ConnectionType.DirectConnection)
    qapp.processEvents()

    assert (mini_map.property("x"), mini_map.property("y")) == pytest.approx(original_position)
    assert (experience.miniMapX, experience.miniMapY) == pytest.approx(original_position)
    root.close()


def test_collapsing_inspector_docks_mini_map_to_the_lower_right(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    mini_map = root.findChild(object, "miniMap")
    inspector = root.findChild(object, "inspector")
    experience = engine.rootContext().contextProperty("experience")

    experience.setMiniMapPosition(96.0, 72.0)
    qapp.processEvents()
    inspector.setProperty("collapsed", True)
    qapp.processEvents()

    assert mini_map.property("x") == pytest.approx(mini_map.property("maximumX"))
    assert mini_map.property("y") == pytest.approx(mini_map.property("maximumY"))
    root.close()


def test_reentering_editor_preserves_the_persisted_inspector_state(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    inspector = root.findChild(object, "inspector")

    inspector.setProperty("collapsed", True)
    qapp.processEvents()
    assert root.property("inspectorCollapsed") is True
    assert QMetaObject.invokeMethod(
        root, "setPage", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", "launch"), Q_ARG("QVariant", False),
    )
    assert QMetaObject.invokeMethod(
        root, "setPage", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", "editor"), Q_ARG("QVariant", False),
    )
    qapp.processEvents()

    assert root.property("inspectorCollapsed") is True
    root.close()


def test_mini_map_resizes_moves_locks_ratio_and_resets_without_recreation(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    mini_map = root.findChild(object, "miniMap")
    experience = engine.rootContext().contextProperty("experience")
    timeline = root.findChild(object, "timeline")

    assert QMetaObject.invokeMethod(mini_map, "beginResize", Qt.ConnectionType.DirectConnection)
    assert QMetaObject.invokeMethod(
        mini_map,
        "resizeFromDrag",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", -80.0),
        Q_ARG("QVariant", -40.0),
    )
    qapp.processEvents()
    expected_width = min(360, mini_map.property("maximumWidth"))
    assert (mini_map.property("width"), mini_map.property("height")) == pytest.approx((expected_width, 200))
    assert QMetaObject.invokeMethod(mini_map, "finishResize", Qt.ConnectionType.DirectConnection)
    assert (experience.miniMapWidth, experience.miniMapHeight) == pytest.approx((expected_width, 200))
    resize_handle = root.findChild(object, "miniMapResizeHandle")
    assert (resize_handle.property("x"), resize_handle.property("y")) == pytest.approx((0, 0))

    experience.setMiniMapAspectLocked(True)
    assert QMetaObject.invokeMethod(mini_map, "beginResize", Qt.ConnectionType.DirectConnection)
    assert QMetaObject.invokeMethod(
        mini_map,
        "resizeFromDrag",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", -20.0),
        Q_ARG("QVariant", -90.0),
    )
    qapp.processEvents()
    assert mini_map.property("width") / mini_map.property("height") == pytest.approx(16 / 9)

    assert QMetaObject.invokeMethod(mini_map, "beginMove", Qt.ConnectionType.DirectConnection)
    assert QMetaObject.invokeMethod(
        mini_map,
        "moveFromDrag",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", -20.0),
        Q_ARG("QVariant", -12.0),
    )
    assert QMetaObject.invokeMethod(mini_map, "finishMove", Qt.ConnectionType.DirectConnection)
    assert experience.miniMapX == pytest.approx(mini_map.property("x"))
    assert experience.miniMapY == pytest.approx(mini_map.property("y"))

    assert mini_map.property("width") <= timeline.property("width") * 0.35 + 0.1
    assert mini_map.property("height") <= timeline.property("height") * 0.4 + 0.1
    assert QMetaObject.invokeMethod(mini_map, "resetSize", Qt.ConnectionType.DirectConnection)
    assert (experience.miniMapWidth, experience.miniMapHeight) == pytest.approx((280, 160))
    assert root.findChild(object, "miniMap") is mini_map

    timeline.setProperty("width", 500)
    timeline.setProperty("height", 320)
    qapp.processEvents()
    assert mini_map.property("width") == pytest.approx(175)
    assert mini_map.property("height") == pytest.approx(128)

    experience.setMiniMapVisible(False)
    qapp.processEvents()
    assert mini_map.property("visible") is False
    experience.setMiniMapVisible(True)
    assert root.findChild(object, "miniMapSettingsMenu") is not None
    assert root.findChild(object, "miniMapSettingsButton") is not None
    root.close()


def test_hidden_mini_map_has_no_canvas_restore_control_and_menu_preserves_layout(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    mini_map = root.findChild(object, "miniMap")
    experience = engine.rootContext().contextProperty("experience")

    experience.setMiniMapSize(332.0, 188.0)
    experience.setMiniMapPosition(104.0, 68.0)
    experience.setMiniMapVisible(False)
    qapp.processEvents()

    assert mini_map.property("visible") is False
    assert root.findChild(object, "miniMapRestoreButton") is None
    # View options now use StudioOptionItem's clicked signal.
    root.findChild(object, "studioOverviewAction").clicked.emit()
    qapp.processEvents()

    assert experience.miniMapVisible is True
    assert mini_map.property("visible") is True
    assert (experience.miniMapWidth, experience.miniMapHeight) == pytest.approx((332.0, 188.0))
    assert (experience.miniMapX, experience.miniMapY) == pytest.approx((104.0, 68.0))
    root.close()


def test_timeline_reserves_transport_space_and_keeps_pitch_rows_readable(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline = root.findChild(object, "timeline")
    transport = root.findChild(object, "bottomTransportBar")

    root.setHeight(640)
    qapp.processEvents()

    assert timeline.property("y") + timeline.property("height") <= transport.property("y")
    assert timeline.property("rowHeight") == pytest.approx(18)
    assert timeline.property("minimumPitch") == 12
    assert timeline.property("maximumPitch") == 108
    timeline.setProperty("viewportTop", 72)
    qapp.processEvents()
    assert root.findChild(object, "centerCPianoKey") is not None

    root.close()


def test_pitch_viewport_can_scroll_in_standard_mode(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline = root.findChild(object, "timeline")
    before = timeline.property("highestPitch")

    assert QMetaObject.invokeMethod(
        timeline, "scrollPitchViewport", Qt.ConnectionType.DirectConnection, Q_ARG("QVariant", 1)
    )
    assert timeline.property("highestPitch") > before
    root.close()


def test_loaded_low_notes_are_brought_into_the_pitch_viewport(qapp) -> None:
    notes = (
        Note.create(id="c1", pitch=24, start_time=0.0, duration=0.5),
        Note.create(id="c2", pitch=36, start_time=1.0, duration=0.5),
    )
    engine, root, controller = load_shell(qapp, notes)
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    timeline = root.findChild(object, "timeline")
    timeline.setProperty("viewportTop", 108)

    controller.loadSucceeded.emit("fixture.aips")
    qapp.processEvents()

    assert timeline.property("lowestPitch") <= 24 <= timeline.property("highestPitch")
    root.close()


def test_selecting_visible_note_preserves_manual_pitch_viewport(qapp) -> None:
    notes = (
        Note.create(id="c2", pitch=36, start_time=0.0, duration=0.5),
        Note.create(id="visible", pitch=60, start_time=1.0, duration=0.5),
        Note.create(id="c6", pitch=84, start_time=2.0, duration=0.5),
    )
    engine, root, controller = load_shell(qapp, notes)
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    timeline = root.findChild(object, "timeline")
    timeline.setProperty("viewportTop", 66)
    qapp.processEvents()

    assert timeline.property("lowestPitch") <= 60 <= timeline.property("highestPitch")
    assert timeline.property("highestPitch") == 66
    controller.selectNote("visible")
    assert controller.selectedPitchRange == {"lowest": 60, "highest": 60}
    qapp.processEvents()

    assert timeline.property("highestPitch") == 66
    root.close()


def test_playhead_is_available_at_time_zero_for_a_nonempty_project(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    playhead = root.findChild(object, "playhead")
    playback = engine.rootContext().contextProperty("playback")

    assert playback.positionSeconds == 0.0
    assert playhead.property("visible") is True
    root.close()


def test_playhead_is_visible_while_recording(qapp) -> None:
    # This checks UI visibility; a deterministic player keeps recording silent.
    from tests.qt.test_playback import FakePlayer
    engine, root, controller = load_shell(qapp, player=FakePlayer())
    playhead = root.findChild(object, "playhead")
    keyboard = engine.rootContext().contextProperty("keyboard")

    keyboard.startRecording()
    qapp.processEvents()
    assert playhead.property("visible") is True
    keyboard.stopRecording()
    root.close()


def test_timeline_exposes_follow_and_playback_navigation(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline = root.findChild(object, "timeline")

    assert timeline.property("followPlayback") is True
    assert root.findChild(object, "returnToPlaybackButton") is not None
    assert root.findChild(object, "playheadDragArea") is not None
    assert root.findChild(object, "timelineSeekArea") is not None
    assert root.findChild(object, "timelineZoomArea") is not None
    root.close()


def test_follow_playback_reveals_current_cursor_immediately_and_tracks_updates(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(object, "timeline")
    playback = engine.rootContext().contextProperty("playback")

    timeline.setProperty("followPlayback", False)
    timeline.setProperty("viewStartSeconds", 20.0)
    playback.seek(5.0)
    _invoke_timeline(timeline, "setFollowPlayback", True)
    qapp.processEvents()

    visible_seconds = float(timeline.property("visibleSeconds"))
    view_start = float(timeline.property("viewStartSeconds"))
    assert view_start <= playback.positionSeconds <= view_start + visible_seconds
    assert view_start == pytest.approx(
        max(0.0, playback.positionSeconds - visible_seconds * 0.4)
    )
    playback.seek(50.0)
    _invoke_timeline(timeline, "setFollowPlayback", True)
    qapp.processEvents()
    view_start = float(timeline.property("viewStartSeconds"))
    assert view_start <= playback.positionSeconds <= view_start + visible_seconds
    assert playback.positionSeconds - view_start == pytest.approx(visible_seconds * 0.4)
    root.close()


def test_selection_audition_moves_the_timeline_playhead_with_temporary_position(qapp) -> None:
    notes = (Note.create(id="selected", pitch=60, start_time=4.0, duration=1.0),)
    player = DeterministicPlaybackPlayer()
    engine, root, _controller = load_shell(qapp, notes=notes, player=player)
    playback = engine.rootContext().contextProperty("playback")
    timeline = root.findChild(object, "timeline")
    playhead = root.findChild(object, "playhead")

    playback.selectionAudition.playSelection()
    qapp.processEvents()
    assert timeline.property("cursorSeconds") == pytest.approx(4.0)

    player.temporaryPositionChanged.emit(0.35)
    qapp.processEvents()

    assert timeline.property("cursorSeconds") == pytest.approx(4.35)
    assert playhead.property("x") == pytest.approx(
        timeline.property("keyWidth")
        + (4.35 - timeline.property("viewStartSeconds"))
            * timeline.property("pixelsPerSecond")
    )
    root.close()


def test_editor_background_layer_is_passive_and_uses_stage_asset(qapp) -> None:
    engine, root, _controller = load_shell(qapp)

    background_layer = root.findChild(QQuickItem, "editorBackgroundLayer")
    background_image = root.findChild(QQuickItem, "editorBackgroundImage")
    background_overlay = root.findChild(QQuickItem, "editorBackgroundOverlay")
    ruler = root.findChild(QQuickItem, "barRuler")
    canvas = root.findChild(QQuickItem, "pianoCanvas")

    assert background_layer is not None
    assert background_image is not None
    assert background_overlay is not None
    assert ruler is not None
    assert canvas is not None
    for name in ("projectRail", "inspector", "miniMap"):
        assert root.findChild(QQuickItem, name) is not None
    assert background_layer.property("enabled") is False
    assert background_layer.property("ambientRunning") is True
    glow_source = root.findChild(QQuickItem, "editorBackgroundGlowSource")
    blur_effect = root.findChild(QQuickItem, "editorBackgroundBlur")
    assert glow_source is not None
    assert blur_effect is not None
    assert glow_source.property("opacity") <= 0.04
    assert background_layer.property("z") == ruler.property("z")
    assert background_layer.property("z") == canvas.property("z")
    # The ruler now lives in the nested workspace layout; compare its ancestor
    # at the background's level to verify the same paint-order guarantee.
    def ancestry(item):
        chain = []
        while item is not None:
            chain.insert(0, item)
            item = item.parentItem()
        return chain
    back_path, ruler_path = ancestry(background_layer), ancestry(ruler)
    split = next(i for i, pair in enumerate(zip(back_path, ruler_path)) if pair[0] != pair[1])
    siblings = back_path[split - 1].childItems()
    back_branch, ruler_branch = back_path[split], ruler_path[split]
    assert back_branch.z() < ruler_branch.z() or (
        back_branch.z() == ruler_branch.z()
        and siblings.index(back_branch) < siblings.index(ruler_branch)
    )
    source = background_image.property("source")
    source_text = source.toString() if isinstance(source, QUrl) else str(source)
    assert source_text.endswith("stage-lake.png")
    QTest.qWait(100)
    assert background_image.property("visible") is True
    assert blur_effect.property("visible") is True

    scene = engine.rootContext().contextProperty("scene")
    scene.setBackgroundBlur(0.0)
    scene.setDynamicEnabled(False)
    qapp.processEvents()
    assert blur_effect.property("visible") is False
    assert background_layer.property("ambientRunning") is False
    scene.setBackgroundBlur(0.8)
    scene.setDynamicEnabled(True)
    scene.setMotionLevel("low")
    qapp.processEvents()
    assert blur_effect.property("visible") is True
    assert background_layer.property("ambientRunning") is False

    root.close()


def test_recording_cursor_follows_with_follow_playback_disabled(qapp) -> None:
    clock = FakeKeyboardClock()
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
        keyboard_clock=clock,
    )
    timeline = root.findChild(object, "timeline")
    keyboard = engine.rootContext().contextProperty("keyboard")

    timeline.setProperty("followPlayback", False)
    timeline.setProperty("viewStartSeconds", 0.0)
    # Recording now overdubs at the transport cursor instead of appending.
    playback = engine.rootContext().contextProperty("playback")
    playback.seek(60.0)
    keyboard.startRecording()
    clock.advance(0.5)
    keyboard._tick()
    qapp.processEvents()

    assert keyboard.recordCursorSeconds > 60.0
    assert timeline.property("viewStartSeconds") == pytest.approx(
        timeline.property("maxFollowViewStartSeconds")
    )
    keyboard.stopRecording()
    root.close()


def test_minimap_exposes_the_same_current_playback_cursor_as_timeline(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(object, "timeline")
    mini_map = root.findChild(object, "miniMap")
    mini_map_playhead = root.findChild(object, "miniMapPlayhead")
    playback = engine.rootContext().contextProperty("playback")

    assert mini_map_playhead is not None
    assert mini_map.property("playheadSeconds") == pytest.approx(playback.positionSeconds)

    playback.seek(18.0)
    qapp.processEvents()
    assert timeline.property("cursorSeconds") == pytest.approx(18.0)
    assert mini_map.property("playheadSeconds") == pytest.approx(18.0)
    assert mini_map_playhead.property("visible") is True
    root.close()


def _invoke_timeline(timeline, method: str, value=None) -> None:
    if value is None:
        invoked = QMetaObject.invokeMethod(
            timeline,
            method,
            Qt.ConnectionType.DirectConnection,
        )
    else:
        invoked = QMetaObject.invokeMethod(
            timeline,
            method,
            Qt.ConnectionType.DirectConnection,
            Q_ARG("QVariant", value),
        )
    assert invoked is True


def test_timeline_scrub_updates_seek_and_restores_follow(qapp, qtlog) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    playback = engine.rootContext().contextProperty("playback")
    first_x = timeline.property("keyWidth") + 20.0
    second_x = first_x + 80.0

    _invoke_timeline(timeline, "beginScrub", first_x)
    first_position = playback.positionSeconds
    _invoke_timeline(timeline, "updateScrub", second_x)

    assert playback.positionSeconds > first_position
    assert timeline.property("scrubActive") is True
    assert timeline.property("followPlayback") is False

    _invoke_timeline(timeline, "finishScrub")

    assert timeline.property("scrubActive") is False
    assert timeline.property("followPlayback") is True
    assert timeline.property("edgeScrollRunning") is False
    assert not [
        record
        for record in qtlog.records
        if "ReferenceError" in record.message or "TypeError" in record.message
    ]
    root.close()


def test_scrub_edge_tick_scrolls_in_both_directions_and_stops_on_exit(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(QObject, "timeline")
    right_x = timeline.property("width") - 1.0

    _invoke_timeline(timeline, "beginScrub", right_x)
    _invoke_timeline(timeline, "tickEdgeScroll")
    right_scrolled = timeline.property("viewStartSeconds")

    assert right_scrolled > 0.0
    assert right_scrolled * timeline.property("pixelsPerSecond") > 36
    assert timeline.property("edgeScrollRunning") is True

    left_x = timeline.property("keyWidth") + 1.0
    _invoke_timeline(timeline, "updateScrub", left_x)
    _invoke_timeline(timeline, "tickEdgeScroll")

    assert timeline.property("viewStartSeconds") < right_scrolled
    assert (right_scrolled - timeline.property("viewStartSeconds")) * timeline.property("pixelsPerSecond") > 36
    assert timeline.property("edgeScrollRunning") is True

    middle_x = (timeline.property("keyWidth") + timeline.property("width")) / 2.0
    _invoke_timeline(timeline, "updateScrub", middle_x)

    assert timeline.property("edgeScrollRunning") is False
    _invoke_timeline(timeline, "finishScrub")
    root.close()


def test_scrub_edge_scroll_clamps_to_bounds_and_ignores_other_gestures(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(QObject, "timeline")
    playback = engine.rootContext().contextProperty("playback")

    timeline.setProperty("viewStartSeconds", 0.0)
    _invoke_timeline(timeline, "tickEdgeScroll")
    assert timeline.property("viewStartSeconds") == pytest.approx(0.0)
    assert timeline.property("edgeScrollRunning") is False

    max_start = timeline.property("maxViewStartSeconds")
    timeline.setProperty("viewStartSeconds", max_start)
    _invoke_timeline(timeline, "beginScrub", timeline.property("width") - 1.0)
    _invoke_timeline(timeline, "tickEdgeScroll")
    assert timeline.property("viewStartSeconds") == pytest.approx(max_start)
    assert timeline.property("edgeScrollRunning") is False

    _invoke_timeline(timeline, "finishScrub")
    timeline.setProperty("gestureFeedbackVisible", True)
    _invoke_timeline(timeline, "tickEdgeScroll")
    assert timeline.property("edgeScrollRunning") is False
    visible_seconds = float(timeline.property("visibleSeconds"))
    assert playback.positionSeconds - timeline.property("viewStartSeconds") == pytest.approx(
        visible_seconds * 0.4
    )
    root.close()


def test_ruler_pointer_drag_scrubs_continuously_without_qml_errors(qapp, qtlog) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    ruler = root.findChild(QQuickItem, "barRuler")
    playback = engine.rootContext().contextProperty("playback")
    first_x = timeline.property("keyWidth") + 60.0
    second_x = first_x + 120.0
    ruler_y = ruler.property("y")
    press_at = timeline.mapToScene(QPointF(first_x, ruler_y + 15.0)).toPoint()
    move_to = timeline.mapToScene(QPointF(second_x, ruler_y + 15.0)).toPoint()

    QTest.mousePress(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        press_at,
    )
    qapp.processEvents()
    first_position = playback.positionSeconds
    QTest.mouseMove(root, move_to, delay=10)
    qapp.processEvents()
    moved_position = playback.positionSeconds
    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        move_to,
    )
    qapp.processEvents()

    assert moved_position > first_position
    assert timeline.property("scrubActive") is False
    assert timeline.property("followPlayback") is True
    assert timeline.property("edgeScrollRunning") is False
    assert not [
        record
        for record in qtlog.records
        if "ReferenceError" in record.message or "TypeError" in record.message
    ]
    root.close()


def test_playhead_pointer_drag_updates_position_without_reference_error(qapp, qtlog) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    playhead = root.findChild(QQuickItem, "playhead")
    playback = engine.rootContext().contextProperty("playback")
    press_at = playhead.mapToScene(QPointF(1.0, 12.0)).toPoint()
    move_to = playhead.mapToScene(QPointF(121.0, 12.0)).toPoint()

    QTest.mousePress(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        press_at,
    )
    QTest.mouseMove(root, move_to, delay=10)
    qapp.processEvents()
    moved_position = playback.positionSeconds
    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        move_to,
    )
    qapp.processEvents()

    assert moved_position > 0.0
    assert timeline.property("scrubActive") is False
    assert timeline.property("followPlayback") is True
    assert not [
        record
        for record in qtlog.records
        if "playhead is not defined" in record.message
        or "ReferenceError" in record.message
        or "TypeError" in record.message
    ]
    root.close()


def test_timeline_exposes_draw_note_and_pitch_viewport_controls(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    assert root.findChild(object, "drawNoteArea") is not None
    assert root.findChild(object, "pitchViewport") is not None
    root.close()


def test_piano_roll_exposes_ctrl_multiselect_and_plain_drag_marquee(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert "property bool marqueeAdditive: false" in source
    assert "marqueeAdditive = (mouse.modifiers & Qt.ControlModifier) !== 0" in source
    assert "if (!marqueeActive && (Math.abs(dx) >= 6 || Math.abs(dy) >= 6))" in source
    assert "mouse.modifiers & Qt.ControlModifier" in source
    assert "marqueeAdditive" in source[source.index("editor.selectRect"):]
    root.close()


def test_dragging_one_marquee_selected_note_moves_the_full_selection(qapp) -> None:
    notes = (
        Note.create(id="first", pitch=60, start_time=1.0, duration=0.5),
        Note.create(id="second", pitch=64, start_time=2.0, duration=0.5),
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    controller.clearSelection()
    # The custom client title bar leaves a little less vertical Piano Roll
    # space, so keep the marquee around both notes rather than depending on a
    # viewport-boundary pixel.
    controller.selectRect(70, 250, 150, 300, False)
    qapp.processEvents()
    timeline = root.findChild(QQuickItem, "timeline")
    pending = [timeline]
    first_item = None
    while pending:
        item = pending.pop()
        if item.objectName() == "noteItem-first":
            first_item = item
            break
        pending.extend(item.childItems())
    assert first_item is not None
    assert controller.state.selected_ids == frozenset({"first", "second"})

    start = first_item.mapToScene(
        QPointF(first_item.width() / 2, first_item.height() / 2)
    ).toPoint()
    destination = start + QPoint(40, -18)

    QTest.mousePress(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    QTest.mouseMove(root, destination, delay=10)
    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        destination,
    )
    qapp.processEvents()

    moved = {note.id: note for note in controller.state.notes}
    assert moved["first"].start_time == pytest.approx(1.5)
    assert moved["second"].start_time == pytest.approx(2.5)
    assert moved["first"].pitch == 61
    assert moved["second"].pitch == 65
    root.close()


def test_dragging_an_unselected_note_survives_selection_action_rail_appearing(qapp) -> None:
    note = Note.create(id="target", pitch=60, start_time=1.0, duration=0.5)
    engine, root, controller = load_shell(qapp, notes=(note,))
    controller.clearSelection()
    qapp.processEvents()
    timeline = root.findChild(QQuickItem, "timeline")
    pending = [timeline]
    target = None
    while pending:
        item = pending.pop()
        if item.objectName() == "noteItem-target":
            target = item
            break
        pending.extend(item.childItems())
    assert target is not None

    start = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    destination = start + QPoint(40, -18)
    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    qapp.processEvents()
    QTest.mouseMove(root, destination, delay=10)
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, destination
    )
    qapp.processEvents()

    moved = next(item for item in controller.state.notes if item.id == "target")
    assert moved.start_time == pytest.approx(1.5)
    assert moved.pitch == 61
    root.close()


def test_editor_has_one_experience_with_beginner_first_inspector(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    timeline = root.findChild(QObject, "timeline")
    inspector = root.findChild(QObject, "inspector")

    assert experience.mode == "advanced"
    assert timeline.property("advancedMode") is True
    assert inspector.property("isAdvanced") is False
    assert root.findChild(QObject, "moreAdvancedAction") is None
    assert root.findChild(QObject, "beginnerActionBar").property("visible") is False
    assert root.findChild(QObject, "editorStarterGuide").property("visible") is False
    root.close()


def test_blank_click_clears_selection_before_next_click_draws_and_updates_inspector(
    qapp,
) -> None:
    existing = Note.create(id="existing", pitch=60, start_time=0.0, duration=0.5)
    engine, root, controller = load_shell(qapp, notes=(existing,))
    canvas = root.findChild(QQuickItem, "pianoCanvas")
    selection = root.findChild(QObject, "selectionSection")
    assert canvas is not None
    assert selection is not None
    assert controller.state.selected_ids == frozenset({"existing"})

    start = canvas.mapToScene(QPointF(72 + 240, 252 + 12)).toPoint()
    QTest.mousePress(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    qapp.processEvents()
    assert controller.state.selected_ids == frozenset({"existing"})

    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    qapp.processEvents()

    assert len(controller.state.active_notes) == 1
    assert controller.state.selected_ids == frozenset()

    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    qapp.processEvents()

    assert len(controller.state.active_notes) == 2
    assert controller.state.selected_ids != frozenset({"existing"})
    assert len(controller.state.selected_ids) == 1
    assert selection.property("badge") == "1"
    assert root.findChild(QObject, "selectedNotesTitle").property("text") == "当前选择"
    root.close()


def test_note_creation_and_piano_preview_keep_separate_default_lengths(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")
    inspector_source = (QML_DIR / "Inspector.qml").read_text(encoding="utf-8")
    canvas_pointer_source = timeline_source[
        timeline_source.index('id: canvasPointer'):timeline_source.index('Repeater {', timeline_source.index('id: canvasPointer'))
    ]

    assert 'id: pianoPreviewLengthField' in inspector_source
    assert 'experience.pianoPreviewLengthBeats' in inspector_source
    assert 'keyboard.setPreviewDefaults(experience.newNoteVelocity, pianoPreviewDuration)' in timeline_source
    assert 'editor.previewDrawNoteTo(' not in canvas_pointer_source
    root.close()


def test_one_beat_creation_is_independent_from_piano_preview_length(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    timeline = root.findChild(object, "timeline")

    experience.setNewNoteLengthBeats(1.0)
    experience.setPianoPreviewLengthBeats(2.0)
    qapp.processEvents()

    assert timeline.property("defaultNewNoteDuration") == pytest.approx(0.5)
    assert timeline.property("pianoPreviewDuration") == pytest.approx(1.0)
    note_id = controller.beginDrawNoteAt(80, 144, timeline.property("defaultNewNoteDuration"), 75)
    controller.commitGesture()
    created = next(note for note in controller.state.notes if note.id == note_id)
    assert created.duration / timeline.property("secondsPerBeat") == pytest.approx(1.0)
    root.close()


def test_keyboard_creation_exposes_live_feedback(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    assert root.findChild(object, "keyboardHint") is not None
    assert root.findChild(object, "activeKeyboardKeys") is not None
    root.close()


def test_project_rail_is_expanded_at_standard_desktop_width(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    rail = root.findChild(object, "projectRail")

    assert rail.property("width") >= 200
    root.close()


def test_project_rail_can_be_collapsed_by_the_creator(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    rail = root.findChild(object, "projectRail")
    toggle = root.findChild(object, "projectRailToggle")

    assert toggle is not None
    toggle.clicked.emit()
    qapp.processEvents()
    QTest.qWait(20)
    assert rail.property("width") <= 64
    root.close()


def test_editor_keeps_a_visible_return_to_library_entry(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    back_to_library = root.findChild(object, "backToLibraryButton")
    project_rail = root.findChild(object, "projectRail")

    assert back_to_library is not None
    assert project_rail is not None
    for width in (1280, 1366, 1920):
        root.setWidth(width)
        qapp.processEvents()
        QTest.qWait(20)
        assert back_to_library.property("visible") is True
    root.close()


def test_project_rail_compacts_before_toolbar_actions_are_clipped(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    rail = root.findChild(object, "projectRail")

    root.setWidth(1280)
    qapp.processEvents()
    QTest.qWait(20)
    assert rail.property("width") <= 64
    root.close()


def test_project_rail_cycles_expanded_and_icon_modes_only(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    rail = root.findChild(QQuickItem, "projectRail")
    toggle = root.findChild(QQuickItem, "projectRailToggle")

    assert rail is not None
    assert toggle is not None
    assert rail.property("displayMode") == "expanded"
    assert rail.property("width") >= 200

    toggle.clicked.emit()
    qapp.processEvents()
    assert rail.property("displayMode") == "icon"
    assert rail.property("width") <= 64
    assert root.findChild(object, "projectRailIconMode") is not None

    toggle.clicked.emit()
    qapp.processEvents()
    assert rail.property("displayMode") == "expanded"
    root.close()


def test_icon_rail_does_not_split_its_list_with_a_filler_item(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1280)
    qapp.processEvents()

    rail = root.findChild(QQuickItem, "projectRail")
    filler = root.findChild(QQuickItem, "projectRailIconMode")
    assert rail.property("effectiveDisplayMode") == "icon"
    assert filler.property("visible") is True
    assert filler.property("height") <= 1
    root.close()


def test_focus_mode_hides_the_starter_guide_without_dismissing_it(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    guide = root.findChild(QQuickItem, "editorStarterGuide")

    experience.setMode("beginner")
    experience.completeFirstRun()
    qapp.processEvents()
    assert guide.property("visible") is True

    experience.setFocusMode(True)
    qapp.processEvents()
    assert root.property("focusMode") is True
    assert guide.property("visible") is False

    experience.setFocusMode(False)
    qapp.processEvents()
    assert guide.property("visible") is True
    root.close()


def test_focus_mode_keeps_hidden_minimap_without_a_restore_affordance(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMiniMapVisible(False)
    qapp.processEvents()
    assert root.findChild(QQuickItem, "miniMapRestoreButton") is None

    experience.setFocusMode(True)
    qapp.processEvents()
    assert root.findChild(QQuickItem, "miniMapRestoreButton") is None

    experience.setFocusMode(False)
    qapp.processEvents()
    assert root.findChild(QQuickItem, "miniMapRestoreButton") is None
    root.close()


def test_legacy_project_rail_collapsed_bridge_still_controls_width(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1920)
    qapp.processEvents()
    rail = root.findChild(QQuickItem, "projectRail")

    rail.setProperty("collapsed", True)
    qapp.processEvents()
    assert rail.property("effectiveDisplayMode") == "icon"
    assert rail.property("width") <= 64

    rail.setProperty("collapsed", False)
    qapp.processEvents()
    assert rail.property("effectiveDisplayMode") == "expanded"
    assert rail.property("width") >= 200
    root.close()


def test_inspector_sections_can_toggle_from_the_keyboard(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    section = root.findChild(QQuickItem, "selectionSection")
    assert section.property("activeFocusOnTab") is True
    assert section.property("expanded") is True

    section.forceActiveFocus()
    assert section.property("activeFocus") is True
    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert section.property("expanded") is False

    QTest.keyClick(root, Qt.Key_Return)
    qapp.processEvents()
    assert section.property("expanded") is True
    root.close()


def test_inspector_has_two_panel_modes_and_compact_parameter_copy(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    engine.rootContext().contextProperty("experience").setMode("beginner")
    qapp.processEvents()
    inspector = root.findChild(QQuickItem, "inspector")
    collapse_button = root.findChild(QObject, "inspectorCollapseButton")

    assert inspector is not None
    assert collapse_button is not None
    assert inspector.property("panelMode") == "expanded"
    assert root.findChild(object, "inspectorModeSwitch") is not None
    for name in (
        "inspectorNarrowSummary", "inspectorPerformanceSection",
        "inspectorTimeSection", "inspectorHelpSection",
    ):
        assert root.findChild(object, name) is None

    start_time = root.findChild(QQuickItem, "startTimeField")
    duration = root.findChild(QQuickItem, "durationField")
    velocity = root.findChild(QQuickItem, "velocityField")
    assert start_time.property("label") == "从第几拍开始"
    assert duration.property("label") == "持续几拍"
    assert velocity.property("label") == "力度 · 音符强弱"

    expanded_width = inspector.property("width")
    collapse_button.clicked.emit()
    qapp.processEvents()
    assert inspector.property("panelMode") == "collapsed"
    assert inspector.property("width") <= 64
    collapse_button.clicked.emit()
    qapp.processEvents()
    assert inspector.property("panelMode") == "expanded"
    assert inspector.property("width") == pytest.approx(expanded_width)

    # A legacy caller that still assigns the removed mode is normalized too.
    inspector.setProperty("panelMode", "narrow")
    qapp.processEvents()
    assert inspector.property("panelMode") == "collapsed"
    assert inspector.property("width") <= 64
    root.close()


def test_inspector_exposes_composite_summary_and_atomic_overall_controls(qapp) -> None:
    notes = tuple(
        Note.create(
            id=note_id,
            pitch=60,
            start_time=index * 0.1,
            duration=0.2,
            velocity=70 + index,
        )
        for index, note_id in enumerate(("a", "b", "c"))
    )
    engine, root, controller = load_shell(qapp, notes)

    summary = root.findChild(QQuickItem, "compositeSelectionSummary")
    mode_switch = root.findChild(QObject, "compositeModeSwitch")
    velocity_up = root.findChild(QObject, "compositeVelocityUpButton")

    assert summary is not None
    assert summary.property("visible") is True
    assert mode_switch is not None
    assert velocity_up is not None

    assert mode_switch.property("currentIndex") == 0
    mode_switch.activated.emit(1)
    qapp.processEvents()
    assert root.findChild(QQuickItem, "compositeSegmentList").property("visible") is True
    mode_switch.activated.emit(0)
    qapp.processEvents()

    before_depth = controller.historyUndoDepth
    velocity_up.clicked.emit()
    qapp.processEvents()
    assert [note.velocity for note in controller.state.notes] == [80, 81, 82]
    assert controller.historyUndoDepth == before_depth + 1

    controller.undo()
    assert [note.velocity for note in controller.state.notes] == [70, 71, 72]
    root.close()


def test_inspector_release_controls_edit_selected_note_without_changing_duration(qapp) -> None:
    note = Note.create(
        id="release-note",
        pitch=60,
        start_time=0.0,
        duration=0.75,
        velocity=80,
    )
    engine, root, controller = load_shell(qapp, notes=(note,))

    release_control = root.findChild(QQuickItem, "releaseControl")
    release_mode = root.findChild(QObject, "releaseModeSwitch")
    release_ms = root.findChild(QObject, "releaseMsField")

    assert release_control is not None
    assert release_mode is not None
    assert release_ms is not None
    assert release_mode.property("currentIndex") == 0
    assert release_ms.property("visible") is False

    before_depth = controller.historyUndoDepth
    release_mode.activated.emit(1)
    qapp.processEvents()
    assert controller.state.notes[0].release_mode == "custom"
    assert controller.state.notes[0].release_ms == 250.0
    assert release_ms.property("visible") is True
    assert controller.state.notes[0].duration == 0.75
    assert controller.historyUndoDepth == before_depth + 1

    release_ms.setProperty("text", "420")
    release_ms.editingFinished.emit()
    qapp.processEvents()
    assert controller.state.notes[0].release_mode == "custom"
    assert controller.state.notes[0].release_ms == 420.0
    assert controller.state.notes[0].duration == 0.75
    assert controller.historyUndoDepth == before_depth + 2

    release_mode.activated.emit(2)
    qapp.processEvents()
    assert controller.state.notes[0].release_mode == "cut"
    assert controller.state.notes[0].release_ms is None
    assert release_ms.property("visible") is False

    controller.undo()
    assert controller.state.notes[0].release_mode == "custom"
    assert controller.state.notes[0].release_ms == 420.0
    root.close()


def test_selection_action_bar_exposes_only_selection_audition_and_guarded_shortcut(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    playback = engine.rootContext().contextProperty("playback")

    for name in ("auditionSelectionButton", "selectionAuditionStopButton", "selectionAuditionStopShortcut"):
        assert root.findChild(QObject, name) is not None
    for name in ("selectionLoopButton", "selectionSoloButton", "selectionContextButton"):
        assert root.findChild(QObject, name) is None

    source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    shortcut_start = source.index('sequence: "Shift+Space"')
    assert "activeTextInputFocused" in source[shortcut_start:]
    shortcut = root.findChild(QObject, "selectionAuditionShortcut")
    assert shortcut is not None
    assert shortcut.property("enabled") is True

    assert playback.selectionAudition.loopSelection is False
    assert playback.selectionAudition.soloSelection is False
    root.close()


def test_selection_audition_restarts_with_one_click_after_natural_completion(qapp) -> None:
    player = DeterministicPlaybackPlayer()
    engine, root, _controller = load_shell(qapp, player=player)
    playback = engine.rootContext().contextProperty("playback")
    button = _visual_item(root.contentItem(), "auditionSelectionButton")
    center = button.mapToScene(
        QPointF(button.width() / 2, button.height() / 2)
    ).toPoint()

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=center)
    qapp.processEvents()

    assert player.temporary_play_calls == 1
    assert playback.selectionAudition.playing is True

    player.finish_temporary()
    qapp.processEvents()
    assert playback.selectionAudition.playing is False
    button = _visual_item(root.contentItem(), "auditionSelectionButton")
    center = button.mapToScene(
        QPointF(button.width() / 2, button.height() / 2)
    ).toPoint()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=center)
    qapp.processEvents()

    assert player.temporary_play_calls == 2
    assert playback.selectionAudition.playing is True
    root.close()


def test_escape_stops_selection_audition_without_clearing_selection(qapp) -> None:
    notes = (
        Note.create(id="escape-a", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="escape-b", pitch=64, start_time=1.0, duration=0.5),
    )
    player = DeterministicPlaybackPlayer()
    engine, root, controller = load_shell(qapp, notes=notes, player=player)
    playback = engine.rootContext().contextProperty("playback")
    root.findChild(QQuickItem, "timeline").forceActiveFocus()

    playback.selectionAudition.playSelection()
    qapp.processEvents()
    assert playback.selectionAudition.playing is True
    selected_before = controller.state.selected_ids

    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()

    assert playback.selectionAudition.playing is False
    assert player.stop_temporary_calls >= 1
    assert controller.state.selected_ids == selected_before
    root.close()


def test_focus_mode_reclaims_sidebars_and_is_exposed_in_toolbar(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    rail = root.findChild(QQuickItem, "projectRail")
    inspector = root.findChild(QQuickItem, "inspector")
    timeline = root.findChild(QQuickItem, "timeline")
    focus_toggle = root.findChild(QObject, "editorFocusModeToggle")

    assert focus_toggle is not None
    assert root.findChild(object, "focusModeShortcut") is not None
    initial_width = timeline.property("width")
    assert QMetaObject.invokeMethod(
        focus_toggle, "activate", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()
    assert root.property("focusMode") is True
    assert rail.property("effectiveDisplayMode") == "icon"
    assert inspector.property("effectivePanelMode") == "collapsed"
    assert timeline.property("width") > initial_width

    assert QMetaObject.invokeMethod(
        focus_toggle, "activate", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()
    assert root.property("focusMode") is False

    # An external settings change must continue to drive the bound Main.qml
    # property after toolbar/shortcut toggles; imperative writes to
    # window.focusMode would silently break that binding.
    experience = engine.rootContext().contextProperty("experience")
    experience.setFocusMode(True)
    qapp.processEvents()
    assert root.property("focusMode") is True
    experience.setFocusMode(False)
    qapp.processEvents()
    assert root.property("focusMode") is False
    root.close()


def test_mini_map_cycles_visible_weak_and_hidden_modes(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    mini_map = root.findChild(QQuickItem, "miniMap")
    display_toggle = root.findChild(QQuickItem, "miniMapDisplayModeButton")
    experience = engine.rootContext().contextProperty("experience")

    assert mini_map is not None
    assert display_toggle is not None
    assert experience.miniMapDisplayMode == "visible"
    assert mini_map.property("displayMode") == "visible"

    display_toggle.clicked.emit()
    qapp.processEvents()
    assert experience.miniMapDisplayMode == "weak"
    assert mini_map.property("visible") is True
    assert mini_map.property("opacity") < 1.0

    display_toggle.clicked.emit()
    qapp.processEvents()
    assert experience.miniMapDisplayMode == "hidden"
    assert mini_map.property("visible") is False
    assert mini_map.property("enabled") is False
    assert mini_map.property("width") == 0
    assert mini_map.property("height") == 0

    display_toggle.clicked.emit()
    qapp.processEvents()
    assert experience.miniMapDisplayMode == "visible"
    assert mini_map.property("visible") is True
    root.close()


def test_inspector_mode_switch_selects_the_matching_layer(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    inspector = root.findChild(QQuickItem, "inspector")
    mode_switch = root.findChild(QQuickItem, "inspectorModeSwitch")
    selection = root.findChild(QQuickItem, "selectionSection")
    defaults = root.findChild(QQuickItem, "newNoteDefaultsSection")
    project = root.findChild(QQuickItem, "inspectorProjectSection")

    assert inspector is not None
    assert mode_switch is not None
    assert inspector.property("inspectorMode") == "selection"
    assert selection.property("visible") is True
    assert defaults.property("visible") is False
    assert project.property("visible") is False

    mode_switch.activated.emit(1)
    qapp.processEvents()
    assert inspector.property("inspectorMode") == "defaults"
    assert selection.property("visible") is False
    assert defaults.property("visible") is True
    assert project.property("visible") is False

    mode_switch.activated.emit(2)
    qapp.processEvents()
    assert inspector.property("inspectorMode") == "project"
    assert selection.property("visible") is False
    assert defaults.property("visible") is False
    assert project.property("visible") is True
    root.close()


def test_editor_layout_preferences_restore_all_density_states(qapp, tmp_path) -> None:
    settings_file = tmp_path / "editor-density.ini"
    engine, root, controller = load_shell(
        qapp, experience_settings_file=settings_file
    )
    experience = engine.rootContext().contextProperty("experience")
    experience.setSidebarMode("narrow")
    experience.setInspectorPanelMode("collapsed")
    experience.setFocusMode(True)
    experience.setMiniMapDisplayMode("weak")
    experience.setInspectorSectionExpanded("time", True)
    qapp.processEvents()
    root.close()
    engine.deleteLater()
    qapp.processEvents()

    engine, root, controller = load_shell(
        qapp,
        experience_settings_file=settings_file,
        clear_experience_settings=False,
    )
    experience = engine.rootContext().contextProperty("experience")
    assert experience.sidebarMode == "icon"
    assert experience.inspectorPanelMode == "collapsed"
    assert experience.focusMode is True
    assert experience.miniMapDisplayMode == "weak"
    assert experience.inspectorSectionExpanded("time", False) is True
    root.close()


def test_editor_top_bar_exposes_semantic_action_groups(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1920)
    qapp.processEvents()

    for name in (
        "projectIdentityGroup", "rhythmGroup", "actionGroup", "fileGroup",
        "projectTheoryGroupDivider", "editFileGroupDivider",
        "moreUndoAction", "moreRedoAction",
    ):
        assert root.findChild(object, name) is not None
    assert root.findChild(object, "commandBar").property("ultraCompact") is False
    root.close()


def test_core_actions_stay_in_the_first_viewport_at_supported_sizes(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    transport = root.findChild(object, "bottomTransportBar")

    for width, height in ((1280, 720), (1366, 768), (1920, 1080)):
        root.setWidth(width)
        root.setHeight(height)
        qapp.processEvents()
        QTest.qWait(20)
        assert root.findChild(object, "saveButton").property("visible") is True
        assert root.findChild(object, "transportPlayPauseButton").property("visible") is True
        assert transport.property("y") + transport.property("height") <= height
    root.close()


def test_beginner_shell_groups_actions_and_details(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    engine.rootContext().contextProperty("experience").setMode("beginner")
    qapp.processEvents()

    for name in (
        "projectIdentityGroup", "rhythmGroup", "actionGroup",
        # Background settings moved into the persistent More menu.
        "moreBackgroundSettingsAction", "projectSearchField", "advancedInspectorToggle",
        "followPlaybackToggle",
    ):
        assert root.findChild(object, name) is not None
    assert root.findChild(object, "bottomTransportBar") is not None
    # The current compact beginner shell keeps core rhythm editing reachable.
    assert root.findChild(object, "bpmField").isVisible() is True
    root.close()


def test_selection_actions_stay_visible_while_beginner_actions_can_collapse(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    engine.rootContext().contextProperty("experience").setMode("beginner")
    qapp.processEvents()

    beginner = root.findChild(object, "beginnerActionBar")
    beginner_toggle = root.findChild(object, "beginnerActionBarToggle")
    selection = root.findChild(object, "selectionActions")
    selection_toggle = root.findChild(object, "selectionActionsToggle")
    action_rail = root.findChild(object, "editorActionRail")
    collapsed_toggle = root.findChild(object, "editorActionRailCollapsedToggle")
    experience = engine.rootContext().contextProperty("experience")

    assert beginner is not None
    assert beginner_toggle is not None
    assert selection is not None
    assert selection_toggle is not None
    assert action_rail is not None
    assert collapsed_toggle is not None
    assert beginner.property("expanded") is True
    assert beginner.property("visible") is True
    assert selection.property("visible") is True

    controller.selectNotes([])
    qapp.processEvents()
    assert beginner.property("visible") is True
    assert selection.property("visible") is False

    beginner_toggle.clicked.emit()
    qapp.processEvents()

    assert beginner.property("expanded") is False
    assert beginner.property("collapsedHeight") == 0
    assert beginner.property("height") == pytest.approx(
        beginner.property("collapsedHeight")
    )
    assert action_rail.property("height") == pytest.approx(0)
    assert action_rail.property("clip") is False
    assert collapsed_toggle.property("visible") is True
    assert beginner_toggle.property("text") == "⌄"
    assert selection.property("visible") is False
    assert root.findChild(object, "beginnerActionBarBackground").property(
        "visible"
    ) is False
    assert experience.inspectorSectionExpanded("beginnerActions", True) is False

    controller.selectNotes(["demo"])
    qapp.processEvents()
    assert selection.property("visible") is True
    assert beginner.property("expanded") is False
    assert selection.property("visible") is True
    assert selection.property("expanded") is True
    assert selection.property("collapsedHeight") == 0
    assert selection.property("height") == pytest.approx(
        selection.property("expandedHeight")
    )
    assert action_rail.property("height") == pytest.approx(0)
    assert selection.parentItem() == root.findChild(object, "timeline")
    assert collapsed_toggle.property("visible") is False
    assert selection_toggle.property("visible") is False
    assert root.findChild(object, "selectionActionsBackground").property(
        "visible"
    ) is True

    controller.selectNotes([])
    qapp.processEvents()
    assert collapsed_toggle.property("visible") is True
    collapsed_toggle.clicked.emit()
    qapp.processEvents()
    assert beginner.property("expanded") is True
    root.close()


def test_editor_hides_empty_action_rail_in_advanced_mode(qapp) -> None:
    """Advanced mode without a selection must not leave a blank rail above the roll."""
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    action_rail = root.findChild(object, "editorActionRail")
    beginner = root.findChild(object, "beginnerActionBar")
    selection = root.findChild(object, "selectionActions")
    command_bar = root.findChild(object, "commandBar")
    ruler = root.findChild(object, "barRuler")

    experience.setMode("advanced")
    controller.selectNotes([])
    qapp.processEvents()

    assert beginner.property("visible") is False
    assert selection.property("visible") is False
    assert action_rail.property("visible") is False
    assert action_rail.property("height") == pytest.approx(0)
    command_bottom = command_bar.mapToScene(
        QPointF(0, command_bar.property("height"))
    ).y()
    ruler_top = ruler.mapToScene(QPointF(0, 0)).y()
    assert ruler_top == pytest.approx(command_bottom)

    controller.selectNotes(["demo"])
    qapp.processEvents()
    assert selection.property("visible") is True
    assert action_rail.property("visible") is False
    assert action_rail.property("height") == pytest.approx(0)
    assert selection.parentItem() == root.findChild(object, "timeline")
    assert selection.property("width") <= 520
    assert root.findChild(object, "arrangementToggle").property("visible") is True
    assert root.findChild(object, "assistantButton").property("visible") is True
    assert root.findChild(object, "saveButton").property("visible") is True
    assert ruler.mapToScene(QPointF(0, 0)).y() == pytest.approx(ruler_top)

    experience.setMode("beginner")
    controller.selectNotes([])
    qapp.processEvents()
    assert beginner.property("visible") is True
    assert selection.property("visible") is False
    assert action_rail.property("visible") is True
    assert action_rail.property("height") > 0
    root.close()


def test_editor_action_modules_collapse_without_residue_and_restore_from_settings(
    qapp, tmp_path
) -> None:
    settings_file = tmp_path / "experience.ini"
    engine, root, controller = load_shell(
        qapp,
        experience_settings_file=settings_file,
    )
    engine.rootContext().contextProperty("experience").setMode("beginner")
    qapp.processEvents()

    beginner = root.findChild(object, "beginnerActionBar")
    beginner_flow = root.findChild(object, "beginnerActionFlow")
    beginner_toggle = root.findChild(object, "beginnerActionBarToggle")
    selection = root.findChild(object, "selectionActions")
    selection_flow = root.findChild(object, "selectionActionFlow")
    selection_toggle = root.findChild(object, "selectionActionsToggle")
    assert all(item is not None for item in (
        beginner, beginner_flow, beginner_toggle,
        selection, selection_flow, selection_toggle,
    ))

    controller.selectNotes([])
    qapp.processEvents()
    assert beginner.property("visible") is True
    assert selection.property("visible") is False
    expanded_beginner_height = beginner.property("height")
    assert beginner_flow.property("visible") is True

    beginner_toggle.clicked.emit()
    qapp.processEvents()
    assert beginner.property("expanded") is False
    assert beginner_flow.property("visible") is False
    assert beginner_flow.property("height") == 0
    assert beginner.property("height") == pytest.approx(
        beginner.property("collapsedHeight")
    )
    assert beginner.property("collapsedHeight") == 0
    assert beginner.property("height") < expanded_beginner_height
    assert selection_flow.property("visible") is False

    controller.selectNotes(["demo"])
    qapp.processEvents()
    assert selection.property("visible") is True
    assert selection_flow.property("visible") is True
    assert selection.property("expanded") is True
    assert selection_toggle.property("visible") is False

    root.close()
    engine.deleteLater()
    qapp.processEvents()

    restored_engine, restored_root, _controller = load_shell(
        qapp,
        experience_settings_file=settings_file,
        clear_experience_settings=False,
    )
    restored_beginner = restored_root.findChild(object, "beginnerActionBar")
    restored_beginner_flow = restored_root.findChild(object, "beginnerActionFlow")
    restored_selection = restored_root.findChild(object, "selectionActions")
    restored_selection_flow = restored_root.findChild(object, "selectionActionFlow")
    assert restored_beginner.property("expanded") is False
    assert restored_beginner_flow.property("visible") is False
    assert restored_selection.property("visible") is True
    assert restored_selection.property("expanded") is True
    assert restored_selection_flow.property("visible") is True
    restored_root.close()


def test_beginner_quick_start_stays_on_one_compact_rail_when_narrow(qapp) -> None:
    """Quick actions must scroll horizontally instead of growing over the canvas."""
    engine, root, controller = load_shell(qapp)
    # Geometry is meaningful only while the beginner-only rail is active.
    engine.rootContext().contextProperty("experience").setMode("beginner")
    qapp.processEvents()
    beginner = root.findChild(object, "beginnerActionBar")
    action_flow = root.findChild(object, "beginnerActionFlow")
    assert beginner is not None and action_flow is not None

    root.setWidth(1000)
    root.setHeight(640)
    qapp.processEvents()
    QTest.qWait(20)

    assert action_flow.property("height") <= 36
    assert action_flow.property("clip") is True
    assert action_flow.property("width") >= 200
    assert action_flow.property("contentWidth") > action_flow.property("width")
    root.close()


def test_selection_actions_share_a_fixed_top_rail_without_moving_the_ruler(qapp) -> None:
    """Creating/selecting a note must replace the top rail, not insert a notch."""
    engine, root, controller = load_shell(qapp)
    action_rail = root.findChild(object, "editorActionRail")
    selection = root.findChild(object, "selectionActions")
    timeline = root.findChild(object, "timeline")
    ruler = root.findChild(object, "barRuler")

    assert action_rail is not None
    assert selection is not None
    assert timeline is not None
    assert ruler is not None
    rail_height = action_rail.property("height")
    assert rail_height == pytest.approx(action_rail.property("implicitHeight"))
    assert selection.property("y") >= ruler.property("height")
    assert ruler.property("y") == pytest.approx(0)

    note_id = controller.beginDrawNoteAt(
        120, 144, timeline.property("defaultNewNoteDuration"), 75
    )
    assert note_id
    controller.commitGesture()
    qapp.processEvents()
    QTest.qWait(20)

    assert selection.property("visible") is True
    assert action_rail.property("height") == pytest.approx(rail_height)
    assert ruler.property("y") == pytest.approx(0)
    assert timeline.property("height") > 0
    root.close()


def test_programmatic_note_creation_reopens_selection_actions(qapp) -> None:
    """Replacing one selected note with another must refresh the action context."""
    engine, root, controller = load_shell(qapp)
    selection = root.findChild(object, "selectionActions")
    selection_flow = root.findChild(object, "selectionActionFlow")
    selection_toggle = root.findChild(object, "selectionActionsToggle")
    action_rail = root.findChild(object, "editorActionRail")
    ruler = root.findChild(object, "barRuler")

    assert all(item is not None for item in (
        selection, selection_flow, selection_toggle, action_rail, ruler,
    ))
    selection.setProperty("expanded", False)
    qapp.processEvents()
    assert selection.property("expanded") is False
    assert selection_flow.property("visible") is False
    ruler_scene_y = ruler.mapToScene(QPointF(0, 0)).y()

    controller.selectNotes(["demo"])
    note_id = controller.beginDrawNoteAt(
        160, 162, 0.5, 75
    )
    assert note_id
    controller.commitGesture()
    qapp.processEvents()
    QTest.qWait(20)

    assert controller.selectedCount == 1
    assert selection.property("expanded") is True
    assert selection_flow.property("visible") is True
    assert selection_toggle.property("visible") is False
    assert action_rail.property("height") == pytest.approx(0)
    assert ruler.mapToScene(QPointF(0, 0)).y() == pytest.approx(ruler_scene_y)
    root.close()


def test_dense_raw_canvas_drag_moves_the_current_selection(qapp) -> None:
    """Large projects keep note dragging when delegates switch to Canvas."""
    selected = (
        Note.create(id="dense-target", pitch=60, start_time=1.0, duration=0.5),
        Note.create(id="dense-peer-a", pitch=64, start_time=2.0, duration=0.5),
        Note.create(id="dense-peer-b", pitch=67, start_time=3.0, duration=0.5),
    )
    fillers = tuple(
        Note.create(
            id=f"dense-filler-{index}",
            pitch=36 + index % 48,
            start_time=30.0 + index * 0.25,
            duration=0.1,
        )
        for index in range(600)
    )
    _engine, root, controller = load_shell(
        qapp,
        notes=selected + fillers,
        selected_ids=frozenset(note.id for note in selected),
    )
    qapp.processEvents()
    QTest.qWait(20)

    timeline = _visual_item(root.contentItem(), "timeline")
    canvas = _visual_item(timeline, "pianoCanvas")
    raw_canvas = _visual_item(canvas, "rawNoteCanvas")
    pointer = _visual_item(raw_canvas, "rawCanvasPointer")
    assert raw_canvas.property("visible") is True

    x = float(timeline.property("keyWidth")) + (
        1.0 - float(timeline.property("viewStartSeconds"))
    ) * float(timeline.property("pixelsPerSecond")) + 10
    y = (
        int(timeline.property("highestPitch")) - 60
    ) * float(timeline.property("rowHeight")) + 8
    start = raw_canvas.mapToScene(QPointF(x, y)).toPoint()
    destination = start + QPoint(40, -18)

    QTest.mouseMove(root, start)
    assert pointer.property("containsMouse") is True
    QTest.mousePress(root, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(root, destination, delay=10)
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, pos=destination)
    qapp.processEvents()

    moved = {note.id: note for note in controller.state.notes}
    assert moved["dense-target"].start_time == pytest.approx(1.5)
    assert moved["dense-peer-a"].start_time == pytest.approx(2.5)
    assert moved["dense-peer-b"].start_time == pytest.approx(3.5)
    assert moved["dense-target"].pitch == 61
    assert moved["dense-peer-a"].pitch == 65
    assert moved["dense-peer-b"].pitch == 68

    note_count = len(controller.state.notes)
    blank = raw_canvas.mapToScene(QPointF(min(raw_canvas.width() - 80, 700), 40)).toPoint()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=blank)
    qapp.processEvents()
    assert controller.selectedCount == 0
    assert len(controller.state.notes) == note_count
    root.close()


@pytest.mark.parametrize("mode", ["beginner", "advanced"])
def test_selection_changes_keep_piano_roll_and_note_scene_geometry_stable(
    qapp, mode,
) -> None:
    """Selecting, replacing, extending, and clearing selection must not move notes."""
    notes = (
        Note.create(id="first", pitch=60, start_time=1.2, duration=0.8),
        Note.create(id="second", pitch=64, start_time=2.6, duration=0.8),
    )
    engine, root, controller = load_shell(
        qapp, notes=notes, selected_ids=frozenset()
    )
    engine.rootContext().contextProperty("experience").setMode(mode)
    root.setWidth(1600)
    root.setHeight(900)
    qapp.processEvents()
    QTest.qWait(20)

    timeline = _visual_item(root.contentItem(), "timeline")
    first_note = _visual_item(root.contentItem(), "noteItem-first")
    second_note = _visual_item(root.contentItem(), "noteItem-second")
    first_body = _visual_item(root.contentItem(), "noteBody-first")
    second_body = _visual_item(root.contentItem(), "noteBody-second")
    action_rail = _visual_item(root.contentItem(), "editorActionRail")

    def scene_geometry(item: QQuickItem) -> tuple[float, float, float, float]:
        origin = item.mapToScene(QPointF(0, 0))
        return (origin.x(), origin.y(), item.width(), item.height())

    initial_timeline = scene_geometry(timeline)
    initial_first = scene_geometry(first_note)
    initial_second = scene_geometry(second_note)

    first_point = first_body.mapToScene(
        QPointF(first_body.width() / 2, first_body.height() / 2)
    ).toPoint()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=first_point)
    qapp.processEvents()

    assert controller.selectedCount == 1
    assert scene_geometry(timeline) == pytest.approx(initial_timeline)
    assert scene_geometry(first_note) == pytest.approx(initial_first)
    assert scene_geometry(second_note) == pytest.approx(initial_second)
    assert action_rail.height() == pytest.approx(
        0 if mode == "advanced" else action_rail.property("implicitHeight")
    )

    second_point = second_body.mapToScene(
        QPointF(second_body.width() / 2, second_body.height() / 2)
    ).toPoint()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=second_point)
    qapp.processEvents()
    assert controller.selectedCount == 1
    assert scene_geometry(timeline) == pytest.approx(initial_timeline)
    assert scene_geometry(second_note) == pytest.approx(initial_second)

    first_point = first_body.mapToScene(
        QPointF(first_body.width() / 2, first_body.height() / 2)
    ).toPoint()
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        first_point,
    )
    qapp.processEvents()
    assert controller.selectedCount == 2
    assert scene_geometry(timeline) == pytest.approx(initial_timeline)

    controller.clearSelection()
    qapp.processEvents()
    assert controller.selectedCount == 0
    assert scene_geometry(timeline) == pytest.approx(initial_timeline)
    assert scene_geometry(first_note) == pytest.approx(initial_first)
    assert scene_geometry(second_note) == pytest.approx(initial_second)
    root.close()


@pytest.mark.parametrize(
    "initial_selection",
    (frozenset(), frozenset({"marquee-a"})),
    ids=("empty-selection", "existing-selection"),
)
def test_marquee_selection_keeps_piano_roll_geometry_stable(
    qapp, initial_selection
) -> None:
    """Dragging must marquee-select whether or not a selection already exists."""
    notes = (
        Note.create(id="marquee-a", pitch=68, start_time=1.4, duration=0.7),
        Note.create(id="marquee-b", pitch=72, start_time=2.6, duration=0.7),
    )
    _engine, root, controller = load_shell(
        qapp, notes=notes, selected_ids=initial_selection
    )
    root.setWidth(1600)
    root.setHeight(900)
    qapp.processEvents()
    QTest.qWait(20)

    timeline = _visual_item(root.contentItem(), "timeline")
    canvas = _visual_item(root.contentItem(), "pianoCanvas")
    first = _visual_item(root.contentItem(), "noteItem-marquee-a")
    second = _visual_item(root.contentItem(), "noteItem-marquee-b")
    timeline_origin = timeline.mapToScene(QPointF(0, 0))
    timeline_geometry = (
        timeline_origin.x(), timeline_origin.y(), timeline.width(), timeline.height()
    )

    left = min(first.x(), second.x()) - 10
    top = min(first.y(), second.y()) - 10
    right = max(first.x() + first.width(), second.x() + second.width()) + 10
    bottom = max(first.y() + first.height(), second.y() + second.height()) + 10
    start = canvas.mapToScene(QPointF(left, top)).toPoint()
    finish = canvas.mapToScene(QPointF(right, bottom)).toPoint()

    QTest.mousePress(root, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(root, finish, delay=20)
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, pos=finish)
    qapp.processEvents()
    QTest.qWait(20)

    current_origin = timeline.mapToScene(QPointF(0, 0))
    current_geometry = (
        current_origin.x(), current_origin.y(), timeline.width(), timeline.height()
    )
    assert controller.state.selected_ids == frozenset({"marquee-a", "marquee-b"})
    assert current_geometry == pytest.approx(timeline_geometry)
    root.close()


def test_note_hit_testing_tracks_inspector_arrangement_and_window_resize(qapp) -> None:
    """Every layout change must route a click at the rendered note to that note."""
    note = Note.create(id="hit-target", pitch=72, start_time=1.5, duration=0.8)
    _engine, root, controller = load_shell(
        qapp, notes=(note,), selected_ids=frozenset()
    )
    root.setWidth(1600)
    root.setHeight(900)
    qapp.processEvents()
    QTest.qWait(20)

    shell = root.findChild(QObject, "workspaceShell")
    timeline = _visual_item(root.contentItem(), "timeline")

    def assert_visual_note_click_selects() -> None:
        controller.clearSelection()
        qapp.processEvents()
        body = _visual_item(root.contentItem(), "noteBody-hit-target")
        local_center = QPointF(body.width() / 2, body.height() / 2)
        scene_center = body.mapToScene(local_center)
        remapped = body.mapFromScene(scene_center)
        assert remapped.x() == pytest.approx(local_center.x())
        assert remapped.y() == pytest.approx(local_center.y())
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=scene_center.toPoint())
        qapp.processEvents()
        assert controller.state.selected_ids == frozenset({"hit-target"})

    assert_visual_note_click_selects()
    width_with_inspector = timeline.width()

    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, False),
    )
    qapp.processEvents()
    QTest.qWait(20)
    assert timeline.width() > width_with_inspector
    assert_visual_note_click_selects()

    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    qapp.processEvents()
    QTest.qWait(20)
    assert timeline.width() == pytest.approx(width_with_inspector)
    assert_visual_note_click_selects()

    height_without_arrangement = timeline.height()
    assert QMetaObject.invokeMethod(
        shell,
        "requestArrangement",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    qapp.processEvents()
    QTest.qWait(20)
    assert timeline.height() < height_without_arrangement
    assert_visual_note_click_selects()

    assert QMetaObject.invokeMethod(
        shell,
        "requestArrangement",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, False),
    )
    root.setWidth(1366)
    root.setHeight(768)
    qapp.processEvents()
    QTest.qWait(20)
    assert_visual_note_click_selects()

    root.setWidth(1920)
    root.setHeight(1080)
    qapp.processEvents()
    QTest.qWait(20)
    assert_visual_note_click_selects()
    root.close()


def test_every_real_selection_change_refreshes_and_shows_context_actions(qapp) -> None:
    """Non-empty selection replacements must not leave the action context stale."""
    notes = (
        Note.create(id="a", pitch=60, start_time=1.0, duration=0.5),
        Note.create(id="b", pitch=64, start_time=2.0, duration=0.5),
        Note.create(id="c", pitch=67, start_time=3.0, duration=0.5),
    )
    engine, root, controller = load_shell(
        qapp, notes=notes, selected_ids=frozenset({"a"})
    )
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    selection = root.findChild(object, "selectionActions")
    selection_toggle = root.findChild(object, "selectionActionsToggle")
    selection_play = root.findChild(object, "auditionSelectionButton")
    timeline = _visual_item(root.contentItem(), "timeline")
    timeline_geometry = (
        timeline.mapToScene(QPointF(0, 0)).x(),
        timeline.mapToScene(QPointF(0, 0)).y(),
        timeline.width(),
        timeline.height(),
    )

    assert selection is not None
    assert selection_toggle is not None
    assert selection_play is not None

    for note_ids in (["a", "b"], ["b", "c"]):
        selection.setProperty("expanded", False)
        controller.selectNotes(note_ids)
        qapp.processEvents()
        assert selection.property("expanded") is True
        assert selection_play.property("visible") is True

    selection.setProperty("expanded", False)
    controller.selectNote("a", True)
    qapp.processEvents()
    assert controller.selectedCount == 3
    assert selection.property("expanded") is True

    selection.setProperty("expanded", False)
    controller.selectNote("b", True)
    qapp.processEvents()
    assert controller.selectedCount == 2
    assert selection.property("expanded") is True

    selection.setProperty("expanded", False)
    controller.selectNotes(["c"])
    qapp.processEvents()

    assert selection.property("expanded") is True
    assert selection_play.property("visible") is True
    assert selection_play.property("tooltip") == "试听选中"
    assert (
        timeline.mapToScene(QPointF(0, 0)).x(),
        timeline.mapToScene(QPointF(0, 0)).y(),
        timeline.width(),
        timeline.height(),
    ) == pytest.approx(timeline_geometry)

    controller.selectNotes([])
    qapp.processEvents()
    assert selection.property("visible") is False
    root.close()


def test_selected_note_actions_use_compact_timeline_overlay(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    selection = root.findChild(object, "selectionActions")
    action_flow = root.findChild(object, "selectionActionFlow")

    qapp.processEvents()
    QTest.qWait(30)

    ruler = root.findChild(object, "barRuler")
    timeline = root.findChild(object, "timeline")
    assert selection.property("visible") is True
    assert selection.parentItem() == timeline
    assert selection.property("width") <= 520
    assert selection.property("height") <= 44
    assert selection.property("y") >= ruler.property("height")
    assert ruler.property("y") == pytest.approx(0)
    assert action_flow.property("height") == pytest.approx(
        action_flow.property("implicitHeight")
    )
    for button_name in (
        "selectionDuplicateButton", "selectionOctaveUpButton",
        "selectionOctaveDownButton", "selectionQuantizeButton",
        "selectionDeleteButton",
    ):
        button = root.findChild(object, button_name)
        assert button.property("y") + button.property("height") <= (
            action_flow.property("height")
        )
    root.close()


def test_selected_note_actions_stay_visible_without_moving_the_ruler(qapp) -> None:
    """Selected-note actions overlay the roll while primary commands remain."""
    engine, root, controller = load_shell(qapp)
    selection = root.findChild(object, "selectionActions")
    action_flow = root.findChild(object, "selectionActionFlow")
    close_button = root.findChild(object, "selectionActionsToggle")
    command_bar = root.findChild(object, "commandBar")
    primary_commands = root.findChild(object, "primaryCommandContent")
    workspace_commands = root.findChild(object, "actionGroup")
    file_group = root.findChild(object, "fileGroup")
    save_button = root.findChild(object, "saveButton")
    ruler = root.findChild(object, "barRuler")
    timeline = root.findChild(object, "timeline")

    assert selection is not None
    assert action_flow is not None
    assert close_button is not None
    assert ruler is not None
    assert close_button.property("visible") is False
    assert selection.property("visible") is True
    assert selection.property("y") >= ruler.property("height")
    selection_top = selection.mapToScene(QPointF(0, 0)).y()
    selection_bottom = selection.mapToScene(
        QPointF(0, selection.property("height"))
    ).y()
    command_bottom = command_bar.mapToScene(
        QPointF(0, command_bar.property("height"))
    ).y()
    ruler_bottom = ruler.mapToScene(QPointF(0, ruler.property("height"))).y()
    timeline_bottom = timeline.mapToScene(QPointF(0, timeline.property("height"))).y()
    assert selection_top >= ruler_bottom
    assert selection_bottom <= timeline_bottom
    assert ruler.mapToScene(QPointF(0, 0)).y() == pytest.approx(command_bottom)
    selection_right = selection.mapToScene(
        QPointF(selection.property("width"), 0)
    ).x()
    file_group_left = file_group.mapToScene(QPointF(0, 0)).x()
    assert selection_right <= file_group_left
    assert primary_commands.property("visible") is True
    assert workspace_commands.property("visible") is True
    assert save_button.property("visible") is True

    assert selection.property("visible") is True
    assert selection.property("expanded") is True
    assert ruler.property("y") == 0
    assert action_flow.property("visible") is True
    assert selection.property("collapsedHeight") == 0
    assert root.findChild(object, "editorActionRail").property("height") == 0
    assert primary_commands.property("visible") is True
    assert workspace_commands.property("visible") is True
    assert root.findChild(object, "editorActionRailCollapsedToggle").property(
        "visible"
    ) is False
    root.close()


def test_collapsed_action_rail_does_not_reserve_overlay_height(qapp) -> None:
    """The reopen affordance must not become a blank layout row."""
    engine, root, controller = load_shell(qapp)
    beginner_toggle = root.findChild(object, "beginnerActionBarToggle")
    action_rail = root.findChild(object, "editorActionRail")
    editor_tips = root.findChild(object, "editorTips")
    command_bar = root.findChild(object, "commandBar")
    ruler = root.findChild(object, "barRuler")

    assert all(item is not None for item in (
        beginner_toggle, action_rail, editor_tips,
        command_bar, ruler,
    ))
    engine.rootContext().contextProperty("experience").setMode("beginner")
    controller.selectNotes([])
    qapp.processEvents()
    beginner_toggle.clicked.emit()
    qapp.processEvents()

    command_bottom = command_bar.mapToScene(
        QPointF(0, command_bar.property("height"))
    ).y()
    ruler_top = ruler.mapToScene(QPointF(0, 0)).y()
    expected_top = command_bottom + (
        editor_tips.property("height") if editor_tips.property("visible") else 0
    ) + action_rail.property("height")
    assert ruler_top == pytest.approx(expected_top)
    assert action_rail.property("height") == pytest.approx(0)

    root.close()


def test_selected_note_actions_wrap_their_visible_controls(qapp) -> None:
    """The floating palette must not reserve a large inert area after More."""
    _engine, root, _controller = load_shell(qapp)
    selection = _visual_item(root.contentItem(), "selectionActions")
    more_button = _visual_item(root.contentItem(), "selectionMoreButton")

    qapp.processEvents()
    QTest.qWait(20)

    selection_right = selection.mapToScene(QPointF(selection.width(), 0)).x()
    more_right = more_button.mapToScene(QPointF(more_button.width(), 0)).x()
    assert selection_right - more_right <= 12
    root.close()


def test_plain_blank_click_clears_existing_selection_without_drawing(qapp) -> None:
    """A blank click dismisses selection; a drag can still start a marquee."""
    note = Note.create(id="selected", pitch=60, start_time=1.0, duration=0.5)
    _engine, root, controller = load_shell(
        qapp, notes=(note,), selected_ids=frozenset({"selected"})
    )
    root.setWidth(1366)
    root.setHeight(768)
    qapp.processEvents()
    QTest.qWait(20)

    canvas = _visual_item(root.contentItem(), "pianoCanvas")
    selection = _visual_item(root.contentItem(), "selectionActions")
    note_count = len(controller.state.notes)
    blank_point = canvas.mapToScene(
        QPointF(min(canvas.width() - 80, 760), min(canvas.height() - 80, 260))
    ).toPoint()

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=blank_point)
    qapp.processEvents()

    assert controller.selectedCount == 0
    assert len(controller.state.notes) == note_count
    assert selection.property("visible") is False
    root.close()


def test_collapsed_action_rail_toggle_sits_before_timeline_zero_marker(qapp) -> None:
    """The reopen affordance belongs before 0 seconds, not over the inspector."""
    engine, root, controller = load_shell(qapp)
    beginner_toggle = root.findChild(object, "beginnerActionBarToggle")
    collapsed_toggle = root.findChild(object, "editorActionRailCollapsedToggle")
    timeline = root.findChild(object, "timeline")

    assert all(item is not None for item in (
        beginner_toggle, collapsed_toggle, timeline,
    ))
    engine.rootContext().contextProperty("experience").setMode("beginner")
    controller.selectNotes([])
    qapp.processEvents()
    beginner_toggle.clicked.emit()
    qapp.processEvents()
    assert collapsed_toggle.property("visible") is True

    toggle_left = collapsed_toggle.mapToScene(QPointF(0, 0)).x()
    toggle_right = collapsed_toggle.mapToScene(
        QPointF(collapsed_toggle.property("width"), 0)
    ).x()
    zero_marker_x = timeline.mapToScene(
        QPointF(timeline.property("keyWidth"), 0)
    ).x()
    timeline_left = timeline.mapToScene(QPointF(0, 0)).x()

    assert toggle_left >= timeline_left
    assert toggle_right < zero_marker_x
    root.close()


def test_scroll_surfaces_keep_content_clear_of_vertical_scrollbars() -> None:
    """Every scroll surface must use the shared safe gutter contract."""
    theme_source = (QML_DIR / "Theme.qml").read_text(encoding="utf-8")
    safe_scrollbar = QML_DIR / "SafeScrollBar.qml"
    assert safe_scrollbar.exists()
    assert "scrollbarGutter" in theme_source

    for filename in (
        "AssistantWorkspace.qml",
        "AudioImportDialog.qml",
        "HarmonyHelpViewer.qml",
        "Inspector.qml",
        "Main.qml",
        "SupportDialog.qml",
        "TechnicalDetailsDialog.qml",
        "WelcomeLibraryView.qml",
    ):
        source = (QML_DIR / filename).read_text(encoding="utf-8")
        assert "SafeScrollBar" in source, filename
        assert "scrollbarGutter" in source, filename


def test_transport_has_one_play_pause_toggle_in_the_bottom_bar(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    toggle = root.findChild(object, "transportPlayPauseButton")

    assert toggle is not None
    assert root.findChild(object, "playButton") is None
    assert root.findChild(object, "pauseButton") is None
    assert toggle.property("text") == "播放"
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    transport_source = (QML_DIR / "TransportBar.qml").read_text(encoding="utf-8")
    assert "legacyTransportGroup" not in main_source
    assert 'text: transport.playing ? "暂停" : "播放"' in transport_source
    assert 'semanticKind: "record"' in transport_source
    assert "if (playback.playing)" in main_source
    assert "playback.pause()" in main_source
    assert main_source.count("keyboard.toggleRecording()") == 1
    root.close()


def test_record_button_keeps_record_semantics_without_corner_indicator(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    record_button = root.findChild(object, "recordButton")
    indicator = root.findChild(object, "semanticIndicator")

    assert record_button is not None
    assert record_button.property("semanticKind") == "record"
    assert indicator is not None
    assert indicator.property("visible") is False
    root.close()


def test_export_has_one_top_level_menu_and_no_bottom_duplicate(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    transport_source = (QML_DIR / "TransportBar.qml").read_text(encoding="utf-8")
    top_bar_source = (QML_DIR / "EditorTopBar.qml").read_text(encoding="utf-8")

    assert root.findChild(object, "moreImportMidiAction") is None
    assert root.findChild(object, "moreExportMidiAction") is None
    assert root.findChild(object, "exportAudioAction") is not None
    assert root.findChild(object, "exportMidiAction") is not None
    assert root.findChild(object, "exportAudioButton") is None
    assert root.findChild(object, "transportMoreExportAudioAction") is None
    assert 'sequence: "Space"' in main_source
    assert "enabled: window.editorInputEnabled" in main_source
    assert "audioExportRequested" not in transport_source
    assert "exportAudioRequested" not in transport_source
    assert 'signal exportAudioRequested()' in top_bar_source
    assert 'actionCallback: function() { window.openExportFolder(path) }' in main_source
    assert "function exportedFileName(path)" in main_source
    assert "function openExportFolder(path)" in main_source
    assert root.exportedFileName(r"C:\\Exports\\song.mid") == "song.mid"
    assert root.exportedFileName("file:///tmp/song.wav") == "song.wav"
    root.close()


def test_ordinary_save_uses_internal_name_surface_and_export_stays_native(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    controller.setBpm(96)

    assert QMetaObject.invokeMethod(
        root, "requestExplicitSave", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()

    name_dialog = root.findChild(QObject, "projectNameDialog")
    export_dialog = root.findChild(QObject, "exportProjectDialog")
    assert name_dialog is not None
    assert export_dialog is not None
    assert name_dialog.property("opened") is True
    assert export_dialog.property("visible") is False
    assert controller.currentFilePath == ""

    assert QMetaObject.invokeMethod(
        name_dialog, "reject", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()
    assert name_dialog.property("opened") is False
    root.close()


def test_internal_name_surface_saves_a_named_library_project(qapp, qtbot) -> None:
    engine, root, controller = load_shell(
        qapp, player=DeterministicPlaybackPlayer()
    )
    controller.setBpm(96)
    assert QMetaObject.invokeMethod(
        root, "requestExplicitSave", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()

    name_dialog = root.findChild(QObject, "projectNameDialog")
    name_field = root.findChild(QObject, "projectNameField")
    confirm = root.findChild(QObject, "projectNameConfirmButton")
    assert name_dialog is not None and name_field is not None and confirm is not None
    name_field.setProperty("text", "夜曲练习")
    qapp.processEvents()
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        confirm.clicked.emit()

    assert controller.currentProjectIsLibrary is True
    assert controller.currentFileName == "夜曲练习"
    assert name_dialog.property("opened") is False
    root.close()


def test_save_copy_failure_offers_a_copy_retry_surface(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    assert QMetaObject.invokeMethod(
        root, "requestSaveCopy", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()
    name_dialog = root.findChild(QObject, "projectNameDialog")
    feedback = root.findChild(QObject, "feedbackHost")
    assert name_dialog is not None and feedback is not None
    assert name_dialog.property("opened") is True
    assert QMetaObject.invokeMethod(
        name_dialog, "reject", Qt.ConnectionType.DirectConnection
    ) is True

    controller.saveFailed.emit("disk unavailable")
    qapp.processEvents()
    assert feedback.property("actionLabel") == "重试"
    assert QMetaObject.invokeMethod(
        feedback, "triggerAction", Qt.ConnectionType.DirectConnection
    ) is True
    qapp.processEvents()
    assert name_dialog.property("opened") is True
    assert root.property("pendingNameOperation") == "copy"
    name_dialog.close()
    root.close()


def test_export_menu_exposes_project_file_as_a_separate_action(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    action = root.findChild(QObject, "exportProjectAction")
    assert action is not None
    assert action.property("text") == "导出工程文件"
    root.close()


def test_import_export_menu_has_distinct_import_and_export_actions(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    import_menu = root.findChild(QObject, "importMenu")
    export_menu = root.findChild(QObject, "exportMenu")
    import_button = root.findChild(QObject, "importButton")
    export_button = root.findChild(QObject, "exportButton")

    assert import_menu is not None
    assert export_menu is not None
    assert import_button is not None
    assert export_button is not None
    assert import_button.property("text") == "导入"
    assert export_button.property("text") == "导出"
    for name, label in (
        ("importAudioAction", "导入音频"),
        ("openExternalProjectAction", "导入工程文件"),
        ("importMidiAction", "导入 MIDI"),
        ("exportProjectAction", "导出工程文件"),
        ("exportAudioAction", "导出音频"),
        ("exportMidiAction", "导出 MIDI"),
    ):
        action = root.findChild(QObject, name)
        assert action is not None
        assert action.property("text") == label
    assert import_menu.findChild(QObject, "importSectionLabel") is None
    assert import_menu.findChild(QObject, "importMidiAction") is not None
    assert export_menu.findChild(QObject, "exportSectionLabel") is None
    assert export_menu.findChild(QObject, "exportMidiAction") is not None
    assert root.findChild(QObject, "moreImportMidiAction") is None
    assert root.findChild(QObject, "moreExportMidiAction") is None
    assert root.findChild(QObject, "importExportMenu") is None
    assert root.findChild(QObject, "exportMidiButton") is None
    assert root.findChild(QObject, "moreImportAudioAction") is None
    root.close()


def test_audio_import_action_exposes_audio_only_filters(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    export_dialog = root.findChild(QObject, "audioExportFileDialog")
    import_dialog = root.findChild(QObject, "audioImportFileDialog")

    assert export_dialog is not None
    assert import_dialog is not None
    assert export_dialog.property("nameFilters") == ["WAV 音频 (*.wav)"]
    assert import_dialog.property("title") == "导入音频"
    assert import_dialog.property("nameFilters") == [
        "WAV 音频 (*.wav)",
        "MP3 音频 (*.mp3)",
    ]
    root.close()


def test_midi_import_action_uses_a_dedicated_picker(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    import_dialog = root.findChild(QObject, "midiImportFileDialog")

    assert import_dialog is not None
    assert import_dialog.property("title") == "导入 MIDI 文件"
    assert import_dialog.property("nameFilters") == ["MIDI 文件 (*.mid *.midi)"]
    assert "function routeImportedFile(url)" not in main_source
    assert "onMidiImportRequested: midiImportFileDialog.open()" in main_source
    root.close()


def test_midi_import_picker_and_confirmation_surface_use_midi_route(qapp, tmp_path) -> None:
    target = tmp_path / "menu-import.mid"
    target.write_bytes(
        encode_midi(
            (Note.create(id="source", pitch=60, start_time=0.0, duration=0.5),),
            TempoMap(bpm=90),
        )
    )
    engine, root, _controller = load_shell(qapp)
    file_dialog = root.findChild(QObject, "midiImportFileDialog")
    assert file_dialog is not None
    assert file_dialog.property("nameFilters") == ["MIDI 文件 (*.mid *.midi)"]

    assert QMetaObject.invokeMethod(
        root,
        "openMidiImportDialog",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", QUrl.fromLocalFile(str(target))),
    ) is True
    qapp.processEvents()

    dialog = root.findChild(QObject, "midiImportDialog")
    assert dialog is not None
    assert dialog.property("opened") is True
    assert dialog.findChild(QObject, "midiAppendButton") is not None
    assert dialog.findChild(QObject, "midiReplaceButton") is not None
    assert dialog.findChild(QObject, "midiImportConfirmButton") is not None
    dialog.close()
    root.close()


def test_export_menu_aligns_to_the_visible_export_button(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    export_button = root.findChild(QQuickItem, "exportButton")
    export_menu = root.findChild(QObject, "exportMenu")

    assert export_button is not None
    assert export_menu is not None
    assert QMetaObject.invokeMethod(
        export_menu,
        "open",
        Qt.ConnectionType.DirectConnection,
    ) is True
    qapp.processEvents()

    button_x = export_button.mapToItem(root.contentItem(), QPointF()).x()
    button_right = button_x + float(export_button.width())
    menu_x = float(export_menu.property("x"))
    menu_width = float(export_menu.property("width"))
    menu_parent = export_menu.parent()
    assert isinstance(menu_parent, QQuickItem)
    menu_parent_x = menu_parent.mapToItem(root.contentItem(), QPointF()).x()
    assert menu_parent_x + menu_x + menu_width == pytest.approx(button_right, abs=2.0)
    assert QMetaObject.invokeMethod(
        export_menu,
        "close",
        Qt.ConnectionType.DirectConnection,
    ) is True
    root.close()


def test_top_bar_menus_keep_dark_text_contrast_when_hovered_or_disabled(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    theme = root.property("appTheme")

    for name in (
        "importAudioAction",
        "openExternalProjectAction",
        "importMidiAction",
        "exportAudioAction",
        "exportMidiAction",
        "moreOpenAction",
        "moreBackgroundSettingsAction",
        "moreHarmonyAction",
    ):
        action = root.findChild(QObject, name)
        assert action is not None
        assert action.property("textColor") == theme.property("textPrimary")
        assert action.property("hoverBackgroundColor") == theme.property("surfaceHover")

    root.close()


def test_wide_advanced_toolbar_keeps_save_action_inside_the_window(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    root.setWidth(1920)
    root.setHeight(1080)
    qapp.processEvents()
    QTest.qWait(40)

    top_bar = root.findChild(QQuickItem, "commandBar")
    save_button = root.findChild(QQuickItem, "saveButton")
    assert top_bar is not None
    assert save_button is not None
    top_bar_right = top_bar.mapToItem(root.contentItem(), QPointF()).x() + top_bar.width()
    save_right = save_button.mapToItem(root.contentItem(), QPointF()).x() + save_button.width()
    assert save_right <= top_bar_right + 1.0
    root.close()


def test_follow_button_stays_bound_after_external_follow_state_changes(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    timeline = root.findChild(QQuickItem, "timeline")
    follow_toggle = root.findChild(QQuickItem, "followPlaybackToggle")

    assert timeline is not None
    assert follow_toggle is not None
    assert QMetaObject.invokeMethod(
        follow_toggle,
        "activate",
        Qt.ConnectionType.DirectConnection,
    ) is True
    qapp.processEvents()
    assert timeline.property("followPlayback") is False
    assert QMetaObject.invokeMethod(
        follow_toggle,
        "activate",
        Qt.ConnectionType.DirectConnection,
    ) is True
    qapp.processEvents()
    assert timeline.property("followPlayback") is True
    timeline.setProperty("followPlayback", False)
    qapp.processEvents()

    assert timeline.property("followPlayback") is False
    assert follow_toggle.property("checked") is False
    root.close()


def test_playback_follow_coalesces_rapid_position_updates(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
        player=DeterministicPlaybackPlayer(),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    playback = engine.rootContext().contextProperty("playback")
    assert timeline is not None

    visible_seconds = float(timeline.property("visibleSeconds"))
    first_position = visible_seconds * 0.4 + 1.0
    second_position = first_position + 0.5
    playback.seek(first_position)
    _invoke_timeline(timeline, "setFollowPlayback", True)
    qapp.processEvents()
    first_view_start = float(timeline.property("viewStartSeconds"))

    playback.play()
    qapp.processEvents()
    playback.seek(second_position)
    qapp.processEvents()
    second_view_start = float(timeline.property("viewStartSeconds"))

    # Audio transport reports at 10 ms intervals.  Following every report
    # repeatedly re-filters and re-lays out the full timeline, which starves
    # real-time scheduling on a large window.  The view should wait for its
    # display-rate follow tick instead of moving synchronously here.
    assert second_view_start == pytest.approx(first_view_start)
    # Allow one full display tick plus Windows timer granularity; the follow
    # implementation intentionally coalesces updates instead of restarting
    # this one-shot timer on every 10 ms transport notification.
    QTest.qWait(40)
    followed_view_start = float(timeline.property("viewStartSeconds"))
    assert followed_view_start > first_view_start
    assert second_position - followed_view_start == pytest.approx(visible_seconds * 0.4)
    playback.stop()
    root.close()


def test_timeline_reuses_an_overscanned_model_viewport_for_small_pans(qapp) -> None:
    notes = tuple(
        Note.create(id=f"scroll-{second}", pitch=60, start_time=float(second), duration=0.5)
        for second in range(60)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline is not None

    first_raw_viewport = controller.noteModel._viewport
    first_smart_viewport = controller.compositeModel._viewport
    assert first_raw_viewport is not None
    assert first_smart_viewport is not None

    timeline.setProperty("viewStartSeconds", 0.25)
    qapp.processEvents()

    assert controller.noteModel._viewport == first_raw_viewport
    assert controller.compositeModel._viewport == first_smart_viewport

    timeline.setProperty("viewStartSeconds", first_raw_viewport[1] + 0.25)
    qapp.processEvents()

    assert controller.noteModel._viewport != first_raw_viewport
    assert controller.compositeModel._viewport != first_smart_viewport
    root.close()


def test_playback_follow_keeps_playhead_on_the_reading_anchor_between_view_ticks(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
        player=DeterministicPlaybackPlayer(),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    playhead = root.findChild(QQuickItem, "playhead")
    playback = engine.rootContext().contextProperty("playback")
    assert timeline is not None
    assert playhead is not None

    playback.seek(10.0)
    _invoke_timeline(timeline, "setFollowPlayback", True)
    playback.play()
    qapp.processEvents()

    anchor_x = float(timeline.property("keyWidth")) + float(
        timeline.property("visibleSeconds")
    ) * 0.4 * float(timeline.property("pixelsPerSecond"))
    assert float(playhead.property("x")) == pytest.approx(anchor_x)

    playback.seek(10.5)
    qapp.processEvents()

    # The view start is intentionally updated on the next display tick. The
    # playhead must not jump with the raw cursor during that interval.
    assert float(playhead.property("x")) == pytest.approx(anchor_x)
    root.close()


def test_playback_follow_keeps_playhead_anchored_near_project_end(qapp) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
        player=DeterministicPlaybackPlayer(),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    playhead = root.findChild(QQuickItem, "playhead")
    playback = engine.rootContext().contextProperty("playback")
    assert timeline is not None
    assert playhead is not None

    playback.play()
    playback.seek(60.0)
    QTest.qWait(50)

    visible_seconds = float(timeline.property("visibleSeconds"))
    view_start = float(timeline.property("viewStartSeconds"))
    assert playback.positionSeconds - view_start == pytest.approx(visible_seconds * 0.4)
    assert playhead.property("x") < timeline.property("width") - 1.0
    playback.stop()
    root.close()


@pytest.mark.parametrize("pending_follow", [False, True])
def test_pause_keeps_playback_viewport_near_project_end(qapp, pending_follow) -> None:
    engine, root, _controller = load_shell(
        qapp,
        notes=(Note.create(id="long", pitch=60, start_time=0.0, duration=60.0),),
        player=DeterministicPlaybackPlayer(),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    playhead = root.findChild(QQuickItem, "playhead")
    playback = engine.rootContext().contextProperty("playback")
    assert timeline is not None and playhead is not None
    try:
        playback.play()
        playback.seek(58.0)
        QTest.qWait(40)
        anchor_x = float(playhead.property("x"))
        if pending_follow:
            # Pause between the transport update and its display-rate follow tick.
            playback.seek(58.1)
        playback.pause()
        QTest.qWait(40)
        expected_start = playback.positionSeconds - float(timeline.property("visibleSeconds")) * 0.4
        assert expected_start > float(timeline.property("maxViewStartSeconds"))
        assert timeline.property("viewStartSeconds") == pytest.approx(expected_start)
        assert playhead.property("x") == pytest.approx(anchor_x)
        paused_start = float(timeline.property("viewStartSeconds"))
        playback.play()
        QTest.qWait(40)
        assert timeline.property("viewStartSeconds") == pytest.approx(paused_start)
        assert playhead.property("x") == pytest.approx(anchor_x)
    finally:
        playback.stop()
        root.close()


def test_project_switch_controls_are_disabled_while_saving(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    rail_source = (QML_DIR / "ProjectRail.qml").read_text(encoding="utf-8")
    top_bar_source = (QML_DIR / "EditorTopBar.qml").read_text(encoding="utf-8")
    rail = root.findChild(QObject, "projectRail")

    assert "function requestNewProject(focusReturnTarget) {\n        if (editor.isSaving)" in main_source
    assert "if (editor.recycleInProgress)" in main_source
    assert "readonly property bool blocked: isSaving || recycleInProgress" in rail_source
    assert "enabled: !editor.isSaving" in top_bar_source
    assert "editor.openRecent(" not in main_source
    assert 'editor.openProject(selectedFile.toString())' in main_source

    for blocking_property in ("isSaving", "recycleInProgress"):
        rail.setProperty(blocking_property, True)
        qapp.processEvents()
        for name in (
            "backToLibraryButton", "libraryNewProjectButton", "projectRailOpenButton",
        ):
            assert root.findChild(QObject, name).property("enabled") is False
        rail.setProperty(blocking_property, False)
    root.close()


def test_inspector_explicit_precision_toggle_preserves_music(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    inspector = root.findChild(object, "inspector")
    experience = engine.rootContext().contextProperty("experience")

    assert inspector.property("experienceMode") == "advanced"
    assert inspector.property("isAdvanced") is False
    before = controller.state
    root.findChild(QObject, "advancedInspectorToggle").clicked.emit()
    qapp.processEvents()
    assert inspector.property("isAdvanced") is True
    assert controller.state == before
    root.findChild(QObject, "advancedInspectorToggle").clicked.emit()
    qapp.processEvents()
    assert inspector.property("isAdvanced") is False
    assert controller.state == before
    root.close()


def test_project_card_menu_exposes_all_safe_library_actions(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    for name in ("renameProjectDialog", "deleteProjectDialog"):
        assert root.findChild(object, name) is not None
    assert root.findChild(object, "projectDeleteConfirmAction") is not None
    source = (QML_DIR / "ProjectRail.qml").read_text(encoding="utf-8")
    assert 'objectName: "projectCardMenu"' in source
    assert 'text: "移动到回收站"' in source
    assert hasattr(controller, "copyProject")
    assert hasattr(controller, "exportProjectMidi")
    root.close()


def test_project_card_menu_has_readable_dark_hover_colours(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    theme = root.property("appTheme")
    project_rail = root.findChild(QObject, "projectRail")
    open_action = root.findChild(QObject, "projectMenuOpenAction")
    delete_action = root.findChild(QObject, "projectDeleteAction")

    project_rail.setProperty("menuProject", {"path": "demo.pianoai.json"})
    qapp.processEvents()

    assert open_action.property("textColor") == theme.property("textPrimary")
    assert open_action.property("hoverBackgroundColor") == theme.property("surfaceHover")
    assert delete_action.property("textColor") == theme.property("danger")
    assert delete_action.property("hoverBackgroundColor") == theme.property("surfaceHover")
    root.close()


def test_welcome_exposes_home_creation_choices(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    for name in (
        "homePrimaryCreate",
        "homeAiCreate",
        "homeOpenProject",
        "homeImportMusic",
        "homeBrowseExamples",
    ):
        assert root.findChild(object, name) is not None
    root.close()


def test_welcome_uses_the_png_bitmap_fallback_when_webp_loading_fails(qapp, qtbot) -> None:
    engine, root, controller = load_shell(qapp)
    backdrop = root.findChild(QObject, "cosmicKeyBackdrop")
    launch_image = backdrop.findChild(QObject, "launchBackdropImage")
    assert launch_image is not None

    backdrop.setProperty(
        "backgroundSource", QUrl.fromLocalFile(str(QML_DIR / "assets" / "missing.webp"))
    )

    qtbot.waitUntil(lambda: backdrop.property("triedPngFallback") is True, timeout=2000)
    qtbot.waitUntil(lambda: launch_image.property("imageReady") is True, timeout=2000)
    assert backdrop.property("fallbackActive") is False
    root.close()


def test_editor_does_not_load_the_obsolete_emotion_scene(qapp) -> None:
    engine, root, controller = load_shell(qapp)

    assert root.findChild(object, "emotionScene") is None
    assert root.findChild(object, "editorProtectionLayer") is not None
    root.close()


def test_shell_exposes_only_effective_launch_appearance_controls(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    source = (QML_DIR / "BackgroundSettingsDialog.qml").read_text(encoding="utf-8")

    for name in (
        "backgroundSettingsDialog", "welcomeThemePicker",
        "backgroundEnabledToggle", "backgroundMotionPicker",
    ):
        assert root.findChild(object, name) is not None
    for removed_control in (
        "backgroundGlowSlider", "backgroundMotionSlider", "focusModeToggle",
        "powerSaveToggle", "visualizerAdvancedOptionsToggle",
    ):
        assert root.findChild(object, removed_control) is None
    for obsolete in (
        "sceneChoiceRepeater", "梦境花海", "深空脉冲", "黄昏港湾", "setSceneId",
        "dragPreviewModePicker", "experienceModeToggle", "miniMapVisibilityToggle",
    ):
        assert obsolete not in source
    root.close()


def test_appearance_settings_expose_background_presets_and_upload_entry(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    source = (QML_DIR / "BackgroundSettingsDialog.qml").read_text(encoding="utf-8")
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    appearance_popup = root.findChild(object, "backgroundSettingsDialog")
    assert appearance_popup is not None
    appearance_popup.open()
    QTest.qWait(20)

    background_section = root.findChild(object, "backgroundSection")
    background_toggle = root.findChild(object, "backgroundEnabledToggle")
    preset_picker = root.findChild(object, "backgroundPresetPicker")
    upload_button = root.findChild(object, "customBackgroundUploadButton")
    clear_button = root.findChild(object, "clearCustomBackgroundButton")
    file_dialog = root.findChild(object, "backgroundFileDialog")
    opacity_slider = root.findChild(object, "backgroundOpacitySlider")
    blur_slider = root.findChild(object, "backgroundBlurSlider")
    darkness_slider = root.findChild(object, "backgroundDarknessSlider")
    motion_picker = root.findChild(object, "backgroundMotionPicker")
    preview = root.findChild(object, "backgroundSettingsPreview")
    scene = engine.findChild(SceneController)

    assert background_section is not None
    assert background_toggle is not None
    assert preset_picker is not None
    assert upload_button is not None
    assert clear_button is not None
    assert file_dialog is not None
    for slider in (opacity_slider, blur_slider, darkness_slider):
        assert slider is not None
    assert motion_picker is not None
    assert preview is not None
    assert appearance_popup.property("modal") is True
    assert appearance_popup.property("width") <= 900
    assert appearance_popup.property("height") <= 640
    assert root.findChild(object, "appearanceDockHost") is None
    assert scene is not None
    assert background_toggle.property("checked") is True
    assert preset_picker.property("currentIndex") == 2
    assert opacity_slider.property("value") == pytest.approx(0.38)
    assert blur_slider.property("value") == pytest.approx(0.42)
    assert darkness_slider.property("value") == pytest.approx(0.58)
    assert clear_button.property("visible") is False
    filters = file_dialog.property("nameFilters")
    assert any("webp" in str(item).lower() for item in filters)
    assert "scene.setCustomBackgroundPath(selectedFile.toString())" in main_source

    custom_path = QML_DIR / "assets" / "launch-cosmos.webp"
    scene.setCustomBackgroundPath(str(custom_path))
    qapp.processEvents()
    assert upload_button.property("text") == "更换图片"
    assert clear_button.property("visible") is True

    opacity_slider.valueCommitted.emit(0.51)
    qapp.processEvents()
    assert scene.backgroundOpacity == pytest.approx(0.51)
    assert opacity_slider.property("value") == pytest.approx(0.51)

    preset_picker.select(0, True)
    qapp.processEvents()
    assert preset_picker.property("currentIndex") == 0
    assert clear_button.property("visible") is False
    assert scene.customBackgroundPath == ""
    assert scene.backgroundSource == ""
    assert root.findChild(QQuickItem, "editorBackgroundLayer").property("visible") is False

    appearance_popup.close()
    root.close()


def test_record_indicator_hidden_until_recording(qapp) -> None:
    # This checks UI visibility; a deterministic player keeps recording silent.
    from tests.qt.test_playback import FakePlayer
    engine, root, controller = load_shell(qapp, player=FakePlayer())
    octave = root.findChild(object, "octaveLabel")
    recording_lane = root.findChild(object, "recordingLane")
    assert octave.property("text") == "八度 C4"
    assert recording_lane is not None
    assert recording_lane.property("visible") is False
    assert root.findChild(object, "editorStarterGuide") is not None
    assert root.findChild(object, "recordingStatusBadge") is not None

    engine.rootContext().contextProperty("keyboard").startRecording()
    qapp.processEvents()
    assert recording_lane.property("visible") is True
    canvas = root.findChild(object, "pianoCanvas")
    assert recording_lane.property("y") + recording_lane.property("height") <= (
        canvas.property("height") - 58
    )
    root.close()


def test_latest_recorded_note_receives_visual_feedback(qapp, qtbot) -> None:
    from tests.qt.test_playback import FakePlayer

    engine, root, controller = load_shell(qapp, player=FakePlayer())
    keyboard = engine.rootContext().contextProperty("keyboard")
    timeline = root.findChild(object, "timeline")

    keyboard.startRecording()
    keyboard.pressKey(ord("A"))
    qapp.processEvents()

    qtbot.waitUntil(lambda: bool(keyboard.lastRecordedNoteId))
    assert timeline.property("latestRecordedNoteId") == keyboard.lastRecordedNoteId
    assert "latestRecorded" in (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")
    keyboard.stopRecording()
    root.close()


def test_note_context_menu_uses_the_dark_application_surface(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert 'objectName: "noteContextMenu"' in source
    assert 'objectName: "gestureFeedback"' in source
    assert "gestureBarLabel" in source
    assert "Menu {\n                    id: noteMenu" not in source
    root.close()


def test_timeline_exposes_note_sync_states_and_playback_choice(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert root.findChild(object, "timelineSyncStatus") is not None
    assert root.findChild(object, "timelineDurationStatus") is not None
    assert root.findChild(object, "unsyncedPlaybackDialog") is not None
    assert "required property string status" in timeline_source
    assert "pendingNoteBadge" not in timeline_source
    assert "synchronizeNow" in (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    root.close()


def test_pending_notes_are_dimmed_without_per_note_sync_chrome(qapp) -> None:
    pending = Note.create(id="pending", pitch=60, start_time=1.2, duration=0.8, status="pending")
    engine, root, controller = load_shell(qapp, notes=(pending,))
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert "pending ? root.theme.notePreviewAlpha" in timeline_source
    assert root.findChild(object, "pendingNoteBadge-pending") is None
    root.close()


def test_alt_drag_duplicate_renders_a_ghost_without_rebuilding_real_notes(qapp) -> None:
    source = Note.create(id="source", pitch=60, start_time=1.0, duration=0.5)
    engine, root, controller = load_shell(qapp, notes=(source,))
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    controller.beginDuplicateGesture()
    controller.previewDuplicatePixels(80.0, 0.0)
    qapp.processEvents()

    assert len(controller.state.notes) == 1
    assert controller.duplicatePreview[0]["startTime"] == pytest.approx(2.0)
    assert 'objectName: "duplicatePreviewNote-" + sourceId' in timeline_source
    assert "opacity: 0.34" in timeline_source
    assert "Qt.AltModifier" in timeline_source
    controller.cancelDuplicateGesture()
    root.close()


def test_drag_feedback_uses_high_contrast_semantic_theme_tokens(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")
    theme_source = (QML_DIR / "Theme.qml").read_text(encoding="utf-8")

    assert "readonly property color feedbackSurface" in theme_source
    assert "color: root.theme.feedbackSurface" in timeline_source
    assert 'text: "正在移动"' in timeline_source
    assert "font.family: root.theme.numberFont" in timeline_source
    root.close()


def test_piano_roll_uses_a_playable_real_keyboard_and_clips_notes_at_its_edge(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    timeline_source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert 'objectName: "pianoKeyboard"' in timeline_source
    assert '"centerCPianoKey"' in timeline_source
    assert "keyboard.pressPitch" in timeline_source
    assert "keyboard.releasePitch" in timeline_source
    assert "rawNoteX" in timeline_source
    root.close()


def test_inspector_exposes_new_note_defaults_and_can_collapse(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    inspector = root.findChild(object, "inspector")

    assert root.findChild(object, "newNoteDefaults") is not None
    assert root.findChild(object, "newNoteDefaultsTitle").property("text") == "新建音符默认值"
    assert root.findChild(object, "selectedNotesTitle").property("text") == "当前选择"
    assert root.findChild(object, "defaultNoteLengthField") is not None
    assert root.findChild(object, "defaultNoteVelocitySlider") is not None
    assert root.findChild(object, "inspectorCollapseButton") is not None
    inspector.setProperty("collapsed", True)
    qapp.processEvents()
    assert inspector.property("collapsed") is True
    root.close()


def test_inspector_uses_one_scroll_owner_and_independent_property_sections(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    inspector = root.findChild(object, "inspector")
    body = root.findChild(object, "inspectorBodyFlickable")
    scrollbar = root.findChild(object, "inspectorBodyScrollBar")
    defaults = root.findChild(object, "newNoteDefaultsSection")
    selection = root.findChild(object, "selectionSection")

    assert body is not None
    assert scrollbar is not None
    assert defaults is not None
    assert selection is not None
    assert defaults.property("expanded") is True
    assert selection.property("expanded") is True

    defaults.setProperty("expanded", False)
    qapp.processEvents()

    assert defaults.property("expanded") is False
    assert selection.property("expanded") is True
    experience = engine.rootContext().contextProperty("experience")
    assert experience.inspectorSectionExpanded("new-note-defaults", True) is False
    assert body.property("contentY") >= 0
    assert body.property("contentY") <= max(
        0.0, body.property("contentHeight") - body.property("height")
    ) + 0.5
    assert inspector.property("width") == 304
    root.close()


def test_inspector_keeps_defaults_isolated_and_renders_mixed_selection_values(qapp) -> None:
    notes = (
        Note.create(id="low", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="high", pitch=64, start_time=1.0, duration=0.75),
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    qapp.processEvents()

    defaults = root.findChild(object, "newNoteDefaultsSection")
    selection = root.findChild(object, "selectionSection")
    pitch_field = root.findChild(object, "pitchField")
    default_length = root.findChild(object, "defaultNoteLengthField")

    assert selection.property("badge") == "2"
    assert pitch_field.property("text") in ("", None)
    assert pitch_field.property("placeholderText") == "混合"

    experience.setNewNoteLengthBeats(2.0)
    qapp.processEvents()
    assert default_length.property("text") == "2.00"
    assert controller.selectionSummary["pitchMixed"] is True
    assert defaults.property("expanded") is True
    assert selection.property("expanded") is True
    root.close()


def test_inspector_wheel_scrolls_body_without_editing_unfocused_numeric_field(qapp) -> None:
    notes = (
        Note.create(id="low", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="high", pitch=64, start_time=1.0, duration=0.75),
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    controller.selectNotes(["low", "high"])
    root.setProperty("height", 640)
    qapp.processEvents()

    body = root.findChild(QObject, "inspectorBodyFlickable")
    field = root.findChild(QObject, "durationField")
    assert body is not None and field is not None
    maximum = max(0.0, float(body.property("contentHeight")) - float(body.property("height")))
    assert maximum > 0
    starting_y = maximum / 2
    body.setProperty("contentY", starting_y)
    qapp.processEvents()
    before_text = field.property("text")

    # Use a mid-scroll position where the duration field is visibly hit-testable.
    position = field.mapToItem(
        root.contentItem(),
        QPointF(
            float(field.property("width")) / 2,
            float(field.property("height")) / 2,
        ),
    )
    event = QWheelEvent(
        position,
        position,
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert QCoreApplication.sendEvent(root, event) is True
    qapp.processEvents()

    # One 120-unit wheel step may legitimately clamp this short document to zero.
    assert float(body.property("contentY")) == max(0.0, starting_y - 120.0)
    assert field.property("text") == before_text
    root.close()


def test_editor_starter_guide_is_for_beginner_mode_only(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    guide = root.findChild(object, "editorStarterGuide")

    experience.setMode("beginner")
    experience.completeFirstRun()
    qapp.processEvents()
    assert guide.property("visible") is True

    experience.setMode("advanced")
    qapp.processEvents()
    assert guide.property("visible") is False
    root.close()


def test_tempo_controls_reflect_project_tempo(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    # Status text is mode-dependent; tempo detail belongs to advanced mode.
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    bpm_field = root.findChild(object, "bpmField")
    signature_box = root.findChild(object, "timeSignatureBox")
    # Selection actions also own a gridLabel; scope this to transport status.
    status_bar = _visual_item(root.contentItem(), "statusBar")
    assert bpm_field.property("value") == 120
    assert signature_box.property("currentIndex") == 0
    controller.setBpm(90)
    controller.setTimeSignature(3, 4)
    assert bpm_field.property("value") == 90
    assert signature_box.property("currentIndex") == 1
    # Compact transport moves full meter detail to the status tooltip.
    assert "3/4" in status_bar.property("tooltipText")
    root.close()


def test_transport_places_compact_tone_slider_after_position(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    qapp.processEvents()

    position = root.findChild(QObject, "transportPositionLabel")
    slider = root.findChild(QObject, "transportPianoToneSlider")
    speed = root.findChild(QObject, "transportPlaybackRateControl")

    assert slider is not None
    assert slider.property("accessibleName") == "钢琴音色：柔和到明亮"
    assert position.mapToItem(root.contentItem(), QPointF()).x() < slider.mapToItem(
        root.contentItem(), QPointF()
    ).x()
    assert slider.mapToItem(root.contentItem(), QPointF()).x() < speed.mapToItem(
        root.contentItem(), QPointF()
    ).x()
    root.close()


def test_tone_slider_previews_locally_and_commits_once(qapp) -> None:
    tone = FakePianoTone(value=0.5)
    engine, root, controller = load_shell(qapp, piano_tone=tone)
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    slider = root.findChild(QObject, "transportPianoToneSlider")

    slider.valueEdited.emit(0.25)
    qapp.processEvents()
    assert tone.value == pytest.approx(0.5)
    assert tone.committed_values == []

    slider.valueCommitted.emit(0.25)
    qapp.processEvents()
    assert tone.value == pytest.approx(0.25)
    assert tone.committed_values == [pytest.approx(0.25)]
    root.close()


def test_tone_slider_keyboard_commits_and_fallback_disables(qapp) -> None:
    tone = FakePianoTone(value=0.5)
    engine, root, controller = load_shell(qapp, piano_tone=tone)
    engine.rootContext().contextProperty("experience").setMode("advanced")
    slider = root.findChild(QObject, "transportPianoToneSlider")

    assert slider.property("activeFocusOnTab") is True
    slider.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Right)
    qapp.processEvents()
    assert tone.committed_values == [pytest.approx(0.55)]
    QTest.keyClick(root, Qt.Key_Left)
    qapp.processEvents()
    assert tone.committed_values == [pytest.approx(0.55), pytest.approx(0.5)]

    root.close()
    unavailable = FakePianoTone(
        available=False,
        status_message="采样不可用，已自动使用 Legacy Piano；详情请查看日志。",
    )
    engine, root, controller = load_shell(qapp, piano_tone=unavailable)
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    slider = root.findChild(QObject, "transportPianoToneSlider")
    assert slider.property("enabled") is False
    assert "已自动使用 Legacy Piano" in slider.property("toneStatusMessage")
    assert unavailable.statusMessage in slider.property("toneStatusMessage")
    root.close()


def test_bpm_stepper_reserves_space_for_numeric_input(qapp) -> None:
    """Keep the editable number visibly separate from the step controls."""
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    qapp.processEvents()

    bpm_field = root.findChild(object, "bpmField")
    numeric_input = bpm_field.property("contentItem")
    decrease_button = root.findChild(object, "bpmDecreaseButton")
    increase_button = root.findChild(object, "bpmIncreaseButton")

    assert bpm_field.property("implicitWidth") >= 108
    assert decrease_button is not None
    assert increase_button is not None
    assert numeric_input.property("x") >= decrease_button.property("x") + decrease_button.property("width") + 4
    assert numeric_input.property("x") + numeric_input.property("width") <= increase_button.property("x") - 4
    root.close()


def test_bpm_stepper_commits_typed_integer(qapp) -> None:
    """Commit a directly typed BPM when the user confirms the edit."""
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    bpm_field = root.findChild(object, "bpmField")
    numeric_input = bpm_field.property("contentItem")
    numeric_input.forceActiveFocus()
    QTest.keyClick(root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for key in (Qt.Key.Key_1, Qt.Key.Key_4, Qt.Key.Key_4):
        QTest.keyClick(root, key)
    QTest.keyClick(root, Qt.Key.Key_Return)
    qapp.processEvents()

    assert controller.bpm == 144
    assert bpm_field.property("value") == 144
    root.close()


def test_snap_controls_reflect_editor_snap(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    # Status text is mode-dependent; snap detail belongs to advanced mode.
    engine.rootContext().contextProperty("experience").setMode("advanced")
    qapp.processEvents()
    snap_toggle = root.findChild(object, "snapToggle")
    division_box = root.findChild(object, "snapDivisionBox")
    # Selection actions also own a gridLabel; scope this to transport status.
    status_bar = _visual_item(root.contentItem(), "statusBar")
    assert snap_toggle.property("checked") is True
    assert division_box.property("currentIndex") == 1
    controller.setSnapDivision(16)
    controller.setSnapEnabled(False)
    assert division_box.property("currentIndex") == 2
    assert snap_toggle.property("checked") is False
    # Compact transport moves full snap detail to the status tooltip.
    assert "吸附关" in status_bar.property("tooltipText")
    root.close()


def test_welcome_new_action_enters_a_clean_project(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setProperty("pageState", "launch")
    qapp.processEvents()
    welcome = root.findChild(object, "welcomeOverlay")

    assert welcome is not None
    assert welcome.property("visible") is True
    assert root.findChild(object, "homePrimaryCreate") is not None
    assert root.findChild(object, "homeOpenProject") is not None
    assert root.findChild(object, "welcomeBlankButton") is not None

    welcome.newRequested.emit()
    qapp.processEvents()

    assert root.property("pageState") == "editor"
    assert welcome.property("visible") is False
    assert controller.state.notes == ()
    root.close()


def test_mini_map_drag_uses_a_stable_parent_coordinate_space(qapp) -> None:
    source = (QML_DIR / "MiniMap.qml").read_text(encoding="utf-8")

    assert "moveArea.mapToItem(root.parent, mouse.x, mouse.y)" in source
    assert "resizePointer.mapToItem(root.parent, mouse.x, mouse.y)" in source


def test_project_library_exposes_batch_management_controls(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    source = (QML_DIR / "Welcome.qml").read_text(encoding="utf-8")

    assert root.findChild(object, "libraryManagementButton") is not None
    assert root.findChild(object, "bulkDeleteProjectDialog") is not None
    assert "welcome.rememberRecycle(paths)" in source
    assert "editor.recycleProjects(paths)" in source
    root.close()


def test_welcome_library_exposes_search_paging_and_recycle_progress(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    welcome = root.findChild(object, "welcomeOverlay")

    assert welcome is not None
    assert welcome.property("projectsPerPage") == 10
    assert welcome.property("libraryPage") == 1
    assert welcome.property("librarySearchQuery") == ""
    for name in (
        "projectSearchField",
        "previousLibraryPageButton",
        "nextLibraryPageButton",
        "libraryPageInput",
        "jumpLibraryPageButton",
        "bulkRecycleProgressBar",
    ):
        assert root.findChild(object, name) is not None

    controller._recycle_job = object()
    controller.recycleProgressChanged.emit()
    qapp.processEvents()
    for name in (
        "restoreButton",
        "discardRecoveryButton",
        "homeBrowseExamples",
        "homePrimaryCreate",
        "homeOpenProject",
        "welcomeBlankButton",
    ):
        assert root.findChild(object, name).property("enabled") is False
    root.close()


def test_timeline_ruler_labels_elapsed_seconds(qapp) -> None:
    source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")

    assert "readonly property int secondIndex" in source
    assert 'text: secondIndex + " 秒"' in source
    assert "root.pixelsPerSecond" in source


def test_original_and_smart_views_share_editor_state_without_dirtying(qapp) -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),
        Note.create(id="b", pitch=60, start_time=0.2, duration=0.2),
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    timeline = root.findChild(QObject, "timeline")
    experience = engine.rootContext().contextProperty("experience")

    assert timeline.property("displayMode") == "original"
    before = controller.state
    experience.setCompositeDisplayMode("smart")
    qapp.processEvents()

    assert timeline.property("displayMode") == "smart"
    assert controller.state == before
    assert controller.isDirty is False


def test_smart_view_expands_group_and_selection_targets_child(qapp) -> None:
    notes = tuple(
        Note.create(id=f"n{i}", pitch=60, start_time=i * 0.2, duration=0.18)
        for i in range(5)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    qapp.processEvents()

    group_name = "compositeNoteItem-composite:n0,n1,n2,n3,n4"
    pending = [root.findChild(QQuickItem, "timeline")]
    composite = None
    while pending:
        item = pending.pop()
        if item.objectName() == group_name:
            composite = item
            break
        pending.extend(item.childItems())
    assert composite is not None
    expand_button = composite.findChild(QObject, "expandButton")
    assert expand_button is not None
    expand_button.clicked.emit()
    qapp.processEvents()

    pending = [composite]
    segment = None
    while pending:
        item = pending.pop()
        if item.objectName() == "segmentButton-n2":
            segment = item
            break
        pending.extend(item.childItems())
    assert segment is not None
    assert segment.property("visible") is True
    point = segment.mapToScene(
        QPointF(segment.width() / 2, segment.height() / 2)
    ).toPoint()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()

    assert controller.state.selected_ids == frozenset({"n2"})
    assert controller.isDirty is False


def test_smart_view_drag_moves_the_whole_composite_in_one_gesture(qapp) -> None:
    notes = tuple(
        Note.create(id=f"drag-{index}", pitch=60, start_time=index * 0.2, duration=0.18)
        for index in range(3)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    controller.clearSelection()
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    group = _visual_item(
        timeline, "compositeNoteItem-composite:drag-0,drag-1,drag-2"
    )
    start = group.mapToScene(QPointF(8, group.height() / 2)).toPoint()
    destination = start + QPoint(40, -18)
    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(root, destination, delay=10)
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, destination
    )
    qapp.processEvents()

    moved = {note.id: note for note in controller.state.notes}
    assert [moved[f"drag-{index}"].start_time for index in range(3)] == pytest.approx(
        [0.5, 0.7, 0.9]
    )
    assert [moved[f"drag-{index}"].pitch for index in range(3)] == [61, 61, 61]
    assert controller.historyUndoDepth == 1
    root.close()


def test_smart_view_drag_preserves_a_marquee_selection_across_composites(qapp) -> None:
    notes = (
        Note.create(id="left-0", pitch=60, start_time=0.0, duration=0.18),
        Note.create(id="left-1", pitch=60, start_time=0.2, duration=0.18),
        Note.create(id="left-2", pitch=60, start_time=0.4, duration=0.18),
        Note.create(id="right-0", pitch=64, start_time=2.0, duration=0.18),
        Note.create(id="right-1", pitch=64, start_time=2.2, duration=0.18),
        Note.create(id="right-2", pitch=64, start_time=2.4, duration=0.18),
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    controller.selectNotes([note.id for note in notes])
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    left_group = _visual_item(
        timeline, "compositeNoteItem-composite:left-0,left-1,left-2"
    )
    right_group = _visual_item(
        timeline, "compositeNoteItem-composite:right-0,right-1,right-2"
    )
    right_origin = right_group.mapToScene(QPointF(0, 0))
    start = left_group.mapToScene(
        QPointF(8, left_group.height() / 2)
    ).toPoint()
    destination = start + QPoint(40, -18)
    QTest.mousePress(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(root, destination, delay=10)
    qapp.processEvents()
    right_preview = right_group.mapToScene(QPointF(0, 0))
    assert right_preview.x() - right_origin.x() == pytest.approx(40)
    assert right_preview.y() - right_origin.y() == pytest.approx(-18)
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, destination
    )
    qapp.processEvents()

    moved = {note.id: note for note in controller.state.notes}
    assert [moved[note.id].start_time for note in notes] == pytest.approx(
        [0.5, 0.7, 0.9, 2.5, 2.7, 2.9]
    )
    assert [moved[note.id].pitch for note in notes] == [61, 61, 61, 65, 65, 65]
    assert controller.selectedCount == 6
    assert controller.historyUndoDepth == 1
    root.close()


def test_smart_view_drag_moves_only_the_expanded_segment(qapp) -> None:
    notes = tuple(
        Note.create(id=f"part-{index}", pitch=60, start_time=index * 0.2, duration=0.18)
        for index in range(5)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    controller.clearSelection()
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    group = _visual_item(
        timeline, "compositeNoteItem-composite:part-0,part-1,part-2,part-3,part-4"
    )
    _visual_item(group, "expandButton").clicked.emit()
    qapp.processEvents()
    segment = _visual_item(group, "segmentButton-part-2")
    start = segment.mapToScene(QPointF(segment.width() / 2, segment.height() / 2)).toPoint()
    destination = start + QPoint(40, -18)
    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(root, destination, delay=10)
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, destination
    )
    qapp.processEvents()

    moved = {note.id: note for note in controller.state.notes}
    assert moved["part-0"].start_time == pytest.approx(0.0)
    assert moved["part-1"].start_time == pytest.approx(0.2)
    assert moved["part-2"].start_time == pytest.approx(0.9)
    assert moved["part-3"].start_time == pytest.approx(0.6)
    assert moved["part-2"].pitch == 61
    assert controller.historyUndoDepth == 1
    root.close()


def test_smart_segment_selection_highlights_only_the_target_segment(qapp) -> None:
    notes = tuple(
        Note.create(id=f"segment-{index}", pitch=60, start_time=index * 0.2, duration=0.18)
        for index in range(3)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    controller.clearSelection()
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline is not None
    group = _visual_item(
        timeline, "compositeNoteItem-composite:segment-0,segment-1,segment-2"
    )
    _visual_item(group, "expandButton").clicked.emit()
    qapp.processEvents()
    assert group.property("expanded") is True
    assert controller.state.selected_ids == frozenset()
    target = _visual_item(group, "segmentButton-segment-1")
    other = _visual_item(group, "segmentButton-segment-0")

    controller.selectNotes(["segment-1"])
    qapp.processEvents()

    assert controller.state.selected_ids == frozenset({"segment-1"})
    assert target.property("childSelected") is True
    assert other.property("childSelected") is False
    root.close()


def test_smart_expand_button_accepts_real_pointer_without_selecting_group(qapp) -> None:
    notes = tuple(
        Note.create(id=f"expand-{index}", pitch=60, start_time=index * 0.2, duration=0.18)
        for index in range(3)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    controller.clearSelection()
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline is not None
    group = _visual_item(
        timeline, "compositeNoteItem-composite:expand-0,expand-1,expand-2"
    )
    expand_button = _visual_item(group, "expandButton")
    point = expand_button.mapToScene(
        QPointF(expand_button.width() / 2, expand_button.height() / 2)
    ).toPoint()
    QTest.mouseMove(root, point)
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()

    assert group.property("expanded") is True
    assert controller.state.selected_ids == frozenset()
    root.close()


def test_dense_smart_canvas_keeps_composite_expand_and_segment_selection(qapp) -> None:
    notes = [
        Note.create(id="dense-0", pitch=60, start_time=1.0, duration=0.1),
        Note.create(id="dense-1", pitch=60, start_time=1.2, duration=0.1),
        Note.create(id="dense-2", pitch=60, start_time=1.4, duration=0.1),
    ]
    notes.extend(
        Note.create(
            id=f"dense-singleton-{index}",
            pitch=36 + (index % 48),
            start_time=20.0 + index * 0.3,
            duration=0.08,
        )
        for index in range(700)
    )
    engine, root, controller = load_shell(qapp, notes=tuple(notes))
    experience = engine.rootContext().contextProperty("experience")
    controller.clearSelection()
    experience.setCompositeDisplayMode("smart")
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    canvas = _visual_item(timeline, "smartCompositeCanvas")
    pointer = _visual_item(canvas, "smartCompositePointer")
    assert canvas.property("visible") is True
    group_id = "composite:dense-0,dense-1,dense-2"
    assert any(item["groupId"] == group_id for item in controller.compositeModel.visibleItems)

    group_y = (int(timeline.property("highestPitch")) - 60) * float(
        timeline.property("rowHeight")
    ) + 8
    expand_point = canvas.mapToScene(QPointF(182, group_y)).toPoint()
    QTest.mouseMove(root, expand_point)
    assert pointer.property("containsMouse") is True
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, expand_point)
    qapp.processEvents()
    assert any(
        item["groupId"] == group_id and item["expanded"]
        for item in controller.compositeModel.visibleItems
    )
    assert controller.state.selected_ids == frozenset()

    # The Canvas keeps the expand affordance at the group's right edge, so
    # use the first child, which remains outside that affordance's hit box.
    segment_point = canvas.mapToScene(QPointF(156, group_y)).toPoint()
    QTest.mouseMove(root, segment_point)
    assert pointer.property("containsMouse") is True
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, segment_point)
    qapp.processEvents()
    assert controller.state.selected_ids == frozenset({"dense-0"})
    root.close()


def test_composite_view_and_selection_audition_preserve_project_state_end_to_end(
    qapp,
) -> None:
    notes = tuple(
        Note.create(id=str(index), pitch=60, start_time=index * 0.1, duration=0.4)
        for index in range(5)
    )
    player = DeterministicPlaybackPlayer()
    engine, root, controller = load_shell(qapp, notes=notes, player=player)
    experience = engine.rootContext().contextProperty("experience")
    playback = engine.rootContext().contextProperty("playback")
    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline is not None

    before_content = controller.state.content
    before_depth = controller.historyUndoDepth
    before_dirty = controller.isDirty

    experience.setCompositeDisplayMode("smart")
    qapp.processEvents()
    assert timeline.property("displayMode") == "smart"

    group = None
    pending = [timeline]
    while pending:
        item = pending.pop()
        if item.objectName() == "compositeNoteItem-composite:0,1,2,3,4":
            group = item
            break
        pending.extend(item.childItems())
    assert group is not None
    expand_button = group.findChild(QObject, "expandButton")
    assert expand_button is not None
    expand_button.clicked.emit()
    qapp.processEvents()
    segment = None
    pending = [group]
    while pending:
        item = pending.pop()
        if item.objectName() == "segmentButton-2":
            segment = item
            break
        pending.extend(item.childItems())
    assert segment is not None and segment.property("visible") is True
    expand_button.clicked.emit()
    qapp.processEvents()

    # Start the normal transport first, then audition a single selected child.
    # This proves that selection audition takes ownership of playback without
    # resetting the user's project playhead or changing project history.
    playback.seek(0.25)
    playback.play()
    qapp.processEvents()
    assert playback.playing is True
    project_position = playback.positionSeconds
    controller.selectNotes(["2"])
    qapp.processEvents()

    playback.selectionAudition.playSelection()
    qapp.processEvents()
    assert playback.playing is False
    assert playback.selectionAudition.playing is True
    assert playback.positionSeconds == pytest.approx(project_position)
    assert player.temporary_play_calls == 1
    assert [note.id for note in player.temporary_notes] == ["2"]

    playback.selectionAudition.setSoloSelection(True)
    playback.selectionAudition.setLoopSelection(True)
    assert playback.selectionAudition.soloSelection is True
    assert playback.selectionAudition.loopSelection is True
    player.finish_temporary()
    qapp.processEvents()
    assert playback.selectionAudition.playing is True
    assert player.temporary_play_calls == 2
    assert [note.id for note in player.temporary_notes] == ["2"]
    player.finish_temporary()
    qapp.processEvents()
    assert playback.selectionAudition.playing is True
    assert player.temporary_play_calls == 3

    playback.selectionAudition.stop()
    qapp.processEvents()
    assert playback.selectionAudition.playing is False
    assert player.finish_temporary_calls == 2
    assert player.stop_temporary_calls >= 2
    playback.selectionAudition.setLoopSelection(False)
    playback.selectionAudition.setSoloSelection(False)
    assert playback.selectionAudition.loopSelection is False
    assert playback.selectionAudition.soloSelection is False

    experience.setCompositeDisplayMode("original")
    qapp.processEvents()
    assert timeline.property("displayMode") == "original"
    assert controller.state.content == before_content
    assert controller.historyUndoDepth == before_depth
    assert controller.isDirty is before_dirty
    root.close()


def test_selection_audition_dims_unselected_notes_and_updates_status(qapp) -> None:
    notes = (
        Note.create(id="audition-selected", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="audition-other", pitch=64, start_time=1.0, duration=0.5),
    )
    player = DeterministicPlaybackPlayer()
    engine, root, controller = load_shell(qapp, notes=notes, player=player)
    playback = engine.rootContext().contextProperty("playback")
    controller.selectNotes(["audition-selected"])
    qapp.processEvents()
    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline is not None
    selected = _visual_item(timeline, "noteItem-audition-selected")
    other = _visual_item(timeline, "noteItem-audition-other")
    status = _visual_item(
        _visual_item(root.contentItem(), "selectionActions"),
        "selectionActionsSummary",
    )
    assert selected is not None and other is not None and status is not None

    playback.selectionAudition.playSelection()
    qapp.processEvents()

    assert selected.property("opacity") == pytest.approx(1.0)
    assert other.property("opacity") < 1.0
    assert "试听" in status.property("text")
    playback.selectionAudition.stop()
    root.close()


@pytest.mark.parametrize("note_count", [500, 1000, 5000])
def test_composite_display_performance_records_timings(note_count: int, qapp) -> None:
    notes = tuple(
        Note.create(
            id=f"composite-performance-{index}",
            pitch=36 + (index % 48),
            start_time=(index % 400) * 0.125,
            duration=0.18,
            velocity=48 + (index % 80),
        )
        for index in range(note_count)
    )

    grouping_started = perf_counter()
    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)
    grouping_seconds = perf_counter() - grouping_started

    load_started = perf_counter()
    engine, root, controller = load_shell(qapp, notes=notes)
    load_seconds = perf_counter() - load_started
    timeline = root.findChild(QQuickItem, "timeline")
    experience = engine.rootContext().contextProperty("experience")
    assert timeline is not None
    assert controller.noteModel.rowCount() == note_count
    assert groups

    toggle_started = perf_counter()
    experience.setCompositeDisplayMode("smart")
    qapp.processEvents()
    toggle_seconds = perf_counter() - toggle_started
    assert timeline.property("displayMode") == "smart"
    if note_count == 5000:
        smart_canvas = timeline.findChild(object, "smartCompositeCanvas")
        assert smart_canvas is not None
        assert smart_canvas.property("visible") is True
        assert toggle_seconds < 5.0

    print(
        "composite display performance "
        f"{note_count} notes: grouping={grouping_seconds:.6f}s, "
        f"qml_load={load_seconds:.6f}s, smart_toggle={toggle_seconds:.6f}s, "
        f"raw_items={note_count}, smart_items={len(groups)}"
    )
    experience.setCompositeDisplayMode("original")
    qapp.processEvents()
    root.close()
