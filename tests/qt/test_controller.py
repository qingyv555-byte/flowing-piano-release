import pytest
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.domain.music_layer import (
    DEFAULT_LAYER_ID,
    UNCLASSIFIED_LAYER_ID,
    MusicLayer,
)
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.editor.midi_import_controller import MidiImportController
from ai_piano_studio.persistence.midi import (
    MidiImportResult,
    MidiNoteEvent,
    encode_midi,
    load_midi,
)


def seeded_controller() -> EditorController:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    return EditorController(ProjectState((note,), frozenset({"a"})))


def test_controller_exposes_music_layer_api_and_signal_boundaries() -> None:
    controller = seeded_controller()
    layers_changed = QSignalSpy(controller.layersChanged)
    active_changed = QSignalSpy(controller.activeLayerChanged)

    melody_id = controller.addMusicLayer("主旋律", "melody")
    assert melody_id
    assert controller.layerModel.rowCount() == 2
    assert layers_changed.count() == 1

    controller.setActiveLayer(melody_id)
    assert controller.activeLayerId == melody_id
    assert controller.activeLayer["name"] == "主旋律"
    assert active_changed.count() == 1

    assert controller.renameMusicLayer(melody_id, "旋律") is True
    assert controller.setMusicLayerProperty(melody_id, "volume", 0.75) is True
    assert controller.moveMusicLayer(melody_id, 0) is True
    assert controller.moveSelectionToLayer(melody_id) is True
    assert controller.state.selected_notes[0].layer_id == melody_id
    assert controller.selectMusicLayer(melody_id) is None

    copy_id = controller.duplicateMusicLayer(melody_id)
    assert copy_id
    assert controller.deleteMusicLayer(copy_id) is False
    empty_copy_id = controller.duplicateMusicLayer(DEFAULT_LAYER_ID)
    assert controller.deleteMusicLayer(empty_copy_id) is True


def test_controller_custom_music_layer_role_uses_valid_fallback_color_role() -> None:
    controller = seeded_controller()

    assert controller.setMusicLayerProperty(DEFAULT_LAYER_ID, "role", "custom") is True
    layer = controller.state.layer(DEFAULT_LAYER_ID)
    assert layer.role == "custom"
    assert layer.color_role == "other"


def test_controller_creates_layer_from_selection_as_one_undoable_action() -> None:
    controller = seeded_controller()
    before_depth = controller.historyUndoDepth

    layer_id = controller.createMusicLayerFromSelection("主旋律", "melody")

    assert layer_id
    assert controller.activeLayerId == layer_id
    assert controller.state.selected_notes[0].layer_id == layer_id
    assert controller.historyUndoDepth == before_depth + 1
    controller.undo()
    assert [layer.id for layer in controller.state.layers] == [DEFAULT_LAYER_ID]
    assert controller.state.selected_notes[0].layer_id == DEFAULT_LAYER_ID


def test_controller_delete_layer_with_notes_uses_unclassified_and_undoes_once() -> None:
    controller = seeded_controller()
    layer_id = controller.createMusicLayerFromSelection("主旋律", "melody")
    before_depth = controller.historyUndoDepth

    assert controller.deleteMusicLayerWithPolicy(
        layer_id, "move_to_unclassified"
    ) is True

    assert controller.state.selected_notes[0].layer_id == UNCLASSIFIED_LAYER_ID
    assert controller.historyUndoDepth == before_depth + 1
    controller.undo()
    assert controller.state.selected_notes[0].layer_id == layer_id


def test_controller_moves_selection_to_unclassified_in_one_undoable_action() -> None:
    controller = seeded_controller()
    before_depth = controller.historyUndoDepth

    assert controller.moveSelectionToUnclassified() is True

    assert controller.state.selected_notes[0].layer_id == UNCLASSIFIED_LAYER_ID
    assert controller.historyUndoDepth == before_depth + 1
    controller.undo()
    assert UNCLASSIFIED_LAYER_ID not in controller.state.layer_ids
    assert controller.state.selected_notes[0].layer_id == DEFAULT_LAYER_ID


def test_controller_new_notes_use_active_music_layer() -> None:
    controller = EditorController(ProjectState())
    melody_id = controller.addMusicLayer("主旋律", "melody")
    controller.setActiveLayer(melody_id)

    controller.createNoteAt(80, 144)

    assert controller.state.selected_notes[0].layer_id == melody_id


def test_controller_new_notes_use_active_layer_release_defaults() -> None:
    controller = EditorController(ProjectState())
    assert controller.setMusicLayerProperty(
        DEFAULT_LAYER_ID, "defaultReleaseMode", "custom"
    ) is True
    assert controller.setMusicLayerProperty(
        DEFAULT_LAYER_ID, "defaultReleaseMs", 420.0
    ) is True

    controller.createNoteAt(80, 144)

    note = controller.state.selected_notes[0]
    assert note.release_mode == "custom"
    assert note.release_ms == 420.0


def test_project_session_id_changes_when_a_project_is_replaced() -> None:
    controller = EditorController(ProjectState())
    initial_session_id = controller.projectSessionId

    controller.newProject()

    assert controller.projectSessionId == initial_session_id + 1


def test_create_at_pixels_selects_new_note() -> None:
    controller = EditorController(ProjectState())
    controller.createNoteAt(80, 144)
    note = controller.state.selected_notes[0]
    assert (note.start_time, note.pitch, note.duration) == (1.0, 64, 0.5)


def test_drawing_note_uses_explicit_defaults_and_emits_creation_preview() -> None:
    controller = EditorController(ProjectState())
    created: list[tuple[int, int, float]] = []
    controller.noteCreated.connect(lambda pitch, velocity, duration: created.append((pitch, velocity, duration)))

    note_id = controller.beginDrawNoteAt(80, 144, 1.0, 96)

    note = next(note for note in controller.state.notes if note.id == note_id)
    assert (note.pitch, note.duration, note.velocity) == (64, 1.0, 96)
    assert created == [(64, 96, 1.0)]


def test_manual_create_note_selects_created_note() -> None:
    existing = Note.create(id="existing", pitch=60, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((existing,), frozenset({existing.id})))

    created_id = controller.beginDrawNoteAt(80, 144, 1.0, 96)
    assert controller.state.selected_ids == frozenset({existing.id})

    controller.commitGesture()

    assert controller.state.selected_ids == frozenset({created_id})


def test_manual_create_note_replaces_previous_multi_selection() -> None:
    notes = (
        Note.create(id="first", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="second", pitch=64, start_time=1.0, duration=0.5),
    )
    controller = EditorController(ProjectState(notes, frozenset({"first", "second"})))

    created_id = controller.beginDrawNoteAt(160, 144, 1.0, 96)
    controller.commitGesture()

    assert controller.state.selected_ids == frozenset({created_id})


def test_import_detected_notes_is_one_undoable_transaction_and_selects_imports() -> None:
    existing = Note.create(id="existing", pitch=48, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((existing,), frozenset({existing.id})))
    before_undo_depth = controller._history.undo_depth

    imported = [
        {"pitch": 60, "startTime": 0.5, "duration": 0.5, "velocity": 82},
        {"pitch": 64, "startTime": 1.0, "duration": 0.25, "velocity": 76},
    ]

    assert controller.importDetectedNotes(imported, "playhead", 2.0, 0) is True
    assert len(controller.state.active_notes) == 3
    assert controller.selectedCount == 2
    assert controller._history.undo_depth == before_undo_depth + 1
    assert all(note.id != existing.id for note in controller.state.selected_notes)
    assert [(note.pitch, note.start_time) for note in controller.state.selected_notes] == [
        (60, 2.5),
        (64, 3.0),
    ]

    controller.undo()
    assert controller.state.active_notes == (existing,)
    assert controller.state.selected_ids == frozenset({existing.id})
    controller.redo()
    assert controller.selectedCount == 2


def test_import_detected_notes_can_apply_detected_tempo_in_same_transaction() -> None:
    controller = EditorController(ProjectState())

    assert controller.importDetectedNotes(
        [{"pitch": 60, "startTime": 0.0, "duration": 0.5}],
        "project_start",
        0.0,
        84,
    ) is True

    assert controller.bpm == 84
    assert controller._history.undo_depth == 1


def test_import_midi_result_preserves_ppq_timing_and_is_one_undoable_transaction() -> None:
    controller = EditorController(ProjectState())
    result = MidiImportResult(
        notes=(MidiNoteEvent(240, 720, 60, 91, 0, 0),),
        ppq=480,
        bpm=90,
        beats_per_bar=3,
        beat_unit=4,
        track_count=1,
        note_track_count=1,
        tempo_event_count=1,
        warnings=(),
    )

    assert controller.importMidiResult(result) is True

    imported = controller.state.selected_notes[0]
    assert imported.pitch == 60
    assert imported.velocity == 91
    assert imported.start_time == pytest.approx(1 / 3)
    assert imported.duration == pytest.approx(2 / 3)
    assert imported.track == 0
    assert imported.channel == 0
    assert controller.state.playback_notes == (imported,)
    assert imported.status == "saved"
    assert (controller.bpm, controller.beatsPerBar, controller.beatUnit) == (90, 3, 4)
    assert controller._history.undo_depth == 1

    controller.undo()
    assert controller.state.notes == ()


def test_import_midi_result_replace_discards_existing_notes_and_undo_restores_them() -> None:
    existing = Note.create(id="existing", pitch=48, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((existing,), frozenset({existing.id})))
    result = MidiImportResult(
        notes=(MidiNoteEvent(0, 120, 72, 80, 0, 1),),
        ppq=480,
        bpm=120,
        beats_per_bar=4,
        beat_unit=4,
        track_count=2,
        note_track_count=1,
        tempo_event_count=1,
        warnings=(),
    )

    assert controller.importMidiResult(result, "replace") is True
    assert [note.pitch for note in controller.state.active_notes] == [72]
    assert controller._history.undo_depth == 1

    controller.undo()
    assert controller.state.active_notes == (existing,)
    assert controller.state.selected_ids == frozenset({existing.id})


def test_midi_import_controller_round_trips_a_real_file_and_invalidates_on_project_switch(
    tmp_path,
) -> None:
    target = tmp_path / "imported.mid"
    target.write_bytes(
        encode_midi(
            (Note.create(id="source", pitch=69, start_time=0.25, duration=0.5),),
            TempoMap(bpm=96, beats_per_bar=6, beat_unit=8),
        )
    )
    controller = EditorController(ProjectState())
    importer = MidiImportController(controller)

    assert importer.prepare(str(target)) is True
    assert importer.ready is True
    assert importer.noteCount == 1
    assert (importer.bpm, importer.beatsPerBar, importer.beatUnit) == (96, 6, 8)
    assert importer.importResult() is True
    assert controller.state.selected_notes[0].pitch == 69
    exported = tmp_path / "exported.mid"
    controller.exportMidi(str(exported))
    exported_result = load_midi(exported)
    assert exported_result.notes[0].pitch == 69
    assert exported_result.notes[0].end_tick > exported_result.notes[0].start_tick
    assert controller._history.undo_depth == 1

    assert importer.prepare(str(target)) is True
    controller.newProject()
    assert importer.ready is False
    assert importer.importResult() is False


def test_midi_import_export_preserves_source_track_and_channel_identity(tmp_path) -> None:
    source = tmp_path / "multi-track-source.mid"
    source.write_bytes(
        encode_midi(
            (
                Note.create(
                    id="source-a",
                    pitch=60,
                    start_time=0.0,
                    duration=0.5,
                    velocity=80,
                    track=0,
                    channel=2,
                ),
                Note.create(
                    id="source-b",
                    pitch=60,
                    start_time=0.0,
                    duration=0.5,
                    velocity=80,
                    track=1,
                    channel=9,
                ),
            )
        )
    )
    controller = EditorController(ProjectState())
    assert controller.importMidiResult(load_midi(source)) is True

    exported = tmp_path / "multi-track-export.mid"
    controller.exportMidi(str(exported))

    assert [
        (note.track, note.channel, note.pitch, note.velocity)
        for note in load_midi(exported).notes
    ] == [
        (0, 2, 60, 80),
        (1, 9, 60, 80),
    ]


def test_midi_import_creates_named_layers_and_export_uses_layer_order(tmp_path) -> None:
    result = MidiImportResult(
        notes=(
            MidiNoteEvent(0, 240, 72, 90, 3, 0),
            MidiNoteEvent(0, 240, 36, 70, 2, 1),
        ),
        ppq=480,
        bpm=120,
        beats_per_bar=4,
        beat_unit=4,
        track_count=2,
        note_track_count=2,
        tempo_event_count=1,
        warnings=(),
        track_names=("主旋律", "低音"),
    )
    controller = EditorController(ProjectState())

    assert controller.importMidiResult(result, "replace") is True
    assert [(layer.id, layer.name) for layer in controller.state.layers] == [
        ("layer-midi-0-3", "主旋律"),
        ("layer-midi-1-2", "低音"),
    ]

    reordered = controller.state.reorder_layer("layer-midi-1-2", 0)
    controller._execute(reordered)
    target = tmp_path / "ordered.mid"
    controller.exportMidi(str(target))
    exported = load_midi(target)

    assert exported.track_names == ("低音", "主旋律")
    assert [(note.track, note.channel, note.pitch) for note in exported.notes] == [
        (0, 2, 36),
        (1, 3, 72),
    ]


def test_failed_note_creation_preserves_previous_selection(monkeypatch) -> None:
    existing = Note.create(id="existing", pitch=60, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((existing,), frozenset({existing.id})))
    controller.beginDrawNoteAt(80, 144, 1.0, 96)

    def fail_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(controller._harmony_history, "commit_gesture", fail_commit)
    controller.commitGesture()

    assert controller.state.selected_ids == frozenset({existing.id})
    assert controller.state.active_notes == (existing,)
    assert controller._gesture_origin is None


def test_undo_created_note_does_not_leave_stale_selection() -> None:
    existing = Note.create(id="existing", pitch=60, start_time=0.0, duration=0.5)
    controller = EditorController(ProjectState((existing,), frozenset({existing.id})))

    created_id = controller.beginDrawNoteAt(80, 144, 1.0, 96)
    controller.commitGesture()
    controller.undo()

    assert created_id not in controller.state.selected_ids
    assert all(note.id != created_id for note in controller.state.notes)

    controller.redo()

    assert controller.state.selected_ids == frozenset({created_id})


def test_set_view_scale_changes_geometry_mapping() -> None:
    controller = EditorController(ProjectState())
    controller.setViewScale(160, 24)
    controller.createNoteAt(160, 192)
    note = controller.state.selected_notes[0]
    assert (note.start_time, note.pitch) == (1.0, 64)


def test_set_view_scale_clamps_to_bounds() -> None:
    controller = EditorController(ProjectState())
    controller.setViewScale(1.0, 1.0)
    assert (controller.pixelsPerSecond, controller.rowHeight) == (30.0, 12.0)
    controller.setViewScale(9999.0, 9999.0)
    assert (controller.pixelsPerSecond, controller.rowHeight) == (320.0, 32.0)


def test_set_view_scale_emits_only_on_change(qtbot) -> None:
    controller = EditorController(ProjectState())
    with qtbot.waitSignal(controller.viewScaleChanged, timeout=500):
        controller.setViewScale(100.0, 20.0)
    controller.setViewScale(100.0, 20.0)
    assert (controller.pixelsPerSecond, controller.rowHeight) == (100.0, 20.0)


def test_note_pitch_range_describes_loaded_notes() -> None:
    notes = (
        Note.create(id="low", pitch=24, start_time=0.0, duration=0.5),
        Note.create(id="high", pitch=108, start_time=1.0, duration=0.5),
        Note.create(id="middle", pitch=60, start_time=2.0, duration=0.5),
    )
    controller = EditorController(ProjectState(notes))

    assert controller.notePitchRange == {"lowest": 24, "highest": 108}


def test_validate_timeline_reports_unsynced_notes_and_same_pitch_conflicts() -> None:
    notes = (
        Note.create(id="first", pitch=60, start_time=0.0, duration=1.0),
        Note.create(id="overlap", pitch=60, start_time=0.5, duration=1.0),
        Note.create(id="draft", pitch=64, start_time=3.0, duration=1.0, status="pending"),
    )
    controller = EditorController(ProjectState(notes))

    report = controller.validateTimeline()

    assert report == {
        "valid": True,
        "conflictCount": 1,
        "invalidPitchCount": 0,
        "emptyDurationCount": 0,
        "unsyncedCount": 1,
    }


def test_validate_timeline_ignores_serialized_same_pitch_boundary_jitter() -> None:
    notes = (
        Note.create(id="first", pitch=60, start_time=12.954545, duration=0.511365),
        Note.create(id="next", pitch=60, start_time=13.465909, duration=0.170455),
    )
    controller = EditorController(ProjectState(notes))

    assert controller.validateTimeline()["conflictCount"] == 0


def test_validate_timeline_keeps_a_larger_same_pitch_intersection() -> None:
    notes = (
        Note.create(id="first", pitch=60, start_time=0.0, duration=1.001),
        Note.create(id="overlap", pitch=60, start_time=1.0, duration=0.5),
    )
    controller = EditorController(ProjectState(notes))

    assert controller.validateTimeline()["conflictCount"] == 1


def test_selection_summary_notifies_during_move_preview() -> None:
    controller = seeded_controller()
    property_index = controller.metaObject().indexOfProperty("selectionSummary")

    assert controller.metaObject().property(property_index).notifySignal().name() == b"stateChanged"


def test_move_preview_uses_view_scale() -> None:
    controller = seeded_controller()
    controller.setViewScale(160, 24)
    controller.beginGesture()
    controller.previewMovePixels(160, 24)
    controller.commitGesture()
    note = controller.state.notes[0]
    assert (note.start_time, note.pitch) == (1.0, 59)


def test_duplicate_drag_keeps_a_ghost_preview_until_one_undoable_commit() -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="b", pitch=64, start_time=1.0, duration=0.75),
    )
    controller = EditorController(ProjectState(notes, frozenset({"a", "b"})))
    controller.setViewScale(80.0, 20.0)
    preview_changes: list[object] = []
    controller.duplicatePreviewChanged.connect(lambda: preview_changes.append(object()))

    controller.beginDuplicateGesture()
    controller.previewDuplicatePixels(80.0, 20.0)

    assert controller.state.notes == notes
    assert controller.duplicatePreview == [
        {"sourceId": "a", "pitch": 59, "noteName": "B3", "startTime": 1.0, "duration": 0.5},
        {"sourceId": "b", "pitch": 63, "noteName": "D#4", "startTime": 2.0, "duration": 0.75},
    ]
    assert len(preview_changes) == 1

    controller.previewDuplicatePixels(0.0, 0.0)
    assert len(preview_changes) == 1

    controller.commitDuplicateGesture()

    assert len(controller.state.notes) == 4
    copies = controller.state.selected_notes
    assert [(note.pitch, note.start_time, note.duration) for note in copies] == [
        (59, 1.0, 0.5),
        (63, 2.0, 0.75),
    ]
    assert all(note.status == "pending" for note in copies)
    assert controller.duplicatePreview == []
    controller.undo()
    assert controller.state.notes == notes


def test_copy_paste_creates_a_separately_selectable_pending_copy() -> None:
    controller = seeded_controller()

    controller.copySelected()
    controller.pasteCopied()

    assert len(controller.state.notes) == 2
    original, copied = controller.state.notes
    assert copied.id != original.id
    assert copied.pitch == original.pitch
    assert copied.start_time == pytest.approx(original.start_time + controller.snap_seconds())
    assert copied.duration == original.duration
    assert copied.status == "pending"
    assert controller.state.selected_ids == frozenset({copied.id})


def test_duplicate_selected_is_one_history_transaction() -> None:
    controller = seeded_controller()
    before_depth = controller._history.undo_depth

    controller.duplicateSelected()

    assert len(controller.state.active_notes) == 2
    assert controller._history.undo_depth == before_depth + 1
    copied = next(note for note in controller.state.active_notes if note.id != "a")
    assert copied.start_time == pytest.approx(controller.snap_seconds())
    controller.undo()
    assert len(controller.state.active_notes) == 1


def test_shift_selected_octave_is_clamped_and_one_history_transaction() -> None:
    controller = seeded_controller()
    before_depth = controller._history.undo_depth

    controller.shiftSelectedOctave(12)

    assert controller.state.selected_notes[0].pitch == 72
    assert controller._history.undo_depth == before_depth + 1
    controller.undo()
    assert controller.state.selected_notes[0].pitch == 60


def test_quantize_selected_is_one_history_transaction() -> None:
    note = Note.create(id="q", pitch=60, start_time=0.37, duration=0.5)
    controller = EditorController(ProjectState((note,), frozenset({"q"})))
    before_depth = controller._history.undo_depth

    controller.quantizeSelected()

    assert controller.state.selected_notes[0].start_time == pytest.approx(0.25)
    assert controller._history.undo_depth == before_depth + 1


def test_keyboard_move_selected_preserves_atomic_history() -> None:
    controller = seeded_controller()
    before_depth = controller._history.undo_depth

    controller.moveSelectedByKeyboard(1, 0)
    controller.moveSelectedByKeyboard(0, 1)

    note = controller.state.selected_notes[0]
    assert note.start_time == pytest.approx(controller.snap_seconds())
    assert note.pitch == 61
    assert controller._history.undo_depth == before_depth + 2


def test_delete_selected_can_be_undone(qtbot) -> None:
    controller = seeded_controller()
    with qtbot.waitSignal(controller.feedbackRequested, timeout=500):
        controller.deleteSelected()
    assert controller.noteModel.rowCount() == 1
    assert controller.state.notes[0].status == "deleted"
    controller.undo()
    assert controller.noteModel.rowCount() == 1
    assert controller.state.notes[0].status == "saved"


def test_selection_change_does_not_enable_undo() -> None:
    controller = seeded_controller()
    controller.clearSelection()
    assert controller.selectedCount == 0
    assert controller.canUndo is False


def test_set_bpm_rescales_notes_to_keep_beat_positions() -> None:
    note = Note.create(id="a", pitch=60, start_time=1.0, duration=0.5)
    controller = EditorController(ProjectState((note,)))
    controller.setBpm(60)
    rescaled = controller.state.notes[0]
    assert controller.bpm == 60
    assert (rescaled.start_time, rescaled.duration) == (2.0, 1.0)
    assert controller.isDirty is True


def test_set_bpm_is_undoable() -> None:
    note = Note.create(id="a", pitch=60, start_time=1.0, duration=0.5)
    controller = EditorController(ProjectState((note,)))
    controller.setBpm(60)
    controller.undo()
    assert controller.bpm == 120
    assert controller.state.notes[0].start_time == 1.0
    controller.redo()
    assert controller.bpm == 60


def test_set_bpm_rejects_out_of_range(qtbot) -> None:
    controller = seeded_controller()
    with qtbot.waitSignal(controller.feedbackRequested, timeout=500) as blocker:
        controller.setBpm(45)
    assert "BPM" in blocker.args[0]
    assert controller.bpm == 120
    assert controller.canUndo is False


def test_set_bpm_90_reanchors_and_roundtrips_undo_redo() -> None:
    note = Note.create(id="a", pitch=60, start_time=1.0, duration=0.5)
    controller = EditorController(ProjectState((note,)))
    controller.setBpm(90)
    rescaled = controller.state.notes[0]
    assert controller.bpm == 90
    # 1.0s @120 = 2 拍 -> 4/3s @90；0.5s = 1 拍 -> 2/3s
    assert rescaled.start_time == pytest.approx(4 / 3)
    assert rescaled.duration == pytest.approx(2 / 3)
    controller.undo()
    assert controller.bpm == 120
    assert controller.state.notes[0].start_time == 1.0
    controller.redo()
    assert controller.bpm == 90
    assert controller.state.notes[0].start_time == pytest.approx(4 / 3)


@pytest.mark.parametrize("bpm", (500, 50))
def test_set_bpm_rejects_values_beyond_both_bounds(qtbot, bpm: int) -> None:
    controller = seeded_controller()
    with qtbot.waitSignal(controller.feedbackRequested, timeout=500) as blocker:
        controller.setBpm(bpm)
    assert "BPM" in blocker.args[0]
    assert controller.bpm == 120
    assert controller.canUndo is False


def test_set_time_signature_and_reject_invalid(qtbot) -> None:
    controller = seeded_controller()
    controller.setTimeSignature(6, 8)
    assert (controller.beatsPerBar, controller.beatUnit) == (6, 8)
    with qtbot.waitSignal(controller.feedbackRequested, timeout=500):
        controller.setTimeSignature(5, 4)
    assert (controller.beatsPerBar, controller.beatUnit) == (6, 8)


@pytest.mark.parametrize("signature", ((3, 4), (6, 8), (3, 8), (2, 2)))
def test_set_time_signature_is_undoable(signature) -> None:
    controller = seeded_controller()
    controller.setTimeSignature(*signature)
    assert (controller.beatsPerBar, controller.beatUnit) == signature
    controller.undo()
    assert (controller.beatsPerBar, controller.beatUnit) == (4, 4)
    controller.redo()
    assert (controller.beatsPerBar, controller.beatUnit) == signature


def test_snap_division_changes_snap_step() -> None:
    controller = EditorController(ProjectState())
    assert controller.snap_seconds() == 0.25
    controller.setSnapDivision(4)
    assert controller.snap_seconds() == 0.5
    controller.setSnapDivision(16)
    assert controller.snap_seconds() == 0.125


def test_set_snap_division_rejects_invalid_value(qtbot) -> None:
    controller = EditorController(ProjectState())
    with qtbot.waitSignal(controller.feedbackRequested, timeout=500) as blocker:
        controller.setSnapDivision(3)
    assert "吸附" in blocker.args[0]
    assert controller.snapDivision == 8


def test_create_note_snaps_to_selected_division() -> None:
    controller = EditorController(ProjectState())
    controller.setSnapDivision(4)
    controller.createNoteAt(70, 144)  # 0.875s -> 1.0s on a 0.5s grid
    assert controller.state.notes[0].start_time == 1.0


def test_create_note_without_snap_keeps_raw_time() -> None:
    controller = EditorController(ProjectState())
    controller.setSnapEnabled(False)
    controller.createNoteAt(70, 144)
    assert controller.state.notes[0].start_time == 0.875


def test_move_gesture_without_snap_keeps_raw_delta() -> None:
    note = Note.create(id="a", pitch=60, start_time=1.0, duration=0.5)
    controller = EditorController(ProjectState((note,), frozenset({"a"})))
    controller.setSnapEnabled(False)
    controller.beginGesture()
    controller.previewMovePixels(10.0, 0.0)  # 0.125s
    controller.commitGesture()
    assert controller.state.notes[0].start_time == 1.125


def test_snap_step_follows_project_bpm() -> None:
    controller = EditorController(ProjectState())
    controller.setBpm(60)
    assert controller.snap_seconds() == 0.5


def test_export_midi_commits_current_tempo_before_writing(qtbot, tmp_path) -> None:
    # Recording integration (1213e9424) made export a synchronization boundary.
    # Never export stale playback tempo while the editor displays a newer BPM.
    note = Note.create(id="a", pitch=60, start_time=0.5, duration=0.5)
    controller = EditorController(ProjectState((note,)))
    controller.setBpm(60)
    assert controller.state.playback_tempo.bpm == 120
    target = tmp_path / "saved.mid"
    with qtbot.waitSignal(controller.midiExported, timeout=500):
        controller.exportMidi(str(target))
    payload = target.read_bytes()

    assert (1_000_000).to_bytes(3, "big") in payload
    assert controller.state.playback_tempo.bpm == 60
    controller.setBpm(100)
    with qtbot.waitSignal(controller.midiExported, timeout=500):
        controller.exportMidi(str(target))
    payload = target.read_bytes()
    assert (600_000).to_bytes(3, "big") in payload
    assert controller.state.playback_tempo.bpm == 100


def test_load_project_restores_tempo(qtbot, tmp_path) -> None:
    from ai_piano_studio.persistence.project_json import save_project

    target = tmp_path / "waltz.pianoai.json"
    state = ProjectState(
        (Note.create(id="a", pitch=60, start_time=0.0, duration=0.5),),
        tempo=TempoMap(bpm=90, beats_per_bar=3, beat_unit=4),
    )
    save_project(target, state)
    controller = seeded_controller()
    with qtbot.waitSignal(controller.loadSucceeded, timeout=500):
        controller.loadProject(str(target))
    assert controller.bpm == 90
    assert (controller.beatsPerBar, controller.beatUnit) == (3, 4)
    assert controller.isDirty is False


def test_edit_stays_pending_until_debounced_sync_commits_playback_snapshot(qtbot) -> None:
    original = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5, velocity=75)
    controller = EditorController(
        ProjectState((original,), frozenset({"a"})),
        sync_interval_ms=10,
    )

    controller.setSelectedProperty("velocity", 96)

    assert controller.state.notes[0].status == "modified"
    assert controller.state.playback_notes[0].velocity == 75
    assert controller.unsyncedNoteCount == 1
    qtbot.waitUntil(lambda: controller.syncState == "synced", timeout=500)

    assert controller.state.notes[0].status == "saved"
    assert controller.state.playback_notes[0].velocity == 96
    controller.undo()
    assert controller.state.notes[0].velocity == 75
    assert controller.state.playback_notes[0].velocity == 75


def test_delete_remains_a_tombstone_until_explicit_sync() -> None:
    controller = seeded_controller()

    controller.deleteSelected()

    assert controller.state.notes[0].status == "deleted"
    assert controller.state.playback_notes[0].id == "a"
    controller.synchronizeNow()
    assert controller.state.notes == ()
    assert controller.state.playback_notes == ()
