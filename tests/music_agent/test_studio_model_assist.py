"""Studio's natural-language entry exercises the real loop and authorization."""
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.providers import ProviderEvent
from tests.music_agent.test_assistant_controller import _SerializedLoopProvider, _serialized_loop_assistant
from tests.music_agent.test_creation_workflow import Playback, done
from tests.music_agent.test_source_confirmation_workflow import call, fetched


FIRST = '帮我写一段30s的烟花易冷，周杰伦的歌'
CORRECTION = '烟花易冷，你是不是搜错歌了导致没找到'
CONTINUE = '就是这首，继续'
PAGE = 'https://scores.example.org/fireworks'
SCORE = 'https://scores.example.org/fireworks.jpg'
IDENTITY = '周杰伦 烟花易冷 钢琴改编'


def studio_assistant(tmp_path, editor, scripts):
    playback = Playback()
    provider = _SerializedLoopProvider(scripts)
    assistant = _serialized_loop_assistant(tmp_path, editor, provider, playback=playback,
        automatic_music=True, require_song_confirmation=True, model_driven_assistant=True)
    return assistant, provider, playback


@pytest.mark.parametrize('found_first', [False, True])
def test_original_three_turns_retain_song_duration_and_exact_unreadable_jpg(tmp_path, qapp, monkeypatch, found_first):
    image = QImage(200, 260, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    encoded = QBuffer()
    assert encoded.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(encoded, 'JPEG')
    jpg = bytes(encoded.data())
    requested = []

    def fetch(url, **_kwargs):
        requested.append(url)
        if url == PAGE:
            return (f'<h1>{IDENTITY}</h1><a href="{SCORE}">五线谱</a>'.encode(), url, 'text/html')
        if url == SCORE:
            return jpg, url, 'image/jpeg'
        return fetched(url, **_kwargs)

    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', fetch)
    discovery = [
        call('inspect_music_resource', dict(url=PAGE, title='烟花易冷', source_url=PAGE)),
        call('inspect_music_resource', dict(url=SCORE, title='烟花易冷', source_url=PAGE)),
        call('propose_song_source', dict(title='烟花易冷', artist='周杰伦', edition='钢琴改编',
             section='开头', selected_url=SCORE, start_seconds=0, evidence=[dict(url=PAGE, quote=IDENTITY)],
             summary='来源页标注歌手、歌名并链接该谱图。', uncertainty='转写尚未验证。')),
    ]
    assistant, provider, _ = studio_assistant(tmp_path, EditorController(ProjectState()), [
        call('set_song_task', dict(title='烟花易冷', artist='周杰伦', continuing=False, duration_seconds=15)),
        *(discovery if found_first else [(ProviderEvent(text_delta='我还没找到可确认的谱源。', finish_reason='stop'),)]),
        call('set_song_task', dict(title='烟花易冷', artist='', continuing=True)),
        *([(ProviderEvent(text_delta='上次是同一首。', finish_reason='stop'),)] if found_first else discovery),
        call('inspect_music_resource', dict(url=SCORE, title='烟花易冷', source_url=PAGE)),
        (ProviderEvent(text_delta='当前连接无法读取谱图中的音符。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage(FIRST)
        done(qapp, assistant)
        assert FIRST in provider.requests[0][-1]['content']
        assert assistant.lastTurnResult['status'] == ('source_confirmation' if found_first else 'source_unavailable')
        task = assistant._task_memory.state
        task_id = task['taskId']
        assert task['constraints']['targetSeconds'] == 30

        assert assistant.sendMessage(CORRECTION)
        done(qapp, assistant)
        assert any(CORRECTION in request[-1]['content'] and task_id in request[-1]['content']
                   for request in provider.requests if isinstance(request[-1]['content'], str))
        candidate = assistant.songSourceConfirmation
        assert (candidate['title'], candidate['artist'], candidate['durationSeconds']) == ('烟花易冷', '周杰伦', 30)
        assert any(message.get('role') == 'tool' and 'low-resolution preview' in message['content']
                   for request in provider.requests for message in request)
        assert not assistant.taskPreviewAvailable

        assert assistant.sendMessage(CONTINUE)
        done(qapp, assistant)
        assert any(CONTINUE in request[-1]['content'] for request in provider.requests
                   if isinstance(request[-1]['content'], str))
        state = assistant._task_memory.state
        assert state['taskId'] == task_id
        assert state['constraints']['targetSeconds'] == 30
        assert state['receipt']['source']['source_url'] == SCORE
        assert state['receipt']['source']['stage'] == 'confirmed_no_preview'
        assert assistant.lastTurnResult['status'] == 'score_images_unsupported'
        assert requested[-1] == SCORE and not assistant.taskPreviewAvailable
        assert not assistant._editor.state.active_notes
        assert [m.text for m in assistant.messages.entries if m.kind.value == 'USER_MESSAGE'] == [FIRST, CORRECTION, CONTINUE]
    finally:
        assistant.shutdown()


def test_model_can_request_an_independent_original_draft(tmp_path, qapp):
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    before = editor.state.content, editor.projectRevision
    assistant, _, playback = studio_assistant(tmp_path, editor, [
        call('start_original_draft', {}),
        call('add_notes', {'notes': [{'pitch': 67, 'start_time': 0, 'duration': 1, 'velocity': 72}]}),
        (ProviderEvent(text_delta='已经写好。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('请另写一段一秒的原创短曲')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert assistant.auditionTask() and [note.pitch for note in playback.notes] == [67]
        assert (editor.state.content, editor.projectRevision) == before
        assert len([m for m in assistant.messages.entries if m.kind.value == 'USER_MESSAGE']) == 1
    finally:
        assistant.shutdown()


def test_model_reads_and_enriches_current_music_as_playable_proposal(tmp_path, qapp):
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    before = editor.state.content, editor.projectRevision, editor.historyUndoDepth
    assistant, provider, playback = studio_assistant(tmp_path, editor, [
        call('get_notes', {'start_bar': 1, 'end_bar': 1}),
        call('add_notes', {'notes': [{'pitch': 48, 'start_time': 0, 'duration': 1, 'velocity': 72}]}),
        (ProviderEvent(text_delta='加入了一个较轻的低音声部，可先试听。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('这段太单调了，帮我丰富一下')
        done(qapp, assistant)
        result = assistant.lastTurnResult
        assert result['status'] == 'proposal' and result['readCount'] >= 1 and result['writeCount'] == 1
        assert not result['committed']
        assert {tool['function']['name'] for tool in provider.tool_schemas[0]} >= {'get_notes', 'add_notes'}
        assistant.auditionProposal()
        assert sorted(note.pitch for note in playback.notes) == [48, 60]
        assert (editor.state.content, editor.projectRevision, editor.historyUndoDepth) == before
    finally:
        assistant.shutdown()
