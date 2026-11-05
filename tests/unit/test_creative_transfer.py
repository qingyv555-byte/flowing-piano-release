from dataclasses import replace
import json
from uuid import uuid4
import zipfile
import pytest
from ai_piano_studio.creative.transfer import export_bundle, import_bundle
from ai_piano_studio.creative.memory import MemoryStore
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.note import Note
from ai_piano_studio.persistence.project_document import ProjectDocument


def test_two_profiles_roundtrip_and_conflict_do_not_overwrite(tmp_path):
    source, target = MemoryStore(tmp_path/'a.sqlite'), MemoryStore(tmp_path/'b.sqlite')
    source.remember('work-a', 'density', 'sparse', confirmed=True)
    document = ProjectDocument(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.5),)))
    identity = str(uuid4())
    metadata = {'schema_version': 1, 'id': identity, 'revision': 1, 'scope': 'work-a', 'cue': None}
    path = tmp_path/'themes.zip'
    export_bundle(path, source.export_data(['work-a']), document, metadata)
    result = import_bundle(path, target, tmp_path/'library')
    assert result['themes_imported'] == 1 and result['imported'] == 1
    assert import_bundle(path, target, tmp_path/'library')['themes_imported'] == 0
    old = (tmp_path/'library'/identity/'project.pianoai.json').read_bytes()
    altered = ProjectDocument(replace(document.state, notes=(Note.create(pitch=72, start_time=0, duration=.5),), playback_notes=None))
    export_bundle(path, source.export_data(['work-a']), altered, dict(metadata, revision=2))
    assert import_bundle(path, target, tmp_path/'library')['theme_conflicts'] == [identity]
    assert (tmp_path/'library'/identity/'project.pianoai.json').read_bytes() == old


def test_malicious_or_corrupt_package_leaves_store_unchanged(tmp_path):
    target = MemoryStore(tmp_path/'b.sqlite')
    package = tmp_path/'bad.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('../escape.json', '{}')
        archive.writestr('manifest.json', json.dumps({'schema_version': 1, 'files': {}}))
    with pytest.raises(ValueError):
        import_bundle(package, target, tmp_path/'library')
    assert not (tmp_path/'escape.json').exists()
    assert not target.recall('global')


def test_memory_transaction_failure_rolls_back_new_theme_directory(tmp_path, monkeypatch):
    source, target = MemoryStore(tmp_path/'a.sqlite'), MemoryStore(tmp_path/'b.sqlite')
    document = ProjectDocument(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.5),)))
    identity = str(uuid4())
    metadata = {'schema_version': 1, 'id': identity, 'revision': 1, 'scope': 'global', 'cue': None}
    path = tmp_path/'themes.zip'
    export_bundle(path, source.export_data(['global']), document, metadata)
    def fail(_):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(target, 'import_data', fail)
    with pytest.raises(OSError):
        import_bundle(path, target, tmp_path/'library')
    assert not (tmp_path/'library'/identity).exists()
