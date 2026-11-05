import pytest
from dataclasses import replace

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import (
    DEFAULT_LAYER_ID,
    UNCLASSIFIED_LAYER_ID,
    MusicLayer,
)
from ai_piano_studio.domain.project import ProjectState


def test_cached_derived_values_are_per_immutable_snapshot():
    original = ProjectState((Note.create(id="cached", pitch=60, start_time=0, duration=1, status="saved"),))
    assert original.active_notes is original.notes
    assert original.estimated_duration == original.playback_duration == 1
    changed = replace(original, notes=(original.notes[0].with_changes(duration=3),), playback_notes=None)
    assert changed.estimated_duration == changed.playback_duration == 3
    assert original.estimated_duration == original.playback_duration == 1
    # Caches are projections, never musical content or equality fields.
    uncached = replace(original)
    assert original == uncached and original.content == uncached.content


def make_state() -> ProjectState:
    first = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5, velocity=70)
    second = Note.create(id="b", pitch=64, start_time=1.0, duration=0.5, velocity=80)
    third = Note.create(id="c", pitch=67, start_time=2.0, duration=0.25, velocity=90)
    return ProjectState(notes=(first, second, third))


def test_project_rejects_duplicate_note_ids() -> None:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)

    with pytest.raises(ValueError, match="unique"):
        ProjectState(notes=(note, note))


def test_project_rejects_playback_notes_missing_from_editable_notes() -> None:
    editable = Note.create(id="editable", pitch=60, start_time=0.0, duration=0.5)
    orphan = Note.create(id="orphan", pitch=72, start_time=1.0, duration=0.5)

    with pytest.raises(ValueError, match="unknown playback note"):
        ProjectState(notes=(editable,), playback_notes=(orphan,))


def test_select_ids_replaces_or_extends_with_known_ids() -> None:
    state = make_state()

    selected = state.select_ids({"a", "missing"})
    extended = selected.select_ids({"b"}, additive=True)

    assert selected.selected_ids == frozenset({"a"})
    assert extended.selected_ids == frozenset({"a", "b"})


def test_toggle_selection_adds_and_removes_note() -> None:
    state = make_state()

    selected = state.toggle_selection("a")
    cleared = selected.toggle_selection("a")

    assert selected.selected_ids == frozenset({"a"})
    assert cleared.selected_ids == frozenset()


def test_move_resize_and_batch_edit_return_new_state() -> None:
    state = make_state().select_ids({"a", "b"})

    moved = state.move_selected(delta_time=0.25, delta_pitch=1)
    resized = moved.resize_selected(delta_duration=0.25)
    edited = resized.update_selected(velocity=90, instrument="electric_piano")

    assert [note.start_time for note in edited.selected_notes] == [0.25, 1.25]
    assert [note.pitch for note in edited.selected_notes] == [61, 65]
    assert {note.duration for note in edited.selected_notes} == {0.75}
    assert {note.velocity for note in edited.selected_notes} == {90}
    assert {note.instrument for note in edited.selected_notes} == {"electric_piano"}
    assert state.notes[0].start_time == 0.0


def test_move_selected_clamps_time_and_pitch() -> None:
    state = make_state().select_only("a")

    moved = state.move_selected(delta_time=-1.0, delta_pitch=-80)

    assert moved.selected_notes[0].start_time == 0.0
    assert moved.selected_notes[0].pitch == 0


def test_offset_selected_velocity_clamps_each_selected_note() -> None:
    notes = (
        Note.create(id="low", pitch=60, start_time=0.0, duration=0.5, velocity=4),
        Note.create(id="high", pitch=64, start_time=1.0, duration=0.5, velocity=124),
    )
    state = ProjectState(notes).select_ids({"low", "high"})

    shifted = state.offset_selected_velocity(10)

    assert [note.velocity for note in shifted.selected_notes] == [14, 127]
    assert [note.velocity for note in state.selected_notes] == [4, 124]


@pytest.mark.parametrize(
    ("release_mode", "release_ms"),
    [
        ("unknown", None),
        ("custom", -1.0),
        ("custom", 5000.1),
        ("custom", float("nan")),
        ("custom", float("inf")),
    ],
)
def test_note_rejects_invalid_release_metadata(release_mode, release_ms) -> None:
    with pytest.raises(ValueError):
        Note.create(
            pitch=60,
            start_time=0.0,
            duration=0.5,
            release_mode=release_mode,
            release_ms=release_ms,
        )


def test_custom_release_defaults_to_250_ms_and_is_not_duration() -> None:
    note = Note.create(
        pitch=60,
        start_time=0.0,
        duration=0.5,
        release_mode="custom",
    )

    assert note.release_ms == pytest.approx(250.0)
    assert note.duration == pytest.approx(0.5)


def test_resize_selected_clamps_minimum_duration() -> None:
    state = make_state().select_only("c")

    resized = state.resize_selected(delta_duration=-10.0)

    assert resized.selected_notes[0].duration == 0.03125


def test_delete_selected_keeps_unselected_notes() -> None:
    state = make_state().select_ids({"a", "c"})

    updated = state.delete_selected()

    assert [note.id for note in updated.active_notes] == ["b"]
    assert [note.status for note in updated.notes] == ["deleted", "saved", "deleted"]
    assert updated.selected_ids == frozenset()


def test_update_note_changes_single_note_and_keeps_selection() -> None:
    state = make_state().select_ids({"b"})

    updated = state.update_note("a", duration=1.25)

    assert updated.notes[0].duration == 1.25
    assert updated.notes[1].duration == 0.5
    assert updated.selected_ids == frozenset({"b"})


def test_update_note_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="unknown note"):
        make_state().update_note("missing", duration=1.0)


def test_project_starts_with_one_default_music_layer() -> None:
    state = ProjectState()

    assert len(state.layers) == 1
    assert state.layers[0].id == DEFAULT_LAYER_ID


def test_project_rejects_unknown_note_layer_and_duplicate_layer_ids() -> None:
    unknown = Note.create(
        pitch=60, start_time=0.0, duration=0.5, layer_id="missing"
    )
    with pytest.raises(ValueError, match="unknown music layer"):
        ProjectState((unknown,))

    duplicate = MusicLayer.create(id="same", name="A")
    with pytest.raises(ValueError, match="layer ids must be unique"):
        ProjectState(layers=(duplicate, duplicate))


def test_project_music_layer_operations_are_immutable_and_ordered() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody")
    bass = MusicLayer.create(id="bass", name="低音", role="bass")
    state = ProjectState().add_layer(melody).add_layer(bass)

    assert [layer.id for layer in state.layers] == [DEFAULT_LAYER_ID, "melody", "bass"]
    assert [layer.order for layer in state.layers] == [0, 1, 2]

    renamed = state.update_layer("melody", name="旋律")
    reordered = renamed.reorder_layer("bass", 0)
    duplicated = reordered.duplicate_layer(
        "melody", MusicLayer.create(id="melody-copy", name="旋律副本")
    )

    assert state.layers[1].name == "主旋律"
    assert renamed.layers[1].name == "旋律"
    assert [layer.id for layer in reordered.layers] == ["bass", DEFAULT_LAYER_ID, "melody"]
    assert [layer.order for layer in duplicated.layers] == list(range(4))


def test_layer_color_change_does_not_mutate_note_musical_data() -> None:
    note = Note.create(id="a", pitch=64, start_time=1.25, duration=0.75, velocity=91)
    state = ProjectState((note,))

    recolored = state.update_layer(DEFAULT_LAYER_ID, custom_color="#8B72FF")

    assert recolored.notes == state.notes
    assert recolored.playback_notes == state.playback_notes
    assert recolored.layer(DEFAULT_LAYER_ID).custom_color == "#8B72FF"


def test_project_selects_moves_and_deletes_music_layers_safely() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody")
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    state = ProjectState((note,)).add_layer(melody)

    moved = state.select_only("a").move_selected_to_layer("melody")
    assert moved.notes[0].layer_id == "melody"
    assert moved.select_layer("melody").selected_ids == frozenset({"a"})
    with pytest.raises(ValueError, match="contains notes"):
        moved.delete_layer("melody")

    returned = moved.move_selected_to_layer(DEFAULT_LAYER_ID)
    deleted = returned.delete_layer("melody")
    assert [layer.id for layer in deleted.layers] == [DEFAULT_LAYER_ID]
    with pytest.raises(ValueError, match="last music layer"):
        deleted.delete_layer(DEFAULT_LAYER_ID)


def test_delete_populated_layer_moves_notes_to_unclassified_in_one_state() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody", order=1)
    note = Note.create(
        id="a", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"
    )
    state = ProjectState((note,), frozenset({"a"}), layers=(MusicLayer.default(), melody))

    deleted = state.delete_layer("melody", note_policy="move_to_unclassified")

    assert [layer.id for layer in deleted.layers] == [
        DEFAULT_LAYER_ID,
        UNCLASSIFIED_LAYER_ID,
    ]
    assert deleted.layer(UNCLASSIFIED_LAYER_ID).name == "未分类"
    assert deleted.notes[0].layer_id == UNCLASSIFIED_LAYER_ID
    assert deleted.selected_ids == frozenset({"a"})


def test_delete_only_populated_layer_can_replace_it_with_unclassified() -> None:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    state = ProjectState((note,), frozenset({"a"}))

    deleted = state.delete_layer(
        DEFAULT_LAYER_ID, note_policy="move_to_unclassified"
    )

    assert [layer.id for layer in deleted.layers] == [UNCLASSIFIED_LAYER_ID]
    assert deleted.notes[0].layer_id == UNCLASSIFIED_LAYER_ID


def test_delete_notes_from_only_layer_leaves_one_empty_default_layer() -> None:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    state = ProjectState((note,), frozenset({"a"}))

    deleted = state.delete_layer(DEFAULT_LAYER_ID, note_policy="delete_notes")

    assert deleted.notes == ()
    assert deleted.selected_ids == frozenset()
    assert deleted.layers == (MusicLayer.default(),)


def test_delete_populated_layer_can_delete_its_notes() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody", order=1)
    note = Note.create(
        id="a", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"
    )
    state = ProjectState((note,), frozenset({"a"}), layers=(MusicLayer.default(), melody))

    deleted = state.delete_layer("melody", note_policy="delete_notes")

    assert [layer.id for layer in deleted.layers] == [DEFAULT_LAYER_ID]
    assert deleted.notes == ()
    assert deleted.selected_ids == frozenset()


def test_create_layer_from_selection_adds_and_moves_as_one_state() -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="b", pitch=64, start_time=0.5, duration=0.5),
    )
    layer = MusicLayer.create(id="melody", name="主旋律", role="melody")
    state = ProjectState(notes, frozenset({"a", "b"}))

    created = state.create_layer_from_selection(layer)

    assert created.layer("melody") == layer.with_changes(order=1)
    assert {note.layer_id for note in created.selected_notes} == {"melody"}


def test_locked_music_layer_rejects_note_mutation() -> None:
    locked = MusicLayer.create(id="locked", name="锁定", locked=True)
    note = Note.create(
        id="a", pitch=60, start_time=0.0, duration=0.5, layer_id="locked"
    )
    state = ProjectState((note,), frozenset({"a"}), layers=(locked,))

    with pytest.raises(ValueError, match="locked"):
        state.update_selected(velocity=90)
    with pytest.raises(ValueError, match="locked"):
        state.delete_selected()

    unselected = ProjectState((note,), layers=(locked,))
    assert unselected.select_ids({"a"}).selected_ids == frozenset()
    assert unselected.toggle_selection("a").selected_ids == frozenset()
    assert unselected.select_layer("locked").selected_ids == frozenset()


def test_locking_music_layer_clears_its_selected_notes() -> None:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    state = ProjectState((note,), frozenset({"a"}))

    locked = state.update_layer(DEFAULT_LAYER_ID, locked=True)

    assert locked.layer(DEFAULT_LAYER_ID).locked is True
    assert locked.selected_ids == frozenset()


def test_effective_notes_apply_layer_transforms_without_mutating_sources() -> None:
    layer = MusicLayer.create(
        id="melody",
        name="主旋律",
        role="melody",
        instrument="electric_piano",
        volume=0.5,
        velocity_offset=20,
        transpose=12,
        time_offset=-0.25,
    )
    notes = (
        Note.create(
            id="quiet",
            pitch=60,
            start_time=0.0,
            duration=0.5,
            velocity=40,
            layer_id="melody",
        ),
        Note.create(
            id="loud",
            pitch=120,
            start_time=1.0,
            duration=0.5,
            velocity=120,
            layer_id="melody",
        ),
    )
    state = ProjectState(notes=notes, layers=(layer,))

    effective = state.effective_notes()

    assert [(note.pitch, note.start_time, note.velocity) for note in effective] == [
        (72, 0.0, 30),
        (127, 0.75, 70),
    ]
    assert {note.instrument for note in effective} == {"electric_piano"}
    assert state.notes == notes
    assert effective[0] is not notes[0]


def test_effective_playback_obeys_mute_and_solo_but_export_keeps_hidden_layers() -> None:
    melody = MusicLayer.create(
        id="melody", name="主旋律", role="melody", solo=True, visible=False, order=0
    )
    muted_solo = MusicLayer.create(
        id="muted", name="静音层", muted=True, solo=True, order=1
    )
    backing = MusicLayer.create(id="backing", name="伴奏", order=2)
    notes = (
        Note.create(id="m", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"),
        Note.create(id="x", pitch=62, start_time=0.5, duration=0.5, layer_id="muted"),
        Note.create(id="b", pitch=64, start_time=1.0, duration=0.5, layer_id="backing"),
    )
    state = ProjectState(notes=notes, layers=(melody, muted_solo, backing))

    assert [note.id for note in state.effective_notes(for_playback=True)] == ["m"]
    assert [note.id for note in state.effective_notes()] == ["m", "x", "b"]
    assert [note.id for note in state.effective_notes(include_hidden=False)] == ["x", "b"]
