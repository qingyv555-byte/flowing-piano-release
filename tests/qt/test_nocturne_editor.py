"""Focused coverage for the production Nocturne editor shell."""

import pytest
from PySide6.QtCore import QObject, QPointF, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from ai_piano_studio.nocturne import build_nocturne_engine
from ai_piano_studio.persistence.project_document import load_project_document
from tests.qt.test_qml_smoke import _visual_item
from tests.qt.qml_test_utils import load_inline_qml
from tests.qt.test_nocturne import capture, preserved_qsettings_state, session


def _click(root, object_name: str) -> None:
    item = root.findChild(QObject, object_name)
    assert item is not None and item.isVisible() and item.isEnabled()
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    assert 0 <= point.x() < root.width() and 0 <= point.y() < root.height()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=point.toPoint())


def test_nocturne_canvas_tokens_use_the_sage_ivory_palette(qapp) -> None:
    _engine, host = load_inline_qml(
        qapp,
        'import QtQuick\nimport "."\nItem { Theme { id: skin; objectName: "skin"; nocturne: true } }',
    )
    skin = host.findChild(QObject, "skin")
    assert skin.property("keyWhite") == QColor("#c9c8b9")
    assert skin.property("keyBlack") == QColor("#111713")
    assert skin.property("layerOther") == QColor("#98aa91")
    assert skin.property("noteMono") == QColor("#a8be9b")
    assert skin.property("minimapViewportBorder") == QColor("#dce5cf")

    skin.setProperty("nocturne", False)
    qapp.processEvents()
    assert skin.property("keyWhite") == QColor("#c8d5e2")
    assert skin.property("layerOther") == QColor("#7f9bb4")


def test_adapted_project_global_library_button_opens_real_library(session, qtbot) -> None:
    app, engine, root, editor, playback, templates = session
    _click(root, "nocturneAdaptButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert root.property("templateEditorMode") is True
    _click(root, "nocturneLibraryNav")
    qtbot.waitUntil(lambda: root.property("pageState") == "library")


def test_nocturne_narrow_transport_keeps_status_inside_window(
    qapp, tmp_path, preserved_qsettings_state
) -> None:
    app, engine, editor, playback, _keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    root.setPage("editor", False)
    root.setWidth(1000)
    root.setHeight(640)
    qapp.processEvents()
    try:
        transport = root.findChild(QObject, "bottomTransportBar")
        status = _visual_item(root.contentItem(), "statusBar")
        status_right = status.mapToScene(QPointF(status.width(), 0)).x()
        assert status.isVisible()
        assert root.findChild(QObject, "transportPlayPauseButton").isVisible()
        assert root.findChild(QObject, "transportPositionLabel").isVisible()
        assert root.findChild(QObject, "timelineSyncStatus").isVisible()
        assert status_right <= root.width()
        assert transport.property("statusTooltipContextText")
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("width,height", [(1000, 640), (1366, 768)])
def test_nocturne_editor_keeps_global_actions_reachable(
    qapp, qtbot, tmp_path, qtlog, preserved_qsettings_state, width, height
) -> None:
    old_format = QSettings.defaultFormat()
    old_org, old_app = qapp.organizationName(), qapp.applicationName()
    app, engine, editor, playback, _keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    root.setPage("editor", False)
    root.setWidth(width)
    root.setHeight(height)
    qapp.processEvents()
    capture(root, f"editor-nav-{width}x{height}")

    try:
        for name in (
            "nocturneHomeNav",
            "nocturneLibraryNav",
            "nocturneArrangementNav",
            "nocturneAiNav",
            "nocturneInspectorNav",
            "nocturneFocusNav",
            "nocturneSaveNav",
            "nocturneImportButton",
            "nocturneExportButton",
            "nocturneMoreMenuButton",
            "nocturneEditorSettingsButton",
        ):
            item = root.findChild(QObject, name)
            assert item is not None and item.isVisible() and item.isEnabled()

        shell = root.findChild(QObject, "workspaceShell")
        assert shell.property("arrangementOpen") is False
        _click(root, "nocturneArrangementNav")
        assert shell.property("arrangementOpen") is True

        inspector_was_open = shell.property("inspectorOpen")
        _click(root, "nocturneInspectorNav")
        assert shell.property("inspectorOpen") is not inspector_was_open

        _click(root, "nocturneAiNav")
        assert root.property("assistantWindowRequested") is True

        _click(root, "nocturneMoreMenuButton")
        for name in (
            "moreUndoAction", "moreRedoAction", "moreOpenAction",
            "moreBackgroundSettingsAction", "moreHarmonyAction",
            "moreNoteDisplayAction", "moreFollowPlaybackAction",
        ):
            assert root.findChild(QObject, name) is not None
        root.findChild(QObject, "moreMenu").close()

        _click(root, "nocturneEditorSettingsButton")
        settings_popup = root.findChild(QObject, "nocturneEditorSettingsPopup")
        assert settings_popup.property("opened") is True
        for name in (
            "nocturneBpmField", "nocturneTimeSignatureBox",
            "nocturneSnapToggle", "nocturneSnapDivisionBox",
            "nocturneKeyboardHint", "nocturneStageHelpButton",
        ):
            assert root.findChild(QObject, name) is not None
        capture(root, f"editor-settings-{width}x{height}")
        settings_popup.close()

        _click(root, "nocturneImportButton")
        assert root.findChild(QObject, "importMenu").property("opened") is True
        root.findChild(QObject, "importMenu").close()
        _click(root, "nocturneExportButton")
        assert root.findChild(QObject, "exportMenu").property("opened") is True
        root.findChild(QObject, "exportMenu").close()

        _click(root, "nocturneSaveNav")
        assert root.findChild(QObject, "projectNameDialog").property("opened") is True
        root.findChild(QObject, "projectNameDialog").close()

        _click(root, "nocturneFocusNav")
        qtbot.waitUntil(lambda: root.property("focusMode") is True)
        for name in (
            "nocturneHomeNav",
            "nocturneLibraryNav",
            "nocturneArrangementNav",
            "nocturneAiNav",
            "nocturneInspectorNav",
            "nocturneFocusNav",
            "nocturneSaveNav",
            "nocturneImportButton",
            "nocturneExportButton",
            "nocturneMoreMenuButton",
            "nocturneEditorSettingsButton",
        ):
            assert root.findChild(QObject, name).isVisible()
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
        QSettings.setDefaultFormat(old_format)
        app.setOrganizationName(old_org)
        app.setApplicationName(old_app)
    assert not [r.message for r in qtlog.records if any(
        marker in r.message for marker in ("TypeError", "ReferenceError", "Binding loop")
    )]


def test_nocturne_editor_settings_edit_real_tempo_and_snap(
    qapp, qtbot, tmp_path, preserved_qsettings_state
) -> None:
    app, engine, editor, playback, _keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    root.setPage("editor", False)
    qapp.processEvents()
    try:
        _click(root, "nocturneEditorSettingsButton")
        bpm = root.findChild(QObject, "nocturneBpmField")
        before_bpm = editor.bpm
        before_depth = editor.historyUndoDepth
        bpm.setProperty("value", before_bpm + 1)
        bpm.editingValueCommitted.emit(before_bpm + 1)
        qapp.processEvents()
        assert editor.bpm == before_bpm + 1
        assert editor.historyUndoDepth == before_depth + 1
        editor.undo()
        assert editor.bpm == before_bpm

        snap = root.findChild(QObject, "nocturneSnapToggle")
        snap_depth = editor.historyUndoDepth
        old_snap = editor.snapEnabled
        snap.clicked.emit()
        qapp.processEvents()
        assert editor.snapEnabled is not old_snap
        assert editor.historyUndoDepth == snap_depth
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


def test_nocturne_selection_survives_focus_without_moving_canvas(
    qapp, qtbot, tmp_path, preserved_qsettings_state
) -> None:
    old_format = QSettings.defaultFormat()
    old_org, old_app = qapp.organizationName(), qapp.applicationName()
    app, engine, editor, playback, _keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    root.setPage("editor", False)
    qapp.processEvents()

    try:
        editor.createNoteAt(120, 80)
        editor.createNoteAt(240, 120)
        engine.rootContext().contextProperty("experience").setCompositeDisplayMode("original")
        notes = editor.state.notes
        timeline = root.findChild(QObject, "timeline")
        actions = root.findChild(QObject, "selectionActions")
        qtbot.waitUntil(
            lambda: _visual_item(root.contentItem(), f"noteItem-{notes[0].id}") is not None
            and _visual_item(root.contentItem(), f"noteItem-{notes[1].id}") is not None
        )
        first_note = _visual_item(root.contentItem(), f"noteItem-{notes[0].id}")
        second_note = _visual_item(root.contentItem(), f"noteItem-{notes[1].id}")
        assert first_note is not None and second_note is not None

        def geometry():
            origin = timeline.mapToScene(QPointF(0, 0))
            return origin.x(), origin.y(), timeline.width(), timeline.height()

        editor.selectNotes([notes[0].id])
        qapp.processEvents()
        before = geometry()
        note_positions = {
            notes[0].id: first_note.mapToScene(QPointF(0, 0)),
            notes[1].id: second_note.mapToScene(QPointF(0, 0)),
        }
        editor.selectNotes([notes[1].id])
        qapp.processEvents()
        assert editor.selectedCount == 1
        assert actions.property("visible") is True
        assert geometry() == before
        assert first_note.mapToScene(QPointF(0, 0)) == note_positions[notes[0].id]
        assert second_note.mapToScene(QPointF(0, 0)) == note_positions[notes[1].id]

        drag_start = second_note.mapToScene(
            QPointF(5, second_note.property("height") / 2)
        ).toPoint()
        drag_end = drag_start + QPointF(40, 0).toPoint()
        before_note = editor.state.selected_notes[0]
        before_depth = editor.historyUndoDepth
        QTest.mousePress(root, Qt.LeftButton, pos=drag_start)
        QTest.mouseMove(root, drag_end, delay=10)
        QTest.mouseRelease(root, Qt.LeftButton, pos=drag_end)
        qapp.processEvents()
        assert editor.historyUndoDepth == before_depth + 1
        assert editor.state.selected_notes[0].start_time != before_note.start_time
        editor.undo()
        assert editor.state.selected_notes[0] == before_note

        _click(root, "nocturneFocusNav")
        qtbot.waitUntil(lambda: root.property("focusMode") is True)
        assert actions.property("visible") is True
        editor.selectNotes([])
        qapp.processEvents()
        assert actions.property("visible") is False
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
        QSettings.setDefaultFormat(old_format)
        app.setOrganizationName(old_org)
        app.setApplicationName(old_app)


def test_nocturne_arrangement_uses_production_layer_history_and_persistence(
    qapp, qtbot, tmp_path, preserved_qsettings_state
) -> None:
    old_format = QSettings.defaultFormat()
    old_org, old_app = qapp.organizationName(), qapp.applicationName()
    app, engine, editor, playback, _keyboard = build_nocturne_engine(tmp_path)
    root = engine.rootObjects()[0]
    playback.setVolume(0)
    root.setPage("editor", False)
    qapp.processEvents()

    try:
        layer_id = editor.addMusicLayer("夜庭伴奏", "accompaniment")
        changes = (
            ("muted", True),
            ("solo", True),
            ("visible", False),
            ("locked", True),
        )
        for field, value in changes:
            before_depth = editor.historyUndoDepth
            assert editor.setMusicLayerProperty(layer_id, field, value)
            assert getattr(editor.state.layer(layer_id), field) is value
            assert editor.historyUndoDepth == before_depth + 1
            editor.undo()
            assert getattr(editor.state.layer(layer_id), field) is not value
            editor.redo()
            assert getattr(editor.state.layer(layer_id), field) is value

        before_depth = editor.historyUndoDepth
        assert editor.setMusicLayerPinned(layer_id, True)
        assert editor.state.layer(layer_id).pinned is True
        assert editor.historyUndoDepth == before_depth + 1
        editor.undo()
        assert editor.state.layer(layer_id).pinned is False
        editor.redo()
        assert editor.state.layers[0].id == layer_id

        project_path = tmp_path / "Projects" / "layer-state.aipiano.json"
        project_path.parent.mkdir(parents=True, exist_ok=True)
        assert editor.saveProject(str(project_path))
        qtbot.waitUntil(lambda: not editor.isSaving)
        restored = load_project_document(project_path).state.layer(layer_id)
        assert (restored.muted, restored.solo, restored.visible, restored.locked, restored.pinned) == (
            True, True, False, True, True
        )
        _click(root, "nocturneArrangementNav")
        qapp.processEvents()
        capture(root, "editor-arrangement-real-layers")
    finally:
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
        QSettings.setDefaultFormat(old_format)
        app.setOrganizationName(old_org)
        app.setApplicationName(old_app)
