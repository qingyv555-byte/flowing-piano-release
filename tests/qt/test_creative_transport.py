from PySide6.QtCore import QObject, Signal
from ai_piano_studio.creative.transport import CreativeTransport
from ai_piano_studio.domain.note import Note
from ai_piano_studio.audio.profile import ReleaseMode


class Mixer(QObject):
    voiceFinished = Signal(str)
    def start_audio(self, **kwargs):
        pass


class Source:
    def __init__(self):
        self.started, self.released = [], []
    def start_voice(self, *args):
        self.started.append(args)
    def note_off(self, *args):
        self.released.append(args)


def test_transport_releases_only_its_voices_and_stops_pending_notes(qapp):
    now, busy = [0.0], [False]
    source, mixer = Source(), Mixer()
    transport = CreativeTransport(source, mixer, lambda: busy[0], clock=lambda: now[0])
    notes = (Note.create(pitch=60, start_time=0, duration=1), Note.create(pitch=64, start_time=2, duration=1))
    transport.start('companion', notes)
    transport.process()
    assert len(source.started) == 1
    voice = source.started[0][0]
    assert voice.startswith('creative:')
    busy[0] = True
    now[0] = 3
    transport.process()
    assert source.released == [(voice, ReleaseMode.SAFE)]
    assert len(source.started) == 1
    mixer.voiceFinished.emit(voice)
    assert transport.voice_count == 0
    transport.close()


def test_missing_finished_callbacks_are_bounded(qapp):
    source, mixer = Source(), Mixer()
    transport = CreativeTransport(source, mixer, lambda: False, clock=lambda: 0)
    failures = []
    transport.failed.connect(failures.append)
    notes = tuple(Note.create(pitch=60, start_time=0, duration=.2) for _ in range(256))
    transport.start('preview', notes)
    transport.start('preview', notes)
    transport.start('preview', notes)
    assert transport.voice_count == 512
    assert len(source.started) == 512
    assert failures and not transport._timer.isActive()
    transport.close()


def test_completed_preview_notifies_and_late_voice_cannot_finish_new_preview(qapp):
    now, source, mixer = [0.0], Source(), Mixer()
    transport = CreativeTransport(source, mixer, lambda: False, clock=lambda: now[0])
    done = []
    transport.finished.connect(lambda: done.append(True))
    notes = (Note.create(pitch=60, start_time=0, duration=.2),)
    transport.start('preview', notes)
    old = source.started[0][0]
    transport.release()
    transport.start('preview', notes)
    new = source.started[-1][0]
    mixer.voiceFinished.emit(old)
    assert not done
    now[0] = .3
    transport.process()
    mixer.voiceFinished.emit(new)
    assert done == [True]
    transport.close()
