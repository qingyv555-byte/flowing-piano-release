"""Release notices retain distinct upstream and vendored license texts."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import tarfile


def test_vendored_license_files_are_not_overwritten_or_omitted(tmp_path, monkeypatch):
    script = Path(__file__).parents[2] / 'tools' / 'collect-release-notices.py'
    spec = importlib.util.spec_from_file_location('release_notices', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = [
        'parent.dist-info/licenses/LICENSE',
        'parent/_vendor/first.dist-info/licenses/LICENSE',
        'parent/_vendor/second.dist-info/LICENSE',
        'parent/_vendor/second.dist-info/NOTICE',
    ]
    site = tmp_path / 'site'
    for index, record in enumerate(records):
        source = site / record
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f'upstream notice {index}', encoding='utf-8')
    distribution = SimpleNamespace(
        metadata={'Name': 'parent', 'License-Expression': 'MIT'},
        version='1.0', files=records, locate_file=lambda record: site / record,
    )
    monkeypatch.setattr(module.metadata, 'distributions', lambda: [distribution])
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'LICENSE.txt').write_text('Python license', encoding='utf-8')
    monkeypatch.setattr(module, 'sys', SimpleNamespace(base_prefix=str(runtime), version='fixture'))
    cache = tmp_path / 'sources'
    cache.mkdir()
    (cache / 'SHA256SOURCES.json').write_text('[]', encoding='utf-8')
    output = tmp_path / 'notices'
    module.collect(output, cache)
    inventory = json.loads((output / 'DEPENDENCIES.json').read_text())
    notices = inventory['distributions'][0]['notice_files']
    assert len(notices) == len(set(notices)) == 4
    assert {(output / name).read_text() for name in notices} == {
        f'upstream notice {index}' for index in range(4)
    }


def test_deep_upstream_license_paths_remain_distinct_and_fit_windows_installer(tmp_path, monkeypatch):
    script = Path(__file__).parents[2] / 'tools' / 'collect-release-notices.py'
    spec = importlib.util.spec_from_file_location('release_notices_paths', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.metadata, 'distributions', lambda: [])
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'LICENSE.txt').write_text('Python license')
    monkeypatch.setattr(module, 'sys', SimpleNamespace(base_prefix=str(runtime), version='fixture'))
    cache = tmp_path / 'cache'
    cache.mkdir()
    archive = cache / 'fixture-source.tar.gz'
    originals = {}
    with tarfile.open(archive, 'w:gz') as bundle:
        for index in range(2):
            name = 'source/' + ('long-component/' * 20) + str(index) + '/LICENSE.txt'
            payload = f'Original upstream copyright {index}'.encode()
            originals[name] = payload
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (cache / 'SHA256SOURCES.json').write_text(json.dumps([{'file': archive.name, 'sha256': digest}]))
    output = tmp_path / 'notices'
    module.collect(output, cache)
    records = json.loads((output / 'UPSTREAM-NOTICES.json').read_text())
    assert len(records) == 2
    assert len({record['file'] for record in records}) == 2
    for record in records:
        assert len(record['file']) < 100
        assert (output / record['file']).read_bytes() == originals[record['original_path']]
        assert record['sha256'] == hashlib.sha256(originals[record['original_path']]).hexdigest()
