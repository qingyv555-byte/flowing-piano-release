"""Opt-in RegisterHotKey and disposable bounded UIA subprocess."""
from dataclasses import asdict
import ctypes
from ctypes import wintypes
import json
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QProcess, QTimer, Signal

from .desktop import CaptureEnvelope, SourceToken, validate_capture
from .windows_source import foreground_token, ime_composing
from .helper_runtime import helper_command


class NativeHotkey(QAbstractNativeEventFilter):
    ID = 0x6A31

    def __init__(self, app, callback):
        super().__init__()
        self.app, self.callback, self.registered = app, callback, False

    def enable(self, key=0x77):
        self.close()
        if sys.platform != 'win32' or key not in range(0x70, 0x7C):
            raise ValueError('请选择可用的 F1–F12 热键')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        if not self.user.RegisterHotKey(None, self.ID, 0x4000, key):
            raise ValueError('热键已被占用，请选择其他按键')
        self.registered = True
        self.app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if self.registered and bytes(event_type) in {b'windows_generic_MSG', b'windows_dispatcher_MSG'}:
            event = wintypes.MSG.from_address(int(message))
            if event.message == 0x0312 and event.wParam == self.ID:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            if not self.user.UnregisterHotKey(None, self.ID):
                raise ctypes.WinError(ctypes.get_last_error())
            self.app.removeNativeEventFilter(self)
            self.registered = False


class DesktopCapture(QObject):
    ready = Signal(object)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._epoch = 0
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(2000)
        self._deadline.timeout.connect(self._timeout)

    @property
    def busy(self):
        return self._process is not None

    def start(self):
        if self.busy:
            self.cancel()
            self.status.emit('已取消读取')
            return
        try:
            token = foreground_token()
            validate_capture(CaptureEnvelope(token, 'probe'), token, {'notepad.exe', 'explorer.exe'}, composing=ime_composing(token))
            self._epoch += 1
            epoch = self._epoch
            process = QProcess(self)
            self._process = process
            self._output = bytearray()
            program, arguments = helper_command()
            process.setProgram(program)
            process.setArguments(arguments)
            process.setStandardErrorFile(QProcess.nullDevice())
            process.readyReadStandardOutput.connect(lambda: self._read(process, epoch))
            process.finished.connect(lambda *_: self._finished(process, token, epoch))
            process.errorOccurred.connect(lambda *_: self._error(process, epoch))
            process.started.connect(lambda: self._send_request(process, token, epoch))
            self.status.emit('正在读取获准来源的光标文本或所选图片…再次按热键可取消')
            self._deadline.start()
            process.start()
        except Exception as error:
            self.status.emit(str(error))

    def _send_request(self, process, token, epoch):
        if epoch == self._epoch:
            process.write(json.dumps(asdict(token)).encode('utf-8'))
            process.closeWriteChannel()

    def _read(self, process, epoch):
        if epoch != self._epoch:
            return
        self._output.extend(bytes(process.readAllStandardOutput()))
        if len(self._output) > 40_000:
            self.cancel()
            self.status.emit('读取结果超过限制，已丢弃')

    def _finished(self, process, token, epoch):
        if epoch != self._epoch:
            return
        self._deadline.stop()
        self._read(process, epoch)
        if epoch != self._epoch:
            return
        self._process = None
        try:
            result = json.loads(self._output)
            if not result.get('ok'):
                raise ValueError(result.get('error', '读取失败，请手动粘贴'))
            value = result['capture']
            envelope = CaptureEnvelope(SourceToken(**value['source']), value['text'], value['method'], value['truncated'])
            validate_capture(envelope, foreground_token(), {'notepad.exe', 'explorer.exe'}, composing=ime_composing(token))
            self.ready.emit(envelope)
        except Exception:
            self.status.emit('未取得可确认的光标文本。请选中后重试或手动粘贴。')
        finally:
            self._output.clear()
            # Signal closures can hold the capture's last Python reference.
            # Detach before deferred destruction so releasing those closures
            # cannot destroy the parent while it still owns this child.
            process.setParent(None)
            process.deleteLater()

    def _error(self, process, epoch):
        if epoch == self._epoch:
            self.cancel()
            self.status.emit('读取进程无法启动，请手动粘贴')

    def _timeout(self):
        self.cancel()
        self.status.emit('读取超过 2 秒，已终止；请手动粘贴。')

    def cancel(self):
        self._epoch += 1
        self._deadline.stop()
        process, self._process = self._process, None
        if process is not None:
            process.setParent(None)
            process.finished.connect(process.deleteLater)
            process.kill()
            if process.state() == QProcess.NotRunning:
                process.deleteLater()
        if hasattr(self, '_output'):
            self._output.clear()
