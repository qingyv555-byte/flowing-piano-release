"""The visible sounding notes belong to the same snapshot as detached audio."""
from pathlib import Path
from PySide6.QtCore import QObject

from ai_piano_studio.domain.note import Note
from tests.qt.test_qml_smoke import load_shell
from tests.qt.test_playback import FakePlayer
from tests.qt.test_studio_workflows import find_item, qml


def test_draft_audition_dims_original_and_highlights_only_sounding_draft(qapp, qtbot):
    original = Note.create(pitch=60, start_time=0, duration=3)
    draft = Note.create(pitch=67, start_time=0, duration=1)
    engine, root, editor = load_shell(qapp, notes=(original,), player=FakePlayer())
    playback = engine.rootContext().contextProperty("playback")
    before = editor.state.content
    old_item = find_item(root, "noteItem-" + original.id)
    playback.play()
    playback.seek(.5)
    assert old_item.property("playbackActive")
    playback.play_detached_notes((draft,))
    playback.seek(.5)
    qtbot.wait(80)
    canvas = find_item(root, "detachedPreviewCanvas")
    assert not old_item.property("playbackActive")
    assert old_item.opacity() < .3
    assert canvas.property("activeNoteCount") == 1
    assert find_item(root, "rawNoteCanvas").opacity() < .3
    assert find_item(root, "smartCompositeCanvas").opacity() < .3
    assert "原工程未播放" in find_item(root, "detachedPreviewStatus").property("text")
    folder = Path("artifacts/audition-20261102")
    folder.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(folder / "source-aligned.png"))
    playback.pause()
    assert canvas.property("activeNoteCount") == 0
    assert old_item.opacity() < .3
    playback.stop_detached_preview()
    assert old_item.opacity() == 1
    assert editor.state.content == before
    root.close()


def test_detached_energy_uses_the_audible_snapshot(qapp):
    from ai_piano_studio.audio.controller import PlaybackController
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.editor.controller import EditorController
    editor = EditorController(ProjectState((Note.create(pitch=60, start_time=10, duration=1),)))
    playback = PlaybackController(editor, player=FakePlayer())
    playback.play_detached_notes((Note.create(pitch=67, start_time=0, duration=2),))
    playback.seek(.5)
    assert playback.activePitches == [67]
    assert playback.visualEnergy > 0
    playback.stop_detached_preview()


def test_smart_view_original_segments_do_not_light_up_during_draft_audition(qapp, qtbot):
    notes = tuple(Note.create(id=f"source-{i}", pitch=60, start_time=i * .2, duration=.18)
                  for i in range(5))
    engine, root, editor = load_shell(qapp, notes=notes, player=FakePlayer())
    engine.rootContext().contextProperty("experience").setCompositeDisplayMode("smart")
    qtbot.wait(60)
    group = find_item(root, "compositeNoteItem-composite:source-0,source-1,source-2,source-3,source-4")
    assert group is not None
    group.findChild(QObject, "expandButton").clicked.emit()
    qtbot.wait(30)
    segment = find_item(root, "segmentButton-source-0")
    assert segment is not None
    playback = engine.rootContext().contextProperty("playback")
    playback.play()
    playback.seek(.1)
    assert segment.property("childPlaybackActive")
    playback.play_detached_notes((Note.create(pitch=67, start_time=0, duration=2),))
    playback.seek(.1)
    assert not segment.property("childPlaybackActive")
    assert group.opacity() < .3
    playback.stop_detached_preview()
    assert group.opacity() > .8
    root.close()
