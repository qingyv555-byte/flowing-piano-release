"""Creative voices share the existing sink without touching global transport."""
from __future__ import annotations

from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal

from ai_piano_studio.audio.profile import ReleaseMode


class CreativeTransport(QObject):
    finished = Signal()
    failed = Signal(str)

    def __init__(self, source, mixer, studio_busy, *, clock=monotonic, parent=None):
        super().__init__(parent)
        self._source, self._mixer = source, mixer
        self._busy, self._clock = studio_busy, clock
        self._prefix = 'creative:' + uuid4().hex
        self._generation = 0
        self._voices: set[str] = set()
        self._current: set[str] = set()
        self._releases: list[tuple] = []
        self._pending = []
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self.process)
        mixer.voiceFinished.connect(self._voice_finished)

    @property
    def voice_count(self):
        return len(self._voices)

    def start(self, owner, notes):
        if self._busy():
            raise RuntimeError('工作室正在使用音频，请稍后试听')
        if owner not in {'preview', 'companion'} or not 1 <= len(notes) <= 256:
            raise ValueError('invalid creative playback')
        self.release()
        self._generation += 1
        self._pending = list(sorted(notes, key=lambda n: n.start_time))
        self._started = self._clock()
        self._mixer.start_audio(buffer_milliseconds=160)
        self._running = True
        self._timer.start()
        self.process()

    def process(self):
        if not self._running:
            return
        if self._busy():
            self.release()
            self.finished.emit()
            return
        elapsed = self._clock() - self._started
        try:
            while self._pending and self._pending[0].start_time <= elapsed:
                note = self._pending.pop(0)
                # After a suspended event loop, expired notes are dropped.
                if note.start_time + note.duration <= elapsed:
                    continue
                if len(self._voices) >= 512:
                    raise RuntimeError('音频释放未完成，已暂停；请等待设备恢复')
                voice = f'{self._prefix}:{self._generation}:{note.id}'
                self._voices.add(voice)
                self._current.add(voice)
                self._source.start_voice(voice, note.pitch, min(note.velocity, 72))
                self._releases.append((note.start_time + note.duration, voice, note))
            remaining = []
            for end, voice, note in self._releases:
                if end <= elapsed:
                    if note.release_mode == 'custom':
                        self._source.note_off(voice, ReleaseMode.NATURAL, release_seconds=note.release_ms / 1000)
                    else:
                        self._source.note_off(voice, ReleaseMode.STOP if note.release_mode == 'cut' else ReleaseMode.NATURAL)
                else:
                    remaining.append((end, voice, note))
            self._releases = remaining
        except Exception as error:
            self.release()
            self.failed.emit(str(error))
            return
        self._finish_if_done()

    def _voice_finished(self, voice):
        self._voices.discard(voice)
        self._current.discard(voice)
        self._finish_if_done()

    def _finish_if_done(self):
        if self._running and not self._pending and not self._current:
            self._running = False
            self._timer.stop()
            self.finished.emit()

    def release(self):
        self._running = False
        self._timer.stop()
        self._pending.clear()
        self._releases.clear()
        self._current.clear()
        for voice in tuple(self._voices):
            self._source.note_off(voice, ReleaseMode.SAFE)

    def close(self):
        self.release()
        self._mixer.voiceFinished.disconnect(self._voice_finished)
