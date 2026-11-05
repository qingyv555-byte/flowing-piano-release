"""Draft transitions retain inspected evidence and accept newer user directions."""
import json
from threading import Event

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent import resource_tools
from ai_piano_studio.music_agent.task_memory import TaskMemory
from tests.music_agent.test_assistant_controller import _wait_until
from tests.music_agent.test_creation_workflow import done
from tests.music_agent.test_source_confirmation_workflow import call, fetched, discovery, SCORE, PAGE
from tests.music_agent.test_studio_model_assist import studio_assistant


def test_inspected_resource_id_survives_start_song_draft_without_refetch(tmp_path, qapp, monkeypatch):
    fetched_urls = []
    def fetch(url, **kwargs):
        fetched_urls.append(url)
        return fetched(url, **kwargs)
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetch)
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    before = editor.state.content
    assistant, provider, _ = studio_assistant(tmp_path, editor, [
        call('set_song_task', dict(title='云端旅行', artist='测试乐队', continuing=False, duration_seconds=30)),
        call('inspect_music_resource', dict(url=SCORE, source_url=PAGE, title='云端旅行')),
        call('start_song_draft', {}),
        call('read_resource_notes', dict(resource_id='resource-1', offset=0)),
        call('prepare_song_preview', dict(resource_id='resource-1', start_seconds=10, duration_seconds=30)),
    ])
    try:
        assert assistant.sendMessage('帮我准备云端旅行30秒试听')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(30)
        assert fetched_urls == [SCORE]
        assert 'resource-1' in provider.requests[3][-1]['content']
        assert editor.state.content == before
        assert not assistant._resource_sessions
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('model_duration', [20, 30, None])
def test_busy_song_supplement_updates_duration_and_section_in_same_draft(tmp_path, qapp, monkeypatch, model_duration):
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetched)
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    before = editor.state.content
    assistant, provider, _ = studio_assistant(tmp_path, editor, [
        call('set_song_task', dict(title='云端旅行', artist='测试乐队', continuing=False,
                                  duration_seconds=30, section='副歌', start_seconds=30)),
        call('start_song_draft', {}),
        call('get_project_summary', {}),  # superseded while the provider is busy
        call('set_song_task', dict(title='云端旅行', artist='测试乐队', continuing=True,
                                  **({'duration_seconds': model_duration} if model_duration is not None else {}),
                                  section='主歌', start_seconds=8)),
        call('inspect_music_resource', dict(url=SCORE, source_url=PAGE, title='云端旅行')),
        call('prepare_song_preview', dict(resource_id='resource-1', start_seconds=8, duration_seconds=30)),
    ])
    entered, release = Event(), Event()
    stream = provider.stream_events
    def blocked(*args):
        if len(provider.requests) == 2:
            entered.set()
            assert release.wait(5)
        yield from stream(*args)
    provider.stream_events = blocked
    try:
        assert assistant.sendMessage('帮我准备云端旅行30秒副歌试听')
        _wait_until(qapp, entered.is_set)
        assert assistant.sendMessage('改为20秒，取主歌')
        release.set()
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(20)
        constraints = assistant._task_memory.state['constraints']
        assert constraints['section'] == '主歌' and constraints['sourceStartSeconds'] == 8
        assert constraints['targetSeconds'] == 20
        assert '改为20秒' in json.dumps(provider.requests[3], ensure_ascii=False)
        assert editor.state.content == before
    finally:
        release.set()
        assistant.shutdown()


def test_changed_song_identity_does_not_inherit_previous_section_or_timestamp():
    memory = TaskMemory(None, 'conversation')
    memory.accept_song('A副歌', 'A', 'Singer', 30, continuing=False, section='副歌', start_seconds=80)
    task = memory.state['taskId']
    memory.accept_song('改成B', 'B', 'Singer', 30, continuing=True)
    assert memory.state['taskId'] == task
    assert memory.state['constraints']['songTitle'] == 'B'
    assert 'sourceStartSeconds' not in memory.state['constraints']
    assert 'section' not in memory.state['constraints']


@pytest.mark.parametrize('model_duration', [30, None])
def test_confirmed_song_keeps_explicit_duration_despite_stale_model_update(tmp_path, qapp, monkeypatch, model_duration):
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', fetched)
    editor = EditorController(ProjectState())
    assistant, _, _ = studio_assistant(tmp_path, editor, [
        call('set_song_task', dict(title='云端旅行', artist='测试乐队', continuing=False, duration_seconds=20)),
        *discovery(),
        call('set_song_task', dict(title='云端旅行', artist='测试乐队', continuing=True,
            **({'duration_seconds': model_duration} if model_duration is not None else {}))),
        call('inspect_music_resource', dict(url=SCORE, source_url=PAGE, title='云端旅行')),
        call('prepare_song_preview', dict(resource_id='resource-1', start_seconds=0, duration_seconds=30)),
    ])
    try:
        assert assistant.sendMessage('帮我准备云端旅行20秒试听')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation['durationSeconds'] == 20
        assert assistant.confirmSongSource()
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(20)
        assert assistant._task_memory.state['constraints']['targetSeconds'] == 20
        assert not editor.state.active_notes
    finally:
        assistant.shutdown()
