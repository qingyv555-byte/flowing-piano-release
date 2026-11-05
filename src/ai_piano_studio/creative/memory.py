"""Confirmed structured musical preferences, never captured text or credentials."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4


CHOICES = {
    'density': ('sparse', 'balanced', 'rich'),
    'register': ('low', 'middle', 'high'),
    'dynamics': ('gentle', 'balanced', 'strong'),
    'mood': ('calm', 'bright', 'wistful', 'playful', 'dramatic'),
    'tempo': ('slow', 'moderate', 'fast'),
    'ending': ('rest', 'resolve', 'open'),
}
_FIELDS = frozenset({'id', 'scope', 'kind', 'value', 'revision', 'status', 'confirmed_at', 'source'})


def validate_scope(scope: str) -> str:
    if not isinstance(scope, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', scope):
        raise ValueError('invalid memory scope')
    return scope


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences (id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, value TEXT, revision INTEGER NOT NULL, status TEXT NOT NULL, confirmed_at TEXT NOT NULL, source TEXT NOT NULL)')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('unsupported memory database version')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _validate(record: dict) -> dict:
        if not isinstance(record, dict) or set(record) != _FIELDS:
            raise ValueError('invalid memory fields')
        try:
            UUID(record['id'])
            validate_scope(record['scope'])
            datetime.fromisoformat(record['confirmed_at'])
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid memory identity') from None
        if record['kind'] not in CHOICES or record['source'] != 'user-confirmed':
            raise ValueError('invalid musical preference')
        if type(record['revision']) is not int or not 1 <= record['revision'] <= 1_000_000:
            raise ValueError('invalid memory revision')
        if record['status'] not in {'confirmed', 'deleted'}:
            raise ValueError('invalid memory status')
        if record['status'] == 'confirmed' and record['value'] not in CHOICES[record['kind']]:
            raise ValueError('invalid musical preference value')
        if record['status'] == 'deleted' and record['value'] is not None:
            raise ValueError('deleted memory cannot retain content')
        return record

    def remember(self, scope: str, kind: str, value: str, *, confirmed: bool, record_id: str | None = None) -> str:
        if confirmed is not True:
            raise ValueError('explicit confirmation required')
        validate_scope(scope)
        if kind not in CHOICES or value not in CHOICES[kind]:
            raise ValueError('only structured musical preferences may be saved')
        with self._connection() as db:
            old = db.execute('SELECT * FROM preferences WHERE id=?', (record_id,)).fetchone() if record_id else db.execute(
                "SELECT * FROM preferences WHERE scope=? AND kind=? AND status='confirmed' LIMIT 1", (scope, kind)
            ).fetchone()
            if record_id and (old is None or old['scope'] != scope):
                raise ValueError('memory not found in this scope')
            item = {'id': old['id'] if old else str(uuid4()), 'scope': scope, 'kind': kind, 'value': value,
                    'revision': old['revision'] + 1 if old else 1, 'status': 'confirmed',
                    'confirmed_at': datetime.now(timezone.utc).isoformat(), 'source': 'user-confirmed'}
            self._write(db, self._validate(item))
            return item['id']

    @staticmethod
    def _write(db, record):
        db.execute('INSERT OR REPLACE INTO preferences VALUES (:id,:scope,:kind,:value,:revision,:status,:confirmed_at,:source)', record)

    def recall(self, scope: str, *, include_global: bool = True) -> list[dict]:
        validate_scope(scope)
        with self._connection() as db:
            scopes = (scope, 'global' if include_global else scope)
            rows = db.execute("SELECT * FROM preferences WHERE status='confirmed' AND scope IN (?,?) ORDER BY confirmed_at DESC", scopes).fetchall()
        # Scope-specific preferences win over global ones for the same kind.
        selected = {}
        for row in sorted(rows, key=lambda item: item['scope'] != scope):
            selected.setdefault(row['kind'], dict(row))
        return list(selected.values())[:8]

    def records(self, scope: str) -> list[dict]:
        validate_scope(scope)
        with self._connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM preferences WHERE scope=? AND status='confirmed' ORDER BY kind", (scope,))]

    def delete(self, record_id: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE preferences SET value=NULL,status='deleted',revision=revision+1 WHERE id=?", (record_id,))

    def export_data(self, scopes: list[str]) -> dict:
        if not scopes or len(scopes) > 100:
            raise ValueError('select scopes to export')
        for scope in scopes:
            validate_scope(scope)
        with self._connection() as db:
            records = [dict(row) for row in db.execute('SELECT * FROM preferences') if row['scope'] in scopes]
        return {'schema_version': 1, 'records': records}

    def import_data(self, package: dict) -> dict:
        if not isinstance(package, dict) or set(package) != {'schema_version', 'records'} or package['schema_version'] != 1:
            raise ValueError('unsupported memory package')
        records = package['records']
        if not isinstance(records, list) or len(records) > 2000 or len(json.dumps(package)) > 2_000_000:
            raise ValueError('memory package too large')
        validated = [self._validate(item) for item in records]
        if len({item['id'] for item in validated}) != len(validated):
            raise ValueError('duplicate memory identity')
        imported, conflicts, details = 0, [], []
        with self._connection() as db:
            for item in validated:
                old = db.execute('SELECT * FROM preferences WHERE id=?', (item['id'],)).fetchone()
                if old is None and item['status'] == 'confirmed':
                    old = db.execute("SELECT * FROM preferences WHERE scope=? AND kind=? AND status='confirmed' LIMIT 1", (item['scope'], item['kind'])).fetchone()
                if old:
                    if dict(old) != item:
                        conflicts.append(item['id'])
                        details.append({'incoming': dict(item), 'existing': dict(old)})
                    continue
                self._write(db, item)
                imported += 1
        return {'imported': imported, 'conflicts': conflicts, 'conflict_details': details}

    def resolve_conflict(self, conflict: dict, *, restore_deleted: bool) -> None:
        incoming = self._validate(dict(conflict['incoming']))
        expected = self._validate(dict(conflict['existing']))
        with self._connection() as db:
            old = db.execute('SELECT * FROM preferences WHERE id=?', (expected['id'],)).fetchone()
            if old is None or dict(old) != expected:
                raise ValueError('memory changed; preview the import again')
            if old['status'] == 'deleted' and incoming['status'] != 'deleted' and not restore_deleted:
                raise ValueError('explicit restore confirmation required')
            updated = dict(incoming, id=old['id'], revision=max(old['revision'], incoming['revision']) + 1,
                           confirmed_at=datetime.now(timezone.utc).isoformat())
            self._write(db, self._validate(updated))
