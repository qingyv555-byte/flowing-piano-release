"""Connection testing leaves Qt responsive and ignores outdated configuration."""
from threading import Event, get_ident

from PySide6.QtCore import QTimer

from tests.music_agent.test_assistant_controller import _assistant, _wait_until


def test_connection_probe_runs_off_qt_thread_and_emits_real_result(qapp, tmp_path):
    assistant = _assistant(tmp_path)
    started, release = Event(), Event()
    worker_threads = []

    class Provider:
        def test_connection(self):
            worker_threads.append(get_ident())
            started.set()
            assert release.wait(3)

    assistant._provider_settings._provider_factory = lambda _: Provider()
    qt_thread = get_ident()
    ticks = []
    try:
        assert assistant.testConnectionAsync(assistant.currentProviderId)
        assert started.wait(1)
        assert assistant.connectionTesting
        assert not assistant.testConnectionAsync(assistant.currentProviderId)
        QTimer.singleShot(0, lambda: ticks.append(True))
        _wait_until(qapp, lambda: bool(ticks))
        assert worker_threads == [worker_threads[0]] and worker_threads[0] != qt_thread
        release.set()
        _wait_until(qapp, lambda: not assistant.connectionTesting)
        assert assistant.connectionResult['code'] == 'SUCCESS'
        assert assistant.connectionTested
    finally:
        release.set()
        assistant.shutdown()


def test_connection_probe_cannot_relabel_an_edited_connection(qapp, tmp_path):
    assistant = _assistant(tmp_path)
    started, release = Event(), Event()

    class Provider:
        def test_connection(self):
            started.set()
            assert release.wait(3)

    assistant._provider_settings._provider_factory = lambda _: Provider()
    try:
        assert assistant.testConnectionAsync(assistant.currentProviderId)
        assert started.wait(1)
        result = assistant.saveProviderSettings('openai', 'OpenAI', 'https://api.openai.com/v1',
                                                'test-new-credential', 'gpt-4o-mini', True)
        assert result['success']
        release.set()
        _wait_until(qapp, lambda: not assistant.connectionTesting)
        assert assistant.connectionResult['code'] == 'UNKNOWN_ERROR'
        assert '尚未测试' in assistant.connectionResult['message']
        assert not assistant.connectionTested
    finally:
        release.set()
        assistant.shutdown()


def test_unknown_connection_failure_is_distinguishable_from_not_tested(qapp, tmp_path):
    assistant = _assistant(tmp_path)

    class Provider:
        def test_connection(self):
            raise RuntimeError('test-only unclassified failure')

    assistant._provider_settings._provider_factory = lambda _: Provider()
    try:
        assert assistant.testConnectionAsync(assistant.currentProviderId)
        _wait_until(qapp, lambda: not assistant.connectionTesting)
        assert assistant.connectionResult['code'] == 'UNKNOWN_ERROR'
        assert assistant.connectionTested
        assert assistant.selectModel(assistant.currentModelId)
        assert assistant.connectionTested, 'Reselecting the same model must retain the failed-test state'
        assistant.selectProvider('deepseek')
        assert not assistant.connectionTested
    finally:
        assistant.shutdown()
