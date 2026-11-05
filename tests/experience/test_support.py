from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib
from zipfile import ZipFile

from PySide6.QtCore import QSettings

from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.experience.support import ApplicationSupportController


class _Recovery:
    available = True

    def info(self):
        return {
            "name": "未命名工程",
            "savedAt": "2026-08-20T01:02:03+00:00",
            "noteCount": 4,
            "durationSeconds": 2.5,
            "originalPath": r"C:\Users\Admin\Documents\secret.pianoai.json",
        }


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def test_about_uses_pyproject_version(tmp_path: Path) -> None:
    with Path("pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    support = ApplicationSupportController(settings=_settings(tmp_path), log_dir=tmp_path / "logs")

    assert support.version == project_version


def test_first_run_state_persists_and_does_not_repeat(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = ExperienceSettings(settings=settings)

    assert first.firstRunCompleted is False
    first.completeFirstRun()

    second = ExperienceSettings(settings=settings)
    assert second.firstRunCompleted is True


def test_reset_ui_does_not_delete_projects_or_credentials(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.setValue("experience/firstRunCompleted", True)
    settings.setValue("appearance/themeId", "cosmic-pulse")
    settings.setValue("assistantWindow/normal/Width", 900)
    settings.setValue("musicAssistant/providers/deepseek/modelId", "deepseek-chat")
    settings.setValue("musicAssistant/providers/deepseek/credentialEntryId", "opaque-ref")
    project_path = tmp_path / "project.pianoai.json"
    project_path.write_text('{"notes": []}\n', encoding="utf-8")

    support = ApplicationSupportController(settings=settings, log_dir=tmp_path / "logs")
    support.resetUiSettings()

    assert not settings.contains("experience/firstRunCompleted")
    assert not settings.contains("appearance/themeId")
    assert not settings.contains("assistantWindow/normal/Width")
    assert settings.value("musicAssistant/providers/deepseek/modelId") == "deepseek-chat"
    assert settings.value("musicAssistant/providers/deepseek/credentialEntryId") == "opaque-ref"
    assert project_path.exists()


def test_restore_defaults_deletes_credentials_only_when_explicitly_requested(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.setValue("appearance/themeId", "cosmic-pulse")
    reset_calls: list[bool] = []
    support = ApplicationSupportController(
        settings=settings,
        log_dir=tmp_path / "logs",
        credential_reset=lambda: reset_calls.append(True),
    )

    support.restoreDefaults(False)
    assert reset_calls == []
    support.restoreDefaults(True)
    assert reset_calls == [True]


def test_diagnostics_redacts_api_keys_and_does_not_include_projects(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "api_key=super-secret-key Authorization: Bearer bearer-secret "
        r"path=C:\Users\Admin\Documents\song.pianoai.json\n",
        encoding="utf-8",
    )
    project_path = tmp_path / "song.pianoai.json"
    project_path.write_text('{"notes": [{"pitch": 60}]}\n', encoding="utf-8")
    settings = _settings(tmp_path)
    settings.setValue("musicAssistant/providers/deepseek/modelId", "deepseek-chat")
    settings.setValue("musicAssistant/providers/deepseek/credentialEntryId", "opaque-ref")

    support = ApplicationSupportController(
        settings=settings,
        log_dir=log_dir,
        recovery_store=_Recovery(),
        output_dir=tmp_path / "diagnostics",
    )
    archive = Path(support.exportDiagnostics())

    assert archive.name == "AI-Piano-Studio-Diagnostics.zip"
    with ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert names == {"app-info.json", "config-summary.json", "logs/app.log", "crash-recovery.json"}
        combined = "\n".join(bundle.read(name).decode("utf-8") for name in names)

    assert "super-secret-key" not in combined
    assert "bearer-secret" not in combined
    assert "song.pianoai.json" not in combined
    assert "C:\\Users\\Admin" not in combined
    assert "opaque-ref" not in combined
    info = json.loads(ZipFile(archive).read("app-info.json"))
    assert info["beta"] is True
    assert re.fullmatch(r"\d+\.\d+\.\d+", info["version"])


def test_diagnostics_redacts_paths_with_spaces_without_leaking_project_names(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "path=C:\\Users\\Admin\\My Documents\\private song.pianoai.json\n"
        "path=/Users/Admin/My Documents/private song.pianoai.json\n",
        encoding="utf-8",
    )
    support = ApplicationSupportController(
        settings=_settings(tmp_path),
        log_dir=log_dir,
        output_dir=tmp_path / "diagnostics",
    )

    archive = Path(support.exportDiagnostics())
    with ZipFile(archive) as bundle:
        log_text = bundle.read("logs/app.log").decode("utf-8")

    assert "private song.pianoai.json" not in log_text
    assert "My Documents" not in log_text
    assert log_text.count("<path>") == 2


def test_diagnostics_preview_is_safe_and_copyable_without_exporting_project_data(
    tmp_path: Path, qapp
) -> None:
    settings = _settings(tmp_path)
    settings.setValue("musicAssistant/providers/deepseek/modelId", "deepseek-v4-pro")
    settings.setValue("musicAssistant/providers/deepseek/credentialEntryId", "secret-ref")
    support = ApplicationSupportController(
        settings=settings,
        log_dir=tmp_path / "logs",
        recovery_store=_Recovery(),
    )
    support.recordErrorCategory("provider_unavailable")

    preview = support.diagnosticsPreview
    assert "AI Piano Studio" in preview
    assert "deepseek-v4-pro" in preview
    assert "provider_unavailable" in preview
    assert "secret-ref" not in preview
    assert "C:\\Users\\Admin" not in preview
    assert "project" not in preview.lower()

    assert support.copyDiagnosticsPreview() is True
    assert qapp.clipboard().text() == preview
