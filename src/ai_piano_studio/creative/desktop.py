"""Bounded desktop input and separate, revocable network permissions."""
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from pathlib import Path
import json
import os
import tempfile
import re


@dataclass(frozen=True)
class SourceToken:
    window: int
    process: int
    focus: int
    application: str


@dataclass(frozen=True)
class CaptureEnvelope:
    source: SourceToken
    text: str = field(repr=False)
    method: str = 'caret'
    truncated: bool = False


def recent_phrase(text):
    paragraphs = [part.strip() for part in re.split(r'[\r\n]+', text) if part.strip()]
    if not paragraphs:
        return ''
    sentences = [part.strip() for part in re.split(r'(?<=[。！？.!?])\s*', paragraphs[-1]) if part.strip()]
    return sentences[-1] if sentences else ''


def validate_capture(envelope, current, allowed, *, password=False, composing=False):
    if envelope.source != current:
        raise ValueError('前台或输入控件已经变化，本次内容已丢弃')
    if password or composing:
        raise ValueError('密码字段或输入法组合中，未读取/发送')
    if PureWindowsPath(current.application).name.lower() not in allowed:
        raise ValueError('此应用未获读取许可')
    if envelope.method not in {'caret', 'selection', 'image-selection'} or not 1 <= len(envelope.text.strip()) <= 2000:
        raise ValueError('没有可用的有限文本，请选中文本或手动粘贴')
    return envelope


class SourcePermissions:
    def __init__(self, path=None):
        self._grants = set()
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            if self._path.stat().st_size > 200_000:
                raise ValueError('source permissions file too large')
            values = json.loads(self._path.read_text(encoding='utf-8'))
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError('invalid source permissions')
            for value in values:
                if not isinstance(value, list) or len(value) != 4 or any(not isinstance(v, str) or len(v) > 2048 for v in value):
                    raise ValueError('invalid source permission')
                if value[-1] not in {'caret', 'selection', 'image-selection'}:
                    raise ValueError('invalid source method')
                self._grants.add(tuple(value))

    def _save(self):
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.source-permissions-', dir=self._path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(sorted(self._grants), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def allow(self, application, endpoint, model, method):
        if len(self._grants) >= 100:
            raise ValueError('来源许可过多，请清除后重试')
        if method not in {'caret', 'selection', 'image-selection'}:
            raise ValueError('invalid source method')
        if any(not isinstance(v, str) or len(v) > 2048 for v in (application, endpoint, model)):
            raise ValueError('invalid source permission')
        previous = set(self._grants)
        self._grants.add((application.casefold(), endpoint.rstrip('/'), model, method))
        try:
            self._save()
        except Exception:
            self._grants = previous
            raise

    def permits(self, application, endpoint, model, method):
        return (application.casefold(), endpoint.rstrip('/'), model, method) in self._grants

    def clear(self):
        self._grants.clear()
        self._save()
