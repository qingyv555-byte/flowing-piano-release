from PySide6.QtCore import QObject, QSettings, Signal
from ai_piano_studio.creative.controller import CreativeController
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document, load_project_document


class Mixer(QObject):
    voiceFinished = Signal(str)
    def start_audio(self, **kwargs):
        raise AssertionError('must not open audio for editing or generation')


def test_oversized_proposal_cannot_be_accepted(qapp, tmp_path):
    from unittest.mock import Mock
    from dataclasses import replace
    settings = ProviderSettingsService(settings=QSettings(str(tmp_path / 'bounded.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
    controller = CreativeController(tmp_path, object(), Mixer(), lambda: False, provider_settings=settings)
    try:
        controller.open()
        original = controller._workspace.proposals
        proposed = Mock(staged_state=ProjectState(notes=tuple(Note.create(pitch=60, start_time=0, duration=.5) for _ in range(257))))
        controller._workspace.proposals = proposed
        assert not controller.accept()
        proposed.apply_proposal.assert_not_called()
        proposed.staged_state = ProjectState(notes=(Note.create(pitch=60, start_time=61, duration=.5),))
        assert not controller.accept()
        proposed.apply_proposal.assert_not_called()
        assert controller.noteCount == 0
        controller._workspace.proposals = original
    finally:
        controller.shutdown()


def test_load_save_and_memory_do_not_require_audio_or_online_model(qapp, tmp_path):
    settings = ProviderSettingsService(settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
    controller = CreativeController(tmp_path, object(), Mixer(), lambda: False, provider_settings=settings)
    original = ProjectDocument(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.5),)))
    source = tmp_path / 'original.json'
    save_project_document(source, original)
    try:
        assert controller.assistant is None
        controller.open()
        assert controller.loadTheme(str(source)), controller.message
        assert controller.noteCount == 1
        assert controller.companionState == 'paused'
        target = tmp_path / 'saved.json'
        assert controller.saveTheme(str(target))
        assert load_project_document(target) == original
        assert not controller.remember('density', 'sparse', False)
        assert controller.remember('density', 'sparse', True)
        assert len(controller.memories) == 1
        assert controller.setScope('character-A')
        assert not controller.memories
        assert not controller.generate('x' * 2001, False)
        assert not controller.generate('雨夜', False)  # No credential; no fabricated music.
        assert controller.noteCount == 1
    finally:
        controller.shutdown()


def test_scope_switch_and_cancel_clear_temporary_conversation(qapp, tmp_path):
    from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
    settings = ProviderSettingsService(settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
    controller = CreativeController(tmp_path, object(), Mixer(), lambda: False, provider_settings=settings)
    try:
        controller.open()
        controller.assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, 'private story A'))
        assert controller.setScope('character-B')
        assert not controller.assistant.messages.entries
        controller.assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, 'private story B'))
        controller.cancel()
        assert not controller.assistant.messages.entries
    finally:
        controller.shutdown()


def test_real_agent_loop_to_saved_theme_and_local_companion(qapp, qtbot, tmp_path):
    from tests.music_agent.test_assistant_controller import _SerializedLoopProvider
    from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
    from tests.qt.test_creative_transport import Source, Mixer as SilentMixer
    from ai_piano_studio.creative.activity import ActivityAccumulator
    provider = _SerializedLoopProvider([
        (ProviderEvent(tool_calls=(ProviderToolCall('add', 'add_notes',
          '{"notes":[{"pitch":60,"start_time":0,"duration":0.5,"velocity":65}]}'),), finish_reason='tool_calls'),),
        (ProviderEvent(text_delta='本地测试主题。', finish_reason='stop'),),
    ])
    settings = ProviderSettingsService(settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(), provider_factory=lambda _: provider)
    settings.save('compatible', 'Offline fixture', 'https://fixture.example.test/v1', 'fixture-only', 'scripted-model', True)
    settings.record_capability_verification('compatible', 'scripted-model', provider.TOOLS)
    source, mixer = Source(), SilentMixer()
    controller = CreativeController(tmp_path, source, mixer, lambda: False, provider_settings=settings)
    class Collector(ActivityAccumulator):
        def start(self):
            self.clear()
        def stop(self):
            self.clear()
    controller._activity = Collector()
    try:
        controller.open()
        controller.assistant.selectProvider('compatible')
        assert controller.generate('雨后小路', False), controller.message
        qtbot.waitUntil(lambda: not controller.assistant.isBusy, timeout=5000)
        assert controller.assistant.proposal.get('status') == 'pending', controller.assistant.messages.entries
        assert controller.noteCount == 0
        assert not source.started
        controller.audition(False)
        assert source.started
        assert controller.accept()
        assert controller.noteCount == 1
        assert controller.saveTheme(str(tmp_path / 'theme.json'))
        request_count = len(provider.requests)
        controller.cancel()
        controller.startCompanion()
        assert controller.companionState == 'running'
        controller.pauseCompanion()
        assert controller.companionState == 'paused'
        assert len(provider.requests) == request_count
        assert load_project_document(tmp_path / 'theme.json').state.notes[0].pitch == 60
    finally:
        controller.shutdown()


def test_captured_text_needs_first_confirmation_and_changed_endpoint_reconfirmation(qapp, tmp_path, monkeypatch):
    from ai_piano_studio.creative.desktop import CaptureEnvelope, SourceToken
    settings = ProviderSettingsService(settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
    controller = CreativeController(tmp_path, object(), Mixer(), lambda: False, provider_settings=settings)
    calls, endpoint = [], ['https://a.test/v1']
    try:
        controller.open()
        monkeypatch.setattr(controller, '_binding', lambda: (endpoint[0], 'test-model'))
        monkeypatch.setattr(controller, 'generate', lambda text, global_memory: calls.append(text) or True)
        envelope = CaptureEnvelope(SourceToken(1, 2, 3, 'C:/Windows/notepad.exe'), '雨后', 'caret')
        controller._captured(envelope)
        assert not calls
        controller.sendCaptured(True)
        assert calls == ['雨后']
        controller._captured(envelope)
        assert calls == ['雨后', '雨后']
        endpoint[0] = 'https://b.test/v1'
        controller._captured(envelope)
        assert len(calls) == 2
        endpoint[0] = 'https://c.test/v1'
        controller.sendCaptured(False)
        assert len(calls) == 2
        controller.sendCaptured(False)
        assert len(calls) == 3
    finally:
        controller.shutdown()
