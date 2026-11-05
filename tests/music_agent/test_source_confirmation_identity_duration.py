"""Keep song identity and source metadata opaque to excerpt-duration parsing."""
from dataclasses import replace
import pytest
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds, normalize_excerpt_instruction
from tests.music_agent.test_source_confirmation_workflow import make_assistant, done, discovery, fetched


@pytest.mark.parametrize('text,identity,seconds',[
 ('来一首《60秒》，试听15秒','《60秒》',15),
 ('来一首30 Seconds to Mars的The Kill，15秒','30 Seconds to Mars',15),
 ('来一首30 Seconds to Mars的The Kill','30 Seconds to Mars',15),
 ('来一首《601秒》','《601秒》',15),
 ('来一首《60秒》，试听15秒\n来源：https://scores.example.org/601s','https://scores.example.org/601s',15),
 ('来一首《60秒》，试听15秒\n来源：https://scores.example.org/601s\n用户纠正：改成30秒','《60秒》',30),
])
def test_duration_parser_preserves_identity_and_ignores_source_urls(text,identity,seconds):
    assert requested_excerpt_seconds(text)==seconds
    assert identity in normalize_excerpt_instruction(text)


@pytest.mark.parametrize('text',[
 '来一首《60秒》，试听15秒',
 '来一首30 Seconds to Mars的The Kill，15秒',
])
def test_production_creation_passes_original_identity_to_resolver_and_model(tmp_path,qapp,monkeypatch,text):
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetched)
    assistant,_,provider=make_assistant(tmp_path,EditorController(ProjectState()),discovery())
    seen=[]
    resolver=assistant._creation_workflow.resolver
    original=resolver.resolve
    def capture(instruction,**kwargs):
        seen.append(instruction)
        return original(instruction,**kwargs)
    monkeypatch.setattr(resolver,'resolve',capture)
    try:
        assert assistant.sendMessage(text);done(qapp,assistant)
        assert seen==[text]
        assert assistant._retry_instruction==text
        assert assistant.songSourceConfirmation['durationSeconds']==15
        assert any(text in str(message.get('content','')) for message in provider.requests[0])
    finally: assistant.shutdown()


@pytest.mark.parametrize('correction,seconds',[('不是这首，重新找',15),('不是这首，改成30秒',30)])
def test_source_url_is_not_a_new_duration_on_rejection(tmp_path,qapp,monkeypatch,correction,seconds):
    from tests.music_agent.test_source_confirmation_workflow import SCORE
    def different_edition(url, **kwargs):
        raw, final, mime = fetched(url, **kwargs)
        if url == SCORE:
            ppq = 480 if int.from_bytes(raw[12:14], 'big') != 480 else 960
            raw = raw[:12] + ppq.to_bytes(2, 'big') + raw[14:]
        return raw, final, mime
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',different_edition)
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()),discovery())
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲，15秒');done(qapp,assistant)
        flow=assistant._song_source_flow
        flow.pending=replace(flow.pending,proposal=replace(flow.pending.proposal,selected_url='https://scores.example.org/601s'))
        assert assistant.sendMessage(correction);done(qapp,assistant)
        assert assistant.songSourceConfirmation['durationSeconds']==seconds
        assert 'https://scores.example.org/601s' in assistant._retry_instruction
        assert '月光奏鸣曲，15秒' in assistant._retry_instruction
    finally: assistant.shutdown()


def test_artist_after_explicit_duration_is_not_a_second_directive():
    text='来一段15秒的30 Seconds to Mars的The Kill'
    assert requested_excerpt_seconds(text)==15
    assert '30 Seconds to Mars' in normalize_excerpt_instruction(text)


def test_confirmed_model_preview_uses_excerpt_duration_not_numeric_title(tmp_path,qapp,monkeypatch):
    from tests.music_agent.test_source_confirmation_workflow import call, SCORE, PAGE
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetched)
    scripts=discovery()+[
        call('inspect_music_resource',dict(url=SCORE,title='云端旅行',source_url=PAGE)),
        call('add_notes',dict(notes=[dict(pitch=60+i%4,start_time=i*.5,duration=.5,velocity=70) for i in range(30)])),
    ]
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()),scripts)
    try:
        assert assistant.sendMessage('来一首《60秒》，试听15秒');done(qapp,assistant)
        assert assistant.confirmSongSource();done(qapp,assistant)
        assert assistant.taskPreviewAvailable
        assert assistant.taskPreviewInfo['durationSeconds']==pytest.approx(15)
    finally: assistant.shutdown()


@pytest.mark.parametrize('answer,approved',[
 ('是这首',True),('对',True),('是的',True),('没错',True),('就是这首，继续',True),
 ('现在找到什么了？',False),('你好',False),('是这首吗？',False),('继续',False),
])
def test_pending_source_accepts_only_explicit_affirmatives(tmp_path,qapp,answer,approved):
    assistant,_,_=make_assistant(tmp_path,EditorController(ProjectState()))
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲，15秒');done(qapp,assistant)
        assert assistant.songSourceConfirmation
        generation=assistant._generation
        assert assistant.sendMessage(answer);done(qapp,assistant)
        if not approved:
            assert assistant._generation==generation
        assert assistant.taskPreviewAvailable is approved
        assert bool(assistant.songSourceConfirmation) is not approved
        assert not assistant._editor.state.playback_notes
    finally: assistant.shutdown()


def test_earlier_preview_verb_does_not_turn_later_artist_into_duration():
    text='试听一段15秒的30 Seconds to Mars的The Kill'
    assert requested_excerpt_seconds(text)==15
    assert '30 Seconds to Mars' in normalize_excerpt_instruction(text)
