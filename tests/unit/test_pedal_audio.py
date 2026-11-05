from array import array
import pytest
from ai_piano_studio.domain.control_event import ControlEvent, sustain_snapshot
from ai_piano_studio.domain.note import Note
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.synth import render_project
from ai_piano_studio.keyboard.metronome import MonitorClick
from tests.unit.test_project_session import FakeClock, RecordingSource, RecordingMixer


def test_pedal_release_and_seek_restore_without_note_attack(qapp):
    clock,source,mixer=FakeClock(),RecordingSource(),RecordingMixer()
    ages=[]
    mixer.seek_voice=lambda voice,age:ages.append((voice,age))
    n=Note.create(pitch=60,start_time=0,duration=.5)
    events=(ControlEvent.create(time=.1,value=127),ControlEvent.create(time=2,value=0))
    session=RealtimePlaybackSession(sustain_snapshot((n,),events),source,mixer,clock=clock)
    session.seek(1)
    assert ages[0][1]==1
    assert session._pending_releases[0][0]==2
    session.stop(__import__('ai_piano_studio.audio.profile',fromlist=['ReleaseMode']).ReleaseMode.STOP)


def test_pedal_wav_outlives_key_duration_without_mutating_note():
    n=Note.create(pitch=60,start_time=0,duration=.5)
    notes=sustain_snapshot((n,),(ControlEvent.create(time=.1,value=127),ControlEvent.create(time=2,value=0)))
    dry=render_project((n,),sample_rate=8000)
    held=render_project(notes,sample_rate=8000)
    assert len(held)>len(dry)+8000
    assert any(held[12000:15000])
    assert notes[0].duration==n.duration==.5


def test_metronome_is_excluded_from_analysis_bus_and_retires(qapp):
    mixer=PianoVoiceMixer(sample_rate=44100)
    click=MonitorClick(mixer)
    click.tick(True,.5)
    pcm=mixer.mix_frames(4096)
    assert any(pcm)
    assert not any(mixer._analysis_payload)
    assert mixer.voice_statistics()['active']==0


def test_voice_steal_uses_bounded_fade_and_observable_counter(qapp):
    mixer=PianoVoiceMixer(sample_rate=1000,max_voices=1)
    mixer.start_voice('old',60,127,(10000,)*200,source_sample_rate=1000,owner_tag='test')
    mixer.mix_frames(20)
    mixer.start_voice('silent-new',60,0,(0,)*200,source_sample_rate=1000,owner_tag='test')
    first=array('h');first.frombytes(mixer.mix_frames(12))
    assert first[0]!=0 and first[-1]==0
    assert mixer.voice_statistics()=={'active':1,'peak':1,'steals':1,'limit':1,'stealFadeMs':12}


def test_stolen_metronome_tail_stays_out_of_analysis(qapp):
    mixer = PianoVoiceMixer(sample_rate=1000, max_voices=1)
    mixer.start_voice('click', 60, 127, (10000,) * 200, source_sample_rate=1000, owner_tag='monitor-only')
    mixer.mix_frames(20)
    mixer.start_voice('silent-note', 60, 0, (0,) * 200, source_sample_rate=1000, owner_tag='test')
    assert any(mixer.mix_frames(12))
    assert not any(mixer._analysis_payload)


@pytest.mark.parametrize('pitch', [0, 127])
def test_offline_sample_export_preserves_midi_pitches_outside_piano_bank(pitch, tmp_path):
    from math import sqrt
    from ai_piano_studio.audio.sample_piano import SamplePianoEngine
    from ai_piano_studio.audio.synth import render_note
    from tests.unit.test_sample_piano import _manifest
    engine = SamplePianoEngine(_manifest(tmp_path), preload_c3_c5_on_init=False)
    expected = render_note(pitch, .1, 80, 8000)
    actual = engine.render_note(pitch, .1, 80, 8000)
    if engine.legacy_selected:
        assert actual == expected
    else:
        assert actual == array('h', (int(sample / sqrt(2)) for sample in expected for _ in range(2)))
