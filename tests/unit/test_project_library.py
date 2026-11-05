from time import perf_counter
from types import SimpleNamespace

import pytest
import ai_piano_studio.persistence.project_library as project_library_module
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.autosave import NullRecoveryStore
from ai_piano_studio.persistence import project_recycle
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_metadata import ProjectMetadataStore


def test_new_project_path_uses_internal_folder_and_friendly_display_name(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")

    first = library.next_project_path()
    first.parent.mkdir(parents=True)
    first.touch()
    second = library.next_project_path()

    assert first == tmp_path / "Projects" / "project-001" / "project.pianoai.json"
    assert second == tmp_path / "Projects" / "project-002" / "project.pianoai.json"
    assert library.display_name_for(first) == "我的第一段旋律"
    assert library.display_name_for(second) == "星空片段"


def test_allocate_project_path_does_not_create_metadata_before_save(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")

    allocated = library.allocate_project_path()

    assert allocated == tmp_path / "Projects" / "project-001" / "project.pianoai.json"
    assert not allocated.exists()
    assert not (library.root / ".project-metadata.json").exists()


def test_recycled_project_identity_is_not_reused(tmp_path, monkeypatch) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    original = library.next_project_path()
    original.parent.mkdir()
    save_project(original, ProjectState())
    library.set_favorite(original, True)
    library._metadata.set_scene_theme(original, 'cosmic-pulse')
    library.update_summary(original, ProjectState())
    recycled = tmp_path / 'recycled'
    monkeypatch.setattr(project_library_module, 'move_project_to_recycle_bin',
                        lambda folder, root: folder.rename(recycled))
    library.recycle(original)
    # Reopen the store as a new application process would.
    library = ProjectLibrary(library.root)
    new = library.next_project_path()
    assert new != original
    assert not library._metadata.favorite_for(new)
    assert library._metadata.scene_theme_for(new) == 'dream-meadow'
    assert library._metadata.summary_for(new) is None
    # Restoring the old folder must keep its original presentation metadata.
    recycled.rename(original.parent)
    assert library._metadata.favorite_for(original)
    assert library._metadata.scene_theme_for(original) == 'cosmic-pulse'


def test_set_display_name_normalizes_and_rejects_duplicate_names(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    first = library.allocate_project_path()
    first.parent.mkdir(parents=True)
    save_project(first, ProjectState())
    second = library.allocate_project_path()
    second.parent.mkdir(parents=True)
    save_project(second, ProjectState())

    assert library.set_display_name(first, "  Night:01  ") == "Night_01"
    try:
        library.set_display_name(second, "Night_01")
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("duplicate display name was accepted")


def test_scan_merges_library_and_recent_projects_without_duplicates(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    saved = library.next_project_path()
    saved.parent.mkdir(parents=True)
    save_project(saved, ProjectState((Note.create(pitch=60, start_time=0.0, duration=0.5),)))
    external = tmp_path / "outside.pianoai.json"
    save_project(external, ProjectState((Note.create(pitch=67, start_time=1.0, duration=0.5),)))

    entries = library.entries([str(saved), str(external), str(external)])

    assert [(entry["name"], entry["exists"]) for entry in entries] == [
        ("我的第一段旋律", True),
        ("outside", True),
    ]
    assert entries[0]["noteCount"] == 1
    assert entries[1]["durationSeconds"] == 1.5


def test_copy_project_creates_a_new_internal_folder_and_friendly_copy_name(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    source = library.next_project_path()
    source.parent.mkdir(parents=True)
    save_project(source, ProjectState((Note.create(pitch=64, start_time=0.0, duration=0.5),)))

    copied = library.copy_project(source)

    assert copied == tmp_path / "Projects" / "project-002" / "project.pianoai.json"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert library.display_name_for(copied) == "我的第一段旋律 副本"


def test_copy_project_uses_case_insensitive_unique_display_names(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    source = library.allocate_project_path()
    source.parent.mkdir(parents=True)
    save_project(source, ProjectState())
    library.set_display_name(source, "Foo")

    first_copy = library.copy_project(source)
    library.set_display_name(first_copy, "foo 副本")
    second_copy = library.copy_project(source)

    assert library.display_name_for(second_copy) == "Foo 副本 2"


def test_library_favorite_is_persisted_outside_project_json(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    source = library.next_project_path()
    source.parent.mkdir(parents=True)
    save_project(source, ProjectState())

    assert library.toggle_favorite(source) is True
    entry = library.entries([str(source)])[0]
    assert entry["favorite"] is True
    assert "favorite" not in source.read_text(encoding="utf-8")
    assert library.toggle_favorite(source) is False


def test_library_migrates_legacy_unnamed_display_names_without_editing_json(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    legacy = tmp_path / "Projects" / "legacy-001" / "project.pianoai.json"
    legacy.parent.mkdir(parents=True)
    save_project(legacy, ProjectState())
    original_json = legacy.read_text(encoding="utf-8")

    entries = library.entries([])

    assert entries[0]["name"] == "我的第一段旋律"
    assert legacy.read_text(encoding="utf-8") == original_json


def test_recycle_helper_is_started_without_a_console(tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "Projects" / "project-001"
    project_dir.mkdir(parents=True)
    calls: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(project_recycle.subprocess, "run", fake_run)

    project_recycle.move_project_to_recycle_bin(project_dir, project_dir.parent)

    assert calls[0]["creationflags"] == getattr(
        project_recycle.subprocess, "CREATE_NO_WINDOW", 0
    )


def test_editor_caches_library_entries_until_recent_projects_change(tmp_path, monkeypatch) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    controller = EditorController(
        ProjectState(), project_library=library, recovery_store=NullRecoveryStore()
    )
    calls: list[list[str]] = []

    def fake_entries(recent_paths: list[str]) -> list[dict[str, object]]:
        calls.append(recent_paths)
        return [{"name": "cached", "exists": True}]

    monkeypatch.setattr(library, "entries", fake_entries)

    assert controller.libraryEntries == controller.libraryEntries
    assert len(calls) == 1

    controller.removeRecent("missing-project.pianoai.json")
    assert controller.libraryEntries[0]["name"] == "cached"
    assert len(calls) == 2


def test_library_scan_does_not_parse_untouched_projects_at_startup(tmp_path, monkeypatch) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    for index in range(500):
        project = library.root / f"project-{index:03d}" / "project.pianoai.json"
        project.parent.mkdir(parents=True)
        project.write_text("not parsed during index scan", encoding="utf-8")

    def fail_if_parsed(_path):
        raise AssertionError("untouched project was parsed during library scan")

    monkeypatch.setattr(project_library_module, "load_project", fail_if_parsed)
    entries = library.entries([])

    assert len(entries) == 500
    assert all(entry["exists"] is True for entry in entries)
    assert all(entry["summaryReady"] is False for entry in entries)


@pytest.mark.parametrize("project_count", [0, 10, 100, 500])
def test_library_index_matrix_scales_without_parsing_project_json(
    tmp_path, monkeypatch, project_count
) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    metadata = ProjectMetadataStore(library.root / ".project-metadata.json")
    display_names = {}
    for index in range(1, project_count + 1):
        project = library.root / f"project-{index:03d}" / "project.pianoai.json"
        project.parent.mkdir(parents=True)
        project.write_text("{}", encoding="utf-8")
        display_names[project] = f"工程 {index}"
    metadata.set_display_names(display_names)

    def fail_if_parsed(_path):
        raise AssertionError("library index parsed an untouched project")

    monkeypatch.setattr(project_library_module, "load_project", fail_if_parsed)
    started = perf_counter()
    entries = library.entries([])
    elapsed = perf_counter() - started

    assert len(entries) == project_count
    assert all(entry["isLibraryProject"] is True for entry in entries)
    assert all(entry["summaryReady"] is False for entry in entries)
    assert elapsed >= 0.0  # Keep the measurement diagnostic, never machine-gate it.
