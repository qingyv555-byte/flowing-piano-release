"""Opt-in, privacy-safe startup phase profiling.

The normal application never writes a startup report.  The benchmark harness
enables this module through environment variables so production startup is not
coupled to a log file or to user data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from time import perf_counter


REQUIRED_PHASES = (
    "process_start",
    "python_entry",
    "imports_complete",
    "app_created",
    "settings_loaded",
    "resources_resolved",
    "project_library_initialized",
    "audio_initialized",
    "appearance_initialized",
    "qml_engine_created",
    "main_qml_loaded",
    "main_window_created",
    "main_window_visible",
    "editor_ready",
    "startup_complete",
)


def _environment_enabled() -> bool:
    return os.environ.get("AI_PIANO_STARTUP_PROFILE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _default_output_path() -> Path:
    return Path(tempfile.gettempdir()) / f"ai-piano-studio-startup-{os.getpid()}.json"


def _rss_mb() -> float:
    """Return the current process working set without adding a dependency."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCountersEx(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            get_process_memory_info = ctypes.WinDLL("Psapi.dll").GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCountersEx),
                wintypes.DWORD,
            ]
            get_process_memory_info.restype = wintypes.BOOL
            if get_process_memory_info(process, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / (1024 * 1024)
        else:
            import resource

            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return value / (1024 if os.name == "posix" else 1)
    except (ImportError, OSError, AttributeError):
        pass
    return 0.0


class StartupProfiler:
    """Record named startup markers without exposing user or project data."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        output_path: Path | str | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.output_path = Path(output_path) if output_path is not None else (
            _default_output_path() if self.enabled else None
        )
        self._started = perf_counter()
        self._last = self._started
        self._markers: list[dict[str, object]] = []

    @classmethod
    def from_environment(cls) -> "StartupProfiler":
        raw_path = os.environ.get("AI_PIANO_STARTUP_PROFILE_PATH", "").strip()
        return cls(
            enabled=_environment_enabled(),
            output_path=raw_path or None,
        )

    @property
    def markers(self) -> tuple[dict[str, object], ...]:
        return tuple(self._markers)

    def mark(self, phase: str) -> None:
        if phase not in REQUIRED_PHASES:
            raise ValueError(f"Unknown startup phase: {phase}")
        if not self.enabled:
            return
        now = perf_counter()
        marker = {
            "phase": phase,
            "elapsed_ms": round((now - self._started) * 1_000, 3),
            "delta_ms": round((now - self._last) * 1_000, 3),
            "rss_mb": round(_rss_mb(), 3),
        }
        self._last = now
        self._markers.append(marker)
        self.write_report()

    def write_report(self) -> None:
        if not self.enabled or self.output_path is None:
            return
        report = {
            "schema_version": 1,
            "pid": os.getpid(),
            "markers": self._markers,
            "required_markers": list(REQUIRED_PHASES),
        }
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.output_path.with_name(
                f".{self.output_path.name}.{os.getpid()}.tmp"
            )
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.output_path)
        except OSError:
            # Profiling must never prevent the editor from starting.
            try:
                if "temporary" in locals():
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass


STARTUP_PROFILER = StartupProfiler.from_environment()
STARTUP_PROFILER.mark("process_start")
STARTUP_PROFILER.mark("python_entry")


def startup_profiler() -> StartupProfiler:
    """Return the process-wide profiler used by the application entry point."""

    return STARTUP_PROFILER
