"""Application-owned task facts. Never stores or restores execution authority."""
from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


def bounded_text(text: str, size: int) -> str:
    """Keep both ends: corrections commonly occur at the end of a message."""
    if len(text) <= size:
        return text
    marker = "\n[…中间省略，原文保留在本地记录…]\n"
    remaining = max(0, size - len(marker))
    return text[:remaining // 2] + marker + text[-(remaining - remaining // 2):]


class TaskMemory:
    def __init__(self, path: Path | None, conversation_id: str):
        self.path, self.conversation_id = path, conversation_id
        self.state: dict = {}
        if path is not None:
            with self._db() as db:
                row = db.execute("SELECT payload FROM assistant_task_state WHERE conversation_id=?",
                                 (conversation_id,)).fetchone()
                if row:
                    self.state = json.loads(row[0])
                    if (not isinstance(self.state, dict) or not isinstance(self.state.get("instructions"), list)
                            or not isinstance(self.state.get("constraints"), dict)
                            or not isinstance(self.state.get("receipt"), dict)):
                        raise ValueError("invalid task memory")

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS assistant_task_state (conversation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS assistant_task_events (conversation_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(conversation_id, version))")
            yield db

    def _save(self, kind: str, payload: dict):
        previous = self.state.get("version", 0)
        self.state["version"] = previous + 1
        try:
            if self.path is not None:
                with self._db() as db:
                    row = db.execute("SELECT payload FROM assistant_task_state WHERE conversation_id=?", (self.conversation_id,)).fetchone()
                    stored_version = json.loads(row[0]).get("version", 0) if row else 0
                    if stored_version != previous:
                        raise sqlite3.OperationalError("task memory changed in another controller")
                    db.execute("INSERT INTO assistant_task_events VALUES (?, ?, ?, ?)",
                               (self.conversation_id, self.state["version"], kind, json.dumps({**payload, "taskId": self.state.get("taskId")}, ensure_ascii=False)))
                    db.execute("INSERT INTO assistant_task_state VALUES (?, ?) ON CONFLICT(conversation_id) DO UPDATE SET payload=excluded.payload",
                               (self.conversation_id, json.dumps(self.state, ensure_ascii=False)))
        except (OSError, ValueError, sqlite3.Error):
            self.state["version"] = previous
            raise

    def accept(self, text: str, intent: str, *, new_task: bool = False):
        if intent not in {"CREATE_MUSIC", "EDIT_MUSIC"}:
            return
        if not self.state or new_task or (intent == "CREATE_MUSIC" and self.state.get("status") in {"completed", "cancelled"}):
            version = self.state.get("version", 0)
            self.state = dict(taskId=uuid4().hex, version=version, goal=text,
                              instructions=[], constraints={}, status="pending", receipt={})
        self.state["instructions"].append(text)
        self.state["intent"] = intent
        self.state["status"] = "pending"
        constraints = self.state["constraints"]
        duration = (re.search(r"(?:到|至|改为|改成)\s*(\d+(?:\.\d+)?)\s*(?:秒|s\b)", text.lower())
                    or re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s\b)", text.lower()))
        if duration:
            constraints["targetSeconds"] = float(duration[1])
        if re.search(r"不要.*(?:重复|循环)|别.*(?:重复|循环)|不.*重复", text):
            constraints["allowRepeat"] = False
        elif re.search(r"(?:允许|可以|改为|就|请).*(?:重复|循环)", text):
            constraints["allowRepeat"] = True
        if re.search(r"原曲|原版|找谱|找.*midi", text, re.I):
            constraints["sourceMode"] = "original_requested_not_verified"
        if (re.search(r"(?:改为|换成|允许|就|请).*原创|原创.*(?:曲|旋律)", text)
                and not re.search(r"不要.*原创|不.*原创|拒绝.*原创", text)):
            constraints["sourceMode"] = "original_composition"
        from ai_piano_studio.music_agent.song_resolver import music_request
        request = music_request(text)
        if request.named and len(request.title) <= 100 and not re.search(r"原曲|原版|续写|扩展|重复|循环|midi|找谱|秒", request.title, re.I):
            constraints["songTitle"] = request.title
            constraints["artist"] = request.artist
        if re.search(r"(?:保留|不改|不要改).*(?:开头|前奏)", text):
            constraints["preserveOpening"] = True
        elif re.search(r"(?:重写|替换|改掉).*(?:开头|前奏)", text):
            constraints["preserveOpening"] = False
        self._save("user_instruction", {"text": text, "intent": intent})

    def record(self, status: str, facts: dict, *, task_id: str | None = None):
        if not self.state or (task_id is not None and task_id != self.state.get("taskId")):
            return
        self.state["status"] = status
        self.state["receipt"] = deepcopy(facts)
        self._save("execution_receipt", {"status": status, "facts": facts})

    def accept_song(self, text: str, title: str, artist: str, seconds: float, *, continuing: bool,
                    section: str | None = None, start_seconds: float | None = None):
        """Store the model's interpretation separately from evidence and authority."""
        previous = deepcopy(self.state)
        if not continuing or self.state.get("constraints", {}).get("sourceMode") != "existing_song":
            self.state = dict(taskId=uuid4().hex, version=self.state.get("version", 0),
                              goal=text, instructions=[], constraints={}, receipt={})
        if not self.state["instructions"] or self.state["instructions"][-1] != text:
            self.state["instructions"].append(text)
        self.state.update(intent="CREATE_MUSIC", status="pending")
        if (title, artist) != (previous.get('constraints', {}).get('songTitle'),
                               previous.get('constraints', {}).get('artist')):
            self.state['constraints'].pop('section', None)
            self.state['constraints'].pop('sourceStartSeconds', None)
        self.state["constraints"].update(songTitle=title, artist=artist, targetSeconds=seconds,
                                         sourceMode="existing_song")
        if section is not None:
            self.state['constraints']['section'] = section
            # A changed segment without a new time has no verified timestamp.
            if section != previous.get('constraints', {}).get('section') and start_seconds is None:
                self.state['constraints'].pop('sourceStartSeconds', None)
        if start_seconds is not None:
            self.state['constraints']['sourceStartSeconds'] = start_seconds
        try:
            self._save("song_instruction", {"text": text, "constraints": self.state["constraints"]})
        except (OSError, ValueError, sqlite3.Error):
            self.state = previous
            raise

    def context(self, current: dict) -> dict:
        if not self.state:
            return {}
        state = deepcopy(self.state)
        # Raw instructions remain local; the projection preserves effective fields
        # and both ends of recent amendments, in chronological order.
        state["goal"] = bounded_text(state["goal"], 800)
        state["instructions"] = [bounded_text(t, 400) for t in state["instructions"][-8:]]
        state["currentProject"] = current
        state["instructionCount"] = len(self.state["instructions"])
        state["receiptIsHistorical"] = state["receipt"].get("fingerprint") != current.get("fingerprint")
        # Provenance and large read traces are archived locally. Carry only
        # verified execution facts and bounded source identity into the request.
        receipt = state["receipt"]
        state["receipt"] = {k: receipt[k] for k in (
            "revision", "fingerprint", "durationSeconds", "noteCount", "errorCode",
            "outcomeStatus", "committed", "proposalStatus", "previewDuration") if k in receipt}
        if isinstance(receipt.get("source"), dict):
            state["receipt"]["source"] = {k: (receipt["source"][k] if isinstance(receipt["source"][k], bool) else bounded_text(str(receipt["source"][k]), 180))
                for k in ("kind", "verifiedNotes", "source_url", "title", "artist", "edition", "section", "stage")
                if k in receipt["source"]}
        # Bound the projection, never the persisted events. Effective typed
        # constraints and recent amendments stay separate from archival prose.
        from ai_piano_studio.music_agent.history_context import clip_bytes
        while len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > 3600 and len(state["instructions"]) > 1:
            state["instructions"].pop(0)
        state["goal"] = clip_bytes(state["goal"], 600)
        state["instructionsOmitted"] = (len(self.state["instructions"]) > len(state["instructions"])
                                        or any(len(t) > 400 for t in self.state["instructions"][-8:]))
        state["archiveTool"] = "get_task_history: query earlier requirements before acting if instructionsOmitted; user instructions are chronological, later corrections override earlier ones."
        if state["receiptIsHistorical"] and state["status"] in {"completed", "proposal"}:
            state["status"] = "needs_revalidation"
        if len(json.dumps(state, ensure_ascii=False).encode()) > 4000:
            state["instructions"] = [clip_bytes(t, 200) for t in state["instructions"]]
            state["goal"] = clip_bytes(state["goal"], 200)
        return state

    def read_instructions(self, query="", offset=0, char_offset=0):
        records = [(i, t) for i, t in enumerate(self.state.get("instructions", [])) if query.casefold() in t.casefold()]
        page = records[offset:offset + 3]
        return {"instructions": [{"index": i, "text": t[char_offset:char_offset + 1200],
                "hasMoreText": len(t) > char_offset + 1200} for i, t in page],
                "nextOffset": offset + len(page), "hasMore": len(records) > offset + len(page),
                "taskId": self.state.get("taskId", ""), "version": self.state.get("version", 0)}

    def reconcile(self, current):
        if (self.state.get("status") == "completed" and self.state.get("receipt", {}).get("fingerprint") != current.get("fingerprint")):
            self.record("needs_revalidation", {**current, "reason": "project_changed_after_completion"})

    def clear(self):
        if self.path is not None:
            with self._db() as db:
                db.execute("DELETE FROM assistant_task_state WHERE conversation_id=?", (self.conversation_id,))
                db.execute("DELETE FROM assistant_task_events WHERE conversation_id=?", (self.conversation_id,))
        self.state = {}

    def copy_to(self, conversation_id: str):
        target = TaskMemory(self.path, conversation_id)
        target.state = deepcopy(self.state)
        if target.state:
            # Destination may have its own history. Keep its event sequence valid.
            if self.path is not None:
                with target._db() as db:
                    row = db.execute("SELECT MAX(version) FROM assistant_task_events WHERE conversation_id=?", (conversation_id,)).fetchone()
                target.state["version"] = row[0] or 0
            else:
                target.state["version"] = 0
            target._save("adopt_task", {"sourceConversation": self.conversation_id, "state": target.state})
        return target
