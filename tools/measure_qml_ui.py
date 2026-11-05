"""Measure native Qt Quick shell CPU, memory, and frame-pacing metrics.

The command is deliberately standalone so it can run against a detached
baseline worktree without modifying the user's project files or settings.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import inspect
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


def compare_metrics(baseline: dict, current: dict, dynamic: bool) -> list[str]:
    """Return each budget breach, leaving callers to choose their exit policy."""
    failures: list[str] = []
    cpu_budget = 5.0 if dynamic else 2.0
    if current["cpu_percent"] - baseline["cpu_percent"] > cpu_budget:
        failures.append(f"CPU delta exceeds {cpu_budget:.1f}%")
    if current["rss_delta_mb"] - baseline["rss_delta_mb"] > 25.0:
        failures.append("RSS delta exceeds 25MB")
    if current["max_consecutive_over_50ms"] > 1:
        failures.append("consecutive frame stalls exceed 50ms")
    if current["p95_frame_ms"] > 16.7:
        failures.append("p95 frame time exceeds 16.7ms")
    return failures


def _rss_bytes() -> int:
    """Read private RSS on Windows, with a zero-impact portable fallback."""
    if sys.platform == "win32":
        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
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

        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        )
        if ok:
            return int(counters.WorkingSetSize)
    try:
        import resource  # type: ignore[import-not-found]

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1024 if sys.platform != "darwin" else 1))
    except ImportError:
        return 0


def _percentile_95(values: list[float]) -> float:
    if len(values) < 10:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95)))
    return ordered[index]


def _tool_controller(project_root: Path, settings_dir: Path):
    """Construct a populated yet fully temporary controller for UI probing."""
    from PySide6.QtCore import QSettings

    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.editor.controller import EditorController
    from ai_piano_studio.persistence.autosave import NullRecoveryStore
    from ai_piano_studio.persistence.project_library import ProjectLibrary
    from ai_piano_studio.persistence.recent_files import RecentFiles

    note = Note.create(id="ui-metric-demo", pitch=60, start_time=0.0, duration=8.0)
    state = ProjectState((note,), frozenset({note.id}))
    recent = RecentFiles(QSettings(str(settings_dir / "recent.ini"), QSettings.IniFormat))
    return EditorController(
        state,
        recent_files=recent,
        project_library=ProjectLibrary(settings_dir / "projects"),
        recovery_store=NullRecoveryStore(),
    )


def _build_shell(project_root: Path, settings_dir: Path, mode: str):
    source = str((project_root / "src").resolve())
    if source in sys.path:
        sys.path.remove(source)
    sys.path.insert(0, source)

    from PySide6.QtCore import QSettings

    from ai_piano_studio.__main__ import build_engine
    from ai_piano_studio.experience.settings import ExperienceSettings

    experience_settings = QSettings(
        str(settings_dir / "experience.ini"), QSettings.IniFormat
    )
    appearance_settings = QSettings(
        str(settings_dir / "appearance.ini"), QSettings.IniFormat
    )
    controller = _tool_controller(project_root, settings_dir)
    # a0cbcae predates appearance_settings injection.  Keep the baseline
    # isolated too by routing default QSettings into this process-local temp
    # directory before using the compatible build_engine call.
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
    build_kwargs = {
        "controller": controller,
        "experience_settings": ExperienceSettings(settings=experience_settings),
    }
    if "appearance_settings" in inspect.signature(build_engine).parameters:
        build_kwargs["appearance_settings"] = appearance_settings
    app, engine, controller, playback, keyboard = build_engine(**build_kwargs)
    scene = engine.rootContext().contextProperty("scene")
    scene.setMotionLevel("medium")
    scene.setDynamicEnabled(mode == "medium")
    root = engine.rootObjects()[0]
    root.setProperty("width", 1280)
    root.setProperty("height", 720)
    root.setProperty("visible", True)
    return app, engine, root, controller, playback, keyboard


def measure_worker(project_root: Path, mode: str, seconds: float) -> dict[str, float | int]:
    """Warm a real Main.qml instance, then collect a bounded observation."""
    with tempfile.TemporaryDirectory(prefix="ai-piano-ui-metrics-") as temporary:
        app, engine, root, controller, playback, keyboard = _build_shell(
            project_root, Path(temporary), mode
        )
        window = root
        timestamps: list[float] = []
        window.frameSwapped.connect(lambda: timestamps.append(time.perf_counter()))

        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtTest import QTest

        QTest.qWaitForWindowExposed(window, 1000)
        warmup = QEventLoop()
        QTimer.singleShot(1000, warmup.quit)
        warmup.exec()

        timestamps.clear()
        cpu_start = time.process_time()
        wall_start = time.perf_counter()
        rss_start = _rss_bytes()
        observation = QEventLoop()
        QTimer.singleShot(max(1, round(seconds * 1000)), observation.quit)
        observation.exec()
        wall_elapsed = max(time.perf_counter() - wall_start, 1e-9)
        cpu_percent = (time.process_time() - cpu_start) / wall_elapsed * 100.0
        rss_delta_mb = (_rss_bytes() - rss_start) / (1024 * 1024)
        intervals = [
            (right - left) * 1000.0
            for left, right in zip(timestamps, timestamps[1:])
        ]
        longest_streak = 0
        streak = 0
        for interval in intervals:
            streak = streak + 1 if interval > 50.0 else 0
            longest_streak = max(longest_streak, streak)
        metrics: dict[str, float | int] = {
            "cpu_percent": round(cpu_percent, 3),
            "rss_delta_mb": round(rss_delta_mb, 3),
            "frame_count": len(timestamps),
            "max_frame_ms": round(max(intervals, default=0.0), 3),
            "p95_frame_ms": round(_percentile_95(intervals), 3),
            "max_consecutive_over_50ms": longest_streak,
        }
        window.close()
        engine.deleteLater()
        return metrics


def _read_metrics(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read metrics from {path}: {error}") from error
    required = {
        "cpu_percent", "rss_delta_mb", "frame_count", "max_frame_ms",
        "p95_frame_ms", "max_consecutive_over_50ms",
    }
    if not isinstance(loaded, dict) or not required <= loaded.keys():
        raise ValueError(f"metrics file is missing required fields: {path}")
    return loaded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker", help="measure one source tree")
    worker.add_argument("--project-root", type=Path, required=True)
    worker.add_argument("--mode", choices=("medium", "disabled"), required=True)
    worker.add_argument("--seconds", type=float, default=10.0)
    worker.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare", help="compare two metrics JSON files")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--current", type=Path, required=True)
    compare.add_argument("--mode", choices=("medium", "disabled"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "worker":
            if args.seconds <= 0:
                raise ValueError("--seconds must be greater than zero")
            if not (args.project_root / "src").is_dir():
                raise ValueError("--project-root must contain src")
            metrics = measure_worker(args.project_root.resolve(), args.mode, args.seconds)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(metrics, ensure_ascii=False))
            return 0
        failures = compare_metrics(
            _read_metrics(args.baseline), _read_metrics(args.current), args.mode == "medium"
        )
        if failures:
            print("UI metric comparison failed: " + "; ".join(failures), file=sys.stderr)
            return 1
        print("UI metric comparison passed")
        return 0
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
