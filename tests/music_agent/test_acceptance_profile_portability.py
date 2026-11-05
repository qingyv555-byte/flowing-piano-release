"""A cloned acceptance bundle retains its native chat and task after moving."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.conversation_history import ConversationHistory
from ai_piano_studio.music_agent.task_memory import TaskMemory
from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document
from tools.open_real_music_acceptance import relocate_acceptance_profile


def bundle(tmp_path):
    home = tmp_path / "bundle" / "profile"
    project = home / "Projects" / "project-001" / "project.pianoai.json"
    project.parent.mkdir(parents=True)
    save_project_document(project, ProjectDocument(ProjectState((Note.create(pitch=60, start_time=0, duration=1),))))
    db = home / "assistant" / "conversation.sqlite3"
    history = ConversationHistory(db, project_key="file:original-checkout")
    history.load()
    for i, text in enumerate(("续写到25秒", "hi", "完成并保留原段")):
        history.save(i, {"kind": "USER_MESSAGE", "text": text, "title": "", "detail": "", "proposalId": ""})
    task = TaskMemory(db, history.conversation_id)
    task.accept("保留开头，续写到25秒", "EDIT_MUSIC")
    (home / "acceptance-manifest.json").write_text(json.dumps(dict(project=project.relative_to(home).as_posix(),
        conversationId=history.conversation_id, displayName="青花瓷真实模型验收"), ensure_ascii=False), encoding="utf-8")
    return home, project, db, task.state


def test_moving_bundle_rebinds_only_its_own_chat_and_preserves_task(tmp_path):
    home, project, db, task_state = bundle(tmp_path)
    before = sha256(db.read_bytes()).hexdigest()
    moved = tmp_path / "different-checkout" / "profile"
    shutil.copytree(home, moved)
    target = moved / project.relative_to(home)
    assert relocate_acceptance_profile(moved, target)
    history = ConversationHistory(moved / "assistant" / "conversation.sqlite3",
        project_key=MusicAssistantController._project_conversation_key(str(target)))
    assert [r["text"] for r in history.load()] == ["续写到25秒", "hi", "完成并保留原段"]
    assert TaskMemory(history.path, history.conversation_id).state == task_state
    assert sha256(db.read_bytes()).hexdigest() == before
    assert not relocate_acceptance_profile(moved, target)
    again = tmp_path / "third-checkout" / "profile"
    shutil.copytree(moved, again)
    assert relocate_acceptance_profile(again, again / target.relative_to(moved))


def test_relocation_refuses_to_overwrite_another_conversation(tmp_path):
    home, project, db, _ = bundle(tmp_path)
    other = ConversationHistory(db, project_key=MusicAssistantController._project_conversation_key(str(project)))
    other.load()
    before = sha256(db.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="different conversation"):
        relocate_acceptance_profile(home, project)
    assert sha256(db.read_bytes()).hexdigest() == before
