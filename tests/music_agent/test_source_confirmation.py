"""Explicit confirmation records identity independently of isolated draft preparation."""
import hashlib
import pytest
from ai_piano_studio.music_agent.source_confirmation import SourceProposal, InspectedSource


def payload(**changes):
    return dict(title='River', artist='Singer', edition='piano arrangement', section='opening',
                selected_url='https://scores.example.org/score', start_seconds=0,
                evidence=[dict(url='https://scores.example.org/page', quote='River by Singer')],
                summary='The page names the work and artist.', uncertainty='Edition is unverified.', **changes)


def inspected():
    return {'https://scores.example.org/page': InspectedSource('https://scores.example.org/page', 'a'*64, 'River by Singer — piano', ('https://scores.example.org/score',)),
            'https://scores.example.org/score': InspectedSource('https://scores.example.org/score', 'b'*64, '', artifact_kind='parsed_midi')}


def test_proposal_binds_inspected_content_and_selected_bytes():
    proposal = SourceProposal.from_payload(payload(), inspected())
    assert proposal.selected_sha256 == 'b'*64
    assert proposal.public()['evidence'][0]['quote'] == 'River by Singer'
    assert proposal.matches('https://scores.example.org/score', 'b'*64)
    assert not proposal.matches('https://scores.example.org/score', 'c'*64)


@pytest.mark.parametrize('change', [dict(approved=True), dict(selected_url='https://unread.example.org/score'),
    dict(evidence=[dict(url='https://scores.example.org/page', quote='Invented artist')]),
    dict(evidence=[dict(url='https://unread.example.org/page', quote='River by Singer')]),
    dict(title='x'*161), dict(start_seconds=float('nan'))])
def test_untrusted_proposal_cannot_forge_approval_or_evidence(change):
    data = payload(); data.update(change)
    with pytest.raises(ValueError):
        SourceProposal.from_payload(data, inspected())


def test_single_identity_evidence_retains_explicit_uncertainty():
    data = payload(); data['uncertainty'] = ''
    proposal = SourceProposal.from_payload(data, inspected())
    assert '独立' in proposal.uncertainty


def test_resource_proposal_requires_real_page_identity_after_optional_preview(tmp_path, monkeypatch):
    from ai_piano_studio.music_agent import resource_tools as module
    from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
    session = module.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.require_source_confirmation = True
    def fetch(url, **kw):
        return (b'<html><title>River by Singer</title><a href="/score">Score</a></html>', url, 'text/html') if url.endswith('/page') else (
            (CATALOG_HOME / 'moonlight1.mid').read_bytes(), url, 'audio/midi')
    monkeypatch.setattr(module, 'fetch_public_resource', fetch)
    for suffix in ('page', 'score'):
        result = session.execute('inspect_music_resource', dict(url='https://scores.example.org/'+suffix,
            title='model label', source_url='https://scores.example.org/forged-origin'))
        assert result.success
    preview = session.execute('prepare_song_preview', dict(resource_id='resource-1', start_seconds=0, duration_seconds=15))
    assert preview.data['status'] == 'preview_ready'
    assert preview.data['current_project_modified'] is False
    assert 'confirmedIdentity' not in session.pending_song.provenance
    assert session.approved_source is None
    proposed = session.execute('propose_song_source', payload())
    assert proposed.data['status'] == 'source_confirmation'
    assert session.pending_song is None
    proposal = session.pending_source
    assert proposal.title == 'River'
    assert not session.approved_source_ready
    session.approved_source = proposal
    assert session.approved_source_ready
    ready = session.execute('prepare_song_preview', dict(resource_id='resource-1', start_seconds=0, duration_seconds=15))
    assert ready.data['status'] == 'preview_ready'
    assert session.pending_song.state.playback_duration == pytest.approx(15)
    assert session.pending_song.title == 'River'
    assert session.pending_song.provenance['source_url'] == proposal.selected_url
    session.close()
    assert session.pending_source is None and not session.inspected_sources


@pytest.mark.parametrize('download_url', ['https://scores.example.org/score', 'https://scores.example.org/other'])
def test_alternate_preview_records_actual_source_and_interval_without_replacing_approval(tmp_path, monkeypatch, download_url):
    from ai_piano_studio.music_agent import resource_tools as module
    from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
    session = module.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.require_source_confirmation = True
    approved = SourceProposal.from_payload(payload(), inspected())
    session.approved_source = approved
    raw = (CATALOG_HOME / 'moonlight1.mid').read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest != approved.selected_sha256
    monkeypatch.setattr(module, 'fetch_public_resource', lambda url, **kw: (raw, url, 'audio/midi'))
    source = session.execute('inspect_music_resource', dict(
        url=download_url, title='Alternate rendition', source_url='https://scores.example.org/page',
    ))
    assert source.data['status'] == 'parsed'
    assert not session.approved_source_ready
    result = session.execute('prepare_song_preview', dict(
        resource_id=source.data['resource_id'], start_seconds=10, duration_seconds=15,
    ))
    assert result.data['status'] == 'preview_ready'
    assert result.data['current_project_modified'] is False
    provenance = session.pending_song.provenance
    assert provenance['download_url'] == download_url
    assert provenance['sha256'] == digest
    assert provenance['sourceStartSeconds'] == 10
    assert 'confirmedIdentity' not in provenance
    assert session.approved_source is approved and not session.approved_source_ready
    session.close()


def test_discovery_proposal_is_terminal_zero_write_and_closes_provider(qapp, tmp_path):
    import json
    from ai_piano_studio.music_agent.agent_loop import MusicAgentLoop
    from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicExecutionMode, MusicToolResult
    from ai_piano_studio.music_agent.resource_tools import with_music_resources
    from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
    from tests.music_agent.test_agent_loop import _editor, _ScriptedProvider, _StaticContextBuilder
    editor = _editor(); before = editor.state
    gateway = MusicAgentToolGateway(editor)
    real_execute = gateway.execute
    gateway.execute = lambda name, args, context: MusicToolResult.success_result(name, 'waiting', data={'status':'source_confirmation'}) if name == 'propose_song_source' else real_execute(name,args,context)
    provider = _ScriptedProvider([(ProviderEvent(tool_calls=(ProviderToolCall('p','propose_song_source', json.dumps(payload())),)),)])
    provider.closed = False
    provider.close = lambda: setattr(provider, 'closed', True)
    context = gateway.create_execution_context(authorized_bars=(), permissions=frozenset(MusicPermission),
        mode=MusicExecutionMode.COLLABORATION, intent_template='source_discovery')
    loop = MusicAgentLoop(provider=provider, registry=with_music_resources(gateway.registry), gateway=gateway,
        context_builder=_StaticContextBuilder(), model_id='scripted')
    outcome = loop.run('Find River',context,intent='CREATE_MUSIC',intent_template='source_discovery')
    assert outcome.status == 'source_confirmation'
    assert provider.closed and len(provider.requests) == 1
    assert editor.state == before


def test_discovery_text_finish_does_not_force_notes(qapp):
    from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicExecutionMode
    from ai_piano_studio.music_agent.providers import ProviderEvent
    from tests.music_agent.test_agent_loop import _editor, _ScriptedProvider, _loop
    editor = _editor(); gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider([(ProviderEvent(text_delta='Identity remains unclear.'),)])
    context = gateway.create_execution_context(authorized_bars=(),permissions=frozenset(MusicPermission),
        mode=MusicExecutionMode.COLLABORATION,intent_template='source_discovery')
    outcome = _loop(provider,gateway).run('Find River',context,intent='CREATE_MUSIC',intent_template='source_discovery')
    assert outcome.status == 'needs_input'
    assert len(provider.requests) == 1


def test_identity_page_cannot_authorize_unrelated_score():
    data = inspected()
    data['https://scores.example.org/page'] = InspectedSource('https://scores.example.org/page','a'*64,'River by Singer')
    with pytest.raises(ValueError, match='link'):
        SourceProposal.from_payload(payload(), data)


def test_explicit_excerpt_duration_wins():
    from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds
    assert requested_excerpt_seconds('弹一段歌曲') == 15
    assert requested_excerpt_seconds('弹30秒的歌曲') == 30


def test_discovery_ignores_forged_write_tool_even_if_provider_calls_it(qapp):
    import json
    from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicExecutionMode
    from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
    from tests.music_agent.test_agent_loop import _editor, _ScriptedProvider, _loop
    editor = _editor(); before = editor.state; gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider([(ProviderEvent(tool_calls=(ProviderToolCall('evil','set_bpm',json.dumps({'bpm':95})),)),)])
    context = gateway.create_execution_context(authorized_bars=(),permissions=frozenset(MusicPermission),
        mode=MusicExecutionMode.COLLABORATION,intent_template='source_discovery')
    outcome = _loop(provider,gateway).run('Find River',context,intent='CREATE_MUSIC',intent_template='source_discovery')
    assert outcome.status == 'source_confirmation_required'
    assert editor.state == before


def test_visual_discovery_can_render_before_confirmation(tmp_path, monkeypatch):
    from ai_piano_studio.music_agent import resource_tools as module
    from ai_piano_studio.music_agent import score_reference
    from tests.music_agent.test_visual_score_tools import png
    session = module.MusicResourceSession(tmp_path, lambda:False, lambda e:None)
    session.require_source_confirmation = True
    monkeypatch.setattr(module,'fetch_public_resource',lambda url,**kw:(b'%PDF-fake',url,'application/pdf'))
    # The signature selects PDF dispatch. Rendering behavior is controlled.
    monkeypatch.setattr(score_reference, 'render_score_reference', lambda *a, **k: dict(
        page_count=1, text='controlled notation', images=(png(),), rendered_pages=[1],
    ))
    result = session.execute('inspect_music_resource',dict(url='https://scores.example.org/score',title='River',source_url='https://scores.example.org/page'))
    assert result.data['status'] == 'visual_score'
    assert result.data['_provider_images'] and session.score_sources
    assert session.approved_source is None and session.pending_song is None
    session.close()


def test_proposal_follows_only_observed_resource_redirects():
    sources = inspected()
    selected = sources.pop('https://scores.example.org/score')
    final = InspectedSource('https://cdn.example.org/music', selected.sha256, '', artifact_kind='parsed_midi')
    sources['https://scores.example.org/score'] = final
    sources[final.url] = final
    proposal = SourceProposal.from_payload(payload(), sources)
    assert proposal.selected_url == final.url
    assert proposal.matches(final.url, final.sha256)


def test_new_proposal_clears_session_approval(tmp_path):
    from ai_piano_studio.music_agent.resource_tools import MusicResourceSession
    session = MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    session.inspected_sources.update(inspected())
    session.approved_source = SourceProposal.from_payload(payload(), inspected())
    assert session.approved_source_ready
    assert session.execute('propose_song_source',payload()).data['status'] == 'source_confirmation'
    assert session.approved_source is None and not session.approved_source_ready
