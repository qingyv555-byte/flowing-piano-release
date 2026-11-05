from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPointF, Qt
from PySide6.QtGui import QAccessible, QColor
from PySide6.QtQml import QQmlProperty
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest

from ai_piano_studio.domain.note import Note
from tests.qt.qml_test_utils import load_inline_qml
from tests.qt.test_playback import FakePlayer
from tests.qt.test_qml_smoke import QML_DIR, load_shell
from tools.capture_qml_ui import _apply_state, _build_shell


MODAL_SURFACES = (
    "newConfirmDialog",
    "renameProjectDialog",
    "deleteProjectDialog",
    "unsyncedPlaybackDialog",
    "invalidTimelineDialog",
)

PROJECT_ROOT = Path(__file__).parents[2]
HARMONY_CAPTURE_EXPECTATIONS = (
    ("harmony-off", "editor", "off", 0, None),
    ("harmony-preview-collapsed", "editor", "preview", 1, None),
    ("harmony-preview-expanded", "editor", "preview", 1, None),
    ("harmony-preview-temporary", "editor", "preview", 1, None),
    ("harmony-auto", "editor", "auto", 0, None),
    ("harmony-help", "harmonyHelp", "off", 0, "harmony-basics"),
    ("harmony-help-5", "harmonyHelp", "off", 0, "chord-selection"),
    ("harmony-help-11", "harmonyHelp", "off", 0, "climax-design"),
    ("harmony-help-12", "harmonyHelp", "off", 0, "cadence-ending"),
    ("harmony-melody-c-major", "editor", "preview", 1, None),
    ("harmony-melody-a-minor", "editor", "preview", 1, None),
    ("harmony-melody-f-major", "editor", "preview", 1, None),
    ("harmony-melody-cadence", "editor", "preview", 1, None),
)


def _is_descendant(item: QObject | None, ancestor: QObject) -> bool:
    while item is not None:
        if item == ancestor:
            return True
        item = item.parent()
    return False


def _visual_descendants(root_item: QQuickItem):
    pending = list(root_item.childItems())
    while pending:
        item = pending.pop()
        yield item
        pending.extend(item.childItems())


def _visual_item(root_item: QQuickItem, object_name: str) -> QQuickItem:
    for item in [root_item, *_visual_descendants(root_item)]:
        if item.objectName() == object_name:
            return item
    raise AssertionError(f"missing visual item: {object_name}")


def _accessible_name(item: QObject) -> str:
    interface = QAccessible.queryAccessibleInterface(item)
    assert interface is not None
    return interface.text(QAccessible.Text.Name)


def _accessible_description(item: QObject) -> str:
    interface = QAccessible.queryAccessibleInterface(item)
    assert interface is not None
    return interface.text(QAccessible.Text.Description)


def _assert_item_within_window(item: QQuickItem, root) -> None:
    """Assert an item's rendered rectangle is not pushed outside the shell."""
    content = root.contentItem()
    top_left = item.mapToItem(content, QPointF(0, 0))
    bottom_right = item.mapToItem(content, QPointF(item.width(), item.height()))
    assert top_left.x() >= 0
    assert top_left.y() >= 0
    assert bottom_right.x() <= root.width()
    assert bottom_right.y() <= root.height()


def _overlapping_notes() -> tuple[Note, Note]:
    return (
        Note.create(id="first", pitch=60, start_time=0.0, duration=1.0),
        Note.create(id="overlap", pitch=60, start_time=0.5, duration=1.0),
    )


def test_workspace_shell_keeps_only_one_wide_side_panel_when_canvas_is_constrained(qapp) -> None:
    engine, shell = load_inline_qml(
        qapp,
        """
        import QtQuick
        import "."

        WorkspaceShell {
            availableWidth: 1100
            minimumCanvasWidth: 640
        }
        """,
    )

    assert QMetaObject.invokeMethod(
        shell,
        "requestLeftContent",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )

    assert shell.property("leftContentOpen") is False
    assert shell.property("inspectorOpen") is True
    assert shell.property("inspectorLayoutWidth") == 304


def test_workspace_shell_uses_the_icon_rail_at_laptop_width(qapp) -> None:
    engine, shell = load_inline_qml(
        qapp,
        """
        import QtQuick
        import "."

        WorkspaceShell {
            availableWidth: 1366
            minimumCanvasWidth: 640
        }
        """,
    )

    assert QMetaObject.invokeMethod(
        shell,
        "requestLeftContent",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert shell.property("leftContentOpen") is False
    assert shell.property("leftLayoutWidth") == shell.property("leftIconWidth")
    assert shell.property("inspectorOpen") is True


def test_workspace_shell_closed_inspector_releases_width_and_focus_restores_panels(qapp) -> None:
    engine, shell = load_inline_qml(
        qapp,
        """
        import QtQuick
        import "."

        WorkspaceShell {
            availableWidth: 1600
            minimumCanvasWidth: 640
        }
        """,
    )

    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, False),
    )
    assert shell.property("inspectorLayoutWidth") == 0

    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert QMetaObject.invokeMethod(
        shell,
        "requestArrangement",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert QMetaObject.invokeMethod(
        shell,
        "enterFocus",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert shell.property("focusMode") is True
    assert shell.property("leftContentOpen") is False
    assert shell.property("inspectorOpen") is False
    assert shell.property("arrangementOpen") is False

    assert QMetaObject.invokeMethod(
        shell,
        "enterFocus",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, False),
    )
    assert shell.property("focusMode") is False
    assert shell.property("leftContentOpen") is True
    assert shell.property("inspectorOpen") is True
    assert shell.property("arrangementOpen") is True


def test_workspace_shell_drives_editor_panel_widths_without_a_collapsed_strip(qapp, qtbot) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1100)
    root.setHeight(720)
    qapp.processEvents()

    shell = root.findChild(QObject, "workspaceShell")
    rail = root.findChild(QObject, "projectRail")
    inspector = root.findChild(QObject, "inspector")
    timeline = root.findChild(QObject, "timeline")
    assert shell is not None

    assert QMetaObject.invokeMethod(
        shell,
        "requestLeftContent",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, True),
    )
    qapp.processEvents()
    assert rail.property("width") == shell.property("leftIconWidth")
    assert inspector.property("width") == 304

    canvas_with_inspector = timeline.property("width")
    assert QMetaObject.invokeMethod(
        shell,
        "requestInspector",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(bool, False),
    )
    qapp.processEvents()
    assert inspector.property("width") == 0
    assert root.findChild(QObject, "inspectorEdgeAffordance") is None
    qtbot.waitUntil(
        lambda: timeline.property("width") >= canvas_with_inspector + 300,
        timeout=1000,
    )
    assert timeline.property("width") >= canvas_with_inspector + 300
    root.close()


def test_serialized_boundary_jitter_starts_playback_without_overlap_dialog(qapp) -> None:
    notes = (
        Note.create(id="first", pitch=60, start_time=12.954545, duration=0.511365),
        Note.create(id="next", pitch=60, start_time=13.465909, duration=0.170455),
    )
    player = FakePlayer()
    engine, root, controller = load_shell(qapp, notes=notes, player=player)

    root.togglePlayback()
    qapp.processEvents()

    assert root.findChild(QObject, "overlapPlaybackDialog") is None
    assert player.played == [controller.state.playback_notes]
    root.close()


@pytest.mark.parametrize("trigger", ["toggle", "button", "space"])
def test_overlapping_notes_start_playback_without_confirmation(qapp, trigger) -> None:
    player = FakePlayer()
    engine, root, controller = load_shell(qapp, notes=_overlapping_notes(), player=player)
    assert controller.validateTimeline()["conflictCount"] == 1
    original_notes = controller.state.active_notes

    if trigger == "toggle":
        root.togglePlayback()
    elif trigger == "button":
        root.findChild(QObject, "transportPlayPauseButton").clicked.emit()
    else:
        root.contentItem().forceActiveFocus()
        QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()

    assert player.played == [controller.state.playback_notes]
    assert controller.state.active_notes == original_notes
    assert root.findChild(QObject, "overlapPlaybackDialog") is None
    for object_name in MODAL_SURFACES:
        assert root.findChild(QObject, object_name).property("opened") is False
    root.close()


def test_modal_dialogs_trap_tab_and_restore_the_opener(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    opener = root.findChild(QObject, "saveButton")
    opener.forceActiveFocus()

    for object_name in MODAL_SURFACES:
        dialog = root.findChild(QObject, object_name)
        assert dialog is not None, object_name
        root.openSurface(dialog, opener)
        qapp.processEvents()
        assert dialog.property("opened") is True
        assert _is_descendant(root.property("activeFocusItem"), dialog)
        for _ in range(8):
            QTest.keyClick(root, Qt.Key_Tab)
            qapp.processEvents()
            assert _is_descendant(root.property("activeFocusItem"), dialog)
        QTest.keyClick(root, Qt.Key_Backtab)
        qapp.processEvents()
        assert _is_descendant(root.property("activeFocusItem"), dialog)
        dialog.close()
        qapp.processEvents()
        assert opener.property("activeFocus") is True

    root.close()


def test_dialogs_use_theme_tokens_and_appearance_restores_focus(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1920)
    qapp.processEvents()
    opener = root.findChild(QObject, "moreMenuButton")

    dialog = root.findChild(QObject, "deleteProjectDialog")
    opener.forceActiveFocus()
    root.openSurface(dialog, opener)
    qapp.processEvents()
    assert dialog.property("opened") is True
    assert QQmlProperty(dialog, "background.color").read() == QColor("#0E2032")
    assert QQmlProperty(dialog, "background.radius").read() == pytest.approx(8)

    scrim = root.findChild(QObject, "modalScrim")
    if scrim is not None:
        assert scrim.property("visible") is True
        assert scrim.property("color") == QColor("#A6000000")
    else:
        source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
        assert source.count('objectName: "modalScrim"') == 1

    delete_action = root.findChild(QObject, "projectDeleteConfirmAction")
    assert delete_action.property("visible") is True
    assert delete_action.property("text") == "移动到回收站"
    assert delete_action.property("variant") == "danger"
    assert delete_action.property("semanticKind") == "danger"
    assert _accessible_name(delete_action) == "移动到回收站"
    dialog.close()
    qapp.processEvents()
    assert opener.property("activeFocus") is True

    popup = root.findChild(QObject, "backgroundSettingsDialog")
    opener.forceActiveFocus()
    root.openSurface(popup, opener)
    qapp.processEvents()
    assert popup.property("activeFocus") is True or _is_descendant(
        root.property("activeFocusItem"), popup
    )
    popup.close()
    qapp.processEvents()
    assert opener.property("activeFocus") is True
    assert popup.property("modal") is True
    assert root.findChild(QObject, "backgroundMotionPicker") is not None
    assert root.findChild(QObject, "focusModeToggle") is None
    root.close()


def test_background_settings_and_autosave_entries_fit_1280_by_720(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1280)
    root.setHeight(720)
    qapp.processEvents()
    save_preferences = engine.rootContext().contextProperty("savePreferences")
    popup = root.findChild(QObject, "backgroundSettingsDialog")
    entry = root.findChild(QObject, "moreBackgroundSettingsAction")
    autosave_entry = root.findChild(QObject, "moreAutosaveAction")

    assert entry.property("text") == "背景设置"
    assert autosave_entry.property("text") == "自动保存 · 关"
    entry.triggered.emit()
    qapp.processEvents()

    assert popup.property("opened") is True
    assert popup.property("modal") is True
    assert popup.property("width") >= 700
    assert popup.property("x") >= 0
    assert popup.property("y") >= 0
    assert popup.property("x") + popup.property("width") <= root.width()
    assert popup.property("y") + popup.property("height") <= root.height()

    motion = root.findChild(QObject, "backgroundMotionPicker")
    upload = root.findChild(QObject, "customBackgroundUploadButton")
    _assert_item_within_window(motion, root)
    _assert_item_within_window(upload, root)

    autosave_entry.triggered.emit()
    qapp.processEvents()
    assert save_preferences.autosaveEnabled is True
    assert autosave_entry.property("text") == "自动保存 · 开"

    popup.close()
    root.close()


def test_editor_settings_background_section_remains_reachable_at_minimum_size(qapp) -> None:
    engine, root, _controller = load_shell(qapp)
    root.setWidth(1000)
    root.setHeight(640)
    qapp.processEvents()

    popup = root.findChild(QObject, "backgroundSettingsDialog")
    opener = root.findChild(QObject, "moreMenuButton")
    upload_button = root.findChild(QQuickItem, "customBackgroundUploadButton")

    root.openAppearancePopup(opener)
    qapp.processEvents()
    QTest.qWait(50)

    assert popup.property("opened") is True
    assert upload_button.property("visible") is True
    _assert_item_within_window(upload_button, root)
    assert popup.property("modal") is True
    assert popup.property("x") >= 0
    assert popup.property("y") >= 0

    popup.close()
    root.close()


@pytest.mark.parametrize("width,height", [(1280, 720), (1920, 1080)])
def test_background_settings_dialog_does_not_resize_the_piano_roll(
    qapp, width, height
) -> None:
    """Opening background settings must keep Piano Roll geometry stable."""
    engine, root, _controller = load_shell(qapp)
    root.setWidth(width)
    root.setHeight(height)
    qapp.processEvents()

    timeline = root.findChild(QQuickItem, "timeline")
    popup = root.findChild(QObject, "backgroundSettingsDialog")
    entry = root.findChild(QObject, "moreBackgroundSettingsAction")
    dock = root.findChild(QQuickItem, "appearanceDockHost")
    assert timeline is not None and popup is not None and entry is not None
    assert dock is None

    timeline_width_before = timeline.width()
    entry.triggered.emit()
    qapp.processEvents()
    QTest.qWait(50)

    assert popup.property("opened") is True
    assert timeline.width() == pytest.approx(timeline_width_before)
    assert popup.property("modal") is True
    assert popup.property("x") >= 0
    assert popup.property("y") >= 0
    assert popup.property("x") + popup.property("width") <= root.width()
    assert popup.property("y") + popup.property("height") <= root.height()
    preview = root.findChild(QQuickItem, "backgroundSettingsPreview")
    assert preview is not None
    _assert_item_within_window(preview, root)

    popup.close()
    qapp.processEvents()
    QTest.qWait(50)
    assert timeline.width() == pytest.approx(timeline_width_before)
    root.close()


def test_main_owns_one_feedback_host_and_classifies_known_messages(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    hosts = [
        item for item in root.findChildren(QObject)
        if item.objectName() == "feedbackHost"
    ]
    assert len(hosts) == 1
    host = hosts[0]

    controller.feedbackRequested.emit("无法移动工程到回收站")
    qapp.processEvents()
    assert host.property("kind") == "danger"
    assert host.property("message") == "无法移动工程到回收站"

    controller.feedbackRequested.emit("已创建副本：晨光旋律")
    qapp.processEvents()
    assert host.property("kind") == "success"
    root.close()


def test_export_feedback_uses_compact_nonblocking_toast(qapp, qtbot) -> None:
    engine, root, controller = load_shell(qapp)
    playback = engine.rootContext().contextProperty("playback")
    host = root.findChild(QObject, "feedbackHost")
    surface = root.findChild(QObject, "feedbackSurface")
    close_button = root.findChild(QObject, "feedbackCloseButton")

    assert close_button is not None
    for export_signal, path in (
            (controller.projectExported, r"C:\exports\p1.aipiano"),
            (controller.midiExported, r"C:\exports\p1.mid"),
            (playback.audioExported, r"C:\exports\p1.wav")):
        export_signal.emit(path)
        qapp.processEvents()

        assert host.property("persistent") is False
        assert host.property("dismissAfterMs") == 2000
        assert host.property("dismissible") is True
        assert host.property("shownAsBanner") is False
        assert surface.property("width") < root.width()
        assert close_button.property("visible") is True
        assert QMetaObject.invokeMethod(close_button, "activate", Qt.DirectConnection)
        assert host.property("visible") is False

    controller.midiExported.emit(r"C:\exports\p1.mid")
    qapp.processEvents()
    qtbot.waitUntil(lambda: host.property("visible") is False, timeout=2500)
    root.close()


def test_specialized_feedback_preserves_retry_and_persistence(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    host = root.findChild(QObject, "feedbackHost")

    controller.saveFailed.emit("backend failure")
    controller.feedbackRequested.emit("无法开始保存")
    qapp.processEvents()
    assert host.property("kind") == "danger"
    assert host.property("actionLabel") == "重试"
    assert host.property("persistent") is False

    controller.recycleFinished.emit(1, 1)
    controller.feedbackRequested.emit("已移动 1 个工程到回收站，1 个未能移动")
    qapp.processEvents()
    assert host.property("kind") == "warning"
    assert host.property("persistent") is True
    assert host.property("actionLabel") == "重试"
    root.close()


def test_timeline_and_minimap_use_tokens_and_chinese_terms(qapp) -> None:
    # Recording UI assertions do not require an operating-system audio stream.
    engine, root, controller = load_shell(qapp, player=FakePlayer())
    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline.property("color") == QColor("#091827")

    marquee = _visual_item(timeline, "marqueeSelection")
    assert marquee.property("color") == QColor("#7BE7FF")
    assert marquee.property("opacity") == pytest.approx(0.15)
    assert QQmlProperty(marquee, "border.color").read() == QColor("#7BE7FF")

    selected_note = _visual_item(timeline, "noteItem-demo")
    assert QQmlProperty(selected_note, "border.width").read() == pytest.approx(2)
    assert QQmlProperty(selected_note, "border.color").read() == QColor("#A8F1FF")

    piano_key = _visual_item(timeline, "pianoKey-60")
    keyboard = engine.rootContext().contextProperty("keyboard")
    keyboard.keyNoteOn.emit(60)
    qapp.processEvents()
    assert piano_key.property("color") == QColor("#9DEBFF")
    keyboard.keyNoteOff.emit(60)

    keyboard.startRecording()
    qapp.processEvents()
    badge = root.findChild(QQuickItem, "recordingStatusBadge")
    lane = root.findChild(QQuickItem, "recordingLane")
    assert badge.property("visible") is True
    assert badge.property("color") == QColor("#FF5F83")
    assert _accessible_name(badge).startswith("正在录制")
    assert lane.property("visible") is True
    assert QQmlProperty(lane, "border.color").read() == QColor("#FF5F83")
    assert any(
        "正在录制" in str(item.property("text"))
        for item in _visual_descendants(lane)
        if item.property("text") is not None
    )
    keyboard.stopRecording()

    mini_map = root.findChild(QQuickItem, "miniMap")
    assert mini_map.property("visible") is True
    assert mini_map.property("width") == pytest.approx(280)
    assert mini_map.property("height") == pytest.approx(160)
    title = _visual_item(mini_map, "miniMapTitle")
    assert title.property("visible") is True
    assert title.property("text") == "缩略图"
    assert title.property("color") == QColor(220, 232, 245, round(0.88 * 255))

    viewport = _visual_item(mini_map, "miniMapViewport")
    assert viewport.property("color") == QColor.fromRgbF(135 / 255, 235 / 255, 255 / 255, 0.06)
    assert viewport.property("opacity") == pytest.approx(1.0)
    assert QQmlProperty(viewport, "border.color").read() == QColor.fromRgbF(
        210 / 255, 248 / 255, 255 / 255, 0.92
    )

    move_area = _visual_item(mini_map, "miniMapMoveArea")
    resize_handle = _visual_item(mini_map, "miniMapResizeHandle")
    resize_grip = resize_handle.parentItem()
    expected_grip_color = QColor("#49D7FF")
    expected_grip_color.setAlphaF(0.10)
    assert resize_grip.property("color") == expected_grip_color
    assert _accessible_name(move_area) == "拖动缩略图"
    assert _accessible_name(resize_handle) == "调整缩略图大小"

    settings_button = root.findChild(QObject, "miniMapSettingsButton")
    assert settings_button is not None
    settings_action = root.findChild(QObject, "miniMapSettingsAction")
    assert settings_action is not None
    assert _accessible_name(settings_action) == "缩略图设置"
    settings_menu = root.findChild(QObject, "miniMapSettingsMenu")
    assert QMetaObject.invokeMethod(
        settings_menu, "open", Qt.ConnectionType.DirectConnection
    )
    qapp.processEvents()
    assert settings_menu.property("visible") is True
    visible_texts = [
        str(item.property("text"))
        for item in root.findChildren(QObject)
        if item.property("text") is not None and item.property("visible") is True
    ]
    assert "缩略图设置" in visible_texts
    assert "隐藏缩略图" in visible_texts
    assert "恢复默认大小 280 × 160" in visible_texts
    assert all("Mini Map" not in text and "Piano Roll" not in text
               for text in visible_texts)

    accessible_names = []
    for item in root.findChildren(QObject):
        interface = QAccessible.queryAccessibleInterface(item)
        if interface is not None:
            name = interface.text(QAccessible.Text.Name)
            if name:
                accessible_names.append(name)
    assert "定位钢琴卷帘视图" in accessible_names
    assert all("Mini Map" not in name and "Piano Roll" not in name
               for name in accessible_names)
    settings_menu.close()
    root.close()


def test_piano_roll_pitch_gradient_and_floating_minimap_visual_language(qapp) -> None:
    notes = (
        Note.create(id="low", pitch=36, start_time=0.0, duration=0.5),
        Note.create(id="mid", pitch=60, start_time=1.0, duration=0.5),
        Note.create(id="high", pitch=84, start_time=2.0, duration=0.5),
    )
    engine, root, controller = load_shell(qapp, notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setNoteColorMode("pitch")
    qapp.processEvents()
    timeline = root.findChild(QQuickItem, "timeline")

    assert timeline.property("color") == QColor("#091827")
    assert _visual_item(timeline, "noteItem-low").property("color") == QColor("#4B86FF")
    assert _visual_item(timeline, "noteItem-mid").property("color") == QColor("#34CFFF")
    assert _visual_item(timeline, "noteItem-high").property("color") == QColor("#42E7C9")

    selected = _visual_item(timeline, "noteItem-mid")
    assert QQmlProperty(selected, "border.color").read() == QColor("#A8F1FF")
    assert _visual_item(timeline, "pianoKey-60").property("color") == QColor("#C8D5E2")
    assert _visual_item(timeline, "playheadGlow").property("color") == QColor.fromRgbF(
        242 / 255, 184 / 255, 75 / 255, 0.24
    )

    mini_map = root.findChild(QQuickItem, "miniMap")
    assert mini_map.property("color") == QColor.fromRgbF(10 / 255, 20 / 255, 32 / 255, 0.72)
    viewport = _visual_item(mini_map, "miniMapViewport")
    assert viewport.property("color") == QColor.fromRgbF(135 / 255, 235 / 255, 255 / 255, 0.06)
    root.close()


def test_reactive_visual_layer_is_non_interactive_and_has_no_note_fallback(qapp) -> None:
    notes = (
        Note.create(id="reactive-low", pitch=42, start_time=0.0, duration=1.0),
        Note.create(id="reactive-high", pitch=82, start_time=0.0, duration=1.0),
    )
    player = FakePlayer()
    engine, root, controller = load_shell(qapp, notes=notes, player=player)
    timeline = root.findChild(QQuickItem, "timeline")
    reactive = _visual_item(timeline, "reactiveRhythmLayer")

    assert reactive.property("enabled") is False
    assert reactive.property("visible") is False
    assert reactive.property("smoothedEnergy") == pytest.approx(0.0)

    player.playingChanged.emit(True)
    player.positionChanged.emit(0.25)
    qapp.processEvents()
    assert reactive.property("visible") is False
    assert reactive.property("targetEnergy") == 0
    assert reactive.property("pitchBand") == pytest.approx(0.5)

    scene = engine.rootContext().contextProperty("appearance")
    scene.setFocusModeEnabled(True)
    qapp.processEvents()
    assert reactive.property("visible") is False
    scene.setFocusModeEnabled(False)
    root.close()


def test_inspector_uses_compact_parameter_definitions_and_collapses_cleanly(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    inspector = root.findChild(QObject, "inspector")
    timeline = root.findChild(QObject, "timeline")
    assert inspector.property("width") == 304
    expanded_canvas_width = timeline.property("width")
    inspector.setProperty("collapsed", True)
    qapp.processEvents()
    assert inspector.property("width") == 0
    assert timeline.property("width") >= expanded_canvas_width + 300
    assert root.findChild(QObject, "inspectorEdgeAffordance") is None

    experience = engine.rootContext().contextProperty("experience")
    experience.setMode("advanced")
    inspector.setProperty("advancedShown", True)
    qapp.processEvents()
    texts = {
        str(item.property("text"))
        for item in inspector.findChildren(QObject)
        if item.property("text") is not None
    }
    for expected in (
        "音高 · 音符高低", "位置 · 时间位置（Tick）",
        "长度 · 持续时长（Duration）", "力度 · 音符强弱",
    ):
        assert expected in texts
    assert root.findChild(QObject, "inspectorNarrowSummary") is None
    for object_name in (
        "inspectorPerformanceSection", "inspectorTimeSection",
        "inspectorHelpSection",
    ):
        assert root.findChild(QObject, object_name) is None
    assert root.findChild(QObject, "inspectorFeedback") is None
    root.close()


def test_status_and_transport_have_stable_semantic_regions(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    status = root.findChild(QQuickItem, "statusBar")
    transport = root.findChild(QQuickItem, "bottomTransportBar")
    timeline = root.findChild(QQuickItem, "timeline")
    record = root.findChild(QObject, "recordButton")

    assert status.property("embedded") is True
    assert status.height() <= transport.height()
    assert transport.property("height") == 64
    status_top = status.mapToScene(QPointF(0, 0)).y()
    status_bottom = status.mapToScene(QPointF(0, status.height())).y()
    transport_top = transport.mapToScene(QPointF(0, 0)).y()
    transport_bottom = transport.mapToScene(QPointF(0, transport.height())).y()
    assert status_top >= transport_top
    assert status_bottom <= transport_bottom
    timeline_bottom = timeline.mapToScene(QPointF(0, timeline.height())).y()
    assert timeline_bottom == pytest.approx(transport_top)
    assert root.findChild(QObject, "statusContextSlot") is not None
    assert root.findChild(QObject, "statusTemporarySlot") is not None
    assert root.findChild(QObject, "statusPersistentSlot") is not None
    assert record.property("semanticKind") == "record"
    root.close()


def test_space_does_not_toggle_playback_while_text_input_has_focus(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    playback = engine.rootContext().contextProperty("playback")
    search = root.findChild(QObject, "projectRailSearchField")

    search.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert playback.playing is False
    root.close()


def test_status_sync_states_use_info_warning_success_colors(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    status = root.findChild(QObject, "statusBar")
    sync = root.findChild(QObject, "timelineSyncStatus")

    status.setProperty("syncState", "syncing")
    status.setProperty("unsyncedCount", 0)
    qapp.processEvents()
    assert sync.property("color") == QColor("#66D9F4")

    status.setProperty("syncState", "pending")
    status.setProperty("unsyncedCount", 1)
    qapp.processEvents()
    assert sync.property("color") == QColor("#F2B84B")

    status.setProperty("syncState", "synced")
    status.setProperty("unsyncedCount", 0)
    qapp.processEvents()
    assert sync.property("color") == QColor("#3FD0A8")
    root.close()


def test_top_bar_extracts_commands_and_uses_overflow(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    command_bars = [
        item for item in root.findChildren(QObject)
        if item.objectName() == "commandBar"
    ]
    assert len(command_bars) == 1
    top_bar = command_bars[0]
    assert top_bar.metaObject().className().startswith("EditorTopBar")

    root.setWidth(1280)
    qapp.processEvents()
    assert root.findChild(QObject, "saveButton").property("visible") is True
    assert root.findChild(QObject, "exportButton").property("visible") is True
    assert root.findChild(QObject, "undoButton") is not None
    assert root.findChild(QObject, "redoButton") is not None
    assert root.findChild(QObject, "moreMenuButton").property("visible") is True
    # The global beginner/pro switch was removed by the preceding update;
    # display and focus actions are the actual view controls now.
    for name in (
        "moreOpenAction", "moreBackgroundSettingsAction", "moreHarmonyAction",
        "moreFocusModeAction", "moreNoteDisplayAction",
    ):
        assert root.findChild(QObject, name) is not None

    open_action = root.findChild(QObject, "moreOpenAction")
    signal_index = top_bar.metaObject().indexOfSignal("openRequested()")
    assert signal_index >= 0
    open_spy = QSignalSpy(top_bar, top_bar.metaObject().method(signal_index))
    assert QMetaObject.invokeMethod(
        open_action, "triggered", Qt.ConnectionType.DirectConnection
    )
    assert open_spy.count() == 1
    root.close()


def test_transport_status_prioritizes_selection_and_sync_at_1366(qapp) -> None:
    _engine, root, _controller = load_shell(qapp)
    status = root.findChild(QQuickItem, "statusBar")
    context = root.findChild(QQuickItem, "gridLabel")
    sync = root.findChild(QQuickItem, "timelineSyncStatus")
    duration = root.findChild(QQuickItem, "timelineDurationStatus")

    root.setWidth(1920)
    qapp.processEvents()
    assert "已选 1" in context.property("text")
    assert "吸附" in context.property("text")
    assert duration.property("visible") is True

    root.setWidth(1366)
    qapp.processEvents()
    assert status.property("compact") is True
    assert context.property("visible") is True
    assert context.property("text") == "已选 1"
    assert sync.property("visible") is True
    assert duration.property("visible") is False
    assert "网格" in status.property("tooltipText")
    assert "长度" in status.property("tooltipText")
    root.close()


def test_inspector_selection_context_matrix_is_explicit(qapp) -> None:
    engine, root, _controller = load_shell(qapp, selected_ids=frozenset())
    inspector = root.findChild(QObject, "inspector")
    heading = root.findChild(QObject, "inspectorContextHeading")
    empty_state = root.findChild(QObject, "inspectorEmptyState")

    assert inspector.property("contextKind") == "empty"
    assert heading.property("text") == "属性"
    assert empty_state.property("text") == "选择音符或音乐层以编辑参数。"
    assert empty_state.property("visible") is True
    root.close()

    note = Note.create(id="single", pitch=64, start_time=0.5, duration=0.5)
    engine, root, _controller = load_shell(qapp, notes=(note,))
    inspector = root.findChild(QObject, "inspector")
    heading = root.findChild(QObject, "inspectorContextHeading")

    assert inspector.property("contextKind") == "note"
    assert heading.property("text") == "单个音符"
    assert root.findChild(QObject, "selectionSection").property("visible") is True
    root.close()

    notes = (
        Note.create(id="multi-a", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="multi-b", pitch=67, start_time=1.0, duration=1.0),
    )
    engine, root, _controller = load_shell(qapp, notes=notes)
    inspector = root.findChild(QObject, "inspector")
    heading = root.findChild(QObject, "inspectorContextHeading")

    assert inspector.property("contextKind") == "multi"
    assert heading.property("text") == "多个音符"
    assert root.findChild(QObject, "selectionSection").property("visible") is True
    root.close()


def test_selection_action_bar_has_one_stable_direct_audition_action(qapp) -> None:
    _engine, root, _controller = load_shell(qapp)

    assert root.findChild(QObject, "auditionSelectionButton") is not None
    assert root.findChild(QObject, "selectionMoveToLayerButton") is not None
    assert root.findChild(QObject, "selectionCreateLayerButton") is not None
    assert root.findChild(QObject, "selectionPlayButton") is None
    root.close()


def test_top_bar_groups_workspace_commands_and_exposes_arrangement_toggle(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    top_bar = root.findChild(QObject, "commandBar")
    arrangement = root.findChild(QObject, "arrangementToggle")

    for group_name in ("projectTheoryGroup", "actionGroup", "fileGroup"):
        assert root.findChild(QObject, group_name) is not None
    assert arrangement is not None
    assert arrangement.property("text") == "编排"
    signal_index = top_bar.metaObject().indexOfSignal("arrangementRequested(bool)")
    assert signal_index >= 0
    spy = QSignalSpy(top_bar, top_bar.metaObject().method(signal_index))
    arrangement.clicked.emit()
    assert spy.count() == 1
    assert spy.at(0)[0] is True
    root.close()


def test_project_rail_uses_theme_widths_and_blocks_switching(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    rail = root.findChild(QObject, "projectRail")
    shell = root.findChild(QObject, "workspaceShell")

    root.setWidth(1920)
    qapp.processEvents()
    assert rail.property("width") == 224

    root.setWidth(1280)
    qapp.processEvents()
    assert rail.property("width") == shell.property("leftIconWidth")
    assert root.findChild(QObject, "backToLibraryButton") is not None
    assert root.findChild(QObject, "libraryNewProjectButton") is not None
    assert root.findChild(QObject, "projectRailOpenButton") is not None
    assert root.findChild(QObject, "projectRailSearchField") is not None
    root.close()


@pytest.mark.parametrize(
    "width,height",
    [(1280, 720), (1366, 768), (1920, 1080), (2560, 1440)],
)
def test_editor_regions_fit_supported_viewports(qapp, width, height) -> None:
    """Catch editor layout regressions that clip required command regions."""
    engine, root, controller = load_shell(qapp)
    root.setWidth(width)
    root.setHeight(height)
    qapp.processEvents()

    rail = root.findChild(QObject, "projectRail")
    shell = root.findChild(QObject, "workspaceShell")
    timeline = root.findChild(QObject, "timeline")
    inspector = root.findChild(QObject, "inspector")
    transport = root.findChild(QObject, "bottomTransportBar")
    assert rail.property("width") == (224 if width > 1366 else shell.property("leftIconWidth"))
    assert inspector.property("width") == 304
    assert timeline.property("width") >= 720
    rail_right = rail.mapToScene(QPointF(rail.property("width"), 0)).x()
    timeline_left = timeline.mapToScene(QPointF(0, 0)).x()
    timeline_right = timeline.mapToScene(QPointF(timeline.property("width"), 0)).x()
    inspector_left = inspector.mapToScene(QPointF(0, 0)).x()
    assert timeline_left >= rail_right
    assert inspector_left >= timeline_right
    assert transport.property("height") == 64
    assert transport.property("y") + transport.property("height") <= height
    for object_name in ("saveButton", "exportButton", "transportPlayPauseButton"):
        control = root.findChild(QQuickItem, object_name)
        assert control.property("visible") is True
        _assert_item_within_window(control, root)
    root.close()


@pytest.mark.parametrize(
    "state,page_state,mode,preview_count,help_section",
    HARMONY_CAPTURE_EXPECTATIONS,
)
def test_harmony_capture_states_apply_deterministic_visible_state(
    qapp, tmp_path, state, page_state, mode, preview_count, help_section
) -> None:
    temporary = tmp_path / state
    temporary.mkdir()
    app, engine, root, controller, playback, keyboard = _build_shell(
        PROJECT_ROOT, temporary, False, False, state
    )
    root.setWidth(1366)
    root.setHeight(768)
    root.setVisible(True)
    qapp.processEvents()
    harmony = engine.rootContext().contextProperty("harmony")
    help_content = engine.rootContext().contextProperty("harmonyHelp")
    try:
        _apply_state(state, root, engine, controller, playback, keyboard)
        qapp.processEvents()

        assert root.property("pageState") == page_state
        assert harmony.mode == mode
        assert harmony.previewModel.count == preview_count
        if help_section is not None:
            assert help_content.currentSectionId == help_section
        if state == "harmony-preview-collapsed":
            assert _visual_item(
                root.contentItem(), "harmonySuggestionTile-0"
            ).property("expanded") is False
        if state in {"harmony-preview-expanded", "harmony-preview-temporary"}:
            assert _visual_item(
                root.contentItem(), "harmonySuggestionTile-0"
            ).property("expanded") is True
        if state == "harmony-preview-temporary":
            _visual_item(root.contentItem(), "harmonyTemporaryBadge-0")
    finally:
        harmony.close()
        root.close()
        engine.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("width,height", [(1280, 720), (1366, 768), (1920, 1080)])
def test_harmony_preview_controls_fit_supported_viewports(
    qapp, tmp_path, width, height
) -> None:
    temporary = tmp_path / f"{width}x{height}"
    temporary.mkdir()
    app, engine, root, controller, playback, keyboard = _build_shell(
        PROJECT_ROOT, temporary, False, False, "harmony-preview-expanded"
    )
    root.setWidth(width)
    root.setHeight(height)
    root.setVisible(True)
    qapp.processEvents()
    harmony = engine.rootContext().contextProperty("harmony")
    try:
        _apply_state(
            "harmony-preview-expanded", root, engine, controller, playback, keyboard
        )
        qapp.processEvents()

        assert root.findChild(QObject, "moreHarmonyAction") is not None
        for object_name in (
            "moreMenuButton", "harmonySuggestionLane", "harmonySuggestionTile-0",
        ):
            _assert_item_within_window(
                _visual_item(root.contentItem(), object_name), root
            )
    finally:
        harmony.close()
        root.close()
        engine.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    "state", ("harmony-off", "harmony-preview-expanded", "harmony-help")
)
def test_harmony_capture_cli_accepts_a_nonblank_150_percent_frame(
    tmp_path, state
) -> None:
    output = tmp_path / f"{state}-1366x768-150.png"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "capture_qml_ui.py"),
            "--project-root",
            str(PROJECT_ROOT),
            "--output",
            str(output),
            "--width",
            "1366",
            "--height",
            "768",
            "--state",
            state,
            "--scale",
            "1.5",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    assert output.stat().st_size > 0
