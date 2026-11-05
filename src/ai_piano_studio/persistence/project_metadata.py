from __future__ import annotations

import json
from pathlib import Path

from ai_piano_studio.persistence.atomic import atomic_write_bytes


class ProjectMetadataStore:
    """Keeps user-facing project metadata outside project JSON files."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries_cache: dict[str, dict[str, object]] | None = None

    def display_name_for(self, project_path: Path) -> str | None:
        return self._entries().get(self._key(project_path), {}).get("displayName")

    def has_project(self, project_path: Path) -> bool:
        """Keep identities reserved even while their folders are recycled."""
        return self._key(project_path) in self._entries()

    def summary_for(self, project_path: Path) -> dict[str, object] | None:
        summary = self._entries().get(self._key(project_path), {}).get("summary")
        return dict(summary) if isinstance(summary, dict) else None

    def set_summary(self, project_path: Path, summary: dict[str, object]) -> None:
        cleaned = {
            "noteCount": max(0, int(summary.get("noteCount", 0))),
            "durationSeconds": max(0.0, float(summary.get("durationSeconds", 0.0))),
            "bpm": max(1, int(summary.get("bpm", 120))),
            "key": str(summary.get("key", ""))[:64],
        }
        entries = self._entries()
        entry = entries.setdefault(self._key(project_path), {})
        entry["summary"] = cleaned
        self._write(entries)

    def set_display_name(self, project_path: Path, name: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("display name cannot be empty")
        self.set_display_names({project_path: cleaned})

    def set_display_names(self, names: dict[Path, str]) -> None:
        """Persist several migrated display names with one metadata write."""
        cleaned_names = {
            path: name.strip()
            for path, name in names.items()
        }
        if any(not name for name in cleaned_names.values()):
            raise ValueError("display name cannot be empty")
        entries = self._entries()
        for project_path, name in cleaned_names.items():
            entry = entries.setdefault(self._key(project_path), {})
            entry["displayName"] = name
        if cleaned_names:
            self._write(entries)

    def scene_theme_for(self, project_path: Path) -> str:
        return self._entries().get(self._key(project_path), {}).get("sceneTheme", "dream-meadow")

    def favorite_for(self, project_path: Path) -> bool:
        return bool(self._entries().get(self._key(project_path), {}).get("favorite", False))

    def set_favorite(self, project_path: Path, favorite: bool) -> None:
        entries = self._entries()
        entry = entries.setdefault(self._key(project_path), {})
        entry["favorite"] = bool(favorite)
        self._write(entries)

    def set_scene_theme(self, project_path: Path, scene_theme: str) -> None:
        if scene_theme not in {"dream-meadow", "cosmic-pulse", "sunset-haven"}:
            raise ValueError("invalid scene theme")
        entries = self._entries()
        entry = entries.setdefault(self._key(project_path), {})
        entry["sceneTheme"] = scene_theme
        self._write(entries)

    def _entries(self) -> dict[str, dict[str, object]]:
        if self._entries_cache is not None:
            return self._entries_cache
        if not self._path.is_file():
            self._entries_cache = {}
            return self._entries_cache
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._entries_cache = {}
            return self._entries_cache
        self._entries_cache = data if isinstance(data, dict) else {}
        return self._entries_cache

    def _write(self, entries: dict[str, dict[str, object]]) -> None:
        self._entries_cache = entries
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write_bytes(self._path, payload)

    @staticmethod
    def _key(project_path: Path) -> str:
        return str(project_path.resolve(strict=False))
