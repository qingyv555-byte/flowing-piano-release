from time import perf_counter

from PySide6.QtQuick import QQuickItem

from ai_piano_studio.domain.note import Note
from tests.qt.test_editor_ui import _visual_item
from tests.qt.test_playback import FakePlayer
from tests.qt.test_qml_smoke import load_shell


def test_reactive_visuals_keep_a_1000_note_editor_responsive_without_note_simulation(qapp) -> None:
    notes = tuple(
        Note.create(
            id=f"wave3-{index}",
            pitch=36 + (index % 48),
            start_time=(index % 160) * 0.125,
            duration=0.25,
            velocity=48 + (index % 80),
        )
        for index in range(1000)
    )
    player = FakePlayer()
    started = perf_counter()
    engine, root, controller = load_shell(qapp, notes=notes, player=player)
    load_seconds = perf_counter() - started
    timeline = root.findChild(QQuickItem, "timeline")
    reactive = _visual_item(timeline, "reactiveRhythmLayer")
    assert controller.noteModel.rowCount() == 1000
    assert load_seconds < 5.0

    player.playingChanged.emit(True)
    player.positionChanged.emit(2.0)
    qapp.processEvents()
    # The lightweight QML fixture has no Master PCM tap. Note playback state
    # must not invent visual movement in its place.
    assert reactive.property("visible") is False
    assert reactive.property("targetEnergy") == 0

    before = tuple(controller.state.notes)
    scene = engine.rootContext().contextProperty("appearance")
    scene.setFocusModeEnabled(True)
    qapp.processEvents()
    assert reactive.property("visible") is False
    assert tuple(controller.state.notes) == before
    scene.setFocusModeEnabled(False)
    root.close()
