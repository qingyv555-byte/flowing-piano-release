from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QObject, Property, QSettings, QStandardPaths, Slot, QUrl, qVersion, Signal
from PySide6.QtGui import QGuiApplication

from ai_piano_studio.logging_config import default_log_directory
from ai_piano_studio.music_agent.security import SecretScrubber
from ai_piano_studio.version import APP_VERSION, BUILD_COMMIT, IS_BETA


# A log line may contain a Windows path with spaces. Once a path marker is
# found, redact through the end of the line rather than leaking the tail of a
# user directory or project name. This intentionally favors privacy over
# preserving the remainder of that single diagnostic line.
_DRIVE_PATH = re.compile(r"(?i)(?<![\w])[A-Z]:[\\/][^\r\n\"']*")
_POSIX_PATH = re.compile(r"(?<![\w])/(?:Users|home|var|tmp)/[^\r\n\"']*")


class ApplicationSupportController(QObject):
    """Safe, user-facing Beta support actions.

    This bridge deliberately receives QSettings and the log/recovery stores as
    dependencies. It never reads credential values or project contents.
    """

    changed = Signal()
    diagnosticsExported = Signal(str)
    diagnosticsFailed = Signal(str)
    resetCompleted = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        settings: QSettings | None = None,
        log_dir: str | Path | None = None,
        recovery_store: object | None = None,
        output_dir: str | Path | None = None,
        credential_reset: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("AIPianoStudio", "AI Piano Studio")
        self._log_dir = Path(log_dir) if log_dir is not None else default_log_directory()
        self._recovery_store = recovery_store
        self._output_dir = Path(output_dir) if output_dir is not None else None
        self._credential_reset = credential_reset
        self._recent_error_category = ""

    @Property(str, constant=True)
    def version(self) -> str:
        return APP_VERSION

    @Property(bool, constant=True)
    def beta(self) -> bool:
        return IS_BETA

    @Property(str, constant=True)
    def buildCommit(self) -> str:
        return BUILD_COMMIT

    @Property(str, constant=True)
    def qtVersion(self) -> str:
        return qVersion()

    @Property(str, constant=True)
    def logDirectory(self) -> str:
        return str(self._log_dir)

    @Property(str, constant=True)
    def logDirectoryUrl(self) -> str:
        return QUrl.fromLocalFile(str(self._log_dir)).toString()

    @Property(str, notify=changed)
    def diagnosticsPreview(self) -> str:
        """Safe, human-reviewable metadata shown before any share/export."""
        payload = {
            "app": self._app_info(),
            "config": self._safe_config_summary(),
            "recovery": self._safe_recovery_summary(),
        }
        return self._json_text(payload)

    @Slot(str)
    def recordErrorCategory(self, category: str) -> None:
        value = str(category or "").strip().lower()
        self._recent_error_category = (
            value if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value) else "unknown"
        )
        self.changed.emit()

    @Slot(result=bool)
    def copyDiagnosticsPreview(self) -> bool:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(self.diagnosticsPreview)
        return True

    @Slot(result=str)
    def exportDiagnostics(self) -> str:
        try:
            output_dir = self._output_dir or self._default_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            destination = output_dir / "AI-Piano-Studio-Diagnostics.zip"
            temporary = destination.with_suffix(".tmp.zip")
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "app-info.json",
                    self._json_text(self._app_info()),
                )
                archive.writestr(
                    "config-summary.json",
                    self._json_text(self._safe_config_summary()),
                )
                archive.writestr("logs/app.log", self._safe_log_text())
                archive.writestr(
                    "crash-recovery.json",
                    self._json_text(self._safe_recovery_summary()),
                )
            os.replace(temporary, destination)
            self.diagnosticsExported.emit(str(destination))
            return str(destination)
        except Exception:
            temporary = locals().get("temporary")
            if isinstance(temporary, Path):
                temporary.unlink(missing_ok=True)
            self.diagnosticsFailed.emit("无法导出诊断信息")
            return ""

    @Slot(result=str)
    def openLogDirectory(self) -> str:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        return self.logDirectoryUrl

    @Slot()
    def resetUiSettings(self) -> None:
        """Reset UI-only groups; projects, recent files and credentials survive."""
        for prefix in ("experience", "appearance", "assistantWindow/normal"):
            self._settings.remove(prefix)
        self._settings.sync()
        self.resetCompleted.emit()
        self.changed.emit()

    @Slot(bool)
    def restoreDefaults(self, removeCredentials: bool = False) -> None:
        self.resetUiSettings()
        if removeCredentials and self._credential_reset is not None:
            self._credential_reset()

    def _default_output_dir(self) -> Path:
        downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        return Path(downloads) if downloads else self._log_dir.parent / "Diagnostics"

    def _app_info(self) -> dict[str, object]:
        return {
            "product": "AI Piano Studio",
            "version": self.version,
            "beta": self.beta,
            "buildCommit": self.buildCommit,
            "os": platform.platform(aliased=True),
            "qt": self.qtVersion,
            "pyside": pyside_version,
            "python": platform.python_version(),
            "recentErrorCategory": self._recent_error_category,
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _safe_config_summary(self) -> dict[str, object]:
        providers: list[dict[str, object]] = []
        self._settings.beginGroup("musicAssistant/providers")
        try:
            for provider_id in self._settings.childGroups():
                self._settings.beginGroup(provider_id)
                try:
                    model_id = str(self._settings.value("modelId", ""))
                    configured = self._settings.contains("providerId") or self._settings.contains("baseUrl")
                    has_credential_reference = bool(
                        self._settings.value("credentialEntryId", "")
                        or self._settings.value("credentialRef", "")
                    )
                    providers.append(
                        {
                            "id": provider_id,
                            "configured": configured,
                            "model": model_id[:120],
                            "hasCredentialReference": has_credential_reference,
                        }
                    )
                finally:
                    self._settings.endGroup()
        finally:
            self._settings.endGroup()
        return {"providers": providers}

    def _safe_recovery_summary(self) -> dict[str, object]:
        info = self._recovery_store.info() if self._recovery_store is not None else None
        if not isinstance(info, dict):
            return {"available": False}
        return {
            "available": bool(getattr(self._recovery_store, "available", True)),
            "noteCount": info.get("noteCount"),
            "durationSeconds": info.get("durationSeconds"),
            "savedAt": info.get("savedAt", ""),
        }

    def _safe_log_text(self) -> str:
        log_path = self._log_dir / "app.log"
        if not log_path.is_file():
            return "No application log is available.\n"
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()[-250:]
        scrubber = SecretScrubber()
        safe_lines = [self._scrub_paths(scrubber.scrub(line)) for line in lines]
        return "\n".join(safe_lines) + ("\n" if safe_lines else "")

    @staticmethod
    def _scrub_paths(value: str) -> str:
        return _POSIX_PATH.sub("<path>", _DRIVE_PATH.sub("<path>", value))

    @staticmethod
    def _json_text(value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
