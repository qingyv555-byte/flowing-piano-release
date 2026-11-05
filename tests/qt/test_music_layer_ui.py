from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
import pytest

from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID
from ai_piano_studio.domain.note import Note
from tests.qt.test_qml_smoke import _visual_item, load_shell


def test_workspace_exposes_operable_music_layer_list(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.setWidth(1920)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()

    layer_list = root.findChild(QObject, "musicLayerList")
    qtbot.waitUntil(lambda: _visual_item(
        root.contentItem(), f"musicLayerRow-{DEFAULT_LAYER_ID}"
    ) is not None)
    row = _visual_item(root.contentItem(), f"musicLayerRow-{DEFAULT_LAYER_ID}")

    assert layer_list is None
    assert row is not None
    assert row.property("accessibleName") == "音乐层：默认音层"
    for suffix in ("Mute", "Solo", "Visible", "Lock", "Menu"):
        assert _visual_item(
            root.contentItem(), f"musicLayer{suffix}-{DEFAULT_LAYER_ID}"
        ) is not None
    root.close()


def test_activating_music_layer_opens_contextual_inspector(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.setWidth(1920)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(lambda: _visual_item(
        root.contentItem(), f"musicLayerRow-{DEFAULT_LAYER_ID}"
    ) is not None)
    row = _visual_item(root.contentItem(), f"musicLayerRow-{DEFAULT_LAYER_ID}")
    inspector = root.findChild(QObject, "inspector")
    heading = root.findChild(QObject, "inspectorContextHeading")

    row.activated.emit(DEFAULT_LAYER_ID)
    qapp.processEvents()

    assert controller.activeLayerId == DEFAULT_LAYER_ID
    assert inspector.property("contextKind") == "layer"
    assert heading.property("text") == "音乐层"
    assert root.findChild(QObject, "musicLayerInspectorSection").property("visible") is True
    assert root.findChild(QObject, "musicLayerColorField") is not None
    assert root.findChild(QObject, "musicLayerRoleControl").property("displayText") == "其他"
    assert root.findChild(QObject, "musicLayerDefaultReleaseControl") is not None
    assert root.findChild(QObject, "musicLayerDefaultReleaseMsField") is not None
    assert root.findChild(QObject, "deleteActiveMusicLayerButton") is None
    root.close()


def test_music_layer_inspector_edits_new_note_release_default(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    root.setWidth(1920)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(lambda: _visual_item(
        root.contentItem(), f"musicLayerRow-{DEFAULT_LAYER_ID}"
    ) is not None)
    _visual_item(
        root.contentItem(), f"musicLayerRow-{DEFAULT_LAYER_ID}"
    ).activated.emit(DEFAULT_LAYER_ID)
    qapp.processEvents()
    control = root.findChild(QObject, "musicLayerDefaultReleaseControl")
    release_ms = root.findChild(QObject, "musicLayerDefaultReleaseMsField")

    assert control.property("currentIndex") == 0
    assert release_ms.property("visible") is False
    control.activated.emit(1)
    qapp.processEvents()
    assert controller.state.layer(DEFAULT_LAYER_ID).default_release_mode == "custom"
    assert release_ms.property("visible") is True
    release_ms.setProperty("text", "420")
    release_ms.editingFinished.emit()
    qapp.processEvents()
    assert controller.state.layer(DEFAULT_LAYER_ID).default_release_ms == 420.0
    root.close()


def test_selection_layer_menu_always_offers_unclassified_fallback(qapp) -> None:
    _engine, root, _controller = load_shell(qapp)

    fallback = root.findChild(QObject, "selectionMoveToUnclassified")

    assert fallback is not None
    assert fallback.property("text") == "未分类（移出当前层）"
    root.close()


def test_piano_roll_uses_layer_visibility_lock_and_color_modes(qapp, qtbot) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "role", "melody")
    qapp.processEvents()
    note = _visual_item(root.contentItem(), "noteItem-demo")

    assert note.property("layerRole") == "melody"
    assert note.property("presentationColor") == QColor("#39C8FF")
    assert note.property("interactive") is True

    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "customColor", "#B764FF")
    qapp.processEvents()
    assert note.property("presentationColor") == QColor("#B764FF")

    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "locked", True)
    qapp.processEvents()
    assert note.property("interactive") is False

    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "visible", False)
    qapp.processEvents()
    assert note.property("visible") is False

    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "visible", True)
    experience.setNoteColorMode("mono")
    qapp.processEvents()
    assert note.property("presentationColor") == QColor("#45D7DD")

    experience.setNoteColorMode("pitch")
    qapp.processEvents()
    assert note.property("presentationColor") != QColor("#45D7DD")
    root.close()


def test_first_drag_of_unlocked_note_in_inactive_layer_moves_it(qapp) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    controller.createNoteAt(240, 144)
    note_id = controller.state.selected_notes[0].id
    controller.synchronizeNow()
    controller.setActiveLayer(DEFAULT_LAYER_ID)
    qapp.processEvents()

    note_item = _visual_item(root.contentItem(), f"noteItem-{note_id}")
    before = next(note for note in controller.state.notes if note.id == note_id)
    before_depth = controller.historyUndoDepth
    start = note_item.mapToScene(
        QPointF(5.0, note_item.height() / 2.0)
    ).toPoint()
    destination = start + QPointF(80.0, 0.0).toPoint()

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

    after = next(note for note in controller.state.notes if note.id == note_id)
    assert controller.activeLayerId == second_id
    assert after.start_time == pytest.approx(before.start_time + 1.0)
    assert controller.historyUndoDepth == before_depth + 1

    controller.undo()
    restored = next(note for note in controller.state.notes if note.id == note_id)
    assert restored.start_time == before.start_time
    root.close()


def test_locked_note_drag_is_consumed_without_preview_or_error(qapp) -> None:
    _engine, root, controller = load_shell(qapp)
    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "locked", True)
    qapp.processEvents()
    note = _visual_item(root.contentItem(), "noteItem-demo")
    feedback = QSignalSpy(controller.feedbackRequested)
    before = next(note for note in controller.state.notes if note.id == "demo")
    undo_depth = controller.historyUndoDepth
    start = note.mapToScene(QPointF(5.0, note.height() / 2.0)).toPoint()
    destination = start + QPointF(80.0, 18.0).toPoint()

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

    assert next(note for note in controller.state.notes if note.id == "demo") == before
    assert controller.historyUndoDepth == undo_depth
    assert feedback.count() == 0
    assert root.findChild(QObject, "timeline").property("gestureFeedbackVisible") is False
    root.close()


def test_locked_composite_drag_is_consumed_without_selection_or_error(qapp) -> None:
    notes = tuple(
        Note.create(id=f"locked-{index}", pitch=60, start_time=index * 0.2, duration=0.18)
        for index in range(3)
    )
    engine, root, controller = load_shell(qapp, notes=notes)
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "locked", True)
    qapp.processEvents()
    group = _visual_item(
        root.contentItem(), "compositeNoteItem-composite:locked-0,locked-1,locked-2"
    )
    feedback = QSignalSpy(controller.feedbackRequested)
    before = controller.state.notes
    start = group.mapToScene(QPointF(6.0, group.height() / 2.0)).toPoint()
    destination = start + QPointF(80.0, 18.0).toPoint()

    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(root, destination, delay=10)
    QTest.mouseRelease(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, destination
    )
    qapp.processEvents()

    assert group.property("layerLocked") is True
    assert controller.state.notes == before
    assert controller.state.selected_ids == frozenset()
    assert feedback.count() == 0
    root.close()


def test_locked_note_in_dense_canvas_cannot_be_selected(qapp) -> None:
    notes = [Note.create(id="target", pitch=60, start_time=1.0, duration=0.5)]
    notes.extend(
        Note.create(
            id=f"far-{index}", pitch=36 + index % 48,
            start_time=20.0 + index * 0.2, duration=0.08,
        )
        for index in range(600)
    )
    _engine, root, controller = load_shell(qapp, notes=tuple(notes))
    controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "locked", True)
    qapp.processEvents()
    timeline = root.findChild(QObject, "timeline")
    canvas = _visual_item(root.contentItem(), "rawNoteCanvas")
    point = canvas.mapToScene(QPointF(
        float(timeline.property("keyWidth"))
            + (1.0 - float(timeline.property("viewStartSeconds")))
            * float(timeline.property("pixelsPerSecond")) + 4.0,
        (int(timeline.property("highestPitch")) - 60)
            * float(timeline.property("rowHeight")) + 6.0,
    )).toPoint()

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()

    assert controller.state.selected_ids == frozenset()
    root.close()


def test_layer_controls_remain_visible_on_inactive_rows_without_hover(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    controller.setActiveLayer(DEFAULT_LAYER_ID)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(lambda: _visual_item(
        root.contentItem(), f"musicLayerRow-{second_id}"
    ) is not None)

    second_row = _visual_item(root.contentItem(), f"musicLayerRow-{second_id}")
    QTest.mouseMove(root, QPointF(root.width() - 8, root.height() - 8).toPoint())
    qapp.processEvents()

    assert second_row.property("active") is False
    for suffix in ("Mute", "Solo", "Visible", "Lock", "Menu"):
        control = _visual_item(
            root.contentItem(), f"musicLayer{suffix}-{second_id}"
        )
        assert control.property("visible") is True
        assert control.width() == pytest.approx(22)

    root.close()


def test_layer_menu_toggles_pinned_state_and_omits_step_reorder_actions(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(lambda: _visual_item(
        root.contentItem(), f"musicLayerRow-{second_id}"
    ) is not None)

    second_row = _visual_item(root.contentItem(), f"musicLayerRow-{second_id}")
    pin_action = second_row.findChild(QObject, f"musicLayerPin-{second_id}")

    assert pin_action is not None
    assert pin_action.property("text") == "置顶"
    assert second_row.findChild(QObject, f"musicLayerMoveUp-{second_id}") is None
    assert second_row.findChild(QObject, f"musicLayerMoveDown-{second_id}") is None

    pin_action.triggered.emit()
    qapp.processEvents()
    assert controller.state.layers[0].id == second_id
    assert controller.state.layers[0].pinned is True
    qtbot.waitUntil(lambda: _visual_item(
        root.contentItem(), f"musicLayerRow-{second_id}"
    ) is not None)
    pinned_row = _visual_item(root.contentItem(), f"musicLayerRow-{second_id}")
    pin_action = pinned_row.findChild(QObject, f"musicLayerPin-{second_id}")
    assert pin_action.property("text") == "取消置顶"

    controller.setMusicLayerProperty(second_id, "locked", True)
    qapp.processEvents()
    assert pinned_row.property("canSelectAll") is False
    assert pinned_row.property("canReceiveSelection") is False
    root.close()


def test_pinning_layer_is_one_history_transition_and_round_trips_undo_redo(qapp) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    before_depth = controller.historyUndoDepth

    assert controller.setMusicLayerPinned(second_id, True) is True
    assert controller.historyUndoDepth == before_depth + 1
    assert [(layer.id, layer.pinned) for layer in controller.state.layers] == [
        (second_id, True),
        (DEFAULT_LAYER_ID, False),
    ]

    controller.undo()
    assert [layer.id for layer in controller.state.layers] == [
        DEFAULT_LAYER_ID, second_id,
    ]
    assert controller.state.layer(second_id).pinned is False

    controller.redo()
    assert controller.state.layers[0].id == second_id
    assert controller.state.layers[0].pinned is True
    root.close()


def test_inactive_layer_menu_button_stays_visible_before_and_under_pointer(qapp, qtbot) -> None:
    _engine, root, controller = load_shell(qapp)
    second_id = controller.addMusicLayer("伴奏", "accompaniment")
    controller.setActiveLayer(DEFAULT_LAYER_ID)
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(
        lambda: _visual_item(
            root.contentItem(), f"musicLayerRow-{second_id}"
        ) is not None
    )

    row = _visual_item(root.contentItem(), f"musicLayerRow-{second_id}")
    menu_button = _visual_item(
        root.contentItem(), f"musicLayerMenu-{second_id}"
    )
    assert menu_button.property("visible") is True
    row_point = row.mapToScene(QPointF(20.0, row.height() / 2.0)).toPoint()
    QTest.mouseMove(root, row_point)
    qapp.processEvents()
    assert menu_button.property("visible") is True

    point = menu_button.mapToScene(
        QPointF(menu_button.width() / 2, menu_button.height() / 2)
    ).toPoint()
    QTest.mouseMove(root, point)
    qapp.processEvents()
    assert menu_button.property("visible") is True

    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point,
    )
    delete_action = row.findChild(QObject, f"musicLayerDelete-{second_id}")
    qtbot.waitUntil(lambda: delete_action.property("visible"))
    root.close()


def test_layer_view_modes_keep_context_without_editing_ghost_notes(qapp) -> None:
    engine, root, controller = load_shell(qapp)
    experience = engine.rootContext().contextProperty("experience")
    backing_id = controller.addMusicLayer("伴奏", "accompaniment")
    controller.setActiveLayer(backing_id)
    controller.createNoteAt(240, 144)
    backing_note_id = controller.state.selected_notes[0].id
    controller.synchronizeNow()
    controller.setActiveLayer(DEFAULT_LAYER_ID)
    qapp.processEvents()

    active_note = _visual_item(root.contentItem(), "noteItem-demo")
    backing_note = _visual_item(
        root.contentItem(), "noteItem-" + backing_note_id
    )

    experience.setLayerViewMode("ghost")
    qapp.processEvents()
    assert active_note.property("visible") is True
    assert active_note.property("interactive") is True
    assert backing_note.property("visible") is True
    assert backing_note.property("interactive") is False
    assert backing_note.property("opacity") == pytest.approx(0.28)

    experience.setLayerViewMode("active")
    qapp.processEvents()
    assert active_note.property("visible") is True
    assert backing_note.property("visible") is False

    experience.setLayerViewMode("all")
    qapp.processEvents()
    assert active_note.property("visible") is True
    assert backing_note.property("visible") is True
    assert backing_note.property("opacity") == pytest.approx(1.0)
    assert backing_note.property("interactive") is False
    root.close()


def test_minimap_respects_layer_visibility_and_emphasizes_active_layer(qapp) -> None:
    _engine, root, controller = load_shell(qapp)
    backing_id = controller.addMusicLayer("伴奏", "accompaniment")
    controller.setActiveLayer(backing_id)
    controller.createNoteAt(240, 144)
    backing_note_id = controller.state.selected_notes[0].id
    controller.synchronizeNow()
    controller.setActiveLayer(DEFAULT_LAYER_ID)
    qapp.processEvents()

    active_note = _visual_item(root.contentItem(), "miniMapNote-demo")
    backing_note = _visual_item(
        root.contentItem(), "miniMapNote-" + backing_note_id
    )
    assert active_note.property("opacity") > backing_note.property("opacity")

    controller.setMusicLayerProperty(backing_id, "muted", True)
    qapp.processEvents()
    assert backing_note.property("visible") is True
    assert backing_note.property("opacity") > 0

    controller.setMusicLayerProperty(backing_id, "visible", False)
    qapp.processEvents()
    assert backing_note.property("visible") is False
    root.close()


def test_dense_minimap_uses_one_canvas_instead_of_note_delegates(qapp) -> None:
    notes = tuple(
        Note.create(
            id=f"dense-{index}",
            pitch=48 + index % 24,
            start_time=index * 0.05,
            duration=0.12,
        )
        for index in range(601)
    )
    _engine, root, _controller = load_shell(qapp, notes=notes)
    qapp.processEvents()

    canvas = _visual_item(root.contentItem(), "miniMapDenseNoteCanvas")
    assert canvas is not None
    assert canvas.property("visible") is True
    assert root.findChild(QObject, "miniMapNote-dense-0") is None
    root.close()
