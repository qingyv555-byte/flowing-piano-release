"""Explicitly enabled mouse-only aggregation; no keyboard hook or event history."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


class MouseData(ctypes.Structure):
    # Reuse across hook lifetimes; ctypes.POINTER retains each distinct type.
    _fields_ = [('pt', wintypes.POINT), ('mouseData', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('extra', ctypes.c_size_t)]


class ActivityAccumulator:
    def __init__(self):
        self.clear()

    def add(self, movement=0, wheel=0):
        self._movement = min(1000, self._movement + max(0, movement))
        self._wheel = min(100, self._wheel + abs(wheel))

    def take(self):
        result = self._movement, self._wheel
        self.clear()
        return result

    def clear(self):
        self._movement = self._wheel = 0


class WindowsMouseActivity:
    """Install on the Qt message-loop thread; callbacks only aggregate counts.

    Source: Microsoft LowLevelMouseProc contract. Every event is passed on.
    Coordinates exist only as the last position to derive distance; there is
    no trajectory, target window, click target, or file/log persistence.
    """

    def __init__(self):
        self._hook = None
        self._callback = None
        self._previous = None
        self._counts = ActivityAccumulator()

    def start(self):
        if self._hook:
            return
        if sys.platform != 'win32':
            raise RuntimeError('活动陪伴目前仅支持 Windows')
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def callback(code, message, address):
            if code >= 0 and message in (0x0200, 0x020A, 0x020E):
                event = ctypes.cast(address, ctypes.POINTER(MouseData)).contents
                if not event.flags & 1:  # Ignore injected mouse events.
                    if message == 0x0200:
                        point = event.pt.x, event.pt.y
                        if self._previous is not None:
                            self._counts.add(min(100, abs(point[0] - self._previous[0]) + abs(point[1] - self._previous[1])))
                        self._previous = point
                    else:
                        self._counts.add(wheel=ctypes.c_short(event.mouseData >> 16).value / 120)
            return user32.CallNextHookEx(None, code, message, address)

        self._callback = callback_type(callback)
        self._user32 = user32
        self._hook = user32.SetWindowsHookExW(14, self._callback, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self._callback = None
            raise ctypes.WinError(ctypes.get_last_error())

    def take(self):
        return self._counts.take()

    def stop(self):
        if self._hook:
            if not self._user32.UnhookWindowsHookEx(self._hook):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hook = None
            self._callback = None
        self._previous = None
        self._counts.clear()
