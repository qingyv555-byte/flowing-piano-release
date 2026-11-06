"""Snapshot public build inputs and pinned dependency archives for a candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
from source_identity import read_identity


ROOT_FILES = {
    'README.md', 'README.zh-CN.md', 'LICENSE', 'COPYRIGHT', 'CONTRIBUTING.md', 'CONSTRAINTS.md',
    'pyproject.toml', 'uv.lock', 'ai_piano_studio.spec', '.gitignore',
    'Open-Studio.cmd', 'Open-Nocturne.cmd', 'Open-Playing-Recording-Preview.cmd',
    '启动AI钢琴工作室.bat', 'THIRD_PARTY_NOTICES.md',
}

# Product/build/legal documentation only. Development journals and acceptance
# evidence may contain local paths, private conversations or unpublished assets.
PUBLIC_DOCS = {
    'docs/BUILD-FROM-SOURCE.md', 'docs/LICENSING.md', 'docs/RELEASING.md',
    'docs/TEMPLATE-SOURCES.md', 'docs/DOWNLOAD-FAQ.md',
    'docs/product-polish/wave2-evaluation.md',
}


def public_build_input(name: str) -> bool:
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        return False
    if path.name.startswith('skin-') and path.suffix.lower() == '.png':
        return False
    if any(part in {'__pycache__', '.git', 'source-assets', 'audition'} for part in path.parts):
        return False
    if name.endswith(('.local.json', '.pyc')):
        return False
    if name in ROOT_FILES:
        return True
    if name.startswith('docs/'):
        return name in PUBLIC_DOCS
    if name == 'tools/music-runtime-requirements.txt':
        return True
    if name.startswith(('src/ai_piano_studio/', 'resources/piano/banks/', 'resources/release/')):
        return True
    if name.startswith('resources/piano/') and path.name in {
        'banks.json', 'LICENSE-SALAMANDER.txt', 'PROVENANCE-SALAMANDER.json', 'PROVENANCE-SALAMANDER.md',
    }:
        return True
    if name.startswith(('tools/', 'installer/', 'tests/', '.github/')):
        return path.suffix.lower() in {'.py', '.ps1', '.cmd', '.cjs', '.iss', '.md', '.toml', '.yml', '.yaml', '.json'}
    return False


def build(root: Path, output: Path, cache: Path) -> None:
    identity = read_identity(root)
    if identity['git_checkout']:
        names = subprocess.check_output(
            ['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        ).decode('utf-8').split('\0')
    else:
        # A source ZIP deliberately has no Git history. Include its build tree
        # and any new source files, without scanning environments or profiles.
        names = list(ROOT_FILES)
        for prefix in ('src/ai_piano_studio', 'resources/piano', 'resources/release',
                       'tools', 'installer', 'tests', 'docs', '.github'):
            names.extend(path.relative_to(root).as_posix() for path in (root / prefix).rglob('*') if path.is_file())
    selected = sorted({name for name in names if public_build_input(name) and (root / name).is_file()})
    output.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(output / 'Flowing-Piano-Source.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name in selected:
            payload = (root / name).read_bytes()
            records.append({'file': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
            bundle.writestr('Flowing-Piano/' + name, payload)
        snapshot = {'base_commit': identity['git_commit'], 'git_branch': identity['git_branch'],
                    'source_dirty': identity['source_dirty'],
                    'files': records}
        bundle.writestr('Flowing-Piano/SOURCE-SNAPSHOT.json', json.dumps(snapshot, indent=2) + '\n')
    sources = json.loads((root / 'resources/release/SOURCE-ARCHIVES.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(output / 'Dependency-Sources.zip', 'w', zipfile.ZIP_STORED) as bundle:
        for record in sources:
            path = cache / record['file']
            if path.name != record['file']:
                raise ValueError('Unsafe dependency archive name')
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if digest != record['sha256']:
                raise ValueError('Dependency source checksum mismatch: ' + path.name)
            bundle.write(path, path.name)
        bundle.writestr('SHA256SOURCES.json', json.dumps(sources, indent=2) + '\n')
    checksums = []
    for name in ('Flowing-Piano-Source.zip', 'Dependency-Sources.zip'):
        with (output / name).open('rb') as source:
            checksums.append(hashlib.file_digest(source, 'sha256').hexdigest() + '  ' + name)
    (output / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    print(f'Snapshotted {len(records)} project files and {len(sources)} dependency source archives in {output}')


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=root / 'dist/release-sources')
    parser.add_argument('--source-cache', type=Path, default=root / '.release-source-cache')
    args = parser.parse_args()
    build(root, args.output.resolve(), args.source_cache.resolve())
