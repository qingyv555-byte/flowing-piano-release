import json
from dataclasses import replace

import pytest

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent import codex_provider as codex
from ai_piano_studio.music_agent import creation_workflow as creation
from ai_piano_studio.music_agent.codex_provider import CodexProvider
from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.providers import ProviderError, ProviderEvent
from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME, music_request, SourceCancelled
from ai_piano_studio.music_agent.source_search import (
    MusicSourceCandidate, MusicSourceSearchResult, downloadable_midi_url,
    parse_search_result, resolve_search_candidate,
)
from tests.music_agent.test_codex_provider import fake_generation
from tests.music_agent.test_creation_workflow import assistant_for, done, music_scripts


CANDIDATE = MusicSourceCandidate("青花瓷钢琴谱", "https://github.com/example/score/blob/main/青花瓷.mid",
                                "https://github.com/example/score/blob/main/青花瓷.mid", "第三方编配待核对")


def report_payload():
    return {"summary": "找到 MIDI 来源", "sources": [CANDIDATE.__dict__]}


def test_codex_source_search_enables_live_web_but_no_shell_or_music_writes(monkeypatch):
    captured = fake_generation(monkeypatch, {"text": "找到来源", "calls": [
        {"name": "report_music_sources", "arguments": json.dumps(report_payload())}]})
    original = codex.run_codex
    def run(path, args, **kwargs):
        result = original(path, args, **kwargs)
        if args != ['login', 'status']:
            return json.dumps({"type": "item.completed", "item": {"type": "web_search", "status": "completed"}}) + '\n' + result
        return result
    monkeypatch.setattr(codex, 'run_codex', run)
    provider = CodexProvider('test-model')
    result = provider.search_music_sources(music_request('来一段10s的周杰伦青花瓷'))
    assert result.sources == (CANDIDATE,)
    assert 'web_search="live"' in captured['args'] and 'features.shell_tool=false' in captured['args']
    assert captured['schema']['properties']['calls']['items']['properties']['name']['enum'] == ['report_music_sources']
    assert 'add_notes' not in captured['text'] and 'current project' in captured['text']
    assert not provider.allow_web_search  # Ordinary chat remains scoped independently.


def test_claiming_search_without_actual_search_event_is_rejected(monkeypatch):
    fake_generation(monkeypatch, {"text": "已经搜索", "calls": [
        {"name": "report_music_sources", "arguments": json.dumps(report_payload())}]})
    with pytest.raises(ProviderError) as error:
        CodexProvider('test-model').search_music_sources(music_request('弹青花瓷'))
    assert error.value.code == 'search_unavailable'


@pytest.mark.parametrize('url', ['http://raw.githubusercontent.com/a/b/main/青花瓷.mid',
    'https://127.0.0.1/青花瓷.mid', 'https://raw.githubusercontent.com.evil.test/a/青花瓷.mid',
    'https://user:password@raw.githubusercontent.com/a/b/main/青花瓷.mid',
    'https://raw.githubusercontent.com/a/b/main/别的歌.mid',
    'https://raw.githubusercontent.com/a/b/main/青花瓷.exe',
    'https://s3-ap-southeast-1.amazonaws.com/another-tenant/青花瓷.mid',
    'https://raw.githubusercontent.com:444/a/b/main/青花瓷.mid'])
def test_unrelated_and_untrusted_downloads_are_never_fetched(url):
    with pytest.raises(ValueError):
        resolve_search_candidate(replace(CANDIDATE, midi_url=url), music_request('弹青花瓷'),
            fetch=lambda *_: pytest.fail('must not fetch an unapproved source'))


def test_real_midi_parser_retains_unverified_identity_and_source_hash():
    raw = (CATALOG_HOME / 'moonlight1.mid').read_bytes()  # Fixture notes, not a claim about 青花瓷.
    result = resolve_search_candidate(CANDIDATE, music_request('弹青花瓷'), fetch=lambda *_: raw)
    assert len(result.midi.notes) == 1142
    assert result.provenance['verifiedNotes'] is False
    assert result.provenance['accuracy'] == 'online_edition_unverified'
    assert result.provenance['source_url'] == CANDIDATE.source_url
    assert len(result.provenance['sha256']) == 64


def test_author_bucket_is_scoped_to_its_source_page():
    candidate = replace(CANDIDATE, source_url='https://music.bensonby.me/jay-chou-blue-and-white-porcelain/',
                        midi_url='https://s3-ap-southeast-1.amazonaws.com/music.bensonby.me/青花瓷.midi')
    assert downloadable_midi_url(candidate, music_request('弹青花瓷')).startswith(
        'https://s3-ap-southeast-1.amazonaws.com/music.bensonby.me/')
    with pytest.raises(ValueError):
        downloadable_midi_url(replace(candidate, source_url='https://example.com/'), music_request('弹青花瓷'))


def test_search_candidate_cancel_and_invalid_midi_never_become_music():
    with pytest.raises(SourceCancelled):
        resolve_search_candidate(CANDIDATE, music_request('弹青花瓷'), cancelled=lambda: True,
            fetch=lambda *_: pytest.fail('cancelled source must not download'))
    with pytest.raises(ValueError):
        resolve_search_candidate(CANDIDATE, music_request('弹青花瓷'), fetch=lambda *_: b'<html>login required</html>')


def test_discovered_midi_is_staged_clipped_and_requires_audition(tmp_path, qapp, monkeypatch):
    editor = EditorController(ProjectState())
    assistant, playback, _ = assistant_for(tmp_path, editor)
    workflow = assistant._creation_workflow
    raw = (CATALOG_HOME / 'moonlight1.mid').read_bytes()
    class SearchProvider:
        def search_music_sources(self, request, **kwargs):
            assert request.title == '青花瓷'
            return parse_search_result(report_payload())
    original_resolve = workflow._resolve
    monkeypatch.setattr(workflow, '_resolve', lambda g, c, i, *_: original_resolve(g, c, i, SearchProvider()))
    monkeypatch.setattr(creation, 'resolve_search_candidate', lambda candidate, request, **kwargs:
        resolve_search_candidate(candidate, request, fetch=lambda *_: raw, **kwargs))
    assert assistant.sendMessage('来一段10s的周杰伦青花瓷')
    done(qapp, assistant)
    assert assistant.taskPreviewAvailable
    assert assistant.taskPreviewInfo['sourceKind'] == 'online_reference_midi'
    assert assistant.taskPreviewInfo['durationSeconds'] <= 10.01
    assert not playback.notes and not editor.state.notes and not assistant.applyTaskProject()
    assert assistant.auditionTask() and assistant.applyTaskProject()
    assert assistant.lastTurnResult['provenance']['source_url'] == CANDIDATE.source_url


def test_no_midi_search_result_still_reconstructs_instead_of_asking_user(tmp_path, qapp, monkeypatch):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    result = MusicSourceSearchResult('只找到数字曲谱页面，没有 MIDI 直链。', (
        replace(CANDIDATE, midi_url='', detail='可阅读曲谱'),))
    monkeypatch.setattr(assistant._creation_workflow, '_resolve', lambda *_: result)
    assert assistant.sendMessage('来一段10s的周杰伦青花瓷')
    done(qapp, assistant)
    assert provider.requests and assistant.taskPreviewAvailable
    assert assistant.taskPreviewInfo['sourceKind'] == 'ai_reconstruction_unverified'
    prompt = provider.requests[0][0]['content']
    # The capability update lets the model choose formats and recover itself;
    # it no longer injects the old mandatory-MIDI/reconstruction script.
    assert 'Choose your own sources, formats and tool order' in prompt
    assert 'Do not invent unreadable notes or substitute another song' in prompt
    assert CANDIDATE.source_url in json.dumps(provider.requests[0], ensure_ascii=False)
    assert not playback.notes and not editor.state.notes
    assert '未取得可自动解析的 MIDI' in assistant.messages.entries[-1].text


def test_screenshot_title_and_chorus_followups_keep_song_and_duration(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, _, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage('来一段10s的周杰伦青花瓷')
    done(qapp, assistant)
    provider._scripts.extend(music_scripts())
    assert assistant.sendMessage('烟花易冷呢')
    done(qapp, assistant)
    assert assistant._creation_workflow.instruction == '来一段10s的周杰伦《烟花易冷》'
    assert assistant.messages.entries[-2].text == '烟花易冷呢'
    provider._scripts.extend(music_scripts())
    assert assistant.sendMessage('高潮曲，谈一段15s的')
    done(qapp, assistant)
    assert '15s' in assistant._creation_workflow.instruction and '高潮' in assistant._creation_workflow.instruction
    assert '烟花易冷' in assistant.taskPreviewInfo['title']
    assert len(provider.requests) >= 3


def test_model_blocker_does_not_trigger_a_forced_reconstruction(tmp_path, qapp):
    scripts = [(ProviderEvent(text_delta='请先导入原曲 MIDI 才能继续。', finish_reason='stop'),)] + music_scripts()
    assistant, playback, provider = assistant_for(tmp_path, EditorController(ProjectState()), scripts)
    assert assistant.sendMessage('来一段10s的周杰伦青花瓷')
    done(qapp, assistant)
    # Removing automatic 'no write' retries was explicitly requested. A model
    # response must not be turned into invented music by a local continuation.
    assert len(provider.requests) == 1 and not assistant.taskPreviewAvailable
    assert not playback.notes and not assistant._editor.state.notes
    assert not assistant.taskProjectAvailable and not assistant.applyTaskProject()
    assert assistant.messages.entries[-1].text


@pytest.mark.parametrize('text', ['天气呢', '为什么不行呢', '模型设置呢', '先聊聊它的编配思路，不用生成'])
def test_followup_questions_do_not_grant_creation_permissions(tmp_path, qapp, text):
    assistant, _, _ = assistant_for(tmp_path, EditorController(ProjectState()), music_scripts())
    assert assistant.sendMessage('来一段10s的周杰伦青花瓷')
    done(qapp, assistant)
    assert not assistant._creation_workflow.expand_song_followup(text)


def test_artist_song_parsing_without_a_hardcoded_title():
    request = music_request('来一段10s的周杰伦烟花易冷')
    assert request.named and request.title == '烟花易冷' and request.artist == '周杰伦'
    assert not music_request('写一段周杰伦风格的原创钢琴曲').named
