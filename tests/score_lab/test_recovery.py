import pytest
from ai_piano_studio.score_lab.contracts import LabFailure, validate_transcription

def test_captured_low_resolution_reason_survives():
    with pytest.raises(LabFailure) as e:
        validate_transcription(dict(can_read=False, uncertainty=['图片分辨率过低，无法可靠辨认印刷速度、双手音高、节奏及连音线，不能完成转录。']))
    assert '分辨率过低' in e.value.message
    assert e.value.public()['code'] == 'unreadable'

def test_public_failure_is_bounded_and_redacts_credentials():
    e = LabFailure('unreadable', 'low resolution api_key=secret-value', stage='transcribing', sourceUrl='https://example.org/a?token=secret', sourceSha256='a'*64)
    assert 'secret' not in str(e.public())
    assert e.public()['sourceSha256'] == 'a'*64


def test_captured_failure_model_changes_source_with_live_events(tmp_path, monkeypatch, qtbot):
    import hashlib
    import json
    from ai_piano_studio.score_lab.service import ScoreLabService
    from test_discovery import Provider, call, proposal, PAGE, IMAGE
    from test_source_inspection import png
    raw=png(200,260)
    new='https://music.example/new.mml'; copied='https://music.example/copy.png'
    checked=[]
    def fetch(url,**kw):
        checked.append(url)
        if url==PAGE:
            return f'<html>Fixture artist<a href="{IMAGE}">score</a><a href="{new}">score</a><a href="{copied}">copy</a></html>'.encode(),url,'text/html'
        if url==new: return b'MML@t120o4c4d4,t120o3c2;',url,'text/plain'
        return raw,url,'image/png'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    def inspect(url): return call('inspect_music_resource',dict(url=url,title='Fixture',source_url=PAGE))
    provider=Provider([[inspect(PAGE),inspect(IMAGE)],
        [call('propose_song_source',proposal()),inspect(copied)],
        [inspect(new),call('propose_song_source',proposal(new))]])
    events=[]
    service=ScoreLabService(tmp_path,provider_factory=lambda _:provider,
        cli_runner=lambda *a,**k:pytest.fail('Known thumbnail must not reach vision'))
    candidate=service.discover('Fixture 20秒',progress=events.append)
    assert candidate.proposal.selected_url==new and candidate.duration_seconds==20
    feedback=[json.loads(m['content']) for m in provider.requests[-1][0] if m['role']=='tool']
    first=next(row for row in feedback if 'visual_inspection' in row.get('data',{}))
    assert first['success'] is False
    assert first['data']['visual_inspection']['dimensions']==[[200,260]]
    assert first['data']['visual_inspection']['sourceSha256']==hashlib.sha256(raw).hexdigest()
    assert first['data']['visual_inspection']['sourceUrl']==IMAGE
    assert any(e['stage']=='rejected' and e['url']==copied for e in events)
    assert provider.closed and not list(tmp_path.rglob('project.json'))


@pytest.mark.parametrize('text', [
    'Authorization: Bearer example-private-value',
    'authorization: bearer example-private-value',
    'Bearer example-private-value',
    'Authorization: Basic example-private-value',
])
def test_standard_authorization_and_standalone_bearer_are_fully_redacted(text):
    e = LabFailure('unreadable', text)
    assert 'example-private-value' not in e.message
    assert 'example-private-value' not in str(e.public())
    assert '[redacted]' in e.message


def test_failed_actual_inspections_survive_two_rounds_without_refetch(tmp_path, monkeypatch, qtbot):
    import hashlib
    import json
    from ai_piano_studio.score_lab.controller import ScoreLabController
    from ai_piano_studio.score_lab.service import ScoreLabService
    from test_discovery import Provider, call, proposal, PAGE
    failed = 'https://music.example/bad.mml?token=example-private-value'
    final = 'https://music.example/final.mml?token=example-private-value'
    copied = 'https://music.example/copied.mml'
    good = 'https://music.example/good.mml'
    bad_bytes = b'MML@this-is-not-valid-music'
    digest = hashlib.sha256(bad_bytes).hexdigest()
    fetched = []
    def fetch(url, **kw):
        fetched.append(url)
        if url == PAGE:
            return f'<html>Fixture artist<a href="{failed}">score</a><a href="{copied}">copy</a><a href="{good}">score</a></html>'.encode(), url, 'text/html'
        if url == good:
            return b'MML@t120o4c4d4,t120o3c2;',url,'text/plain'
        return bad_bytes, final if url == failed else url, 'text/plain'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    def inspect(url):
        return call('inspect_music_resource',dict(url=url,title='Fixture',source_url=PAGE))
    first = Provider([[inspect(PAGE), inspect(failed)], []])
    second = Provider([[inspect(PAGE), inspect(failed), inspect(final), inspect(copied),call('propose_song_source',proposal(copied))], []])
    third = Provider([[inspect(PAGE),inspect(copied),inspect(good),call('propose_song_source',proposal(good))]])
    providers = iter([first,second,third])
    svc = ScoreLabService(tmp_path,provider_factory=lambda _:next(providers))
    c = ScoreLabController(tmp_path,None,service=svc)
    rows = []
    c.changed.connect(lambda: rows.extend(c.liveCandidates))
    try:
        c.findSong('Fixture 20秒',15)
        qtbot.waitUntil(lambda:not c.busy,timeout=5000)
        assert c.state == 'awaiting_confirmation' and c.recoveryRounds == 2
        assert c.candidate['selectedUrl'] == good and not c.draft
        assert fetched.count(failed) == 1 and fetched.count(final) == 0
        assert fetched.count(copied) == 1 and fetched.count(PAGE) == 3
        for provider in (second,third):
            context = json.loads(provider.requests[0][0][1]['content'])
            assert failed in context['rejected_urls'] and final in context['rejected_urls']
            assert PAGE not in context['rejected_urls'] and digest in context['rejected_hashes']
            checked = context['failure_evidence_untrusted']['checked_sources']
            assert any(r['outcome']=='rejected' and r['sourceSha256']==digest for r in checked)
            assert any(r['status']=='page' and r['outcome']=='checked' for r in checked)
        last_context = json.loads(third.requests[0][0][1]['content'])
        assert copied in last_context['rejected_urls']
        assert any(r['stage']=='rejected' and r['url'].endswith('/bad.mml') for r in rows)
        assert 'example-private-value' not in str(rows)
        assert all(p.closed for p in (first,second,third))
    finally:
        c.shutdown()


def test_independent_requests_restart_exclusion_lifetime_after_many_failures(tmp_path,monkeypatch,qtbot):
    import json
    from ai_piano_studio.score_lab.controller import ScoreLabController
    from ai_piano_studio.score_lab.service import ScoreLabService
    from test_discovery import Provider, call, PAGE
    fetched=[]
    def fetch(url,**kw):
        fetched.append(url)
        return b'MML@invalid-music',url.replace('/request/', '/final/'),'text/plain'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    providers=[]
    for request in range(6):
        for round in range(3):
            calls=[call('inspect_music_resource',dict(url=f'https://music.example/request/{request}/{round}/{i}.mml',title='Fixture',source_url=PAGE)) for i in range(8)]
            providers.append(Provider([calls,[]]))
    remaining=iter(providers)
    c=ScoreLabController(tmp_path,None,service=ScoreLabService(tmp_path,provider_factory=lambda _:next(remaining)))
    try:
        for request in range(5):
            c.findSong(f'Independent song {request}',20)
            qtbot.waitUntil(lambda:not c.busy,timeout=5000)
            assert c.state=='error' and c.failure['code']=='no_source'
            assert c.recoveryRounds==2
            start=json.loads(providers[request*3].requests[0][0][1]['content'])
            assert start['rejected_urls']==[] and start['rejected_hashes']==[]
            assert len(c._rejected)==48  # All aliases retained within each request.
        assert len(set(fetched))==120  # 240 historical requested/final URL identities.
        c.cancel()
        c.findSong('Restart after more than 192 historical exclusions',20)
        qtbot.waitUntil(lambda:not c.busy,timeout=5000)
        assert c.failure['code']=='no_source' and c.recoveryRounds==2
        assert providers[15].requests and len(c._rejected)==48
        assert all(p.closed for p in providers)
    finally:
        c.shutdown()
