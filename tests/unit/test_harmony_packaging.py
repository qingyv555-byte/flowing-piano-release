from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import tomllib

import pytest
from PySide6.QtCore import QCoreApplication

from ai_piano_studio import __main__ as app_main


ROOT = Path(__file__).parents[2]


def test_package_data_and_dev_dependencies_include_harmony_release_assets() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    package_data = config["tool"]["setuptools"]["package-data"]["ai_piano_studio"]
    assert "resources/help/*.md" in package_data
    assert any(
        dependency.lower().startswith("pyinstaller>=6") and dependency.endswith("<7")
        for dependency in config["project"]["optional-dependencies"]["dev"]
    )


def test_pyinstaller_spec_collects_data_from_the_spec_worktree(
    tmp_path, monkeypatch
) -> None:
    spec_path = tmp_path / "current-worktree"
    package_path = spec_path / "src" / "ai_piano_studio"
    qml_path = package_path / "qml"
    resources_path = package_path / "resources"
    qml_path.mkdir(parents=True)
    resources_path.mkdir()
    piano_root = spec_path / "resources" / "piano"
    piano_root.mkdir(parents=True)
    (piano_root / "banks.json").write_text('{"banks": []}', encoding="utf-8")
    (piano_root / "banks").mkdir()
    (piano_root / "LICENSE-SALAMANDER.txt").write_text("license", encoding="utf-8")
    (piano_root / "PROVENANCE-SALAMANDER.json").write_text("{}", encoding="utf-8")
    (piano_root / "PROVENANCE-SALAMANDER.md").write_text("provenance", encoding="utf-8")
    (spec_path / "THIRD_PARTY_NOTICES.md").write_text("notice", encoding="utf-8")

    wrong_package_path = tmp_path / "editable-install" / "ai_piano_studio"
    collected = [
        (str(wrong_package_path / "qml"), "ai_piano_studio/qml"),
        (str(wrong_package_path / "resources"), "ai_piano_studio/resources"),
    ]
    calls: dict[str, object] = {}
    hooks = ModuleType("PyInstaller.utils.hooks")

    def collect_data_files(package: str):
        calls["package"] = package
        return collected

    hooks.collect_data_files = collect_data_files
    monkeypatch.setitem(sys.modules, "PyInstaller", ModuleType("PyInstaller"))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", ModuleType("PyInstaller.utils"))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)

    def analysis(*args, **kwargs):
        calls["analysis_datas"] = kwargs["datas"]
        return SimpleNamespace(
            scripts=[], pure=[], binaries=[], datas=kwargs["datas"], zipped_data=[]
        )

    result = runpy.run_path(
        str(ROOT / "ai_piano_studio.spec"),
        init_globals={
            "SPECPATH": str(spec_path),
            "Analysis": analysis,
            "PYZ": lambda *args, **kwargs: object(),
            "EXE": lambda *args, **kwargs: object(),
            "COLLECT": lambda *args, **kwargs: object(),
        },
    )

    expected = [
        (str(spec_path / "build" / "release-notices"), "ai_piano_studio/resources/third-party"),
        (str(qml_path), "ai_piano_studio/qml"),
        (str(resources_path), "ai_piano_studio/resources"),
        (str(piano_root / "banks.json"), "ai_piano_studio/resources/piano"),
        (str(piano_root / "banks"), "ai_piano_studio/resources/piano/banks"),
        (str(piano_root / "LICENSE-SALAMANDER.txt"), "ai_piano_studio/resources/piano"),
        (str(piano_root / "PROVENANCE-SALAMANDER.json"), "ai_piano_studio/resources/piano"),
        (str(piano_root / "PROVENANCE-SALAMANDER.md"), "ai_piano_studio/resources/piano"),
        (str(spec_path / "THIRD_PARTY_NOTICES.md"), "ai_piano_studio/resources"),
        (str(spec_path / "LICENSE"), "ai_piano_studio/resources"),
        (str(spec_path / "COPYRIGHT"), "ai_piano_studio/resources"),
        (str(spec_path / "docs" / "LICENSING.md"), "ai_piano_studio/resources"),
    ]
    assert calls["analysis_datas"] == expected
    assert result["datas"] == expected


@pytest.mark.parametrize("pyinstaller_exit_code", [0, 17])
def test_portable_build_temporarily_prioritizes_the_project_source(
    tmp_path, pyinstaller_exit_code
) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None

    # Exercise the real PowerShell path handling in an isolated build tree.
    # Notice collection has its own integration coverage; this unit test must
    # not download sources or mutate the working release build.
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    (project / "src").mkdir()
    script = (ROOT / "tools" / "build-portable.ps1").read_text(encoding="utf-8-sig")
    script = script.replace('$DevelopmentPython = Join-Path $ProjectRoot ".venv-dev\\Scripts\\python.exe"',
                            "$DevelopmentPython = '" + sys.executable.replace("'", "''") + "'")
    (project / "tools" / "build-portable.ps1").write_text(script, encoding="utf-8-sig")
    (project / "tools" / "collect-release-notices.py").write_text("# Isolated notice collector double.\n", encoding="utf-8")
    (project / "tools" / "source_identity.py").write_text('print(\'{"git_commit":"fixture000"}\')\n', encoding="utf-8")

    wrong_site = tmp_path / "editable-install"
    fake_pyinstaller = wrong_site / "PyInstaller"
    fake_pyinstaller.mkdir(parents=True)
    (fake_pyinstaller / "__init__.py").write_text("", encoding="utf-8")
    (fake_pyinstaller / "__main__.py").write_text(
        """\
import json
import os
from pathlib import Path
import sys

Path(os.environ["CAPTURE_PYINSTALLER_PATHS"]).write_text(
    json.dumps({"pythonpath": os.environ.get("PYTHONPATH", ""), "sys_path": sys.path}),
    encoding="utf-8",
)
raise SystemExit(int(os.environ["FAKE_PYINSTALLER_EXIT_CODE"]))
""",
        encoding="utf-8",
    )
    capture_path = tmp_path / "pyinstaller-paths.json"
    after_path = tmp_path / "pythonpath-after.txt"
    failure_path = tmp_path / "build-failure.txt"
    original_pythonpath = str(wrong_site)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": original_pythonpath,
            "CAPTURE_PYINSTALLER_PATHS": str(capture_path),
            "CAPTURE_PYTHONPATH_AFTER": str(after_path),
            "CAPTURE_BUILD_FAILURE": str(failure_path),
            "FAKE_PYINSTALLER_EXIT_CODE": str(pyinstaller_exit_code),
        }
    )
    command = (
        "$failure = ''\n"
        f"try {{ & '{project / 'tools' / 'build-portable.ps1'}' -SkipClean }} "
        "catch { $failure = $_.Exception.Message }\n"
        "[System.IO.File]::WriteAllText("
        "$env:CAPTURE_PYTHONPATH_AFTER, [string]$env:PYTHONPATH)\n"
        "[System.IO.File]::WriteAllText("
        "$env:CAPTURE_BUILD_FAILURE, [string]$failure)"
    )

    subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=False,
    )

    assert capture_path.exists(), failure_path.read_text(encoding="utf-8")
    observed = json.loads(capture_path.read_text(encoding="utf-8"))
    project_src = (project / "src").resolve()
    pythonpath = [Path(entry).resolve() for entry in observed["pythonpath"].split(os.pathsep)]
    sys_path = [Path(entry).resolve() for entry in observed["sys_path"] if entry]
    assert pythonpath[0] == project_src
    assert sys_path.index(project_src) < sys_path.index(wrong_site.resolve())
    assert after_path.read_text(encoding="utf-8") == original_pythonpath
    failure = failure_path.read_text(encoding="utf-8")
    if pyinstaller_exit_code:
        assert failure == f"PyInstaller failed with exit code {pyinstaller_exit_code}."
    else:
        assert failure == ""


def test_smoke_flag_builds_then_returns_from_a_queued_quit(
    qapp, monkeypatch
) -> None:
    built: list[bool] = []

    class FakeWindow:
        def __init__(self) -> None:
            self.closing = SimpleNamespace(connect=lambda _callback: None)

        def setGeometry(self, _geometry) -> None:
            pass

        def showMaximized(self) -> None:
            pass

        def showNormal(self) -> None:
            pass

    engine = SimpleNamespace(rootObjects=lambda: [FakeWindow()])
    previous_organization = QCoreApplication.organizationName()
    previous_application = QCoreApplication.applicationName()
    previous_excepthook = sys.excepthook

    def fake_build_engine():
        built.append(True)
        return qapp, engine, object(), object(), object()

    monkeypatch.setattr(app_main, "configure_logging", lambda: None)
    monkeypatch.setattr(app_main, "build_engine", fake_build_engine)
    monkeypatch.setattr(app_main, "validate_smoke_resources", lambda: None)

    try:
        assert app_main.main(["--smoke-test"]) == 0
        assert built == [True]
    finally:
        QCoreApplication.setOrganizationName(previous_organization)
        QCoreApplication.setApplicationName(previous_application)
        sys.excepthook = previous_excepthook


def test_smoke_resource_validation_covers_help_icons_and_templates() -> None:
    resources = app_main.smoke_resource_paths()

    assert any(path.name == "harmony_beginner_guide.md" for path in resources)
    assert any(path.suffix == ".svg" for path in resources)
    assert sum(path.name == "template.pianoai.json" for path in resources) == 6
    assert all(path.is_file() for path in resources)


def test_package_root_uses_collected_package_directory_for_frozen_entry(
    tmp_path, monkeypatch
) -> None:
    internal = tmp_path / "_internal"
    collected_package = internal / "ai_piano_studio"
    collected_package.mkdir(parents=True)
    monkeypatch.setattr(app_main, "__file__", str(internal / "__main__.py"))

    assert app_main.package_root() == collected_package
