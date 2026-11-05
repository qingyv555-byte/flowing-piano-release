import sys
from PySide6.QtCore import QProcess
from shiboken6 import isValid
from ai_piano_studio.creative.hotkey import DesktopCapture
from ai_piano_studio.creative.desktop import SourceToken


def scripted_capture(monkeypatch, script):
    from ai_piano_studio.creative import hotkey
    token = SourceToken(window=10, process=20, focus=30, application=r'C:\Windows\System32\notepad.exe')
    monkeypatch.setattr(hotkey, 'foreground_token', lambda: token)
    monkeypatch.setattr(hotkey, 'ime_composing', lambda _: False)
    monkeypatch.setattr(hotkey, 'helper_command', lambda: (sys.executable, ['-c', script]))
    return DesktopCapture(), token


def test_redirected_helper_roundtrip_uses_stdin_not_commandline(qapp, qtbot, monkeypatch):
    script = "import json,sys; t=json.loads(sys.stdin.buffer.read()); print(json.dumps({'ok':True,'capture':{'source':t,'text':'fixture only','method':'selection','truncated':False}}))"
    capture, token = scripted_capture(monkeypatch, script)
    results = []
    capture.ready.connect(results.append)
    capture.start()
    qtbot.waitUntil(lambda: not capture.busy)
    assert len(results) == 1
    assert results[0].text == 'fixture only' and results[0].source == token
    assert not capture._output
    assert not capture._deadline.isActive()


def test_helper_output_budget_is_rejected(qapp, qtbot, monkeypatch):
    capture, token = scripted_capture(monkeypatch, "import sys; sys.stdin.buffer.read(); sys.stdout.write('X'*50000)")
    results, statuses = [], []
    capture.ready.connect(results.append)
    capture.status.connect(statuses.append)
    capture.start()
    process = capture._process
    qtbot.waitUntil(lambda: not capture.busy)
    assert not results and not capture._output
    assert any('超过限制' in item for item in statuses)
    qtbot.waitUntil(lambda: not isValid(process) or process.state() == QProcess.NotRunning)


def test_helper_start_failure_cleans_up_and_stays_sanitized(qapp, qtbot, monkeypatch):
    from ai_piano_studio.creative import hotkey
    capture, _ = scripted_capture(monkeypatch, '')
    monkeypatch.setattr(hotkey, 'helper_command', lambda: ('Z:/unavailable/private-app.exe', []))
    results, statuses = [], []
    capture.ready.connect(results.append)
    capture.status.connect(statuses.append)
    capture.start()
    qtbot.waitUntil(lambda: not capture.busy)
    assert not results and not capture._deadline.isActive()
    assert 'private-app' not in ''.join(statuses)


def test_timeout_kills_helper_and_invalidates_queued_result(qapp, qtbot):
    capture = DesktopCapture()
    process = QProcess(capture)
    process.setProgram(sys.executable)
    process.setArguments(['-c', 'import time; time.sleep(60)'])
    process.start()
    assert process.waitForStarted(1000)
    capture._process = process
    capture._output = bytearray(b'private transient text')
    epoch = capture._epoch
    statuses = []
    capture.status.connect(statuses.append)
    capture._deadline.setInterval(20)
    capture._deadline.start()
    qtbot.waitUntil(lambda: not capture.busy)
    assert capture._epoch > epoch
    assert not capture._output
    assert statuses and '2' in statuses[-1]
    capture._finished(process, None, epoch)  # A stale result must not be parsed.


def test_overlay_flags_reject_activation(studio_session):
    from PySide6.QtCore import Qt
    app, engine, root, editor, playback = studio_session
    overlay = next(w for w in app.allWindows() if w.objectName() == 'creativeOverlay')
    assert overlay.flags() & Qt.WindowDoesNotAcceptFocus
    assert not overlay.isVisible()


from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
