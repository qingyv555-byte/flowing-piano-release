from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings


MAX_RECENT_FILES = 8
_SETTINGS_KEY = "recentFiles"
_ENTRIES_KEY = "recentEntries"

_NAME_SUFFIXES = (".pianoai.json", ".json")


def display_name(path: str) -> str:
    """Human-friendly project name derived from a file path."""
    name = Path(path).name
    for suffix in _NAME_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name or path


def _parse_entries(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, str) or not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict) and entry.get("path")]


class RecentFiles:
    """Tracks recently opened project files.

    Persists through QSettings when the application names are configured
    (the real app); otherwise falls back to an in-memory list so tests and
    tools never touch the user registry. Pass an explicit QSettings to
    control the storage location.

    Alongside the plain path list, each entry keeps lightweight metadata
    (display name, last-opened time, note count, duration) so the welcome
    screen can render rich project cards without reopening the files.
    """

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings
        self._memory: list[str] = []
        self._memory_entries: list[dict[str, object]] = []

    def _store(self) -> QSettings | None:
        if self._settings is not None:
            return self._settings
        if QCoreApplication.organizationName() and QCoreApplication.applicationName():
            return QSettings()
        return None

    def files(self) -> list[str]:
        store = self._store()
        if store is None:
            return list(self._memory)
        raw = store.value(_SETTINGS_KEY, [])
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return []
        return [path for path in raw if isinstance(path, str) and path]

    def entries(self) -> list[dict[str, object]]:
        """Ordered recent entries with metadata and a live existence flag."""
        known = {str(entry["path"]): entry for entry in self._raw_entries()}
        ordered: list[dict[str, object]] = []
        for path in self.files():
            entry = dict(known.get(path, {}))
            entry["path"] = path
            entry.setdefault("name", display_name(path))
            entry.setdefault("lastOpened", "")
            entry.setdefault("noteCount", None)
            entry.setdefault("durationSeconds", None)
            entry["exists"] = os.path.isfile(path)
            ordered.append(entry)
        return ordered

    def remember(
        self,
        path: str,
        *,
        note_count: int | None = None,
        duration_seconds: float | None = None,
    ) -> list[str]:
        files = [item for item in self.files() if item != path]
        files.insert(0, path)
        files = files[:MAX_RECENT_FILES]
        entries = [entry for entry in self._raw_entries() if entry.get("path") != path]
        entries.insert(
            0,
            {
                "path": path,
                "name": display_name(path),
                "lastOpened": datetime.now(timezone.utc).isoformat(),
                "noteCount": note_count,
                "durationSeconds": duration_seconds,
            },
        )
        entries = entries[:MAX_RECENT_FILES]
        self._persist(files, entries)
        return files

    def remove(self, path: str) -> None:
        files = [item for item in self.files() if item != path]
        entries = [entry for entry in self._raw_entries() if entry.get("path") != path]
        self._persist(files, entries)

    def _raw_entries(self) -> list[dict[str, object]]:
        store = self._store()
        if store is None:
            return list(self._memory_entries)
        return _parse_entries(store.value(_ENTRIES_KEY, ""))

    def _persist(self, files: list[str], entries: list[dict[str, object]]) -> None:
        store = self._store()
        if store is None:
            self._memory = files
            self._memory_entries = entries
            return
        store.setValue(_SETTINGS_KEY, files)
        store.setValue(_ENTRIES_KEY, json.dumps(entries, ensure_ascii=False))
        store.sync()
