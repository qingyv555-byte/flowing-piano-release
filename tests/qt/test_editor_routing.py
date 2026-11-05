from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, QObject, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.qt.test_studio_workflows import no_audio_device


def load_launch(qapp, tmp_path):
    note = Note.create(id="launch-note", pitch=60, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((note,), frozenset({note.id})))
    appearance_settings = QSettings(
        str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
    )
    app, engine, controller, playback, keyboard = build_engine(
        controller=controller, appearance_settings=appearance_settings
    )
    return engine, engine.rootObjects()[0], controller, playback, keyboard, note


def set_page(root: QObject, page_state: str) -> None:
    assert QMetaObject.invokeMethod(
        root, "setPage", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", page_state), Q_ARG("QVariant", False),
    )


def test_launch_and_editor_are_mutually_exclusive(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)

    assert root.property("pageState") == "launch"
    assert root.property("editorInputEnabled") is False
    assert root.findChild(QObject, "editorShell") is None
    welcome = root.findChild(QObject, "welcomeOverlay")
    assert welcome is not None and welcome.property("visible") is True
    assert welcome.property("enabled") is True

    set_page(root, "editor")
    qapp.processEvents()

    editor_shell = root.findChild(QObject, "editorShell")
    assert editor_shell is not None
    assert editor_shell.property("visible") is True
    assert editor_shell.property("enabled") is True
    assert welcome.property("visible") is False
    assert welcome.property("enabled") is False
    root.close()


def test_welcome_route_signal_forwards_the_current_project_focus_request(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    assert welcome is not None
    assert welcome.property("focusCurrentProject") is False

    assert QMetaObject.invokeMethod(
        welcome, "pageChangeRequested", Qt.ConnectionType.DirectConnection,
        Q_ARG("QString", "library"), Q_ARG("bool", True),
    )
    qapp.processEvents()

    assert root.property("pageState") == "library"
    assert welcome.property("focusCurrentProject") is True
    root.close()


def test_launch_blocks_editor_keys_without_changing_the_project(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    controller._saved_content = controller.state.content
    note_count = controller.noteModel.rowCount()
    content_before = controller.state.content
    title_before = root.property("title")
    key_ons: list[int] = []
    keyboard.keyNoteOn.connect(key_ons.append)

    for key in (
        Qt.Key_A, Qt.Key_S, Qt.Key_D, Qt.Key_F, Qt.Key_G, Qt.Key_H, Qt.Key_J,
        Qt.Key_W, Qt.Key_E, Qt.Key_T, Qt.Key_Y, Qt.Key_U,
        Qt.Key_Delete, Qt.Key_Backspace,
    ):
        QTest.keyClick(root, key)
    QTest.keyClick(root, Qt.Key_Z, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()

    assert playback.playing is False
    assert key_ons == []
    assert controller.noteModel.rowCount() == note_count
    assert controller.state.content == content_before
    assert controller.isDirty is False
    assert root.property("title") == title_before

    # Space activates the intentionally focused primary creation action, but
    # must not leak through as the editor's play/pause shortcut.
    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert root.property("pageState") == "editor"
    assert playback.playing is False
    root.close()


def test_launch_pointer_and_wheel_events_cannot_reach_the_unloaded_timeline(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    controller._saved_content = controller.state.content
    content_before = controller.state.content
    title_before = root.property("title")

    assert root.findChild(QObject, "timeline") is None
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(12, 12))
    QTest.mouseClick(root, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(18, 18))
    QTest.mouseClick(root, Qt.MouseButton.ExtraButton1, Qt.KeyboardModifier.NoModifier, QPoint(24, 24))
    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, 30))
    QTest.mouseMove(root, QPoint(70, 70))
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(70, 70))
    QCoreApplication.sendEvent(
        root,
        QWheelEvent(
            QPointF(36, 36), QPointF(36, 36), QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        ),
    )
    qapp.processEvents()

    assert root.findChild(QObject, "timeline") is None
    assert controller.state.content == content_before
    assert controller.isDirty is False
    assert root.property("title") == title_before
    root.close()


def test_leaving_editor_releases_notes_and_pauses_playback(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    set_page(root, "editor")
    qapp.processEvents()
    assert root.property("pageState") == "editor"
    key_offs: list[int] = []
    keyboard.keyNoteOff.connect(key_offs.append)
    keyboard.startRecording()
    keyboard.pressKey(Qt.Key.Key_A)
    playback.play()

    set_page(root, "launch")
    qapp.processEvents()

    assert root.property("pageState") == "launch"
    assert keyboard.recording is False
    assert keyboard._held == {}
    assert key_offs == [60]
    assert playback.playing is False
    assert root.property("editorInputEnabled") is False
    root.close()


def test_leaving_editor_routes_release_all_through_safe_audio_without_project_changes(
    qapp, tmp_path
) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    original_content = controller.state.content

    class RoutePlayback:
        def __init__(self) -> None:
            self._next_voice_id = 1
            self.stop_modes: list[str] = []

        def startAudition(self, pitch: int, velocity: int) -> int:
            voice_id = self._next_voice_id
            self._next_voice_id += 1
            return voice_id

        def releaseAudition(self, voice_id: int, mode: str) -> None:
            self.stop_modes.append(mode)

        def stopAudition(self, mode: str) -> None:
            raise AssertionError('Route changes must release only keyboard-owned voices')

    route_playback = RoutePlayback()
    keyboard._playback = route_playback
    set_page(root, "editor")
    qapp.processEvents()
    keyboard.pressPitch(60)

    set_page(root, "launch")
    qapp.processEvents()

    assert route_playback.stop_modes
    assert set(route_playback.stop_modes) == {"safe"}
    assert controller.state.content == original_content
    root.close()


def test_editor_route_restores_input_and_preserves_project_and_playhead(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    content_before = controller.state.content

    set_page(root, "editor")
    qapp.processEvents()
    timeline = root.findChild(QObject, "timeline")
    assert timeline is not None
    timeline.forceActiveFocus()
    qapp.processEvents()
    assert root.property("editorInputEnabled") is True

    key_ons: list[int] = []
    keyboard.keyNoteOn.connect(key_ons.append)
    QTest.keyClick(root, Qt.Key_A)
    qapp.processEvents()
    assert key_ons == [60]

    playback.seek(0.25)
    set_page(root, "launch")
    qapp.processEvents()
    assert playback.positionSeconds == 0.25
    assert controller.state.content == content_before

    set_page(root, "editor")
    qapp.processEvents()
    restored_timeline = root.findChild(QObject, "timeline")
    assert restored_timeline is not None
    restored_timeline.forceActiveFocus()
    qapp.processEvents()
    assert root.property("editorInputEnabled") is True
    assert controller.state.content == content_before
    assert playback.positionSeconds == 0.25
    root.close()


def test_launch_play_opens_template_library_without_creating_project(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    play = root.findChild(QObject, "homeBrowseExamples")
    templates = engine.rootContext().contextProperty("templates")
    assert play is not None
    assert templates is not None
    assert root.property("pageState") == "launch"
    assert controller.currentFilePath == ""

    play.clicked.emit()
    qapp.processEvents()

    assert root.property("pageState") == "templates"
    assert root.findChild(QObject, "welcomeOverlay").property("visible") is False
    assert root.findChild(QObject, "templateMusicLibrary").property("visible") is True
    assert controller.currentFilePath == ""
    root.close()


def test_returning_to_launch_uses_short_transition_and_restores_focus(qapp, tmp_path, qtbot) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    launch = root.findChild(QObject, "welcomeLaunchView")
    primary = root.findChild(QObject, "homePrimaryCreate")
    assert welcome is not None and launch is not None and primary is not None

    set_page(root, "editor")
    qapp.processEvents()
    set_page(root, "launch")
    qapp.processEvents()

    assert welcome.property("returningToLaunch") is True
    assert launch.property("returnAnimationRunning") is True
    assert primary.property("enabled") is False
    assert root.property("editorInputEnabled") is False

    qtbot.waitUntil(
        lambda: launch.property("returnAnimationRunning") is False,
        timeout=1000,
    )
    assert welcome.property("returningToLaunch") is False
    assert primary.property("activeFocus") is True
    assert launch.property("returnAnimationCount") == 1
    root.close()


def test_returning_to_launch_low_motion_only_fades_and_blocks_duplicate_trigger(
    qapp, tmp_path, qtbot
) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    launch = root.findChild(QObject, "welcomeLaunchView")
    appearance = engine.rootContext().contextProperty("appearance")
    assert welcome is not None and launch is not None

    appearance.setMotionLevel("low")
    set_page(root, "editor")
    qapp.processEvents()
    set_page(root, "launch")
    qapp.processEvents()
    assert welcome.property("returningToLaunch") is True
    assert launch.property("returnAnimationMode") == "fade"
    first_count = launch.property("returnAnimationCount")

    set_page(root, "library")
    set_page(root, "launch")
    qapp.processEvents()
    assert launch.property("returnAnimationCount") == first_count

    qtbot.waitUntil(
        lambda: launch.property("returnAnimationRunning") is False,
        timeout=500,
    )
    assert launch.property("returnAnimationCount") == first_count
    root.close()


def test_returning_to_launch_with_motion_disabled_switches_immediately(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    launch = root.findChild(QObject, "welcomeLaunchView")
    appearance = engine.rootContext().contextProperty("appearance")
    assert welcome is not None and launch is not None

    appearance.setDynamicEnabled(False)
    set_page(root, "editor")
    qapp.processEvents()
    set_page(root, "launch")
    qapp.processEvents()

    assert welcome.property("returningToLaunch") is False
    assert launch.property("returnAnimationRunning") is False
    assert launch.property("returnAnimationMode") == "none"
    root.close()


def test_editor_input_predicate_blocks_text_entry_and_open_popups(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    set_page(root, "editor")
    qapp.processEvents()
    timeline = root.findChild(QObject, "timeline")
    search = root.findChild(QObject, "projectRailSearchField")
    popup = root.findChild(QObject, "backgroundSettingsDialog")
    assert timeline is not None and search is not None and popup is not None

    timeline.forceActiveFocus()
    qapp.processEvents()
    assert root.property("editorInputEnabled") is True

    assert QMetaObject.invokeMethod(
        search, "focusInput", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", True),
    )
    qapp.processEvents()
    assert root.property("editorInputEnabled") is False

    timeline.forceActiveFocus()
    popup.open()
    qapp.processEvents()
    assert popup.property("visible") is True
    assert root.property("editorInputEnabled") is False
    popup.close()
    qapp.processEvents()
    root.close()


def test_clicking_piano_roll_releases_project_search_focus(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    set_page(root, "editor")
    qapp.processEvents()

    timeline = root.findChild(QObject, "timeline")
    draw_area = root.findChild(QObject, "drawNoteArea")
    search = root.findChild(QObject, "projectRailSearchField")
    search_input = search.findChild(QObject, "fieldInput")
    assert timeline is not None and draw_area is not None
    assert search is not None and search_input is not None

    assert QMetaObject.invokeMethod(
        search, "focusInput", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", True),
    )
    qapp.processEvents()
    assert search_input.property("activeFocus") is True
    assert root.property("editorInputEnabled") is False

    click_point = draw_area.mapToItem(
        root.contentItem(), QPointF(220, 120)
    ).toPoint()
    QTest.mouseClick(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, click_point
    )
    qapp.processEvents()

    assert search_input.property("activeFocus") is False
    assert timeline.property("activeFocus") is True
    assert root.property("editorInputEnabled") is True
    root.close()


def test_project_search_keeps_text_entry_when_it_owns_focus(qapp, tmp_path) -> None:
    engine, root, controller, playback, keyboard, note = load_launch(qapp, tmp_path)
    set_page(root, "editor")
    qapp.processEvents()

    search = root.findChild(QObject, "projectRailSearchField")
    search_input = search.findChild(QObject, "fieldInput")
    assert search is not None and search_input is not None
    assert QMetaObject.invokeMethod(
        search, "focusInput", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", True),
    )
    qapp.processEvents()

    search_input.setProperty("text", "")
    for key in (Qt.Key_D, Qt.Key_E, Qt.Key_M, Qt.Key_O):
        QTest.keyClick(root, key)
    QTest.keyClick(root, Qt.Key_Return)
    qapp.processEvents()

    assert search_input.property("text") == "demo"
    assert search_input.property("activeFocus") is True
    assert root.property("editorInputEnabled") is False
    root.close()
