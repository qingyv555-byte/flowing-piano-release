"""Build identity for both a Git checkout and a distributed source snapshot."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


def read_identity(root: Path) -> dict:
    if (root / '.git').exists():
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        branch = subprocess.check_output(['git', '-C', str(root), 'branch', '--show-current'], text=True).strip()
        dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']))
        return {'git_commit': commit, 'git_branch': branch, 'source_dirty': dirty, 'git_checkout': True}
    snapshot = root / 'SOURCE-SNAPSHOT.json'
    record = json.loads(snapshot.read_text(encoding='utf-8'))
    commit = record['base_commit']
    if len(commit) != 40 or any(character not in '0123456789abcdef' for character in commit):
        raise ValueError('Source snapshot contains an invalid base commit')
    return {'git_commit': commit, 'git_branch': record.get('git_branch', 'source-snapshot'),
            'source_dirty': True, 'git_checkout': False,
            'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}


if __name__ == '__main__':
    print(json.dumps(read_identity(Path(__file__).resolve().parents[1])))
