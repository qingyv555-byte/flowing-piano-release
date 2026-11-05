from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.persistence.save_preferences import SavePreferences


def _store(tmp_path):
    return QSettings(str(tmp_path / "save.ini"), QSettings.Format.IniFormat)


def test_save_preferences_default_to_disabled_and_sixty_seconds(tmp_path) -> None:
    preferences = SavePreferences(settings=_store(tmp_path))
    assert preferences.autosaveEnabled is False
    assert preferences.autosaveDelaySeconds == 60


def test_save_preferences_persist_valid_values_and_emit_once_per_change(tmp_path) -> None:
    store = _store(tmp_path)
    preferences = SavePreferences(settings=store)
    changed = QSignalSpy(preferences.changed)
    preferences.setAutosaveEnabled(True)
    preferences.setAutosaveDelaySeconds(120)
    preferences.setAutosaveDelaySeconds(120)
    restored = SavePreferences(settings=_store(tmp_path))
    assert (restored.autosaveEnabled, restored.autosaveDelaySeconds) == (True, 120)
    assert changed.count() == 2


def test_invalid_stored_or_requested_delay_normalizes_to_sixty_seconds(tmp_path) -> None:
    store = _store(tmp_path)
    store.setValue("save/autosaveDelaySeconds", "broken")
    preferences = SavePreferences(settings=store)
    assert preferences.autosaveDelaySeconds == 60
    assert store.value("save/autosaveDelaySeconds", type=int) == 60
    preferences.setAutosaveDelaySeconds(17)
    assert preferences.autosaveDelaySeconds == 60


def test_stored_fractional_delay_normalizes_and_is_written_back(tmp_path) -> None:
    store = _store(tmp_path)
    store.setValue("save/autosaveDelaySeconds", 60.5)
    preferences = SavePreferences(settings=store)
    assert preferences.autosaveDelaySeconds == 60
    assert store.value("save/autosaveDelaySeconds") == 60


def test_fractional_requested_delay_is_not_accepted_as_a_valid_delay(tmp_path) -> None:
    preferences = SavePreferences(settings=_store(tmp_path))
    preferences.setAutosaveDelaySeconds(120.9)
    assert preferences.autosaveDelaySeconds == 60
