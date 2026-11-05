"""Source comparison remains readable without changing the approved source or editor."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from ai_piano_studio.music_agent import resource_tools
from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds
from ai_piano_studio.music_agent.assistant_controller import _SerializedGateway
from ai_piano_studio.music_agent.tool_contracts import MusicToolResult
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.music_agent.test_source_confirmation import inspected, payload
from ai_piano_studio.music_agent.source_confirmation import SourceProposal
from tests.music_agent.test_source_confirmation_workflow import (make_assistant, done, fetched, discovery, call, PAGE, SCORE)


@pytest.mark.parametrize('variant', ['same_bytes_other_url', 'different_midi', 'mml', 'xml'])
def test_confirmed_generation_can_compare_and_draft_from_another_parsed_source(tmp_path,qapp,qtbot,monkeypatch,variant):
    other='https://scores.example.org/other'
    def fetch(url, **kw):
        if url != other: return fetched(url,**kw)
        if variant == 'mml': return b'MML@t120o4c4d4;',url,'text/plain'
        if variant == 'xml':
            return b"""<score-partwise version="4.0">
              <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
              <part id="P1"><measure number="1"><attributes><divisions>1</divisions></attributes>
                <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
              </measure></part>
            </score-partwise>""",url,'application/xml'
        raw=fetched(SCORE)[0]
        if variant == 'different_midi':
            # Legal MIDI header changes PPQ, giving genuinely different timing/content.
            raw=raw[:12]+(480 if int.from_bytes(raw[12:14],'big')!=480 else 960).to_bytes(2,'big')+raw[14:]
        return raw,url,'audio/midi'
    monkeypatch.setattr(resource_tools,'fetch_public_resource',fetch)
    scripts=discovery()+[
        call('inspect_music_resource',dict(url=SCORE,title='A',source_url=PAGE)),
        call('inspect_music_resource',dict(url=other,title='B',source_url=PAGE)),
        call('read_resource_notes',dict(resource_id='resource-2',offset=0)),
        call('add_notes',dict(notes=[dict(pitch=70,start_time=i*.5,duration=.5,velocity=70) for i in range(30)])),
    ]
    assistant,_,provider=make_assistant(tmp_path,EditorController(ProjectState()),scripts)
    try:
        assert assistant.sendMessage('来一首测试乐队的云端旅行')
        done(qapp,assistant)
        assert assistant.confirmSongSource()
        # The real MusicXML worker can take over 500 ms under suite load.
        qtbot.waitUntil(lambda: not assistant.isBusy, timeout=5000)
        read_id = scripts[-2][0].tool_calls[0].call_id
        feedback = next(json.loads(m['content']) for request in provider.requests for m in request
                        if m.get('role') == 'tool' and m.get('tool_call_id') == read_id)
        assert feedback['data']['notes'], feedback
        assert assistant.taskPreviewAvailable
        assert not assistant._editor.state.playback_notes
    finally: assistant.shutdown()


@pytest.mark.parametrize('correction,expected',[('不是这首，换成另一版，30秒',30),('不是这首，换成另一版',15)])
def test_rejection_duration_flows_through_rediscovery_confirmation_preparation(tmp_path,qapp,monkeypatch,correction,expected):
    other='https://scores.example.org/second.mid'
    raw=fetched(SCORE)[0]; other_raw=raw[:12]+(480 if int.from_bytes(raw[12:14],'big')!=480 else 960).to_bytes(2,'big')+raw[14:]
    def fetch(url,**kw):
        if url==other: return other_raw,url,'audio/midi'
        if url==PAGE: return fetched(url)[0].replace(b'</html>',f'<a href="{other}">Other edition</a></html>'.encode()),url,'text/html'
        return fetched(url,**kw)
    monkeypatch.setattr(resource_tools,'fetch_public_resource',fetch)
    second=discovery()
    from ai_piano_studio.music_agent.providers import ProviderEvent,ProviderToolCall
    from uuid import uuid4
    second=[(ProviderEvent(tool_calls=tuple(ProviderToolCall(uuid4().hex,c.name,c.arguments_json.replace(SCORE,other)) for c in events[0].tool_calls)),) for events in second]
    scripts=discovery()+second+[call('inspect_music_resource',dict(url=other,title='Other',source_url=PAGE)),
        call('prepare_song_preview',dict(resource_id='resource-1',start_seconds=0,duration_seconds=15))]
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()),scripts)
    try:
        assert assistant.sendMessage('来一首测试乐队的云端旅行，15秒');done(qapp,assistant)
        assert assistant.sendMessage(correction);done(qapp,assistant)
        assert assistant.songSourceConfirmation['durationSeconds']==expected
        assert assistant.confirmSongSource();done(qapp,assistant)
        assert assistant.taskPreviewAvailable
        assert assistant.taskPreviewInfo['durationSeconds']==pytest.approx(expected)
    finally: assistant.shutdown()


@pytest.mark.parametrize('duration',['601','9'*310,'0','-1','nan','inf'])
def test_invalid_explicit_duration_is_not_defaulted_or_exposed(tmp_path,qapp,duration):
    with pytest.raises(ValueError): requested_excerpt_seconds('来一首歌曲，'+duration+'秒')
    assistant,_,provider=make_assistant(tmp_path,EditorController(ProjectState()))
    try:
        assert not assistant.sendMessage('来一首贝多芬的月光奏鸣曲，'+duration+'秒')
        assert not assistant.songSourceConfirmation and not assistant.taskPreviewAvailable
        assert not provider.requests
        assert any('600' in m.text for m in assistant.messages.entries)
    finally: assistant.shutdown()


@pytest.mark.parametrize('duration',['601','9'*310])
def test_gateway_never_dispatches_an_unbounded_trusted_duration(tmp_path,duration):
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.require_source_confirmation=True;session.instruction='来一首歌曲，'+duration+'秒'
    gateway=object.__new__(_SerializedGateway);gateway._resource_session=session;gateway._creator=False
    gateway._controller=SimpleNamespace(_appearance_tools=None,_song_source_flow=None,gateway=SimpleNamespace(registry=__import__('ai_piano_studio.music_agent.tool_registry',fromlist=['MusicToolRegistry']).MusicToolRegistry.default()))
    dispatched=[]
    gateway._invoke=lambda *args:MusicToolResult.success_result('prepare_song_preview','ok',data={'payload':args[2]})
    session.execute=lambda name,p:dispatched.append(p) or MusicToolResult.success_result(name,'ok')
    result=gateway.execute('prepare_song_preview',dict(resource_id='resource-1',start_seconds=0,duration_seconds=15),SimpleNamespace(intent_template=''))
    assert not result.success and result.error_code=='validation_error'
    assert not dispatched



def test_alternative_music_read_preserves_approval_and_readiness(tmp_path,monkeypatch):
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    monkeypatch.setattr(resource_tools,'fetch_public_resource',lambda url,**kw:(fetched(SCORE)[0],url,'audio/midi'))
    for url in [SCORE,SCORE+'?other']:
        session.execute('inspect_music_resource',dict(url=url,title='label',source_url=PAGE))
    from dataclasses import replace
    proposal=replace(SourceProposal.from_payload(payload(),inspected()),selected_url=SCORE,selected_sha256=hashlib.sha256(fetched(SCORE)[0]).hexdigest())
    session.require_source_confirmation=True;session.approved_source=proposal
    assert session.approved_source_ready
    result=session.execute('read_resource_notes',dict(resource_id='resource-2',offset=0))
    assert result.success and result.data['notes']
    assert session.approved_source is proposal and session.approved_source_ready
    result=session.execute('inspect_music_resource',dict(url=SCORE,title='A',source_url=PAGE))
    assert result.success and session.approved_source_ready
    session.close()


def test_approved_resource_allows_an_observed_redirect_to_same_artifact(tmp_path,monkeypatch):
    from dataclasses import replace
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.require_source_confirmation=True
    session.approved_source=replace(SourceProposal.from_payload(payload(),inspected()),selected_url=SCORE,selected_sha256=hashlib.sha256(fetched(SCORE)[0]).hexdigest())
    monkeypatch.setattr(resource_tools,'fetch_public_resource',lambda url,**kw:fetched(SCORE))
    result=session.execute('inspect_music_resource',dict(url='https://scores.example.org/redirect',title='A',source_url=PAGE))
    assert result.data['status']=='parsed' and session.approved_source_ready
    session.close()


def test_latest_duration_is_normalized_for_local_resolver_resume(tmp_path,qapp,monkeypatch):
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()))
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲，15秒');done(qapp,assistant)
        flow=assistant._song_source_flow; pending=flow.pending
        # Exercise trusted resume used by correction, retaining a local reference.
        assert flow._start(pending.instruction+'，用户纠正：改为30秒','改为30秒',approval=pending.proposal,reference=pending.reference)
        done(qapp,assistant)
        assert assistant.taskPreviewInfo['durationSeconds']==pytest.approx(30)
    finally: assistant.shutdown()


@pytest.mark.parametrize('text,expected',[('0.5秒',.5),('1e-6秒',.000001),('600秒',600),('15秒，改为30秒',30)])
def test_supported_duration_normalization_has_matching_legacy_parser(text,expected):
    from ai_piano_studio.music_agent.source_confirmation import normalize_excerpt_instruction
    from ai_piano_studio.music_agent.song_resolver import DURATION_RE
    normalized=normalize_excerpt_instruction(text)
    assert float(DURATION_RE.search(normalized).group(1))==expected


def test_invalid_correction_clears_pending_without_throwing_in_qt_slot(tmp_path,qapp):
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()))
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲，15秒');done(qapp,assistant)
        assert not assistant.sendMessage('不是这首，改为601秒')
        assert not assistant.songSourceConfirmation and not assistant.taskPreviewAvailable
        assert any('600' in m.text for m in assistant.messages.entries)
    finally: assistant.shutdown()


def test_public_candidate_and_confirm_defend_against_invalid_retained_duration(tmp_path,qapp):
    from dataclasses import replace
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()))
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲');done(qapp,assistant)
        flow=assistant._song_source_flow
        flow.pending=replace(flow.pending,instruction='来一首歌曲，'+'9'*310+'秒')
        assert assistant.songSourceConfirmation=={}
        assert not assistant.confirmSongSource()
        assert flow.pending is None and not assistant.taskPreviewAvailable
        assert assistant.taskProgress['status']=='failed'
    finally: assistant.shutdown()


def test_trusted_supported_duration_is_present_at_schema_validation(tmp_path):
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.require_source_confirmation=True;session.instruction='来一首歌曲，30秒'
    gateway=object.__new__(_SerializedGateway);gateway._resource_session=session;gateway._creator=False
    from ai_piano_studio.music_agent.tool_registry import MusicToolRegistry
    gateway._controller=SimpleNamespace(_appearance_tools=None,_song_source_flow=None,gateway=SimpleNamespace(registry=MusicToolRegistry.default()))
    validated=[]; dispatched=[]
    def validate(method,name,arguments,context):
        validated.append(arguments['duration_seconds'])
        return MusicToolResult.success_result(name,'ok',data={'payload':arguments})
    gateway._invoke=validate
    session.execute=lambda name,p:dispatched.append(p['duration_seconds']) or MusicToolResult.success_result(name,'ok')
    assert gateway.execute('prepare_song_preview',dict(resource_id='resource-1',start_seconds=0,duration_seconds=15),SimpleNamespace(intent_template='')).success
    assert validated==[30] and dispatched==[30]


def test_approved_parsed_reads_page_across_selected_interval(tmp_path,monkeypatch):
    from dataclasses import replace
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.require_source_confirmation=True;session.instruction='来一首歌曲，15秒'
    session.approved_source=replace(SourceProposal.from_payload(payload(),inspected()),selected_url=SCORE,
        selected_sha256=hashlib.sha256(fetched(SCORE)[0]).hexdigest(),start_seconds=10)
    monkeypatch.setattr(resource_tools,'fetch_public_resource',fetched)
    result=session.execute('inspect_music_resource',dict(url=SCORE,title='A',source_url=PAGE))
    assert result.data['status']=='parsed'
    result=session.execute('read_resource_notes',dict(resource_id='resource-1',offset=0))
    assert result.success and result.data['notes']
    assert result.data['notes'][0]['start_time'] < 10
    previous_offset = -1
    while result.data['next_offset'] is not None:
        offset = result.data['next_offset']
        assert offset > previous_offset
        previous_offset = offset
        result = session.execute('read_resource_notes', dict(resource_id='resource-1', offset=offset))
        assert result.success and result.data['notes']
    assert result.data['notes'][-1]['start_time'] > 25
    session.close()


def test_source_title_duration_cannot_override_validated_numeric_preview(tmp_path,monkeypatch):
    from dataclasses import replace
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.require_source_confirmation=True;session.instruction='来一段15秒的试听'
    session.approved_source=replace(SourceProposal.from_payload(payload(),inspected()),selected_url=SCORE,
        selected_sha256=hashlib.sha256(fetched(SCORE)[0]).hexdigest(),title='30 Seconds 与 20秒')
    monkeypatch.setattr(resource_tools,'fetch_public_resource',fetched)
    assert session.execute('inspect_music_resource',dict(url=SCORE,title='label',source_url=PAGE)).data['status']=='parsed'
    result=session.execute('prepare_song_preview',dict(resource_id='resource-1',start_seconds=0,duration_seconds=15))
    assert result.data['status']=='preview_ready'
    assert session.pending_song.state.playback_duration==pytest.approx(15)
    assert session.pending_song.title=='30 Seconds 与 20秒'
    session.close()
