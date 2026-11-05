from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy

from ai_piano_studio.audio.tone_settings import PianoToneSettings


def ini_settings(tmp_path, values=None) -> QSettings:
    settings = QSettings(str(tmp_path / "tone.ini"), QSettings.Format.IniFormat)
    for key, value in (values or {}).items():
        settings.setValue(key, value)
    settings.sync()
    return settings


def restored_ini_value(tmp_path) -> float:
    settings = QSettings(str(tmp_path / "tone.ini"), QSettings.Format.IniFormat)
    return float(settings.value("audio/pianoTone"))


def test_tone_settings_clamps_persists_and_restores_qsettings(tmp_path):
    """Fails if malformed persisted input is not defaulted or a committed value is not saved."""
    tone = PianoToneSettings(settings=ini_settings(tmp_path, {"audio/pianoTone": "bad"}))
    assert tone.value == .5
    tone.setValue(1.7)
    assert restored_ini_value(tmp_path) == 1.0


def test_tone_settings_emits_one_change_only_for_a_normalized_commit(tmp_path):
    """Fails if a no-op setting update emits duplicate UI changes."""
    tone = PianoToneSettings(settings=ini_settings(tmp_path, {"audio/pianoTone": .5}))
    changed = QSignalSpy(tone.changed)

    tone.setValue(.5)
    tone.setValue(-1.0)

    assert tone.value == 0.0
    assert changed.count() == 1


def test_audition_preview_does_not_commit_preference_and_restores_selection(tmp_path):
    from types import SimpleNamespace

    class Coordinator:
        position = .5

        def available_interval(self):
            return (0.0, 1.0)

        def status_message(self):
            return "Sample Piano"

        def commit(self, value):
            self.position = value
            return SimpleNamespace(position=value)

    coordinator = Coordinator()
    tone = PianoToneSettings(settings=ini_settings(tmp_path, {"audio/pianoTone": .5}),
                             coordinator=coordinator)
    changed = QSignalSpy(tone.changed)
    assert tone.previewValue(.9)
    assert coordinator.position == .9
    assert tone.value == restored_ini_value(tmp_path) == .5
    tone.restorePreview()
    assert coordinator.position == .5
    assert changed.count() == 0
    assert not tone.previewValue(float('nan'))
    assert coordinator.position == .5


def test_runtime_total_failure_notifies_available_and_status(qtbot, tmp_path):
    """Fails if a late automatic Legacy fallback never reaches the QML bridge."""
    class Coordinator:
        available = True

        def available_interval(self):
            return (0.0, 1.0) if self.available else None

        def status_message(self):
            return "Sample Piano" if self.available else "Legacy Piano"

        def commit(self, _value):
            return None

    coordinator = Coordinator()
    tone = PianoToneSettings(
        settings=ini_settings(tmp_path, {"audio/pianoTone": .5}),
        coordinator=coordinator,
        availability_poll_ms=10,
    )
    changed = QSignalSpy(tone.availabilityChanged)

    coordinator.available = False

    qtbot.waitUntil(lambda: not tone.available, timeout=500)
    assert tone.statusMessage == "Legacy Piano"
    assert changed.count() == 1
