import pytest
from ai_piano_studio.creative.desktop import SourceToken, CaptureEnvelope, validate_capture, recent_phrase, SourcePermissions


def test_capture_rejects_focus_change_password_ime_and_unapproved_source():
    token = SourceToken(1, 2, 3, 'C:/Windows/notepad.exe')
    envelope = CaptureEnvelope(token, '雨后的小路。', 'caret', False)
    assert validate_capture(envelope, token, {'notepad.exe'}) == envelope
    with pytest.raises(ValueError):
        validate_capture(envelope, SourceToken(2, 2, 3, token.application), {'notepad.exe'})
    for secret, composing in [(True, False), (False, True)]:
        with pytest.raises(ValueError):
            validate_capture(envelope, token, {'notepad.exe'}, password=secret, composing=composing)
    with pytest.raises(ValueError):
        validate_capture(envelope, token, {'other.exe'})


def test_text_is_bounded_and_selects_only_last_phrase():
    assert recent_phrase('第一句。第二句。\n') == '第二句。'
    assert recent_phrase('First. Last!') == 'Last!'
    token = SourceToken(1, 2, 3, 'notepad.exe')
    with pytest.raises(ValueError):
        validate_capture(CaptureEnvelope(token, 'x'*2001, 'caret', False), token, {'notepad.exe'})


def test_permission_is_bound_to_source_endpoint_model_and_range():
    permissions = SourcePermissions()
    permissions.allow('notepad.exe', 'https://a.test/v1', 'model', 'caret')
    assert permissions.permits('notepad.exe', 'https://a.test/v1', 'model', 'caret')
    assert not permissions.permits('notepad.exe', 'https://b.test/v1', 'model', 'caret')
    assert not permissions.permits('notepad.exe', 'https://a.test/v1', 'other', 'caret')
    assert not permissions.permits('notepad.exe', 'https://a.test/v1', 'model', 'selection')
    permissions.clear()
    assert not permissions.permits('notepad.exe', 'https://a.test/v1', 'model', 'caret')


def test_permissions_persist_only_on_this_profile_and_revoke(tmp_path):
    path = tmp_path/'permissions.json'
    first = SourcePermissions(path)
    first.allow('notepad.exe', 'https://a.test/v1', 'model', 'caret')
    second = SourcePermissions(path)
    assert second.permits('notepad.exe', 'https://a.test/v1', 'model', 'caret')
    assert not SourcePermissions(tmp_path/'other.json').permits('notepad.exe', 'https://a.test/v1', 'model', 'caret')
    second.clear()
    assert not SourcePermissions(path).permits('notepad.exe', 'https://a.test/v1', 'model', 'caret')
