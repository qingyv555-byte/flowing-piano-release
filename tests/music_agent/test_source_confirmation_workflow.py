"""Source confirmation is optional for drafting. Only explicit adoption changes the editor."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
from tests.music_agent.test_assistant_controller import _SerializedLoopProvider, _serialized_loop_assistant
from tests.music_agent.test_creation_workflow import Playback, done


PAGE = 'https://scores.example.org/cloud-song'
SCORE = 'https://scores.example.org/cloud.mid'
IDENTITY = '测试乐队 云端旅行 钢琴改编'


def call(name, arguments):
    return (ProviderEvent(tool_calls=(ProviderToolCall(uuid4().hex, name, json.dumps(arguments)),)),)


def discovery():
    return [
        call('inspect_music_resource', dict(url=PAGE, title='云端旅行', source_url=PAGE)),
        call('inspect_music_resource', dict(url=SCORE, title='云端旅行', source_url=PAGE)),
        call('propose_song_source', dict(title='云端旅行', artist='测试乐队', edition='钢琴改编',
             section='开头', selected_url=SCORE, start_seconds=0,
             evidence=[dict(url=PAGE, quote=IDENTITY)],
             summary='实际读取来源页的歌名、乐队及该页的乐谱链接。', uncertainty='未与原唱录音比对。')),
    ]


def make_assistant(tmp_path, editor, scripts=()):
    playback = Playback()
    provider = _SerializedLoopProvider(list(scripts))
    assistant = _serialized_loop_assistant(tmp_path, editor, provider, automatic_music=True,
        playback=playback, require_song_confirmation=True)
    return assistant, playback, provider


def fetched(url, **_kwargs):
    if url == PAGE:
        return (f'<html><h1>{IDENTITY}</h1><a href="{SCORE}">主旋律MIDI</a></html>'.encode(), url, 'text/html')
    if url == SCORE:
        return ((CATALOG_HOME / 'moonlight1.mid').read_bytes(), url, 'audio/midi')
    raise ValueError('Unexpected test URL')


def test_local_named_song_waits_without_preparing_notes(tmp_path, qapp):
    editor = EditorController(ProjectState(notes=(Note.create(pitch=65, start_time=0, duration=1),)))
    assistant, playback, provider = make_assistant(tmp_path, editor)
    before, depth = editor.state.content, editor.historyUndoDepth
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲')
        done(qapp, assistant)
        candidate = assistant.songSourceConfirmation
        assert candidate['title'] and candidate['artist']
        assert candidate['durationSeconds'] == 15
        assert not assistant.taskPreviewAvailable and not playback.notes
        assert assistant.lastTurnResult['status'] == 'source_confirmation'
        assert assistant.taskProgress['status'] == 'waiting'
        assert assistant.taskProgress['processAlive'] is False
        assert editor.state.content == before and editor.historyUndoDepth == depth
        assert not provider.requests
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('instruction,seconds', [('来一首贝多芬的月光奏鸣曲', 15), ('来一首贝多芬的月光奏鸣曲，30秒', 30)])
def test_user_confirmation_resumes_local_excerpt_and_keeps_dirty_project(tmp_path, qapp, instruction, seconds):
    editor = EditorController(ProjectState())
    editor.createNoteAt(0, 0)
    editor.synchronizeNow()
    before, depth = editor.state.content, editor.historyUndoDepth
    assistant, playback, _provider = make_assistant(tmp_path, editor)
    try:
        assert assistant.sendMessage(instruction)
        done(qapp, assistant)
        assert assistant.songSourceConfirmation
        assert assistant.confirmSongSource()
        done(qapp, assistant)
        assert not assistant.songSourceConfirmation
        assert assistant.taskPreviewAvailable
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(seconds, abs=.1)
        assert editor.state.content == before and editor.historyUndoDepth == depth and editor.isDirty
        assert len([m for m in assistant.messages.entries if m.kind.value == 'USER_MESSAGE']) == 2
        assert assistant.auditionTask() and playback.notes
        assert assistant.applyTaskProject()
        assert Path(assistant.lastTurnResult['savedPath']).is_file()
        assert editor.state.content == before
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('duration_text,tool_duration,expected', [('', 30, 15), ('，30秒', 15, 30)])
def test_model_discovery_stops_at_real_inspected_source_then_user_approves(tmp_path, qapp, monkeypatch, duration_text, tool_duration, expected):
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetched)
    scripts = discovery() + [
        call('inspect_music_resource', dict(url=SCORE, title='云端旅行', source_url=PAGE)),
        call('prepare_song_preview', dict(resource_id='resource-1', start_seconds=0, duration_seconds=tool_duration)),
    ]
    editor = EditorController(ProjectState())
    assistant, _playback, provider = make_assistant(tmp_path, editor, scripts)
    try:
        assert assistant.sendMessage('来一首测试乐队的云端旅行' + duration_text)
        done(qapp, assistant)
        assert assistant.songSourceConfirmation, (assistant.taskProgress, assistant.lastTurnResult,
                                                   [m.text for m in assistant.messages.entries])
        assert assistant.songSourceConfirmation['selectedUrl'] == SCORE
        assert assistant.songSourceConfirmation['evidence'][0]['quote'] == IDENTITY
        assert not assistant.taskPreviewAvailable and not editor.state.playback_notes
        requests_before = len(provider.requests)
        assert assistant.sendMessage('现在找到什么了？')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation and len(provider.requests) == requests_before
        assert assistant.sendMessage('就是这首，继续')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable and not assistant.songSourceConfirmation
        assert not editor.state.playback_notes
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(expected, abs=.1)
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('change', ['edit', 'new_project', 'cancel', 'shutdown'])
def test_pending_source_cannot_be_approved_after_task_or_project_changes(tmp_path, qapp, change):
    editor = EditorController(ProjectState())
    assistant, _playback, _provider = make_assistant(tmp_path, editor)
    try:
        assert assistant.sendMessage('来一首贝多芬的月光奏鸣曲')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation
        if change == 'edit':
            editor.createNoteAt(0, 0)
            editor.synchronizeNow()
        elif change == 'new_project':
            editor.newProject()
        elif change == 'cancel':
            assistant.cancelGeneration()
        else:
            assistant.shutdown()
        assert not assistant.songSourceConfirmation
        assert not assistant.confirmSongSource()
        assert not assistant.taskPreviewAvailable
    finally:
        assistant.shutdown()


def test_artist_attribution_correction_and_typed_confirmation_keep_duration(tmp_path, qapp, monkeypatch):
    from ai_piano_studio.music_agent.song_resolver import music_request

    original = '帮我写一段30s的烟花易冷，周杰伦的歌'
    parsed = music_request(original)
    assert (parsed.title, parsed.artist, parsed.named) == ('烟花易冷', '周杰伦', True)
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetched)
    scripts = [(ProviderEvent(finish_reason='stop'),)] + discovery() + [
        call('inspect_music_resource', dict(url=SCORE, title='云端旅行', source_url=PAGE)),
        call('prepare_song_preview', dict(resource_id='resource-1', start_seconds=0, duration_seconds=15)),
    ]
    editor = EditorController(ProjectState())
    assistant, _, provider = make_assistant(tmp_path, editor, scripts)
    try:
        assert assistant.sendMessage('帮我写一段30s的云端旅行，测试乐队的歌')
        done(qapp, assistant)
        assert not assistant.songSourceConfirmation
        assert assistant.sendMessage('云端旅行，你是不是搜错歌了导致没找到')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation['title'] == '云端旅行'
        assert assistant.songSourceConfirmation['durationSeconds'] == 30
        assert assistant.sendMessage('就是这首，继续')
        done(qapp, assistant)
        assert assistant.taskPreviewInfo['durationSeconds'] == pytest.approx(30, abs=.1)
        assert not editor.state.playback_notes
        request_count = len(provider.requests)
        generation = assistant._generation
        assert assistant.sendMessage('就是这首，继续')
        assert len(provider.requests) == request_count and assistant._generation == generation
        assert '没有待确认的谱源' in assistant.messages.entries[-1].text
    finally:
        assistant.shutdown()


def test_discovery_can_write_an_isolated_draft_before_source_proposal(tmp_path, qapp):
    editor = EditorController(ProjectState(notes=(Note.create(pitch=65, start_time=0, duration=1),)))
    before = editor.state
    scripts = [call('add_notes', dict(notes=[dict(pitch=67, start_time=0, duration=1, velocity=70)]))]
    assistant, playback, _provider = make_assistant(tmp_path, editor, scripts)
    try:
        assert assistant.sendMessage('来一首测试乐队的云端旅行')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert editor.state == before and not playback.notes
        assert not assistant.songSourceConfirmation
        assert not assistant.applyTaskProject()
        assert assistant.auditionTask()
        assert [note.pitch for note in playback.notes] == [67]
        assert editor.state == before
        assert assistant.applyTaskProject()
        assert Path(assistant.lastTurnResult['savedPath']).is_file()
        assert [note.pitch for note in editor.state.playback_notes] == [67]
    finally:
        assistant.shutdown()


def test_confirmed_model_can_write_isolated_draft_without_reloading_source(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetched)
    scripts = discovery() + [call('add_notes', dict(notes=[dict(pitch=67, start_time=0, duration=1, velocity=70)]))]
    assistant, _playback, _provider = make_assistant(tmp_path, EditorController(ProjectState()), scripts)
    try:
        assert assistant.sendMessage('来一首测试乐队的云端旅行')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation
        assert assistant.confirmSongSource()
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert not assistant._editor.state.playback_notes
    finally:
        assistant.shutdown()


def test_rejected_source_is_not_automatically_reoffered_or_generated(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetched)
    assistant, _playback, provider = make_assistant(tmp_path, EditorController(ProjectState()), discovery() + discovery())
    try:
        assert assistant.sendMessage('来一首测试乐队的云端旅行')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation
        assert assistant.rejectSongSource()
        done(qapp, assistant)
        assert len(provider.requests) > 3
        assert not assistant.songSourceConfirmation
        assert not assistant.taskPreviewAvailable and not assistant._editor.state.playback_notes
        assert not assistant.confirmSongSource()
    finally:
        assistant.shutdown()


def test_confirmed_identity_is_used_for_a_model_written_preview_title(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetched)
    scripts = discovery() + [
        call('inspect_music_resource', dict(url=SCORE, title='模型任意标签', source_url=PAGE)),
        call('add_notes', dict(notes=[dict(pitch=60 + i % 4, start_time=i * .5, duration=.5, velocity=70)
                                     for i in range(30)])),
    ]
    assistant, _playback, _provider = make_assistant(tmp_path, EditorController(ProjectState()), scripts)
    try:
        assert assistant.sendMessage('来一首测试乐队的《云端》')
        done(qapp, assistant)
        assert assistant.songSourceConfirmation['title'] == '云端旅行'
        assert assistant.confirmSongSource()
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert assistant.taskPreviewInfo['title'].startswith('云端旅行')
    finally:
        assistant.shutdown()


def test_original_route_can_read_sources_and_prepare_an_isolated_draft(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetched)
    scripts = discovery()[:2] + [call('add_notes', dict(notes=[dict(pitch=67, start_time=0, duration=1, velocity=70)]))]
    assistant, _playback, _provider = make_assistant(tmp_path, EditorController(ProjectState()), scripts)
    try:
        assert assistant.sendMessage('创作一首原创钢琴曲')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert not assistant._editor.state.playback_notes
    finally:
        assistant.shutdown()
