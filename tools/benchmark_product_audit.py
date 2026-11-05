"""Measure bounded editor operations in an isolated offscreen Qt shell."""
from __future__ import annotations

import json
import argparse
import hashlib
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PySide6.QtCore import QObject, Q_ARG, QMetaObject, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from ai_piano_studio.domain.note import Note
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.project_json import load_project, save_project
from tests.qt.test_qml_smoke import load_shell


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "product-audit-2026-09-10" / "performance.json")
    args = parser.parse_args()
    source_digest = hashlib.sha256(args.project.read_bytes()).hexdigest() if args.project else None
    project = load_project(args.project) if args.project else None
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    results = []
    with tempfile.TemporaryDirectory(prefix="piano-audit-perf-") as directory:
        with patch("ai_piano_studio.editor.controller.ProjectLibrary",
                   lambda: ProjectLibrary(Path(directory) / "projects")):
            for count in ((len(project.notes),) if project else (1000, 5000)):
                print(f"Loading {count} notes", flush=True)
                notes = project.notes if project else tuple(Note.create(id=f"perf-{i}", pitch=48 + i % 36,
                                          start_time=i * .04, duration=.12)
                              for i in range(count))
                start = perf_counter()
                engine, root, editor = load_shell(app, notes=notes, selected_ids=frozenset())
                if args.project:
                    copy_path = Path(directory) / "input-copy.pianoai.json"
                    save_project(copy_path, project)
                    editor._load_project(str(copy_path))
                    # Only the in-memory benchmark copy is made editable.
                    for layer in editor.state.layers:
                        if layer.locked:
                            editor.setMusicLayerProperty(layer.id, "locked", False)
                root.setWidth(1920)
                root.setHeight(1080)
                root.findChild(QObject, "arrangementToggle").clicked.emit()
                app.processEvents()
                result = {"notes": count, "shell_load_ms": (perf_counter() - start) * 1000}
                other = editor.addMusicLayer("Benchmark", "melody")
                experience = engine.rootContext().contextProperty("experience")
                panel = root.findChild(QObject, "arrangementPanel")

                def measure(name, action):
                    print(f"Measuring {count}: {name}", flush=True)
                    times = []
                    for iteration in range(5):
                        started = perf_counter()
                        action(iteration)
                        app.processEvents()
                        times.append((perf_counter() - started) * 1000)
                    result[name] = {"median_ms": round(statistics.median(times), 2),
                                    "max_ms": round(max(times), 2)}
                    print(result[name], flush=True)

                measure("layer_switch", lambda i: editor.setActiveLayer(
                    "layer-default" if i % 2 else other))
                measure("layer_recolor", lambda i: editor.setMusicLayerProperty(
                    "layer-default", "customColor", "#39C8FF" if i % 2 else "#45D6A8"))
                measure("selection_100", lambda i: editor.selectNotes(
                    [note.id for note in notes[i:i + 100]]))
                assert len(editor.state.selected_notes) == 100
                measure("atomic_drag_100", lambda i: editor.moveSelectedByPixels(20, 0))
                print(f"Groups before Smart View: {editor.compositeModel.totalCount}", flush=True)
                measure("smart_toggle", lambda i: experience.setCompositeDisplayMode(
                    "original" if i % 2 else "smart"))
                measure("arrangement_resize", lambda i: QMetaObject.invokeMethod(
                    panel, "previewHeight", Qt.DirectConnection,
                    Q_ARG("double", 220.0 + i * 20)))
                measure("minimap_overview", lambda i: editor.noteModel.overviewByLayer)
                result["shell_load_ms"] = round(result["shell_load_ms"], 2)
                results.append(result)
                root.close()
                engine.deleteLater()
                app.processEvents()
    if args.project:
        assert hashlib.sha256(args.project.read_bytes()).hexdigest() == source_digest
    output = args.output
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
