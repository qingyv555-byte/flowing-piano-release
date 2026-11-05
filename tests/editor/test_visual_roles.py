"""Legacy MIDI display roles must not rewrite the musical project."""
import json

from ai_piano_studio.domain.music_layer import MusicLayer, visual_role_for_layer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.note_list_model import NoteListModel
from ai_piano_studio.editor.composite_model import CompositeNoteModel


def test_known_legacy_tracks_get_visual_roles_without_changing_authored_roles(qapp):
    layers = (
        MusicLayer.create(id="vocal", name="melodystaff:melody", order=0),
        MusicLayer.create(id="right", name="right:", order=1),
        MusicLayer.create(id="left", name="left:", order=2),
    )
    notes = tuple(Note.create(id=f"note-{i}", pitch=72-i*12, start_time=i,
                              duration=0.5, status="saved", layer_id=layer.id)
                  for i, layer in enumerate(layers))
    state = ProjectState(notes=notes, layers=layers)
    content = state.content
    model = NoteListModel(state)
    rows = model.visibleItems
    assert [row["layerRole"] for row in rows] == ["other"] * 3
    assert [row["visualRole"] for row in rows] == [
        "melody", "accompaniment", "bass"]
    assert all(layer.role == "other" for layer in state.layers)
    assert state.content == content
    minimap = json.loads(model.minimapJson)
    assert [minimap[k]["role"] for k in ("vocal", "right", "left")] == [
        "melody", "accompaniment", "bass"]
    composite = CompositeNoteModel(state)
    composite_roles = {row["layerId"]: row["visualRole"]
                       for row in composite._visible_items()}
    assert composite_roles == {
        "vocal": "melody", "right": "accompaniment", "left": "bass"}


def test_visual_roles_honor_authored_semantics_and_do_not_guess(qapp):
    melody = MusicLayer.create(id="melody", name="right:", role="bass")
    custom = MusicLayer.create(id="custom", name="left:", role="custom",
                               custom_color="#DDAA22", order=1)
    unknown = MusicLayer.create(id="unknown", name="Track 8", order=2)
    right = MusicLayer.create(id="rh", name="right hand", order=3)
    layers = (melody, custom, unknown, right)
    assert [visual_role_for_layer(layer, layers) for layer in layers] == [
        "bass", "other", "other", "melody"]


def test_explicit_melody_accompaniment_colors_are_not_changed(qapp):
    layers = (
        MusicLayer.create(id="lead", name="lead", role="melody",
                          custom_color="#AA4477", order=0),
        MusicLayer.create(id="accomp", name="piano", color_role="accompaniment",
                          order=1),
    )
    assert [visual_role_for_layer(layer, layers) for layer in layers] == [
        "melody", "accompaniment"]
