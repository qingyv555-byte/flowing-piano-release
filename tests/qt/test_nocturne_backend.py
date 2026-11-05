"""Exercise the production services reached through the opt-in Nocturne shell."""

import numpy as np
from PySide6.QtCore import QObject, QPointF

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.domain.note import Note
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
from ai_piano_studio.persistence.midi import export_midi
from tests.qt.test_nocturne import click, preserved_qsettings_state, session


def test_nocturne_assistant_stages_applies_and_undoes_real_project(session, qtbot):
    app, engine, root, editor, playback, templates = session
    initialize_deferred_services(engine)
    click(root, "nocturneCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assistant = engine.rootContext().contextProperty("assistant")
    assert assistant.gateway.is_bound_to(editor)
    before = editor.state.content
    depth = editor._history.undo_depth
    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    result = assistant.gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 64, "start_time": 0.0, "duration": 0.5, "velocity": 80}]},
        context,
    )
    assert result.success
    assistant.presentProposal(context)
    assert editor.state.content == before
    assert assistant.proposal_manager.summary.notes_added == 1
    # Provider output is staged locally; no network call or fixed UI suggestion.
    assistant.applyProposal()
    assert len(editor.state.notes) == 1
    assert editor.state.notes[0].pitch == 64
    assert editor._history.undo_depth == depth + 1
    editor.undo()
    assert editor.state.content == before
    editor.redo()
    assert editor.state.notes[0].pitch == 64


def test_nocturne_spectrum_consumes_production_mixer_pcm(session, qtbot):
    app, engine, root, editor, playback, templates = session
    click(root, "nocturneCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    scene = engine.rootContext().contextProperty("scene")
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    mixer = playback.piano_source.mixer
    assert visualizer._tap is mixer._visualizer_tap
    scene.setVisualizerEnabled(True)
    layer = root.findChild(QObject, "reactiveRhythmLayer")
    assert layer is not None
    assert layer.property("visualizer") is visualizer
    # Render a real mixer voice in memory; never start an audio output device.
    mixer.set_volume(1.0)
    wave = (np.sin(np.arange(8192) * 2 * np.pi * 1000 / 48000) * 9000).astype(np.int16)
    mixer.start_voice("nocturne-pcm", 60, 100, tuple(int(x) for x in wave),
                      source_sample_rate=48000, owner_tag="test")
    mixer._read_output_frames(1024)
    payload = visualizer._tap.read_latest()
    assert payload
    qtbot.waitUntil(lambda: visualizer.rms > 0, timeout=2000)
    assert max(visualizer.bands) > 0
    assert layer.property("audioRms") > 0
    scene.setVisualizerEnabled(False)
    assert visualizer.rms == 0
    assert not layer.isVisible()
    mixer.set_volume(0.0)


def test_nocturne_real_midi_import_and_failed_open_preserve_project(session, tmp_path):
    app, engine, root, editor, playback, templates = session
    importer = engine.rootContext().contextProperty("midiImport")
    source = tmp_path / "旋律.mid"
    notes = (Note.create(pitch=67, start_time=0.0, duration=0.5, velocity=90),)
    export_midi(source, notes)
    assert importer.prepare(str(source))
    assert importer.importResult("replace")
    assert editor.state.notes[0].pitch == 67
    assert editor.state.notes[0].duration == 0.5
    before = editor.state.content
    history_depth = editor._history.undo_depth
    broken = tmp_path / "损坏.mid"
    broken.write_bytes(b"invalid midi")
    assert not importer.prepare(str(broken))
    editor.openProject(str(tmp_path / "不存在.pianoai.json"))
    assert editor.state.content == before
    assert editor._history.undo_depth == history_depth


def test_nocturne_long_chinese_project_name_keeps_home_actions_inside_window(session, qtbot):
    app, engine, root, editor, playback, templates = session
    click(root, "nocturneAdaptButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    project_name = "夜庭里的钢琴旋律与宁静月光" * 4
    assert editor.renameProject(editor.currentFilePath, project_name)
    root.requestLibraryExit("launch", None)
    root.setWidth(1000)
    root.setHeight(640)
    app.processEvents()
    assert root.property("pageState") == "launch"
    primary = root.findChild(QObject, "nocturneCreateButton")
    assert project_name in primary.property("text")
    assert primary.width() <= 360
    for name in ("nocturneCreateButton", "nocturnePreviewButton"):
        item = root.findChild(QObject, name)
        edge = item.mapToScene(QPointF(item.width(), item.height()))
        assert edge.x() <= root.width() and edge.y() <= root.height()
    click(root, "nocturneCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert editor.currentFileName == project_name
