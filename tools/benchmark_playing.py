"""Device-free input/scheduling evidence. Never a keyboard-to-speaker measurement."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from statistics import median
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ReleaseMode


class DeviceFreePlayback(QObject):
    playingChanged=Signal()
    positionSeconds=0.
    playbackRate=1.
    def __init__(self):
        super().__init__()
        self.mixer=PianoVoiceMixer()
        self.voice=0
    def startAudition(self,pitch,velocity):
        self.voice+=1
        self.mixer.start_voice(f'audition:{self.voice}',pitch,velocity,(10000,)*8192,
                              source_sample_rate=44100,owner_tag='input')
        return self.voice
    def releaseAudition(self,voice,mode):
        self.mixer.note_off(f'audition:{voice}',ReleaseMode(mode))


def summary(values):
    ordered=sorted(values)
    return {'median_ms':median(values),'p95_ms':ordered[min(len(ordered)-1,int(len(ordered)*.95))], 'max_ms':max(values)}


def main():
    app=QApplication.instance() or QApplication([])
    result={'method':'direct logical input → scheduling + take ledger; then first 64 PCM frames and event-loop display batching, no sink/window/physical input/display/audio latency',
            'python':platform.python_version(),'machine':platform.platform(),'sizes':{}}
    with TemporaryDirectory() as home:
        for count in (1000,5000):
            notes=tuple(Note.create(id=f'n{i}',pitch=48+i%36,start_time=i*.05,duration=.3) for i in range(count))
            editor=EditorController(ProjectState(notes),project_library=ProjectLibrary(Path(home)/str(count)))
            playback=DeviceFreePlayback()
            keyboard=KeyboardController(editor,playback)
            keyboard.startRecording()
            inputs=[]; pcm=[]; frame=[]
            for index in range(30):
                started=perf_counter(); keyboard.pressKey(ord('A')); scheduled=perf_counter()
                playback.mixer.mix_frames(64); mixed=perf_counter()
                app.processEvents(); displayed=perf_counter()
                inputs.append((scheduled-started)*1000);pcm.append((mixed-started)*1000);frame.append((displayed-started)*1000)
                keyboard.releaseKey(ord('A'))
            keyboard.stopRecording()
            result['sizes'][str(count)]={'dispatch':summary(inputs),'first_pcm':summary(pcm),'event_loop_no_window':summary(frame),'voices':playback.mixer.voice_statistics()}
            editor.deleteLater();keyboard.deleteLater();playback.deleteLater();app.processEvents()
    output=Path('artifacts/playing-recording');output.mkdir(parents=True,exist_ok=True)
    (output/'input-performance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
