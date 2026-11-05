from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QAbstractListModel, QModelIndex, Property, Signal, Qt

from ai_piano_studio.domain.project import ProjectState


class MusicLayerListModel(QAbstractListModel):
    changed = Signal()

    _ROLE_NAMES = (
        "layerId", "name", "role", "colorRole", "customColor", "instrument",
        "volume", "velocityOffset", "transpose", "timeOffset", "muted", "solo",
        "visible", "layerVisible", "locked", "pinned", "defaultReleaseMode",
        "defaultReleaseMs", "order", "noteCount",
    )

    def __init__(self, state: ProjectState | None = None, parent=None) -> None:
        super().__init__(parent)
        self._state = state or ProjectState()
        self._roles = {
            Qt.UserRole + index + 1: name.encode()
            for index, name in enumerate(self._ROLE_NAMES)
        }
        self._note_counts = self._count_notes(self._state)

    @staticmethod
    def _count_notes(state: ProjectState) -> Counter[str]:
        return Counter(note.layer_id for note in state.active_notes)

    def roleNames(self) -> dict[int, bytes]:
        return self._roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._state.layers)

    @Property(int, notify=changed)
    def pinnedCount(self) -> int:
        return sum(layer.pinned for layer in self._state.layers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._state.layers):
            return None
        role_name = self._roles.get(role)
        if role_name is None:
            return None
        layer = self._state.layers[index.row()]
        values = {
            b"layerId": layer.id,
            b"name": layer.name,
            b"role": layer.role,
            b"colorRole": layer.color_role,
            b"customColor": layer.custom_color or "",
            b"instrument": layer.instrument,
            b"volume": layer.volume,
            b"velocityOffset": layer.velocity_offset,
            b"transpose": layer.transpose,
            b"timeOffset": layer.time_offset,
            b"muted": layer.muted,
            b"solo": layer.solo,
            b"visible": layer.visible,
            b"layerVisible": layer.visible,
            b"locked": layer.locked,
            b"pinned": layer.pinned,
            b"defaultReleaseMode": layer.default_release_mode,
            b"defaultReleaseMs": layer.default_release_ms,
            b"order": layer.order,
            b"noteCount": self._note_counts[layer.id],
        }
        return values[role_name]

    def set_state(self, state: ProjectState) -> None:
        if state == self._state:
            return
        current_ids = tuple(layer.id for layer in self._state.layers)
        next_ids = tuple(layer.id for layer in state.layers)
        next_counts = self._count_notes(state)
        if current_ids == next_ids:
            presentation_changed = (
                state.layers != self._state.layers or next_counts != self._note_counts
            )
            self._state = state
            self._note_counts = next_counts
            if not presentation_changed:
                return
            if state.layers:
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(len(state.layers) - 1, 0),
                    list(self._roles),
                )
            self.changed.emit()
            return
        self.beginResetModel()
        self._state = state
        self._note_counts = next_counts
        self.endResetModel()
        self.changed.emit()
