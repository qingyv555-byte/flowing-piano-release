"""Visual scores can render before approval. Only usable music artifacts are selectable."""
import json
from dataclasses import replace
import pytest
from ai_piano_studio.music_agent import resource_tools
from ai_piano_studio.music_agent.source_confirmation import SourceProposal
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME, validate_midi
from tests.music_agent.test_source_confirmation import payload
from tests.music_agent.test_visual_score_tools import png

PAGE='https://scores.example.org/page'
ARTIFACT='https://scores.example.org/score'
IDENTITY='River by Singer'


def session_for(tmp_path,monkeypatch,raw,mime):
    def fetch(url,**kwargs):
        if url==PAGE:
            return f'<html><h1>{IDENTITY}</h1><a href="{ARTIFACT}">Actual score</a></html>'.encode(),url,'text/html'
        return raw,url,mime
    monkeypatch.setattr(resource_tools,'fetch_public_resource',fetch)
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.require_source_confirmation=True
    return session


def inspect(session,url):
    return session.execute('inspect_music_resource',dict(url=url,title='Model label',source_url=PAGE))


def test_identity_page_selection_is_repairable_and_linked_png_is_selectable(tmp_path,monkeypatch):
    session=session_for(tmp_path,monkeypatch,png(),'image/png')
    assert inspect(session,PAGE).data['status']=='page'
    assert inspect(session,ARTIFACT).data['status']=='visual_score'
    proposal=payload();proposal['selected_url']=PAGE
    rejected=session.execute('propose_song_source',proposal)
    assert session.pending_source is None
    feedback=json.loads(serialize_provider_feedback(rejected))['data']
    assert feedback['status']=='unavailable'
    assert 'artifact' in (feedback.get('reason','')+' '+feedback.get('next_action','')).lower()
    assert 'select' in (feedback.get('reason','')+' '+feedback.get('next_action','')).lower()
    proposal['selected_url']=ARTIFACT
    assert session.execute('propose_song_source',proposal).data['status']=='source_confirmation'
    assert session.pending_source.selected_url==ARTIFACT
    session.close()


@pytest.mark.parametrize('kind',['midi','musicxml','mml','embedded_mml','png','pdf'])
def test_selection_eligibility_comes_from_inspected_bytes_or_successful_parse(tmp_path,monkeypatch,kind):
    midi=(CATALOG_HOME/'moonlight1.mid').read_bytes()
    variants={
        'midi':(midi,'audio/midi'),
        'musicxml':(b'<score-partwise version="4.0"></score-partwise>','application/xml'),
        'mml':(b'MML@t120o4c4d4;','text/plain'),
        'embedded_mml':(f'<h1>{IDENTITY}</h1><script>var m="MML@t120o4c4d4;";</script>'.encode(),'text/html'),
        'png':(png(),'image/png'),
        'pdf':(b'%PDF-1.4\n%%EOF','application/pdf'),
    }
    from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime
    monkeypatch.setattr(IsolatedMusicRuntime,'convert',lambda *a,**k:validate_midi(midi))
    if kind == 'pdf':
        # The signature fixture selects PDF dispatch. Renderer success is controlled independently.
        from ai_piano_studio.music_agent import score_reference
        monkeypatch.setattr(score_reference, 'render_score_reference', lambda *a, **k: dict(
            page_count=1, text='controlled notation', images=(png(),), rendered_pages=[1],
        ))
    session=session_for(tmp_path,monkeypatch,*variants[kind])
    inspect(session,PAGE);result=inspect(session,ARTIFACT)
    assert result.data['status'] in {'parsed','visual_score'}
    assert result.data['url']==ARTIFACT
    assert session.execute('propose_song_source',payload()).data['status']=='source_confirmation'
    assert session.inspected_sources[ARTIFACT].artifact_kind
    session.close()


@pytest.mark.parametrize('raw,mime',[(b'MThd invalid','audio/midi'),(b'MML@invalid;','text/plain'),(b'<score-partwise/>','application/xml'),(b'<html>not a score</html>','text/html')])
def test_failed_parse_or_identity_content_never_becomes_selectable(tmp_path,monkeypatch,raw,mime):
    from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime
    def failed(*args,**kwargs): raise ValueError('controlled parse failure')
    monkeypatch.setattr(IsolatedMusicRuntime,'convert',failed)
    session=session_for(tmp_path,monkeypatch,raw,mime)
    inspect(session,PAGE);inspect(session,ARTIFACT)
    assert session.execute('propose_song_source',payload()).data.get('status')!='source_confirmation'
    assert session.pending_source is None
    session.close()


def test_html_identity_read_cannot_satisfy_approved_music_readiness(tmp_path,monkeypatch):
    session=session_for(tmp_path,monkeypatch,png(),'image/png')
    inspect(session,PAGE);inspect(session,ARTIFACT)
    proposal=SourceProposal.from_payload(payload(),session.inspected_sources)
    page=session.inspected_sources[PAGE]
    session.approved_source=replace(proposal,selected_url=PAGE,selected_sha256=page.sha256)
    assert not session.approved_source_ready
    session.close()


def test_provider_recovers_from_identity_page_to_actual_linked_artifact(tmp_path,qapp,monkeypatch):
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.editor.controller import EditorController
    from tests.music_agent.test_source_confirmation_workflow import make_assistant, done, call
    session=session_for(tmp_path,monkeypatch,png(),'image/png')
    wrong=payload();wrong['selected_url']=PAGE
    scripts=[call('inspect_music_resource',dict(url=url,title='River',source_url=PAGE)) for url in (PAGE,ARTIFACT)]
    scripts += [call('propose_song_source',wrong),call('propose_song_source',payload())]
    assistant,_,provider=make_assistant(tmp_path,EditorController(ProjectState()),scripts)
    try:
        assert assistant.sendMessage('来一首Singer的River，15秒');done(qapp,assistant)
        assert len(provider.requests)==4
        assert assistant.songSourceConfirmation['selectedUrl']==ARTIFACT
        schema = next(tool['function'] for tool in provider.tool_schemas[0]
                      if tool['function']['name'] == 'propose_song_source')
        selected_url = schema['parameters']['properties']['selected_url']
        assert 'parsed MIDI/MusicXML/MML' in selected_url['description']
        assert 'identity-only page' in selected_url['description']
        assert any('artifact' in str(m.get('content','')).lower() for m in provider.requests[-1] if m.get('role')=='tool')
        assert not assistant.taskPreviewAvailable and not assistant._editor.state.playback_notes
    finally:
        assistant.shutdown();session.close()


def test_approved_visual_artifact_resumes_and_alternative_url_can_render(tmp_path,monkeypatch):
    from ai_piano_studio.music_agent import score_reference
    discovery=session_for(tmp_path,monkeypatch,png(),'image/png')
    inspect(discovery,PAGE);inspect(discovery,ARTIFACT)
    approved=SourceProposal.from_payload(payload(),discovery.inspected_sources)
    discovery.close()
    rendered=[]
    def render(*args,**kwargs):
        rendered.append(True)
        return dict(page_count=1,text='controlled notation',images=(png(),),rendered_pages=[1])
    monkeypatch.setattr(score_reference,'render_score_image_reference',render)
    resumed=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    resumed.require_source_confirmation=True;resumed.approved_source=approved
    assert not resumed.approved_source_ready
    result=inspect(resumed,ARTIFACT)
    assert result.data['status']=='visual_score' and result.data['_provider_images']
    assert not resumed.approved_source_ready
    resumed.acknowledge_score_images(approved.selected_sha256)
    assert resumed.approved_source_ready and rendered==[True]
    result=inspect(resumed,ARTIFACT+'?different')
    assert result.data['status']=='visual_score' and result.data['images_delivered']
    assert resumed.approved_source is approved and resumed.approved_source_ready
    assert rendered==[True]  # Identical bytes reuse the delivered image across URLs.
    resumed.close()


def test_failed_reparse_invalidates_previously_observed_redirect_alias(tmp_path,monkeypatch):
    from ai_piano_studio.music_agent.source_confirmation import InspectedSource
    session=session_for(tmp_path,monkeypatch,b'MThd invalid','audio/midi')
    inspect(session,PAGE)
    alias='https://scores.example.org/redirect'
    # Previously successful inspection, then a newer failed fetch of final URL.
    session.inspected_sources[alias]=InspectedSource(ARTIFACT,'a'*64,'',artifact_kind='parsed_midi')
    inspect(session,ARTIFACT)
    proposal=payload();proposal['selected_url']=alias
    assert session.execute('propose_song_source',proposal).data.get('status')!='source_confirmation'
    assert session.pending_source is None
    session.close()


def test_failed_approved_visual_render_does_not_grant_readiness(tmp_path,monkeypatch):
    from ai_piano_studio.music_agent import score_reference
    discovery=session_for(tmp_path,monkeypatch,png(),'image/png')
    inspect(discovery,PAGE);inspect(discovery,ARTIFACT)
    proposal=SourceProposal.from_payload(payload(),discovery.inspected_sources)
    discovery.close()
    def fail(*args,**kwargs): raise score_reference.ScoreReferenceFailed('controlled failure')
    monkeypatch.setattr(score_reference,'render_score_image_reference',fail)
    resumed=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    resumed.require_source_confirmation=True;resumed.approved_source=proposal
    result=inspect(resumed,ARTIFACT)
    assert result.data['status']=='unavailable'
    assert not resumed.approved_source_ready
    assert not resumed.inspected_sources[ARTIFACT].selectable_music
    resumed.close()
