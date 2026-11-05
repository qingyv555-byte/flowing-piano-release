"""Helper process launch and windowed-executable pipes, without Qt or UI access."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys


HELPER_FLAG = '--desktop-capture-helper'


def helper_command():
    if getattr(sys, 'frozen', False):
        # PyInstaller's executable is the app, not a Python interpreter. Reuse
        # the trusted installed executable; no script/CWD/_MEIPASS lookup.
        return sys.executable, [HELPER_FLAG]
    return sys.executable, ['-m', 'ai_piano_studio.creative.capture_helper']


def uia_typelib_path():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError('system directory unavailable')
    return str(Path(buffer.value) / 'UIAutomationCore.dll')


def _pipe_api(channel):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    handle = kernel.GetStdHandle(channel & 0xFFFFFFFF)
    if not handle or handle == ctypes.c_void_p(-1).value or kernel.GetFileType(handle) != 3:
        raise OSError('helper requires redirected pipes')
    types = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.ReadFile.argtypes = kernel.WriteFile.argtypes = types
    kernel.ReadFile.restype = kernel.WriteFile.restype = wintypes.BOOL
    return kernel, handle


def read_request(limit=8193):
    if sys.stdin is not None:
        return sys.stdin.buffer.read(limit)
    # Windowed PyInstaller sets sys.stdin/stdout to None even with inherited
    # pipe handles. Never reopen CONIN/CONOUT or allocate a console window.
    kernel, handle = _pipe_api(-10)
    result = bytearray()
    while len(result) < limit:
        buffer = ctypes.create_string_buffer(limit-len(result))
        count = wintypes.DWORD()
        if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
            if ctypes.get_last_error() == 109:  # ERROR_BROKEN_PIPE = parent EOF
                break
            raise OSError('helper request read failed')
        if not count.value:
            break
        result.extend(buffer.raw[:count.value])
    return bytes(result)


def write_reply(data):
    if len(data) > 40_000:
        raise ValueError('helper reply exceeds budget')
    if sys.stdout is not None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return
    kernel, handle = _pipe_api(-11)
    sent = 0
    while sent < len(data):
        buffer = ctypes.create_string_buffer(data[sent:])
        count = wintypes.DWORD()
        if not kernel.WriteFile(handle, buffer, len(data)-sent, ctypes.byref(count), None) or not count.value:
            raise OSError('helper reply write failed')
        sent += count.value
