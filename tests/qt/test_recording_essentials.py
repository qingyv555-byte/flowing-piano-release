import pytest
from tests.qt.test_keyboard import make_keyboard
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from types import SimpleNamespace


def test_pedal_records_key_off_and_separate_control_events(qapp):
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    keyboard.pressKey(ord('A'))
    clock.advance(.1)
    keyboard.setPedal(True)
    clock.advance(.4)
    keyboard.releaseKey(ord('A'))
    assert not playback.released_voices
    keyboard._tick()
    assert editor.state.notes[0].duration == pytest.approx(.5)
    clock.advance(1.5)
    keyboard.setPedal(False)
    keyboard.stopRecording()
    assert [e.value for e in editor.state.controls] == [127,0]
    assert [e.time for e in editor.state.controls] == pytest.approx([.1,2])
    assert playback.released_voices == [(1,'natural')]
    editor.undo()
    assert not editor.state.notes and not editor.state.controls


def test_countin_held_key_enters_without_second_attack_and_cancel_preserves_old(qapp):
    old = Note.create(pitch=48,start_time=0,duration=3)
    editor, playback, clock, keyboard = make_keyboard((old,))
    values = keyboard.preferences.snapshot(); values['countInBars']=1
    keyboard.preferences.apply(values)
    keyboard.pressKey(ord('A'))
    keyboard.startRecording()
    clock.advance(1)
    keyboard._tick()
    assert editor.state.notes == (old,)
    clock.advance(1)
    keyboard._tick()
    assert len(editor.state.notes) == 2
    assert len(playback.started_voices) == 1
    assert editor.state.notes[-1].start_time == 0
    clock.advance(.5)
    keyboard.releaseKey(ord('A'))
    keyboard.cancelRecording()
    assert editor.state.notes == (old,)
    assert not editor.recordingTakeActive


def test_target_layer_frozen_and_edit_gestures_cannot_commit_take(qapp):
    editor, playback, clock, keyboard = make_keyboard()
    other=MusicLayer.create(id='other',name='Other',order=1)
    editor._execute(editor.state.add_layer(other))
    original=editor.activeLayerId
    keyboard.startRecording()
    editor.setActiveLayer('other')
    editor.beginGesture()
    editor.commitGesture()
    editor.undo()
    keyboard.pressKey(ord('A'))
    clock.advance(.5)
    keyboard.stopRecording()
    assert len(editor.state.notes)==1
    assert editor.state.notes[0].layer_id==original


def test_recording_uses_transport_position_rate_and_boundary_save(qapp, tmp_path):
    editor, playback, clock, keyboard = make_keyboard()
    playback.positionSeconds=4.0; playback.playbackRate=1.5
    keyboard.startRecording(); keyboard.pressKey(ord('A')); clock.advance(.5)
    editor.exportMidi(str(tmp_path/'take.mid'))
    assert not keyboard.recording
    assert editor.state.notes[0].start_time==4
    assert editor.state.notes[0].duration==pytest.approx(.75)
    assert (tmp_path/'take.mid').exists()


@pytest.mark.parametrize('bars,signature,rate',[(0,(4,4),1),(1,(3,4),1),(2,(6,8),1.5)])
def test_audio_ready_anchors_countin_and_boundary_pedal(qapp, monkeypatch, bars, signature, rate):
    editor, playback, clock, keyboard=make_keyboard()
    editor._execute(editor.state.with_tempo(TempoMap(bpm=120,beats_per_bar=signature[0],beat_unit=signature[1])))
    playback.playbackRate=rate
    mixer=PianoVoiceMixer()
    monkeypatch.setattr(mixer,'start_audio',lambda **kwargs:None)
    playback.piano_source=SimpleNamespace(mixer=mixer)
    settings=keyboard.preferences.snapshot();settings['countInBars']=bars;keyboard.preferences.apply(settings)
    keyboard.pressKey(ord('A'));keyboard.setPedal(True);keyboard.startRecording()
    clock.advance(2);keyboard._tick()
    assert keyboard._stage=='preparing' and not editor.state.notes
    mixer._audio_ready=True
    keyboard._tick()
    clock.advance(bars*signature[0]*.5*4/signature[1]/rate)
    keyboard._tick()
    assert editor.state.notes[0].start_time==0
    assert editor.state.controls[0].time==0
    assert len(playback.started_voices)==1
    clock.advance(.2);keyboard.stopRecording()
    assert editor.state.notes[0].duration==pytest.approx(.2*rate)
    assert editor.state.controls[-1].value==0


def test_empty_countin_cancel_and_timeout_leave_no_history(qapp, monkeypatch):
    editor, playback, clock, keyboard=make_keyboard()
    values=keyboard.preferences.snapshot();values['countInBars']=2;keyboard.preferences.apply(values)
    keyboard.startRecording();keyboard.cancelRecording()
    assert not editor.canUndo
    mixer=PianoVoiceMixer();monkeypatch.setattr(mixer,'start_audio',lambda **kwargs:None)
    playback.piano_source=SimpleNamespace(mixer=mixer)
    keyboard.startRecording();clock.advance(6);keyboard._tick()
    assert not keyboard.recording and not editor.canUndo


def test_muted_target_keeps_take_without_monitor_or_changing_mute(qapp):
    editor, playback, clock, keyboard=make_keyboard()
    editor._execute(editor.state.update_layer(editor.activeLayerId,muted=True))
    keyboard.startRecording();keyboard.pressKey(ord('A'));clock.advance(.3);keyboard.stopRecording()
    assert not playback.started_voices
    assert len(editor.state.notes)==1 and editor.state.layers[0].muted


def test_late_display_tick_cannot_lose_boundary_key_or_pedal(qapp):
    editor, playback, clock, keyboard = make_keyboard()
    values = keyboard.preferences.snapshot(); values['countInBars'] = 1
    keyboard.preferences.apply(values)
    keyboard.pressKey(ord('A')); keyboard.setPedal(True); keyboard.startRecording()
    clock.advance(2.1)  # Musical boundary passed; deliberately no display tick.
    keyboard.releaseKey(ord('A'))
    keyboard.setPedal(False)
    keyboard.pressKey(ord('S')); clock.advance(.2); keyboard.stopRecording()
    assert [n.start_time for n in editor.state.notes] == pytest.approx([0, .1])
    assert [n.duration for n in editor.state.notes] == pytest.approx([.1, .2])
    assert [e.time for e in editor.state.controls] == pytest.approx([0, .1])
    assert len(playback.started_voices) == 2


def test_autosave_waits_for_take_and_manual_save_failure_keeps_take(qtbot, tmp_path, monkeypatch):
    editor, playback, clock, keyboard = make_keyboard()
    editor._set_current_path(str(tmp_path / 'take.pianoai.json'))
    editor.setAutosavePolicy(True, 15)
    keyboard.startRecording(); keyboard.pressKey(ord('A')); clock.advance(.4)
    keyboard.releaseKey(ord('A')); keyboard._tick()
    editor._autosave_now()
    assert keyboard.recording and not editor._autosave_timer.isActive()
    keyboard.stopRecording()
    assert editor._autosave_timer.isActive()
    original = editor.state.notes
    def fail_write(*args, **kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr('ai_piano_studio.editor.controller.save_project_document', fail_write)
    with qtbot.waitSignal(editor.saveFailed, timeout=2000):
        editor.saveProject(str(tmp_path / 'failed.pianoai.json'))
    # Synchronization changes pending/saved display status, not the captured
    # music or the ability to undo the whole take after a failed disk write.
    assert [(n.id, n.pitch, n.start_time, n.duration) for n in editor.state.notes] == [
        (n.id, n.pitch, n.start_time, n.duration) for n in original]
    assert editor.canUndo and editor.isDirty
    editor.undo()
    assert not editor.state.notes


@pytest.mark.parametrize('replace', ['new', 'open'])
def test_clean_countin_project_replacement_ends_take(qapp, tmp_path, replace):
    from ai_piano_studio.persistence.project_json import save_project
    from ai_piano_studio.domain.project import ProjectState
    editor, playback, clock, keyboard = make_keyboard()
    values = keyboard.preferences.snapshot(); values['countInBars'] = 1
    keyboard.preferences.apply(values); keyboard.startRecording()
    if replace == 'new':
        editor.newProject()
    else:
        path = tmp_path / 'other.pianoai.json'; save_project(path, ProjectState())
        editor.openProject(str(path))
    assert not keyboard.recording and not editor.recordingTakeActive
    clock.advance(3); keyboard._tick()
    assert not editor.state.notes


def test_playing_countin_pauses_backing_and_resumes_at_capture_clock(qapp):
    old = Note.create(pitch=48, start_time=0, duration=20)
    editor, playback, clock, keyboard = make_keyboard((old,))
    playback.playing = True; playback.positionSeconds = 4
    pauses, resumes = [], []
    def pause():
        pauses.append(clock()); playback.playing = False
    playback.pause = pause; playback.play = lambda: None
    playback.resumeRecordingAt = lambda at: resumes.append(at)
    values = keyboard.preferences.snapshot(); values['countInBars'] = 1
    keyboard.preferences.apply(values); keyboard.startRecording()
    assert len(pauses) == 1 and not resumes
    clock.advance(2.1); keyboard.pressKey(ord('A')); clock.advance(.2); keyboard.stopRecording()
    assert resumes == pytest.approx([4.1])
    assert editor.state.notes[-1].start_time == pytest.approx(4.1)


def test_overdub_event_order_follows_existing_same_time_events(qapp):
    from ai_piano_studio.domain.control_event import ControlEvent
    editor, playback, clock, keyboard = make_keyboard()
    editor._execute(editor.state.with_controls((ControlEvent.create(time=0, value=0, order=900),)))
    keyboard.setPedal(True); keyboard.startRecording(); keyboard.pressKey(ord('A'))
    clock.advance(.2); keyboard.stopRecording()
    assert editor.state.controls[1].order > 900
    assert editor.state.notes[-1].on_order > editor.state.controls[1].order


def test_periodic_recovery_preserves_active_take_without_stopping(qapp, tmp_path):
    from ai_piano_studio.persistence.autosave import RecoveryStore
    editor, playback, clock, keyboard=make_keyboard()
    editor._recovery=RecoveryStore(tmp_path/'recovery')
    keyboard.startRecording();keyboard.pressKey(ord('A'));keyboard.setPedal(True)
    clock.advance(.5);keyboard._tick()
    assert editor._recovery_timer.isActive()
    editor._write_recovery_now()
    recovered=editor._recovery.read_document()
    assert recovered[0].state.notes and recovered[0].state.controls
    assert keyboard.recording and editor._recovery_timer.isActive()
    keyboard._audio_ready_changed(False)
    assert not keyboard.recording and editor.canUndo
    assert editor.state.controls[-1].value==0
    editor.undo();assert not editor.state.notes and not editor.state.controls


def test_setup_exception_closes_empty_transaction(qapp):
    editor, playback, clock, keyboard=make_keyboard()
    playback.playing=True
    def fail_pause():
        raise OSError('lost transport')
    playback.pause=fail_pause
    keyboard.startRecording()
    assert not keyboard.recording and not editor.recordingTakeActive
    assert editor._gesture_origin is None and not editor.canUndo


def test_backing_restart_failure_finishes_capture(qapp):
    from tests.qt.test_keyboard import make_real_keyboard
    old=Note.create(pitch=48,start_time=0,duration=5)
    editor, player, playback, clock, keyboard=make_real_keyboard((old,))
    def fail_play(*args):
        raise OSError('backing restart failed')
    player.play_notes=fail_play
    keyboard.pressKey(ord('A'));keyboard.startRecording()
    assert not keyboard.recording and not editor.recordingTakeActive
    assert editor.state.notes[0]==old
    assert len(editor.state.notes)==2 and not keyboard.heldKeys


def test_cancel_rewrites_recovery_without_resurrecting_take(qapp, tmp_path):
    from ai_piano_studio.persistence.autosave import RecoveryStore
    editor, playback, clock, keyboard=make_keyboard()
    editor._recovery=RecoveryStore(tmp_path/'recovery')
    old=Note.create(pitch=48,start_time=0,duration=5)
    editor._execute(editor.state.add_note(old))
    original=editor.state.notes
    keyboard.startRecording();keyboard.pressKey(ord('A'));clock.advance(.5);keyboard._tick()
    editor._write_recovery_now()
    assert len(editor._recovery.read_document()[0].state.notes)==2
    keyboard.cancelRecording()
    assert editor._recovery.read_document()[0].state.notes==original
