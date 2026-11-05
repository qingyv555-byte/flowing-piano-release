"""Export auditable source, schemas, and document references (no user profile)."""
from pathlib import Path
import argparse
import hashlib
import json
import re


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/model-boundaries-20261104-before')
    parser.add_argument('--label', default='模型相关模块完整源码快照')
    arguments = parser.parse_args()
    target = root / arguments.output
    target.mkdir(parents=True, exist_ok=False)
    paths = sorted((root / 'src/ai_piano_studio/music_agent').glob('*.py'))
    paths += sorted((root / 'src/ai_piano_studio/score_lab').glob('*.py'))
    paths += [root / 'src/ai_piano_studio/qml/StudioAIServicePane.qml']
    records, corpus, hits = [], ['# ' + arguments.label + '\n'], ['# 限制相关行检索\n\n这是定位索引，不替代完整源码和实际调用条件。\n']
    pattern = re.compile(r'MAX_|MIN_|limit|timeout|deadline|permission|sandbox|disabled|denied|reject|guard|truncate|clip_|must|never|do not|maximum|minimum', re.I)
    for path in paths:
        body = path.read_text(encoding='utf-8-sig')
        rel = path.relative_to(root).as_posix()
        records.append(dict(path=rel, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), lines=len(body.splitlines())))
        corpus.append(f'\n## {rel}\n\n[打开原文件]({path.as_posix()})\n\n```text\n')
        corpus.extend(f'{n:5d} {line}\n' for n, line in enumerate(body.splitlines(), 1))
        corpus.append('```\n')
        matches = [(n, line) for n, line in enumerate(body.splitlines(), 1) if pattern.search(line)]
        if matches:
            hits.append(f'\n## {rel}\n\n')
            for n, line in matches:
                hits.append(f'- [L{n}]({path.as_posix()}:{n}): `{line.strip().replace(chr(96), chr(39))}`\n')
    (target / 'SOURCE-FULL.md').write_text(''.join(corpus), encoding='utf-8')
    (target / 'RESTRICTION-INDEX.md').write_text(''.join(hits), encoding='utf-8')
    (target / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    doc_pattern = re.compile(r'music.agent|tool.gateway|模型.*(?:限制|能力)|助手.*(?:权限|限制)|谱源.*确认|原生', re.I)
    docs = ['# 相关文档索引\n\n按路径列出；历史文档可能已过期，不能单凭文档认定仍生效。\n\n']
    for path in sorted((root / 'docs').rglob('*.md')):
        body = path.read_text(encoding='utf-8-sig')
        if doc_pattern.search(body):
            docs.append(f'- [{path.relative_to(root).as_posix()}]({path.as_posix()})\n')
    (target / 'DOCUMENTS.md').write_text(''.join(docs), encoding='utf-8')
    print(json.dumps(dict(directory=str(target), files=len(paths), sourceLines=sum(r['lines'] for r in records)), ensure_ascii=False))


if __name__ == '__main__':
    main()
