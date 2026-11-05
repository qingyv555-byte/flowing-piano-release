"""Real editor inputs in an isolated, muted, offscreen production shell.

Run with the worktree's Python. --baseline-ref launches this same harness with
that Git snapshot on PYTHONPATH; it does not use the preserved working tree.
Offscreen grab completion is not native presentation latency or FPS.
"""
from __future__ import annotations

import argparse
import cProfile
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import pstats
import shutil
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def statistics_ms(samples):
    values = sorted(samples)
    return {"count": len(values), "p50_ms": statistics.median(values),
            "p95_ms": values[max(0, math.ceil(len(values) * .95) - 1)],
            "min_ms": values[0], "max_ms": values[-1]}


def visual_item(parent, name):
    if parent.objectName() == name:
        return parent
    for child in parent.childItems():
        found = visual_item(child, name)
        if found is not None:
            return found
    return None


def child(args):
    import PySide6
    from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, qVersion
    from PySide6.QtGui import QMouseEvent
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.nocturne import build_nocturne_engine
    from ai_piano_studio.persistence.project_document import load_project_document

    with tempfile.TemporaryDirectory(prefix="studio-perf-profile-") as directory:
        if args.variant == "studio":
            from ai_piano_studio.studio import build_studio_engine
            session = build_studio_engine(Path(directory))
        else:
            session = build_nocturne_engine(Path(directory))
        app, engine, editor, playback, keyboard = session
        playback.setVolume(0)
        root = engine.rootObjects()[0]
        root.setWidth(1440)
        root.setHeight(900)
        root.setProperty("pageState", "editor")
        scene = engine.rootContext().contextProperty("scene")
        scene.setBackgroundEnabled(args.decorations == "on")
        scene.setVisualizerEnabled(args.decorations == "on")
        if args.variant == "studio":
            appearance = engine.rootContext().contextProperty("studioAppearance")
            appearance.setValue("editorSource", "builtin" if args.decorations == "on" else "none")
            appearance.setValue("feedbackLevel", "standard" if args.decorations == "on" else "off")
        if args.scenario == "template":
            templates = Path(__import__("ai_piano_studio").__file__).parent / "resources" / "templates"
            candidates = [(p, load_project_document(p).state) for p in templates.glob("*/template.pianoai.json")]
            source_path, initial = max(candidates, key=lambda item: len(item[1].notes))
            fixture = {"template": source_path.parent.name, "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()}
        else:
            initial = ProjectState(tuple(Note.create(id=f"perf-{i}", pitch=36 + i % 24,
                start_time=i * .04, duration=.12, status="saved") for i in range(int(args.scenario))))
            fixture = {"generator": "36+i%24, start=i*.04, duration=.12, saved"}

        def reset():
            editor._replace_project(initial, saved_content=initial.content, path=None)
            root.setProperty("pageState", "editor")
            app.processEvents()
            root.grabWindow()

        reset()
        pointer = visual_item(root.contentItem(), "drawNoteArea")
        assert pointer is not None and pointer.isVisible() and pointer.isEnabled()
        # Top note row, outside fixture pitches, and inside the real canvas.
        point = pointer.mapToScene(QPointF(260, 9))
        assert 0 < point.x() < root.width() and 0 < point.y() < root.height()
        model_times = []
        original_set_state = editor.noteModel.set_state

        def timed_model(state):
            started = perf_counter()
            original_set_state(state)
            model_times.append((perf_counter() - started) * 1000)

        editor.noteModel.set_state = timed_model

        def pointer_action():
            for kind, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
                event = QMouseEvent(kind, point, point, Qt.LeftButton, buttons, Qt.NoModifier)
                QCoreApplication.sendEvent(root, event)

        def controller_action():
            if args.variant == "studio":
                editor.createNoteAt(188, 9, .5, 75)
            else:
                assert editor.beginDrawNoteAt(188, 9, .5, 75)
                editor.commitGesture()

        action = pointer_action if args.input == "pointer" else controller_action
        rows, warmups = [], []
        frame_submissions = []
        root.afterRendering.connect(lambda: frame_submissions.append(perf_counter()), Qt.DirectConnection)
        for index in range(args.warmup + args.samples):
            reset()
            if args.operation == "move100":
                editor.selectNotes([n.id for n in initial.notes[:100]])
                assert editor.selectedCount == 100
                app.processEvents()
            before_image = root.grabWindow()
            before = editor.state
            history = editor._history.undo_depth
            model_times.clear()
            frame_submissions.clear()
            start = perf_counter()
            if args.operation == "move100":
                editor.beginGesture()
                editor.previewMovePixels(20, 0)
                editor.commitGesture()
            else:
                action()
            dispatched = perf_counter()
            app.processEvents()
            drained = perf_counter()
            rendered = root.grabWindow()
            end = perf_counter()
            assert not rendered.isNull()
            after = editor.state
            assert editor._history.undo_depth == history + 1
            if args.operation == "move100":
                assert len(after.notes) == len(before.notes)
                changed = [n for a, n in zip(before.notes, after.notes) if a != n]
                assert len(changed) == 100
                assert all(n.start_time > a.start_time for a, n in zip(before.notes[:100], after.notes[:100]))
            else:
                assert len(after.notes) == len(before.notes) + 1
                added = after.notes[-1]
                assert after.selected_ids == frozenset({added.id})
                assert any(item["noteId"] == added.id for item in editor.noteModel.visibleItems)
                # Compare the note's actual viewport rectangle, excluding its edges.
                crop = (round(point.x() + 3), round(point.y() - 4), 14, 8)
                assert before_image.copy(*crop) != rendered.copy(*crop), "new note did not reach captured pixels"
            row = {"input_dispatch_ms": (dispatched-start)*1000,
                   "note_model_publish_ms": sum(model_times),
                   "event_drain_ms": (drained-dispatched)*1000,
                   "frame_grab_ms": (end-drained)*1000,
                   "input_to_offscreen_frame_ms": (end-start)*1000,
                   "frame_submission_count": len(frame_submissions),
                   "input_to_first_render_submission_ms": ((frame_submissions[0]-start)*1000 if frame_submissions else None),
                   "visible_note_count": editor.noteModel.visibleCount,
                   "model_viewport": editor.noteModel._viewport,
                   "note_count_before": len(before.notes), "note_count_after": len(after.notes),
                   "history_delta": editor._history.undo_depth-history,
                   "selected_after": len(after.selected_ids)}
            editor.undo()
            assert editor.state.content == before.content
            (warmups if index < args.warmup else rows).append(row)
        # Separate profiled sample: profiler overhead is excluded from timings.
        reset()
        profiler = cProfile.Profile()
        profiler.enable()
        if args.operation == "move100":
            editor.selectNotes([n.id for n in initial.notes[:100]])
            editor.moveSelectedByPixels(20, 0)
        else:
            action()
        app.processEvents()
        root.grabWindow()
        profiler.disable()
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
        stats.print_stats(30)
        stats.print_stats("overviewByLayer|visibleItems")
        result = {"schema": 1, "scenario": args.scenario, "notes": len(initial.notes),
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "fixture": fixture, "operation": args.operation,
            "input": "controller_gesture" if args.operation == "move100" else args.input,
            "variant": args.variant, "decorations": args.decorations,
            "environment": {"python": sys.version, "executable": sys.executable,
                "pyside": PySide6.__version__, "qt": qVersion(), "platform": platform.platform(),
                "processor": platform.processor(), "logical_cpu_count": os.cpu_count(),
                "source": str(Path(__import__("ai_piano_studio").__file__).parent),
                "qpa": os.environ["QT_QPA_PLATFORM"], "render_loop": os.environ["QSG_RENDER_LOOP"],
                "renderer": str(root.rendererInterface().graphicsApi()),
                "logical_window": [root.width(), root.height()], "dpr": root.devicePixelRatio(),
                "volume": playback.volume},
            "scope": "Synthetic Qt events; live production model/audio bridges; offscreen frame readback, not native display latency/FPS",
            "percentile_method": "nearest rank ceil(.95*n); warmups and profiling excluded",
            "warmup": warmups, "samples": rows,
            "summary": {key: statistics_ms([row[key] for row in rows if row[key] is not None])
                        for key in rows[0] if key.endswith("_ms") and all(row[key] is not None for row in rows)},
            "profile": stream.getvalue()}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result["summary"]))
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--baseline-ref")
    parser.add_argument("--backend-overlay", action="store_true", help="copy only current editor Python modules over baseline to isolate backend gain")
    parser.add_argument("--scenario", default="5000")
    parser.add_argument("--operation", choices=("create", "move100"), default="create")
    parser.add_argument("--input", choices=("pointer", "controller"), default="pointer")
    parser.add_argument("--variant", choices=("nocturne", "studio"), default="nocturne")
    parser.add_argument("--decorations", choices=("on", "off"), default="on")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.child:
        return child(args)
    with tempfile.TemporaryDirectory(prefix="studio-perf-source-") as directory:
        source = ROOT
        if args.baseline_ref:
            archive = Path(directory) / "snapshot.zip"
            subprocess.run(["git", "archive", "--format=zip", "-o", str(archive), args.baseline_ref], cwd=ROOT, check=True)
            source = Path(directory) / "source"
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(source)
            if args.backend_overlay:
                for module in ("controller", "note_list_model", "music_layer_list_model", "composite_model", "composite"):
                    relative = Path("src/ai_piano_studio/editor") / f"{module}.py"
                    shutil.copyfile(ROOT / relative, source / relative)
        env = dict(os.environ, PYTHONPATH=str(source / "src"), QT_QPA_PLATFORM="offscreen",
                   QSG_RENDER_LOOP="basic", PYTHONIOENCODING="utf-8")
        forwarded = sys.argv[1:]
        if args.baseline_ref:
            start = forwarded.index("--baseline-ref")
            del forwarded[start:start+2]
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", *forwarded], env=env, cwd=source, check=True)
        result = json.loads(args.output.read_text(encoding="utf-8"))
        modules = ("editor/controller.py", "editor/note_list_model.py", "editor/composite.py",
                   "qml/Main.qml", "qml/Timeline.qml", "qml/MiniMap.qml", "qml/Theme.qml")
        result["provenance"] = {"baseline_ref": args.baseline_ref,
            "backend_overlay": args.backend_overlay,
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_sha256": {module: hashlib.sha256((source / "src/ai_piano_studio" / module).read_bytes()).hexdigest()
                              for module in modules}}
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
