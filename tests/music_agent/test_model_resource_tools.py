"""The in-app model chooses and uses resources, without developer song lookup."""
import importlib
import importlib.util
import json
from uuid import uuid4
from threading import current_thread, main_thread

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from tests.music_agent.test_creation_workflow import assistant_for, done


def resource_module():
    assert importlib.util.find_spec('ai_piano_studio.music_agent.resource_tools'), 'The model has no general music-resource tools'
    return importlib.import_module('ai_piano_studio.music_agent.resource_tools')


def call(tool_name, **arguments):
    return (ProviderEvent(tool_calls=(ProviderToolCall(uuid4().hex, tool_name, json.dumps(arguments)),)),)


def test_model_can_fetch_and_preview_an_unlisted_song_from_an_edit_turn(tmp_path, qapp, monkeypatch):
    module = resource_module()
    threads = []
    def fetch(url, **kwargs):
        threads.append(current_thread())
        return (CATALOG_HOME / 'moonlight1.mid').read_bytes(), url, 'audio/midi'
    monkeypatch.setattr(module, 'fetch_public_resource', fetch)
    old = Note.create(id='old', pitch=60, start_time=4, duration=1, velocity=75)
    editor = EditorController(ProjectState(notes=(old,), selected_ids=frozenset({'old'})))
    assistant, playback, provider = assistant_for(tmp_path, editor, [
        call('inspect_music_resource', url='https://scores.example.org/arrangements/opus-847.dat',
             title='玻璃海与纸月亮', source_url='https://scores.example.org/album'),
        call('prepare_song_preview', resource_id='resource-1', start_seconds=0, duration_seconds=20),
    ])
    before = editor.state
    assert assistant.sendMessage('现在全部删除，来一首20s的玻璃海与纸月亮，某歌手的')
    done(qapp, assistant)
    assert assistant.taskPreviewAvailable, (assistant.taskProgress, [m.text for m in assistant.messages.entries])
    assert editor.state == before and not playback.notes
    assert threads and all(t is not main_thread() for t in threads)
    assert 'This app has no song-search tool' not in provider.requests[0][0]['content']
    assert '玻璃海与纸月亮' in assistant.taskPreviewInfo['title']
    assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(20)
    assert not assistant.applyTaskProject()
    assert assistant.auditionTask() and assistant.applyTaskProject()
    assert len(editor.state.playback_notes) > 1
    assert assistant.lastTurnResult['provenance']['download_url'].endswith('opus-847.dat')
    assistant.shutdown()


def test_model_gets_html_links_and_can_change_sources_without_read_enforcement(tmp_path, qapp, monkeypatch):
    module = resource_module()
    monkeypatch.setattr(module, 'fetch_public_resource', lambda url, **kw:
        (b'<html><title>Score</title><a href="/files/847.mid">MIDI download</a></html>', url, 'text/html')
        if url.endswith('/page') else ((CATALOG_HOME / 'moonlight1.mid').read_bytes(), url, 'audio/midi'))
    editor = EditorController(ProjectState(notes=(Note.create(id='old', pitch=60, start_time=0, duration=1, velocity=80),)))
    scripts = [call('inspect_music_resource', url='https://scores.example.org/page', title='陌生歌', source_url='https://scores.example.org/page') for _ in range(5)]
    scripts += [call('inspect_music_resource', url='https://scores.example.org/files/847.mid', title='陌生歌', source_url='https://scores.example.org/page'),
                call('prepare_song_preview', resource_id='resource-1', start_seconds=2, duration_seconds=10)]
    assistant, _, provider = assistant_for(tmp_path, editor, scripts)
    assert assistant.sendMessage('把当前作品换成陌生歌，先准备10秒试听')
    done(qapp, assistant)
    assert assistant.taskPreviewAvailable, (assistant.taskProgress.get('safeErrorCode'), [m.text for m in assistant.messages.entries],
        len(provider.requests), provider.requests[-1][-1] if provider.requests else {})
    assert len(provider.requests) == 7
    assert '/files/847.mid' in json.dumps(provider.requests[1])
    assert not any('Use the available music tools now' in json.dumps(m) for m in provider.requests)
    assistant.shutdown()


@pytest.mark.parametrize('url', ['file:///C:/secret', 'http://example.org/song.mid',
    'https://127.0.0.1/song.mid', 'https://[::1]/song.mid', 'https://user:pass@example.org/song.mid'])
def test_resource_transport_rejects_nonpublic_or_credential_urls_before_connect(url):
    module = resource_module()
    with pytest.raises(ValueError):
        module.public_resource_address(url)


def test_cancellation_discards_model_prepared_resource(tmp_path, qapp, monkeypatch):
    module = resource_module()
    from threading import Event
    entered, release = Event(), Event()
    def fetch(url, **kwargs):
        entered.set()
        release.wait(5)
        return (CATALOG_HOME / 'moonlight1.mid').read_bytes(), url, 'audio/midi'
    monkeypatch.setattr(module, 'fetch_public_resource', fetch)
    editor = EditorController(ProjectState())
    assistant, _, provider = assistant_for(tmp_path, editor, [call('inspect_music_resource',
        url='https://scores.example.org/song.mid', title='任意曲名', source_url='https://scores.example.org/')])
    assert assistant.sendMessage('请检查这个乐谱来源，不修改当前工程')
    from tests.music_agent.test_assistant_controller import _wait_until
    _wait_until(qapp, entered.is_set)
    assistant.cancelGeneration()
    release.set()
    _wait_until(qapp, lambda: not assistant._workers)
    assert not assistant.taskPreviewAvailable and not editor.state.active_notes
    assistant.shutdown()


def test_codex_native_search_is_available_to_ordinary_in_app_turns(tmp_path):
    from threading import Event
    from tests.music_agent.test_codex_controller import assistant_at
    from ai_piano_studio.music_agent.creation_workflow import CreationWorkflow
    assistant=assistant_at(tmp_path)
    assistant.selectProvider('codex')
    assistant.saveProviderSettings('codex','','','','test-cli-model',False)
    assistant._creation_workflow=CreationWorkflow(assistant,None)
    loop=assistant._create_agent_loop(1,Event())
    assert loop._provider.allow_web_search
    assert 'inspect_music_resource' in loop._registry.names
    assistant.shutdown()


@pytest.mark.parametrize('proxy', [False,True])
def test_resource_fetch_pins_public_ip_and_checks_original_tls_host(monkeypatch,proxy):
    module=resource_module()
    connections,tunnels,tls,paths=[],[],[],[]
    class Sock:
        def close(self): pass
    class Context:
        def wrap_socket(self,sock,*,server_hostname):
            tls.append(server_hostname)
            return sock
    class Response:
        status=200
        def getheader(self,k,default=None): return 'audio/midi' if k=='Content-Type' else default
        def read(self,n):
            value=getattr(self,'remaining',b'MThd')
            self.remaining=b''
            return value
    monkeypatch.setattr(module.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('93.184.216.34',443))])
    monkeypatch.setattr(module.socket,'create_connection',lambda endpoint,timeout:(connections.append(endpoint) or Sock()))
    monkeypatch.setattr(module.ssl,'create_default_context',lambda:Context())
    monkeypatch.setattr(module,'getproxies',lambda:{'https':'http://127.0.0.1:7897'} if proxy else {})
    def request(self,method,path,**kwargs):
        paths.append(path)
        self.connect()
    monkeypatch.setattr(module.http.client.HTTPSConnection,'request',request)
    monkeypatch.setattr(module.http.client.HTTPSConnection,'_tunnel',lambda self:tunnels.append(self._tunnel_host))
    monkeypatch.setattr(module.http.client.HTTPSConnection,'getresponse',lambda self:Response())
    data,url,mime=module.fetch_public_resource('https://scores.example.org/陌生曲.mid')
    assert data==b'MThd' and mime=='audio/midi'
    assert tls==['scores.example.org']
    assert connections==[('127.0.0.1',7897) if proxy else ('93.184.216.34',443)]
    assert tunnels==(['93.184.216.34'] if proxy else [])
    assert paths==['/%E9%99%8C%E7%94%9F%E6%9B%B2.mid']


def test_resource_address_rejects_mixed_public_private_dns(monkeypatch):
    module=resource_module()
    monkeypatch.setattr(module.socket,'getaddrinfo',lambda *a,**kw:[
        (2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('10.0.0.5',443))])
    with pytest.raises(ValueError): module.public_resource_address('https://scores.example.org/s.mid')


@pytest.mark.parametrize('address',['224.0.0.1','240.0.0.1','ff02::1'])
def test_resource_address_rejects_multicast_and_reserved_addresses(monkeypatch,address):
    module=resource_module()
    monkeypatch.setattr(module.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',(address,443))])
    with pytest.raises(ValueError): module.public_resource_address('https://scores.example.org/s.mid')


def test_supplement_after_draft_preparation_discards_superseded_song(tmp_path,qapp,monkeypatch):
    module=resource_module()
    from threading import Event
    from tests.music_agent.test_assistant_controller import _wait_until
    entered,release=Event(),Event()
    monkeypatch.setattr(module,'fetch_public_resource',lambda url,**kw:
        ((CATALOG_HOME/'moonlight1.mid').read_bytes(),url,'audio/midi'))
    original=module.MusicResourceSession._prepare
    def prepare(session,payload):
        result=original(session,payload)
        if not entered.is_set():
            entered.set()
            release.wait(5)
        return result
    monkeypatch.setattr(module.MusicResourceSession,'_prepare',prepare)
    scripts=[]
    for title in ('第一首','改后的第二首'):
        scripts += [call('inspect_music_resource',url='https://scores.example.org/847.mid',
                         title=title,source_url='https://scores.example.org/'),
                    call('prepare_song_preview',resource_id='resource-1',start_seconds=0,duration_seconds=10)]
    editor=EditorController(ProjectState())
    assistant,_,provider=assistant_for(tmp_path,editor,scripts)
    assert assistant.sendMessage('请准备一首歌供我试听')
    _wait_until(qapp,entered.is_set)
    try:
        assert assistant.sendMessage('不要第一首，换成第二首再准备')
    finally:
        release.set()
    done(qapp,assistant)
    assert assistant.taskPreviewAvailable and '第二首' in assistant.taskPreviewInfo['title']
    assert assistant.pendingMessageCount==0 and not editor.state.active_notes
    assistant.shutdown()


def test_natural_dialogue_can_use_real_music_tools_in_proposal_mode(tmp_path,qapp):
    from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
    text='这里有点扎耳朵，帮我处理舒服一些'
    assert MusicAssistantController._classify_intent(text)=='CHAT'
    old=Note.create(id='old',pitch=60,start_time=0,duration=1,velocity=80)
    editor=EditorController(ProjectState(notes=(old,)))
    assistant,_,provider=assistant_for(tmp_path,editor,[
        call('set_note_velocity',note_ids=['old'],velocity=55)])
    assert assistant.sendMessage(text)
    done(qapp,assistant)
    assert assistant.proposal['available'], [m.text for m in assistant.messages.entries]
    assert editor.state.active_notes[0].velocity==80
    assert assistant.applyProposal()['success']
    assert editor.state.active_notes[0].velocity==55
    editor.undo()
    assert editor.state.active_notes[0].velocity==80
    assistant.shutdown()


@pytest.mark.parametrize('compressed',[False,True])
def test_untrusted_digital_score_is_bounded_before_optional_converter(tmp_path,monkeypatch,compressed):
    module=resource_module()
    from io import BytesIO
    from zipfile import ZipFile,ZIP_DEFLATED
    from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime
    if compressed:
        stream=BytesIO()
        with ZipFile(stream,'w',ZIP_DEFLATED) as archive:
            archive.writestr('score.musicxml',b'<score-partwise>'+b' '*17_000_000)
        raw=stream.getvalue()
    else:
        raw=b'<!DOCTYPE score-partwise [<!ENTITY x SYSTEM "file:///secret">]><score-partwise/>'
    called=[]
    monkeypatch.setattr(module,'fetch_public_resource',lambda url,**kw:(raw,url,'application/xml'))
    def convert(*args,**kwargs):
        called.append(True)
        return module.validate_midi((CATALOG_HOME/'moonlight1.mid').read_bytes())
    monkeypatch.setattr(IsolatedMusicRuntime,'convert',convert)
    session=module.MusicResourceSession(tmp_path,lambda:False,lambda event:None)
    result=session.execute('inspect_music_resource',dict(url='https://scores.example.org/score.mxl' if compressed
        else 'https://scores.example.org/score.musicxml',title='乐谱',source_url='https://scores.example.org/'))
    assert result.data['status']=='unavailable' and not called
    session.close()


def test_exhausted_resource_lookup_does_not_force_an_unrelated_composition(tmp_path,qapp,monkeypatch):
    module=resource_module()
    def fetch(*args,**kwargs): raise OSError('unreachable')
    monkeypatch.setattr(module,'fetch_public_resource',fetch)
    old=Note.create(id='old',pitch=60,start_time=0,duration=1,velocity=80)
    editor=EditorController(ProjectState(notes=(old,)))
    scripts=[call('inspect_music_resource',url='https://scores.example.org/a.mid',title='浮光未眠',source_url='https://scores.example.org/'),
             (ProviderEvent(text_delta='本轮来源无法取得可解析音乐文件，尚未准备好试听作品。',finish_reason='stop'),)]
    assistant,_,provider=assistant_for(tmp_path,editor,scripts)
    assert assistant.sendMessage('现在全部删除，来一首20s的浮光未眠，某歌手的')
    done(qapp,assistant)
    assert len(provider.requests)==2
    assert assistant.taskProgress['safeErrorCode']=='source_unavailable'
    assert editor.state.active_notes==(old,) and not assistant.taskPreviewAvailable
    assistant.shutdown()


def test_source_note_pages_reach_model_feedback_with_timing_and_cursor(tmp_path,monkeypatch):
    module=resource_module()
    from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
    raw=(CATALOG_HOME/'moonlight1.mid').read_bytes()
    monkeypatch.setattr(module,'fetch_public_resource',lambda url,**kw:(raw,url,'audio/midi'))
    session=module.MusicResourceSession(tmp_path,lambda:False,lambda event:None)
    inspected=session.execute('inspect_music_resource',dict(url='https://scores.example.org/x.dat',
        title='公开参考',source_url='https://scores.example.org/'))
    rid=inspected.data['resource_id']
    first=session.execute('read_resource_notes',dict(resource_id=rid,offset=0))
    feedback=json.loads(serialize_provider_feedback(first))['data']
    assert len(feedback['notes'])==32 and feedback['next_offset']==32
    second=session.execute('read_resource_notes',dict(resource_id=rid,offset=feedback['next_offset']))
    assert second.data['notes']!=feedback['notes']
    source=session.resources[rid].midi
    expected=source.notes[0]
    assert feedback['notes'][0]['pitch']==expected.pitch
    assert feedback['notes'][0]['start_time']==pytest.approx(expected.start_tick*60/(source.ppq*source.bpm))
    end=session.execute('read_resource_notes',dict(resource_id=rid,offset=len(source.notes)))
    assert end.data['notes']==[] and json.loads(serialize_provider_feedback(end))['data']['next_offset'] is None
    session.close()
