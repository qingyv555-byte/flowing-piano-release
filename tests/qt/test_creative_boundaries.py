"""Cross-mode regression using local providers and in-memory audio only."""
import json

from PySide6.QtCore import QSettings

from ai_piano_studio.creative.controller import CreativeController
from ai_piano_studio.creative.activity import ActivityAccumulator
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document
from tests.music_agent.test_assistant_controller import _SerializedLoopProvider
from tests.qt.test_creative_transport import Mixer, Source


def configured(tmp_path, provider=None, busy=lambda: False):
    settings = ProviderSettingsService(settings=QSettings(str(tmp_path/'ai.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(), provider_factory=(lambda _: provider) if provider else None)
    if provider:
        settings.save('compatible', 'Offline fixture', 'https://fixture.example.test/v1', 'fixture-only', 'scripted-model', True)
        settings.record_capability_verification('compatible', 'scripted-model', provider.TOOLS)
    source, mixer = Source(), Mixer()
    return CreativeController(tmp_path, source, mixer, busy, provider_settings=settings), source, mixer


def test_revoked_global_permission_and_deleted_memory_do_not_survive_in_prompt_history(qapp, qtbot, tmp_path):
    provider = _SerializedLoopProvider([(ProviderEvent(text_delta='fixture', finish_reason='stop'),)] * 4)
    controller, _, _ = configured(tmp_path, provider)
    try:
        controller.open()
        controller.assistant.selectProvider('compatible')
        controller.setScope('work-a')
        controller._memory.remember('global', 'density', 'sparse', confirmed=True)
        controller._memory.remember('work-b', 'mood', 'dramatic', confirmed=True)
        def generate(global_allowed):
            assert controller.generate('fixture material', global_allowed), controller.message
            qtbot.waitUntil(lambda: not controller.assistant.isBusy and not controller.assistant._workers, timeout=5000)
            return json.dumps(provider.requests[-1], ensure_ascii=False)
        assert 'density=sparse' in generate(True)
        second = generate(False)
        assert 'density=sparse' not in second
        assert 'mood=dramatic' not in second
        record = controller._memory.remember('work-a', 'mood', 'calm', confirmed=True)
        assert 'mood=calm' in generate(False)
        controller.deleteMemory(record)
        assert 'mood=calm' not in generate(False)
    finally:
        controller.shutdown()


class Collector(ActivityAccumulator):
    def start(self): self.clear()
    def stop(self): self.clear()


def test_offline_failure_keeps_saved_theme_and_next_request_can_recover(qapp, qtbot, tmp_path):
    class OfflineOnce(_SerializedLoopProvider):
        failed = False
        def stream_events(self, *args):
            if not self.failed:
                self.failed = True
                raise ConnectionError('offline fixture')
            yield from super().stream_events(*args)
    provider = OfflineOnce([
        (ProviderEvent(tool_calls=(ProviderToolCall('add', 'add_notes', '{"notes":[{"pitch":64,"start_time":1,"duration":0.5,"velocity":60}]}'),), finish_reason='tool_calls'),),
        (ProviderEvent(text_delta='recovered fixture', finish_reason='stop'),),
    ])
    controller, source, _ = configured(tmp_path, provider)
    try:
        path = tmp_path/'theme.json'
        save_project_document(path, ProjectDocument(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.2),))))
        assert controller.loadTheme(str(path))
        controller.assistant.selectProvider('compatible')
        before = controller._workspace.editor.state
        assert controller.generate('offline fixture', False)
        qtbot.waitUntil(lambda: not controller.assistant.isBusy and not controller.assistant._workers, timeout=5000)
        assert controller._workspace.editor.state == before
        assert not source.started
        assert controller.saveTheme(str(tmp_path/'still-available.json'))
        assert controller.generate('recover fixture', False)
        qtbot.waitUntil(lambda: not controller.assistant.isBusy and not controller.assistant._workers, timeout=5000)
        assert controller.accept(), controller.message
        assert controller.noteCount == 2 and not source.started
        controller.undoTheme()
        assert controller._workspace.editor.state == before
    finally:
        controller.shutdown()


def test_unknown_image_capability_blocks_request_until_image_removed(qapp, qtbot, tmp_path):
    from PySide6.QtGui import QImage
    from ai_piano_studio.creative.media import normalize_image
    provider = _SerializedLoopProvider([(ProviderEvent(text_delta='fixture', finish_reason='stop'),)])
    controller, source, _ = configured(tmp_path, provider)
    try:
        controller.open()
        controller.assistant.selectProvider('compatible')
        image = QImage(8, 8, QImage.Format_RGB32)
        image.fill(0)
        controller._image = normalize_image(image)
        assert not controller.generate('picture fixture', False)
        assert not provider.requests and not source.started
        controller.clearImage()
        assert controller.generate('text only fixture', False)
        qtbot.waitUntil(lambda: not controller.assistant.isBusy and not controller.assistant._workers, timeout=5000)
        assert provider.requests and not source.started
    finally:
        controller.shutdown()


def test_preview_companion_studio_priority_and_suspend_never_restarts_audio(qapp, tmp_path, monkeypatch):
    from ai_piano_studio.creative import controller as module
    now, busy = [0.0], [False]
    monkeypatch.setattr(module, 'monotonic', lambda: now[0])
    controller, source, mixer = configured(tmp_path, busy=lambda: busy[0])
    controller._transport._clock = lambda: now[0]
    controller._activity = Collector()
    try:
        path = tmp_path/'theme.json'
        save_project_document(path, ProjectDocument(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.2),))))
        assert controller.loadTheme(str(path))
        controller.startCompanion()
        assert controller._arbiter.owner == 'companion' and len(source.started) == 1
        controller.audition(False)
        assert controller._arbiter.owner == 'preview' and len(source.started) == 2
        busy[0] = True  # Same priority predicate covers playback, recording and held keys.
        controller._transport.process()
        assert controller._arbiter.owner == ''
        now[0] = 4
        controller._tick()
        assert len(source.started) == 2
        busy[0] = False
        now[0] = 8
        controller._tick()
        assert len(source.started) == 3
        controller.suspend()
        count = len(source.started)
        now[0] = 12
        controller._tick()
        controller._transport.process()
        assert len(source.started) == count
        assert not controller._timer.isActive() and not controller._transport._timer.isActive()
        assert controller.companionState == 'paused'
    finally:
        controller.shutdown()
