import json
from types import SimpleNamespace

import pytest

from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.resource_tools import MusicResourceSession
from ai_piano_studio.score_lab.contracts import LabFailure
from ai_piano_studio.score_lab.service import ScoreLabService


PAGE = 'https://music.example/song'
IMAGE = 'https://music.example/score.png'
from test_source_inspection import png, cli
PNG = png()


def proposal(selected=IMAGE):
    return dict(title='Fixture song',artist='Fixture artist',edition='Piano edition',section='Visible page beginning',
                selected_url=selected,start_seconds=0,evidence=[dict(url=PAGE,quote='Fixture artist')],summary='Actual linked score',uncertainty='Unverified edition')


def call(name, payload):
    return ProviderToolCall(name,name,json.dumps(payload))


class Provider:
    def __init__(self, turns, observed=True):
        self.turns = iter(turns)
        self._search_observed = observed
        self.closed = False
        self.requests = []

    def stream_events(self, messages, tools, reasoning, attachments, **kwargs):
        self.requests.append((list(messages),tools,attachments))
        yield ProviderEvent(tool_calls=tuple(next(self.turns)))

    def close(self):
        self.closed = True


@pytest.fixture
def resources(monkeypatch):
    monkeypatch.setattr("ai_piano_studio.score_lab.visual_preflight.find_codex",lambda:"injected-codex")
    monkeypatch.setattr("ai_piano_studio.score_lab.visual_preflight.owned_cli",cli())
    monkeypatch.setattr("ai_piano_studio.music_agent.score_reference.render_score_image_reference",
        lambda *a,**k:dict(images=(PNG,),text="",page_count=1,rendered_pages=[1],source_dimensions=[600,800]))
    def fetch(url, **kwargs):
        if url == PAGE:
            return f'<html><title>Fixture artist</title><a href="{IMAGE}">score</a></html>'.encode(),url,'text/html'
        return PNG,url,'image/png'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    sessions = []
    def factory(*args):
        session = MusicResourceSession(*args)
        sessions.append(session)
        return session
    return factory,sessions


def inspected_turns():
    return [[call('inspect_music_resource',dict(url=PAGE,title='Fixture',source_url=PAGE)),
             call('inspect_music_resource',dict(url=IMAGE,title='Fixture',source_url=PAGE))]]


def test_actual_inspection_terminal_wait_no_draft_and_only_two_tools(tmp_path,resources):
    factory,sessions = resources
    provider = Provider([*inspected_turns(),[call('propose_song_source',proposal())]])
    service = ScoreLabService(tmp_path,provider_factory=lambda model:provider,resource_factory=factory)
    candidate = service.discover('Fixture artist',12)
    assert candidate.proposal.selected_url == IMAGE
    assert len(candidate.proposal.selected_sha256) == 64
    assert candidate.public()['durationSeconds'] == 12
    assert provider.closed and not sessions[0].inspected_sources
    assert not list(tmp_path.rglob('project.json'))
    assert {t['function']['name'] for t in provider.requests[0][1]} == {'inspect_music_resource','propose_song_source'}
    assert all(not r[2] for r in provider.requests)


def test_ordinary_page_and_model_approval_rejected_then_corrected(tmp_path,resources):
    factory,_ = resources
    provider = Provider([*inspected_turns(),[call('approve_source',{})],
                         [call('propose_song_source',proposal(PAGE))], [call('propose_song_source',proposal())]])
    candidate = ScoreLabService(tmp_path,provider_factory=lambda _:provider,resource_factory=factory).discover('Fixture')
    assert candidate.proposal.selected_url == IMAGE
    assert any('invalid_source' in m.get('content','') for m in provider.requests[-1][0])


def test_rejected_sources_and_unobserved_search_fail(tmp_path,resources):
    factory,_ = resources
    for rejected, observed in [((IMAGE,),True),((),False)]:
        provider = Provider([*inspected_turns(),[call('propose_song_source',proposal())],[]],observed)
        with pytest.raises(LabFailure):
            ScoreLabService(tmp_path,provider_factory=lambda _:provider,resource_factory=factory).discover('Fixture',rejected_urls=rejected)
        assert provider.closed


def test_duration_directive_wins_numeric_title_and_out_of_scope(tmp_path,resources):
    factory,_ = resources
    def service():
        p = Provider([*inspected_turns(),[call('propose_song_source',proposal())]])
        return ScoreLabService(tmp_path,provider_factory=lambda _:p,resource_factory=factory)
    assert service().discover('Fixture 试听 8秒',20).duration_seconds == 8
    assert service().discover('《30 Seconds to Mars》',12).duration_seconds == 12
    with pytest.raises(LabFailure,match='5–30'):
        service().discover('Fixture 试听 60秒')


def test_cancel_closes_discovery(tmp_path,resources):
    factory,sessions = resources
    p = Provider([])
    with pytest.raises(LabFailure) as error:
        ScoreLabService(tmp_path,provider_factory=lambda _:p,resource_factory=factory).discover('Fixture',cancelled=lambda:True)
    assert error.value.code == 'cancelled' and p.closed


@pytest.mark.parametrize('user_cancelled', [False, True])
def test_same_batch_resource_deadline_stops_before_proposal_and_cleans_up(tmp_path,monkeypatch,user_cancelled):
    clock = [0.0]
    cancelled = [False]
    monkeypatch.setattr('ai_piano_studio.score_lab.discovery.time.monotonic',lambda:clock[0])
    def fetch(url,**kwargs):
        if url == PAGE:
            return f'<html>Fixture artist<a href="{IMAGE}">score</a></html>'.encode(),url,'text/html'
        # The batch starts on time but its second real inspection crosses deadline.
        clock[0] = 241.0
        cancelled[0] = user_cancelled
        return PNG,url,'image/png'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    sessions = []
    def resource_factory(*args):
        s = MusicResourceSession(*args)
        sessions.append(s)
        return s
    p = Provider([[*inspected_turns()[0],call('propose_song_source',proposal())]])
    svc = ScoreLabService(tmp_path,provider_factory=lambda _:p,resource_factory=resource_factory)
    with pytest.raises(LabFailure) as error:
        svc.discover('Fixture',cancelled=lambda:cancelled[0])
    assert error.value.code == ('cancelled' if user_cancelled else 'timeout')
    assert p.closed and not sessions[0].inspected_sources
    assert sessions[0].cancelled is p.is_cancelled
    assert svc._candidate is None
    assert not list(tmp_path.rglob('project.json'))


def test_terminal_proposal_validation_crossing_deadline_cannot_return_candidate(tmp_path,resources,monkeypatch):
    from ai_piano_studio.music_agent.source_confirmation import SourceProposal
    clock = [0.0]
    monkeypatch.setattr('ai_piano_studio.score_lab.discovery.time.monotonic',lambda:clock[0])
    original = SourceProposal.from_payload
    def late_validation(payload,inspected):
        result = original(payload,inspected)
        clock[0] = 241.0
        return result
    monkeypatch.setattr(SourceProposal,'from_payload',late_validation)
    factory,sessions = resources
    p = Provider([[*inspected_turns()[0],call('propose_song_source',proposal())]])
    svc = ScoreLabService(tmp_path,provider_factory=lambda _:p,resource_factory=factory)
    with pytest.raises(LabFailure) as error:
        svc.discover('Fixture')
    assert error.value.code == 'timeout'
    assert p.closed and not sessions[0].inspected_sources
    assert svc._candidate is None
