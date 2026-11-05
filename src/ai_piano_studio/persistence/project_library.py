from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_json import load_project
from ai_piano_studio.persistence.project_metadata import ProjectMetadataStore
from ai_piano_studio.persistence.atomic import atomic_write_bytes
from ai_piano_studio.persistence.project_recycle import move_project_to_recycle_bin
from ai_piano_studio.persistence.project_naming import (
    inspect_project_name,
    normalize_project_name,
)


PROJECT_FILENAME = "project.pianoai.json"
FRIENDLY_NAMES = ("我的第一段旋律", "星空片段", "晨光旋律", "梦境旋律")


class ProjectLibrary:
    """Small filesystem-backed library for beginner-friendly projects."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (Path.home() / "Documents" / "AI Piano Studio" / "Projects")
        self._metadata = ProjectMetadataStore(self.root / ".project-metadata.json")

    def next_project_path(self) -> Path:
        path = self.allocate_project_path()
        index = int(path.parent.name.removeprefix("project-"))
        self._metadata.set_display_name(path, self._friendly_name(index))
        return path

    def allocate_project_path(self) -> Path:
        """Return an unused stable path without creating library metadata."""
        self.root.mkdir(parents=True, exist_ok=True)
        index = 1
        while (
            (self.root / f"project-{index:03d}").exists()
            or self._metadata.has_project(self.root / f"project-{index:03d}" / PROJECT_FILENAME)
        ):
            index += 1
        return self.root / f"project-{index:03d}" / PROJECT_FILENAME

    def display_name_for(self, path: Path) -> str:
        return self._metadata.display_name_for(path) or path.parent.name

    def set_display_name(self, path: Path, name: str) -> str:
        normalized = normalize_project_name(name)
        result = inspect_project_name(
            normalized,
            self._display_names(),
            current_name=self._metadata.display_name_for(path),
        )
        if not result.valid:
            if result.error == "工程名称已存在":
                raise ValueError("project display name already exists")
            raise ValueError(result.error or "invalid project display name")
        self._metadata.set_display_name(path, result.normalized)
        return result.normalized

    def unique_display_name(self, name: str) -> str:
        """Return a valid display name for generated projects.

        User-entered renames should continue to reject collisions, while
        generated projects (for example, built-in template adaptations) need
        a deterministic suffix when the base name already exists.
        """
        normalized = normalize_project_name(name)
        existing_names = [
            normalize_project_name(display_name)
            for display_name in self.display_names()
        ]
        result = inspect_project_name(normalized, existing_names)
        if result.valid:
            return result.normalized
        if result.suggestion:
            return result.suggestion
        raise ValueError(result.error or "invalid project display name")

    def display_name_exists(
        self, name: str, exclude_path: Path | None = None
    ) -> bool:
        normalized = normalize_project_name(name).casefold()
        return any(
            display_name.casefold() == normalized
            for display_name in self.display_names(exclude_path)
        )

    def display_names(self, exclude_path: Path | None = None) -> list[str]:
        """Return user-facing names without parsing project note JSON."""
        excluded = (
            exclude_path.resolve(strict=False) if exclude_path is not None else None
        )
        return [
            self.display_name_for(path)
            for path in self.root.glob(f"*/{PROJECT_FILENAME}")
            if excluded is None or path.resolve(strict=False) != excluded
        ]

    def _display_names(self) -> list[str]:
        return self.display_names()

    def set_favorite(self, path: Path, favorite: bool) -> None:
        if not self._is_library_path(path):
            raise ValueError("only library projects can be favorites")
        self._metadata.set_favorite(path, favorite)

    def toggle_favorite(self, path: Path) -> bool:
        next_value = not self._metadata.favorite_for(path)
        self.set_favorite(path, next_value)
        return next_value

    def update_summary(self, path: Path, state: ProjectState) -> None:
        """Persist lightweight library metadata after a successful project save."""
        if not self._is_library_path(path):
            return
        self._metadata.set_summary(
            path,
            {
                "noteCount": len(state.playback_notes),
                "durationSeconds": state.playback_duration,
                "bpm": state.playback_tempo.bpm,
                "key": "",
            },
        )

    def recycle(self, path: Path) -> None:
        move_project_to_recycle_bin(path.parent, self.root)

    def copy_project(self, path: Path) -> Path:
        """Duplicate one library project into a new private folder."""
        source = path.resolve(strict=True)
        root = self.root.resolve(strict=True)
        if source.name != PROJECT_FILENAME or source.parent.parent != root:
            raise ValueError("project file is outside the project library")
        target = self.next_project_path()
        shutil.copytree(source.parent, target.parent)
        self._metadata.set_display_name(target, self._copy_name(source))
        self._metadata.set_scene_theme(target, self._metadata.scene_theme_for(source))
        self._metadata.set_favorite(target, False)
        summary = self._metadata.summary_for(source)
        if summary is not None:
            self._metadata.set_summary(target, summary)
        return target

    def copy_metadata(self, source: Path, target: Path) -> None:
        """Copy presentation metadata while leaving identity and favorite state fresh."""
        source = source.resolve(strict=False)
        target = target.resolve(strict=False)
        if not self._is_library_path(source) or not self._is_library_path(target):
            raise ValueError("metadata copy requires library projects")
        self._metadata.set_scene_theme(target, self._metadata.scene_theme_for(source))
        summary = self._metadata.summary_for(source)
        if summary is not None:
            self._metadata.set_summary(target, summary)
        self._metadata.set_favorite(target, False)
        # Preserve the licensed source and AI/unverified label with Save Copy.
        # Only these app-authored bounded sidecars travel with a library project.
        for name in ("music-source.json", "MUSIC-SOURCE.txt"):
            sidecar = source.parent / name
            if sidecar.is_file() and sidecar.stat().st_size <= 64_000:
                atomic_write_bytes(target.parent / name, sidecar.read_bytes())

    def _copy_name(self, source: Path) -> str:
        base = normalize_project_name(f"{self.display_name_for(source)} 副本")
        existing = self.display_names()
        result = inspect_project_name(base, existing_names=existing)
        if result.valid:
            return result.normalized
        if result.suggestion:
            return result.suggestion
        return base or "工程副本"

    @staticmethod
    def _friendly_name(index: int) -> str:
        base = FRIENDLY_NAMES[(index - 1) % len(FRIENDLY_NAMES)]
        cycle = (index - 1) // len(FRIENDLY_NAMES)
        return base if cycle == 0 else f"{base} {cycle + 1}"

    def entries(self, recent_paths: list[str]) -> list[dict[str, object]]:
        library_paths = list(sorted(self.root.glob(f"*/{PROJECT_FILENAME}")))
        library_indexes = {
            path.resolve(strict=False): index
            for index, path in enumerate(library_paths, start=1)
        }
        candidates = list(library_paths)
        candidates.extend(Path(path) for path in recent_paths)
        recent_canonicals = {
            Path(path).resolve(strict=False) for path in recent_paths
        }
        seen: set[Path] = set()
        entries: list[dict[str, object]] = []
        migrated_names: dict[Path, str] = {}
        for path in candidates:
            canonical = path.resolve(strict=False)
            if canonical in seen:
                continue
            seen.add(canonical)
            exists = path.is_file()
            if canonical in library_indexes:
                name = self._metadata.display_name_for(path)
                if not name:
                    name = self._friendly_name(library_indexes[canonical])
                    migrated_names[path] = name
            else:
                name = path.name.removesuffix(".pianoai.json").removesuffix(".json")
            entry: dict[str, object] = {
                "path": str(path),
                "name": name,
                "exists": exists,
                "isLibraryProject": canonical in library_indexes,
                "noteCount": None,
                "durationSeconds": None,
                "bpm": None,
                "key": "",
                "summaryReady": False,
                "lastOpened": "",
                "createdAt": "",
                "sceneTheme": self._metadata.scene_theme_for(path),
                "favorite": self._metadata.favorite_for(path),
            }
            if exists:
                stat = path.stat()
                entry["lastOpened"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
                entry["createdAt"] = datetime.fromtimestamp(stat.st_ctime).isoformat()
                summary = self._metadata.summary_for(path)
                # Only recently opened legacy projects are upgraded on demand;
                # untouched library entries never parse their full note JSON.
                should_upgrade_legacy = canonical in recent_canonicals
                if summary is None and should_upgrade_legacy:
                    try:
                        state = load_project(path)
                        summary = {
                            "noteCount": len(state.playback_notes),
                            "durationSeconds": state.playback_duration,
                            "bpm": state.playback_tempo.bpm,
                            "key": "",
                        }
                        if canonical in library_indexes:
                            self._metadata.set_summary(path, summary)
                    except Exception:
                        entry["exists"] = False
                if summary is not None:
                    entry["noteCount"] = summary.get("noteCount")
                    entry["durationSeconds"] = summary.get("durationSeconds")
                    entry["bpm"] = summary.get("bpm")
                    entry["key"] = summary.get("key", "")
                    entry["summaryReady"] = True
            entries.append(entry)
        self._metadata.set_display_names(migrated_names)
        return entries

    def _is_library_path(self, path: Path) -> bool:
        return self.is_library_path(path)

    def is_library_path(self, path: Path) -> bool:
        try:
            canonical = path.resolve(strict=False)
            root = self.root.resolve(strict=False)
            return canonical.name == PROJECT_FILENAME and canonical.parent.parent == root
        except OSError:
            return False
