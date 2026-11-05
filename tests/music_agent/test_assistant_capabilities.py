"""Capability access is independent from adopting changes into the current song."""
import json

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent import resource_tools, score_reference
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
from ai_piano_studio.music_agent.providers import ProviderEvent
from ai_piano_studio.music_agent.task_memory import TaskMemory
from ai_piano_studio.music_agent.tool_contracts import MusicToolResult
from tests.music_agent.test_creation_workflow import done
from tests.music_agent.test_source_confirmation_workflow import call, fetched, SCORE, PAGE
from tests.music_agent.test_studio_model_assist import studio_assistant
from tests.music_agent.test_visual_score_tools import visual_source


def test_song_task_feedback_retains_identity_section_and_real_state():
    task = dict(status='task_recorded', task_id='task-123', title='烟花易冷', artist='周杰伦',
                duration_seconds=30, section='副歌', start_seconds=82, version=4)
    feedback = json.loads(serialize_provider_feedback(MusicToolResult.success_result(
        'set_song_task', 'recorded', data=task)))
    assert feedback['data'] == task


def test_section_correction_preserves_task_and_removes_stale_time():
    memory = TaskMemory(None, 'conversation')
    memory.accept_song('听副歌30秒', '烟花易冷', '周杰伦', 30, continuing=False,
                       section='副歌', start_seconds=82)
    task = memory.state['taskId']
    memory.accept_song('改成前奏', '烟花易冷', '周杰伦', 30, continuing=True, section='前奏')
    assert memory.state['taskId'] == task
    assert memory.state['constraints']['section'] == '前奏'
    assert 'sourceStartSeconds' not in memory.state['constraints']


def test_readonly_score_views_work_before_confirmation_and_cache_per_view(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(resource_tools, 'fetch_public_resource',
                        lambda url, **kw: (b'%PDF-1.7 test', url, 'application/pdf'))

    def render(raw, home, **kw):
        seen.append(kw)
        page = kw.get('page_number', 1)
        regions = [dict(page=page, region='overview')]
        if 'crop' in kw:
            regions.append(dict(page=page, region='crop', crop=kw['crop']))
        return dict(page_count=6, text='notation', images=tuple(b'image' for _ in regions),
                    rendered_pages=[page] * len(regions), image_regions=regions)

    monkeypatch.setattr(score_reference, 'render_score_reference', render)
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    session.require_source_confirmation = True
    args = dict(url=SCORE, title='歌', source_url=PAGE)
    first = session.execute('inspect_music_resource', args)
    assert first.success and first.data['_provider_images']
    session.acknowledge_score_images(first.data['source_sha256'])
    assert session.execute('inspect_music_resource', args).data['images_delivered']
    crop = [0.1, 0.5, 0.9, 0.8]
    later = session.execute('inspect_music_resource', dict(args, page_number=4, crop=crop))
    assert later.success and later.data['_provider_images']
    assert later.data['rendered_pages'] == [4, 4]
    data = json.loads(serialize_provider_feedback(later))['data']
    assert data['image_regions'][1] == dict(page=4, region='crop', crop=crop)
    assert len(seen) == 2 and seen[1]['focus_first_page'] is False
    assert session.approved_source is None and session.pending_song is None
    session.close()


def test_page_text_and_link_cursors_recover_evidence_after_first_chunk(tmp_path, monkeypatch):
    body = '<main>' + '谱' * 4500 + 'DOWNLOAD_DETAILS' + '谱' * 8000 + 'TAIL_NOTATION' + '</main>'
    body += ''.join(f'<a href="/p{i}">link{i}</a>' for i in range(60))
    monkeypatch.setattr(resource_tools, 'fetch_public_resource',
                        lambda url, **kw: (body.encode(), url, 'text/html'))
    session = resource_tools.MusicResourceSession(tmp_path, lambda: False, lambda e: None)
    args = dict(url=PAGE, source_url=PAGE, title='歌')
    first = session.execute('inspect_music_resource', args)
    feedback = json.loads(serialize_provider_feedback(first))['data']
    assert 'DOWNLOAD_DETAILS' in feedback['untrusted_text']
    assert feedback['next_text_offset'] == 12_000 and feedback['next_link_offset'] == 40
    later = session.execute('inspect_music_resource', dict(args, text_offset=12_000, link_offset=40))
    assert 'TAIL_NOTATION' in later.data['untrusted_text']
    assert PAGE.rsplit('/', 1)[0] + '/p59' in later.data['links']
    feedback = json.loads(serialize_provider_feedback(later))['data']
    assert feedback['next_link_offset'] is None and feedback['text_offset'] == 12_000
    session.close()


def test_failed_read_is_reported_as_failed_and_model_can_recover(tmp_path, qapp, monkeypatch):
    def fetch(url, **kw):
        if url.endswith('/blocked'):
            raise OSError('unreachable')
        return fetched(SCORE)
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    before = editor.state.content, editor.historyUndoDepth
    assistant, provider, _ = studio_assistant(tmp_path, editor, [
        call('set_song_task', dict(title='云端旅行', artist='测试乐队', continuing=False, duration_seconds=15)),
        call('inspect_music_resource', dict(url=PAGE + '/blocked', source_url=PAGE, title='云端旅行')),
        call('inspect_music_resource', dict(url=SCORE, source_url=PAGE, title='云端旅行')),
        call('prepare_song_preview', dict(resource_id='resource-1', start_seconds=2, duration_seconds=15)),
    ])
    try:
        assert assistant.sendMessage('先给我云端旅行15秒试听')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        feedback = [json.loads(m['content']) for request in provider.requests for m in request
                    if m['role'] == 'tool' and 'resource_unavailable' in m['content']]
        assert feedback and all(not m['success'] for m in feedback)
        assert (editor.state.content, editor.historyUndoDepth) == before
        assert not assistant.songSourceConfirmation
    finally:
        assistant.shutdown()


def test_model_opens_song_draft_and_transcribes_without_source_approval(tmp_path, qapp, monkeypatch):
    visual_source(monkeypatch)
    fetches = []
    fetch = resource_tools.fetch_public_resource
    def counted_fetch(url, **kwargs):
        fetches.append(url)
        return fetch(url, **kwargs)
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', counted_fetch)
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    before = editor.state.content, editor.historyUndoDepth
    assistant, provider, _ = studio_assistant(tmp_path, editor, [
        call('set_song_task', dict(title='烟花易冷', artist='周杰伦', continuing=False,
                                   duration_seconds=30, section='副歌')),
        call('inspect_music_resource', dict(url=SCORE, source_url=PAGE, title='烟花易冷')),
        call('start_song_draft', {}),
        call('inspect_music_resource', dict(url=SCORE, source_url=PAGE, title='烟花易冷')),
        call('set_bpm', dict(bpm=100)),
        call('add_notes', dict(notes=[dict(pitch=60 + i % 5, start_time=i, duration=1, velocity=70)
                                     for i in range(30)])),
        (ProviderEvent(text_delta='已准备试听，需核对转写。', finish_reason='stop'),),
    ])
    provider.supports_tool_images = True
    delivered = []
    provider.set_tool_result_images = lambda call_id, images: delivered.append(call_id)
    try:
        assert assistant.sendMessage('帮我准备烟花易冷30秒副歌，周杰伦的歌')
        done(qapp, assistant)
        assert len(delivered) == 2  # replacement provider receives cached images again
        assert fetches == [SCORE]
        assert assistant.taskPreviewAvailable, (assistant.taskProgress, [m.text for m in assistant.messages.entries])
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(30)
        assert (editor.state.content, editor.historyUndoDepth) == before
        assert not assistant.songSourceConfirmation
    finally:
        assistant.shutdown()


def test_model_can_inspect_project_repeatedly_without_forced_writes(tmp_path, qapp):
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    assistant, provider, _ = studio_assistant(tmp_path, editor, [
        *[call('get_notes', dict(start_bar=1, end_bar=1)) for _ in range(9)],
        call('add_notes', dict(notes=[dict(pitch=48, start_time=0, duration=1, velocity=70)])),
        (ProviderEvent(text_delta='已添加低音提案。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('丰富一下这段旋律')
        done(qapp, assistant)
        assert assistant.lastTurnResult['status'] == 'proposal'
        assert assistant.lastTurnResult['readCount'] >= 9
        assert not any('Use the available music tools now' in json.dumps(r) for r in provider.requests)
    finally:
        assistant.shutdown()
