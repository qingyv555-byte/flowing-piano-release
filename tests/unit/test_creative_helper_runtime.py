import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_frozen_helper_reenters_own_executable_without_module_or_working_directory(monkeypatch):
    from ai_piano_studio.creative.helper_runtime import helper_command
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', r'C:\Program Files\Flowing Piano\AI-Piano-Studio.exe')
    assert helper_command() == (sys.executable, ['--desktop-capture-helper'])
    monkeypatch.setattr(sys, 'frozen', False)
    assert helper_command() == (sys.executable, ['-m', 'ai_piano_studio.creative.capture_helper'])


def test_helper_entry_exits_before_importing_application_and_does_not_log_input():
    studio = Path(__file__).parents[2] / 'src/ai_piano_studio/studio.py'
    script = """
import runpy, sys
sys.argv = ['app.exe', '--desktop-capture-helper']
try:
    runpy.run_path(STUDIO, run_name='__main__')
except SystemExit as result:
    assert result.code == 0
assert 'PySide6.QtWidgets' not in sys.modules
assert 'ai_piano_studio.audio.mixer' not in sys.modules
""".replace('STUDIO', repr(str(studio)))
    result = subprocess.run([sys.executable, '-c', script], input=b'private-invalid-request', capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert json.loads(result.stdout)['ok'] is False
    assert b'private-invalid-request' not in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='windowed Python pipe handles')
def test_windowed_python_without_standard_streams_returns_sanitized_error():
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    script = "import sys; sys.stdin=sys.stdout=sys.stderr=None; from ai_piano_studio.creative.capture_helper import main; raise SystemExit(main())"
    result = subprocess.run([str(pythonw), '-c', script],
                            input=b'private-invalid-request', capture_output=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(result.stdout)['ok'] is False
    assert b'private-invalid-request' not in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows UIA typelib preparation')
def test_spec_prepares_uia_modules_without_building_or_loading_ui():
    import importlib
    import runpy
    root = Path(__file__).parents[2]
    calls = []
    class StopBeforeBuild(Exception):
        pass
    def analysis(scripts, **kwargs):
        calls.append((scripts, kwargs))
        raise StopBeforeBuild
    with pytest.raises(StopBeforeBuild):
        runpy.run_path(str(root/'ai_piano_studio.spec'), init_globals={'SPECPATH': str(root), 'Analysis': analysis})
    scripts, options = calls[0]
    assert scripts == ['src/ai_piano_studio/studio.py']
    modules = [importlib.import_module(name) for name in options['hiddenimports']]
    assert len(modules) == 2
    assert all(hasattr(module, 'IUIAutomationTextPattern2') for module in modules)
