from PySide6.QtCore import QSettings

from ai_piano_studio.harmony.settings import HarmonySettings


def test_harmony_mode_defaults_to_off_and_persists(tmp_path, qtbot):
    store = QSettings(str(tmp_path / "harmony.ini"), QSettings.IniFormat)
    settings = HarmonySettings(settings=store)

    assert settings.mode == "off"

    settings.setMode("preview")

    assert HarmonySettings(
        settings=QSettings(store.fileName(), QSettings.IniFormat)
    ).mode == "preview"


def test_unknown_harmony_mode_logs_and_falls_back(tmp_path, caplog):
    store = QSettings(str(tmp_path / "harmony.ini"), QSettings.IniFormat)
    store.setValue("harmony/mode", "broken")

    assert HarmonySettings(settings=store).mode == "off"
    assert "Invalid harmony mode" in caplog.text


def test_non_string_harmony_mode_logs_and_falls_back(tmp_path, caplog):
    store = QSettings(str(tmp_path / "harmony.ini"), QSettings.IniFormat)
    store.setValue("harmony/mode", ["broken"])

    assert HarmonySettings(settings=store).mode == "off"
    assert "Invalid harmony mode" in caplog.text


def test_setting_an_unknown_harmony_mode_logs_and_falls_back(tmp_path, caplog):
    store = QSettings(str(tmp_path / "harmony.ini"), QSettings.IniFormat)
    settings = HarmonySettings(settings=store)

    settings.setMode("broken")

    assert settings.mode == "off"
    assert store.value("harmony/mode") == "off"
    assert "Invalid harmony mode" in caplog.text
