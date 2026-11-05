"""Portable logical-key preferences, separate from project content and live input."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ai_piano_studio.keyboard.layout import KEY_SEMITONES
from ai_piano_studio.persistence.atomic import atomic_write_bytes


# One logical key, no modifiers/macros. Navigation, transport, editing and system
# keys remain commands. Numpad and number row intentionally share logical identity.
AVAILABLE_KEYS = frozenset(range(ord('A'), ord('Z') + 1)) | frozenset(
    map(ord, '0123456789,.;/[]\\\'`-='))


def default_profile() -> dict:
    return {
        'id': 'default', 'name': '默认', 'pedalKey': None,
        'mappings': [{'key': key, 'pitch': 60 + offset, 'enabled': True}
                     for key, offset in KEY_SEMITONES.items()],
    }


def _integer(value, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}必须是 {low}–{high} 的整数')
    return value


def validate_profile(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('按键方案必须是对象')
    for name in ('id', 'name'):
        if not isinstance(raw.get(name), str) or not 0 < len(raw[name].strip()) <= 64:
            raise ValueError('请填写有效方案名称和标识（最多 64 字符）')
    rows = raw.get('mappings')
    if not isinstance(rows, list) or len(rows) > len(AVAILABLE_KEYS):
        raise ValueError('按键映射数量无效')
    normalized = []
    used = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get('enabled')) is not bool:
            raise ValueError('映射启用状态无效')
        key = _integer(row.get('key'), 0, 0x01FFFFFF, '按键')
        if key not in AVAILABLE_KEYS:
            raise ValueError('该键为系统/编辑命令保留，请选择字母、数字或标点')
        if key in used:
            raise ValueError(f'{chr(key)} 已被占用，请替换原映射或取消')
        used.add(key)
        pitch = _integer(row.get('pitch'), 21, 108, '钢琴音高')
        normalized.append({'key': key, 'pitch': pitch, 'enabled': row['enabled']})
    pedal = raw.get('pedalKey')
    if pedal is not None:
        pedal = _integer(pedal, 0, 0x01FFFFFF, '踏板按键')
        if pedal not in AVAILABLE_KEYS:
            raise ValueError('踏板按键为系统/编辑命令保留')
        if pedal in used:
            raise ValueError(f'{chr(pedal)} 已被音符占用，请更换踏板键')
    return {'id': raw['id'].strip(), 'name': raw['name'].strip(),
            'mappings': normalized, 'pedalKey': pedal}


def _defaults() -> dict:
    return {'version': 1, 'profiles': [default_profile()], 'activeProfile': 'default',
            'velocity': 80, 'countInBars': 0, 'metronome': 'off',
            'metronomeVolume': 0.3, 'showHints': False}


def validate_settings(raw: object) -> dict:
    if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1:
        raise ValueError('不支持的演奏设置版本')
    profiles = raw.get('profiles')
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 32:
        raise ValueError('方案数量应为 1–32')
    profiles = [validate_profile(item) for item in profiles]
    ids = [item['id'] for item in profiles]
    if len(set(ids)) != len(ids) or 'default' not in ids:
        raise ValueError('方案标识重复或缺少默认方案')
    if profiles[ids.index('default')] != default_profile():
        raise ValueError('默认方案只读，请复制为自定义后编辑')
    if raw.get('activeProfile') not in ids:
        raise ValueError('当前方案不存在')
    result = _defaults() | {key: raw[key] for key in _defaults() if key in raw}
    result['profiles'] = profiles
    _integer(result['velocity'], 1, 127, '输入力度')
    _integer(result['countInBars'], 0, 2, '预备小节')
    if result['metronome'] not in ('off', 'record', 'always'):
        raise ValueError('节拍器模式无效')
    volume = result['metronomeVolume']
    if type(volume) not in (int, float) or not 0 <= volume <= 1:
        raise ValueError('节拍器音量应为 0–1')
    if type(result['showHints']) is not bool:
        raise ValueError('键位提示状态无效')
    return result


class PlayingSettings:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.warning = ''
        self._values = _defaults()
        if path is not None and path.exists():
            try:
                if path.stat().st_size > 256_000:
                    raise ValueError('设置文件过大')
                self._values = validate_settings(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, OSError, TypeError) as exc:
                self.warning = f'演奏设置无法读取，已使用默认键位；原文件保留。{exc}'

    def snapshot(self) -> dict:
        return deepcopy(self._values)

    @property
    def profile(self) -> dict:
        return deepcopy(next(p for p in self._values['profiles']
                             if p['id'] == self._values['activeProfile']))

    def apply(self, draft: object) -> None:
        values = validate_settings(draft)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(self.path, json.dumps(values, ensure_ascii=False,
                                                    allow_nan=False, indent=2).encode('utf-8'))
        self._values = deepcopy(values)
        self.warning = ''
