from __future__ import annotations

import logging
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    Property,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)

from ai_piano_studio.domain.history import History
from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.music_layer import UNCLASSIFIED_LAYER_ID, MusicLayer
from ai_piano_studio.domain.note import (
    DEFAULT_CUSTOM_RELEASE_MS,
    MAX_RELEASE_MS,
    NOTE_RELEASE_MODES,
    Note,
    midi_to_name,
)
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import (
    DEFAULT_SNAP_DIVISION,
    MAX_BPM,
    MIN_BPM,
    TIME_SIGNATURES,
    TempoMap,
    validate_bpm,
    validate_snap_division,
    validate_time_signature,
)
from ai_piano_studio.editor.geometry import (
    Rect,
    notes_intersecting_rect,
    snap_time,
    x_to_time,
    y_to_pitch,
)
from ai_piano_studio.editor.composite_model import CompositeNoteModel
from ai_piano_studio.editor.note_list_model import NoteListModel
from ai_piano_studio.editor.music_layer_list_model import MusicLayerListModel
from ai_piano_studio.harmony.history_sync import HarmonyHistoryCoordinator
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata
from ai_piano_studio.persistence.autosave import NullRecoveryStore, RecoveryStore
from ai_piano_studio.persistence.midi import (
    MidiImportResult,
    export_midi,
    materialize_midi_import,
    materialize_midi_controls,
)
from ai_piano_studio.persistence.project_document import (
    ProjectDocument,
    load_project_document,
    save_project_document,
)
from ai_piano_studio.persistence.project_json import load_project
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_naming import inspect_project_name
from ai_piano_studio.persistence.recent_files import RecentFiles
from ai_piano_studio.persistence.save_preferences import (
    DEFAULT_AUTOSAVE_DELAY_SECONDS,
    VALID_AUTOSAVE_DELAYS_SECONDS,
)


logger = logging.getLogger(__name__)

OVERLAP_TIME_TOLERANCE_SECONDS = 1e-5

PIXELS_PER_SECOND = 80.0
ROW_HEIGHT = 18.0
MIN_PIXELS_PER_SECOND = 30.0
MAX_PIXELS_PER_SECOND = 320.0
MIN_ROW_HEIGHT = 12.0
MAX_ROW_HEIGHT = 32.0
HIGHEST_PITCH = 72
SNAP_SECONDS = 0.25
DEFAULT_DURATION = 0.5
MIN_DRAW_DURATION = 0.03125
AUTOSAVE_INTERVAL_MS = DEFAULT_AUTOSAVE_DELAY_SECONDS * 1000
RECOVERY_INTERVAL_MS = 2000
SYNC_INTERVAL_MS = 500


class _SaveSignals(QObject):
    succeeded = Signal(str)
    failed = Signal(str)


class _SaveJob(QRunnable):
    def __init__(
        self, path: str, document: ProjectDocument, pool_owner: QThreadPool
    ) -> None:
        super().__init__()
        self.path = path
        self.document = document
        # Keep the private pool alive if its controller is destroyed mid-save.
        self._pool_owner = pool_owner
        self.signals = _SaveSignals()

    @Slot()
    def run(self) -> None:
        try:
            save_project_document(self.path, self.document)
            self.signals.succeeded.emit(self.path)
        except Exception as error:
            logger.exception("Project save failed")
            self.signals.failed.emit(str(error))


@dataclass(frozen=True)
class _SaveContext:
    """Frozen main-thread state associated with the only active save job."""

    job: _SaveJob
    snapshot: ProjectDocument
    session_id: int
    generation: int
    operation: str = "save"
    activate_current: bool = True
    display_name: str | None = None
    inherit_metadata_from: str | None = None
    cleanup_target_parent: bool = False


class _RecycleProjectsSignals(QObject):
    completed = Signal(str, bool)
    finished = Signal()


class _RecycleProjectsJob(QRunnable):
    def __init__(self, projects: ProjectLibrary, targets: tuple[Path, ...]) -> None:
        super().__init__()
        self._projects = projects
        self._targets = targets
        self.signals = _RecycleProjectsSignals()

    @Slot()
    def run(self) -> None:
        for target in self._targets:
            try:
                self._projects.recycle(target)
            except Exception:
                logger.exception("Bulk project recycle failed for %s", target)
                self.signals.completed.emit(str(target), False)
            else:
                self.signals.completed.emit(str(target), True)
        self.signals.finished.emit()


class EditorController(QObject):
    stateChanged = Signal()
    recordingBoundaryRequested = Signal()
    recordingTakeChanged = Signal()
    pedalEventsChanged = Signal()
    _recording_layer_id = None
    _recording_write = False

    @Property(bool, notify=recordingTakeChanged)
    def recordingTakeActive(self):
        return self._recording_layer_id is not None

    def beginRecordingTake(self, layer_id):
        if self._gesture_origin is not None or self.state.layer(layer_id).locked:
            return False
        self.beginGesture()
        if self._gesture_origin is None:
            return False
        self._recording_layer_id = layer_id
        self._autosave_timer.stop()
        self.recordingTakeChanged.emit()
        return True

    def recordingCall(self, callback, *args):
        if not self.recordingTakeActive:
            raise RuntimeError('no recording take')
        self._recording_write = True
        try:
            return callback(*args)
        finally:
            self._recording_write = False

    def finishRecordingTake(self, cancel=False):
        self._recording_layer_id = None
        self.recordingTakeChanged.emit()
        (self.cancelGesture if cancel else self.commitGesture)()
        self._update_autosave_state()
        # Replace a periodic preview snapshot immediately. Otherwise Cancel on
        # an already-dirty project could resurrect the canceled take on restart.
        if self.isDirty:
            self._write_recovery_now()

    def previewRecordedEvents(self, notes, controls):
        if not self._recording_write:
            raise RuntimeError('recording preview requires ownership')
        origin = self._gesture_origin
        self._harmony_history.preview(origin._replace(
            (*origin.notes, *notes), controls=(*origin.controls, *controls)))
        self._refresh(selection_changed=False)

    @Slot(str, float, int)
    def updatePedalEvent(self, event_id, seconds, value):
        try:
            self._execute(self.state.with_controls(tuple(
                e.with_changes(time=seconds, value=value) if e.id == event_id else e
                for e in self.state.controls)))
        except ValueError as exc:
            self.feedbackRequested.emit(str(exc))

    @Property('QVariantList', notify=pedalEventsChanged)
    def pedalEvents(self):
        return [e.to_dict() for e in self.state.controls if e.layer_id == self._active_layer_id]

    @Property(str, notify=pedalEventsChanged)
    def pedalEventsJson(self):
        return json.dumps(self.pedalEvents, separators=(',', ':'), allow_nan=False)

    @Slot(float, int)
    def addPedalEvent(self, seconds, value):
        try:
            event = ControlEvent.create(time=seconds, value=value, layer_id=self._active_layer_id)
            self._execute(self.state.with_controls((*self.state.controls, event)))
        except ValueError as exc:
            self.feedbackRequested.emit(str(exc))

    @Slot(str)
    def deletePedalEvent(self, event_id):
        try:
            self._execute(self.state.with_controls(tuple(e for e in self.state.controls if e.id != event_id)))
        except ValueError as exc:
            self.feedbackRequested.emit(str(exc))
    selectionChanged = Signal()
    feedbackRequested = Signal(str)
    dirtyChanged = Signal()
    savingChanged = Signal()
    saveSucceeded = Signal(str)
    saveFailed = Signal(str)
    externalSaveChoiceRequired = Signal()
    projectExported = Signal(str)
    projectExportFailed = Signal(str)
    loadSucceeded = Signal(str)
    loadFailed = Signal(str)
    midiExported = Signal(str)
    midiFailed = Signal(str)
    snapChanged = Signal()
    currentFileChanged = Signal()
    recentFilesChanged = Signal()
    recoveryAvailableChanged = Signal()
    savePathRequired = Signal()
    viewScaleChanged = Signal()
    gesturePreviewRequested = Signal(int, int)
    gesturePreviewFinished = Signal(bool)
    noteCreated = Signal(int, int, float)
    syncStateChanged = Signal()
    duplicatePreviewChanged = Signal()
    recycleProgressChanged = Signal()
    recycleFinished = Signal(int, int)
    layersChanged = Signal()
    activeLayerChanged = Signal()
    activeLayerDataChanged = Signal()

    def __init__(
        self,
        initial: ProjectState | None = None,
        parent=None,
        *,
        recent_files: RecentFiles | None = None,
        project_library: ProjectLibrary | None = None,
        recovery_store: RecoveryStore | NullRecoveryStore | None = None,
        autosave_enabled: bool = False,
        autosave_interval_ms: int = AUTOSAVE_INTERVAL_MS,
        recovery_interval_ms: int = RECOVERY_INTERVAL_MS,
        sync_interval_ms: int = SYNC_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._history = History(initial or ProjectState())
        self._harmony_history = HarmonyHistoryCoordinator(self._history)
        self._model = NoteListModel(self._history.state, self)
        self._layer_model = MusicLayerListModel(self._history.state, self)
        self._layer_model.changed.connect(self.activeLayerDataChanged)
        self._composite_model = CompositeNoteModel(self._history.state, self)
        self._published_layers = self._history.state.layers
        self._published_controls = self._history.state.controls
        self._active_layer_id = self._history.state.layers[0].id
        self._active_layer_cache_state: ProjectState | None = None
        self._active_layer_cache_id = ""
        self._active_layer_cache: dict[str, object] = {}
        self._saved_content: object = self._history.state.content
        self._saved_harmony_metadata: HarmonyProjectMetadata | None = (
            HarmonyProjectMetadata.empty()
        )
        self._current_path: str | None = None
        self._project_session_id = 0
        self._project_revision = 0
        self._agent_turn_active = False
        self._agent_turn_origin: tuple[object, HarmonyProjectMetadata] | None = None
        self._pending_open_path: str | None = None
        self._pending_recycle_path: str | None = None
        self._external_save_destination: str | None = None
        self._is_saving = False
        self._autosave_enabled = bool(autosave_enabled)
        self._save_jobs: set[_SaveJob] = set()
        self._active_save: _SaveContext | None = None
        self._next_save_generation = 0
        self._last_settled_save_generation = 0
        # This intentionally has no QObject parent. Destroying a parent-owned
        # pool waits on Python QRunnable code while holding the UI-thread GIL.
        # The active _SaveJob owns it until that code returns.
        self._save_pool = QThreadPool()
        self._save_pool.setMaxThreadCount(1)
        self._recycle_job: _RecycleProjectsJob | None = None
        self._recycle_progress = 0
        self._recycle_total = 0
        self._recycle_succeeded = 0
        self._gesture_origin: ProjectState | None = None
        self._gesture_revision_origin: tuple[object, HarmonyProjectMetadata] | None = None
        self._gesture_dx = 0.0
        self._gesture_dy = 0.0
        self._draw_note_id = ""
        self._draw_start_time = 0.0
        self._duplicate_sources: tuple[Note, ...] = ()
        self._duplicate_dx = 0.0
        self._duplicate_dy = 0.0
        self._duplicate_preview: tuple[dict[str, object], ...] = ()
        self._clipboard_notes: tuple[Note, ...] = ()
        self._paste_count = 0
        self._highest_pitch = HIGHEST_PITCH
        self._lowest_pitch = 0
        self._snap_enabled = True
        self._snap_division = DEFAULT_SNAP_DIVISION
        self._pixels_per_second = PIXELS_PER_SECOND
        self._row_height = ROW_HEIGHT
        self._recent = recent_files if recent_files is not None else RecentFiles()
        self._projects = project_library if project_library is not None else ProjectLibrary()
        self._library_entries_cache: list[dict[str, object]] | None = None
        self._recovery = (
            recovery_store if recovery_store is not None else NullRecoveryStore()
        )
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(autosave_interval_ms)
        self._autosave_timer.timeout.connect(self._autosave_now)
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setSingleShot(True)
        self._recovery_timer.setInterval(recovery_interval_ms)
        self._recovery_timer.timeout.connect(self._queue_recovery_now)
        if isinstance(self._recovery, RecoveryStore):
            self._recovery.written.connect(self.recoveryAvailableChanged)
        self._sync_state = "pending" if self.state.has_unsynced_changes else "synced"
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(sync_interval_ms)
        self._sync_timer.timeout.connect(self._synchronize_if_idle)

    @property
    def state(self) -> ProjectState:
        return self._history.state

    @property
    def harmony_metadata(self) -> HarmonyProjectMetadata:
        return self._harmony_history.metadata

    @property
    def harmony_analysis_notes(self) -> tuple[Note, ...]:
        generated_ids = {
            note_id
            for group in self.harmony_metadata.groups
            for note_id in group.generated_note_ids
        }
        return tuple(
            note
            for note in self.state.active_notes
            if note.id not in generated_ids
        )

    @property
    def projectSessionId(self) -> int:
        """Monotonic identity for distinguishing project replacements in controllers."""
        return self._project_session_id

    @Property(int, notify=stateChanged)
    def projectRevision(self) -> int:
        """Monotonic in-memory revision used to detect stale agent proposals."""
        return self._project_revision

    @Property(QObject, constant=True)
    def noteModel(self) -> NoteListModel:
        return self._model

    @Property(QObject, constant=True)
    def layerModel(self) -> MusicLayerListModel:
        return self._layer_model

    @Property(str, notify=activeLayerChanged)
    def activeLayerId(self) -> str:
        return self._active_layer_id

    @Property("QVariantMap", notify=activeLayerDataChanged)
    def activeLayer(self) -> dict[str, object]:
        if (self._active_layer_cache_state is self.state
                and self._active_layer_cache_id == self._active_layer_id):
            return dict(self._active_layer_cache)
        try:
            layer = self.state.layer(self._active_layer_id)
        except ValueError:
            return {}
        value = layer.to_dict()
        value["noteCount"] = sum(
            note.layer_id == layer.id for note in self.state.active_notes
        )
        # ProjectState is immutable. Several QML bindings read this projection
        # during one notification; scan its notes once for that state/layer.
        self._active_layer_cache_state = self.state
        self._active_layer_cache_id = self._active_layer_id
        self._active_layer_cache = value
        return dict(value)

    @Slot(str)
    def setActiveLayer(self, layer_id: str) -> None:
        try:
            self.state.layer(layer_id)
        except ValueError:
            self.feedbackRequested.emit("音乐层不存在")
            return
        if layer_id != self._active_layer_id:
            self._active_layer_id = layer_id
            self.activeLayerChanged.emit()
            self.activeLayerDataChanged.emit()
            self.pedalEventsChanged.emit()

    @Slot(str, str, result=str)
    def addMusicLayer(self, name: str, role: str) -> str:
        try:
            layer = MusicLayer.create(name=name, role=role)
            if not self._run_history_transition(
                lambda: self._harmony_history.execute(self.state.add_layer(layer)),
                failure_log="Add music layer failed",
                feedback="无法添加音乐层",
            ):
                return ""
            self.setActiveLayer(layer.id)
            return layer.id
        except Exception:
            logger.exception("Music layer validation failed")
            self.feedbackRequested.emit("音乐层名称或类型无效")
            return ""

    @Slot(str, str, result=str)
    def createMusicLayerFromSelection(self, name: str, role: str) -> str:
        try:
            layer = MusicLayer.create(name=name, role=role)
            next_state = self.state.create_layer_from_selection(layer)
        except Exception:
            logger.exception("Create layer from selection validation failed")
            self.feedbackRequested.emit("无法从选中音符创建音乐层")
            return ""
        if not self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Create layer from selection failed",
            feedback="无法从选中音符创建音乐层",
        ):
            return ""
        self.setActiveLayer(layer.id)
        return layer.id

    @Slot(str, str, result=bool)
    def renameMusicLayer(self, layer_id: str, name: str) -> bool:
        return self._set_music_layer_changes(layer_id, {"name": name})

    @Slot(str, result=str)
    def duplicateMusicLayer(self, layer_id: str) -> str:
        try:
            source = self.state.layer(layer_id)
            copy = MusicLayer.create(
                name=f"{source.name} 副本",
                role=source.role,
                color_role=source.color_role,
                custom_color=source.custom_color,
                instrument=source.instrument,
                volume=source.volume,
                velocity_offset=source.velocity_offset,
                transpose=source.transpose,
                time_offset=source.time_offset,
                muted=source.muted,
                solo=source.solo,
                visible=source.visible,
                locked=False,
            )
            if not self._run_history_transition(
                lambda: self._harmony_history.execute(
                    self.state.duplicate_layer(layer_id, copy)
                ),
                failure_log="Duplicate music layer failed",
                feedback="无法复制音乐层",
            ):
                return ""
            self.setActiveLayer(copy.id)
            return copy.id
        except Exception:
            logger.exception("Duplicate music layer validation failed")
            self.feedbackRequested.emit("无法复制音乐层")
            return ""

    @Slot(str, result=bool)
    def deleteMusicLayer(self, layer_id: str) -> bool:
        try:
            next_state = self.state.delete_layer(layer_id)
        except Exception as error:
            logger.info("Delete music layer rejected: %s", error)
            self.feedbackRequested.emit(
                "请先移动或删除该层音符" if "contains notes" in str(error)
                else "至少保留一个音乐层"
            )
            return False
        return self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Delete music layer failed",
            feedback="无法删除音乐层",
        )

    @Slot(str, str, result=bool)
    def deleteMusicLayerWithPolicy(self, layer_id: str, note_policy: str) -> bool:
        try:
            next_state = self.state.delete_layer(
                layer_id, note_policy=note_policy
            )
        except Exception as error:
            logger.info("Delete music layer with policy rejected: %s", error)
            self.feedbackRequested.emit(
                "请先解锁该音乐层" if "locked" in str(error)
                else "无法删除音乐层"
            )
            return False
        if not self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Delete populated music layer failed",
            feedback="无法删除音乐层",
        ):
            return False
        if note_policy == "move_to_unclassified" and (
            UNCLASSIFIED_LAYER_ID in self.state.layer_ids
        ):
            self.setActiveLayer(UNCLASSIFIED_LAYER_ID)
        return True

    @Slot(str, int, result=bool)
    def moveMusicLayer(self, layer_id: str, new_index: int) -> bool:
        try:
            next_state = self.state.reorder_layer(layer_id, int(new_index))
        except Exception:
            self.feedbackRequested.emit("音乐层位置无效")
            return False
        return self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Move music layer failed",
            feedback="无法移动音乐层",
        )

    @Slot(str, bool, result=bool)
    def setMusicLayerPinned(self, layer_id: str, pinned: bool) -> bool:
        try:
            next_state = self.state.set_layer_pinned(layer_id, bool(pinned))
        except Exception:
            self.feedbackRequested.emit("无法更改音乐层置顶状态")
            return False
        return self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Pin music layer failed",
            feedback="无法更改音乐层置顶状态",
        )

    @Slot(str, str, "QVariant", result=bool)
    def setMusicLayerProperty(self, layer_id: str, name: str, value) -> bool:
        fields = {
            "role": "role", "colorRole": "color_role",
            "customColor": "custom_color", "instrument": "instrument",
            "volume": "volume", "velocityOffset": "velocity_offset",
            "transpose": "transpose", "timeOffset": "time_offset",
            "muted": "muted", "solo": "solo", "visible": "visible",
            "locked": "locked",
            "defaultReleaseMode": "default_release_mode",
            "defaultReleaseMs": "default_release_ms",
        }
        field = fields.get(name)
        if field is None:
            self.feedbackRequested.emit("不支持的音乐层属性")
            return False
        changes = {field: value}
        if field == "role":
            changes["color_role"] = "other" if value == "custom" else value
        return self._set_music_layer_changes(layer_id, changes)

    def _set_music_layer_changes(
        self, layer_id: str, changes: dict[str, object]
    ) -> bool:
        try:
            next_state = self.state.update_layer(layer_id, **changes)
        except Exception:
            logger.exception("Music layer update validation failed")
            self.feedbackRequested.emit("音乐层参数无效，已保留原值")
            return False
        return self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Update music layer failed",
            feedback="无法更新音乐层",
            selection_changed=next_state.selected_ids != self.state.selected_ids,
        )

    @Slot(str)
    def selectMusicLayer(self, layer_id: str) -> None:
        try:
            self._execute(self.state.select_layer(layer_id))
            self.setActiveLayer(layer_id)
        except Exception:
            self.feedbackRequested.emit("音乐层不存在")

    @Slot(str, result=bool)
    def moveSelectionToLayer(self, layer_id: str) -> bool:
        try:
            next_state = self.state.move_selected_to_layer(layer_id)
        except Exception:
            self.feedbackRequested.emit("无法移动到该音乐层")
            return False
        return self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Move selection to music layer failed",
            feedback="无法移动选中音符",
        )

    @Slot(result=bool)
    def moveSelectionToUnclassified(self) -> bool:
        if not self.state.selected_ids:
            self.feedbackRequested.emit("请先选择音符")
            return False
        try:
            base_state = self.state
            if UNCLASSIFIED_LAYER_ID not in base_state.layer_ids:
                base_state = base_state.add_layer(
                    MusicLayer.unclassified(order=len(base_state.layers))
                )
            next_state = base_state.move_selected_to_layer(UNCLASSIFIED_LAYER_ID)
        except Exception:
            self.feedbackRequested.emit("无法移动到未分类")
            return False
        return self._run_history_transition(
            lambda: self._harmony_history.execute(next_state),
            failure_log="Move selection to unclassified layer failed",
            feedback="无法移动选中音符",
        )

    @Property(QObject, constant=True)
    def compositeModel(self) -> CompositeNoteModel:
        return self._composite_model

    @Slot(str, result="QVariantMap")
    def noteDetails(self, note_id: str) -> dict[str, object]:
        """Return current raw Note fields for an expanded presentation item."""

        note = next((item for item in self.state.notes if item.id == note_id), None)
        if note is None:
            return {}
        return {
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

    @Slot(str, result="QVariantMap")
    def compositeDetails(self, group_id: str) -> dict[str, object]:
        """Return derived summary values without creating a second note store."""

        group = self._composite_model.group_for_id(group_id)
        if group is None:
            return {}
        return {
            "groupId": group.group_id,
            "childNoteIds": list(group.child_note_ids),
            "startTime": group.start_time,
            "endTime": group.end_time,
            "representativePitch": group.representative_pitch,
            "pitchMin": group.pitch_min,
            "pitchMax": group.pitch_max,
            "triggerCount": group.trigger_count,
            "pitchVariant": group.has_pitch_variation,
        }

    @Property("QVariantMap", notify=stateChanged)
    def notePitchRange(self) -> dict[str, int]:
        """Expose the current musical pitch span for UI viewport placement."""
        pitches = [note.pitch for note in self.state.notes]
        if not pitches:
            return {"lowest": 60, "highest": 60}
        return {"lowest": min(pitches), "highest": max(pitches)}

    @Property("QVariantMap", notify=selectionChanged)
    def selectedPitchRange(self) -> dict[str, int]:
        """Expose the selected pitch span without disturbing a manual viewport."""
        pitches = [note.pitch for note in self.state.selected_notes]
        if not pitches:
            return {}
        return {"lowest": min(pitches), "highest": max(pitches)}

    @Property(int, notify=selectionChanged)
    def selectedCount(self) -> int:
        return len(self.state.selected_ids)

    @Property("QVariantMap", notify=selectionChanged)
    def compositeSelectionSummary(self) -> dict[str, object]:
        selected_ids = self.state.selected_ids
        for group in self._composite_model.groups:
            if len(group.child_note_ids) != len(selected_ids):
                continue
            if not selected_ids or not selected_ids.issuperset(group.child_note_ids):
                continue
            return {
                "groupId": group.group_id,
                "childNoteIds": list(group.child_note_ids),
                "startTime": group.start_time,
                "endTime": group.end_time,
                "representativePitch": group.representative_pitch,
                "pitchMin": group.pitch_min,
                "pitchMax": group.pitch_max,
                "triggerCount": group.trigger_count,
                "pitchVariant": group.has_pitch_variation,
                "count": len(group.child_note_ids),
            }
        return {
            "count": len(selected_ids),
            "childNoteIds": sorted(selected_ids),
        }

    @Property("QVariantList", notify=duplicatePreviewChanged)
    def duplicatePreview(self) -> list[dict[str, object]]:
        """Transient, snapped copy ghosts shown while the user Alt-drags notes."""
        return list(self._duplicate_preview)

    @Property(str, notify=syncStateChanged)
    def syncState(self) -> str:
        return self._sync_state

    @Property(int, notify=stateChanged)
    def unsyncedNoteCount(self) -> int:
        return sum(note.status != "saved" for note in self.state.notes)

    @Property(int, notify=stateChanged)
    def savedNoteCount(self) -> int:
        return len(self.state.playback_notes)

    @Property(float, notify=stateChanged)
    def estimatedDurationSeconds(self) -> float:
        return self.state.estimated_duration

    @Property(float, notify=stateChanged)
    def playbackDurationSeconds(self) -> float:
        return self.state.playback_duration

    @Slot(result="QVariantMap")
    def validateTimeline(self) -> dict[str, int | bool]:
        """Return non-destructive checks used before playback and export.

        Overlapping equal-pitch notes are legal for layered MIDI, so they are
        reported to the user instead of blocking playback.  Invalid pitches or
        empty durations are guarded here as a defensive check for imported data.
        """
        notes = self.state.active_notes
        invalid_pitch_count = sum(note.pitch < 0 or note.pitch > 127 for note in notes)
        empty_duration_count = sum(note.duration <= 0 for note in notes)
        conflict_count = 0
        by_pitch: dict[int, list[Note]] = {}
        for note in notes:
            by_pitch.setdefault(note.pitch, []).append(note)
        for pitch_notes in by_pitch.values():
            previous_end = float("-inf")
            for note in sorted(pitch_notes, key=lambda item: (item.start_time, item.duration)):
                if note.start_time < previous_end - OVERLAP_TIME_TOLERANCE_SECONDS:
                    conflict_count += 1
                previous_end = max(previous_end, note.start_time + note.duration)
        return {
            "valid": invalid_pitch_count == 0 and empty_duration_count == 0,
            "conflictCount": conflict_count,
            "invalidPitchCount": invalid_pitch_count,
            "emptyDurationCount": empty_duration_count,
            "unsyncedCount": self.unsyncedNoteCount,
        }

    @Property("QVariantMap", notify=stateChanged)
    def selectionSummary(self) -> dict[str, object]:
        notes = self.state.selected_notes
        summary: dict[str, object] = {"count": len(notes)}
        fields = {
            "pitch": "pitch",
            "noteName": "note_name",
            "startTime": "start_time",
            "duration": "duration",
            "velocity": "velocity",
            "instrument": "instrument",
            "releaseMode": "release_mode",
            "releaseMs": "release_ms",
        }
        for display_name, attribute in fields.items():
            values = [getattr(note, attribute) for note in notes]
            mixed = bool(values) and any(value != values[0] for value in values[1:])
            summary[display_name] = None if not values or mixed else values[0]
            summary[f"{display_name}Mixed"] = mixed
        return summary

    @Property(bool, notify=stateChanged)
    def canUndo(self) -> bool:
        return self._history.can_undo

    @Property(bool, notify=stateChanged)
    def canRedo(self) -> bool:
        return self._history.can_redo

    @Property(int, notify=stateChanged)
    def historyUndoDepth(self) -> int:
        """Read-only diagnostic surface for transaction-level UI tests."""
        return self._history.undo_depth

    @Property(int, notify=stateChanged)
    def bpm(self) -> int:
        return self.state.tempo.bpm

    @Property(int, notify=stateChanged)
    def beatsPerBar(self) -> int:
        return self.state.tempo.beats_per_bar

    @Property(int, notify=stateChanged)
    def beatUnit(self) -> int:
        return self.state.tempo.beat_unit

    @Property(bool, notify=snapChanged)
    def snapEnabled(self) -> bool:
        return self._snap_enabled

    @Property(int, notify=snapChanged)
    def snapDivision(self) -> int:
        return self._snap_division

    def snap_seconds(self) -> float:
        return self.state.tempo.snap_step_seconds(self._snap_division)

    def _snap_step_seconds(self) -> float:
        return self.snap_seconds()

    @Property(float, notify=viewScaleChanged)
    def pixelsPerSecond(self) -> float:
        return self._pixels_per_second

    @Property(float, notify=viewScaleChanged)
    def rowHeight(self) -> float:
        return self._row_height

    @Slot(float, float)
    def setViewScale(self, pixels_per_second: float, row_height: float) -> None:
        pps = min(MAX_PIXELS_PER_SECOND, max(MIN_PIXELS_PER_SECOND, float(pixels_per_second)))
        height = min(MAX_ROW_HEIGHT, max(MIN_ROW_HEIGHT, float(row_height)))
        if pps != self._pixels_per_second or height != self._row_height:
            self._pixels_per_second = pps
            self._row_height = height
            self.viewScaleChanged.emit()

    @Slot(int)
    def setBpm(self, bpm: int) -> None:
        try:
            tempo = replace(self.state.tempo, bpm=validate_bpm(int(bpm)))
            self._execute(self.state.with_tempo(tempo))
        except Exception:
            logger.exception("Set bpm failed")
            self.feedbackRequested.emit(f"BPM 需在 {MIN_BPM}-{MAX_BPM} 之间")

    @Property("QStringList", constant=True)
    def timeSignatureOptions(self) -> list[str]:
        return [f"{upper}/{lower}" for upper, lower in TIME_SIGNATURES]

    @Slot(int, int)
    def setTimeSignature(self, beats_per_bar: int, beat_unit: int) -> None:
        try:
            upper, lower = validate_time_signature(int(beats_per_bar), int(beat_unit))
            tempo = replace(
                self.state.tempo, beats_per_bar=upper, beat_unit=lower
            )
            self._execute(self.state.with_tempo(tempo))
        except Exception:
            logger.exception("Set time signature failed")
            self.feedbackRequested.emit("拍号仅支持 " + "、".join(self.timeSignatureOptions))

    @Slot(bool)
    def setSnapEnabled(self, enabled: bool) -> None:
        if bool(enabled) != self._snap_enabled:
            self._snap_enabled = bool(enabled)
            self.snapChanged.emit()

    @Slot(int)
    def setSnapDivision(self, division: int) -> None:
        try:
            validated = validate_snap_division(int(division))
        except Exception:
            self.feedbackRequested.emit("吸附仅支持 1/4、1/8、1/16")
            return
        if validated != self._snap_division:
            self._snap_division = validated
            self.snapChanged.emit()

    @Property(bool, notify=dirtyChanged)
    def isDirty(self) -> bool:
        return (
            self._saved_content is None
            or self._saved_harmony_metadata is None
            or self.state.content != self._saved_content
            or self.harmony_metadata != self._saved_harmony_metadata
        )

    @Property(bool, notify=savingChanged)
    def isSaving(self) -> bool:
        return self._is_saving

    @Property(bool, notify=recycleProgressChanged)
    def recycleInProgress(self) -> bool:
        return self._recycle_job is not None

    @Property(int, notify=recycleProgressChanged)
    def recycleProgress(self) -> int:
        return self._recycle_progress

    @Property(int, notify=recycleProgressChanged)
    def recycleTotal(self) -> int:
        return self._recycle_total

    @Property(str, notify=currentFileChanged)
    def currentFilePath(self) -> str:
        return self._current_path or ""

    @Property(str, notify=currentFileChanged)
    def currentFileName(self) -> str:
        if not self._current_path:
            return "未命名工程"
        path = Path(self._current_path)
        try:
            if path.resolve(strict=False).parent.parent == self._projects.root.resolve(strict=False):
                return self._projects.display_name_for(path)
        except OSError:
            pass
        return path.name

    @Property(bool, notify=currentFileChanged)
    def currentProjectIsLibrary(self) -> bool:
        return bool(
            self._current_path
            and self._projects.is_library_path(Path(self._current_path))
        )

    @Property("QStringList", notify=recentFilesChanged)
    def recentFiles(self) -> list[str]:
        return self._recent.files()

    @Property("QVariantList", notify=recentFilesChanged)
    def recentEntries(self) -> list[dict[str, object]]:
        return self._recent.entries()

    @Property("QVariantList", notify=recentFilesChanged)
    def libraryEntries(self) -> list[dict[str, object]]:
        if self._library_entries_cache is None:
            self._library_entries_cache = self._projects.entries(self._recent.files())
        return list(self._library_entries_cache)

    def _invalidate_library_entries(self) -> None:
        self._library_entries_cache = None

    def _notify_recent_files_changed(self) -> None:
        self._invalidate_library_entries()
        self.recentFilesChanged.emit()

    @Property(bool, notify=recoveryAvailableChanged)
    def recoveryAvailable(self) -> bool:
        return self._recovery.available

    @Property("QVariantMap", notify=recoveryAvailableChanged)
    def recoveryInfo(self) -> dict[str, object]:
        return self._recovery.info() or {}

    @Slot(str, str, result="QVariantMap")
    def validateProjectName(
        self, name: str, exclude_path: str = ""
    ) -> dict[str, object]:
        excluded = None
        current_name = None
        if exclude_path:
            try:
                excluded = Path(self._local_path(exclude_path))
                current_name = self._projects.display_name_for(excluded)
            except (TypeError, ValueError, OSError):
                excluded = None
        result = inspect_project_name(
            name,
            self._projects.display_names(excluded),
            current_name=current_name,
        )
        return {
            "valid": result.valid,
            "normalized": result.normalized,
            "error": result.error,
            "suggestion": result.suggestion,
        }

    def _set_current_path(self, path: str | None) -> None:
        if path != self._current_path:
            self._current_path = path
            self.currentFileChanged.emit()

    def _replace_project(
        self,
        state: ProjectState,
        *,
        saved_content: object,
        path: str | None,
        harmony_metadata: HarmonyProjectMetadata | None = None,
        saved_harmony_metadata: HarmonyProjectMetadata | None = None,
    ) -> None:
        """Switch the active project and invalidate callbacks from the prior one."""
        self.recordingBoundaryRequested.emit()
        if self._agent_turn_active:
            raise RuntimeError("cannot replace project during an agent turn")
        history_checkpoint = self._harmony_history._checkpoint()
        refresh_checkpoint = self._refresh_checkpoint()
        controller_checkpoint = (
            self._project_session_id,
            self._project_revision,
            self._saved_content,
            self._saved_harmony_metadata,
            self._current_path,
            self._external_save_destination,
            self._gesture_origin,
            self._gesture_dx,
            self._gesture_dy,
            self._draw_note_id,
            self._draw_start_time,
            self._gesture_revision_origin,
            self._active_layer_id,
        )
        refresh_started = False
        try:
            self._harmony_history.reset(state, harmony_metadata)
            self.setActiveLayer(state.layers[0].id)
            self._project_session_id += 1
            self._project_revision += 1
            self._saved_content = saved_content
            self._saved_harmony_metadata = saved_harmony_metadata
            self._external_save_destination = None
            self._set_current_path(path)
            self._clear_gesture()
            refresh_started = True
            self._refresh()
        except Exception:
            logger.exception("Project replacement failed")
            self._harmony_history._restore(history_checkpoint)
            (
                self._project_session_id,
                self._project_revision,
                self._saved_content,
                self._saved_harmony_metadata,
                previous_path,
                self._external_save_destination,
                self._gesture_origin,
                self._gesture_dx,
                self._gesture_dy,
                self._draw_note_id,
                self._draw_start_time,
                self._gesture_revision_origin,
                self._active_layer_id,
            ) = controller_checkpoint
            self._set_current_path(previous_path)
            if refresh_started:
                self._restore_refresh_checkpoint(refresh_checkpoint)
            raise
        if isinstance(self._recovery, RecoveryStore):
            self._recovery.cancel_pending()

    def _remember_recent(self, path: str, state: ProjectState | None = None) -> None:
        state = state if state is not None else self.state
        self._recent.remember(
            path,
            note_count=len(state.playback_notes),
            duration_seconds=state.playback_duration,
        )
        self._notify_recent_files_changed()

    def _refresh(self, *, selection_changed: bool = True) -> None:
        layers_changed = self.state.layers != self._published_layers
        controls_changed = self.state.controls != self._published_controls
        self._model.set_state(self.state)
        self._layer_model.set_state(self.state)
        # Keep existing display groups stable during a take. New performance
        # notes are visible through NoteListModel immediately; regroup once at
        # the committed boundary rather than rebuilding the entire song at 30Hz.
        if not self.recordingTakeActive:
            self._composite_model.set_state(self.state)
        active_layer_changed = False
        if self._active_layer_id not in self.state.layer_ids:
            self._active_layer_id = self.state.layers[0].id
            active_layer_changed = True
        if layers_changed:
            self._published_layers = self.state.layers
            self.layersChanged.emit()
            self.activeLayerDataChanged.emit()
        if active_layer_changed:
            self.activeLayerChanged.emit()
            self.activeLayerDataChanged.emit()
        self.stateChanged.emit()
        if controls_changed or active_layer_changed:
            self._published_controls = self.state.controls
            self.pedalEventsChanged.emit()
        if selection_changed:
            self.selectionChanged.emit()
        self._schedule_sync()
        self.dirtyChanged.emit()
        self._update_autosave_state()

    def _refresh_checkpoint(self) -> tuple[str, bool, bool, bool]:
        return (
            self._sync_state,
            self._sync_timer.isActive(),
            self._autosave_timer.isActive(),
            self._recovery_timer.isActive(),
        )

    def _restore_refresh_checkpoint(
        self,
        checkpoint: tuple[str, bool, bool, bool],
        *,
        selection_changed: bool = True,
    ) -> None:
        sync_state, sync_active, autosave_active, recovery_active = checkpoint
        self._run_rollback_step(
            "model restore",
            lambda: (
                self._model.set_state(self.state),
                self._layer_model.set_state(self.state),
                self._composite_model.set_state(self.state),
            ),
        )
        self._published_layers = self.state.layers
        self._run_rollback_step("layer notification", self.layersChanged.emit)
        self._run_rollback_step("active layer notification", self.activeLayerChanged.emit)
        self._run_rollback_step("active layer data notification", self.activeLayerDataChanged.emit)
        self._run_rollback_step("state notification", self.stateChanged.emit)
        if selection_changed:
            self._run_rollback_step("selection notification", self.selectionChanged.emit)
        self._run_rollback_step("dirty notification", self.dirtyChanged.emit)
        self._run_rollback_step(
            "sync state restore", lambda: self._set_sync_state(sync_state)
        )
        for name, timer, active in (
            ("sync timer restore", self._sync_timer, sync_active),
            ("autosave timer restore", self._autosave_timer, autosave_active),
            ("recovery timer restore", self._recovery_timer, recovery_active),
        ):
            action = timer.start if active else timer.stop
            self._run_rollback_step(name, action)

    @staticmethod
    def _run_rollback_step(name: str, action: Callable[[], object]) -> None:
        try:
            action()
        except Exception:
            logger.exception("Editor rollback %s failed", name)

    def _revision_snapshot(self) -> tuple[object, HarmonyProjectMetadata]:
        return self.state.content, self.harmony_metadata

    def _advance_revision_if_changed(
        self, origin: tuple[object, HarmonyProjectMetadata]
    ) -> None:
        if origin != self._revision_snapshot():
            self._project_revision += 1

    def _run_history_transition(
        self,
        transition: Callable[[], object],
        *,
        failure_log: str,
        feedback: str | None,
        selection_changed: bool = True,
        advance_revision: bool = True,
        revision_origin: tuple[object, HarmonyProjectMetadata] | None = None,
    ) -> bool:
        if self.recordingTakeActive:
            self.feedbackRequested.emit('录制中，请先结束或取消本段')
            return False
        history_checkpoint = self._harmony_history._checkpoint()
        refresh_checkpoint = self._refresh_checkpoint()
        current_path_checkpoint = self._current_path
        revision_checkpoint = self._project_revision
        active_layer_checkpoint = self._active_layer_id
        initial_revision_snapshot = self._revision_snapshot()
        transition_completed = False
        try:
            transition()
            transition_completed = True
            if advance_revision:
                self._advance_revision_if_changed(
                    initial_revision_snapshot
                    if revision_origin is None
                    else revision_origin
                )
            self._refresh(selection_changed=selection_changed)
            return True
        except Exception:
            logger.exception(failure_log)
            self._harmony_history._restore(history_checkpoint)
            self._project_revision = revision_checkpoint
            self._active_layer_id = active_layer_checkpoint
            if transition_completed:
                self._run_rollback_step(
                    "current path restore",
                    lambda: self._set_current_path(current_path_checkpoint),
                )
                self._restore_refresh_checkpoint(
                    refresh_checkpoint, selection_changed=selection_changed
                )
            if feedback is not None:
                self.feedbackRequested.emit(feedback)
            return False

    def _set_sync_state(self, value: str) -> None:
        if value != self._sync_state:
            self._sync_state = value
            self.syncStateChanged.emit()

    def _schedule_sync(self) -> None:
        if not self.state.has_unsynced_changes:
            self._sync_timer.stop()
            self._set_sync_state("synced")
            return
        self._set_sync_state("pending")
        if self._gesture_origin is None and not self._agent_turn_active:
            self._sync_timer.start()

    def _synchronize_if_idle(self) -> None:
        if self._gesture_origin is not None or self._agent_turn_active:
            return
        self.synchronizeNow()

    @Slot()
    def synchronizeNow(self) -> None:
        """Commit timeline edits to the playback snapshot without an undo entry."""
        if not self.state.has_unsynced_changes:
            self._set_sync_state("synced")
            return
        if self._gesture_origin is not None or self._agent_turn_active:
            self._set_sync_state("pending")
            return
        try:
            revision_origin = self._revision_snapshot()
            self._sync_timer.stop()
            self._set_sync_state("syncing")
            self._harmony_history.replace_current(self.state.synchronize())
            self._advance_revision_if_changed(revision_origin)
            self._refresh(selection_changed=False)
        except Exception:
            logger.exception("Timeline synchronization failed")
            self._set_sync_state("pending")
            self.feedbackRequested.emit("音符同步失败，请重试")

    def _update_autosave_state(self) -> None:
        if self.recordingTakeActive:
            self._autosave_timer.stop()
            # Periodic crash recovery is separate from saving/ending a take.
            # Do not restart its deadline on every displayed held-note frame.
            if self.isDirty and not self._recovery_timer.isActive():
                self._recovery_timer.start()
            return
        if self._agent_turn_active:
            self._autosave_timer.stop()
            self._recovery_timer.stop()
            return
        if self.isDirty:
            self._recovery_timer.start()
            if self._autosave_enabled and self._current_path and not self._is_saving:
                self._autosave_timer.start()
            else:
                self._autosave_timer.stop()
            return
        self._autosave_timer.stop()
        self._recovery_timer.stop()
        if self._recovery.available or self._recovery.pending:
            self._recovery.clear()
            self.recoveryAvailableChanged.emit()

    def _autosave_now(self) -> None:
        if (
            not self._agent_turn_active
            and not self.recordingTakeActive
            and
            self._autosave_enabled
            and self.isDirty
            and self._current_path
            and not self._is_saving
        ):
            self.saveProject(self._current_path)

    @Slot(bool, int)
    def setAutosavePolicy(self, enabled: bool, delay_seconds: int) -> None:
        try:
            parsed_delay = int(delay_seconds)
        except (TypeError, ValueError):
            parsed_delay = DEFAULT_AUTOSAVE_DELAY_SECONDS
        if not isinstance(delay_seconds, str) and delay_seconds != parsed_delay:
            parsed_delay = DEFAULT_AUTOSAVE_DELAY_SECONDS
        normalized_delay = (
            parsed_delay
            if parsed_delay in VALID_AUTOSAVE_DELAYS_SECONDS
            else DEFAULT_AUTOSAVE_DELAY_SECONDS
        )
        self._autosave_enabled = bool(enabled)
        self._autosave_timer.stop()
        self._autosave_timer.setInterval(normalized_delay * 1000)
        if self.isDirty:
            self._update_autosave_state()

    def _queue_recovery_now(self) -> None:
        if not self._agent_turn_active and self.isDirty:
            self._recovery.queue_document(
                ProjectDocument(self.state, self.harmony_metadata), self._current_path
            )
            if self.recordingTakeActive:
                self._recovery_timer.start()

    def _write_recovery_now(self) -> None:
        if not self._agent_turn_active and self.isDirty:
            self._recovery.write_document(
                ProjectDocument(self.state, self.harmony_metadata),
                self._current_path,
            )
            self.recoveryAvailableChanged.emit()

            if self.recordingTakeActive:
                self._recovery_timer.start()

    def _execute(self, state: ProjectState) -> None:
        self._run_history_transition(
            lambda: self._harmony_history.execute(state),
            failure_log="Editor operation failed",
            feedback="操作失败，请重试",
        )

    def _execute_harmony_transaction(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata,
        *,
        emit_failure_feedback: bool = True,
    ) -> None:
        self._run_history_transition(
            lambda: self._harmony_history.execute(state, metadata),
            failure_log="Harmony transaction failed",
            feedback="和弦应用失败，请重试" if emit_failure_feedback else None,
        )

    def apply_harmony_transaction(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata,
        *,
        emit_failure_feedback: bool = True,
    ) -> bool:
        """Apply one music+metadata transaction and report whether it committed."""
        self._execute_harmony_transaction(
            state,
            metadata,
            emit_failure_feedback=emit_failure_feedback,
        )
        return self.state == state and self.harmony_metadata == metadata

    def begin_agent_turn(self) -> None:
        """Begin one controller-owned transaction for a complete collaboration turn."""
        if self._agent_turn_active:
            raise RuntimeError("agent turn is already active")
        if self._gesture_origin is not None:
            raise RuntimeError("cannot begin agent turn during an editor gesture")
        self._harmony_history.begin_gesture()
        self._agent_turn_active = True
        self._agent_turn_origin = self._revision_snapshot()
        self._sync_timer.stop()
        self._autosave_timer.stop()
        self._recovery_timer.stop()

    def preview_agent_state(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata | None = None,
    ) -> None:
        """Preview a validated agent state without creating a History entry."""
        if not self._agent_turn_active:
            raise RuntimeError("no agent turn is active")
        succeeded = self._run_history_transition(
            lambda: self._harmony_history.preview(state, metadata),
            failure_log="Agent turn preview failed",
            feedback=None,
            advance_revision=False,
        )
        if succeeded:
            return
        try:
            self.cancel_agent_turn()
        except RuntimeError:
            logger.exception("Agent turn cancellation after preview failure failed")
        raise RuntimeError("agent turn preview failed")

    def commit_agent_turn(self) -> None:
        """Commit the whole collaboration turn as exactly one History entry."""
        if not self._agent_turn_active or self._agent_turn_origin is None:
            raise RuntimeError("no agent turn is active")
        origin = self._agent_turn_origin
        succeeded = self._run_history_transition(
            self._harmony_history.commit_gesture,
            failure_log="Agent turn commit failed",
            feedback=None,
            advance_revision=True,
            revision_origin=origin,
        )
        if succeeded:
            self._agent_turn_active = False
            self._agent_turn_origin = None
            self._resume_after_agent_turn()
            return
        try:
            self.cancel_agent_turn()
        except RuntimeError:
            logger.exception("Agent turn cancellation after commit failure failed")
        raise RuntimeError("agent turn commit failed")

    def cancel_agent_turn(self) -> None:
        """Cancel a collaboration turn and restore its original project/metadata state."""
        if not self._agent_turn_active:
            raise RuntimeError("no agent turn is active")
        succeeded = self._run_history_transition(
            self._harmony_history.cancel_gesture,
            failure_log="Agent turn cancellation failed",
            feedback=None,
            advance_revision=False,
        )
        if not succeeded:
            raise RuntimeError("agent turn cancellation failed")
        self._agent_turn_active = False
        self._agent_turn_origin = None
        self._resume_after_agent_turn()

    def _resume_after_agent_turn(self) -> None:
        self._schedule_sync()
        self._update_autosave_state()

    @Slot(int, int)
    def setPitchRange(self, highest_pitch: int, lowest_pitch: int) -> None:
        """Set UI-only Piano Roll bounds used for pointer geometry."""
        highest = max(0, min(127, int(highest_pitch)))
        lowest = max(0, min(127, int(lowest_pitch)))
        if lowest > highest:
            highest, lowest = lowest, highest
        self._highest_pitch = highest
        self._lowest_pitch = lowest

    def _canvas_time(self, x: float) -> float:
        return snap_time(
            x_to_time(x, self._pixels_per_second),
            step=self._snap_step_seconds(),
            enabled=self._snap_enabled,
        )

    def _canvas_pitch(self, y: float) -> int:
        return max(
            self._lowest_pitch,
            min(
                self._highest_pitch,
                y_to_pitch(y, self._row_height, self._highest_pitch),
            ),
        )

    @Slot(float, float)
    @Slot(float, float, float, int)
    def createNoteAt(
        self, x: float, y: float, duration: float = DEFAULT_DURATION, velocity: int = 75
    ) -> None:
        try:
            start_time = self._canvas_time(x)
            pitch = self._canvas_pitch(y)
            note = Note.create(
                pitch=pitch,
                start_time=start_time,
                duration=duration,
                velocity=velocity,
                layer_id=self._active_layer_id,
                release_mode=self.state.layer(self._active_layer_id).default_release_mode,
                release_ms=self.state.layer(self._active_layer_id).default_release_ms,
            )
            self._execute(self.state.add_note(note))
            if any(existing.id == note.id for existing in self.state.notes):
                self.noteCreated.emit(note.pitch, note.velocity, note.duration)
        except Exception:
            logger.exception("Create note failed")
            self.feedbackRequested.emit("无法在该位置创建音符")

    @Slot(float, float, float, int, result=str)
    def beginDrawNoteAt(
        self,
        x: float,
        y: float,
        duration: float = DEFAULT_DURATION,
        velocity: int = 75,
    ) -> str:
        """Start a one-history-entry note drawing gesture at canvas coordinates."""
        self.beginGesture()
        self._draw_start_time = self._canvas_time(x)
        self._draw_note_id = self.previewAddNote(
            self._canvas_pitch(y),
            self._draw_start_time,
            duration,
            velocity,
        )
        if self._draw_note_id:
            self.noteCreated.emit(self._canvas_pitch(y), int(velocity), float(duration))
        return self._draw_note_id

    @Slot(float)
    def previewDrawNoteTo(self, x: float) -> None:
        """Extend the active drawn note to the supplied canvas x coordinate."""
        if not self._draw_note_id:
            return
        end_time = self._canvas_time(x)
        self.previewNoteDuration(
            self._draw_note_id,
            max(MIN_DRAW_DURATION, end_time - self._draw_start_time),
        )

    @Slot(str, bool)
    def selectNote(self, note_id: str, additive: bool = False) -> None:
        state = (
            self.state.toggle_selection(note_id)
            if additive
            else self.state.select_only(note_id)
        )
        self._execute(state)

    @Slot(float, float, float, float, bool)
    @Slot(float, float, float, float, bool, str)
    def selectRect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        additive: bool = False,
        visible_layer_id: str = "",
    ) -> None:
        try:
            marquee = Rect(
                min(x, x + width),
                min(y, y + height),
                abs(width),
                abs(height),
            )
            visible_layers = {
                layer.id for layer in self.state.layers
                if layer.visible and (not visible_layer_id or layer.id == visible_layer_id)
            }
            selected = notes_intersecting_rect(
                tuple(note for note in self.state.active_notes if note.layer_id in visible_layers),
                marquee,
                pixels_per_second=self._pixels_per_second,
                row_height=self._row_height,
                highest_pitch=self._highest_pitch,
            )
            self._execute(self.state.select_ids(selected, additive=additive))
        except Exception:
            logger.exception("Marquee selection failed")
            self.feedbackRequested.emit("框选失败，请重试")

    @Slot()
    def clearSelection(self) -> None:
        self._execute(self.state.clear_selection())

    @Slot()
    def copySelected(self) -> None:
        """Store active selected notes for Ctrl+C without changing project state."""
        self._clipboard_notes = tuple(
            note for note in self.state.selected_notes if note.status != "deleted"
        )
        self._paste_count = 0
        if self._clipboard_notes:
            self.feedbackRequested.emit(f"已复制 {len(self._clipboard_notes)} 个音符")

    @Slot()
    def pasteCopied(self) -> None:
        """Paste independent note copies one snap step to the right of the source."""
        if not self._clipboard_notes:
            self.feedbackRequested.emit("请先选择音符后复制")
            return
        self._paste_count += 1
        copies = self._make_note_copies(
            self._clipboard_notes,
            delta_time=self._paste_count * self._snap_step_seconds(),
            delta_pitch=0,
        )
        self._commit_note_copies(copies)

    @Slot()
    def duplicateSelected(self) -> None:
        """Duplicate the current selection one grid step to the right."""
        sources = tuple(
            note for note in self.state.selected_notes if note.status != "deleted"
        )
        if not sources:
            self.feedbackRequested.emit("请先选择音符后复制")
            return
        self._commit_note_copies(
            self._make_note_copies(
                sources,
                delta_time=self._snap_step_seconds(),
                delta_pitch=0,
            )
        )

    @Slot(int)
    def shiftSelectedOctave(self, semitones: int) -> None:
        """Move selected notes by an octave as one undoable operation."""
        try:
            delta = int(semitones)
        except (TypeError, ValueError, OverflowError):
            self.feedbackRequested.emit("八度移动参数无效")
            return
        if delta == 0 or not self.state.selected_notes:
            return
        self._execute(self.state.move_selected(delta_time=0.0, delta_pitch=delta))

    @Slot()
    def quantizeSelected(self) -> None:
        """Quantize selected note starts to the current grid in one transaction."""
        selected = tuple(
            note for note in self.state.selected_notes if note.status != "deleted"
        )
        if not selected:
            return
        step = self._snap_step_seconds()
        next_state = self.state
        for note in selected:
            next_state = next_state.update_note(
                note.id,
                start_time=snap_time(note.start_time, step=step, enabled=True),
            )
        self._execute(next_state)

    @Slot(int, int)
    def moveSelectedByKeyboard(self, time_steps: int, pitch_steps: int) -> None:
        """Move selection by grid steps/semitones for keyboard-accessible editing."""
        try:
            time_delta = int(time_steps) * self._snap_step_seconds()
            pitch_delta = int(pitch_steps)
        except (TypeError, ValueError, OverflowError):
            self.feedbackRequested.emit("键盘移动参数无效")
            return
        if (time_delta == 0 and pitch_delta == 0) or not self.state.selected_notes:
            return
        self._execute(
            self.state.move_selected(
                delta_time=time_delta,
                delta_pitch=pitch_delta,
            )
        )

    @Slot(float, float)
    def moveSelectedByPixels(self, dx: float, dy: float) -> None:
        """Commit a visual drag in one state transition.

        Composite delegates render a local drag preview because regrouping can
        replace their model rows.  Applying the final pixel delta atomically
        ensures the release still creates exactly one undo entry.
        """
        try:
            raw_delta = float(dx) / self._pixels_per_second
            if self._snap_enabled:
                step = self._snap_step_seconds()
                delta_time = round(raw_delta / step) * step
            else:
                delta_time = raw_delta
            delta_pitch = round(-float(dy) / self._row_height)
        except (TypeError, ValueError, OverflowError, ZeroDivisionError):
            self.feedbackRequested.emit("拖动参数无效")
            return
        if (delta_time == 0 and delta_pitch == 0) or not self.state.selected_notes:
            return
        self._execute(
            self.state.move_selected(
                delta_time=delta_time,
                delta_pitch=delta_pitch,
            )
        )

    @Slot()
    def beginDuplicateGesture(self) -> None:
        """Begin an Alt-drag copy without mutating the project on every frame."""
        if self._gesture_origin is not None:
            self.feedbackRequested.emit("当前编辑尚未完成")
            return
        self._clear_duplicate_preview()
        self._duplicate_sources = tuple(
            note for note in self.state.selected_notes if note.status != "deleted"
        )

    @Slot(float, float)
    def previewDuplicatePixels(self, dx: float, dy: float) -> None:
        """Update the ghost only after the drag crosses a snapped grid position."""
        if not self._duplicate_sources:
            return
        self._duplicate_dx += float(dx)
        self._duplicate_dy += float(dy)
        raw_delta = self._duplicate_dx / self._pixels_per_second
        if self._snap_enabled:
            step = self._snap_step_seconds()
            delta_time = round(raw_delta / step) * step
        else:
            delta_time = raw_delta
        delta_pitch = round(-self._duplicate_dy / self._row_height)
        preview = (
            self._copy_preview_items(delta_time, delta_pitch)
            if delta_time != 0.0 or delta_pitch != 0
            else ()
        )
        if preview != self._duplicate_preview:
            self._duplicate_preview = preview
            self.duplicatePreviewChanged.emit()

    @Slot()
    def commitDuplicateGesture(self) -> None:
        """Commit all Alt-drag copies as one undoable editor action."""
        sources = self._duplicate_sources
        preview = self._duplicate_preview
        self._clear_duplicate_preview()
        if not sources or not preview:
            return
        copies = tuple(
            Note.create(
                pitch=int(item["pitch"]),
                start_time=float(item["startTime"]),
                duration=float(item["duration"]),
                velocity=source.velocity,
                instrument=source.instrument,
                layer_id=source.layer_id,
            )
            for source, item in zip(sources, preview, strict=True)
        )
        self._commit_note_copies(copies)

    @Slot()
    def cancelDuplicateGesture(self) -> None:
        self._clear_duplicate_preview()

    def _copy_preview_items(
        self, delta_time: float, delta_pitch: int
    ) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "sourceId": note.id,
                "pitch": max(0, min(127, note.pitch + delta_pitch)),
                "noteName": midi_to_name(max(0, min(127, note.pitch + delta_pitch))),
                "startTime": max(0.0, note.start_time + delta_time),
                "duration": note.duration,
            }
            for note in self._duplicate_sources
        )

    @staticmethod
    def _make_note_copies(
        notes: tuple[Note, ...], *, delta_time: float, delta_pitch: int
    ) -> tuple[Note, ...]:
        return tuple(
            Note.create(
                pitch=max(0, min(127, note.pitch + delta_pitch)),
                start_time=max(0.0, note.start_time + delta_time),
                duration=note.duration,
                velocity=note.velocity,
                instrument=note.instrument,
                layer_id=note.layer_id,
            )
            for note in notes
        )

    def _commit_note_copies(self, copies: tuple[Note, ...]) -> None:
        if not copies:
            return
        try:
            revision_origin = self._revision_snapshot()
            self._harmony_history.begin_gesture()
            next_state = self.state
            for note in copies:
                next_state = next_state.add_note(note, select=False)
            self._harmony_history.preview(
                next_state.select_ids(note.id for note in copies)
            )
            self._harmony_history.commit_gesture()
            self._advance_revision_if_changed(revision_origin)
            self._refresh()
        except Exception:
            logger.exception("Copy notes failed")
            self.feedbackRequested.emit("无法复制音符")

    @Slot(list)
    def selectNotes(self, note_ids: list) -> None:
        try:
            self._execute(self.state.select_ids([str(note_id) for note_id in note_ids]))
        except Exception:
            logger.exception("Select notes failed")
            self.feedbackRequested.emit("无法选择音符")

    @Slot()
    def beginGesture(self) -> None:
        if self.recordingTakeActive:
            return
        try:
            self._harmony_history.begin_gesture()
            self._gesture_origin = self.state
            self._gesture_revision_origin = self._revision_snapshot()
            self._gesture_dx = 0.0
            self._gesture_dy = 0.0
        except Exception:
            logger.exception("Begin gesture failed")
            self.feedbackRequested.emit("无法开始编辑手势")

    @Slot(float, float)
    def previewMovePixels(self, dx: float, dy: float) -> None:
        if self.recordingTakeActive:
            return
        try:
            if self._gesture_origin is None:
                raise RuntimeError("no gesture is active")
            self._gesture_dx += dx
            self._gesture_dy += dy
            raw_delta = self._gesture_dx / self._pixels_per_second
            if self._snap_enabled:
                step = self._snap_step_seconds()
                delta_time = round(raw_delta / step) * step
            else:
                delta_time = raw_delta
            delta_pitch = round(-self._gesture_dy / self._row_height)
            self._harmony_history.preview(
                self._gesture_origin.move_selected(
                    delta_time=delta_time,
                    delta_pitch=delta_pitch,
                )
            )
            preview_notes = self.state.selected_notes
            if preview_notes:
                self.gesturePreviewRequested.emit(preview_notes[0].pitch, preview_notes[0].velocity)
            self._refresh(selection_changed=False)
        except Exception:
            logger.exception("Move preview failed")
            self.feedbackRequested.emit("无法移动音符")

    @Slot(float)
    def previewResizePixels(self, dx: float) -> None:
        if self.recordingTakeActive:
            return
        try:
            if self._gesture_origin is None:
                raise RuntimeError("no gesture is active")
            self._gesture_dx += dx
            raw_delta = self._gesture_dx / self._pixels_per_second
            if self._snap_enabled:
                step = self._snap_step_seconds()
                delta_duration = round(raw_delta / step) * step
            else:
                delta_duration = raw_delta
            self._harmony_history.preview(
                self._gesture_origin.resize_selected(
                    delta_duration=delta_duration
                )
            )
            self._refresh(selection_changed=False)
        except Exception:
            logger.exception("Resize preview failed")
            self.feedbackRequested.emit("无法调整音符长度")

    @Slot(int, float, float, int)
    def previewAddNote(
        self,
        pitch: int,
        start_time: float,
        duration: float,
        velocity: int = 75,
    ) -> str:
        """Add a note inside the active gesture; returns the new note id."""
        if self.recordingTakeActive and not self._recording_write:
            return ''
        try:
            if self._gesture_origin is None:
                raise RuntimeError("no gesture is active")
            note = Note.create(
                pitch=int(pitch),
                start_time=float(start_time),
                duration=float(duration),
                velocity=int(velocity),
                layer_id=self._recording_layer_id or self._active_layer_id,
                release_mode=self.state.layer(self._recording_layer_id or self._active_layer_id).default_release_mode,
                release_ms=self.state.layer(self._recording_layer_id or self._active_layer_id).default_release_ms,
            )
            self._harmony_history.preview(self.state.add_note(note, select=False))
            self._refresh(selection_changed=False)
            return note.id
        except Exception:
            logger.exception("Add note preview failed")
            self.feedbackRequested.emit("无法添加音符")
            return ""

    @Slot(str, float)
    def previewNoteDuration(self, note_id: str, duration: float) -> None:
        """Update one note's duration inside the active gesture."""
        if self.recordingTakeActive and not self._recording_write:
            return
        try:
            if self._gesture_origin is None:
                raise RuntimeError("no gesture is active")
            self._harmony_history.preview(
                self.state.update_note(note_id, duration=float(duration))
            )
            self._refresh(selection_changed=False)
        except Exception:
            logger.exception("Note duration preview failed")
            self.feedbackRequested.emit("无法调整音符长度")

    @Slot()
    def commitGesture(self) -> None:
        if self.recordingTakeActive:
            return
        manual_note_id = self._draw_note_id
        try:
            self._harmony_history.commit_gesture()
            if self._gesture_revision_origin is not None:
                self._advance_revision_if_changed(self._gesture_revision_origin)
            self._clear_gesture()
            if manual_note_id and any(
                note.id == manual_note_id for note in self.state.active_notes
            ):
                self._execute(self.state.select_only(manual_note_id))
            else:
                self._refresh(selection_changed=False)
            self.gesturePreviewFinished.emit(True)
        except Exception:
            logger.exception("Commit gesture failed")
            if self._gesture_origin is not None:
                try:
                    self._harmony_history.cancel_gesture()
                except Exception:
                    logger.exception("Rollback failed gesture failed")
                self._clear_gesture()
                self._refresh()
                self.gesturePreviewFinished.emit(False)
            self.feedbackRequested.emit("无法完成编辑手势")

    @Slot()
    def cancelGesture(self) -> None:
        if self.recordingTakeActive:
            return
        try:
            self._harmony_history.cancel_gesture()
            self._clear_gesture()
            self._refresh()
            self.gesturePreviewFinished.emit(False)
        except Exception:
            logger.exception("Cancel gesture failed")
            self.feedbackRequested.emit("无法取消编辑手势")

    def _clear_gesture(self) -> None:
        self._gesture_origin = None
        self._gesture_revision_origin = None
        self._gesture_dx = 0.0
        self._gesture_dy = 0.0
        self._draw_note_id = ""
        self._draw_start_time = 0.0

    def _clear_duplicate_preview(self) -> None:
        had_preview = bool(self._duplicate_preview)
        self._duplicate_sources = ()
        self._duplicate_dx = 0.0
        self._duplicate_dy = 0.0
        self._duplicate_preview = ()
        if had_preview:
            self.duplicatePreviewChanged.emit()

    @Slot()
    def deleteSelected(self) -> None:
        count = self.selectedCount
        if count:
            self._execute(self.state.delete_selected())
            self.feedbackRequested.emit(f"已删除 {count} 个音符")

    @Slot("QVariantList", str, "QVariant", result=bool)
    def applyCompositeOperation(self, note_ids: list, operation: str, value=None) -> bool:
        """Apply one overall operation to the real child Notes in one transaction."""

        try:
            ids = tuple(dict.fromkeys(str(note_id) for note_id in note_ids or []))
            active_ids = {note.id for note in self.state.active_notes}
            if not ids or any(note_id not in active_ids for note_id in ids):
                raise ValueError("composite operation contains an unknown note")
            selected_state = self.state.select_ids(ids)
            if operation == "move":
                if isinstance(value, dict):
                    delta_time = float(value.get("deltaTime", 0.0))
                    delta_pitch = value.get("deltaPitch", 0)
                else:
                    delta_time = 0.0 if value is None else float(value)
                    delta_pitch = 0
                if not self._is_finite_number(delta_time):
                    raise ValueError("invalid composite time delta")
                if not isinstance(delta_pitch, int) or isinstance(delta_pitch, bool):
                    raise ValueError("invalid composite pitch delta")
                next_state = selected_state.move_selected(
                    delta_time=delta_time,
                    delta_pitch=delta_pitch,
                )
            elif operation == "transpose":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("invalid composite pitch delta")
                next_state = selected_state.move_selected(
                    delta_time=0.0,
                    delta_pitch=value,
                )
            elif operation == "velocity_offset":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("invalid composite velocity delta")
                next_state = selected_state.offset_selected_velocity(value)
            elif operation == "time_shift":
                delta_time = 0.0 if value is None else float(value)
                if not self._is_finite_number(delta_time):
                    raise ValueError("invalid composite time delta")
                next_state = selected_state.move_selected(
                    delta_time=delta_time,
                    delta_pitch=0,
                )
            elif operation == "delete":
                next_state = selected_state.delete_selected()
            else:
                raise ValueError("unsupported composite operation")
            return self._run_history_transition(
                lambda: self._harmony_history.execute(next_state),
                failure_log="Composite operation failed",
                feedback="复合音符操作失败，请重试",
            )
        except Exception:
            logger.exception("Composite operation validation failed")
            self.feedbackRequested.emit("复合音符操作无效，已保留原值")
            return False

    @Slot("QVariantList", str, float, int, result=bool)
    def importDetectedNotes(
        self,
        raw_notes: list[dict[str, object]],
        start_mode: str = "project_start",
        start_seconds: float = 0.0,
        tempo_bpm: int = 0,
    ) -> bool:
        """Import temporary audio candidates as one normal editor transaction."""
        if start_mode not in {"project_start", "playhead"}:
            self.feedbackRequested.emit("导入位置无效")
            return False
        try:
            offset = float(start_seconds) if start_mode == "playhead" else 0.0
            if not self._is_finite_number(offset) or offset < 0:
                raise ValueError("invalid import offset")
            imported: list[Note] = []
            for raw in raw_notes or []:
                if not isinstance(raw, dict):
                    continue
                pitch = int(raw["pitch"])
                start = float(raw.get("startTime", raw.get("start", 0.0))) + offset
                duration = float(raw["duration"])
                velocity = int(raw.get("velocity", 75))
                imported.append(
                    Note.create(
                        pitch=pitch,
                        start_time=start,
                        duration=duration,
                        velocity=velocity,
                        instrument="piano",
                        status="pending",
                        layer_id=self._active_layer_id,
                    )
                )
            if not imported:
                self.feedbackRequested.emit("没有可导入的音符")
                return False

            base_state = self.state
            normalized_tempo = int(tempo_bpm)
            if normalized_tempo:
                normalized_tempo = validate_bpm(normalized_tempo)
                if normalized_tempo != base_state.tempo.bpm:
                    base_state = base_state.with_tempo(
                        replace(base_state.tempo, bpm=normalized_tempo)
                    )
            imported_ids = frozenset(note.id for note in imported)
            next_state = ProjectState(
                notes=(*base_state.notes, *imported),
                selected_ids=imported_ids,
                tempo=base_state.tempo,
                playback_notes=base_state.playback_notes,
                playback_tempo=base_state.playback_tempo,
                layers=base_state.layers,
                controls=base_state.controls,
                playback_controls=base_state.playback_controls,
            )
            succeeded = self._run_history_transition(
                lambda: self._harmony_history.execute(next_state),
                failure_log="Audio note import failed",
                feedback="导入音符失败，请重试",
            )
            if succeeded:
                self.feedbackRequested.emit(f"已导入 {len(imported)} 个旋律音符")
            return succeeded
        except Exception:
            logger.exception("Audio note import validation failed")
            self.feedbackRequested.emit("导入音符失败，请检查检测结果")
            return False

    def importMidiResult(
        self, result: MidiImportResult, mode: str = "append"
    ) -> bool:
        """Import parsed MIDI as one undoable Add or Replace transaction."""
        if not isinstance(result, MidiImportResult):
            self.feedbackRequested.emit("MIDI 导入结果无效")
            return False
        if mode not in {"append", "replace"}:
            self.feedbackRequested.emit("MIDI 导入方式无效")
            return False
        try:
            if not result.notes and not result.controls:
                self.feedbackRequested.emit("MIDI 文件中没有可导入的音符")
                return False
            tempo = TempoMap(
                bpm=validate_bpm(int(result.bpm)),
                beats_per_bar=int(result.beats_per_bar),
                beat_unit=int(result.beat_unit),
            )
            imported, imported_layers = materialize_midi_import(result, tempo)
            imported_controls = materialize_midi_controls(result, tempo)
            if not imported and not imported_controls:
                self.feedbackRequested.emit("MIDI 文件中没有有效音符")
                return False
            imported_ids = frozenset(note.id for note in imported)
            if mode == "append":
                base_state = self.state
                if base_state.tempo != tempo:
                    base_state = base_state.with_tempo(tempo)
                if not base_state.notes and not base_state.controls and len(base_state.layers) == 1:
                    merged_layers = imported_layers
                    merged_notes = imported
                else:
                    existing_ids = set(base_state.layer_ids)
                    layer_id_map: dict[str, str] = {}
                    appended_layers: list[MusicLayer] = []
                    for imported_layer in imported_layers:
                        candidate = imported_layer.id
                        suffix = 2
                        while candidate in existing_ids:
                            candidate = f"{imported_layer.id}-{suffix}"
                            suffix += 1
                        existing_ids.add(candidate)
                        layer_id_map[imported_layer.id] = candidate
                        appended_layers.append(
                            imported_layer.with_changes(
                                id=candidate,
                                order=len(base_state.layers) + len(appended_layers),
                            )
                        )
                    merged_layers = (*base_state.layers, *appended_layers)
                    merged_notes = tuple(
                        note.with_changes(
                            layer_id=layer_id_map[note.layer_id],
                            updated_at=note.updated_at,
                        )
                        for note in imported
                    )
                    imported_ids = frozenset(note.id for note in merged_notes)
                    imported_controls = tuple(e.with_changes(layer_id=layer_id_map[e.layer_id]) for e in imported_controls)
                next_state = ProjectState(
                    notes=(*base_state.notes, *merged_notes),
                    selected_ids=imported_ids,
                    tempo=base_state.tempo,
                    playback_notes=base_state.playback_notes,
                    playback_tempo=base_state.playback_tempo,
                    layers=merged_layers,
                    controls=(*base_state.controls, *imported_controls),
                    playback_controls=base_state.playback_controls,
                )
            else:
                next_state = ProjectState(
                    notes=imported,
                    selected_ids=imported_ids,
                    tempo=tempo,
                    layers=imported_layers,
                    controls=imported_controls,
                    playback_controls=(),
                )
            succeeded = self._run_history_transition(
                lambda: self._harmony_history.execute(next_state),
                failure_log="MIDI note import failed",
                feedback="导入 MIDI 音符失败，请重试",
            )
            if succeeded:
                # MIDI is already a complete note source, so make the imported
                # snapshot immediately available to playback and export. This
                # is a system sync and does not add a second undo entry.
                self.synchronizeNow()
                self.feedbackRequested.emit(f"已导入 {len(imported)} 个 MIDI 音符")
            return succeeded
        except Exception:
            logger.exception("MIDI note import validation failed")
            self.feedbackRequested.emit("MIDI 导入失败，请检查文件内容")
            return False

    @staticmethod
    def _is_finite_number(value: object) -> bool:
        try:
            return bool(float(value) == float(value)) and abs(float(value)) != float("inf")
        except (TypeError, ValueError):
            return False

    @Slot(str, "QVariant")
    def setSelectedProperty(self, name: str, value) -> None:
        allowed = {"pitch", "start_time", "duration", "velocity", "instrument"}
        if name not in allowed:
            self.feedbackRequested.emit("不支持的属性")
            return
        try:
            state = self.state.update_selected(**{name: value})
            self._execute(state)
        except Exception:
            logger.exception("Set selected property failed")
            self.feedbackRequested.emit("属性值无效，已保留原值")

    @Slot(str, "QVariant", result=bool)
    def setSelectedRelease(self, mode: str, release_ms=None) -> bool:
        try:
            if mode not in NOTE_RELEASE_MODES:
                raise ValueError("unsupported release mode")
            if release_ms is not None and (
                isinstance(release_ms, bool)
                or not isinstance(release_ms, (int, float))
                or not self._is_finite_number(release_ms)
                or release_ms < 0.0
                or release_ms > MAX_RELEASE_MS
            ):
                raise ValueError("invalid release duration")
            normalized_release_ms = (
                DEFAULT_CUSTOM_RELEASE_MS
                if mode == "custom" and release_ms is None
                else (float(release_ms) if mode == "custom" else None)
            )
            selected = self.state.selected_notes
            if not selected:
                self.feedbackRequested.emit("请先选择音符")
                return False
            if all(
                note.release_mode == mode
                and note.release_ms == normalized_release_ms
                for note in selected
            ):
                return True
            next_state = self.state.update_selected(
                release_mode=mode,
                release_ms=normalized_release_ms,
            )
            return self._run_history_transition(
                lambda: self._harmony_history.execute(next_state),
                failure_log="Set selected release failed",
                feedback="释放设置失败，请重试",
            )
        except Exception:
            logger.exception("Set selected release validation failed")
            self.feedbackRequested.emit("释放设置无效，已保留原值")
            return False

    @Slot()
    def undo(self) -> None:
        self._run_history_transition(
            self._harmony_history.undo,
            failure_log="Undo failed",
            feedback="无法撤销操作",
        )

    @Slot()
    def redo(self) -> None:
        self._run_history_transition(
            self._harmony_history.redo,
            failure_log="Redo failed",
            feedback="无法重做操作",
        )

    @Slot()
    def newProject(self) -> None:
        self.recordingBoundaryRequested.emit()
        if self._project_switch_is_blocked():
            return
        state = ProjectState()
        empty_metadata = HarmonyProjectMetadata.empty()
        self._replace_project(
            state,
            saved_content=state.content,
            path=None,
            harmony_metadata=empty_metadata,
            saved_harmony_metadata=empty_metadata,
        )

    def _promote_draft_to_library_project(self) -> None:
        if self._current_path:
            return
        path = self._projects.next_project_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._set_current_path(str(path))

    @Slot()
    def saveCurrent(self) -> None:
        self.recordingBoundaryRequested.emit()
        if not self._current_path:
            self.savePathRequired.emit()
            return
        if self._projects.is_library_path(Path(self._current_path)):
            self.saveProject(self._current_path)
            return
        if self._external_save_destination == "original":
            self.saveProject(self._current_path)
            return
        if self._external_save_destination == "library":
            self.savePathRequired.emit()
            return
        self.externalSaveChoiceRequired.emit()

    @Slot(str, result=bool)
    def chooseExternalSaveDestination(self, destination: str) -> bool:
        if not self._current_path or self._projects.is_library_path(Path(self._current_path)):
            self.feedbackRequested.emit("当前工程不需要选择保存位置")
            return False
        if destination not in {"library", "original"}:
            self.feedbackRequested.emit("无效的保存位置")
            return False
        self._external_save_destination = destination
        if destination == "library":
            self.savePathRequired.emit()
            return True
        return self.saveProject(self._current_path)

    @Slot(result=bool)
    def discardUnsavedChanges(self) -> bool:
        if self._project_switch_is_blocked():
            return False
        try:
            if self._current_path:
                document = load_project_document(self._current_path)
                state = document.state
                metadata = document.harmony
                path = self._current_path
            else:
                state = ProjectState()
                metadata = HarmonyProjectMetadata.empty()
                path = None
            self._pending_open_path = None
            self._pending_recycle_path = None
            self._replace_project(
                state,
                saved_content=state.content,
                path=path,
                harmony_metadata=metadata,
                saved_harmony_metadata=metadata,
            )
            return True
        except Exception:
            logger.exception("Discard unsaved changes failed")
            self.feedbackRequested.emit(
                "鏃犳硶鎭㈠涓婃淇濆瓨鐨勫伐绋嬶紝褰撳墠淇敼浠嶇劧淇濈暀"
            )
            return False

    @Slot(str)
    def openProject(self, raw_path: str) -> None:
        self.recordingBoundaryRequested.emit()
        target = self._local_path(raw_path)
        if target == self._current_path:
            self.loadSucceeded.emit(target)
            return
        if self._project_switch_is_blocked():
            return
        if not self.isDirty:
            self._load_project(target)
            return
        self._pending_open_path = target
        self.saveCurrent()

    @Slot(str)
    def openRecent(self, raw_path: str) -> None:
        self.openProject(raw_path)

    @Slot(str)
    def removeRecent(self, raw_path: str) -> None:
        self._recent.remove(str(raw_path))
        self._notify_recent_files_changed()

    @Slot(str, str, result=bool)
    def renameProject(self, raw_path: str, name: str) -> bool:
        try:
            path = Path(self._local_path(raw_path))
            if not self._projects.is_library_path(path):
                raise ValueError("only library projects can be renamed")
            normalized = self._projects.set_display_name(path, name)
            self._notify_recent_files_changed()
            if str(path) == self._current_path:
                self.currentFileChanged.emit()
            self.feedbackRequested.emit(f"已重命名为：{normalized}")
            return True
        except Exception:
            self.feedbackRequested.emit("工程名称无效或已存在")
            return False

    @Slot(str, result=bool)
    def saveNamedProject(self, name: str) -> bool:
        self.recordingBoundaryRequested.emit()
        if self._is_saving:
            self.feedbackRequested.emit("正在保存，请稍候")
            return False
        result = self.validateProjectName(name)
        if not result["valid"]:
            self.feedbackRequested.emit(str(result["error"] or "工程名称无效"))
            return False
        try:
            self.synchronizeNow()
            path = self._projects.allocate_project_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = ProjectDocument(self.state, self.harmony_metadata)
            return self._start_save(
                path,
                snapshot,
                display_name=str(result["normalized"]),
                cleanup_target_parent=True,
            )
        except Exception:
            logger.exception("Named project save failed")
            self.feedbackRequested.emit("无法开始保存")
            return False

    @Slot(str, result=bool)
    def saveCopy(self, name: str) -> bool:
        self.recordingBoundaryRequested.emit()
        if self._is_saving:
            self.feedbackRequested.emit("正在保存，请稍候")
            return False
        result = self.validateProjectName(name)
        if not result["valid"]:
            self.feedbackRequested.emit(str(result["error"] or "工程名称无效"))
            return False
        try:
            self.synchronizeNow()
            path = self._projects.allocate_project_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = ProjectDocument(self.state, self.harmony_metadata)
            return self._start_save(
                path,
                snapshot,
                operation="copy",
                display_name=str(result["normalized"]),
                inherit_metadata_from=(
                    self._current_path if self.currentProjectIsLibrary else None
                ),
                cleanup_target_parent=True,
            )
        except Exception:
            logger.exception("Project copy save failed")
            self.feedbackRequested.emit("无法创建工程副本")
            return False

    @Slot(str)
    def copyProject(self, raw_path: str) -> None:
        try:
            if self._project_switch_is_blocked():
                return
            if self.isDirty:
                self.feedbackRequested.emit("请先保存或放弃当前修改，再创建工程副本")
                return
            copied = self._projects.copy_project(Path(self._local_path(raw_path)))
            self._notify_recent_files_changed()
            self._load_project(str(copied))
            self.feedbackRequested.emit(f"已创建并打开副本：{self._projects.display_name_for(copied)}")
        except Exception:
            logger.exception("Copy project failed")
            self.feedbackRequested.emit("无法复制工程")

    @Slot(str)
    def toggleProjectFavorite(self, raw_path: str) -> None:
        try:
            path = Path(self._local_path(raw_path))
            favorite = self._projects.toggle_favorite(path)
            self._notify_recent_files_changed()
            self.feedbackRequested.emit("已收藏工程" if favorite else "已取消收藏")
        except Exception:
            logger.exception("Toggle project favorite failed")
            self.feedbackRequested.emit("无法更新收藏状态")

    @Slot(str)
    def recycleProject(self, raw_path: str) -> None:
        try:
            target = Path(self._local_path(raw_path))
            if str(target) == self._current_path:
                self._pending_recycle_path = str(target)
                if self.isDirty or self._is_saving:
                    if not self._is_saving:
                        self.saveCurrent()
                    self.feedbackRequested.emit("正在保存当前工程，保存后会移到回收站")
                    return
            self._complete_recycle(target)
        except Exception:
            logger.exception("Recycle project failed")
            self.feedbackRequested.emit("无法移动工程到回收站")

    @Slot(list)
    def recycleProjects(self, raw_paths: list) -> None:
        """Move selected non-current library projects to the recycle bin."""
        if self._recycle_job is not None:
            self.feedbackRequested.emit("正在移动工程到回收站，请稍候")
            return
        if self._project_switch_is_blocked():
            return
        current = Path(self._current_path).resolve(strict=False) if self._current_path else None
        targets: list[Path] = []
        seen: set[Path] = set()
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path:
                continue
            try:
                target = Path(self._local_path(raw_path))
            except ValueError:
                continue
            if target not in seen:
                seen.add(target)
                targets.append(target)

        valid_targets: list[Path] = []
        for target in targets:
            if target == current or not target.is_file():
                continue
            valid_targets.append(target)

        if not valid_targets:
            self.feedbackRequested.emit("没有可移动到回收站的工程")
            return

        try:
            job = _RecycleProjectsJob(self._projects, tuple(valid_targets))
            job.signals.completed.connect(
                lambda path, succeeded: self._finish_recycle_item(job, path, succeeded)
            )
            job.signals.finished.connect(lambda: self._finish_recycle_projects(job))
            self._recycle_job = job
            self._recycle_progress = 0
            self._recycle_total = len(valid_targets)
            self._recycle_succeeded = 0
            self.recycleProgressChanged.emit()
            QThreadPool.globalInstance().start(job)
        except Exception:
            logger.exception("Could not start bulk project recycle")
            self._recycle_job = None
            self.recycleProgressChanged.emit()
            self.feedbackRequested.emit("无法开始移动工程到回收站")

    def _finish_recycle_item(
        self, job: _RecycleProjectsJob, path: str, succeeded: bool
    ) -> None:
        if self._recycle_job is not job:
            return
        self._recycle_progress += 1
        if succeeded:
            self._recycle_succeeded += 1
            self._recent.remove(path)
        self.recycleProgressChanged.emit()

    def _finish_recycle_projects(self, job: _RecycleProjectsJob) -> None:
        if self._recycle_job is not job:
            return
        failed = self._recycle_total - self._recycle_succeeded
        self._recycle_job = None
        self.recycleProgressChanged.emit()
        if self._recycle_succeeded:
            self._notify_recent_files_changed()
        self.recycleFinished.emit(self._recycle_succeeded, failed)
        if failed:
            self.feedbackRequested.emit(
                f"已移动 {self._recycle_succeeded} 个工程到回收站，{failed} 个未能移动"
            )
        else:
            self.feedbackRequested.emit(f"已移动 {self._recycle_succeeded} 个工程到回收站")

    def _complete_recycle(self, target: Path) -> None:
        is_current = str(target) == self._current_path
        self._projects.recycle(target)
        self._recent.remove(str(target))
        if is_current:
            self.newProject()
        self._notify_recent_files_changed()
        self.feedbackRequested.emit("工程已移动到回收站")

    @Slot(result=bool)
    def restoreRecovery(self) -> bool:
        if self._project_switch_is_blocked():
            return False
        result = self._recovery.read_document()
        if result is None:
            self.recoveryAvailableChanged.emit()
            self.feedbackRequested.emit("没有可恢复的工程")
            return False
        document, original_path = result
        self._replace_project(
            document.state,
            saved_content=None,
            path=original_path,
            harmony_metadata=document.harmony,
            saved_harmony_metadata=None,
        )
        self._recovery.clear()
        self.recoveryAvailableChanged.emit()
        self.feedbackRequested.emit("已恢复上次未保存的工程")
        return True

    @Slot()
    def discardRecovery(self) -> None:
        self._recovery.clear()
        self.recoveryAvailableChanged.emit()
        self.feedbackRequested.emit("已放弃恢复内容")

    @Slot(str)
    def loadProject(self, raw_path: str) -> None:
        self.recordingBoundaryRequested.emit()
        self.openProject(raw_path)

    def _load_project(self, raw_path: str) -> None:
        try:
            path = self._local_path(raw_path)
            document = load_project_document(path)
            self._replace_project(
                document.state,
                saved_content=document.state.content,
                path=path,
                harmony_metadata=document.harmony,
                saved_harmony_metadata=document.harmony,
            )
            self._remember_recent(path)
            self.loadSucceeded.emit(path)
        except Exception as error:
            logger.exception("Project load failed")
            self.loadFailed.emit(str(error))
            self.feedbackRequested.emit("无法打开工程文件")

    @Slot(str)
    def exportMidi(self, raw_path: str) -> None:
        self.recordingBoundaryRequested.emit()
        try:
            self.synchronizeNow()
            if self.state.has_unsynced_changes:
                raise ValueError('finish the current edit before MIDI export')
            path = self._local_path(raw_path)
            if not self.state.playback_notes and not self.state.playback_controls:
                raise ValueError("project has no notes to export")
            export_midi(
                path,
                self.state.effective_notes(),
                self.state.playback_tempo,
                layers=self.state.layers,
                controls=self.state.effective_controls(),
            )
            self.midiExported.emit(path)
        except Exception as error:
            logger.exception("MIDI export failed")
            self.midiFailed.emit(str(error))
            self.feedbackRequested.emit("无法导出 MIDI")

    @Slot(str, str)
    def exportProjectMidi(self, raw_project_path: str, raw_output_path: str) -> None:
        try:
            project_path = self._local_path(raw_project_path)
            output_path = self._local_path(raw_output_path)
            state = load_project(project_path)
            if not state.playback_notes and not state.playback_controls:
                raise ValueError("project has no notes to export")
            export_midi(
                output_path,
                state.effective_notes(),
                state.playback_tempo,
                layers=state.layers,
                controls=state.effective_controls(),
            )
            self.midiExported.emit(output_path)
        except Exception as error:
            logger.exception("Project MIDI export failed")
            self.midiFailed.emit(str(error))
            self.feedbackRequested.emit("无法导出 MIDI")

    @Slot(str, result=bool)
    def saveProject(self, raw_path: str) -> bool:
        self.recordingBoundaryRequested.emit()
        if self._is_saving:
            self.feedbackRequested.emit("正在保存，请稍候")
            return False
        try:
            self.synchronizeNow()
            path = self._local_path(raw_path)
            snapshot = ProjectDocument(self.state, self.harmony_metadata)
            return self._start_save(
                path,
                snapshot,
                inherit_metadata_from=None,
            )
        except Exception as error:
            logger.exception("Could not start project save")
            self.saveFailed.emit(str(error))
            self.feedbackRequested.emit("无法开始保存")
            return False

    @Slot(str, result=bool)
    def exportProjectFile(self, raw_path: str) -> bool:
        self.recordingBoundaryRequested.emit()
        if self._is_saving:
            self.feedbackRequested.emit("正在保存，请稍候")
            return False
        try:
            self.synchronizeNow()
            path = self._local_path(raw_path)
            snapshot = ProjectDocument(self.state, self.harmony_metadata)
            return self._start_save(
                path,
                snapshot,
                operation="export",
                activate_current=False,
            )
        except Exception as error:
            logger.exception("Could not start project export")
            self.projectExportFailed.emit(str(error))
            self.feedbackRequested.emit("无法导出工程文件")
            return False

    def _start_save(
        self,
        path: Path | str,
        snapshot: ProjectDocument,
        *,
        operation: str = "save",
        activate_current: bool = True,
        display_name: str | None = None,
        inherit_metadata_from: str | None = None,
        cleanup_target_parent: bool = False,
    ) -> bool:
        if self._is_saving:
            self.feedbackRequested.emit("正在保存，请稍候")
            return False
        target = Path(path).resolve(strict=False)
        self._next_save_generation += 1
        job = _SaveJob(str(target), snapshot, self._save_pool)
        self._active_save = _SaveContext(
            job,
            snapshot,
            self._project_session_id,
            self._next_save_generation,
            operation,
            activate_current,
            display_name,
            inherit_metadata_from,
            cleanup_target_parent=cleanup_target_parent,
        )
        job.signals.succeeded.connect(
            self._finish_save, Qt.ConnectionType.QueuedConnection
        )
        job.signals.failed.connect(
            self._finish_save_failure, Qt.ConnectionType.QueuedConnection
        )
        self._save_jobs.add(job)
        self._is_saving = True
        self.savingChanged.emit()
        self._save_pool.start(job)
        return True

    @staticmethod
    def _local_path(raw_path: str) -> str:
        if not raw_path:
            raise ValueError("save path cannot be empty")
        url = QUrl(raw_path)
        local_path = url.toLocalFile() if url.isLocalFile() else raw_path
        return str(Path(local_path).resolve(strict=False))

    def _project_switch_is_blocked(self) -> bool:
        if self._recycle_job is not None:
            self.feedbackRequested.emit("正在移动工程到回收站，请完成后再切换")
            return True
        if self._is_saving:
            self.feedbackRequested.emit("正在保存当前工程，请稍候再切换")
            return True
        return False

    def _retire_active_save(self) -> _SaveContext | None:
        context = self._active_save
        if context is None:
            return None
        self._active_save = None
        self._save_jobs.discard(context.job)
        self._is_saving = bool(self._save_jobs)
        self.savingChanged.emit()
        if (
            context.session_id != self._project_session_id
            or context.generation <= self._last_settled_save_generation
        ):
            self._update_autosave_state()
            return None
        return context

    @Slot(str)
    def _finish_save(self, path: str) -> None:
        context = self._retire_active_save()
        if context is None:
            return
        self._last_settled_save_generation = context.generation
        snapshot = context.snapshot

        if context.operation == "export":
            self._update_autosave_state()
            self.projectExported.emit(path)
            return

        self._saved_content = snapshot.state.content
        self._saved_harmony_metadata = snapshot.harmony
        if context.inherit_metadata_from:
            try:
                self._projects.copy_metadata(
                    Path(context.inherit_metadata_from), Path(path)
                )
            except Exception:
                logger.exception("Project copy metadata update failed")
        if context.display_name:
            try:
                self._projects.set_display_name(Path(path), context.display_name)
            except Exception:
                logger.exception("Project display name update failed")
                self.feedbackRequested.emit("文件已保存，但工程名称未能更新")
        if context.activate_current:
            self._set_current_path(path)
            if self._projects.is_library_path(Path(path)):
                self._external_save_destination = None
            else:
                # A direct save to an explicit external path establishes the
                # user's intent for subsequent Ctrl+S operations.  Projects
                # opened from disk still start with no policy and therefore
                # ask once before their first ordinary save.
                self._external_save_destination = "original"
        try:
            self._projects.update_summary(Path(path), snapshot.state)
        except Exception:
            logger.exception("Project library metadata update failed")
        if context.activate_current:
            self._remember_recent(path, snapshot.state)
        self.dirtyChanged.emit()
        self._update_autosave_state()
        self.saveSucceeded.emit(path)
        pending_open_path = self._pending_open_path
        self._pending_open_path = None
        if pending_open_path:
            self.loadProject(pending_open_path)
        pending_recycle_path = self._pending_recycle_path
        self._pending_recycle_path = None
        if pending_recycle_path and pending_recycle_path == path:
            try:
                self._complete_recycle(Path(pending_recycle_path))
            except Exception:
                logger.exception("Recycle project after save failed")
                self.feedbackRequested.emit("保存完成，但无法移动工程到回收站")

    @Slot(str)
    def _finish_save_failure(self, message: str) -> None:
        context = self._retire_active_save()
        if context is None:
            return
        self._last_settled_save_generation = context.generation
        self._update_autosave_state()
        if context.cleanup_target_parent:
            target = Path(context.job.path)
            try:
                if not target.exists() and target.parent.is_dir():
                    target.parent.rmdir()
            except OSError:
                logger.exception("Reserved library folder cleanup failed")
        self._pending_open_path = None
        self._pending_recycle_path = None
        if context.operation == "export":
            self.projectExportFailed.emit(message)
        self.saveFailed.emit(message)
        self.feedbackRequested.emit("保存失败，请重试")
