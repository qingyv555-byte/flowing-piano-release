from pathlib import Path


def test_windows_launcher_falls_back_to_a_recoverable_runtime() -> None:
    root = Path(__file__).parents[2]
    launcher = (root / "Open-Studio.cmd").read_text(encoding="utf-8")

    assert ".venv\\Scripts\\pythonw.exe" in launcher
    assert ".venv-dev\\Scripts\\pythonw.exe" in launcher
    assert "setup-development-environment.ps1" in launcher
    assert "No usable Python 3.12 runtime was found" in launcher
    assert "PySide6, ai_piano_studio" in launcher
    assert 'set "CONSOLE_PYTHON=%~dp1python.exe"' in launcher
    alias = (root / "启动AI钢琴工作室.bat").read_text(encoding="utf-8")
    assert 'call "%~dp0Open-Studio.cmd" %*' in alias
    assert '--profile-home' not in alias


def test_development_setup_accepts_the_windows_py_launcher() -> None:
    root = Path(__file__).parents[2]
    setup = (root / "tools" / "setup-development-environment.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "Get-Command py -ErrorAction" in setup
    assert '"-3.12"' in setup
    assert "foreach ($option in $candidates)" in setup
    assert ".venv-dev uses an unsupported interpreter" in setup
