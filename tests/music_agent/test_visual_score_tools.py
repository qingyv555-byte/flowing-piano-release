"""PDF score images reach native vision without entering plain-text history."""
import json
import struct
import zlib

import pytest

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent import resource_tools, score_reference
from ai_piano_studio.music_agent.codex_provider import CodexProvider
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from tests.music_agent.test_creation_workflow import assistant_for, done, music_scripts


def score_call(call_id='score'):
    return ProviderToolCall(call_id, 'inspect_music_resource', json.dumps(dict(
        url='https://scores.example.org/score.pdf',
        source_url='https://scores.example.org/edition', title='钢琴谱')))


@pytest.mark.parametrize('bpm', [77, 120])
def test_visual_reconstruction_repairs_missing_tempo_once_without_rewriting_notes(
    tmp_path, qapp, monkeypatch, bpm
):
    """Break caught: source-derived seconds publish with an unconfirmed default BPM."""
    from ai_piano_studio.music_agent.assistant_controller import AssistantMessageKind
    visual_source(monkeypatch)
    notes = [dict(pitch=60 + i % 5, start_time=i * .4, duration=.4, velocity=70)
             for i in range(30)]
    scripts = [
        (ProviderEvent(tool_calls=(score_call(),)),),
        (ProviderEvent(tool_calls=(ProviderToolCall('notes', 'add_notes', json.dumps(dict(notes=notes))),)),),
        (ProviderEvent(text_delta='Ready.', finish_reason='stop'),),
        # Native completion loses image context; the correction may reread the
        # same cached source before explicitly confirming even default 120 BPM.
        (ProviderEvent(tool_calls=(score_call('reread'), ProviderToolCall(
            'tempo', 'set_bpm', json.dumps(dict(bpm=bpm))))),),
    ]
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), scripts)
    provider.supports_tool_images = True
    provider.native_context_reset_required = False
    received, resets = [], []
    provider.set_tool_result_images = lambda call_id, images: received.append(call_id)
    stream = provider.stream_events
    def events(*args):
        batch = list(stream(*args))
        yield from batch
        if any(event.finish_reason == 'stop' for event in batch):
            provider.native_context_reset_required = True
    provider.stream_events = events
    def reset():
        resets.append(True)
        provider.native_context_reset_required = False
        return True
    provider.reset_for_supplement = reset
    try:
        instruction = '来一首周杰伦的烟花易冷'
        assert assistant.sendMessage(instruction)
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        state = assistant._creation_workflow.pending_song.state
        assert state.tempo.bpm == state.playback_tempo.bpm == bpm
        assert [(n.start_time, n.duration) for n in state.active_notes] == [
            (n['start_time'], n['duration']) for n in notes]
        assert received == ['score', 'reread'] and resets
        assert len(provider.requests) == 5
        assert [m.text for m in assistant.messages.entries
                if m.kind == AssistantMessageKind.USER_MESSAGE] == [instruction]
        assert assistant._creation_workflow.editor is None  # isolated editor retired
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('repair', ['missing', 'duplicate_notes'])
def test_visual_reconstruction_cannot_publish_without_confirmed_source_tempo(
    tmp_path, qapp, monkeypatch, repair
):
    """Break caught: missing tempo succeeds, or correction duplicates authored notes."""
    visual_source(monkeypatch)
    correction = (ProviderEvent(text_delta='Finished.', finish_reason='stop'),)
    if repair == 'duplicate_notes':
        correction = music_scripts()[0]
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [
        (ProviderEvent(tool_calls=(score_call(),)),), *music_scripts(),
        (ProviderEvent(text_delta='Finished.', finish_reason='stop'),), correction,
    ])
    provider.supports_tool_images = True
    provider.set_tool_result_images = lambda *args: None
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert assistant.taskProgress['status'] == 'failed'
        assert assistant.taskProgress['safeErrorCode'] == 'source_tempo_unconfirmed'
        assert not assistant.taskPreviewAvailable and not assistant.taskProjectAvailable
        assert not assistant._editor.state.active_notes
        assert assistant._creation_workflow.editor is None
        assert len(provider.requests) == 4
        assert not list((tmp_path / 'Projects').glob('*/project.pianoai.json'))
    finally:
        assistant.shutdown()


def test_tempo_correction_cannot_loop_on_source_reads(tmp_path, qapp, monkeypatch):
    """Break caught: missing metadata consumes unbounded source/provider rounds."""
    visual_source(monkeypatch)
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [
        (ProviderEvent(tool_calls=(score_call(),)),), *music_scripts(),
        (ProviderEvent(finish_reason='stop'),),
        *((ProviderEvent(tool_calls=(score_call(f'reread-{i}'),)),) for i in range(5)),
    ])
    provider.supports_tool_images = True
    provider.set_tool_result_images = lambda *args: None
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert assistant.taskProgress['safeErrorCode'] == 'source_tempo_unconfirmed'
        assert len(provider.requests) == 5
        assert not assistant.taskPreviewAvailable
    finally:
        assistant.shutdown()


def test_latest_supplement_supersedes_tempo_correction_write_restriction(tmp_path, qapp, monkeypatch):
    """Break caught: a metadata correction blocks new user-authorized note work."""
    visual_source(monkeypatch)
    extra = dict(pitch=72, start_time=31, duration=.5, velocity=55)
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [
        (ProviderEvent(tool_calls=(score_call(),)),), *music_scripts(),
        (ProviderEvent(finish_reason='stop'),),
        (ProviderEvent(tool_calls=(ProviderToolCall('stale', 'add_notes', '{}'),)),),
        (ProviderEvent(tool_calls=(ProviderToolCall('extra', 'add_notes', json.dumps(dict(notes=[extra]))),
                                  ProviderToolCall('tempo', 'set_bpm', '{"bpm":77}'))),),
    ])
    provider.supports_tool_images = True
    provider.set_tool_result_images = lambda *args: None
    create_loop = assistant._create_agent_loop
    def create(*args, **kwargs):
        loop = create_loop(*args, **kwargs)
        pending = loop._take_pending_messages
        supplied = False
        def take():
            nonlocal supplied
            if len(provider.requests) == 4 and not supplied:
                supplied = True
                return ('再加一个高音，力度55，并确认谱面速度。',)
            return pending()
        loop._take_pending_messages = take
        return loop
    monkeypatch.setattr(assistant, '_create_agent_loop', create)
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        state = assistant._creation_workflow.pending_song.state
        assert len(state.active_notes) == 91  # stale add was superseded
        assert state.active_notes[-1].pitch == 72 and state.active_notes[-1].velocity == 55
        assert state.active_notes[-1].start_time == 31
        assert state.tempo.bpm == 77
    finally:
        assistant.shutdown()


def test_original_with_visual_reference_does_not_require_source_tempo(tmp_path, qapp, monkeypatch):
    """Break caught: reconstruction metadata rule spills into original creation."""
    visual_source(monkeypatch)
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [
        (ProviderEvent(tool_calls=(score_call(),)),), *music_scripts(),
    ])
    provider.supports_tool_images = True
    provider.set_tool_result_images = lambda *args: None
    try:
        assert assistant.sendMessage('创作一首钢琴曲')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        assert assistant._creation_workflow.pending_song.state.tempo.bpm == 120
        assert len(provider.requests) == 3
    finally:
        assistant.shutdown()


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x00')) + chunk(b'IEND', b'')


def visual_source(monkeypatch):
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', lambda url, **kw:
        (b'%PDF-1.7\ncontrolled-renderer-input', url, 'application/pdf'))
    monkeypatch.setattr(score_reference, 'render_score_reference', lambda *a, **kw:
        dict(page_count=2, text='Piano score source', images=(png(),), rendered_pages=[1]))


def confirmed_music_scripts():
    # Visual transcription now requires explicit tempo confirmation. These
    # media-path fixtures author seconds at 120 BPM and confirm that metadata.
    return [*music_scripts(), (ProviderEvent(tool_calls=(ProviderToolCall(
        'tempo', 'set_bpm', '{"bpm":120}'),)),)]


def test_first_draft_page_returns_linked_pdf_images_without_another_model_round(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if url.endswith('/edition'):
            return b'<main><a href="/score.pdf">Piano PDF</a></main>', url, 'text/html'
        return b'%PDF-1.7\ncontrolled-input', url, 'application/pdf'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    monkeypatch.setattr(score_reference, 'render_score_reference', lambda *a, **kw:
        dict(page_count=1, text='score', images=(png(),), rendered_pages=[1]))
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.eager_score_reference = True
    payload = dict(url='https://scores.example.org/edition', source_url='https://scores.example.org/edition', title='Song')
    first = session.execute('inspect_music_resource', payload)
    assert first.data['status'] == 'visual_score'
    assert first.data['_provider_images'] == (png(),)
    assert first.data['source_url'] == payload['url']
    assert first.data['url'].endswith('/score.pdf')
    session.acknowledge_score_images(first.data['source_sha256'])
    second = session.execute('inspect_music_resource', payload)
    assert second.data['images_delivered'] and '_provider_images' not in second.data
    assert calls == [payload['url'], 'https://scores.example.org/score.pdf']


def test_first_draft_page_follow_is_bounded_and_keeps_failed_source_links(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        return b'<main><a href="/next.pdf">PDF</a><a href="/other.pdf">Another PDF</a></main>', url, 'text/html'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.eager_score_reference = True
    payload = dict(url='https://scores.example.org/edition', source_url='https://scores.example.org/edition', title='Song')
    result = session.execute('inspect_music_resource', payload)
    assert result.data['status'] == 'page' and len(calls) == 2
    assert result.data['download_links'] == ['https://scores.example.org/next.pdf', 'https://scores.example.org/other.pdf']


def test_ordinary_inspection_does_not_follow_page_download_links(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        return b'<main><a href="/score.pdf">PDF</a></main>', url, 'text/html'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    result = session.execute('inspect_music_resource', dict(url='https://scores.example.org/edition',
        source_url='https://scores.example.org/edition', title='Song'))
    assert result.data['status'] == 'page' and len(calls) == 1


def test_page_alias_preserves_raw_images_after_delivered_direct_source_and_native_reset(tmp_path, monkeypatch):
    def fetch(url, **kw):
        if url.endswith('/edition'):
            return b'<main><a href="/score.pdf">PDF</a></main>', url, 'text/html'
        return b'%PDF-1.7\ncontrolled-input', url, 'application/pdf'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    monkeypatch.setattr(score_reference, 'render_score_reference', lambda *a, **kw:
        dict(page_count=1, text='score', images=(png(),), rendered_pages=[1]))
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.eager_score_reference = True
    payload = dict(url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition', title='Song')
    source = session.execute('inspect_music_resource', payload)
    session.acknowledge_score_images(source.data['source_sha256'])
    page = dict(payload, url=payload['source_url'])
    assert session.execute('inspect_music_resource', page).data['images_delivered']
    session.invalidate_score_image_delivery()
    assert session.execute('inspect_music_resource', page).data['_provider_images'] == (png(),)


def test_failed_follow_retains_original_page_for_another_model_choice(tmp_path, monkeypatch):
    def fetch(url, **kw):
        if url.endswith('.pdf'):
            raise ValueError('Resource returned HTTP 403')
        return b'<main><a href="/score.pdf">PDF</a><a href="/alternative.pdf">Another PDF</a></main>', url, 'text/html'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.eager_score_reference = True
    result = session.execute('inspect_music_resource', dict(url='https://scores.example.org/edition',
        source_url='https://scores.example.org/edition', title='Song'))
    assert result.data['status'] == 'page'
    assert len(result.data['download_links']) == 2
    assert 'could not provide music data' in result.data['next_action']


def test_inspected_pdf_exposes_real_media_outside_text_feedback(tmp_path, monkeypatch):
    visual_source(monkeypatch)
    calls = []
    def render(*a, **kw):
        calls.append(kw)
        return dict(page_count=2, text='Piano score source', images=(png(),), rendered_pages=[1])
    monkeypatch.setattr(score_reference, 'render_score_reference', render)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    result = session.execute('inspect_music_resource', dict(url='https://scores.example.org/score.pdf',
        source_url='https://scores.example.org/edition', title='指定曲目的钢琴谱'))
    assert result.data['status'] == 'visual_score'
    assert result.data['_provider_images'] == (png(),)
    assert session.score_sources[0]['source_url'] == 'https://scores.example.org/edition'
    feedback = json.loads(serialize_provider_feedback(result))['data']
    assert feedback['page_count'] == 2 and feedback['rendered_pages'] == [1]
    assert '_provider_images' not in feedback
    assert calls[0]['focus_first_page'] is True


def test_native_provider_forwards_images_to_the_pending_session_only():
    received = []
    class Session:
        def set_tool_result_images(self, call_id, images):
            received.append((call_id, images))
    provider = CodexProvider('selected-model')
    provider.native_tools_enabled = True
    provider._native_session = Session()
    assert provider.supports_tool_images is True
    provider.set_tool_result_images('score-call', (png(),))
    assert received == [('score-call', (png(),))]


def test_score_images_are_sent_in_the_same_music_task_and_provenance_survives(tmp_path, qapp, monkeypatch):
    visual_source(monkeypatch)
    source = (ProviderEvent(tool_calls=(ProviderToolCall('score', 'inspect_music_resource', json.dumps(dict(
        url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition',
        title='烟花易冷 · 钢琴谱'))),)),)
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [source, *confirmed_music_scripts()])
    provider.supports_tool_images = True
    received = []
    provider.set_tool_result_images = lambda call_id, images: received.append((call_id, images))
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert received == [('score', (png(),))]
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        assert assistant.lastTurnResult['provenance']['sourceFormat'] == 'pdf_score_images'
        assert assistant.lastTurnResult['provenance']['source_url'] == 'https://scores.example.org/edition'
        assert not assistant._editor.state.active_notes
        assert '_provider_images' not in json.dumps(provider.requests)
        # Controlled notes prove the media/delivery path, not song correctness.
    finally:
        assistant.shutdown()


def test_unsupported_connection_cannot_claim_that_score_images_were_delivered(tmp_path, qapp, monkeypatch):
    visual_source(monkeypatch)
    source = (ProviderEvent(tool_calls=(ProviderToolCall('score', 'inspect_music_resource', json.dumps(dict(
        url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition',
        title='烟花易冷 · 钢琴谱'))),)),)
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [source, *music_scripts()])
    provider.supports_tool_images = False
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert assistant.lastTurnResult['provenance'].get('sourceFormat') != 'pdf_score_images'
        assert not assistant.lastTurnResult['provenance'].get('scoreReferences')
    finally:
        assistant.shutdown()


def test_pdf_download_limit_uses_actual_magic_for_opaque_endpoints(monkeypatch):
    from io import BytesIO
    from urllib.parse import urlsplit
    raw = b'%PDF-1.7\n' + b' ' * 2_100_000
    body = BytesIO(raw)
    class Response:
        status = 200
        def getheader(self, name, default=None):
            return {'Content-Length': str(len(raw)), 'Content-Type': 'application/octet-stream'}.get(name, default)
        def read(self, count):
            return body.read(count)
    class Connection:
        def __init__(self, *a, **kw): pass
        def request(self, *a, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(resource_tools, 'public_resource_address', lambda url: (urlsplit(url), '1.1.1.1'))
    monkeypatch.setattr(resource_tools, 'getproxies', lambda: {})
    monkeypatch.setattr(resource_tools.http.client, 'HTTPSConnection', Connection)
    actual, _, _ = resource_tools.fetch_public_resource('https://scores.example.org/download?id=847')
    assert actual == raw


def test_repeated_pdf_in_one_native_task_reuses_delivered_images(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append('fetch')
        return b'%PDF-1.7\ncontrolled-renderer-input', url, 'application/pdf'
    def render(*a, **kw):
        calls.append('render')
        return dict(page_count=1, text='score', images=(png(),), rendered_pages=[1])
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    monkeypatch.setattr(score_reference, 'render_score_reference', render)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    payload = dict(url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition', title='乐谱')
    first = session.execute('inspect_music_resource', payload)
    session.acknowledge_score_images(first.data['source_sha256'])
    second = session.execute('inspect_music_resource', payload)
    assert calls == ['fetch', 'render']
    assert second.data['images_delivered'] is True
    assert not second.data.get('_provider_images')
    assert len(session.score_sources) == 1
    session.close()
    assert not session._score_cache and not session._delivered_score_hashes


def test_unknown_image_ack_cannot_hide_undelivered_reference(tmp_path, monkeypatch):
    visual_source(monkeypatch)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    payload = dict(url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition', title='乐谱')
    first = session.execute('inspect_music_resource', payload)
    session.acknowledge_score_images('unrelated-source')
    second = session.execute('inspect_music_resource', payload)
    assert second.data['_provider_images'] == first.data['_provider_images']
    assert second.data.get('images_delivered') is not True


def test_native_restart_resends_cached_score_images_without_redownloading(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append('fetch')
        return b'%PDF-1.7\ncontrolled-input', url, 'application/pdf'
    def render(*a, **kw):
        calls.append('render')
        return dict(page_count=1, text='score', images=(png(),), rendered_pages=[1])
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    monkeypatch.setattr(score_reference, 'render_score_reference', render)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    payload = dict(url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition', title='乐谱')
    first = session.execute('inspect_music_resource', payload)
    session.acknowledge_score_images(first.data['source_sha256'])
    assert session.execute('inspect_music_resource', payload).data['images_delivered']
    session.invalidate_score_image_delivery()
    restarted = session.execute('inspect_music_resource', payload)
    assert restarted.success and restarted.data.get('images_delivered') is not True
    assert restarted.data['_provider_images'] == first.data['_provider_images']
    assert calls == ['fetch', 'render']


def test_gateway_acknowledges_delivered_visual_reference_once(tmp_path, qapp, monkeypatch):
    visual_source(monkeypatch)
    def source(call_id):
        return (ProviderEvent(tool_calls=(ProviderToolCall(call_id, 'inspect_music_resource', json.dumps(dict(
            url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition', title='乐谱'))),)),)
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [source('first'), source('second'), *confirmed_music_scripts()])
    provider.supports_tool_images = True
    received = []
    provider.set_tool_result_images = lambda call_id, images: received.append(call_id)
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert received == ['first']
    finally:
        assistant.shutdown()


def test_pdf_mirror_urls_do_not_rerender_or_resend_the_same_score(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append('fetch')
        return b'%PDF-1.7\nidentical-public-score-bytes', url, 'application/pdf'
    def render(*a, **kw):
        calls.append('render')
        return dict(page_count=1, text='score', images=(png(),), rendered_pages=[1])
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    monkeypatch.setattr(score_reference, 'render_score_reference', render)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    first = session.execute('inspect_music_resource', dict(url='https://scores.example.org/first.pdf',
        source_url='https://scores.example.org/edition', title='乐谱'))
    session.acknowledge_score_images(first.data['source_sha256'])
    second = session.execute('inspect_music_resource', dict(url='https://mirror.example.org/other.pdf',
        source_url='https://scores.example.org/edition', title='乐谱镜像'))
    assert calls == ['fetch', 'render', 'fetch']
    assert second.data['images_delivered'] is True and not second.data.get('_provider_images')
    assert len(session.score_sources) == 1

def test_reading_other_pages_and_crops_reuses_downloaded_score(tmp_path, monkeypatch):
    visual_source(monkeypatch)
    fetched = []
    def fetch(url, **kwargs):
        fetched.append(url)
        return b'%PDF-1.7\ncontrolled-renderer-input', url, 'application/pdf'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    payload = dict(url='https://scores.example.org/score.pdf', source_url='https://scores.example.org/edition', title='Song')
    try:
        for view in ({}, {'page_number': 2}, {'page_number': 2, 'crop': [.1, .1, .9, .9]}):
            assert session.execute('inspect_music_resource', dict(payload, **view)).success
        assert fetched == [payload['url']]
    finally:
        session.close()
