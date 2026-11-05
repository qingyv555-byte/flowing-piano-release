from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import QObject
from shiboken6 import isValid

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.synth import SAMPLE_RATE


logger = logging.getLogger(__name__)

class RealtimeVoicePool(QObject):
    """Compatibility adapter from integer audition IDs to one PCM mixer."""

    def __init__(
        self,
        mixer: PianoVoiceMixer,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._mixer = mixer
        self._next_voice_id = 1
        self._audio_started = False
        self._disposed = False
        self._owned = set()
        finished = getattr(mixer, 'voiceFinished', None)
        if finished is not None:
            finished.connect(self._owned.discard)

    def start(
        self,
        pitch: int,
        velocity: int,
        source: Sequence[int],
        *,
        source_sample_rate: int = SAMPLE_RATE,
        owner_tag: str = "audition",
    ) -> int:
        try:
            if self._disposed:
                raise RuntimeError("realtime voice pool is disposed")
            self.ensure_audio_started()
            voice_id = self._next_voice_id
            self._next_voice_id += 1
            self._mixer.start_voice(
                self._mixer_voice_id(voice_id),
                pitch,
                velocity,
                source,
                source_sample_rate=source_sample_rate,
                owner_tag=owner_tag,
                pan=0.0,
            )
            self._owned.add(self._mixer_voice_id(voice_id))
            return voice_id
        except Exception:
            logger.exception("Could not start realtime voice")
            raise

    def ensure_audio_started(self) -> None:
        try:
            if self._disposed:
                raise RuntimeError("realtime voice pool is disposed")
            ready = getattr(self._mixer, 'is_audio_ready', None)
            if not self._audio_started or (callable(ready) and not ready()):
                self._mixer.start_audio()
                self._audio_started = True
        except Exception:
            logger.exception("Could not start realtime audio sink")
            raise

    def release(
        self, voice_id: int, mode: ReleaseMode = ReleaseMode.NATURAL
    ) -> None:
        try:
            self._mixer.note_off(self._mixer_voice_id(voice_id), mode)
        except Exception:
            logger.exception("Could not release realtime voice %r", voice_id)
            raise

    def release_all(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        try:
            for voice_id in tuple(self._owned):
                self._mixer.note_off(voice_id, mode)
        except Exception:
            logger.exception("Could not release all realtime voices")
            raise

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        # A shared engine-owned mixer can be destroyed before its player.
        # Qt already disposed its sink and IO device in that order.
        if isinstance(self._mixer, QObject) and not isValid(self._mixer):
            return
        try:
            if self._audio_started:
                self._mixer.release_all_and_wait_for_stop()
            else:
                self._mixer.release_all(ReleaseMode.STOP)
        except Exception:
            logger.exception("Could not drain realtime voices")
        try:
            self._mixer.stop_audio()
        except Exception:
            logger.exception("Could not dispose realtime audio sink")

    @staticmethod
    def _mixer_voice_id(voice_id: int) -> str:
        if isinstance(voice_id, bool) or not isinstance(voice_id, int) or voice_id <= 0:
            raise ValueError("voice_id must be a positive integer")
        return f"audition:{voice_id}"
