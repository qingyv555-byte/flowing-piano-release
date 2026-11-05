from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController


def make_note(note_id: str, start_time: float, pitch: int, duration: float) -> Note:
    return Note.create(
        id=note_id,
        pitch=pitch,
        start_time=start_time,
        duration=duration,
    )


def test_composite_model_exposes_child_ids_and_summary_roles(qapp):
    notes = (
        make_note("a", 0.0, 60, 0.2),
        make_note("b", 0.2, 60, 0.2),
    )
    editor = EditorController(ProjectState(notes))
    model = editor.compositeModel

    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, model.role("childNoteIds")) == ["a", "b"]
    assert model.data(index, model.role("triggerCount")) == 2
    assert model.data(index, model.role("startTime")) == 0.0
    assert model.data(index, model.role("endTime")) == 0.4
    assert model.data(index, model.role("pitchVariant")) is False
    assert model.data(index, model.role("childDetails")) == [
        {
            "id": "a",
            "pitch": 60,
            "noteName": "C4",
            "startTime": 0.0,
            "duration": 0.2,
            "velocity": 75,
            "instrument": "piano",
            "status": "saved",
            "releaseMode": "natural",
            "releaseMs": None,
        },
        {
            "id": "b",
            "pitch": 60,
            "noteName": "C4",
            "startTime": 0.2,
            "duration": 0.2,
            "velocity": 75,
            "instrument": "piano",
            "status": "saved",
            "releaseMode": "natural",
            "releaseMs": None,
        },
    ]


def test_composite_model_exposes_shared_music_layer_presentation(qapp):
    layer = MusicLayer.create(
        id="melody", name="主旋律", role="melody",
        custom_color="#B764FF", locked=True,
    )
    note = Note.create(
        id="a", pitch=60, start_time=0.0, duration=0.2, layer_id="melody"
    )
    editor = EditorController(ProjectState((note,), layers=(layer,)))
    model = editor.compositeModel
    index = model.index(0, 0)

    assert model.data(index, model.role("layerId")) == "melody"
    assert model.data(index, model.role("layerRole")) == "melody"
    assert model.data(index, model.role("layerCustomColor")) == "#B764FF"
    assert model.data(index, model.role("layerVisible")) is True
    assert model.data(index, model.role("layerLocked")) is True


def test_composite_model_viewport_filters_qml_rows_without_losing_all_groups(qapp):
    notes = (
        make_note("near", 1.0, 60, 0.2),
        make_note("far-time", 20.0, 60, 0.2),
        make_note("far-pitch", 1.0, 90, 0.2),
    )
    editor = EditorController(ProjectState(notes))
    model = editor.compositeModel

    assert model.rowCount() == 3
    model.set_viewport(0.0, 2.0, 48, 72)

    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), model.role("groupId")) == "composite:near"
    assert len(model.groups) == 3
    assert model.group_for_id("composite:far-time") is not None


def test_expansion_is_presentation_state_and_survives_note_state_refresh(qapp):
    notes = (
        make_note("a", 0.0, 60, 0.2),
        make_note("b", 0.2, 60, 0.2),
    )
    editor = EditorController(ProjectState(notes))
    model = editor.compositeModel
    group_id = model.data(model.index(0, 0), model.role("groupId"))

    model.set_expanded(group_id, True)
    editor.clearSelection()

    assert model.data(model.index(0, 0), model.role("expanded")) is True
    assert editor.isDirty is False


def test_selection_only_refresh_updates_composite_roles_without_reset(qapp):
    notes = (
        make_note("a", 0.0, 60, 0.2),
        make_note("b", 0.2, 60, 0.2),
    )
    editor = EditorController(ProjectState(notes))
    model = editor.compositeModel
    reset_spy = QSignalSpy(model.modelReset)
    changed_spy = QSignalSpy(model.dataChanged)

    editor.selectNotes(["a", "b"])

    assert reset_spy.count() == 0
    assert changed_spy.count() == 1
    assert model.data(model.index(0, 0), model.role("selected")) is True
    assert model.data(model.index(0, 0), model.role("selectedChildCount")) == 2
    assert editor.isDirty is False


def test_layer_presentation_change_does_not_regroup_unchanged_notes(qapp, monkeypatch):
    notes = tuple(
        make_note(f"n-{index}", index * 0.25, 60 + index % 4, 0.2)
        for index in range(200)
    )
    editor = EditorController(ProjectState(notes))
    model = editor.compositeModel
    regroup_calls = 0
    original_group = model._grouper.group

    def counted_group(*args, **kwargs):
        nonlocal regroup_calls
        regroup_calls += 1
        return original_group(*args, **kwargs)

    monkeypatch.setattr(model._grouper, "group", counted_group)

    editor.setMusicLayerProperty("layer-default", "customColor", "#8B72FF")

    assert regroup_calls == 0
    assert model.rowCount() > 0


def test_controller_returns_raw_note_details_and_derived_group_details(qapp):
    note = make_note("a", 1.0, 64, 0.5)
    editor = EditorController(ProjectState((note,)))
    model = editor.compositeModel
    group_id = model.data(model.index(0, 0), model.role("groupId"))

    assert editor.noteDetails("a") == {
        "id": "a",
        "pitch": 64,
        "noteName": "E4",
        "startTime": 1.0,
        "duration": 0.5,
        "velocity": 75,
        "instrument": "piano",
        "status": "saved",
        "releaseMode": "natural",
        "releaseMs": None,
    }
    assert editor.compositeDetails(group_id) == {
        "groupId": group_id,
        "childNoteIds": ["a"],
        "startTime": 1.0,
        "endTime": 1.5,
        "representativePitch": 64,
        "pitchMin": 64,
        "pitchMax": 64,
        "triggerCount": 1,
        "pitchVariant": False,
    }


def test_composite_model_does_not_require_a_second_qt_application(qapp):
    assert QCoreApplication.instance() is not None
    assert issubclass(type(EditorController(ProjectState()).compositeModel), QObject)


def test_composite_velocity_offset_is_one_undo_entry(qapp):
    notes = tuple(
        Note.create(
            id=f"n{i}",
            pitch=60,
            start_time=i * 0.2,
            duration=0.2,
            velocity=70 + i,
        )
        for i in range(5)
    )
    editor = EditorController(ProjectState(notes, frozenset(n.id for n in notes)))
    before_depth = editor.historyUndoDepth

    assert editor.applyCompositeOperation(
        [n.id for n in notes], "velocity_offset", 10
    )
    assert [n.velocity for n in editor.state.notes] == [80, 81, 82, 83, 84]
    assert editor.historyUndoDepth == before_depth + 1

    editor.undo()

    assert [n.velocity for n in editor.state.notes] == [70, 71, 72, 73, 74]


def test_composite_selection_summary_contains_derived_group_only(qapp):
    notes = (
        make_note("a", 0.0, 60, 0.2),
        make_note("b", 0.2, 60, 0.2),
    )
    editor = EditorController(ProjectState(notes))

    editor.selectNotes(["a", "b"])

    assert editor.compositeSelectionSummary == {
        "groupId": "composite:a,b",
        "childNoteIds": ["a", "b"],
        "startTime": 0.0,
        "endTime": 0.4,
        "representativePitch": 60,
        "pitchMin": 60,
        "pitchMax": 60,
        "triggerCount": 2,
        "pitchVariant": False,
        "count": 2,
    }
