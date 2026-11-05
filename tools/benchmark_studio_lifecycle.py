"""Bounded fresh-process startup and offscreen lifecycle observations.

No OS cache flush: these are fresh Python processes, not controlled cold boots.
Qt event loops run normally while CPU/working-set observations are sampled.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter, process_time
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage")]


class ThreadEntry(ctypes.Structure):
    _fields_ = [(name, kind) for name, kind in (
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD))]


def memory():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set_bytes": counters.WorkingSetSize,
            "peak_working_set_bytes": counters.PeakWorkingSetSize,
            "pagefile_usage_bytes": counters.PagefileUsage}


def native_thread_count():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    item = ThreadEntry()
    item.dwSize = ctypes.sizeof(item)
    count = 0
    try:
        available = kernel.Thread32First(snapshot, ctypes.byref(item))
        while available:
            if item.th32OwnerProcessID == os.getpid():
                count += 1
            available = kernel.Thread32Next(snapshot, ctypes.byref(item))
    finally:
        kernel.CloseHandle(snapshot)
    return count


def child(args):
    started = perf_counter()
    import PySide6
    from shiboken6 import getCppPointer
    from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QTimer, Qt, qVersion
    from PySide6.QtGui import QWindow
    from ai_piano_studio.__main__ import initialize_deferred_services
    if args.variant == "studio":
        from ai_piano_studio.studio import build_studio_engine as build
    else:
        from ai_piano_studio.nocturne import build_nocturne_engine as build
    imported = perf_counter()

    with tempfile.TemporaryDirectory(prefix="studio-lifecycle-profile-") as directory:
        app, engine, editor, playback, keyboard = build(Path(directory))
        built = perf_counter()
        playback.setVolume(0)
        root = engine.rootObjects()[0]
        root.resize(1440, 900)
        # Mirror the real entry point's deferred initialization before the next
        # event-loop turn. The first-frame metric includes work that runs here.
        QTimer.singleShot(0, lambda: initialize_deferred_services(engine))
        app.processEvents()
        first_frame = root.grabWindow()
        first_frame_done = perf_counter()
        assert not first_frame.isNull() and root.isVisible()
        assert root.property("pageState") == "launch"
        mixer = playback.piano_source.mixer
        assert mixer._sink is None and not playback.playing and playback.volume == 0
        visualizer = engine.rootContext().contextProperty("audioVisualizer")
        templates = engine.rootContext().contextProperty("templates")

        def event_window(milliseconds):
            loop = QEventLoop()
            QTimer.singleShot(milliseconds, loop.quit)
            loop.exec()

        def page(name):
            assert QMetaObject.invokeMethod(root, "setPage", Qt.DirectConnection,
                Q_ARG("QVariant", name), Q_ARG("QVariant", False))
            assert root.property("pageState") == name

        def objects():
            todo = [root, engine, editor, visualizer]
            seen = set()
            while todo:
                item = todo.pop()
                address = getCppPointer(item)[0]
                if address in seen:
                    continue
                seen.add(address)
                yield item
                todo.extend(item.children())
                if hasattr(item, "childItems"):
                    todo.extend(item.childItems())

        def properties(item, names):
            result = {}
            meta = item.metaObject()
            for name in names:
                if meta.indexOfProperty(name) < 0:
                    continue
                value = item.property(name)
                if hasattr(value, "toString"):
                    value = value.toString()
                elif hasattr(value, "value"):
                    value = value.value
                if value is None or isinstance(value, (str, int, float, bool)):
                    result[name] = value
            return result

        def status():
            timers, animations, surfaces, decoders = [], [], [], []
            for item in objects():
                class_name = item.metaObject().className()
                name = item.objectName()
                if "Timer" in class_name:
                    if isinstance(item, QTimer):
                        values = {"running": item.isActive(), "interval": item.interval()}
                    else:
                        values = properties(item, ("running", "interval", "repeat"))
                    timers.append({"name": name, "class": class_name, **values})
                if "Animation" in class_name:
                    values = properties(item, ("running", "paused", "loops"))
                    if values:
                        animations.append({"name": name, "class": class_name, **values})
                if name in {"studioFeedback", "reactiveRhythmLayer", "editorBackgroundLayer",
                            "studioBackgroundLoader", "studioBackgroundAnimation"}:
                    surfaces.append({"name": name, "class": class_name, **properties(item, (
                        "visible", "active", "renderActive", "windowActive", "viewActive", "ambientRunning",
                        "decorationRunning", "effectsEnabled", "eventsRequested", "analysisRequested",
                        "animationRunning", "motionActive", "source", "playing"))})
                if "AnimatedImage" in class_name:
                    decoders.append({"name": name, "class": class_name, **properties(item, (
                        "visible", "playing", "paused", "source", "currentFrame", "frameCount"))})
            worker = visualizer._worker
            worker_timer = worker._timer if worker else None
            feedback = engine.rootContext().contextProperty("studioFeedback") if args.variant == "studio" else None
            return {"page": root.property("pageState"), "window_visible": root.isVisible(),
                "window_visibility": root.visibility().name, "native_thread_count": native_thread_count(),
                "audio_sink_created": mixer._sink is not None, "playback_running": playback.playing,
                "template_preview_running": templates.previewing, "volume": playback.volume,
                "visualizer_enabled": visualizer.enabled,
                "visualizer_thread_running": visualizer._thread.isRunning() if visualizer._thread else False,
                "visualizer_timer_running": worker_timer.isActive() if worker_timer else False,
                "visualizer_timer_interval_ms": worker_timer.interval() if worker_timer else None,
                "feedback_controller_enabled": feedback.enabled if feedback is not None else None,
                "qt_timers": timers, "active_qt_timer_count": sum(bool(t.get("running")) for t in timers),
                "qml_animations": animations,
                "running_qml_animation_count": sum(bool(a.get("running")) and not a.get("paused", False) for a in animations),
                "surfaces": surfaces, "animated_image_decoders": decoders,
                "playing_decoder_count": sum(bool(d.get("playing")) for d in decoders)}

        result = {"schema": 1, "variant": args.variant, "launch_index": args.launch_index,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "startup": {"import_ms": (imported-started)*1000,
                "build_ms": (built-imported)*1000, "event_and_first_frame_ms": (first_frame_done-built)*1000,
                "import_start_to_first_frame_ms": (first_frame_done-started)*1000,
                "memory_after_first_frame": memory()},
            "environment": {"python": sys.version, "executable": sys.executable,
                "pyside": PySide6.__version__, "qt": qVersion(), "os": platform.platform(),
                "processor": platform.processor(), "logical_processors": os.cpu_count(),
                "qpa": os.environ["QT_QPA_PLATFORM"], "render_loop": os.environ["QSG_RENDER_LOOP"],
                "renderer": str(root.rendererInterface().graphicsApi()), "dpr": root.devicePixelRatio(),
                "logical_window": [root.width(), root.height()]},
            "scope": "Fresh Python process; uncontrolled OS file/cache state; offscreen software frame; no audio device",
            "resource_phases": []}

        if args.resource_run:
            def sample_phase(label):
                # Let queued page/visibility changes settle, outside the idle
                # observation. This is test harness event-loop time, not sleep
                # or a change to production scheduling.
                event_window(150)
                before_status = status()
                before_memory = memory()
                samples = []
                loop = QEventLoop()
                timer = QTimer()
                timer.setInterval(250)
                begin_cpu, begin_wall = process_time(), perf_counter()

                def collect():
                    samples.append({"elapsed_seconds": perf_counter()-begin_wall,
                                    "process_cpu_seconds": process_time()-begin_cpu, **memory()})

                timer.timeout.connect(collect)
                timer.start()
                QTimer.singleShot(round(args.seconds*1000), loop.quit)
                loop.exec()
                end_cpu, end_wall = process_time(), perf_counter()
                timer.stop()
                collect()
                elapsed, cpu = end_wall-begin_wall, end_cpu-begin_cpu
                after_status = status()
                after_memory = memory()
                assert not after_status["audio_sink_created"] and not after_status["playback_running"]
                phase = {"name": label, "requested_seconds": args.seconds, "wall_seconds": elapsed,
                    "process_cpu_seconds": cpu, "one_core_cpu_percent": cpu/elapsed*100,
                    "working_set_start_bytes": before_memory["working_set_bytes"],
                    "working_set_end_bytes": after_memory["working_set_bytes"],
                    "working_set_sample_peak_bytes": max(before_memory["working_set_bytes"],
                        after_memory["working_set_bytes"], *(s["working_set_bytes"] for s in samples)),
                    "status_start": before_status, "status_end": after_status, "samples": samples}
                result["resource_phases"].append(phase)
                print(json.dumps({"phase": label, "cpu_one_core_percent": phase["one_core_cpu_percent"],
                    "working_set_bytes": phase["working_set_end_bytes"]}), flush=True)

            sample_phase("home_idle")
            from ai_piano_studio.domain.note import Note
            from ai_piano_studio.domain.project import ProjectState
            from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document
            state = ProjectState(tuple(Note.create(id=f"life-{i}", pitch=48+i%24,
                start_time=i*.04, duration=.12) for i in range(1000)))
            fixture = Path(directory) / "lifecycle-fixture.pianoai.json"
            save_project_document(fixture, ProjectDocument(state))
            editor.openProject(str(fixture))
            assert len(editor.state.notes) == 1000 and not editor.isDirty
            page("editor")
            sample_phase("editor_idle_1000")
            root.showMinimized()
            event_window(150)
            assert root.visibility() == QWindow.Minimized
            sample_phase("editor_minimized")
            root.showNormal()
            page("launch")
            sample_phase("returned_home")
            cycle_rows = [{"cycle": 0, **memory(), "status": status()}]
            for index in range(args.cycles):
                page("editor")
                event_window(150)
                root.showMinimized()
                event_window(150)
                root.showNormal()
                page("launch")
                event_window(150)
                cycle_rows.append({"cycle": index+1, **memory(), "status": status()})
            result["bounded_switch_cycles"] = cycle_rows
            result["bounded_switch_working_set_delta_bytes"] = (
                cycle_rows[-1]["working_set_bytes"]-cycle_rows[0]["working_set_bytes"])

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"launch": args.launch_index, "variant": args.variant,
            "import_start_to_first_frame_ms": result["startup"]["import_start_to_first_frame_ms"]}), flush=True)
        root.close()
        engine.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--resource-run", action="store_true")
    parser.add_argument("--baseline-ref")
    parser.add_argument("--variant", choices=("nocturne", "studio"), default="studio")
    parser.add_argument("--launches", type=int, default=5)
    parser.add_argument("--launch-index", type=int, default=1)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.launches <= 10 or not 0 < args.seconds <= 10 or not 0 <= args.cycles <= 10:
        parser.error("bounded run requires 1..10 launches, 0<seconds<=10, 0..10 cycles")
    if args.child:
        return child(args)
    with tempfile.TemporaryDirectory(prefix="studio-lifecycle-source-") as directory:
        source = ROOT
        if args.baseline_ref:
            source = Path(directory) / "source"
            archive = Path(directory) / "baseline.zip"
            subprocess.run(["git", "archive", "--format=zip", "-o", str(archive), args.baseline_ref], cwd=ROOT, check=True)
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(source)
        env = dict(os.environ, PYTHONPATH=str(source/"src"), QT_QPA_PLATFORM="offscreen",
                   QSG_RENDER_LOOP="basic", PYTHONIOENCODING="utf-8")
        launches = []
        for index in range(1, args.launches+1):
            output = Path(directory) / f"launch-{index}.json"
            command = [sys.executable, str(Path(__file__).resolve()), "--child", "--variant", args.variant,
                       "--launch-index", str(index), "--seconds", str(args.seconds), "--cycles", str(args.cycles),
                       "--output", str(output)]
            if index == args.launches:
                command.append("--resource-run")
            subprocess.run(command, env=env, cwd=source, check=True)
            launches.append(json.loads(output.read_text(encoding="utf-8")))
        values = [item["startup"]["import_start_to_first_frame_ms"] for item in launches]
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
        result = {"schema": 1, "variant": args.variant,
            "provenance": {"baseline_ref": args.baseline_ref, "worktree_head": revision,
                "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "startup_summary": {"fresh_process_count": len(values), "p50_ms": statistics.median(values),
                "min_ms": min(values), "max_ms": max(values), "first_process_ms": values[0],
                "later_process_p50_ms": statistics.median(values[1:]) if len(values)>1 else None},
            "method": {"cache_control": "No OS cache flush. First launch is not claimed to be cold.",
                "startup_boundary": "Before Qt/app imports to first non-null QQuickWindow.grabWindow completion; excludes Python process creation and harness stdlib imports; includes deferred callbacks dispatched before frame.",
                "resources": "Last fresh process only; 150ms page settle, then 5s default Qt event loop with 250ms PSAPI samples. CPU includes harness observation overhead.",
                "cpu": "Windows process user+kernel CPU seconds / measured wall seconds *100; one core basis, not normalized across20 processors.",
                "memory": "PSAPI current working set and process lifetime high water. Bounded sampled observations, no forced GC and no leak-proof claim.",
                "decoders": "Default builtin background only; no custom GIF decoder workload configured."},
            "launches": launches}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result["startup_summary"]))


if __name__ == "__main__":
    main()
