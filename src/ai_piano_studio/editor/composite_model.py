"""Qt presentation model for derived Composite Note ranges."""

from __future__ import annotations

import json

from PySide6.QtCore import QAbstractListModel, QModelIndex, Property, Qt, Signal, Slot

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.music_layer import visual_role_for_layer
from ai_piano_studio.editor.composite import (
    DEFAULT_COMPOSITE_GROUPING,
    CompositeGroupingConfig,
    CompositeGroupingMode,
    CompositeGroup,
    CompositeNoteGrouper,
)


class CompositeNoteModel(QAbstractListModel):
    """Expose derived groups while keeping ProjectState as the source of truth."""

    changed = Signal()

    _ROLE_NAMES = (
        "groupId",
        "childNoteIds",
        "childDetails",
        "startTime",
        "endTime",
        "representativePitch",
        "pitchMin",
        "pitchMax",
        "triggerCount",
        "pitchVariant",
        "selected",
        "selectedChildCount",
        "expanded",
        "layerId",
        "layerRole",
        "visualRole",
        "layerCustomColor",
        "layerVisible",
        "layerLocked",
    )

    def __init__(
        self,
        state: ProjectState | None = None,
        parent=None,
        *,
        grouping_mode: CompositeGroupingMode | str = CompositeGroupingMode.SMART,
        grouping_config: CompositeGroupingConfig = DEFAULT_COMPOSITE_GROUPING,
    ) -> None:
        super().__init__(parent)
        self._state = state or ProjectState()
        self._grouper = CompositeNoteGrouper()
        self._grouping_mode = CompositeGroupingMode(grouping_mode)
        self._grouping_config = grouping_config
        self._expanded_ids: set[str] = set()
        self._all_groups: tuple[CompositeGroup, ...] = self._grouper.group(
            self._state.active_notes,
            self._grouping_mode,
            self._grouping_config,
        )
        self._visible_groups: tuple[CompositeGroup, ...] = self._all_groups
        self._notes_by_id = {note.id: note for note in self._state.active_notes}
        self._visible_json_source = None
        self._visible_json = '[]'
        self._child_details_cache: dict[str, list[dict[str, object]]] = {}
        self._visible_item_cache: tuple[dict[str, object], ...] | None = None
        self._viewport: tuple[float, float, int, int] | None = None
        self._roles = {
            Qt.UserRole + index + 1: name.encode()
            for index, name in enumerate(self._ROLE_NAMES)
        }
        self._role_by_name = {
            name: role for role, encoded in self._roles.items() for name in [encoded.decode()]
        }

    def roleNames(self) -> dict[int, bytes]:
        return self._roles

    def role(self, name: str) -> int:
        """Return a role number for tests and small QML adapters."""

        try:
            return self._role_by_name[name]
        except KeyError as error:
            raise KeyError(f"unknown composite role: {name}") from error

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible_groups)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._visible_groups):
            return None
        group = self._visible_groups[index.row()]
        layer_values = self._layer_values(group)
        values = {
            "groupId": group.group_id,
            "childNoteIds": list(group.child_note_ids),
            "childDetails": self._child_details(group),
            "startTime": group.start_time,
            "endTime": group.end_time,
            "representativePitch": group.representative_pitch,
            "pitchMin": group.pitch_min,
            "pitchMax": group.pitch_max,
            "triggerCount": group.trigger_count,
            "pitchVariant": group.has_pitch_variation,
            "selected": bool(group.child_note_ids)
            and all(note_id in self._state.selected_ids for note_id in group.child_note_ids),
            "selectedChildCount": sum(
                note_id in self._state.selected_ids for note_id in group.child_note_ids
            ),
            "expanded": group.group_id in self._expanded_ids,
            **layer_values,
        }
        role_name = self._roles.get(role)
        return values.get(role_name.decode()) if role_name is not None else None

    @property
    def groups(self) -> tuple[CompositeGroup, ...]:
        """Return all derived groups for selection and inspector queries."""

        return self._all_groups

    @property
    def visible_groups(self) -> tuple[CompositeGroup, ...]:
        return self._visible_groups

    @Property(int, notify=changed)
    def visibleCount(self) -> int:
        return len(self._visible_groups)

    @Property(int, notify=changed)
    def totalCount(self) -> int:
        """Total derived groups, independent of the current viewport cull."""

        return len(self._all_groups)

    @Property("QVariantList", notify=changed)
    def visibleItems(self) -> list[dict[str, object]]:
        return list(self._visible_items())

    @Property(str, notify=changed)
    def visibleItemsJson(self):
        source = self._visible_items()
        if source is not self._visible_json_source:
            self._visible_json = json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            self._visible_json_source = source
        return self._visible_json

    @Property("QVariantList", notify=changed)
    def compositeItems(self) -> list[dict[str, object]]:
        return [item for item in self._visible_items() if item["triggerCount"] > 1]

    @Property("QVariantList", notify=changed)
    def singletonItems(self) -> list[dict[str, object]]:
        return [item for item in self._visible_items() if item["triggerCount"] == 1]

    def group_for_id(self, group_id: str) -> CompositeGroup | None:
        return next((group for group in self._all_groups if group.group_id == group_id), None)

    def set_state(self, state: ProjectState) -> None:
        if not isinstance(state, ProjectState):
            raise TypeError("state must be a ProjectState")
        next_active_notes = state.active_notes
        if next_active_notes == self._state.active_notes:
            self._state = state
            self._visible_item_cache = None
            if self._visible_groups:
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(len(self._visible_groups) - 1, 0),
                    list(self._roles),
                )
            self.changed.emit()
            return
        next_all_groups = self._grouper.group(
            next_active_notes,
            self._grouping_mode,
            self._grouping_config,
        )
        self._notes_by_id = {note.id: note for note in next_active_notes}
        self._child_details_cache.clear()
        self._visible_item_cache = None
        next_groups = self._filter_groups(next_all_groups)
        groups_changed = next_groups != self._visible_groups
        self._state = state
        self._all_groups = next_all_groups
        next_ids = {group.group_id for group in next_all_groups}
        self._expanded_ids.intersection_update(next_ids)
        if groups_changed:
            self.beginResetModel()
            self._visible_groups = next_groups
            self.endResetModel()
        else:
            self._visible_groups = next_groups
            if self._visible_groups:
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(len(self._visible_groups) - 1, 0),
                    list(self._roles),
                )
        self.changed.emit()

    @Slot(float, float, int, int)
    def set_viewport(
        self,
        start_time: float,
        end_time: float,
        lowest_pitch: int,
        highest_pitch: int,
    ) -> None:
        """Bound the QML model to the currently visible Piano Roll window."""

        values = (float(start_time), float(end_time), int(lowest_pitch), int(highest_pitch))
        if values[1] < values[0]:
            values = (values[0], values[0], values[2], values[3])
        if values[3] < values[2]:
            values = (values[0], values[1], values[3], values[3])
        if values == self._viewport:
            return
        self._viewport = values
        next_groups = self._filter_groups(self._all_groups)
        self._visible_item_cache = None
        if next_groups == self._visible_groups:
            return
        self.beginResetModel()
        self._visible_groups = next_groups
        self.endResetModel()
        self.changed.emit()

    def _filter_groups(
        self, groups: tuple[CompositeGroup, ...]
    ) -> tuple[CompositeGroup, ...]:
        if self._viewport is None:
            return groups
        start_time, end_time, lowest_pitch, highest_pitch = self._viewport
        return tuple(
            group
            for group in groups
            if group.end_time >= start_time
            and group.start_time <= end_time
            and group.pitch_max >= lowest_pitch
            and group.pitch_min <= highest_pitch
        )

    def _visible_items(self) -> tuple[dict[str, object], ...]:
        if self._visible_item_cache is None:
            self._visible_item_cache = tuple(
                {
                    "groupId": group.group_id,
                    "childNoteIds": list(group.child_note_ids),
                    "childDetails": self._child_details(group)
                    if group.trigger_count > 1
                    else [],
                    "startTime": group.start_time,
                    "endTime": group.end_time,
                    "representativePitch": group.representative_pitch,
                    "pitchMin": group.pitch_min,
                    "pitchMax": group.pitch_max,
                    "triggerCount": group.trigger_count,
                    "pitchVariant": group.has_pitch_variation,
                    "selected": bool(group.child_note_ids)
                    and all(note_id in self._state.selected_ids for note_id in group.child_note_ids),
                    "selectedChildCount": sum(
                        note_id in self._state.selected_ids
                        for note_id in group.child_note_ids
                    ),
                    "expanded": group.group_id in self._expanded_ids,
                    **self._layer_values(group),
                }
                for group in self._visible_groups
            )
        return self._visible_item_cache

    def _layer_values(self, group: CompositeGroup) -> dict[str, object]:
        first_note = self._notes_by_id.get(group.child_note_ids[0])
        layer = self._state.layer(first_note.layer_id) if first_note is not None else None
        return {
            "layerId": "" if layer is None else layer.id,
            "layerRole": "other" if layer is None else layer.role,
            "visualRole": "other" if layer is None else visual_role_for_layer(
                layer, self._state.layers),
            "layerCustomColor": "" if layer is None else (layer.custom_color or ""),
            "layerVisible": True if layer is None else layer.visible,
            "layerLocked": False if layer is None else layer.locked,
        }

    def _child_details(self, group: CompositeGroup) -> list[dict[str, object]]:
        cached = self._child_details_cache.get(group.group_id)
        if cached is not None:
            return cached
        details = []
        for note_id in group.child_note_ids:
            note = self._notes_by_id.get(note_id)
            if note is None:
                continue
            details.append(
                {
                    "id": note.id,
                    "pitch": note.pitch,
                    "noteName": note.note_name,
                    "startTime": note.start_time,
                    "duration": note.duration,
                    "velocity": note.velocity,
                    "instrument": note.instrument,
                    "status": note.status,
                    "releaseMode": note.release_mode,
                    "releaseMs": note.release_ms,
                }
            )
        self._child_details_cache[group.group_id] = details
        return details

    @Slot(str)
    def set_grouping_mode(self, mode: str) -> None:
        grouping_mode = CompositeGroupingMode(mode)
        if grouping_mode is self._grouping_mode:
            return
        self._grouping_mode = grouping_mode
        self.set_state(self._state)

    @Slot(str, bool)
    def set_expanded(self, group_id: str, expanded: bool) -> None:
        group = self.group_for_id(group_id)
        if group is None:
            return
        expanded = bool(expanded)
        current = group_id in self._expanded_ids
        if current == expanded:
            return
        if expanded:
            self._expanded_ids.add(group_id)
        else:
            self._expanded_ids.discard(group_id)
        self._visible_item_cache = None
        if group not in self._visible_groups:
            self.changed.emit()
            return
        row = self._visible_groups.index(group)
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.role("expanded")])
        self.changed.emit()

    @Slot(str)
    def toggle_expanded(self, group_id: str) -> None:
        self.set_expanded(group_id, group_id not in self._expanded_ids)
