"""Public source snapshots exclude private development and acceptance material."""
import importlib.util
import json
from pathlib import Path
import zipfile


def test_source_archive_keeps_build_docs_and_excludes_development_evidence(tmp_path, monkeypatch):
    tools = Path(__file__).parents[2] / 'tools'
    monkeypatch.syspath_prepend(str(tools))
    spec = importlib.util.spec_from_file_location('source_bundle_privacy', tools / 'build-source-bundle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'source'
    inputs = {
        'README.md': 'Product', 'README.zh-CN.md': '产品',
        'docs/BUILD-FROM-SOURCE.md': 'Build instructions',
        'docs/LICENSING.md': 'Licenses',
        'tools/Install-MusicRuntime.ps1': 'Install optional runtime',
        'tools/music-runtime-requirements.txt': 'basic-pitch==0.4.0',
        'tools/hub-workflow.cjs': 'Run diagnostic',
        'tools/private-log.txt': 'PRIVATE LOG',
        'docs/product-polish/private.md': 'PRIVATE CONVERSATION',
        'docs/rebuild/brief.md': 'PRIVATE BRIEF',
        'docs/RELEASE-PREPARATION.md': 'INTERNAL CHECKLIST',
        'assets/acceptance/profile/session.json': 'PRIVATE PROFILE',
        'src/ai_piano_studio/example.py': 'value = 1',
        'src/ai_piano_studio/settings.local.json': 'PRIVATE CONFIG',
        'src/ai_piano_studio/resources/skin-custom.png': 'PRIVATE ART',
        'resources/release/SOURCE-ARCHIVES.json': '[]',
    }
    for name, payload in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding='utf-8')
    monkeypatch.setattr(module, 'read_identity', lambda _: dict(git_checkout=False, git_commit='fixture', git_branch='', source_dirty=False))
    output = tmp_path / 'output'
    module.build(root, output, tmp_path / 'cache')
    with zipfile.ZipFile(output / 'Flowing-Piano-Source.zip') as bundle:
        names = {name.removeprefix('Flowing-Piano/') for name in bundle.namelist()}
        assert names == {
            'README.md', 'README.zh-CN.md', 'docs/BUILD-FROM-SOURCE.md',
            'docs/LICENSING.md', 'src/ai_piano_studio/example.py',
            'tools/Install-MusicRuntime.ps1', 'tools/music-runtime-requirements.txt',
            'tools/hub-workflow.cjs',
            'resources/release/SOURCE-ARCHIVES.json', 'SOURCE-SNAPSHOT.json',
        }
        assert all(b'PRIVATE' not in bundle.read(name) for name in bundle.namelist())
        snapshot = json.loads(bundle.read('Flowing-Piano/SOURCE-SNAPSHOT.json'))
        assert {row['file'] for row in snapshot['files']} == names - {'SOURCE-SNAPSHOT.json'}
