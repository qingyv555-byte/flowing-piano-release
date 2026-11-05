"""Generate a deterministic 20-second take through the real input/controller API.

The independent decoder is mido 1.3.3, installed only in the artifact directory.
No device is opened and no real user profile is used.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.control_event import sustain_snapshot
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_json import save_project,load_project
from ai_piano_studio.persistence.midi import export_midi
from ai_piano_studio.audio.synth import render_project,write_wav
from benchmark_playing import DeviceFreePlayback


def verify_existing_copy(source, app, output):
    """Read a user-selected source once; every write goes to a temporary library."""
    import hashlib
    from time import perf_counter
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    with TemporaryDirectory() as home:
        path = Path(home) / 'copy.pianoai.json'
        path.write_bytes(payload)
        state = load_project(path)
        editor = EditorController(state, project_library=ProjectLibrary(Path(home) / 'Projects'))
        layer = next(layer for layer in state.layers if not layer.locked)
        editor.setActiveLayer(layer.id)
        player = DeviceFreePlayback()
        clock = [100.]
        keyboard = KeyboardController(editor, player, clock=lambda: clock[0])
        before = editor.state.content
        started = perf_counter()
        keyboard.startRecording()
        assert keyboard._stage == 'recording'
        keyboard.pressKey(ord('A')); keyboard.setPedal(True)
        clock[0] += .5; keyboard.releaseKey(ord('A'))
        clock[0] += .5; keyboard.setPedal(False); keyboard.stopRecording()
        elapsed = (perf_counter() - started) * 1000
        assert len(editor.state.notes) == len(state.notes) + 1
        assert editor.state.notes[:len(state.notes)] == state.notes
        assert len(editor.state.controls) == len(state.controls) + 2
        recorded = editor.state.content
        editor.undo(); assert editor.state.content == before
        editor.redo(); assert editor.state.content == recorded
        editor.synchronizeNow(); save_project(path, editor.state)
        restored = load_project(path)
        assert restored.notes == editor.state.notes and restored.controls == editor.state.controls
        keyboard.releaseAll(); app.processEvents()
        result = {'source_sha256': digest, 'source_bytes': len(payload),
            'original_notes': len(state.notes), 'original_layers': len(state.layers),
            'original_cc64': len(state.controls), 'take_notes': 1, 'take_cc64': 2,
            'record_and_commit_ms': elapsed, 'undo_redo': True, 'save_reload': True,
            'original_unchanged': hashlib.sha256(source.read_bytes()).hexdigest() == digest,
            'method': 'temporary copy, real Editor/Keyboard, deterministic clock, no device or window'}
        assert result['original_unchanged']
        (output / 'existing-project-copy.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--existing-project', type=Path)
    args = parser.parse_args()
    app=QApplication.instance() or QApplication([])
    output=Path('artifacts/playing-recording');output.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(output/'verification-deps'))
    import mido
    events=[]
    clock=[100.]
    with TemporaryDirectory() as home:
        editor=EditorController(ProjectState(),project_library=ProjectLibrary(Path(home)/'Projects'))
        player=DeviceFreePlayback()
        keyboard=KeyboardController(editor,player,clock=lambda:clock[0])
        settings=keyboard.preferences.snapshot();settings['countInBars']=1;keyboard.preferences.apply(settings)
        keyboard.startRecording()
        clock[0]+=2;keyboard._tick()
        song_origin=clock[0]
        def event(at, action, key=None):
            clock[0]=song_origin+at
            if action=='down':keyboard.pressKey(ord(key))
            elif action=='up':keyboard.releaseKey(ord(key))
            elif action=='pedal':keyboard.setPedal(bool(key))
            keyboard._tick()
            player.mixer.mix_frames(128)
            events.append({'seconds':at,'action':action,'key':key})
        # Original melody: actual repeated attacks, a held chromatic note,
        # independently released chord, and separate pedal actions.
        for i in range(5):
            event(i*.35,'down','A');event(i*.35+.15,'up','A')
        event(2,'down','W');event(2.2,'pedal',True);event(2.6,'up','W');event(4,'pedal',False)
        for key in 'ADG':event(4.5,'down',key)
        event(4.7,'pedal',True)
        for at,key in zip((4.8,5,5.3),'ADG'):event(at,'up',key)
        event(7,'pedal',False)
        for at,key in ((8,'F'),(10,'H'),(12,'G'),(14,'D'),(17,'A')):
            event(at,'down',key);event(at+.9,'up',key)
        event(19.5,'pedal',True);event(19.7,'pedal',False)
        keyboard.stopRecording()
        first=editor.state.notes
        draft=keyboard.preferences.snapshot()
        profile=json.loads(json.dumps(draft['profiles'][0]));profile.update(id='demo',name='夜庭试弹')
        profile['mappings'].append({'key':ord('Q'),'pitch':66,'enabled':True})
        draft['profiles'].append(profile);draft['activeProfile']='demo'
        keyboard.preferences.apply(draft);keyboard.cancelSettings()
        player.positionSeconds=8
        keyboard.startRecording();clock[0]+=2;keyboard._tick()
        keyboard.pressKey(ord('Q'));clock[0]+=.5;keyboard.releaseKey(ord('Q'));keyboard.stopRecording()
        assert len(editor.state.notes)==len(first)+1 and editor.state.notes[-1].pitch==66
        editor.undo();assert editor.state.notes==first
        editor.redo();editor.synchronizeNow()
        state=editor.state
        save_project(output/'night-garden-take.pianoai.json',state)
        restored=load_project(output/'night-garden-take.pianoai.json')
        assert restored.notes==state.notes and restored.controls==state.controls
        export_midi(output/'night-garden-take.mid',state.effective_notes(),state.tempo,layers=state.layers,controls=state.effective_controls())
        decoded=mido.MidiFile(output/'night-garden-take.mid')
        cc=[];notes=[]
        for track_id,track in enumerate(decoded.tracks):
            tick=0
            for message in track:
                tick+=message.time
                if message.type=='control_change' and message.control==64:
                    cc.append({'tick':tick,'value':message.value,'channel':message.channel,'track':track_id})
                if message.type=='note_on' and message.velocity:
                    notes.append(message.note)
        assert len(notes)==len(state.notes) and len(cc)==len(state.controls)
        # Independent merged-track seconds and FIFO NoteOn/NoteOff pairing,
        # not just a round trip through our own encoder/decoder.
        seconds=0.; held_keys={}; decoded_notes=[]; decoded_controls=[]
        for message in decoded:
            seconds+=message.time
            if message.type=='note_on' and message.velocity:
                held_keys.setdefault((message.channel,message.note),[]).append((seconds,message.velocity))
            elif message.type=='note_off' or (message.type=='note_on' and message.velocity==0):
                start,velocity=held_keys[(message.channel,message.note)].pop(0)
                decoded_notes.append((message.note,start,seconds-start,velocity))
            elif message.type=='control_change' and message.control==64:
                decoded_controls.append((seconds,message.value))
        expected=sorted((n.pitch,n.start_time,n.duration,n.velocity) for n in state.effective_notes(True))
        for actual,wanted in zip(sorted(decoded_notes),expected):
            assert actual[0]==wanted[0] and actual[3]==wanted[3]
            assert abs(actual[1]-wanted[1])<.002 and abs(actual[2]-wanted[2])<.002
        for actual,wanted in zip(sorted(decoded_controls),sorted((e.time,e.value) for e in state.effective_controls(True))):
            assert abs(actual[0]-wanted[0])<.002 and actual[1]==wanted[1]
        assert not any(held_keys.values())
        dry=render_project(state.effective_notes(True),sample_rate=16000)
        held=render_project(sustain_snapshot(state.effective_notes(True),state.effective_controls(True)),sample_rate=16000)
        write_wav(output/'night-garden-with-pedal.wav',held,sample_rate=16000)
        write_wav(output/'night-garden-without-pedal.wav',dry,sample_rate=16000)
        assert held!=dry
        result={'method':'real KeyboardController + Editor History, deterministic event clock, no physical device',
                'notes':len(state.notes),'controls':len(cc),'music_end_seconds':state.playback_duration,
                'wav_tail_end_seconds':len(held)/16000,'mido_version':'1.3.3','decoded_cc64':cc,
                'last_physical_note_off':max(n.start_time+n.duration for n in state.notes),
                'whole_take_undo_redo':True,'json_round_trip':True,'independent_note_times_and_velocity':True,
                'count_in_bars':1,'event_log':events}
        (output/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='event_log'},ensure_ascii=False,indent=2))
        keyboard.releaseAll();app.processEvents()
    if args.existing_project:
        verify_existing_copy(args.existing_project, app, output)


if __name__=='__main__':main()
