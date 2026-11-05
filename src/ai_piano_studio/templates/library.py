from __future__ import annotations

import json
import logging
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl

from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, PianoVoiceProfile, ReleaseMode
from ai_piano_studio.audio.source import PianoSoundSource
from ai_piano_studio.persistence.project_document import (
    ProjectDocument,
    load_project_document,
    save_project_document,
)
from ai_piano_studio.persistence.project_library import ProjectLibrary


logger = logging.getLogger(__name__)

RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "templates"


class TemplateMusicLibrary(QObject):
    """Read-only built-in templates with a player isolated from the editor."""

    templatesChanged = Signal()
    previewChanged = Signal()
    projectCreated = Signal(str)
    errorRaised = Signal(str)

    def __init__(
        self,
        project_library: ProjectLibrary | None = None,
        preview_player: AudioPlayer | None = None,
        resource_root: Path | None = None,
        parent=None,
        piano_profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
        piano_source: PianoSoundSource | None = None,
    ) -> None:
        super().__init__(parent)
        self._resource_root = Path(resource_root or RESOURCE_ROOT)
        self._project_library = project_library or ProjectLibrary()
        self._preview_player = preview_player
        self.piano_profile = piano_profile
        self.piano_source = piano_source or getattr(preview_player, "piano_source", None)
        self._catalog: list[dict[str, object]] = []
        self._documents: dict[str, ProjectDocument] = {}
        self._active_template_id = ""
        self._previewing = False
        self._preview_position = 0.0
        self._load_catalog()
        self._connect_preview_player()

    def _connect_preview_player(self) -> None:
        if self._preview_player is None:
            return
        self._preview_player.playingChanged.connect(self._on_preview_playing_changed)
        self._preview_player.positionChanged.connect(self._on_preview_position_changed)

    def _ensure_preview_player(self) -> AudioPlayer:
        if self._preview_player is None:
            self._preview_player = AudioPlayer(self, piano_source=self.piano_source)
            self._connect_preview_player()
        return self._preview_player

    @Property("QVariantList", notify=templatesChanged)
    def templates(self) -> list[dict[str, object]]:
        return list(self._catalog)

    @Property(str, notify=previewChanged)
    def activeTemplateId(self) -> str:
        return self._active_template_id

    @Slot(str, result="QVariantList")
    def previewNotes(self, template_id: str) -> list[dict[str, float | int]]:
        """Read-only note projection for the home preview, from the played score."""
        document = self._documents.get(str(template_id))
        if document is None:
            return []
        return [
            {"pitch": note.pitch, "start": note.start_time, "duration": note.duration}
            for note in document.state.playback_notes
        ]

    @Property(bool, notify=previewChanged)
    def previewing(self) -> bool:
        return self._previewing

    @Property(float, notify=previewChanged)
    def previewPosition(self) -> float:
        return self._preview_position

    def _load_catalog(self) -> None:
        catalog: list[dict[str, object]] = []
        for metadata_path in sorted(self._resource_root.glob("*/metadata.json")):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                template_id = str(metadata["id"])
                project_path = metadata_path.parent / "template.pianoai.json"
                document = load_project_document(project_path)
                state = document.state
                metadata = dict(metadata)
                metadata["durationSeconds"] = state.playback_duration
                metadata["noteCount"] = len(state.playback_notes)
                metadata["thumbnailSource"] = QUrl.fromLocalFile(
                    str(metadata_path.parent / "thumbnail.png")
                ).toString()
                catalog.append(metadata)
                self._documents[template_id] = document
            except Exception:
                logger.exception("Built-in template load failed: %s", metadata_path)
        self._catalog = catalog
        self.templatesChanged.emit()

    def _document_for(self, template_id: str) -> ProjectDocument:
        document = self._documents.get(str(template_id))
        if document is None:
            raise ValueError("unknown built-in template")
        return document

    def _state_for(self, template_id: str):
        return self._document_for(template_id).state

    @Slot(str)
    def preview(self, template_id: str) -> None:
        template_id = str(template_id)
        if self._active_template_id == template_id and self._previewing:
            self.stopPreview()
            return
        try:
            state = self._state_for(template_id)
            player = self._ensure_preview_player()
            service_reference = weakref.ref(self)

            def start_after_retirement() -> None:
                service = service_reference()
                if service is not None:
                    service._start_preview(template_id, state)

            player.stop(ReleaseMode.SAFE, after_stopped=start_after_retirement)
        except Exception:
            logger.exception("Built-in template preview failed: %s", template_id)
            self._active_template_id = ""
            self._previewing = False
            self.errorRaised.emit("内置音乐试听失败，请重试")

    def _start_preview(self, template_id: str, state) -> None:
        try:
            self._ensure_preview_player().play_notes(tuple(state.playback_notes))
            self._active_template_id = template_id
            self._previewing = True
            self._preview_position = 0.0
            self.previewChanged.emit()
        except Exception:
            logger.exception("Built-in template preview failed after retirement: %s", template_id)
            self._active_template_id = ""
            self._previewing = False
            self.errorRaised.emit("内置音乐试听失败，请重试")

    @Slot()
    def stopPreview(self) -> None:
        if self._preview_player is not None:
            self._preview_player.stop(ReleaseMode.SAFE)
        changed = self._active_template_id or self._previewing or self._preview_position
        self._active_template_id = ""
        self._previewing = False
        self._preview_position = 0.0
        if changed:
            self.previewChanged.emit()

    @Slot(str)
    def adapt(self, template_id: str) -> None:
        template_id = str(template_id)
        try:
            document = self._document_for(template_id)
            metadata = next(item for item in self._catalog if item["id"] == template_id)
            display_name = self._project_library.unique_display_name(
                f"{metadata.get('name', template_id)} 改编"
            )
            path = self._project_library.next_project_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            save_project_document(path, document)
            self._project_library.set_display_name(path, display_name)
            self.stopPreview()
            self.projectCreated.emit(str(path))
        except Exception:
            logger.exception("Built-in template adaptation failed: %s", template_id)
            self.errorRaised.emit("无法创建改编工程，请重试")

    def _on_preview_playing_changed(self, playing: bool) -> None:
        value = bool(playing)
        if value != self._previewing:
            self._previewing = value
            self.previewChanged.emit()

    def _on_preview_position_changed(self, seconds: float) -> None:
        value = max(0.0, float(seconds))
        if value != self._preview_position:
            self._preview_position = value
            self.previewChanged.emit()
