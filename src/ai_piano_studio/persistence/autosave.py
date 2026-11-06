from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QCoreApplication, QObject, QStandardPaths, Signal

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_document import (
    ProjectDocument,
    load_project_document,
    save_project_document,
)
from ai_piano_studio.persistence.recent_files import display_name


logger = logging.getLogger(__name__)

SNAPSHOT_NAME = "session.pianoai.json"
META_NAME = "session.meta.json"


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class RecoveryStore(QObject):
    """Persists a crash-recovery snapshot of the current project.

    The snapshot lives in an application data directory, never next to the
    user's project files. Explicit save/load/new actions clear it; quitting
    the app keeps it so unsaved work survives crashes and accidental closes.
    """

    written = Signal()

    def __init__(self, directory: str | Path) -> None:
        super().__init__()
        self._directory = Path(directory)
        self._writer = None

    @property
    def pending(self) -> bool:
        return self._writer is not None and self._writer.pending

    def queue_document(self, document: ProjectDocument, original_path: str | None) -> None:
        if self._writer is None:
            from .recovery_writer import RecoveryWriter
            self._writer = RecoveryWriter(self._directory)
            self._writer.completed.connect(self.written)
            app = QCoreApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._writer.flush)
        self._writer.queue(document, original_path)

    def flush(self) -> None:
        if self._writer is not None:
            self._writer.flush()

    def cancel_pending(self) -> None:
        if self._writer is not None:
            self._writer.invalidate()

    @property
    def available(self) -> bool:
        return (self._directory / SNAPSHOT_NAME).is_file()

    def write(self, state: ProjectState, original_path: str | None) -> None:
        self.write_document(ProjectDocument(state), original_path)

    def write_document(
        self, document: ProjectDocument, original_path: str | None
    ) -> None:
        if self._writer is not None:
            self._writer.invalidate()
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            save_project_document(self._directory / SNAPSHOT_NAME, document, compact=True)
            _write_json_atomic(
                self._directory / META_NAME,
                {
                    "originalPath": original_path,
                    "savedAt": datetime.now(timezone.utc).isoformat(),
                    "noteCount": len(document.state.playback_notes),
                    "durationSeconds": document.state.playback_duration,
                },
            )
        except Exception:
            logger.exception("Recovery snapshot write failed")

    def info(self) -> dict[str, object] | None:
        """Lightweight metadata for the recovery card; None when no snapshot."""
        if not self.available:
            return None
        info: dict[str, object] = {
            "originalPath": None,
            "savedAt": "",
            "noteCount": None,
            "durationSeconds": None,
        }
        try:
            meta = json.loads((self._directory / META_NAME).read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                for key in info:
                    if key in meta:
                        info[key] = meta[key]
        except Exception:
            logger.exception("Recovery metadata is unreadable")
        original_path = info.get("originalPath")
        info["name"] = (
            display_name(original_path)
            if isinstance(original_path, str) and original_path
            else "未命名工程"
        )
        return info

    def read(self) -> tuple[ProjectState, str | None] | None:
        result = self.read_document()
        if result is None:
            return None
        document, original_path = result
        return document.state, original_path

    def read_document(self) -> tuple[ProjectDocument, str | None] | None:
        snapshot = self._directory / SNAPSHOT_NAME
        if not snapshot.is_file():
            return None
        try:
            document = load_project_document(snapshot)
        except Exception:
            logger.exception("Recovery snapshot is unreadable")
            return None
        original_path: str | None = None
        try:
            meta = json.loads((self._directory / META_NAME).read_text(encoding="utf-8"))
            raw_path = meta.get("originalPath")
            if isinstance(raw_path, str) and raw_path:
                original_path = raw_path
        except Exception:
            logger.exception("Recovery metadata is unreadable; path ignored")
        return document, original_path

    def clear(self) -> None:
        if self._writer is not None:
            self._writer.invalidate()
        for name in (SNAPSHOT_NAME, META_NAME):
            try:
                (self._directory / name).unlink(missing_ok=True)
            except Exception:
                logger.exception("Recovery snapshot cleanup failed")


class NullRecoveryStore:
    """No-op store used when no data directory is available (tests, tools)."""

    @property
    def available(self) -> bool:
        return False

    @property
    def pending(self) -> bool:
        return False

    def queue_document(self, document: ProjectDocument, original_path: str | None) -> None:
        self.write_document(document, original_path)

    def write(self, state: ProjectState, original_path: str | None) -> None:
        pass

    def write_document(
        self, document: ProjectDocument, original_path: str | None
    ) -> None:
        pass

    def read(self) -> tuple[ProjectState, str | None] | None:
        return None

    def read_document(self) -> tuple[ProjectDocument, str | None] | None:
        return None

    def info(self) -> dict[str, object] | None:
        return None

    def clear(self) -> None:
        pass


def default_recovery_store() -> RecoveryStore | NullRecoveryStore:
    """Build the store for the running app.

    Only the real application configures organization/application names, so
    tests and tools get an isolated no-op store and never touch user data.
    """
    if not (QCoreApplication.organizationName() and QCoreApplication.applicationName()):
        return NullRecoveryStore()
    base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if not base:
        return NullRecoveryStore()
    return RecoveryStore(Path(base) / "recovery")
