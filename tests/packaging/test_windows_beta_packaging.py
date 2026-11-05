from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).parents[2]
SPEC = ROOT / "ai_piano_studio.spec"
PORTABLE_SCRIPT = ROOT / "tools" / "build-portable.ps1"
INSTALLER_SCRIPT = ROOT / "tools" / "build-installer.ps1"
RELEASE_SCRIPT = ROOT / "tools" / "build-release.ps1"
INSTALLER = ROOT / "installer" / "AI-Piano-Studio.iss"


def test_windows_beta_packaging_files_and_release_names_exist() -> None:
    assert SPEC.is_file()
    assert PORTABLE_SCRIPT.is_file()
    assert INSTALLER_SCRIPT.is_file()
    assert RELEASE_SCRIPT.is_file()
    assert INSTALLER.is_file()

    release_script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert "AI-Piano-Studio-Portable.zip" in release_script
    assert "AI-Piano-Studio-Setup.exe" in release_script
    assert "SHA256SUMS.txt" in release_script
    assert "BUILD-MANIFEST.json" in release_script
    assert "AI_PIANO_BUILD_COMMIT" in PORTABLE_SCRIPT.read_text(encoding="utf-8")
    assert '"AudioImportDialog.qml"' in release_script
    assert '"TechnicalDetailsDialog.qml"' in release_script
    assert '"ffmpegmediaplugin.dll"' in release_script


def test_version_has_one_source_and_is_forwarded_to_installer() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = config["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)

    release_script = RELEASE_SCRIPT.read_text(encoding="utf-8")
    installer_script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert "pyproject.toml" in release_script
    assert "Version" in installer_script
    assert "DVersion" in installer_script
    assert "0.1.0" not in installer_script
    assert "0.1.0" not in RELEASE_SCRIPT.read_text(encoding="utf-8")


def test_inno_uses_fixed_appid_and_validated_portable_tree() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    app_ids = re.findall(r"^AppId=\{\{([^}]+)\}", source, flags=re.MULTILINE)
    assert len(app_ids) == 1
    assert re.fullmatch(
        r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
        app_ids[0],
    )
    assert "PrivilegesRequired=lowest" in source
    assert "DefaultDirName={localappdata}\\Programs\\AI Piano Studio" in source
    assert 'Source: "{#PortableDir}\\*"' in source
    assert "recursesubdirs" in source
    assert "AI-Piano-Studio-Setup" in source

    installer_script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert "AI-Piano-Studio.exe" in installer_script
    assert "PortableDir" in installer_script
    assert "ISCC.exe" in installer_script


def test_release_script_generates_privacy_safe_metadata() -> None:
    source = RELEASE_SCRIPT.read_text(encoding="utf-8")
    for required in (
        "product",
        "version",
        "git_branch",
        "git_commit",
        "build_timestamp",
        "python_version",
        "pyside_version",
        "pyinstaller_version",
        "installer_tool",
        "portable_sha256",
        "setup_sha256",
    ):
        assert required in source
    for forbidden_pattern in ("api[_-]?key", "credential", ".env"):
        assert forbidden_pattern in source
    assert "Get-FileHash" in source
    assert "ConvertTo-Json" in source


def test_release_scripts_never_use_development_directories_as_installer_sources() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert "{#PortableDir}" in source
    for forbidden in ("tests\\", ".venv", ".git", "screenshots", "logs", "project.json"):
        assert forbidden not in source


def test_spec_keeps_runtime_resource_contract() -> None:
    source = SPEC.read_text(encoding="utf-8")
    for required in (
        '"qml"',
        '"resources"',
        '"banks.json"',
        '"banks"',
        '"LICENSE-SALAMANDER.txt"',
        '"PROVENANCE-SALAMANDER.json"',
        '"PROVENANCE-SALAMANDER.md"',
        '"THIRD_PARTY_NOTICES.md"',
    ):
        assert required in source


def test_formal_windows_launch_path_has_no_console_intermediate() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    runtime_hook = (ROOT / "installer" / "windows_runtime_hook.py").read_text(encoding="utf-8")
    recycle = (ROOT / "src" / "ai_piano_studio" / "persistence" / "project_recycle.py").read_text(encoding="utf-8")
    importer = (ROOT / "src" / "ai_piano_studio" / "audio" / "importer.py").read_text(encoding="utf-8")

    assert re.search(r"\bconsole\s*=\s*False\b", spec)
    assert not re.search(r"\bconsole\s*=\s*True\b", spec)
    assert 'Filename: "{app}\\{#MyAppExeName}"' in installer
    assert "cmd.exe" not in installer
    assert "powershell.exe" not in installer
    assert "CREATE_NO_WINDOW" in recycle
    assert "subprocess" not in importer
    assert "windows_runtime_hook.py" in spec
    assert "PySide6/{Path(_name).name}" in spec
    assert "System32" in spec
    assert "icuuc.dll" in spec
    assert "icudt78.dll" in spec
    assert "not in {\"icuuc.dll\", \"icudt78.dll\"}" in spec
    assert "_system_icu" not in spec  # Windows supplies ICU; do not copy system binaries.
    assert "add_dll_directory" in runtime_hook
