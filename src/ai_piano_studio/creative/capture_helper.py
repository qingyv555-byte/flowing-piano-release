"""Disposable UIA helper. Parent kills this process after the two-second deadline."""
from dataclasses import asdict
import json
import logging
from pathlib import PureWindowsPath

from .desktop import CaptureEnvelope, SourceToken, recent_phrase, validate_capture
from .windows_source import foreground_token, ime_composing
from .helper_runtime import read_request, write_reply, uia_typelib_path


def capture(expected):
    import comtypes.client
    if PureWindowsPath(expected.application).name.lower() == 'explorer.exe':
        if foreground_token() != expected:
            raise ValueError('source changed')
        shell = comtypes.client.CreateObject('Shell.Application', dynamic=True)
        windows = shell.Windows()
        for index in range(min(int(windows.Count), 128)):
            window = windows.Item(index)
            if int(window.HWND) != expected.window:
                continue
            selected = window.Document.SelectedItems()
            if selected.Count != 1:
                raise ValueError('select exactly one image')
            item = selected.Item(0)
            path = str(item.Path)
            if item.IsFolder or PureWindowsPath(path).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}:
                raise ValueError('select a supported local still image')
            return validate_capture(CaptureEnvelope(expected, path, 'image-selection'), foreground_token(), {'explorer.exe'})
        raise ValueError('selected file reference is unavailable')
    uia = comtypes.client.GetModule(uia_typelib_path())
    client = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
    if foreground_token() != expected or ime_composing(expected):
        raise ValueError('focus or IME changed')
    element = client.GetFocusedElement()
    if element.CurrentIsPassword or element.CurrentProcessId != expected.process:
        raise ValueError('password or wrong process')
    runtime_id = tuple(element.GetRuntimeId())
    pattern = element.GetCurrentPattern(10014).QueryInterface(uia.IUIAutomationTextPattern)
    selection = pattern.GetSelection()
    text, method, truncated = '', 'selection', False
    if selection.Length == 1:
        text = selection.GetElement(0).GetText(2001)
        if len(text) > 2000:
            raise ValueError('selection too large')
    if not text.strip():
        pattern2 = element.GetCurrentPattern(10024).QueryInterface(uia.IUIAutomationTextPattern2)
        active, caret = pattern2.GetCaretRange()
        if not active:
            raise ValueError('inactive caret')
        bounded = caret.Clone()
        moved = bounded.MoveEndpointByUnit(0, 0, -2000)
        text = recent_phrase(bounded.GetText(2000))
        method, truncated = 'caret', abs(moved) >= 2000
    current_element = client.GetFocusedElement()
    if tuple(current_element.GetRuntimeId()) != runtime_id or current_element.CurrentIsPassword:
        raise ValueError('focused element changed')
    return validate_capture(CaptureEnvelope(expected, text, method, truncated), foreground_token(), {'notepad.exe'}, composing=ime_composing(expected))


def main():
    # No captured content, exceptions or dependency debug messages go to logs.
    logging.disable(logging.CRITICAL)
    try:
        raw = read_request()
        if len(raw) > 8192:
            raise ValueError('request too large')
        expected = SourceToken(**json.loads(raw))
        # Validate the allowlist before asking any UIA control for content.
        validate_capture(CaptureEnvelope(expected, 'probe'), expected, {'notepad.exe', 'explorer.exe'})
        result = {'ok': True, 'capture': asdict(capture(expected))}
    except Exception:
        result = {'ok': False, 'error': '此应用当前无法安全读取光标文本，请选中或手动粘贴。'}
    try:
        write_reply(json.dumps(result, ensure_ascii=False).encode('utf-8'))
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
