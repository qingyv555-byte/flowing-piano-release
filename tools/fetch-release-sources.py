"""Fetch pinned source archives without changing the application environment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def fetch(manifest: Path, cache: Path) -> None:
    records = json.loads(manifest.read_text(encoding='utf-8'))
    cache.mkdir(parents=True, exist_ok=True)
    for record in records:
        name = record['file']
        if Path(name).name != name or not record['url'].startswith('https://'):
            raise ValueError('Unsafe source archive record')
        destination = cache / name
        if not destination.is_file():
            temporary = destination.with_name(name + '.partial')
            print('Downloading ' + name, flush=True)
            with urllib.request.urlopen(record['url'], timeout=60) as response, temporary.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            with temporary.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if digest != record['sha256']:
                raise ValueError('Source archive checksum mismatch: ' + name)
            temporary.replace(destination)
        with destination.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != record['sha256'] or destination.stat().st_size != record['bytes']:
            raise ValueError('Source archive checksum/size mismatch: ' + name)
    (cache / 'SHA256SOURCES.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    print(f'Verified {len(records)} pinned source archives', flush=True)


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=root / 'resources/release/SOURCE-ARCHIVES.json')
    parser.add_argument('--cache', type=Path, default=root / '.release-source-cache')
    args = parser.parse_args()
    fetch(args.manifest.resolve(), args.cache.resolve())
