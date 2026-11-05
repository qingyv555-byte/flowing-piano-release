from __future__ import annotations

import json

from PySide6.QtCore import QAbstractListModel, QModelIndex, Property, Signal, Slot, Qt

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.music_layer import visual_role_for_layer


class NoteListModel(QAbstractListModel):
    changed = Signal()
    totalCountChanged = Signal()
    overviewChanged = Signal()

    _ROLE_NAMES = (
        "noteId",
        "pitch",
        "noteName",
        "startTime",
        "duration",
        "velocity",
        "instrument",
        "status",
        "createdAt",
        "updatedAt",
        "selected",
        "layerId",
        "layerRole",
        "visualRole",
        "layerVisible",
        "layerLocked",
        "layerMuted",
        "layerCustomColor",
    )

    def __init__(self, state: ProjectState | None = None, parent=None) -> None:
        super().__init__(parent)
        self._state = state or ProjectState()
        self._total_count = len(self._state.notes)
        self._visible_notes = tuple(self._state.notes)
        self._visible_items_cache: tuple[dict[str, object], ...] | None = None
        self._overview_by_layer_cache: dict[str, list[dict[str, object]]] | None = None
        self._visible_json_source = None
        self._overview_json_source = None
        self._visible_json = '[]'
        self._overview_json = '{}'
        self._minimap_json: str | None = None
        self._viewport: tuple[float, float, int, int] | None = None
        self._roles = {
            Qt.UserRole + index + 1: name.encode()
            for index, name in enumerate(self._ROLE_NAMES)
        }

    def roleNames(self) -> dict[int, bytes]:
        return self._roles

    @Property(float, notify=overviewChanged)
    def noteDurationSeconds(self) -> float:
        """Current note extent, independent of saved playback and pedal tails."""
        return max((note.start_time + note.duration for note in self._state.active_notes), default=0.0)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._state.notes)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._state.notes):
            return None
        role_name = self._roles.get(role)
        if role_name is None:
            return None
        note = self._state.notes[index.row()]
        layer = self._state.layer(note.layer_id)
        values = {
            b"noteId": note.id,
            b"pitch": note.pitch,
            b"noteName": note.note_name,
            b"startTime": note.start_time,
            b"duration": note.duration,
            b"velocity": note.velocity,
            b"instrument": note.instrument,
            b"status": note.status,
            b"createdAt": note.created_at,
            b"updatedAt": note.updated_at,
            b"selected": note.id in self._state.selected_ids,
            b"layerId": note.layer_id,
            b"layerRole": layer.role,
            b"visualRole": visual_role_for_layer(layer, self._state.layers),
            b"layerVisible": layer.visible,
            b"layerLocked": layer.locked,
            b"layerMuted": layer.muted,
            b"layerCustomColor": layer.custom_color or "",
        }
        return values[role_name]

    @Property(int, notify=changed)
    def visibleCount(self) -> int:
        """Number of notes intersecting the current Piano Roll viewport."""

        return len(self._visible_notes)

    @Property(int, notify=totalCountChanged)
    def totalCount(self) -> int:
        """Total raw-note count, used to keep dense projects on the Canvas path."""

        return self._total_count

    @Property("QVariantList", notify=changed)
    def visibleItems(self) -> list[dict[str, object]]:
        """Small, cullable projection used by the dense Canvas renderer."""

        if self._visible_items_cache is None:
            self._visible_items_cache = tuple(
                {
                    "noteId": note.id,
                    "pitch": note.pitch,
                    "noteName": note.note_name,
                    "startTime": note.start_time,
                    "duration": note.duration,
                    "velocity": note.velocity,
                    "instrument": note.instrument,
                    "status": note.status,
                    "selected": note.id in self._state.selected_ids,
                    "layerId": note.layer_id,
                    "layerRole": self._state.layer(note.layer_id).role,
                    "visualRole": visual_role_for_layer(
                        self._state.layer(note.layer_id), self._state.layers),
                    "layerVisible": self._state.layer(note.layer_id).visible,
                    "layerLocked": self._state.layer(note.layer_id).locked,
                    "layerMuted": self._state.layer(note.layer_id).muted,
                    "layerCustomColor": self._state.layer(note.layer_id).custom_color or "",
                }
                for note in self._visible_notes
            )
        return list(self._visible_items_cache)

    @Property("QVariantMap", notify=overviewChanged)
    def overviewByLayer(self) -> dict[str, list[dict[str, object]]]:
        """Full-song, layer-grouped projection for the arrangement overview."""

        if self._overview_by_layer_cache is None:
            grouped: dict[str, list[dict[str, object]]] = {}
            for note in self._state.notes:
                layer = self._state.layer(note.layer_id)
                grouped.setdefault(note.layer_id, []).append(
                    {
                        "pitch": note.pitch,
                        "startTime": note.start_time,
                        "duration": note.duration,
                        "status": note.status,
                        "layerRole": layer.role,
                        "visualRole": visual_role_for_layer(layer, self._state.layers),
                        "layerCustomColor": layer.custom_color or "",
                        "layerVisible": layer.visible,
                        "layerMuted": layer.muted,
                    }
                )
            self._overview_by_layer_cache = grouped
        return self._overview_by_layer_cache

    @Property(str, notify=overviewChanged)
    def overviewJson(self):
        """Detach nested values from PySide's quadratic lazy QML wrappers."""
        source = self.overviewByLayer
        if source is not self._overview_json_source:
            self._overview_json = json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            self._overview_json_source = source
        return self._overview_json

    @Property(str, notify=changed)
    def visibleItemsJson(self):
        values = self.visibleItems
        if self._visible_items_cache is not self._visible_json_source:
            self._visible_json = json.dumps(values, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            self._visible_json_source = self._visible_items_cache
        return self._visible_json

    @Property(str, notify=overviewChanged)
    def minimapJson(self) -> str:
        """Compact full-score geometry; layer metadata is sent only once."""
        if self._minimap_json is None:
            grouped = {}
            for note in self._state.notes:
                if note.layer_id not in grouped:
                    layer = self._state.layer(note.layer_id)
                    grouped[note.layer_id] = {
                        "role": visual_role_for_layer(layer, self._state.layers),
                        "color": layer.custom_color or "",
                        "visible": layer.visible, "muted": layer.muted, "notes": [],
                    }
                grouped[note.layer_id]["notes"].append(
                    (note.start_time, note.duration, note.pitch, note.status)
                )
            self._minimap_json = json.dumps(grouped, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        return self._minimap_json

    @Slot(float, float, int, int)
    def set_viewport(
        self,
        start_time: float,
        end_time: float,
        lowest_pitch: int,
        highest_pitch: int,
    ) -> None:
        """Cull dense raw-note projections to the visible time/pitch window."""

        values = (float(start_time), float(end_time), int(lowest_pitch), int(highest_pitch))
        if values[1] < values[0]:
            values = (values[0], values[0], values[2], values[3])
        if values[3] < values[2]:
            values = (values[0], values[1], values[3], values[3])
        if values == self._viewport:
            return
        self._viewport = values
        next_notes = self._filter_notes(self._state.notes)
        if next_notes == self._visible_notes:
            return
        self._visible_notes = next_notes
        self._visible_items_cache = None
        self.changed.emit()

    def _filter_notes(self, notes):
        if self._viewport is None:
            return tuple(notes)
        start_time, end_time, lowest_pitch, highest_pitch = self._viewport
        return tuple(
            note
            for note in notes
            if note.start_time + note.duration >= start_time
            and note.start_time <= end_time
            and lowest_pitch <= note.pitch <= highest_pitch
        )

    def set_state(self, state: ProjectState) -> None:
        if state == self._state:
            return
        previous = self._state
        # Detach sparse QML Repeaters before publishing a large row insertion or
        # reset. On shrink, attach only after the old dense model is gone.
        if len(state.notes) > self._total_count:
            self._publish_total_count(len(state.notes))
        current_ids = tuple(note.id for note in previous.notes)
        next_ids = tuple(note.id for note in state.notes)
        overview_changed = state.notes != previous.notes or state.layers != previous.layers
        appended = len(next_ids) > len(current_ids) and next_ids[:len(current_ids)] == current_ids
        removed_tail = len(next_ids) < len(current_ids) and current_ids[:len(next_ids)] == next_ids
        if current_ids == next_ids or appended or removed_tail:
            if appended:
                self.beginInsertRows(QModelIndex(), len(current_ids), len(next_ids) - 1)
            elif removed_tail:
                self.beginRemoveRows(QModelIndex(), len(next_ids), len(current_ids) - 1)
            self._state = state
            self._visible_notes = self._filter_notes(state.notes)
            self._visible_items_cache = None
            if overview_changed:
                self._overview_by_layer_cache = None
                self._minimap_json = None
            if appended:
                self.endInsertRows()
            elif removed_tail:
                self.endRemoveRows()
            if overview_changed:
                self.overviewChanged.emit()
            # Keep surviving delegates and publish only their changed rows.
            # Selection does not invalidate pitch, timing, colors or lifecycle.
            selection_role = Qt.UserRole + self._ROLE_NAMES.index("selected") + 1
            changed_selection = previous.selected_ids ^ state.selected_ids
            layers_changed = previous.layers != state.layers
            pending_start = pending_end = -1
            pending_roles: list[int] = []
            for row, (old, new) in enumerate(zip(previous.notes, state.notes)):
                roles = (list(self._roles) if layers_changed or (old is not new and old != new)
                         else [selection_role] if new.id in changed_selection else [])
                if pending_start >= 0 and (not roles or roles != pending_roles):
                    self.dataChanged.emit(self.index(pending_start, 0), self.index(pending_end, 0), pending_roles)
                    pending_start = -1
                if roles:
                    if pending_start < 0:
                        pending_start = row
                        pending_roles = roles
                    pending_end = row
            if pending_start >= 0:
                self.dataChanged.emit(self.index(pending_start, 0), self.index(pending_end, 0), pending_roles)
            self._publish_total_count(len(state.notes))
            self.changed.emit()
            return
        self.beginResetModel()
        self._state = state
        self._visible_notes = self._filter_notes(state.notes)
        self._visible_items_cache = None
        self._overview_by_layer_cache = None
        self._minimap_json = None
        self.endResetModel()
        self._publish_total_count(len(state.notes))
        self.overviewChanged.emit()
        self.changed.emit()

    def _publish_total_count(self, count: int) -> None:
        if count != self._total_count:
            self._total_count = count
            self.totalCountChanged.emit()
