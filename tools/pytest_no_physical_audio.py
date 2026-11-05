"""Opt-in pytest guard: fail if a test tries to start physical audio.

Use ``pytest -p tools.pytest_no_physical_audio -k 'not real_audio_sink'``
for the explicitly device-free regression subset. Mock sinks remain usable.
"""
import pytest
from PySide6.QtMultimedia import QAudioSink, QAudioSource, QMediaPlayer, QSoundEffect


@pytest.fixture(autouse=True)
def forbid_physical_audio(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Physical audio start forbidden in device-free validation')
    monkeypatch.setattr(QAudioSink, 'start', forbidden)
    monkeypatch.setattr(QAudioSource, 'start', forbidden)
    monkeypatch.setattr(QMediaPlayer, 'play', forbidden)
    monkeypatch.setattr(QSoundEffect, 'play', forbidden)
