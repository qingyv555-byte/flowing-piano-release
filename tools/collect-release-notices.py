"""Collect exact environment notices and upstream source references for a candidate.

This prepares evidence; it does not declare license compliance or publish a release.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile


def notice_path(name: str) -> Path | None:
    relative = Path(*Path(name).parts[1:])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe upstream archive path')
    leaf = relative.name.lower()
    if ('LICENSES' in relative.parts or
            leaf.startswith(('license', 'copying', 'copyright', 'notice', 'authors')) or
            leaf == 'qt_attribution.json'):
        return relative
    return None


def write_notice_inventory(output: Path) -> None:
    records = []
    for path in sorted(output.rglob('*')):
        if path.is_file() and path.name != 'NOTICE-FILES.json':
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            records.append({'file': path.relative_to(output).as_posix(), 'sha256': digest})
    (output / 'NOTICE-FILES.json').write_text(json.dumps({'files': records}, indent=2) + '\n', encoding='utf-8')


def store_upstream_notice(output: Path, source_record: dict, original: str,
                          payload: bytes, records: list) -> None:
    # Deep CPython/vendored paths exceed the Windows installer compiler's path
    # limit. Preserve bytes and original provenance with short, unique names.
    leaf = re.sub(r'[^A-Za-z0-9._-]', '_', Path(original).name)[-40:]
    path_id = hashlib.sha256(original.encode('utf-8')).hexdigest()[:12]
    destination = output / 'upstream' / source_record['sha256'][:12] / (path_id + '-' + leaf)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.read_bytes() != payload:
        raise ValueError('Upstream notice filename collision')
    destination.write_bytes(payload)
    records.append({'archive': source_record['file'], 'original_path': original,
                    'file': destination.relative_to(output).as_posix(),
                    'sha256': hashlib.sha256(payload).hexdigest()})


def collect(output: Path, source_cache: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    static_notices = Path(__file__).resolve().parents[1] / 'resources' / 'release'
    for notice in static_notices.iterdir():
        if notice.name.startswith(('LICENSE-', 'RUNTIME-NOTICES', 'MICROSOFT-RUNTIME-SOURCE', 'FFMPEG-BUILD')):
            shutil.copy2(notice, output / notice.name)
    inventory = []
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = distribution.metadata['Name']
        records = []
        original_notices = []
        for record in distribution.files or ():
            parts = Path(str(record)).parts
            if not any(part.endswith('.dist-info') for part in parts):
                continue
            if ('/licenses/' not in str(record).replace('\\', '/').lower()
                    and not parts[-1].lower().startswith(('license', 'copying', 'copyright', 'notice', 'authors'))):
                continue
            source = Path(distribution.locate_file(record))
            if not source.is_file():
                continue
            # Keep distinct vendored paths in the index and use bounded file
            # names, including on long Windows installation directories.
            relative = Path(*parts)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe distribution notice path')
            original_path = relative.as_posix()
            package_id = hashlib.sha256(name.lower().encode()).hexdigest()[:12]
            path_id = hashlib.sha256(original_path.encode()).hexdigest()[:12]
            leaf = re.sub(r'[^A-Za-z0-9._-]', '_', relative.name)[-40:]
            destination = output / 'python-packages' / package_id / (path_id + '-' + leaf)
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = source.read_bytes()
            if destination.is_file() and destination.read_bytes() != payload:
                raise ValueError('Distribution notice filename collision')
            destination.write_bytes(payload)
            records.append(destination.relative_to(output).as_posix())
            original_notices.append({'file': records[-1], 'original_path': original_path,
                                     'sha256': hashlib.sha256(payload).hexdigest()})
        inventory.append({'name': name, 'version': distribution.version,
                          'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),
                          'notice_files': records, 'notice_sources': original_notices})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise FileNotFoundError('The exact Python runtime license is missing')
    shutil.copy2(python_license, output / 'PYTHON-LICENSE.txt')
    sources = json.loads((source_cache / 'SHA256SOURCES.json').read_text(encoding='utf-8'))
    upstream_notices = []
    for source_record in sources:
        archive = source_cache / source_record['file']
        with archive.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != source_record['sha256']:
            raise ValueError('Source archive checksum mismatch: ' + archive.name)
        if archive.name.endswith('.tar.zst'):
            # CPython standalone's full build carries its dependency notices
            # and exact build metadata, absent from the install-only runtime.
            names = subprocess.check_output(['tar', '-tf', str(archive)], text=True).splitlines()
            for name in names:
                if name != 'python/PYTHON.json' and not name.startswith('python/licenses/'):
                    continue
                relative = Path(*Path(name).parts[1:])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Unsafe standalone archive path')
                if name.endswith('/'):
                    continue
                store_upstream_notice(output, source_record, name,
                                      subprocess.check_output(['tar', '-xOf', str(archive), name]), upstream_notices)
            continue
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if member.is_dir():
                        continue
                    relative = notice_path(member.filename)
                    if relative is not None:
                        store_upstream_notice(output, source_record, member.filename, bundle.read(member), upstream_notices)
            continue
        with tarfile.open(archive, 'r:*') as bundle:
            for member in bundle:
                if not member.isfile():
                    continue
                relative = notice_path(member.name)
                if relative is None:
                    continue
                payload = bundle.extractfile(member)
                if payload is not None:
                    store_upstream_notice(output, source_record, member.name, payload.read(), upstream_notices)
    (output / 'UPSTREAM-NOTICES.json').write_text(json.dumps(upstream_notices, indent=2) + '\n', encoding='utf-8')
    (output / 'SOURCE-ARCHIVES.json').write_text(json.dumps(sources, indent=2) + '\n', encoding='utf-8')
    (output / 'DEPENDENCIES.json').write_text(json.dumps({
        'scope': 'Exact build environment inventory; presence does not imply every module is shipped.',
        'python': sys.version,
        'distributions': inventory,
    }, indent=2) + '\n', encoding='utf-8')
    write_notice_inventory(output)
    print(f'Collected {len(inventory)} exact distribution records and upstream notices in {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-cache', type=Path, required=True)
    args = parser.parse_args()
    collect(args.output.resolve(), args.source_cache.resolve())
