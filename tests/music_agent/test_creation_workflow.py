import json
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QObject, Signal

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.creation_workflow import CreationWorkflow
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.song_resolver import SongResolver, CatalogSourceProvider, music_request
from ai_piano_studio.music_agent.song_workflow import prepare_song, publish_prepared_song
from ai_piano_studio.persistence.project_document import load_project_document
from ai_piano_studio.persistence.project_library import ProjectLibrary
from tests.music_agent.test_assistant_controller import _serialized_loop_assistant, _SerializedLoopProvider, _wait_until


class Playback(QObject):
    playingChanged = Signal()
    audioFailed = Signal(str)
    playing = False
    notes = ()

    def play_detached_notes(self, notes):
        self.notes = notes
        self.playing = True
        self.playingChanged.emit()

    def stop_detached_preview(self):
        self.playing = False


def assistant_for(tmp_path, editor, scripts=()):
    provider = _SerializedLoopProvider(list(scripts))
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    playback = Playback()
    assistant._creation_workflow = CreationWorkflow(assistant, playback)
    return assistant, playback, provider


def done(qapp, assistant):
    _wait_until(qapp, lambda: not assistant.isBusy)


def accept_prepared(assistant):
    """The user's revised contract requires audition and explicit adoption."""
    assert assistant.taskPreviewAvailable
    assert assistant.auditionTask()
    assert assistant.taskPreviewPlayed
    assert assistant.applyTaskProject()


def test_moonlight_actual_licensed_notes_save_load_and_detached_play(tmp_path, qapp):
    old = Note.create(id="old", pitch=60, start_time=0, duration=1, velocity=90)
    editor = EditorController(ProjectState(notes=(old,)))
    # A real dirty edit must survive the autonomous task.
    editor.setBpm(111)
    editor.synchronizeNow()
    before = editor.state
    assistant, playback, provider = assistant_for(tmp_path, editor)
    assert assistant.sendMessage("弹一首月光奏鸣曲，温柔一点")
    done(qapp, assistant)
    assert not playback.notes and editor.state == before
    accept_prepared(assistant)
    assert assistant.taskProgress["safeErrorCode"] == "", [(m.text) for m in assistant.messages.entries]
    assert assistant.taskProgress["status"] == "completed"
    assert not provider.requests
    assert editor.state == before and editor.isDirty
    assert len(playback.notes) == 1142
    result = assistant.lastTurnResult
    assert result["playbackStarted"] is True
    saved = Path(result["savedPath"])
    assert load_project_document(saved).state.playback_notes == playback.notes
    provenance = json.loads(saved.with_name("music-source.json").read_text("utf-8"))
    assert provenance["license"] == "CC BY-SA 2.5"
    assert provenance["verifiedNotes"] is True
    assert "Mutopia" in saved.with_name("MUSIC-SOURCE.txt").read_text("utf-8")
    assert assistant.taskProjectAvailable


def music_scripts():
    notes = [dict(pitch=60 + i % 8, start_time=i * .5, duration=.5, velocity=70) for i in range(60)]
    notes += [dict(pitch=48 + i % 3 * 4, start_time=i, duration=.9, velocity=50) for i in range(30)]
    return [(ProviderEvent(tool_calls=(ProviderToolCall("notes", "add_notes", json.dumps({"notes": notes})),)),)]


@pytest.mark.parametrize("instruction,kind", [
    ("创作一首 30 秒忧伤钢琴曲", "ai_original"),
    ("我要周杰伦的青花瓷，用钢琴弹", "ai_reconstruction_unverified"),
])
def test_autonomous_real_tools_original_and_commercial_fallback(tmp_path, qapp, instruction, kind):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage(instruction)
    done(qapp, assistant)
    accept_prepared(assistant)
    assert assistant.taskProgress["safeErrorCode"] == "", [(m.text) for m in assistant.messages.entries]
    assert assistant.taskProgress["status"] == "completed", assistant.taskProgress
    assert len(playback.notes) == 90
    result = assistant.lastTurnResult
    assert result["provenance"]["kind"] == kind
    assert result["provenance"]["verifiedNotes"] is False
    assert result["playbackStarted"] is True
    assert editor.state.playback_duration >= 29.9
    assert not editor.isDirty
    assert Path(result["savedPath"]).is_file()
    if kind == 'ai_reconstruction_unverified':
        assert "Prepare an isolated audition of the user's requested song" in provider.requests[0][0]['content']
    else:
        assert "Application autonomous creation route" in provider.requests[0][0]['content']
    assert any(step["stage"] == "writing" for step in assistant.taskProgress["steps"])


def test_exact_song_attempt_is_not_mislabeled_verified_or_original(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage("我要青花瓷，只要原曲，不要替代")
    done(qapp, assistant)
    assert assistant.taskPreviewAvailable and provider.requests
    assert assistant.taskPreviewInfo['sourceKind'] == 'ai_reconstruction_unverified'
    assert not playback.notes and not editor.state.notes


def test_source_cancel_and_late_result_cleanup(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    entered, release = Event(), Event()
    original = assistant._creation_workflow.resolver.resolve
    def slow(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)
    assistant._creation_workflow.resolver.resolve = slow
    assert assistant.sendMessage("弹一首月光奏鸣曲")
    _wait_until(qapp, entered.is_set)
    assistant.cancelGeneration()
    release.set()
    _wait_until(qapp, lambda: not assistant._workers)
    assert assistant.taskProgress["status"] == "cancelled"
    assert not playback.notes and not editor.state.active_notes
    assert not list((assistant._creation_workflow.home / "assistant/pending-music").glob("*/project*"))


def test_invalid_cache_uses_verified_bundled_score_and_atomic_publish(tmp_path, qapp):
    resolver = SongResolver(tmp_path)
    resolver.catalog.cache_home.mkdir(parents=True)
    (resolver.catalog.cache_home / "moonlight1.mid").write_bytes(b"bad")
    source = resolver.resolve("弹月光奏鸣曲")
    assert len(source.midi.notes) == 1142
    song = prepare_song(source, "慢一点", tmp_path)
    assert song.state.tempo.bpm == 60
    duration = max(n.end_tick for n in source.midi.notes) / source.midi.ppq * 60 / source.midi.bpm
    assert song.state.playback_duration == pytest.approx(duration / .85, abs=.1)
    library = ProjectLibrary(tmp_path / "Projects")
    saved = publish_prepared_song(song, library, tmp_path)
    assert not song.staging_home.exists()
    assert {"project.pianoai.json", "music-source.json", "MUSIC-SOURCE.txt"} <= {p.name for p in saved.parent.iterdir()}


def test_continuous_soften_and_slow_edit_apply_save_and_undo(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage("创作一首 30 秒忧伤钢琴曲")
    done(qapp, assistant)
    accept_prepared(assistant)
    before = editor.state
    ids = [n.id for n in before.active_notes if n.start_time < 4]
    provider._scripts.extend([
        (ProviderEvent(tool_calls=(ProviderToolCall("read", "get_notes", '{"start_bar":1,"end_bar":2}'),)),),
        (ProviderEvent(tool_calls=(
            ProviderToolCall("soft", "set_note_velocity", json.dumps({"note_ids": ids, "velocity": 40})),
            ProviderToolCall("slow", "set_bpm", '{"bpm":90}'),
        )),),
    ])
    assert assistant.sendMessage("前奏更柔和，速度慢一点")
    done(qapp, assistant)
    assert assistant.lastTurnResult["intent"] == "EDIT_MUSIC"
    assert assistant.lastTurnResult["writeCount"] == 2
    assert assistant.proposal["status"] == "pending"
    assert assistant.applyProposal()["success"]
    editor.synchronizeNow()
    assert editor.state.tempo.bpm == 90
    assert all(n.velocity == 40 for n in editor.state.active_notes if n.id in ids)
    editor.saveCurrent()
    _wait_until(qapp, lambda: not editor.isSaving)
    assert load_project_document(editor.currentFilePath).state.content == editor.state.content
    editor.undo()
    editor.synchronizeNow()
    assert editor.state.tempo == before.tempo
    assert [n.velocity for n in editor.state.active_notes] == [n.velocity for n in before.active_notes]


def test_model_no_notes_short_piece_and_unavailable_are_failures(tmp_path, qapp):
    for index, scripts in enumerate(([], [(ProviderEvent(tool_calls=(ProviderToolCall(
            "short", "add_notes", '{"notes":[{"pitch":60,"start_time":0,"duration":1,"velocity":70}]}'),)),)])):
        folder = tmp_path / str(index)
        folder.mkdir()
        editor = EditorController(ProjectState())
        assistant, playback, provider = assistant_for(folder, editor, scripts)
        assert assistant.sendMessage("创作一首 30 秒忧伤钢琴曲")
        done(qapp, assistant)
        assert assistant.taskProgress["status"] == "failed"
        assert assistant.taskProgress["safeErrorCode"] in {"no_write", "incomplete_music"}
        assert not playback.notes and not editor.state.active_notes
        assert not assistant.taskProjectAvailable


def test_project_change_during_source_task_cancels_and_retry_succeeds(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    entered, release = Event(), Event()
    resolve = assistant._creation_workflow.resolver.resolve
    def slow(*args, **kwargs):
        entered.set()
        release.wait(2)
        return resolve(*args, **kwargs)
    assistant._creation_workflow.resolver.resolve = slow
    assert assistant.sendMessage("弹一首月光奏鸣曲")
    _wait_until(qapp, entered.is_set)
    editor.setBpm(100)
    assert assistant.taskProgress["status"] == "cancelled"
    release.set()
    _wait_until(qapp, lambda: not assistant._workers)
    assistant._creation_workflow.resolver.resolve = resolve
    assert assistant.retryTask()
    done(qapp, assistant)
    accept_prepared(assistant)
    assert assistant.taskProgress["status"] == "completed"
    assert editor.state.tempo.bpm == 100


def test_source_failures_and_pdf_are_honest_and_do_not_stop_fallback(tmp_path):
    from ai_piano_studio.music_agent.song_resolver import ConvertedLocalSourceProvider
    from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime
    local = tmp_path / "MusicLibrary"
    local.mkdir()
    (local / "测试乐曲.pdf").write_bytes(b"not a parsed score")
    events = []
    runtime = IsolatedMusicRuntime(tmp_path, python=tmp_path / "missing-python.exe")
    provider = ConvertedLocalSourceProvider(local, runtime, "score")
    assert SongResolver(tmp_path, providers=[provider]).resolve("弹测试乐曲", progress=events.append) is None
    assert any(e.detail_code == "score_ocr_unavailable" for e in events)
    request = music_request("我要周杰伦的青花瓷，用钢琴弹")
    assert request.title == "青花瓷" and request.artist == "周杰伦" and request.named


def test_license_sidecars_survive_real_save_copy(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    assert assistant.sendMessage("弹一首月光奏鸣曲")
    done(qapp, assistant)
    accept_prepared(assistant)
    source = Path(editor.currentFilePath)
    assert editor.saveCopy("月光轻柔副本")
    _wait_until(qapp, lambda: not editor.isSaving)
    target = Path(editor.currentFilePath)
    assert source != target
    assert source.with_name("MUSIC-SOURCE.txt").read_bytes() == target.with_name("MUSIC-SOURCE.txt").read_bytes()


def test_real_resolver_network_failure_does_not_invent_notes(tmp_path):
    from urllib.error import URLError
    from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "catalog.json").write_bytes((CATALOG_HOME / "catalog.json").read_bytes())
    def interrupted(_url, _cancelled):
        raise URLError("controlled disconnected network")
    provider = CatalogSourceProvider(tmp_path / "cache", catalog_home=catalog, fetch=interrupted)
    events = []
    assert SongResolver(tmp_path, providers=[provider]).resolve("弹月光奏鸣曲", progress=events.append) is None
    assert any(e.detail_code == "network_unavailable" for e in events)
    assert not list((tmp_path / "cache").glob("*.mid"))


def test_unconfigured_model_does_not_turn_original_request_into_success(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    assistant._provider_settings._provider_factory = None
    assistant.selectProvider("openai")
    assert assistant.sendMessage("创作一首 30 秒忧伤钢琴曲")
    done(qapp, assistant)
    assert assistant.taskProgress["safeErrorCode"] == "configuration_required"
    assert not playback.notes and not provider.requests


def test_qin_shi_ming_yue_song_attempts_reconstruction_without_original_substitution(tmp_path, qapp):
    instruction = "来一段秦时明月的月光，20s"
    request = music_request(instruction)
    assert request.named and request.title == "月光"
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage(instruction)
    done(qapp, assistant)
    assert assistant.taskPreviewInfo['sourceKind'] == 'ai_reconstruction_unverified'
    assert provider.requests and not playback.notes and not editor.state.notes
    assert assistant.taskPreviewAvailable


def test_moonlight_mood_is_not_mistaken_for_named_song():
    assert not music_request("写一段月光下的温柔钢琴音乐").named
    assert music_request("来一段月光，20s").named


def test_missing_midi_attempt_is_presented_honestly_without_user_upload_demand(tmp_path, qapp):
    from ai_piano_studio.music_agent.assistant_controller import AssistantMessageKind
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage('来一段秦时明月的月光，20s')
    done(qapp, assistant)
    reply = assistant.messages.entries[-1]
    assert reply.kind == AssistantMessageKind.ASSISTANT_MESSAGE
    assert '月光' in reply.text and '尝试还原' in reply.text and '待核对' in reply.text
    assert '导入' not in reply.text and '提供' not in reply.text
    assert provider.requests and not playback.notes and not editor.state.notes


def test_partner_keeps_missing_song_in_followup_discussion(tmp_path, qapp):
    editor = EditorController(ProjectState())
    reply = "可以先聊编配：如果你喜欢原曲的力量感，我们可以讨论低音节奏，再决定是否做原创。"
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    assert assistant.sendMessage('来一段秦时明月的月光，20s')
    done(qapp, assistant)
    missing_reply = assistant.messages.entries[-1].text
    assert assistant.assistantStatus == 'proposal_ready' and provider.requests
    request_count = len(provider.requests)
    provider._scripts.append((ProviderEvent(text_delta=reply, finish_reason='stop'),))
    assert assistant.sendMessage('先聊聊它的编配思路，不用生成')
    done(qapp, assistant)
    assert assistant.messages.entries[-1].text == reply
    assert any(m.get('content') == missing_reply for m in provider.requests[request_count])
    # User explicitly expanded Studio chat authority: real music tools remain
    # available in proposal mode alongside resource tools. Discussing an
    # arrangement must still leave the project and playback unchanged.
    names = {tool['function']['name'] for tool in provider.tool_schemas[request_count]}
    assert {'inspect_music_resource','prepare_song_preview','get_notes','set_note_velocity'} <= names
    assert not playback.notes
    assert not editor.state.notes
