"""Open one isolated saved acceptance project with its native conversation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def relocate_acceptance_profile(home: Path, project: Path) -> bool:
    """Bind this bundle's one conversation to its current checkout path."""
    home, project = home.resolve(strict=True), project.resolve(strict=True)
    if not project.is_relative_to(home / "Projects"):
        raise ValueError("acceptance project must be inside its own profile")
    manifest_path = home / "acceptance-manifest.json"
    if not manifest_path.is_file():
        return False
    from contextlib import closing
    import sqlite3
    from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
    from ai_piano_studio.persistence.project_library import ProjectLibrary
    from ai_piano_studio.persistence.project_document import load_project_document
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if project.relative_to(home).as_posix() != manifest["project"]:
        raise ValueError("project does not match acceptance manifest")
    new_key = MusicAssistantController._project_conversation_key(str(project))
    cid = manifest["conversationId"]
    with closing(sqlite3.connect(home / "assistant" / "conversation.sqlite3")) as connection, connection:
        existing = connection.execute("SELECT project_key FROM project_conversations WHERE conversation_id=?", (cid,)).fetchone()
        if existing is None:
            raise ValueError("acceptance conversation is missing")
        conflict = connection.execute("SELECT conversation_id FROM project_conversations WHERE project_key=?", (new_key,)).fetchone()
        if conflict is not None and conflict[0] != cid:
            raise ValueError("destination already owns a different conversation")
        connection.execute("UPDATE project_conversations SET project_key=? WHERE conversation_id=?", (new_key, cid))
    library = ProjectLibrary(home / "Projects")
    library.set_display_name(project, manifest["displayName"])
    library.update_summary(project, load_project_document(project).state)
    return existing[0] != new_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-home", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--smoke-check", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--expected-user-message", default="hi")
    args = parser.parse_args()
    root_dir = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root_dir / "src"), str(root_dir)]
    home, project = args.profile_home.resolve(strict=True), args.project.resolve(strict=True)
    assert project.is_relative_to(home / "Projects")
    assert (home / "assistant" / "conversation.sqlite3").is_file()
    relocate_acceptance_profile(home, project)
    if args.smoke_check:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        from tools.studio_evidence import install_memory_output
        blocked = install_memory_output()
    from PySide6.QtCore import QTimer, QMetaObject, QObject, Qt
    from PySide6.QtWidgets import QApplication
    from ai_piano_studio.studio import build_studio_engine
    from ai_piano_studio.__main__ import initialize_deferred_services
    from ai_piano_studio.logging_config import configure_logging
    app = QApplication([])
    configure_logging(home / "logs")
    app, engine, editor, playback, _ = build_studio_engine(home)
    root = engine.rootObjects()[0]
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty("assistant")
    editor.loadProject(str(project))
    app.processEvents()
    assert editor.currentFilePath == str(project)
    assistant._provider_settings.set_dynamic_models("codex", [args.model])
    assert assistant.selectProvider("codex") and assistant.selectModel(args.model)
    root.setProperty("pageState", "editor")
    assert QMetaObject.invokeMethod(root, "openAssistantWindow", Qt.DirectConnection)
    root.resize(1440, 1000)

    def verify():
        try:
            assert editor.currentFilePath == str(project)
            assert len(assistant.messages.entries) >= 2
            assert any(m.kind.value == "USER_MESSAGE" and m.text == args.expected_user_message
                for m in assistant.messages.entries)
            assert root.property("studioAssistantOpen")
            pane = root.findChild(QObject, "assistantWorkspace")
            assert pane is not None and pane.isVisible() and pane.width() > 100
            listing = root.findChild(QObject, "assistantMessageList")
            assert listing is not None and listing.property("count") >= 2
            assert assistant.currentModelId == args.model
            assert not playback.playing and not blocked
            evidence_home = (args.evidence_dir or home.parent).resolve()
            evidence_home.mkdir(parents=True, exist_ok=True)
            assert root.grabWindow().save(str(evidence_home / "in-app-acceptance.png"))
            result = dict(projectPath=editor.currentFilePath, notes=len(editor.state.active_notes),
                duration=editor.state.playback_duration, chatMessages=len(assistant.messages.entries),
                visibleModel=assistant.currentModelId, assistantOpen=True, physicalAudioAttempts=blocked)
            (evidence_home / "in-app-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            app.exit(0)
        except Exception:
            import traceback
            traceback.print_exc()
            app.exit(1)

    if args.smoke_check:
        QTimer.singleShot(1500, verify)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
