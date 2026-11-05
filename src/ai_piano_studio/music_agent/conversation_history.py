"""Project-scoped transactional chat; the old mixed table remains archived."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class ConversationHistory:
    def __init__(self, path: Path, *, project_key: str | None = None) -> None:
        self.path = Path(path)
        self.project_key = project_key
        self.conversation_id = "legacy" if project_key is None else ""

    def _initialize(self, connection) -> None:
        if self.project_key is None:
            connection.execute('CREATE TABLE IF NOT EXISTS messages (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            return
        connection.execute('CREATE TABLE IF NOT EXISTS project_conversations (project_key TEXT PRIMARY KEY, conversation_id TEXT NOT NULL UNIQUE)')
        connection.execute('CREATE TABLE IF NOT EXISTS project_messages (conversation_id TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(conversation_id, position))')
        connection.execute('INSERT OR IGNORE INTO project_conversations VALUES (?, ?)', (self.project_key, uuid4().hex))
        self.conversation_id = connection.execute('SELECT conversation_id FROM project_conversations WHERE project_key=?', (self.project_key,)).fetchone()[0]

    def load(self) -> list[dict[str, str]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self._initialize(connection)
            rows = (connection.execute('SELECT payload FROM messages ORDER BY position').fetchall()
                    if self.project_key is None else connection.execute(
                        'SELECT payload FROM project_messages WHERE conversation_id=? ORDER BY position', (self.conversation_id,)).fetchall())
        result = [json.loads(row[0]) for row in rows]
        if any(not isinstance(row, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in row.items()) for row in result):
            raise ValueError('Invalid conversation history')
        return result

    def save(self, position: int, message: dict[str, str]) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self._initialize(connection)
            if self.project_key is None:
                connection.execute('INSERT INTO messages(position, payload) VALUES (?, ?) ON CONFLICT(position) DO UPDATE SET payload=excluded.payload',
                                   (position, json.dumps(message, ensure_ascii=False)))
            else:
                connection.execute('INSERT INTO project_messages VALUES (?, ?, ?) ON CONFLICT(conversation_id, position) DO UPDATE SET payload=excluded.payload',
                                   (self.conversation_id, position, json.dumps(message, ensure_ascii=False)))

    def clear(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self._initialize(connection)
            if self.project_key is None:
                connection.execute('DELETE FROM messages')
            else:
                connection.execute('DELETE FROM project_messages WHERE conversation_id=?', (self.conversation_id,))

    def promote(self, project_key: str) -> None:
        """First save changes the draft's key without changing its conversation."""
        if self.project_key is None:
            raise ValueError('Legacy mixed history cannot be assigned to a project')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self._initialize(connection)
            other = connection.execute('SELECT conversation_id FROM project_conversations WHERE project_key=?', (project_key,)).fetchone()
            if other and other[0] != self.conversation_id:
                raise ValueError('Destination project already has a conversation')
            connection.execute('UPDATE project_conversations SET project_key=? WHERE conversation_id=?', (project_key, self.conversation_id))
        self.project_key = project_key
