"""Windows session/power notifications; resume deliberately has no action."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter


def should_pause(message, reason):
    return ((message == 0x02B1 and reason in {2, 4, 6, 7})
            or (message == 0x0218 and reason == 4))


class SessionGuard(QAbstractNativeEventFilter):
    def __init__(self, app, window, pause):
        super().__init__()
        self._app, self._pause = app, pause
        self._hwnd = None
        if sys.platform == 'win32' and app.platformName() != 'offscreen':
            self._wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
            self._wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            self._wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
            self._wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            self._wts.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
            hwnd = int(window.winId())
            if not self._wts.WTSRegisterSessionNotification(hwnd, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd
            app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if self._hwnd and bytes(event_type) in {b'windows_generic_MSG', b'windows_dispatcher_MSG'}:
            event = wintypes.MSG.from_address(int(message))
            if should_pause(event.message, event.wParam):
                self._pause()
        return False, 0

    def close(self):
        if self._hwnd:
            self._app.removeNativeEventFilter(self)
            if not self._wts.WTSUnRegisterSessionNotification(self._hwnd):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = None
