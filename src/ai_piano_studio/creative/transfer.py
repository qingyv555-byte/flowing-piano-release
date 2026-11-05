"""Selected theme + scope package. No profile, credentials, source or permissions."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import UUID, uuid4
import zipfile

from ai_piano_studio.persistence.project_document import project_document_to_dict, project_document_from_dict
from .memory import MemoryStore, validate_scope
from .story import CuePlan


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')


def _metadata(value):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'id', 'revision', 'scope', 'cue'} or value['schema_version'] != 1:
        raise ValueError('unsupported theme metadata')
    if str(UUID(value['id'])) != value['id']:
        raise ValueError('noncanonical theme identity')
    validate_scope(value['scope'])
    if type(value['revision']) is not int or not 1 <= value['revision'] <= 1_000_000:
        raise ValueError('invalid theme revision')
    if value['cue'] is not None:
        CuePlan(**value['cue'])
    return value


def _memories(value):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'records'} or value['schema_version'] != 1:
        raise ValueError('unsupported memory package')
    if not isinstance(value['records'], list) or len(value['records']) > 2000:
        raise ValueError('memory package too large')
    for record in value['records']:
        MemoryStore._validate(record)
    if len({r['id'] for r in value['records']}) != len(value['records']):
        raise ValueError('duplicate memory identity')
    return value


def export_bundle(path, memories, document, metadata):
    metadata = _metadata(metadata)
    _memories(memories)
    if not 1 <= len(document.state.notes) <= 256:
        raise ValueError('select a bounded theme')
    payload = {
        'memories.json': _json(memories),
        'project.pianoai.json': _json(project_document_to_dict(document)),
        'theme.json': _json(metadata),
    }
    if any(len(data) > 2_000_000 for data in payload.values()):
        raise ValueError('selected assets too large')
    payload['manifest.json'] = _json({'schema_version': 1, 'files': {name: sha256(data).hexdigest() for name, data in payload.items()}})
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.creative-', suffix='.zip', dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in payload.items():
                archive.writestr(name, data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_bundle(path):
    path = Path(path)
    if path.stat().st_size > 8_000_000:
        raise ValueError('package too large')
    expected = {'manifest.json', 'memories.json', 'project.pianoai.json', 'theme.json'}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != 4 or {item.filename for item in entries} != expected:
            raise ValueError('unsupported package paths')
        for item in entries:
            if item.file_size > 2_000_000 or item.is_dir() or item.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError('unsafe package entry')
        data = {item.filename: archive.read(item) for item in entries}
    manifest = json.loads(data['manifest.json'])
    if set(manifest) != {'schema_version', 'files'} or manifest['schema_version'] != 1 or set(manifest['files']) != expected - {'manifest.json'}:
        raise ValueError('unsupported manifest')
    for name, digest in manifest['files'].items():
        if sha256(data[name]).hexdigest() != digest:
            raise ValueError('checksum mismatch')
    memories = _memories(json.loads(data['memories.json']))
    metadata = _metadata(json.loads(data['theme.json']))
    document = project_document_from_dict(json.loads(data['project.pianoai.json']))
    if not 1 <= len(document.state.notes) <= 256 or any(n.start_time+n.duration > 120 for n in document.state.notes):
        raise ValueError('theme exceeds budget')
    if any(record['scope'] != metadata['scope'] for record in memories['records']):
        raise ValueError('package must contain only selected scope')
    return memories, metadata, document, data


def import_bundle(path, store, library, *, as_new=False):
    memories, metadata, document, data = read_bundle(path)
    if as_new:
        metadata = dict(metadata, id=str(uuid4()), revision=1)
        data['theme.json'] = _json(metadata)
    root = Path(library).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / metadata['id']
    imported, conflicts, created = 0, [], False
    try:
        if target.exists():
            if target.is_symlink() or any(not (target/name).is_file() or (target/name).is_symlink()
                or (target/name).stat().st_size > 2_000_000
                or (target/name).read_bytes() != data[name] for name in ('project.pianoai.json', 'theme.json')):
                conflicts.append(metadata['id'])
        else:
            target.mkdir()
            created = True
            for name in ('project.pianoai.json', 'theme.json'):
                with (target/name).open('xb') as handle:
                    handle.write(data[name])
                    handle.flush()
                    os.fsync(handle.fileno())
            imported = 1
        # Last fallible mutation: this method is an SQLite transaction.
        result = store.import_data(memories)
    except Exception:
        if created and target.resolve().parent == root and not target.is_symlink():
            shutil.rmtree(target)
        raise
    return {**result, 'themes_imported': imported, 'theme_conflicts': conflicts,
            'theme_id': metadata['id'], 'scope': metadata['scope']}
