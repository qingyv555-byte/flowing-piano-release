from pathlib import Path
from threading import Event, Thread
import time

import pytest
from PySide6.QtCore import QObject, QMetaObject

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_appearance import visual_child
from tools.studio_evidence import install_memory_output


def visible_control(item, name):
    if item.objectName() == name and item.isVisible():
        return item
    for child in item.childItems():
        found = visible_control(child, name)
        if found is not None:
            return found
    return None


@pytest.fixture(autouse=True)
def memory_audio(monkeypatch):
    Path('artifacts/assistant-unified-20261101').mkdir(parents=True, exist_ok=True)
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def test_real_task_card_keeps_time_stop_and_stale_events_separate_from_chat(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    root.resize(1440, 1000)
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty("assistant")
    QMetaObject.invokeMethod(root, "openAssistantWindow")
    assistant._generation = 10
    assistant._active_generation = 10
    assistant._active_cancel = Event()
    assistant._assistant_status = "thinking"
    assistant._task_progress.start(10, "长等待交互验证（模拟）", "codex", "test")
    assistant._task_progress.set_model_driven(10)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '长等待交互验证'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '正在处理'))
    assistant.changed.emit()
    qtbot.waitUntil(lambda: visible_control(root.contentItem(), "assistantProgressCard") is not None)
    card = visible_control(root.contentItem(), "assistantProgressCard")
    Thread(target=lambda: assistant._generationProgress.emit(10, ProviderProgressEvent("submitted", status="waiting", process_alive=True)), daemon=True).start()
    qtbot.waitUntil(lambda: assistant.taskProgress["stage"] == "submitted")
    assert card.isVisible()
    count = assistant.messages.rowCount()
    qtbot.waitUntil(lambda: assistant.taskProgress["elapsedSeconds"] >= 1, timeout=3000)
    assert assistant.messages.rowCount() == count
    assert assistant.taskProgress["responseReceived"] is False
    assert assistant.taskProgress["status"] == "waiting"
    clock = assistant._task_progress._clock
    assistant._task_progress._clock = lambda: clock() + 135
    assistant._task_progress.tick()
    qtbot.wait(30)
    assert "仍在运行" in assistant.taskProgress["waitHint"]
    evidence = Path("artifacts/music-workflow-20261101")
    evidence.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(evidence / "task-waiting-offscreen.png"))
    stop = visual_child(root.contentItem(), "assistantStopButton")
    assert stop is not None and stop.isVisible() and stop.isEnabled()
    stop.clicked.emit()
    assert assistant.taskProgress["status"] == "cancelled"
    assert not assistant.isBusy
    assistant._generationProgress.emit(10, ProviderProgressEvent("writing"))
    qtbot.wait(30)
    assert assistant.taskProgress["status"] == "cancelled"
    listing = visual_child(root.contentItem(), 'assistantMessageList')
    qtbot.waitUntil(lambda: visible_control(listing, 'assistantTaskDetails') is not None)
    visible_control(listing, 'assistantTaskDetails').clicked.emit()
    qtbot.waitUntil(lambda: visible_control(listing, 'assistantTaskRetry') is not None)
    retry = visible_control(listing, "assistantTaskRetry")
    assert retry.isVisible() and retry.isEnabled()
    assert root.grabWindow().save(str(evidence / "task-cancelled-offscreen.png"))


def test_codex_wait_policy_is_bounded_and_local(studio_session):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty("assistant")
    assistant.setCodexWaitSeconds(900)
    assert assistant.codexWaitSeconds == 900
    assistant.setCodexWaitSeconds(999999)
    assert assistant.codexWaitSeconds == 1800
    assistant.setCodexWaitSeconds(1)
    assert assistant.codexWaitSeconds == 120


def test_long_assistant_progress_scrolls_inside_card(studio_session, qtbot):
    _, engine, root, _, _ = studio_session
    root.resize(1200, 760)
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    QMetaObject.invokeMethod(root, 'openAssistantWindow')
    assistant._assistant_status = 'thinking'
    assistant._active_generation = 12
    assistant._active_cancel = Event()
    assistant._task_progress.start(12, '长任务', 'codex', 'test')
    assistant._task_progress.set_model_driven(12)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '长任务'))
    for i in range(40):
        assistant._task_progress.record(12, ProviderProgressEvent('submitted' if i % 2 else 'writing'))
    assistant.changed.emit()
    qtbot.waitUntil(lambda: visible_control(root.contentItem(), 'assistantProgressCard') is not None)
    card = visible_control(root.contentItem(), 'assistantProgressCard')
    assert card is not None
    visible_control(card, 'assistantTaskDetails').clicked.emit()
    details = visible_control(card, 'assistantTaskExpandedDetails')
    qtbot.waitUntil(lambda: details.height() > 0 and details.property('contentHeight') > details.height())
    assert details.height() <= 196
    assert visible_control(card, 'assistantTaskStop') is not None
    assert visible_control(root.contentItem(), 'assistantMessageList').height() >= 80
    viewport = details.property('contentItem')
    viewport.setProperty('contentY', 80.0)
    assert viewport.property('contentY') > 0
    visible_control(card, 'assistantTaskDetails').clicked.emit()
    assert not details.isVisible()


def _use_scripted_studio_provider(assistant, tmp_path, scripts):
    from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
    from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
    from tests.music_agent.test_assistant_controller import _SerializedLoopProvider, _settings
    provider = _SerializedLoopProvider(scripts)
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore(),
                                       provider_factory=lambda _id: provider)
    settings.save('compatible', 'Offline fixture', 'https://scripted.example.test/v1',
                  'scripted-test-key', 'scripted-model', True)
    settings.record_capability_verification('compatible', 'scripted-model', provider.TOOLS)
    assistant._provider_settings = settings
    assistant.selectProvider('compatible')
    assistant.selectModel('scripted-model')
    return provider


def test_one_phrase_moonlight_real_project_and_memory_pcm(studio_session, qtbot, tmp_path, monkeypatch):
    from array import array
    from ai_piano_studio.persistence.project_document import load_project_document
    from tests.music_agent.test_source_confirmation_workflow import call, fetched, SCORE, PAGE
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty("assistant")
    # Creator uses its file-preview tool; retain the real 1142-note MIDI and
    # audition, PCM, adoption and saved-document checks from the legacy route.
    midi_path = tmp_path / 'moonlight.mid'
    midi_path.write_bytes(fetched(SCORE)[0])
    _use_scripted_studio_provider(assistant, tmp_path, [
        call('prepare_file_preview', dict(path=str(midi_path), title='月光奏鸣曲')),
    ])
    assert assistant.sendMessage("弹一首月光奏鸣曲，温柔一点")
    qtbot.waitUntil(lambda: assistant.taskPreviewAvailable, timeout=10000)
    assert not editor.state.active_notes and not editor.currentFilePath
    assert assistant.auditionTask()
    qtbot.waitUntil(lambda: assistant.taskPreviewPlayed, timeout=10000)
    assert playback.playing
    mixer = playback.piano_source.mixer
    peaks = []
    for _ in range(30):
        qtbot.wait(15)
        mixer._capture_consumed = getattr(mixer, "_capture_consumed", 0) + .02
        pcm = array("h", mixer._read_output_frames(int(mixer._sample_rate * .02)))
        peaks.append(max(map(abs, pcm), default=0))
    assert max(peaks) > 0, "real piano mixer must emit audible PCM in memory"
    assert assistant.applyTaskProject()
    assert assistant.taskProgress["status"] == "completed"
    assert assistant.lastTurnResult["playbackStarted"] is True
    saved = load_project_document(assistant.lastTurnResult['savedPath']).state
    assert len(saved.playback_notes) == len(editor.state.playback_notes) == 1142
    assert saved.content == editor.state.content and not editor.isDirty
    root.resize(1440, 1000)
    QMetaObject.invokeMethod(root, "openAssistantWindow")
    qtbot.waitUntil(lambda: bool(assistant.taskDisplayReceipts))
    listing = visual_child(root.contentItem(), 'assistantMessageList')
    assert listing is not None and assistant.messages.rowCount() > 0, (assistant.taskDisplayReceipts, assistant.messages.rowCount())
    qtbot.waitUntil(lambda: visible_control(listing, 'assistantTaskDetails') is not None)
    visible_control(listing, 'assistantTaskDetails').clicked.emit()
    qtbot.waitUntil(lambda: visible_control(listing, 'assistantTaskOpenProject') is not None)
    button = visible_control(listing, "assistantTaskOpenProject")
    assert button.isVisible()
    assert root.grabWindow().save("artifacts/assistant-unified-20261101/moonlight-reviewed-offscreen.png")
    playback.stop()


@pytest.mark.parametrize("instruction,kind", [
    ("创作一首 30 秒忧伤钢琴曲", "ai_original"),
    ("我要周杰伦的青花瓷，用钢琴弹", "ai_reconstruction_unverified"),
])
def test_scripted_model_real_creation_and_piano_pcm(studio_session, qtbot, tmp_path, instruction, kind):
    from array import array
    from ai_piano_studio.persistence.project_document import load_project_document
    from tests.music_agent.test_source_confirmation_workflow import call
    import json
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.persistence.project_document import ProjectDocument, project_document_to_dict
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty("assistant")
    # The scripted model writes a real project through Creator's file tools.
    # The same 90 notes and 30 seconds are verified through audio and reload.
    notes = [Note.create(pitch=60 + i % 8, start_time=i * .5, duration=.5, velocity=70) for i in range(60)]
    notes += [Note.create(pitch=48 + i % 3 * 4, start_time=i, duration=.9, velocity=50) for i in range(30)]
    path = tmp_path / (kind + '.pianoai.json')
    content = json.dumps(project_document_to_dict(ProjectDocument(ProjectState(tuple(notes)))))
    _use_scripted_studio_provider(assistant, tmp_path, [
        call('write_file', dict(path=str(path), content=content)),
        call('prepare_file_preview', dict(path=str(path), title=instruction)),
    ])
    assert assistant.sendMessage(instruction)
    qtbot.waitUntil(lambda: assistant.taskPreviewAvailable or assistant.taskProgress.get("status") == "failed", timeout=10000)
    assert not editor.state.playback_notes and not playback.playing
    assert assistant.auditionTask()
    qtbot.waitUntil(lambda: assistant.taskPreviewPlayed, timeout=10000)
    mixer = playback.piano_source.mixer
    peaks = []
    for _ in range(20):
        qtbot.wait(15)
        mixer._capture_consumed = getattr(mixer, "_capture_consumed", 0) + .02
        pcm = array("h", mixer._read_output_frames(int(mixer._sample_rate * .02)))
        peaks.append(max(map(abs, pcm), default=0))
    assert max(peaks) > 0
    assert assistant.applyTaskProject()
    assert assistant.lastTurnResult["provenance"]["kind"] == 'model_workspace'
    assert assistant.lastTurnResult["provenance"]["verifiedNotes"] is False
    assert assistant.lastTurnResult["provenance"]["source_file"] == str(path)
    saved = load_project_document(assistant.lastTurnResult['savedPath']).state
    assert saved.playback_duration >= 29.9 and len(saved.playback_notes) == 90
    assert saved.content == editor.state.content and not editor.isDirty
    QMetaObject.invokeMethod(root, "openAssistantWindow")
    root.resize(1440, 1000)
    qtbot.wait(40)
    assert root.grabWindow().save(f"artifacts/assistant-unified-20261101/{kind}-reviewed-offscreen.png")
    playback.stop()
