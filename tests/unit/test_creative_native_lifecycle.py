"""Native contracts via fake DLLs only: never inspect or hook the real desktop."""
import ctypes
import sys
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_piano_studio.creative import activity, windows_source

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows native ABI with fake DLLs')


def test_rejected_capture_does_not_accumulate_ctypes_types(monkeypatch):
    api = SimpleNamespace(**{name: Mock(return_value=0) for name in (
        'GetForegroundWindow', 'GetWindowThreadProcessId', 'OpenProcess',
        'QueryFullProcessImageNameW', 'CloseHandle', 'GetGUIThreadInfo')})
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: api)
    with pytest.raises(ValueError):
        windows_source.foreground_token()
    count = len(ctypes._pointer_type_cache)
    for _ in range(100):
        with pytest.raises(ValueError):
            windows_source.foreground_token()
    assert len(ctypes._pointer_type_cache) == count


def test_mouse_hook_cycles_release_and_do_not_accumulate_types(monkeypatch):
    api = SimpleNamespace(**{name: Mock(return_value=1) for name in (
        'SetWindowsHookExW', 'CallNextHookEx', 'UnhookWindowsHookEx', 'GetModuleHandleW')})
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: api)
    class Event(ctypes.Structure):
        _fields_ = [('pt', wintypes.POINT), ('mouseData', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    event = Event()
    collector = activity.WindowsMouseActivity()
    def cycle():
        collector.start()
        collector._callback(0, 0x0200, ctypes.addressof(event))
        event.pt.x += 10
        collector._callback(0, 0x0200, ctypes.addressof(event))
        assert collector.take() == (10, 0)
        collector.stop()
        assert collector._callback is None and collector._hook is None
        assert collector.take() == (0, 0)
    cycle()
    count = len(ctypes._pointer_type_cache)
    for _ in range(100):
        cycle()
    assert len(ctypes._pointer_type_cache) == count
    assert api.UnhookWindowsHookEx.call_count == 101


def test_hotkey_register_dispatch_and_unregistration_use_only_fake_api(monkeypatch):
    from ai_piano_studio.creative.hotkey import NativeHotkey
    api = SimpleNamespace(RegisterHotKey=Mock(return_value=1), UnregisterHotKey=Mock(return_value=1))
    app = SimpleNamespace(installNativeEventFilter=Mock(), removeNativeEventFilter=Mock())
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: api)
    calls = []
    hotkey = NativeHotkey(app, lambda: calls.append('triggered'))
    hotkey.enable(0x77)
    assert api.RegisterHotKey.call_args.args == (None, hotkey.ID, 0x4000, 0x77)
    event = wintypes.MSG()
    event.message, event.wParam = 0x0312, hotkey.ID
    assert hotkey.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(event)) == (True, 0)
    hotkey.close()
    assert not hotkey.registered and calls == ['triggered']
    assert hotkey.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(event)) == (False, 0)
    api.RegisterHotKey.return_value = 0
    with pytest.raises(ValueError):
        hotkey.enable()
    assert not hotkey.registered


def test_native_session_messages_pause_without_resume_or_desktop_registration():
    from ai_piano_studio.creative.session_guard import SessionGuard
    app = SimpleNamespace(platformName=lambda: 'offscreen')
    calls = []
    guard = SessionGuard(app, None, lambda: calls.append('pause'))
    guard._hwnd = 1  # Test only the decoder; no real OS registration.
    event = wintypes.MSG()
    for message, reason in ((0x02B1, 7), (0x0218, 4), (0x02B1, 8), (0x0218, 18)):
        event.message, event.wParam = message, reason
        guard.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(event))
    assert calls == ['pause', 'pause']
    guard._hwnd = None
    guard.close()
