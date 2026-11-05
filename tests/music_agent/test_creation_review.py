from pathlib import Path
import pytest

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.music_agent.test_creation_workflow import assistant_for, music_scripts, done
from ai_piano_studio.music_agent.song_resolver import SongResolver, music_request


def test_resolved_song_waits_for_real_audition_and_explicit_apply(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    before = editor.state
    assert assistant.sendMessage('弹一首月光奏鸣曲，温柔一点')
    done(qapp, assistant)
    assert getattr(assistant, 'taskPreviewAvailable', False)
    assert editor.state == before and not editor.currentFilePath
    assert not playback.notes
    assert not list(editor._projects.root.glob('*/project.pianoai.json'))
    assert not assistant.applyTaskProject(), 'A new work must not be applied before audition'
    assert assistant.auditionTask()
    assert assistant.taskPreviewPlayed
    assert len(playback.notes) == 1142 and editor.state == before
    assert assistant.applyTaskProject()
    assert len(editor.state.playback_notes) == 1142 and not editor.isDirty
    assert Path(editor.currentFilePath).is_file()


def test_discarding_prepared_original_preserves_current_project(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage('创作一首 30 秒忧伤钢琴曲')
    done(qapp, assistant)
    assert getattr(assistant, 'taskPreviewAvailable', False)
    assert not editor.state.active_notes and not playback.notes
    assistant.discardTaskProject()
    assert not assistant.taskPreviewAvailable
    assert not list((assistant._creation_workflow.home / 'assistant/pending-music').glob('*/project*'))
    assert not list(editor._projects.root.glob('*/project.pianoai.json'))


def test_named_song_never_silently_generates_an_original_substitute(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage('来一段周杰伦的青花瓷，30s')
    done(qapp, assistant)
    assert assistant.taskPreviewAvailable and provider.requests
    assert assistant.taskPreviewInfo['sourceKind'] == 'ai_reconstruction_unverified'
    assert not playback.notes
    assert not editor.state.active_notes
    assert not assistant.taskCreativeAlternativeAvailable
    assert not playback.notes and not editor.state.active_notes


def test_playback_request_without_playing_signal_does_not_enable_apply(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    assert assistant.sendMessage('弹一首月光奏鸣曲')
    done(qapp, assistant)
    assert getattr(assistant, 'taskPreviewAvailable', False)
    playback.play_detached_notes = lambda notes: None
    assert assistant.auditionTask()
    assert not assistant.taskPreviewPlayed
    assert not assistant.applyTaskProject()
    assistant.discardTaskProject()


@pytest.mark.parametrize('instruction', ['我要青花瓷原曲，不要原创', '弹青花瓷，不接受原创替代'])
def test_rejecting_original_does_not_turn_exact_song_into_original_request(instruction):
    assert music_request(instruction).named


def test_original_inspired_by_catalog_piece_does_not_return_that_existing_score(tmp_path):
    instruction = '创作一首月光奏鸣曲风格的原创钢琴曲'
    assert not music_request(instruction).named
    assert SongResolver(tmp_path).resolve(instruction) is None
