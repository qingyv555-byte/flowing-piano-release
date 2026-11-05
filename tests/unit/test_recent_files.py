from PySide6.QtCore import QSettings

from ai_piano_studio.persistence.recent_files import MAX_RECENT_FILES, RecentFiles


def test_memory_mode_remembers_most_recent_first() -> None:
    recent = RecentFiles()
    recent.remember("a.pianoai.json")
    recent.remember("b.pianoai.json")
    assert recent.files() == ["b.pianoai.json", "a.pianoai.json"]


def test_remember_deduplicates_and_promotes_existing_entry() -> None:
    recent = RecentFiles()
    recent.remember("a.pianoai.json")
    recent.remember("b.pianoai.json")
    recent.remember("a.pianoai.json")
    assert recent.files() == ["a.pianoai.json", "b.pianoai.json"]


def test_remember_truncates_to_max_entries() -> None:
    recent = RecentFiles()
    for index in range(MAX_RECENT_FILES + 3):
        recent.remember(f"project_{index}.pianoai.json")
    files = recent.files()
    assert len(files) == MAX_RECENT_FILES
    assert files[0] == f"project_{MAX_RECENT_FILES + 2}.pianoai.json"


def test_files_returns_a_copy_of_memory_storage() -> None:
    recent = RecentFiles()
    recent.remember("a.pianoai.json")
    snapshot = recent.files()
    snapshot.append("mutated.pianoai.json")
    assert recent.files() == ["a.pianoai.json"]


def test_explicit_settings_persist_across_instances(tmp_path) -> None:
    settings_path = tmp_path / "recent.ini"
    first = RecentFiles(QSettings(str(settings_path), QSettings.IniFormat))
    first.remember("a.pianoai.json")
    first.remember("b.pianoai.json")

    second = RecentFiles(QSettings(str(settings_path), QSettings.IniFormat))
    assert second.files() == ["b.pianoai.json", "a.pianoai.json"]


def test_invalid_entries_are_filtered(tmp_path) -> None:
    settings_path = tmp_path / "recent.ini"
    settings = QSettings(str(settings_path), QSettings.IniFormat)
    settings.setValue("recentFiles", ["a.pianoai.json", "", 42, None, "b.pianoai.json"])
    settings.sync()

    recent = RecentFiles(settings)
    assert recent.files() == ["a.pianoai.json", "b.pianoai.json"]


def test_entries_carry_display_metadata(tmp_path) -> None:
    target = tmp_path / "my_song.pianoai.json"
    target.write_text("{}", encoding="utf-8")
    recent = RecentFiles()
    recent.remember(str(target), note_count=12, duration_seconds=34.5)

    entries = recent.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["path"] == str(target)
    assert entry["name"] == "my_song"
    assert entry["noteCount"] == 12
    assert entry["durationSeconds"] == 34.5
    assert entry["lastOpened"]
    assert entry["exists"] is True


def test_entries_flag_missing_files() -> None:
    recent = RecentFiles()
    recent.remember("gone.pianoai.json", note_count=3)
    entry = recent.entries()[0]
    assert entry["exists"] is False
    assert entry["name"] == "gone"


def test_entries_without_metadata_fall_back_to_defaults() -> None:
    recent = RecentFiles()
    recent.remember("plain.pianoai.json")
    entry = recent.entries()[0]
    assert entry["name"] == "plain"
    assert entry["noteCount"] is None
    assert entry["durationSeconds"] is None


def test_remove_drops_path_and_metadata() -> None:
    recent = RecentFiles()
    recent.remember("a.pianoai.json", note_count=1)
    recent.remember("b.pianoai.json", note_count=2)
    recent.remove("a.pianoai.json")
    assert recent.files() == ["b.pianoai.json"]
    assert [entry["path"] for entry in recent.entries()] == ["b.pianoai.json"]


def test_entries_persist_across_instances(tmp_path) -> None:
    settings_path = tmp_path / "recent.ini"
    first = RecentFiles(QSettings(str(settings_path), QSettings.IniFormat))
    first.remember("song.pianoai.json", note_count=7, duration_seconds=12.0)

    second = RecentFiles(QSettings(str(settings_path), QSettings.IniFormat))
    entries = second.entries()
    assert len(entries) == 1
    assert entries[0]["noteCount"] == 7
    assert entries[0]["name"] == "song"
