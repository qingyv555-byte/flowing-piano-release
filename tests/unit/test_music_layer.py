import math

import pytest

from ai_piano_studio.domain.music_layer import (
    DEFAULT_LAYER_ID,
    MusicLayer,
)
from ai_piano_studio.domain.project import ProjectState


def test_music_layer_create_uses_release_defaults() -> None:
    layer = MusicLayer.create(name="主旋律", role="melody", order=0)

    assert layer.name == "主旋律"
    assert layer.color_role == "melody"
    assert layer.volume == 1.0
    assert layer.velocity_offset == 0
    assert layer.visible is True
    assert layer.locked is False
    assert layer.pinned is False
    assert layer.default_release_mode == "natural"
    assert layer.default_release_ms is None


def test_default_music_layer_has_stable_identity() -> None:
    layer = MusicLayer.default()

    assert layer.id == DEFAULT_LAYER_ID
    assert layer.name == "默认音层"
    assert layer.order == 0


def test_custom_role_keeps_free_layer_semantics_and_uses_neutral_theme_role() -> None:
    layer = MusicLayer.create(name="环境纹理", role="custom")

    assert layer.role == "custom"
    assert layer.color_role == "other"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": " "}, "name"),
        ({"role": "lead-ish"}, "role"),
        ({"custom_color": "cyan"}, "custom_color"),
        ({"volume": -0.01}, "volume"),
        ({"volume": 1.01}, "volume"),
        ({"velocity_offset": -128}, "velocity_offset"),
        ({"velocity_offset": 128}, "velocity_offset"),
        ({"transpose": -49}, "transpose"),
        ({"transpose": 49}, "transpose"),
        ({"time_offset": math.inf}, "time_offset"),
        ({"default_release_mode": "forever"}, "release_mode"),
        ({"default_release_mode": "custom", "default_release_ms": 5001}, "release_ms"),
        ({"pinned": "yes"}, "pinned"),
    ],
)
def test_music_layer_rejects_invalid_values(changes, message) -> None:
    values = {"name": "主旋律"}
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        MusicLayer.create(**values)


def test_music_layer_round_trips_and_validates_changes() -> None:
    layer = MusicLayer.create(
        id="layer-melody",
        name="主旋律",
        role="melody",
        custom_color="#49D7FF",
        instrument="grand_piano",
        volume=0.8,
        velocity_offset=6,
        transpose=12,
        time_offset=0.125,
        muted=True,
        solo=True,
        visible=False,
        locked=True,
        pinned=True,
        default_release_mode="custom",
        default_release_ms=420,
        order=2,
    )

    assert MusicLayer.from_dict(layer.to_dict()) == layer
    assert layer.with_changes(name="旋律", locked=False).name == "旋律"
    with pytest.raises(ValueError, match="transpose"):
        layer.with_changes(transpose=60)


def test_project_state_pins_layers_ahead_of_regular_layers_without_touching_notes() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", order=0)
    vocal = MusicLayer.create(id="vocal", name="人声", order=1)
    drums = MusicLayer.create(id="drums", name="鼓", order=2)
    state = ProjectState(layers=(melody, vocal, drums))

    pinned_vocal = state.set_layer_pinned("vocal", True)
    pinned_drums = pinned_vocal.set_layer_pinned("drums", True)
    unpinned_vocal = pinned_drums.set_layer_pinned("vocal", False)

    assert [(layer.id, layer.pinned, layer.order) for layer in pinned_vocal.layers] == [
        ("vocal", True, 0),
        ("melody", False, 1),
        ("drums", False, 2),
    ]
    assert [layer.id for layer in pinned_drums.layers] == ["vocal", "drums", "melody"]
    assert [(layer.id, layer.pinned) for layer in unpinned_vocal.layers] == [
        ("drums", True),
        ("vocal", False),
        ("melody", False),
    ]
    assert unpinned_vocal.notes == state.notes
    assert unpinned_vocal.playback_notes == state.playback_notes


def test_project_state_reorder_cannot_cross_pinned_group_boundary() -> None:
    layers = (
        MusicLayer.create(id="pin-a", name="置顶 A", pinned=True, order=0),
        MusicLayer.create(id="pin-b", name="置顶 B", pinned=True, order=1),
        MusicLayer.create(id="regular-a", name="普通 A", order=2),
        MusicLayer.create(id="regular-b", name="普通 B", order=3),
    )
    state = ProjectState(layers=layers)

    regular_clamped = state.reorder_layer("regular-b", 0)
    pinned_clamped = state.reorder_layer("pin-a", 3)

    assert [layer.id for layer in regular_clamped.layers] == [
        "pin-a", "pin-b", "regular-b", "regular-a",
    ]
    assert [layer.id for layer in pinned_clamped.layers] == [
        "pin-b", "pin-a", "regular-a", "regular-b",
    ]
