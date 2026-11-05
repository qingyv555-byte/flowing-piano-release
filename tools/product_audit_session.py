"""Launch the real app with an isolated library/settings for manual GUI audit.

No user projects or settings are read or modified. Explicitly launches a visible
window; do not run as part of automated pytest. Evidence stays in --output.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    os.environ["QT_QPA_PLATFORM"] = "windows"

    from PySide6.QtCore import QSettings, qInstallMessageHandler
    from ai_piano_studio.__main__ import build_engine
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.editor.controller import EditorController
    from ai_piano_studio.persistence.autosave import RecoveryStore
    from ai_piano_studio.persistence.project_library import ProjectLibrary
    from ai_piano_studio.persistence.project_json import save_project
    from ai_piano_studio.persistence.midi import export_midi
    from ai_piano_studio.persistence.recent_files import RecentFiles
    from ai_piano_studio.persistence.save_preferences import SavePreferences
    from ai_piano_studio.experience.settings import ExperienceSettings
    from ai_piano_studio.harmony.settings import HarmonySettings

    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(output / "settings"))
    log = (output / "qt-runtime.log").open("w", encoding="utf-8", buffering=1)
    qInstallMessageHandler(lambda kind, context, message: log.write(
        f"{kind.name}: {message}\n"))
    library = ProjectLibrary(output / "projects")
    fixture = library.root / "audit-fixture" / "project.pianoai.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    notes = tuple(Note.create(id=f"audit-{i}", pitch=(60, 64, 67, 72)[i % 4],
                              start_time=i * 0.5, duration=0.4)
                  for i in range(24))
    state = ProjectState(notes)
    if not fixture.exists():
        save_project(fixture, state)
        library.set_display_name(fixture, "AUDIT — 创作流程验证")
    export_midi(output / "audit-input.mid", notes, state.tempo)

    def settings(name):
        return QSettings(str(output / "settings" / f"{name}.ini"), QSettings.IniFormat)

    controller = EditorController(project_library=library,
                                  recent_files=RecentFiles(settings("recent")),
                                  recovery_store=RecoveryStore(output / "recovery"))
    app, engine, controller, playback, keyboard = build_engine(
        controller=controller,
        experience_settings=ExperienceSettings(settings=settings("experience")),
        harmony_settings=HarmonySettings(settings=settings("harmony")),
        save_preferences=SavePreferences(settings=settings("save")),
        appearance_settings=settings("appearance"),
        piano_tone_settings=settings("tone"),
        support_settings=settings("support"),
    )
    root = engine.rootObjects()[0]
    root.setWidth(1366)
    root.setHeight(768)
    journal = (output / "events.jsonl").open("w", encoding="utf-8", buffering=1)

    def record(event: str) -> None:
        journal.write(json.dumps({
            "event": event, "notes": len(controller.state.notes),
            "selection": sorted(controller.state.selected_ids),
            "layers": len(controller.state.layers),
            "active_layer": controller.activeLayerId,
            "dirty": controller.isDirty, "path": controller.currentFilePath,
            "undo": controller.historyUndoDepth,
        }, ensure_ascii=False) + "\n")

    controller.stateChanged.connect(lambda: record("state"))
    controller.saveSucceeded.connect(lambda path: record("saved"))
    controller.feedbackRequested.connect(lambda message: journal.write(
        json.dumps({"feedback": message}, ensure_ascii=False) + "\n"))
    playback.playingChanged.connect(lambda: journal.write(
        json.dumps({"playing": playback.playing}) + "\n"))
    record("launch")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
