"""Foreground identity only; never reads titles, text, clipboard or keyboard."""
import ctypes
from ctypes import wintypes

from .desktop import SourceToken


class GuiInfo(ctypes.Structure):
    # Keep a stable ctypes type: POINTER caches its type argument permanently.
    _fields_ = [('size', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('active', wintypes.HWND), ('focus', wintypes.HWND),
                ('capture', wintypes.HWND), ('menu', wintypes.HWND),
                ('move', wintypes.HWND), ('caret', wintypes.HWND), ('rect', wintypes.RECT)]


def foreground_token():
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GuiInfo)]
    hwnd = user.GetForegroundWindow()
    pid = wintypes.DWORD()
    thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    info = GuiInfo()
    info.size = ctypes.sizeof(info)
    if not hwnd or not user.GetGUIThreadInfo(thread, ctypes.byref(info)):
        raise ValueError('无法确认前台输入控件')
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        raise ValueError('无法确认来源应用权限')
    try:
        size = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            raise ValueError('无法确认来源应用')
        return SourceToken(int(hwnd), pid.value, int(info.focus or 0), name.value)
    finally:
        kernel.CloseHandle(handle)


def ime_composing(token):
    imm = ctypes.WinDLL('imm32', use_last_error=True)
    imm.ImmGetContext.argtypes = [wintypes.HWND]
    imm.ImmGetContext.restype = wintypes.HANDLE
    imm.ImmGetCompositionStringW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
    imm.ImmGetCompositionStringW.restype = ctypes.c_long
    imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
    context = imm.ImmGetContext(token.focus)
    if not context:
        return False
    try:
        return imm.ImmGetCompositionStringW(context, 8, None, 0) > 0
    finally:
        imm.ImmReleaseContext(token.focus, context)
