import pytest

from PySide6.QtCore import QObject, QPointF, Q_ARG, QMetaObject, Qt
from PySide6.QtTest import QSignalSpy, QTest

from ai_piano_studio.domain.note import Note
from tests.qt.test_qml_smoke import _visual_item, load_shell


def test_arrangement_is_hidden_by_default_and_releases_all_height(qapp) -> None:
    _engine, root, _controller = load_shell(qapp)
    panel = root.findChild(QObject, "arrangementPanel")

    assert panel is not None
    assert panel.property("visible") is False
    assert panel.property("height") == 0
    root.close()


def test_arrangement_height_tracks_layer_rows_and_caps_before_scrolling(
    qapp, qtbot
) -> None:
    """The drawer must not outline or reserve unused space below its layers."""
    _engine, root, controller = load_shell(qapp)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()

    panel = _visual_item(root.contentItem(), "arrangementPanel")
    lanes = _visual_item(root.contentItem(), "arrangementLaneList")
    viewport = _visual_item(root.contentItem(), "arrangementViewport")
    qtbot.waitUntil(lambda: lanes.property("contentHeight") > 0)

    chrome_height = 8 + 38 + 24
    assert panel.height() == pytest.approx(
        chrome_height + lanes.property("contentHeight")
    )
    assert lanes.height() == pytest.approx(lanes.property("contentHeight"))
    assert viewport.height() == pytest.approx(lanes.property("contentHeight"))

    for index in range(8):
        controller.addMusicLayer(f"层 {index + 2}", "other")
    qapp.processEvents()
    qtbot.waitUntil(lambda: controller.layerModel.rowCount() == 9)

    assert panel.height() <= panel.property("panelHeight")
    assert lanes.property("contentHeight") > lanes.height()
    assert viewport.height() == pytest.approx(lanes.height())
    root.close()


def test_arrangement_toggle_shares_timeline_navigation_and_persists_height(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    panel = root.findChild(QObject, "arrangementPanel")
    timeline = root.findChild(QObject, "timeline")
    toggle = root.findChild(QObject, "arrangementToggle")

    toggle.clicked.emit()
    qapp.processEvents()
    assert panel.property("visible") is True
    assert panel.property("height") == panel.property("contentDrivenHeight")
    assert panel.property("height") < experience.arrangementHeight
    assert panel.property("layerCount") == controller.layerModel.rowCount()

    timeline.setProperty("viewStartSeconds", 2.0)
    qapp.processEvents()
    assert panel.property("contentX") == 2.0 * timeline.property("pixelsPerSecond")

    panel.contentXRequested.emit(320.0)
    qapp.processEvents()
    assert timeline.property("viewStartSeconds") == 320.0 / timeline.property("pixelsPerSecond")

    assert QMetaObject.invokeMethod(
        panel, "commitHeight", Qt.ConnectionType.DirectConnection,
        Q_ARG("double", 999.0),
    )
    qapp.processEvents()
    assert experience.arrangementHeight == 360.0
    root.close()


def test_arrangement_is_a_bottom_drawer_below_the_piano_roll(qapp) -> None:
    _engine, root, _controller = load_shell(qapp)
    root.setWidth(1600)
    root.setHeight(900)
    panel = root.findChild(QObject, "arrangementPanel")
    timeline = root.findChild(QObject, "timeline")
    toggle = root.findChild(QObject, "arrangementToggle")

    toggle.clicked.emit()
    qapp.processEvents()

    timeline_bottom = timeline.mapToScene(QPointF(0, timeline.property("height"))).y()
    panel_top = panel.mapToScene(QPointF(0, 0)).y()
    assert panel_top >= timeline_bottom - 1
    root.close()


def test_arrangement_add_layer_is_visible_button_without_inline_hint(qapp) -> None:
    _engine, root, _controller = load_shell(qapp)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()

    button = root.findChild(QObject, "arrangementAddLayer")
    assert button is not None
    assert button.property("visible") is True
    assert button.property("variant") == "secondary"
    background = button.findChild(QObject, "buttonBackground")
    assert background.property("color").alpha() > 0
    assert root.findChild(QObject, "arrangementSingleLayerHint") is None
    root.close()


def test_arrangement_height_previews_live_before_persistence(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    panel = root.findChild(QObject, "arrangementPanel")
    original_height = experience.arrangementHeight
    for index in range(8):
        controller.addMusicLayer(f"层 {index + 2}", "other")
    qapp.processEvents()

    assert QMetaObject.invokeMethod(
        panel,
        "previewHeight",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("double", 248.0),
    )
    qapp.processEvents()

    assert panel.property("effectivePanelHeight") == 248.0
    assert experience.arrangementHeight == original_height

    assert QMetaObject.invokeMethod(
        panel,
        "commitPreviewHeight",
        Qt.ConnectionType.DirectConnection,
    )
    qapp.processEvents()
    assert experience.arrangementHeight == 248.0
    root.close()


def test_escape_cancels_live_arrangement_resize_without_persisting(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    panel = root.findChild(QObject, "arrangementPanel")
    original_height = experience.arrangementHeight
    for index in range(8):
        controller.addMusicLayer(f"层 {index + 2}", "other")
    qapp.processEvents()

    assert QMetaObject.invokeMethod(
        panel,
        "previewHeight",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("double", 320.0),
    )
    QTest.keyClick(root, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert panel.property("previewedHeight") == -1
    assert panel.property("effectivePanelHeight") == original_height
    assert experience.arrangementHeight == original_height
    root.close()


def test_clicking_arrangement_layer_only_selects_and_preserves_time_view(qapp, qtbot) -> None:
    engine, root, controller = load_shell(qapp)
    root.setWidth(1600)
    root.setHeight(900)
    timeline = root.findChild(QObject, "timeline")
    panel = root.findChild(QObject, "arrangementPanel")
    toggle = root.findChild(QObject, "arrangementToggle")
    playback = engine.rootContext().contextProperty("playback")

    toggle.clicked.emit()
    timeline.setProperty("viewStartSeconds", 2.0)
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), "arrangementLayerHitArea-layer-default"
        ) is not None
    )
    hit_area = _visual_item(
        root.contentItem(), "arrangementLayerHitArea-layer-default"
    )
    before_view_start = timeline.property("viewStartSeconds")
    before_playhead = playback.positionSeconds

    point = hit_area.mapToScene(
        QPointF(hit_area.width() - 12, hit_area.height() / 2)
    ).toPoint()
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point,
    )
    qapp.processEvents()

    assert controller.activeLayerId == "layer-default"
    assert timeline.property("viewStartSeconds") == before_view_start
    assert playback.positionSeconds == before_playhead
    assert panel.property("contentX") == before_view_start * timeline.property(
        "pixelsPerSecond"
    )
    root.close()


def test_arrangement_viewport_drag_scrolls_timeline_continuously(qapp, qtbot) -> None:
    notes = (
        Note.create(id="early", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="late", pitch=64, start_time=40.0, duration=0.5),
    )
    _engine, root, _controller = load_shell(qapp, notes=notes)
    root.setWidth(1440)
    root.setHeight(900)
    timeline = root.findChild(QObject, "timeline")
    panel = root.findChild(QObject, "arrangementPanel")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), "arrangementViewportDragHandle"
        ) is not None
    )
    viewport = _visual_item(root.contentItem(), "arrangementViewportDragHandle")
    signal_index = panel.metaObject().indexOfSignal("contentXRequested(double)")
    spy = QSignalSpy(panel, panel.metaObject().method(signal_index))
    start = viewport.mapToScene(
        QPointF(viewport.width() / 2, viewport.height() / 2)
    ).toPoint()

    QTest.mousePress(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(root, start + QPointF(60, 0).toPoint(), delay=10)
    qapp.processEvents()

    assert spy.count() >= 1
    assert timeline.property("viewStartSeconds") > 0
    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start + QPointF(60, 0).toPoint(),
    )
    root.close()


def test_layer_reorder_previews_live_and_commits_one_history_entry(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), "arrangementReorderHandle-layer-default"
        ) is not None
    )
    handle = _visual_item(
        root.contentItem(), "arrangementReorderHandle-layer-default"
    )
    panel = root.findChild(QObject, "arrangementPanel")
    before_depth = controller.historyUndoDepth
    start = handle.mapToScene(
        QPointF(handle.width() / 2, handle.height() / 2)
    ).toPoint()

    QTest.mousePress(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(root, start + QPointF(0, 54).toPoint(), delay=10)
    qapp.processEvents()

    assert panel.property("reorderTargetIndex") == 1
    assert controller.historyUndoDepth == before_depth

    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start + QPointF(0, 54).toPoint(),
    )
    qapp.processEvents()
    assert controller.historyUndoDepth == before_depth + 1
    assert [layer.id for layer in controller.state.layers] == [
        second_id,
        "layer-default",
    ]
    root.close()


def test_layer_reorder_can_start_from_the_row_body(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(root.contentItem(), "musicLayerRow-layer-default")
        is not None
    )
    row = _visual_item(root.contentItem(), "musicLayerRow-layer-default")
    panel = root.findChild(QObject, "arrangementPanel")
    start = row.mapToScene(QPointF(24, row.height() / 2)).toPoint()

    QTest.mousePress(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(root, start + QPointF(0, 54).toPoint(), delay=10)
    qapp.processEvents()

    assert panel.property("reorderTargetIndex") == 1
    QTest.mouseRelease(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start + QPointF(0, 54).toPoint(),
    )
    qapp.processEvents()
    assert [layer.id for layer in controller.state.layers] == [second_id, "layer-default"]
    root.close()


def test_layer_reorder_preview_stays_inside_pinned_or_regular_group(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    pinned_a = controller.addMusicLayer("置顶 A", "melody")
    pinned_b = controller.addMusicLayer("置顶 B", "melody")
    regular_b = controller.addMusicLayer("普通 B", "other")
    controller.setMusicLayerPinned(pinned_a, True)
    controller.setMusicLayerPinned(pinned_b, True)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    panel = root.findChild(QObject, "arrangementPanel")
    qtbot.waitUntil(lambda: panel.property("pinnedCount") == 2)

    regular_handle = _visual_item(
        root.contentItem(), "arrangementReorderHandle-layer-default"
    )
    regular_start = regular_handle.mapToScene(
        QPointF(regular_handle.width() / 2, regular_handle.height() / 2)
    ).toPoint()
    QTest.mousePress(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        regular_start,
    )
    QTest.mouseMove(root, regular_start + QPointF(0, -180).toPoint(), delay=10)
    qapp.processEvents()
    assert panel.property("reorderTargetIndex") == 2
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        regular_start + QPointF(0, -180).toPoint(),
    )

    pinned_handle = _visual_item(
        root.contentItem(), f"arrangementReorderHandle-{pinned_a}"
    )
    pinned_start = pinned_handle.mapToScene(
        QPointF(pinned_handle.width() / 2, pinned_handle.height() / 2)
    ).toPoint()
    QTest.mousePress(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        pinned_start,
    )
    QTest.mouseMove(root, pinned_start + QPointF(0, 180).toPoint(), delay=10)
    qapp.processEvents()
    assert panel.property("reorderTargetIndex") == 1
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        pinned_start + QPointF(0, 180).toPoint(),
    )

    separator = _visual_item(root.contentItem(), "arrangementPinnedSeparator")
    assert separator.property("visible") is True
    assert [layer.id for layer in controller.state.layers[:2]] == [pinned_b, pinned_a]
    assert controller.state.layers[2].pinned is False
    assert regular_b in [layer.id for layer in controller.state.layers[2:]]
    root.close()


def test_arrangement_layer_rows_support_arrow_key_focus(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    first_row = _visual_item(
        root.contentItem(), "musicLayerRow-layer-default"
    )
    second_row = _visual_item(
        root.contentItem(), f"musicLayerRow-{second_id}"
    )

    first_row.forceActiveFocus(Qt.TabFocusReason)
    qapp.processEvents()
    assert first_row.property("activeFocus") is True
    first_row.moveFocusRequested.emit(1)
    qapp.processEvents()

    assert second_row.property("activeFocus") is True
    root.close()


def test_layer_color_menu_opens_lightweight_picker_and_applies_override(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), "musicLayerRow-layer-default"
        ) is not None
    )
    row = _visual_item(root.contentItem(), "musicLayerRow-layer-default")
    popup = root.findChild(QObject, "musicLayerColorPopup")
    before_notes = controller.state.notes
    before_depth = controller.historyUndoDepth

    row.colorRequested.emit("layer-default")
    qapp.processEvents()
    assert popup.property("visible") is True

    panel = root.findChild(QObject, "arrangementPanel")
    assert QMetaObject.invokeMethod(
        panel,
        "applyPendingLayerColor",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(str, "#8B72FF"),
    )
    assert controller.activeLayer["customColor"] == "#8B72FF"
    assert controller.state.notes == before_notes
    assert controller.historyUndoDepth == before_depth + 1
    controller.undo()
    assert controller.activeLayer["customColor"] is None
    root.close()


def test_layer_inline_rename_commits_once_without_a_modal_dialog(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), "musicLayerRow-layer-default"
        ) is not None
    )
    row = _visual_item(root.contentItem(), "musicLayerRow-layer-default")
    field = _visual_item(
        root.contentItem(), "musicLayerRenameField-layer-default"
    )
    before_depth = controller.historyUndoDepth

    assert QMetaObject.invokeMethod(
        row, "beginRename", Qt.ConnectionType.DirectConnection
    )
    field.setProperty("text", "女声主唱")
    assert QMetaObject.invokeMethod(
        row,
        "finishRename",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", True),
    )

    assert controller.state.layer("layer-default").name == "女声主唱"
    assert controller.historyUndoDepth == before_depth + 1
    root.close()


def test_populated_layer_delete_defaults_to_safe_unclassified_move(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), "musicLayerRow-layer-default"
        ) is not None
    )
    row = _visual_item(root.contentItem(), "musicLayerRow-layer-default")
    dialog = root.findChild(QObject, "deleteMusicLayerDialog")

    row.deleteRequested.emit("layer-default")
    qapp.processEvents()
    assert dialog.property("visible") is True

    safe_action = root.findChild(QObject, "moveNotesThenDeleteLayerButton")
    safe_action.clicked.emit()
    qapp.processEvents()

    assert controller.state.notes[0].layer_id == "layer-unclassified"
    assert controller.state.layers[0].name == "未分类"
    controller.undo()
    assert controller.state.notes[0].layer_id == "layer-default"
    root.close()


def test_added_empty_layer_can_be_deleted_through_its_menu_action(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()

    add_button = root.findChild(QObject, "arrangementAddLayer")
    before_add_ids = set(controller.state.layer_ids)
    add_button.clicked.emit()
    qapp.processEvents()
    new_layer_id = next(
        layer_id
        for layer_id in controller.state.layer_ids
        if layer_id not in before_add_ids
    )
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), f"musicLayerRow-{new_layer_id}"
        ) is not None
    )

    row = _visual_item(root.contentItem(), f"musicLayerRow-{new_layer_id}")
    menu_button = _visual_item(
        root.contentItem(), f"musicLayerMenu-{new_layer_id}"
    )
    delete_action = row.findChild(QObject, f"musicLayerDelete-{new_layer_id}")
    assert delete_action is not None
    menu_point = menu_button.mapToScene(
        QPointF(menu_button.width() / 2, menu_button.height() / 2)
    ).toPoint()
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        menu_point,
    )
    qapp.processEvents()
    qtbot.waitUntil(lambda: delete_action.property("visible"))
    before_delete_depth = controller.historyUndoDepth
    delete_point = delete_action.mapToScene(
        QPointF(delete_action.width() / 2, delete_action.height() / 2)
    ).toPoint()
    QTest.mouseClick(
        delete_action.window(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        delete_point,
    )
    qapp.processEvents()

    assert new_layer_id not in controller.state.layer_ids
    assert controller.activeLayerId in controller.state.layer_ids
    assert controller.historyUndoDepth == before_delete_depth + 1
    assert root.findChild(QObject, f"musicLayerRow-{new_layer_id}") is None

    controller.undo()
    qapp.processEvents()
    assert new_layer_id in controller.state.layer_ids
    root.close()
