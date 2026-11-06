"""Serial, coalescing recovery writes; publish only the current generation."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from threading import Event, RLock

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .project_document import ProjectDocument, save_project_document

logger = logging.getLogger(__name__)


class _Job(QRunnable):
    def __init__(self, writer):
        super().__init__()
        self.writer = writer

    def run(self):
        self.writer.run()


class RecoveryWriter(QObject):
    completed = Signal()

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        self.lock = RLock()
        self.finished = Event()
        self.finished.set()
        self.generation = 0
        self.next_document = None
        self.active = False

    @property
    def pending(self):
        with self.lock:
            return self.active

    def invalidate(self):
        with self.lock:
            self.generation += 1
            self.next_document = None

    def queue(self, document: ProjectDocument, original_path: str | None):
        with self.lock:
            self.generation += 1
            self.next_document = (self.generation, document, original_path)
            if self.active:
                return
            self.active = True
            self.finished.clear()
        QThreadPool.globalInstance().start(_Job(self))

    def flush(self):
        # Publication happens in the worker, without needing GUI events. This
        # also works when aboutToQuit has stopped the application's event loop.
        self.finished.wait()

    def run(self):
        from datetime import datetime, timezone
        from .autosave import SNAPSHOT_NAME, META_NAME, _write_json_atomic

        while True:
            with self.lock:
                request = self.next_document
                self.next_document = None
                if request is None:
                    self.active = False
                    self.finished.set()
                    return
            generation, document, original_path = request
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".recovery-", dir=self.directory) as temp:
                    stage = Path(temp)
                    save_project_document(stage / SNAPSHOT_NAME, document, compact=True)
                    _write_json_atomic(stage / META_NAME, {
                        "originalPath": original_path,
                        "savedAt": datetime.now(timezone.utc).isoformat(),
                        "noteCount": len(document.state.playback_notes),
                        "durationSeconds": document.state.playback_duration,
                    })
                    with self.lock:
                        if generation != self.generation:
                            continue
                        # Only the two final atomic renames are serialized with
                        # cancellation. Encoding, fsync and cleanup stay outside.
                        os.replace(stage / SNAPSHOT_NAME, self.directory / SNAPSHOT_NAME)
                        os.replace(stage / META_NAME, self.directory / META_NAME)
                    self.completed.emit()
            except Exception:
                logger.exception("Background recovery snapshot write failed")
