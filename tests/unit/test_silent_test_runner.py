"""No audio device is opened: verify cleanup even when pytest aborts."""
from contextlib import contextmanager

import pytest

from tools import run_silent_tests as runner


@pytest.mark.parametrize('previous', [False, True])
@pytest.mark.parametrize('error', [None, RuntimeError, KeyboardInterrupt])
def test_mute_restores_original_state_on_all_normal_exit_paths(previous, error):
    state = [previous]
    def exercise():
        with runner.temporary_mute(lambda: state[0], lambda value: state.__setitem__(0, value)):
            assert state[0] is True
            if error:
                raise error('interrupted test run')
    if error:
        with pytest.raises(error):
            exercise()
    else:
        exercise()
    assert state[0] is previous


def test_pytest_failure_exit_code_is_returned_only_after_restore(monkeypatch):
    calls = []
    @contextmanager
    def session():
        calls.append('mute')
        try:
            yield
        finally:
            calls.append('restore')
    monkeypatch.setattr(runner, 'mute_test_session', session)
    monkeypatch.setattr(runner.pytest, 'main', lambda args: calls.append(args) or 1)
    assert runner.run_tests(['one-test']) == 1
    assert calls == ['mute', ['one-test'], 'restore']


def test_failed_mute_verification_restores_state_and_refuses_tests():
    writes = []
    with pytest.raises(RuntimeError, match='did not mute'):
        with runner.temporary_mute(lambda: False, writes.append):
            pytest.fail('audible tests must not start')
    assert writes == [True, False]
