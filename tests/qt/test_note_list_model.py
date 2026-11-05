from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.note_list_model import NoteListModel
import json
from dataclasses import replace


def test_compact_minimap_preserves_full_geometry_and_layer_changes(qapp):
    layer = MusicLayer.create(id="melody", name="旋律", role="melody")
    notes = tuple(Note.create(id=str(i), pitch=60+i, start_time=i*20,
                              duration=.5, layer_id=layer.id) for i in range(3))
    state = ProjectState(notes, layers=(layer,))
    model = NoteListModel(state)
    model.set_viewport(0, 1, 48, 72)
    cached = model.minimapJson
    assert json.loads(cached)[layer.id]["notes"] == [
        [n.start_time, n.duration, n.pitch, n.status] for n in notes
    ]
    model.set_state(replace(state, selected_ids=frozenset({"0"})))
    assert model.minimapJson is cached
    changed = replace(state, notes=(notes[0].with_changes(status="deleted"), *notes[1:]),
                      layers=(replace(layer, visible=False, muted=True, custom_color="#123456"),))
    model.set_state(changed)
    projection = json.loads(model.minimapJson)[layer.id]
    assert projection["notes"][0][3] == "deleted"
    assert not projection["visible"] and projection["muted"]
    assert projection["color"] == "#123456"
    # Insertion observers must see fresh geometry, just like the rich overview.
    seen = []
    model.rowsInserted.connect(lambda *_: seen.append(len(json.loads(model.minimapJson)[layer.id]["notes"])))
    model.set_state(replace(changed, notes=(*changed.notes, Note.create(
        id="new", pitch=70, start_time=60, duration=.5, layer_id=layer.id))))
    assert seen == [4]
    model.set_state(state)
    assert model.minimapJson == cached


def test_dense_hint_detaches_before_insert_and_reconnects_after_shrink(qapp):
    model=NoteListModel()
    order=[]
    model.totalCountChanged.connect(lambda:order.append(('hint',model.totalCount,model.rowCount())))
    model.rowsInserted.connect(lambda *_:order.append(('insert',model.totalCount,model.rowCount())))
    model.rowsRemoved.connect(lambda *_:order.append(('remove',model.totalCount,model.rowCount())))
    notes=tuple(Note.create(id=f'dense-{i}',pitch=60,start_time=i,duration=.2) for i in range(601))
    model.set_state(ProjectState(notes))
    assert order[:2]==[('hint',601,0),('insert',601,601)]
    model.set_state(ProjectState(notes[:1]))
    assert order[-2:]==[('remove',601,1),('hint',1,1)]


def test_canvas_json_observes_viewport_selection_and_overview_changes(qapp):
    notes=(Note.create(id='a',pitch=60,start_time=0,duration=.5),
           Note.create(id='b',pitch=64,start_time=30,duration=.5))
    model=NoteListModel(ProjectState(notes))
    model.set_viewport(0,2,48,84)
    assert [n['noteId'] for n in json.loads(model.visibleItemsJson)]==['a']
    assert len(json.loads(model.overviewJson)['layer-default'])==2
    model.set_state(ProjectState(notes,frozenset({'a'})))
    assert json.loads(model.visibleItemsJson)[0]['selected']
    model.set_viewport(29,32,48,84)
    assert [n['noteId'] for n in json.loads(model.visibleItemsJson)]==['b']


def _role(model: NoteListModel, name: str) -> int:
    return next(role for role, value in model.roleNames().items() if bytes(value).decode() == name)


def test_role_names_match_lightweight_note_contract() -> None:
    model = NoteListModel()
    roles = {bytes(name).decode() for name in model.roleNames().values()}
    assert roles == {
        "noteId", "pitch", "noteName", "startTime", "duration",
        "velocity", "instrument", "status", "createdAt", "updatedAt", "selected",
        "layerId", "layerRole", "visualRole", "layerVisible", "layerLocked",
        "layerMuted",
        "layerCustomColor",
    }


def test_model_exposes_canonical_values() -> None:
    note = Note.create(id="a", pitch=60, start_time=1.2, duration=0.8)
    model = NoteListModel(ProjectState((note,), frozenset({"a"})))
    index = model.index(0, 0)
    assert model.data(index, _role(model, "noteId")) == "a"
    assert model.data(index, _role(model, "noteName")) == "C4"
    assert model.data(index, _role(model, "startTime")) == 1.2
    assert model.data(index, _role(model, "status")) == "saved"
    assert model.data(index, _role(model, "createdAt")) == note.created_at
    assert model.data(index, _role(model, "updatedAt")) == note.updated_at
    assert model.data(index, _role(model, "selected")) is True
    assert model.data(index, Qt.DisplayRole) is None


def test_note_model_exposes_music_layer_presentation_roles() -> None:
    layer = MusicLayer.create(
        id="melody", name="主旋律", role="melody", visible=False, locked=True
    )
    note = Note.create(
        id="a", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"
    )
    model = NoteListModel(ProjectState((note,), layers=(layer,)))
    index = model.index(0, 0)

    assert model.data(index, _role(model, "layerId")) == "melody"
    assert model.data(index, _role(model, "layerRole")) == "melody"
    assert model.data(index, _role(model, "layerVisible")) is False
    assert model.data(index, _role(model, "layerLocked")) is True
    assert model.data(index, _role(model, "layerMuted")) is False
    assert model.data(index, _role(model, "layerCustomColor")) == ""


def test_same_note_ids_update_without_model_reset() -> None:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    model = NoteListModel(ProjectState((note,), frozenset()))
    reset_spy = QSignalSpy(model.modelReset)
    changed_spy = QSignalSpy(model.dataChanged)
    model.set_state(ProjectState((note.with_changes(start_time=1.0),), frozenset({"a"})))
    assert reset_spy.count() == 0
    assert changed_spy.count() == 1
    assert model.data(model.index(0, 0), _role(model, "startTime")) == 1.0


def test_overview_items_are_grouped_by_layer_and_not_culled_by_viewport() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody")
    bass = MusicLayer.create(id="bass", name="低音", role="bass", order=1)
    notes = (
        Note.create(
            id="near", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"
        ),
        Note.create(
            id="far", pitch=43, start_time=48.0, duration=1.0, layer_id="bass"
        ),
    )
    model = NoteListModel(ProjectState(notes, layers=(melody, bass)))

    model.set_viewport(0.0, 4.0, 48, 72)

    assert model.visibleCount == 1
    assert model.overviewByLayer == {
        "melody": [
            {
                "pitch": 60,
                "startTime": 0.0,
                "duration": 0.5,
                "status": "saved",
                "layerRole": "melody",
                "visualRole": "melody",
                "layerCustomColor": "",
                "layerVisible": True,
                "layerMuted": False,
            }
        ],
        "bass": [
            {
                "pitch": 43,
                "startTime": 48.0,
                "duration": 1.0,
                "status": "saved",
                "layerRole": "bass",
                "visualRole": "bass",
                "layerCustomColor": "",
                "layerVisible": True,
                "layerMuted": False,
            }
        ],
    }
