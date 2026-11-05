import json

import pytest

from ai_piano_studio.creative.memory import MemoryStore


def test_memory_requires_confirmation_and_isolates_scopes(tmp_path):
    store = MemoryStore(tmp_path / 'memory.sqlite')
    with pytest.raises(ValueError, match='confirm'):
        store.remember('global', 'density', 'sparse', confirmed=False)
    common = store.remember('global', 'density', 'sparse', confirmed=True)
    personal = store.remember('character-a', 'register', 'low', confirmed=True)
    store.remember('character-b', 'register', 'high', confirmed=True)
    assert {r['id'] for r in store.recall('character-a')} == {common, personal}
    assert store.recall('character-a', include_global=False)[0]['id'] == personal
    store.delete(personal)
    assert [r['id'] for r in store.recall('character-a')] == [common]
    assert MemoryStore(tmp_path / 'memory.sqlite').recall('character-a') == store.recall('character-a')


def test_memory_does_not_store_arbitrary_source_or_secrets(tmp_path):
    store = MemoryStore(tmp_path / 'memory.sqlite')
    for kind, value in [('source', 'confidential paragraph'), ('density', 'sk-fake-credential'), ('tempo', 'private original')]:
        with pytest.raises(ValueError):
            store.remember('global', kind, value, confirmed=True)
    assert store.recall('global') == []


def test_memory_transfer_conflicts_and_deleted_records_do_not_revive(tmp_path):
    first = MemoryStore(tmp_path / 'first.sqlite')
    record_id = first.remember('work-a', 'density', 'sparse', confirmed=True)
    package = first.export_data(['work-a'])
    assert 'source_text' not in json.dumps(package)
    second = MemoryStore(tmp_path / 'second.sqlite')
    assert second.import_data(package)['imported'] == 1
    assert second.import_data(package)['imported'] == 0
    second.remember('work-a', 'density', 'balanced', confirmed=True, record_id=record_id)
    assert second.import_data(package)['conflicts'] == [record_id]
    second.delete(record_id)
    assert second.import_data(package)['conflicts'] == [record_id]
    assert second.recall('work-a') == []
    bad = {'schema_version': 1, 'records': [dict(package['records'][0], source_text='PRIVATE')]}
    with pytest.raises(ValueError):
        second.import_data(bad)
    assert second.recall('work-a') == []


def test_semantic_conflicts_and_tombstone_restoration_require_explicit_resolution(tmp_path):
    first, second = MemoryStore(tmp_path/'first.sqlite'), MemoryStore(tmp_path/'second.sqlite')
    first.remember('role', 'density', 'sparse', confirmed=True)
    local_id = second.remember('role', 'density', 'rich', confirmed=True)
    package = first.export_data(['role'])
    result = second.import_data(package)
    assert result['imported'] == 0
    conflict = result['conflict_details'][0]
    assert conflict['existing']['id'] == local_id
    second.resolve_conflict(conflict, restore_deleted=False)
    assert second.recall('role')[0]['value'] == 'sparse'
    second.delete(local_id)
    old_package = {'schema_version': 1, 'records': [dict(second.export_data(['role'])['records'][0], value='rich', status='confirmed', revision=1)]}
    conflict = second.import_data(old_package)['conflict_details'][0]
    with pytest.raises(ValueError, match='restore'):
        second.resolve_conflict(conflict, restore_deleted=False)
    assert not second.recall('role')
    second.resolve_conflict(conflict, restore_deleted=True)
    assert second.recall('role')[0]['value'] == 'rich'
