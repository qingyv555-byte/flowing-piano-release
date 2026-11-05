from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.music_layer_list_model import MusicLayerListModel


EXPECTED_ROLES = {
    "layerId", "name", "role", "colorRole", "customColor", "instrument",
    "volume", "velocityOffset", "transpose", "timeOffset", "muted", "solo",
    "visible", "layerVisible", "locked", "pinned", "defaultReleaseMode",
    "defaultReleaseMs", "order", "noteCount",
}


def _role(model: MusicLayerListModel, name: str) -> int:
    return next(
        role for role, value in model.roleNames().items()
        if bytes(value).decode() == name
    )


def layered_state() -> ProjectState:
    melody = MusicLayer.create(
        id="melody", name="主旋律", role="melody", volume=0.8,
        pinned=True, order=0
    )
    bass = MusicLayer.create(
        id="bass", name="低音", role="bass", muted=True, order=1
    )
    notes = (
        Note.create(id="m1", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"),
        Note.create(id="m2", pitch=64, start_time=1.0, duration=0.5, layer_id="melody"),
        Note.create(id="b1", pitch=36, start_time=0.0, duration=1.0, layer_id="bass"),
    )
    return ProjectState(notes, layers=(melody, bass))


def test_music_layer_model_exposes_stable_roles_and_values() -> None:
    model = MusicLayerListModel(layered_state())

    assert {bytes(name).decode() for name in model.roleNames().values()} == EXPECTED_ROLES
    assert model.rowCount() == 2
    assert model.pinnedCount == 1
    first = model.index(0, 0)
    assert model.data(first, _role(model, "layerId")) == "melody"
    assert model.data(first, _role(model, "role")) == "melody"
    assert model.data(first, _role(model, "volume")) == 0.8
    assert model.data(first, _role(model, "pinned")) is True
    assert model.data(first, _role(model, "defaultReleaseMode")) == "natural"
    assert model.data(first, _role(model, "defaultReleaseMs")) is None
    assert model.data(first, _role(model, "noteCount")) == 2
    assert model.data(first, Qt.DisplayRole) is None


def test_music_layer_model_updates_counts_without_resetting_identity() -> None:
    state = layered_state()
    model = MusicLayerListModel(state)
    reset = QSignalSpy(model.modelReset)
    changed = QSignalSpy(model.dataChanged)
    extra = Note.create(
        id="m3", pitch=67, start_time=2.0, duration=0.5, layer_id="melody"
    )

    model.set_state(ProjectState((*state.notes, extra), layers=state.layers))

    assert reset.count() == 0
    assert changed.count() == 1
    assert model.data(model.index(0, 0), _role(model, "noteCount")) == 3
