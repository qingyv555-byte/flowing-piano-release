from pathlib import Path

from ai_piano_studio.persistence.project_metadata import ProjectMetadataStore


def test_display_name_is_separate_from_project_folder(tmp_path) -> None:
    project = tmp_path / "internal-project-001" / "project.pianoai.json"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    store = ProjectMetadataStore(tmp_path / "metadata.json")

    store.set_display_name(project, "星空片段")

    assert project.parent.name == "internal-project-001"
    assert store.display_name_for(project) == "星空片段"


def test_metadata_tracks_scene_theme_without_changing_project_file(tmp_path) -> None:
    project = tmp_path / "project-001" / "project.pianoai.json"
    project.parent.mkdir()
    project.write_text('{"schemaVersion": 1}', encoding="utf-8")
    store = ProjectMetadataStore(tmp_path / "metadata.json")

    store.set_scene_theme(project, "cosmic-pulse")

    assert store.scene_theme_for(project) == "cosmic-pulse"
    assert project.read_text(encoding="utf-8") == '{"schemaVersion": 1}'
