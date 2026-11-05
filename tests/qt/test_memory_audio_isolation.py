"""The transport reset path must never reopen the physical audio device."""
from array import array

import pytest
from ai_piano_studio.audio import mixer as mixer_module
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from tools.studio_evidence import install_memory_output


def populate(mixer):
    for identity in ("project:voice", "audition:voice"):
        mixer.start_voice(identity, 60, 80, array("h", [1000] * 4800),
                          source_sample_rate=48000, owner_tag="test")
    assert any(mixer._read_output_frames(8))


def test_internal_reset_does_not_open_output_that_was_never_started(qapp, monkeypatch):
    calls = []
    def reject(*args, **kwargs):
        calls.append("attempt")
        raise AssertionError("Blocked before physical device construction")
    monkeypatch.setattr(mixer_module, "QAudioSink", reject)
    monkeypatch.setattr(PianoVoiceMixer, "start_audio", lambda self, **kw: None)
    mixer = PianoVoiceMixer()
    populate(mixer)
    # The old diagnostic expected a device-start attempt. The production
    # had_output guard now forbids that regression even with another live voice.
    mixer.discard_voice_ids_and_reset_output(("project:voice",))
    assert calls == []
    assert mixer._sink is None
    assert mixer.active_voice_ids == ("audition:voice",)


def test_memory_output_keeps_remaining_voice_without_opening_device(qapp, monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    mixer = PianoVoiceMixer()
    mixer.start_audio()
    populate(mixer)
    mixer.discard_voice_ids_and_reset_output(("project:voice",))
    assert mixer._sink is None and mixer.is_audio_ready()
    assert mixer.active_voice_ids == ("audition:voice",)
    assert any(mixer._read_output_frames(8))
    mixer.pause_audio()
    mixer.resume_audio()
    assert mixer._sink is None and not blocked


def test_memory_output_ignores_physical_device_rebinding(qapp, monkeypatch):
    from ai_piano_studio.audio.player import AudioPlayer
    blocked = install_memory_output(monkeypatch.setattr)
    player = AudioPlayer()
    player._on_audio_outputs_changed()
    player._bind_default_output(preserve_playback=True)
    assert not blocked
    player.deleteLater()
    qapp.processEvents()
