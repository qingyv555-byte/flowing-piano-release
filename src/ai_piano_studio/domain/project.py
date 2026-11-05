from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import uuid4

from ai_piano_studio.domain.music_layer import (
    DEFAULT_LAYER_ID,
    UNCLASSIFIED_LAYER_ID,
    MusicLayer,
)
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.domain.control_event import ControlEvent


MIN_NOTE_DURATION = 0.03125


@dataclass(frozen=True, slots=True)
class ProjectState:
    notes: tuple[Note, ...] = ()
    selected_ids: frozenset[str] = field(default_factory=frozenset)
    tempo: TempoMap = field(default_factory=TempoMap)
    playback_notes: tuple[Note, ...] | None = None
    playback_tempo: TempoMap | None = None
    layers: tuple[MusicLayer, ...] = field(
        default_factory=lambda: (MusicLayer.default(),)
    )
    controls: tuple[ControlEvent, ...] = ()
    playback_controls: tuple[ControlEvent, ...] | None = None
    _active_cache: tuple[Note, ...] | None = field(default=None, init=False, repr=False, compare=False)
    _estimated_cache: float | None = field(default=None, init=False, repr=False, compare=False)
    _playback_duration_cache: float | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        note_ids = {note.id for note in self.notes}
        if len(note_ids) != len(self.notes):
            raise ValueError("note ids must be unique")
        if not self.selected_ids <= note_ids:
            raise ValueError("selection contains an unknown note")
        if not self.layers:
            raise ValueError("project must contain at least one music layer")
        layer_ids = {layer.id for layer in self.layers}
        if len({e.id for e in self.controls}) != len(self.controls):
            raise ValueError('control ids must be unique')
        if any(e.layer_id not in layer_ids for e in self.controls):
            raise ValueError('controls reference an unknown music layer')
        playback_controls = self.controls if self.playback_controls is None else self.playback_controls
        if len({e.id for e in playback_controls}) != len(playback_controls):
            raise ValueError('playback control ids must be unique')
        if any(e.layer_id not in layer_ids for e in playback_controls):
            raise ValueError('playback controls reference an unknown music layer')
        object.__setattr__(self, 'playback_controls', playback_controls)
        if len(layer_ids) != len(self.layers):
            raise ValueError("layer ids must be unique")
        expected_orders = tuple(range(len(self.layers)))
        if tuple(layer.order for layer in self.layers) != expected_orders:
            raise ValueError("music layer order must be contiguous and match tuple order")
        seen_regular_layer = False
        for layer in self.layers:
            if layer.pinned and seen_regular_layer:
                raise ValueError("pinned music layers must precede regular layers")
            if not layer.pinned:
                seen_regular_layer = True
        unknown_layer_ids = {note.layer_id for note in self.notes} - layer_ids
        if unknown_layer_ids:
            raise ValueError(
                "notes reference an unknown music layer: "
                + ", ".join(sorted(unknown_layer_ids))
            )
        playback_notes = self.playback_notes
        if playback_notes is None:
            playback_notes = tuple(note for note in self.notes if note.status == "saved")
        playback_ids = {note.id for note in playback_notes}
        if len(playback_ids) != len(playback_notes):
            raise ValueError("playback note ids must be unique")
        unknown_playback_ids = playback_ids - note_ids
        if unknown_playback_ids:
            raise ValueError(
                "unknown playback note ids: "
                + ", ".join(sorted(unknown_playback_ids))
            )
        if any(note.status != "saved" for note in playback_notes):
            raise ValueError("playback notes must be synchronized")
        unknown_playback_layers = {
            note.layer_id for note in playback_notes
        } - layer_ids
        if unknown_playback_layers:
            raise ValueError("playback notes reference an unknown music layer")
        object.__setattr__(self, "playback_notes", playback_notes)
        object.__setattr__(self, "playback_tempo", self.playback_tempo or self.tempo)

    @property
    def selected_notes(self) -> tuple[Note, ...]:
        return tuple(note for note in self.notes if note.id in self.selected_ids)

    @property
    def active_notes(self) -> tuple[Note, ...]:
        """Timeline notes excluding only post-sync removal candidates."""
        if self._active_cache is None:
            active = tuple(note for note in self.notes if note.status != "deleted")
            object.__setattr__(self, "_active_cache", self.notes if len(active) == len(self.notes) else active)
        return self._active_cache

    @property
    def has_unsynced_changes(self) -> bool:
        return any(note.status != "saved" for note in self.notes) or self.controls != self.playback_controls

    @property
    def estimated_duration(self) -> float:
        if self._estimated_cache is not None:
            return self._estimated_cache
        result = max(
            max((note.start_time + note.duration for note in self.active_notes), default=0.0),
            max((event.time for event in self.controls), default=0.0),
        )
        object.__setattr__(self, "_estimated_cache", result)
        return result

    @property
    def playback_duration(self) -> float:
        if self._playback_duration_cache is not None:
            return self._playback_duration_cache
        layers = {layer.id: layer for layer in self.layers}
        has_solo = any(layer.solo for layer in self.layers)
        note_end = max(
            (
                max(0.0, note.start_time + layers[note.layer_id].time_offset) + note.duration
                for note in self.playback_notes
                if not layers[note.layer_id].muted
                and (not has_solo or layers[note.layer_id].solo)
            ),
            default=0.0,
        )
        result = max(note_end, max((e.time for e in self.effective_controls(True)), default=0.0))
        object.__setattr__(self, "_playback_duration_cache", result)
        return result

    @property
    def content(self) -> tuple[object, ...]:
        """The musical content: what belongs to undo history and project files."""
        return (
            self.notes, self.tempo, self.playback_notes, self.playback_tempo,
            self.layers, self.controls, self.playback_controls,
        )

    @property
    def layer_ids(self) -> frozenset[str]:
        return frozenset(layer.id for layer in self.layers)

    def layer(self, layer_id: str) -> MusicLayer:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        raise ValueError("unknown music layer id")

    def effective_notes(
        self,
        for_playback: bool = False,
        include_hidden: bool = True,
    ) -> tuple[Note, ...]:
        """Return transformed note copies for playback, rendering, or export.

        Layer offsets remain non-destructive project metadata. Visibility is an
        editor concern unless a caller explicitly excludes hidden layers; mute
        and solo affect playback only, so hiding a layer never drops export data.
        """
        source = self.playback_notes if for_playback else self.active_notes
        layers_by_id = {layer.id: layer for layer in self.layers}
        has_solo = for_playback and any(layer.solo for layer in self.layers)
        effective: list[Note] = []
        for note in source:
            layer = layers_by_id[note.layer_id]
            if not include_hidden and not layer.visible:
                continue
            if for_playback and (layer.muted or (has_solo and not layer.solo)):
                continue
            velocity = round((note.velocity + layer.velocity_offset) * layer.volume)
            effective.append(
                note.with_changes(
                    pitch=max(0, min(127, note.pitch + layer.transpose)),
                    start_time=max(0.0, note.start_time + layer.time_offset),
                    velocity=max(0, min(127, velocity)),
                    instrument=layer.instrument,
                    updated_at=note.updated_at,
                )
            )
        return tuple(effective)

    @staticmethod
    def _ordered_layers(layers: Iterable[MusicLayer]) -> tuple[MusicLayer, ...]:
        return tuple(
            layer if layer.order == index else layer.with_changes(order=index)
            for index, layer in enumerate(layers)
        )

    def _ensure_notes_editable(self, note_ids: Iterable[str]) -> None:
        requested = frozenset(note_ids)
        locked_layers = {layer.id for layer in self.layers if layer.locked}
        if any(
            note.id in requested and note.layer_id in locked_layers
            for note in self.active_notes
        ):
            raise ValueError("music layer is locked")

    def _replace(
        self,
        notes: tuple[Note, ...],
        selected_ids: frozenset[str] | None = None,
        tempo: TempoMap | None = None,
        layers: tuple[MusicLayer, ...] | None = None,
        playback_notes: tuple[Note, ...] | None = None,
        controls: tuple[ControlEvent, ...] | None = None,
        playback_controls: tuple[ControlEvent, ...] | None = None,
    ) -> ProjectState:
        return ProjectState(
            notes=notes,
            selected_ids=self.selected_ids if selected_ids is None else selected_ids,
            tempo=self.tempo if tempo is None else tempo,
            playback_notes=self.playback_notes if playback_notes is None else playback_notes,
            playback_tempo=self.playback_tempo,
            layers=self.layers if layers is None else layers,
            controls=self.controls if controls is None else controls,
            playback_controls=self.playback_controls if playback_controls is None else playback_controls,
        )

    def with_controls(self, controls: Iterable[ControlEvent]) -> ProjectState:
        events = tuple(controls)
        old = {e.id: e for e in self.controls}
        new = {e.id: e for e in events}
        changed = [e for e in (*self.controls, *events) if old.get(e.id) != new.get(e.id)]
        if any(self.layer(e.layer_id).locked for e in changed):
            raise ValueError('music layer is locked')
        return self._replace(self.notes, controls=events)

    def effective_controls(self, for_playback: bool = False) -> tuple[ControlEvent, ...]:
        source = self.playback_controls if for_playback else self.controls
        layers = {layer.id:layer for layer in self.layers}
        return tuple(e.with_changes(time=max(0.0, e.time + layers[e.layer_id].time_offset))
                     for e in source)

    @staticmethod
    def _edited(note: Note, **changes: object) -> Note:
        if note.status == "deleted":
            return note
        status = "pending" if note.status == "pending" else "modified"
        return note.with_changes(status=status, **changes)

    def select_ids(self, note_ids: Iterable[str], *, additive: bool = False) -> ProjectState:
        locked_layer_ids = {layer.id for layer in self.layers if layer.locked}
        known_ids = {
            note.id
            for note in self.active_notes
            if note.layer_id not in locked_layer_ids
        }
        requested = frozenset(note_ids) & known_ids
        selected = (self.selected_ids & known_ids) | requested if additive else requested
        return self._replace(self.notes, frozenset(selected))

    def select_only(self, note_id: str) -> ProjectState:
        return self.select_ids({note_id})

    def toggle_selection(self, note_id: str) -> ProjectState:
        note = next((note for note in self.active_notes if note.id == note_id), None)
        if note is None or self.layer(note.layer_id).locked:
            return self
        selected = set(self.selected_ids)
        selected.symmetric_difference_update({note_id})
        return self._replace(self.notes, frozenset(selected))

    def clear_selection(self) -> ProjectState:
        return self._replace(self.notes, frozenset())

    def add_note(self, note: Note, *, select: bool = True) -> ProjectState:
        target_layer = self.layer(note.layer_id)
        if target_layer.locked:
            raise ValueError("music layer is locked")
        if note.status == "saved":
            note = note.with_changes(status="pending")
        selected = frozenset({note.id}) if select else self.selected_ids
        return self._replace((*self.notes, note), selected)

    def update_selected(self, **changes: object) -> ProjectState:
        self._ensure_notes_editable(self.selected_ids)
        notes = tuple(
            self._edited(note, **changes) if note.id in self.selected_ids else note
            for note in self.notes
        )
        return self._replace(notes)

    def offset_selected_velocity(self, delta_velocity: int) -> ProjectState:
        if not isinstance(delta_velocity, int) or isinstance(delta_velocity, bool):
            raise ValueError("delta_velocity must be an integer")
        if delta_velocity == 0 or not self.selected_ids:
            return self
        self._ensure_notes_editable(self.selected_ids)
        notes = tuple(
            self._edited(
                note,
                velocity=max(0, min(127, note.velocity + delta_velocity)),
            )
            if note.id in self.selected_ids
            else note
            for note in self.notes
        )
        return self._replace(notes)

    def update_note(self, note_id: str, **changes: object) -> ProjectState:
        if not any(note.id == note_id for note in self.active_notes):
            raise ValueError("unknown note id")
        self._ensure_notes_editable({note_id})
        notes = tuple(
            self._edited(note, **changes) if note.id == note_id else note
            for note in self.notes
        )
        return self._replace(notes)

    def move_selected(self, *, delta_time: float, delta_pitch: int) -> ProjectState:
        self._ensure_notes_editable(self.selected_ids)
        notes = tuple(
            self._edited(
                note,
                start_time=max(0.0, note.start_time + delta_time),
                pitch=max(0, min(127, note.pitch + delta_pitch)),
            )
            if note.id in self.selected_ids
            else note
            for note in self.notes
        )
        return self._replace(notes)

    def resize_selected(self, *, delta_duration: float) -> ProjectState:
        self._ensure_notes_editable(self.selected_ids)
        notes = tuple(
            self._edited(note, duration=max(MIN_NOTE_DURATION, note.duration + delta_duration))
            if note.id in self.selected_ids
            else note
            for note in self.notes
        )
        return self._replace(notes)

    def delete_selected(self) -> ProjectState:
        self._ensure_notes_editable(self.selected_ids)
        notes = tuple(
            note.with_changes(status="deleted") if note.id in self.selected_ids else note
            for note in self.notes
        )
        return self._replace(notes, frozenset())

    def synchronize(self) -> ProjectState:
        """Commit all current timeline edits as the next playback snapshot."""
        notes = tuple(
            note.with_changes(status="saved")
            for note in self.notes
            if note.status != "deleted"
        )
        selected_ids = self.selected_ids & {note.id for note in notes}
        return ProjectState(
            notes=notes,
            selected_ids=selected_ids,
            tempo=self.tempo,
            playback_notes=notes,
            playback_tempo=self.tempo,
            layers=self.layers,
            controls=self.controls,
            playback_controls=self.controls,
        )

    def with_tempo(
        self, tempo: TempoMap, *, preserve_note_seconds: bool = False
    ) -> ProjectState:
        """Change tempo, preserving beats by default or authored event seconds."""
        self._ensure_notes_editable(note.id for note in self.active_notes)
        if any(self.layer(e.layer_id).locked for e in self.controls):
            raise ValueError('music layer is locked')
        if preserve_note_seconds:
            return self._replace(self.notes, tempo=tempo)
        notes = tuple(
            self._edited(
                note,
                start_time=tempo.rescale_seconds(note.start_time, self.tempo),
                duration=max(
                    MIN_NOTE_DURATION,
                    tempo.rescale_seconds(note.duration, self.tempo),
                ),
            )
            for note in self.notes
        )
        controls = tuple(e.with_changes(time=tempo.rescale_seconds(e.time, self.tempo))
                         for e in self.controls)
        return self._replace(notes, tempo=tempo, controls=controls)

    def add_layer(self, layer: MusicLayer) -> ProjectState:
        if layer.id in self.layer_ids:
            raise ValueError("layer ids must be unique")
        layers = self._ordered_layers((*self.layers, layer))
        return self._replace(self.notes, layers=layers)

    def create_layer_from_selection(self, layer: MusicLayer) -> ProjectState:
        """Create a layer and move the current selection in one state transition."""

        return self.add_layer(layer).move_selected_to_layer(layer.id)

    def update_layer(self, layer_id: str, **changes: object) -> ProjectState:
        current_layer = self.layer(layer_id)
        if "id" in changes and changes["id"] != layer_id:
            raise ValueError("music layer id cannot be changed")
        layers = tuple(
            layer.with_changes(**changes) if layer.id == layer_id else layer
            for layer in self.layers
        )
        selected_ids = self.selected_ids
        if changes.get("locked") is True and not current_layer.locked:
            layer_note_ids = {
                note.id for note in self.active_notes if note.layer_id == layer_id
            }
            selected_ids = selected_ids - layer_note_ids
        return self._replace(
            self.notes,
            selected_ids=selected_ids,
            layers=self._ordered_layers(layers),
        )

    def reorder_layer(self, layer_id: str, new_index: int) -> ProjectState:
        if (
            isinstance(new_index, bool)
            or not isinstance(new_index, int)
            or not 0 <= new_index < len(self.layers)
        ):
            raise ValueError("new layer index is out of range")
        target = self.layer(layer_id)
        layers = [layer for layer in self.layers if layer.id != layer_id]
        pinned_count = sum(layer.pinned for layer in self.layers)
        if target.pinned:
            bounded_index = max(0, min(pinned_count - 1, new_index))
        else:
            bounded_index = max(pinned_count, min(len(self.layers) - 1, new_index))
        layers.insert(bounded_index, target)
        return self._replace(self.notes, layers=self._ordered_layers(layers))

    def set_layer_pinned(self, layer_id: str, pinned: bool) -> ProjectState:
        if not isinstance(pinned, bool):
            raise ValueError("pinned must be a boolean")
        target = self.layer(layer_id)
        if target.pinned is pinned:
            return self
        layers = [layer for layer in self.layers if layer.id != layer_id]
        insertion_index = sum(layer.pinned for layer in layers)
        layers.insert(insertion_index, target.with_changes(pinned=pinned))
        return self._replace(self.notes, layers=self._ordered_layers(layers))

    def duplicate_layer(
        self, layer_id: str, new_layer: MusicLayer
    ) -> ProjectState:
        source = self.layer(layer_id)
        if new_layer.id in self.layer_ids:
            raise ValueError("layer ids must be unique")
        insertion_index = (
            sum(layer.pinned for layer in self.layers)
            if source.pinned else source.order + 1
        )
        layers = list(self.layers)
        layers.insert(insertion_index, new_layer)
        duplicate_notes = tuple(
            note.with_changes(
                id=str(uuid4()), layer_id=new_layer.id, status="pending",
                channel=None, track=None, on_order=None, off_order=None,
            )
            for note in self.active_notes
            if note.layer_id == layer_id
        )
        return self._replace(
            (*self.notes, *duplicate_notes),
            selected_ids=frozenset(note.id for note in duplicate_notes),
            layers=self._ordered_layers(layers),
            controls=(*self.controls, *(e.with_changes(id=str(uuid4()), layer_id=new_layer.id, channel=None, track=None) for e in self.controls if e.layer_id == layer_id)),
        )

    def delete_layer(
        self,
        layer_id: str,
        *,
        note_policy: str = "reject",
    ) -> ProjectState:
        source = self.layer(layer_id)
        if note_policy not in {"reject", "move_to_unclassified", "delete_notes"}:
            raise ValueError("unknown music layer note policy")
        source_note_ids = {
            note.id for note in self.notes if note.layer_id == layer_id
        }
        contains_content = bool(source_note_ids) or any(e.layer_id == layer_id for e in (*self.controls, *self.playback_controls))
        replaces_last_layer = (
            contains_content
            and (
                note_policy == "delete_notes"
                or (
                    note_policy == "move_to_unclassified"
                    and layer_id != UNCLASSIFIED_LAYER_ID
                )
            )
        )
        if len(self.layers) == 1 and not replaces_last_layer:
            raise ValueError("cannot delete the last music layer")
        if contains_content and source.locked:
            raise ValueError("music layer is locked")
        if contains_content and note_policy == "reject":
            raise ValueError("music layer contains notes")

        remaining_layers = [layer for layer in self.layers if layer.id != layer_id]
        notes = self.notes
        playback_notes = self.playback_notes
        controls, playback_controls = self.controls, self.playback_controls
        selected_ids = self.selected_ids
        if contains_content and note_policy == "move_to_unclassified":
            target_id = UNCLASSIFIED_LAYER_ID
            if layer_id == UNCLASSIFIED_LAYER_ID:
                target_id = (
                    DEFAULT_LAYER_ID
                    if DEFAULT_LAYER_ID in self.layer_ids
                    else remaining_layers[0].id
                )
            if target_id not in {layer.id for layer in remaining_layers}:
                remaining_layers.append(
                    MusicLayer.unclassified(order=len(remaining_layers))
                )
            notes = tuple(
                note.with_changes(layer_id=target_id)
                if note.layer_id == layer_id else note
                for note in self.notes
            )
            playback_notes = tuple(
                note.with_changes(layer_id=target_id)
                if note.layer_id == layer_id else note
                for note in self.playback_notes
            )
            controls = tuple(e.with_changes(layer_id=target_id) if e.layer_id == layer_id else e for e in controls)
            playback_controls = tuple(e.with_changes(layer_id=target_id) if e.layer_id == layer_id else e for e in playback_controls)
        elif contains_content and note_policy == "delete_notes":
            controls = tuple(e for e in controls if e.layer_id != layer_id)
            playback_controls = tuple(e for e in playback_controls if e.layer_id != layer_id)
            notes = tuple(note for note in self.notes if note.layer_id != layer_id)
            playback_notes = tuple(
                note for note in self.playback_notes if note.layer_id != layer_id
            )
            selected_ids = self.selected_ids - source_note_ids
            if not remaining_layers:
                remaining_layers.append(MusicLayer.default())

        return self._replace(
            notes,
            selected_ids=frozenset(selected_ids),
            layers=self._ordered_layers(remaining_layers),
            playback_notes=playback_notes,
            controls=controls,
            playback_controls=playback_controls,
        )

    def select_layer(self, layer_id: str) -> ProjectState:
        if self.layer(layer_id).locked:
            return self._replace(self.notes, frozenset())
        return self.select_ids(
            note.id for note in self.active_notes if note.layer_id == layer_id
        )

    def move_selected_to_layer(self, layer_id: str) -> ProjectState:
        target = self.layer(layer_id)
        if target.locked:
            raise ValueError("music layer is locked")
        self._ensure_notes_editable(self.selected_ids)
        notes = tuple(
            self._edited(note, layer_id=layer_id)
            if note.id in self.selected_ids else note
            for note in self.notes
        )
        return self._replace(notes)
