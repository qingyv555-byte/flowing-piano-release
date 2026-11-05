"""Fresh Qt processes are necessary for QT_SCALE_FACTOR initialization."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from PySide6.QtCore import QObject
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.audio.sample_piano import SampleVoice
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.persistence.project_json import save_project
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import no_audio_device, click, qml

ROOT = Path(__file__).parents[2]
PROBE = r'''
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtQml import QQmlEngine, QQmlExpression
from ai_piano_studio.audio.mixer import PianoVoiceMixer
PianoVoiceMixer.start_audio = lambda *a, **k: (_ for _ in ()).throw(AssertionError('physical audio forbidden'))
from ai_piano_studio.studio import build_studio_engine
messages = []
qInstallMessageHandler(lambda kind, ctx, message: messages.append(message))
def qml(obj, text):
 e=QQmlExpression(QQmlEngine.contextForObject(obj),obj,text);v,_=e.evaluate()
 assert not e.hasError(),e.error().toString()
 return v
with TemporaryDirectory() as home:
 app,engine,editor,playback,keyboard=build_studio_engine(Path(home))
 root=engine.rootObjects()[0]
 for theme in ['paper','ink']:
  engine.rootContext().contextProperty('studioAppearance').setValue('themeId',theme)
  for size in [(860,640),(1200,820),(1000,700)]:
   root.resize(*size)
   qml(root,'setPage("editor"); true');app.processEvents()
   pending=[root.contentItem()]
   while pending:
    item=pending.pop();pending.extend(item.childItems())
    if 'Repeater' in item.metaObject().className():
     value=item.property('model')
     if isinstance(value,(int,float)): assert value>=0,value
   qml(root,'setPage("launch"); true');app.processEvents()
 assert not [m for m in messages if any(s in m for s in ['Model size of','Binding loop','TypeError','ReferenceError'])],messages
 print(json.dumps({'dpr':root.devicePixelRatio(),'cycles':6,'messages':len(messages)}))
 root.close();engine.deleteLater();app.processEvents()
'''


@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
def test_fast_navigation_and_resize_at_high_dpi_never_produce_invalid_models(scale):
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src'), QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', PROBE], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['cycles'] == 6 and report['dpr'] == pytest.approx(float(scale))


def test_project_switch_and_viewport_request_exact_roots_without_hot_path_decode(studio_session, qtbot, tmp_path):
    _, context, root, editor, playback = studio_session
    initialize_deferred_services(context)
    engine = playback.piano_source._coordinator.selection().primary
    qtbot.waitUntil(lambda: engine.cache_metrics()['pending'] == 0)
    engine.discard_idle_cache()
    engine.activate_cache()
    jobs = []
    engine._scheduler = jobs.append
    project = tmp_path / 'cache-context.pianoai.json'
    save_project(project, ProjectState(notes=(Note.create(pitch=81, start_time=0, duration=.5, status='saved'),)))
    editor._load_project(str(project))
    assert jobs and not engine.cached_root_midis
    for job in jobs[:]:
        job()
    assert engine.ready_for_pitches((81,))
    qml(root, 'setPage("editor"); true')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    roll = root.findChild(QObject, 'timeline')
    roll.setProperty('viewportTop', 100)
    assert len(jobs) > 1  # visible-range requests are metadata-only
    assert engine.cache_metrics()['misses'] == 0


@pytest.mark.parametrize('temporary', [False, True])
def test_real_player_prepares_project_and_proposal_preview_before_first_pcm(studio_session, qtbot, temporary):
    _, _, _, _, playback = studio_session
    player = playback._player
    engine = playback.piano_source._coordinator.selection().primary
    qtbot.waitUntil(lambda: engine.cache_metrics()['pending'] == 0)
    engine.discard_idle_cache()
    engine.activate_cache()
    jobs = []
    engine._scheduler = jobs.append
    notes = (Note.create(pitch=81, start_time=0, duration=.5),)
    (player.play_temporary_notes if temporary else player.play_notes)(notes)
    assert player.is_preparing() and not player._mixer.active_voice_ids
    for job in jobs[:]:
        job()
    qtbot.waitUntil(lambda: not player.is_preparing())
    voices = playback.piano_source._voices
    assert voices
    assert all(isinstance(voice, SampleVoice) for pending in voices.values()
               for tone_voice in pending for _, voice in tone_voice.bank_voices)
    assert engine.cache_metrics()['misses'] == 0
    if temporary:
        player.stop_temporary_notes(ReleaseMode.STOP)
    else:
        player.stop(ReleaseMode.STOP)
