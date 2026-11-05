from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Property, QAbstractListModel, QModelIndex, Qt, Signal, Slot

from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.types import HarmonyAnalysisResult


logger = logging.getLogger(__name__)


class HarmonyPreviewModel(QAbstractListModel):
    """Display-only harmony candidates derived from accepted analysis results."""

    changed = Signal()

    measureIndexRole = Qt.UserRole + 1
    startSecondsRole = Qt.UserRole + 2
    durationSecondsRole = Qt.UserRole + 3
    completeRole = Qt.UserRole + 4
    temporaryRole = Qt.UserRole + 5
    selectedCandidateIndexRole = Qt.UserRole + 6
    chordSymbolRole = Qt.UserRole + 7
    chineseNameRole = Qt.UserRole + 8
    notesTextRole = Qt.UserRole + 9
    candidatesRole = Qt.UserRole + 10
    reasonRole = Qt.UserRole + 11

    def __init__(self, editor: EditorController, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._rows: list[dict[str, object]] = []
        self._project_identity: str | None = None
        self._project_identity_provider: Callable[[], str] | None = None
        self._tempo = editor.state.tempo
        self._roles = {
            self.measureIndexRole: b"measureIndex",
            self.startSecondsRole: b"startSeconds",
            self.durationSecondsRole: b"durationSeconds",
            self.completeRole: b"complete",
            self.temporaryRole: b"temporary",
            self.selectedCandidateIndexRole: b"selectedCandidateIndex",
            self.chordSymbolRole: b"chordSymbol",
            self.chineseNameRole: b"chineseName",
            self.notesTextRole: b"notesText",
            self.candidatesRole: b"candidates",
            self.reasonRole: b"reason",
        }
        editor.stateChanged.connect(self._editor_state_changed)

    def roleNames(self) -> dict[int, bytes]:
        return self._roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        return self._rows[index.row()].get(self._roles.get(role, b"").decode())

    @Property("QVariantList", notify=changed)
    def virtualNotes(self) -> list[dict[str, object]]:
        notes: list[dict[str, object]] = []
        for row in self._rows:
            candidate_index = int(row["selectedCandidateIndex"])
            if candidate_index < 0:
                continue
            for pitch in row["candidatePitches"][candidate_index]:
                notes.append(
                    {
                        "measureIndex": row["measureIndex"],
                        "startSeconds": row["startSeconds"],
                        "durationSeconds": row["durationSeconds"],
                        "pitch": pitch,
                        "temporary": row["temporary"],
                    }
                )
        return notes

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._rows)

    def apply_result(self, result: HarmonyAnalysisResult) -> None:
        if result.mode != "preview":
            self.clear()
            return
        if (
            self._project_identity_provider is not None
            and result.project_identity != self._project_identity_provider()
        ):
            logger.warning("Rejected harmony preview result for a stale project")
            return
        if (
            self._project_identity is not None
            and result.project_identity != self._project_identity
        ):
            logger.warning("Rejected harmony preview result for a stale project")
            return
        previous_rows = {row["measureIndex"]: row for row in self._rows}
        rows = [
            self._build_row(suggestion, result, previous_rows.get(suggestion.measure_index))
            for suggestion in result.suggestions
        ]
        same_measures = [row["measureIndex"] for row in self._rows] == [
            row["measureIndex"] for row in rows
        ]
        if same_measures:
            changed = rows != self._rows
            self._rows = rows
            self._project_identity = result.project_identity
            if changed and rows:
                self.dataChanged.emit(
                    self.index(0, 0), self.index(len(rows) - 1, 0), list(self._roles)
                )
                self.changed.emit()
            return
        self.beginResetModel()
        self._rows = rows
        self._project_identity = result.project_identity
        self.endResetModel()
        self.changed.emit()

    def set_project_identity_provider(self, provider: Callable[[], str]) -> None:
        self._project_identity_provider = provider

    def _build_row(self, suggestion, result: HarmonyAnalysisResult, previous: dict | None) -> dict[str, object]:
        candidates = self._stable_candidates(suggestion.candidates[:3], previous)
        displays = [self._candidate_display(candidate) for candidate in candidates]
        candidate_keys = [self._candidate_key(candidate) for candidate in candidates]
        selected = self._selected_index(candidate_keys, previous)
        selected_display = displays[selected] if selected >= 0 else {}
        return {
            "measureIndex": suggestion.measure_index,
            "startTicks": suggestion.start_tick,
            "durationTicks": suggestion.duration_ticks,
            "startSeconds": self._editor.state.tempo.ticks_to_seconds(suggestion.start_tick),
            "durationSeconds": self._editor.state.tempo.ticks_to_seconds(
                suggestion.duration_ticks
            ),
            "complete": bool(candidates),
            "temporary": suggestion.temporary,
            "selectedCandidateIndex": selected,
            "chordSymbol": selected_display.get("chordSymbol", ""),
            "chineseName": selected_display.get("chineseName", ""),
            "notesText": selected_display.get("notesText", ""),
            "candidates": displays,
            "candidatePitches": [candidate.pitches for candidate in candidates],
            "candidateKeys": candidate_keys,
            "reason": suggestion.message or result.message or "",
        }

    @staticmethod
    def _candidate_key(candidate) -> tuple[str, str, tuple[int, ...]]:
        return candidate.chord_symbol, candidate.quality, candidate.pitches

    def _stable_candidates(self, candidates, previous: dict | None):
        if previous is None:
            return candidates
        previous_keys = previous.get("candidateKeys", [])
        current_by_key = {self._candidate_key(candidate): candidate for candidate in candidates}
        if set(previous_keys) != set(current_by_key):
            return candidates
        previous_scores = {
            key: display["score"]
            for key, display in zip(previous_keys, previous["candidates"])
        }
        if any(
            abs(candidate.score.total - previous_scores[key]) >= 0.03
            for key, candidate in current_by_key.items()
        ):
            return candidates
        return tuple(current_by_key[key] for key in previous_keys)

    @staticmethod
    def _selected_index(candidate_keys, previous: dict | None) -> int:
        if not candidate_keys:
            return -1
        if previous is None:
            return 0
        previous_index = int(previous["selectedCandidateIndex"])
        if 0 <= previous_index < len(previous["candidateKeys"]):
            previous_key = previous["candidateKeys"][previous_index]
            if previous_key in candidate_keys:
                return candidate_keys.index(previous_key)
        return 0

    def _editor_state_changed(self) -> None:
        tempo = self._editor.state.tempo
        if tempo == self._tempo:
            return
        self._tempo = tempo
        if not self._rows:
            return
        for row in self._rows:
            row["startSeconds"] = tempo.ticks_to_seconds(row["startTicks"])
            row["durationSeconds"] = tempo.ticks_to_seconds(row["durationTicks"])
        self.dataChanged.emit(
            self.index(0, 0), self.index(len(self._rows) - 1, 0), list(self._roles)
        )
        self.changed.emit()

    @Slot(int, int)
    def selectCandidate(self, measure_index: int, candidate_index: int) -> None:
        for row_index, row in enumerate(self._rows):
            if row["measureIndex"] != measure_index:
                continue
            candidates = row["candidates"]
            if not 0 <= candidate_index < len(candidates):
                return
            if candidate_index == row["selectedCandidateIndex"]:
                return
            candidate = candidates[candidate_index]
            row["selectedCandidateIndex"] = candidate_index
            row["chordSymbol"] = candidate["chordSymbol"]
            row["chineseName"] = candidate["chineseName"]
            row["notesText"] = candidate["notesText"]
            model_index = self.index(row_index, 0)
            self.dataChanged.emit(model_index, model_index, list(self._roles))
            self.changed.emit()
            return

    def candidate_pitches(self, measure_index: int, candidate_index: int) -> tuple[int, ...] | None:
        """Return one displayed candidate's pitches without exposing row storage."""
        for row in self._rows:
            if row["measureIndex"] != measure_index:
                continue
            candidates = row["candidatePitches"]
            if 0 <= candidate_index < len(candidates):
                return tuple(candidates[candidate_index])
            return None
        return None

    def selected_candidate_key(
        self, measure_index: int
    ) -> tuple[str, str, tuple[int, ...]] | None:
        for row in self._rows:
            if row["measureIndex"] != measure_index:
                continue
            selected_index = int(row["selectedCandidateIndex"])
            candidate_keys = row["candidateKeys"]
            if 0 <= selected_index < len(candidate_keys):
                return candidate_keys[selected_index]
            return None
        return None

    @Slot()
    def clear(self) -> None:
        if not self._rows and self._project_identity is None:
            return
        self.beginResetModel()
        self._rows = []
        self._project_identity = None
        self.endResetModel()
        self.changed.emit()

    @Slot(int)
    def dismissMeasure(self, measure_index: int) -> None:
        for row_index, row in enumerate(self._rows):
            if row["measureIndex"] != measure_index:
                continue
            self.beginRemoveRows(QModelIndex(), row_index, row_index)
            self._rows.pop(row_index)
            self.endRemoveRows()
            self.changed.emit()
            return

    @staticmethod
    def _candidate_display(candidate) -> dict[str, object]:
        return {
            "chordSymbol": candidate.chord_symbol,
            "chineseName": HarmonyPreviewModel._chinese_name(candidate.quality),
            "notesText": HarmonyPreviewModel._notes_text(candidate.pitches),
            "score": candidate.score.total,
        }

    @staticmethod
    def _chinese_name(quality: str) -> str:
        return {
            "major": "大三和弦",
            "minor": "小三和弦",
            "diminished": "减三和弦",
        }.get(quality, quality)

    @staticmethod
    def _notes_text(pitches: tuple[int, ...]) -> str:
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        return " ".join(f"{names[pitch % 12]}{pitch // 12 - 1}" for pitch in pitches)
