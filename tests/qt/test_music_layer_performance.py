from math import isfinite
from time import perf_counter

import pytest
from PySide6.QtCore import QObject, Q_ARG, QMetaObject, Qt

from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.qt.test_qml_smoke import _visual_item, load_shell


@pytest.mark.parametrize("note_count", [1_000, 5_000, 10_000])
def test_music_layer_operations_keep_dense_projects_bounded(
    note_count: int, qapp, capsys
) -> None:
    target = MusicLayer.create(id="target", name="伴奏", role="accompaniment", order=1)
    notes = tuple(
        Note.create(
            id=f"n-{index}",
            pitch=48 + index % 24,
            start_time=index * 0.05,
            duration=0.12,
        )
        for index in range(note_count)
    )
    selected = frozenset(note.id for note in notes[:100])
    editor = EditorController(
        ProjectState(notes, selected, layers=(MusicLayer.default(), target))
    )

    started = perf_counter()
    editor.setActiveLayer("target")
    assert editor.moveSelectionToLayer("target") is True
    assert editor.setMusicLayerProperty("target", "customColor", "#8B72FF") is True
    assert editor.setMusicLayerProperty("target", "visible", False) is True
    assert editor.moveMusicLayer("target", 0) is True
    elapsed = perf_counter() - started

    assert sum(note.layer_id == "target" for note in editor.state.notes) == 100
    assert editor.state.layers[0].id == "target"
    assert editor.state.layers[0].custom_color == "#8B72FF"
    assert editor.state.layers[0].visible is False
    assert isfinite(elapsed)
    print(f"music-layer operations {note_count} notes: {elapsed:.6f}s")
    assert capsys.readouterr().out.startswith("music-layer operations")


def test_arrangement_open_and_live_resize_use_canvas_with_ten_thousand_notes(
    qapp,
) -> None:
    notes = tuple(
        Note.create(
            id=f"arrangement-{index}",
            pitch=48 + index % 24,
            start_time=index * 0.025,
            duration=0.08,
        )
        for index in range(10_000)
    )
    _engine, root, controller = load_shell(qapp, notes=notes)
    # Arrangement height is content-driven. Populate enough real layers to
    # exercise the requested live-resize cap while retaining the 10k-note load.
    for index in range(8):
        controller.addMusicLayer(f"性能层 {index + 2}", "other")
    panel = root.findChild(QObject, "arrangementPanel")
    root.findChild(QObject, "arrangementToggle").clicked.emit()
    assert QMetaObject.invokeMethod(
        panel,
        "previewHeight",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("double", 320.0),
    )
    qapp.processEvents()

    assert panel.property("visible") is True
    assert panel.property("effectivePanelHeight") == 320.0
    assert _visual_item(root.contentItem(), "rawNoteCanvas").property("visible") is True
    assert _visual_item(root.contentItem(), "miniMapDenseNoteCanvas").property(
        "visible"
    ) is True
    root.close()
