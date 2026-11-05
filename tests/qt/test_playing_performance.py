"""Opt-in evidence: actual QML/sample mixer, no physical audio or input devices.

Run explicitly; timings are observations, not cross-machine pass/fail gates.
"""
import json
import os
from pathlib import Path
from time import perf_counter
import numpy as np
import pytest
from PySide6.QtCore import QObject, Qt, QEventLoop, QEvent
from PySide6.QtGui import QKeyEvent
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.control_event import ControlEvent
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_workflows import no_audio_device, click


def timing(values):
    return {'p50_ms':float(np.percentile(values,50)),
            'p95_ms':float(np.percentile(values,95)), 'max_ms':max(values)}


@pytest.mark.parametrize('count', [1000,5000])
@pytest.mark.parametrize('feedback', ['off','standard'])
def test_recording_frame_and_pcm_evidence(studio_session, qtbot, tmp_path, count, feedback):
    app, engine, root, editor, playback=studio_session
    root.resize(1366,768)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'studioRecordButton') is not None)
    timeline=root.findChild(QObject,'timeline')
    qtbot.waitUntil(lambda:timeline.width()>800)
    app.processEvents(QEventLoop.AllEvents,5)
    notes=tuple(Note.create(id=f'perf-{i}',pitch=48+i%36,start_time=i*.05,duration=.3) for i in range(count))
    controls=tuple(ControlEvent.create(id=f'cc-{i}',time=i*.25,value=127 if i%2==0 else 0) for i in range(200))
    state=ProjectState(notes,controls=controls)
    editor._replace_project(state,saved_content=state.content,path=None)
    timeline=root.findChild(QObject,'timeline')
    assert timeline.property('denseProject')
    assert root.findChild(QObject,'rawNoteRepeater').property('count')==0
    assert root.findChild(QObject,'compositeNoteRepeater').property('count')==0
    app.processEvents(QEventLoop.AllEvents, 5)
    appearance=engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('feedbackLevel',feedback)
    playback.setVolume(.65)
    keyboard=engine.rootContext().contextProperty('keyboard')
    keyboard.startRecording();keyboard.setPedal(True)
    qtbot.waitUntil(lambda:keyboard._stage=='recording')
    mixer=playback.piano_source.mixer
    timeline=root.findChild(QObject,'timeline');timeline.forceActiveFocus()
    # Warm sample/render paths; the report deliberately excludes first-load cost.
    app.sendEvent(root,QKeyEvent(QEvent.KeyPress,Qt.Key_A,Qt.NoModifier))
    mixer.mix_frames(256)
    app.sendEvent(root,QKeyEvent(QEvent.KeyRelease,Qt.Key_A,Qt.NoModifier))
    keyboard._tick()
    app.processEvents(QEventLoop.AllEvents, 5)
    root.grabWindow()
    dispatch,pcm,frame,callback=[],[],[],[]
    peak=0;clipped=0
    for _ in range(20):
        started=perf_counter();app.sendEvent(root,QKeyEvent(QEvent.KeyPress,Qt.Key_A,Qt.NoModifier));scheduled=perf_counter()
        payload=mixer.mix_frames(256);mixed=perf_counter()
        keyboard._tick()
        app.processEvents(QEventLoop.AllEvents, 5)
        picture=root.grabWindow()
        painted=perf_counter()
        assert not picture.isNull()
        dispatch.append((scheduled-started)*1000);pcm.append((mixed-started)*1000)
        frame.append((painted-started)*1000);callback.append((mixed-scheduled)*1000)
        values=np.frombuffer(payload,dtype='<i2').astype(np.int32)
        peak=max(peak,int(np.max(np.abs(values))));clipped+=int(np.sum(np.abs(values)>=32767))
        app.sendEvent(root,QKeyEvent(QEvent.KeyRelease,Qt.Key_A,Qt.NoModifier))
    keyboard.setPedal(False);keyboard.stopRecording();playback.stop()
    for _ in range(200):mixer.mix_frames(256)
    assert len(editor.state.notes)==count+21
    assert mixer.voice_statistics()['active']==0
    result={'notes':count,'cc64_source_events':200,'feedback':feedback,'window':'1366x768',
        'method':'warm Qt sendEvent key → real sample scheduling → 256 PCM → explicit display projection + offscreen grab; device output substituted',
        'dispatch':timing(dispatch),'first_pcm':timing(pcm),'offscreen_frame':timing(frame),
        'pcm_pull_256_frames':timing(callback),'voices':mixer.voice_statistics(),
        'pcm_peak_i16':peak,'samples_at_full_scale':clipped,'hardware_underruns':None}
    output=Path(os.environ.get('PLAYING_PERFORMANCE_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    (output/f'gui-performance-{count}-{feedback}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
