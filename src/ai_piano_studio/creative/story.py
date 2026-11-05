"""Explicit story segments and user-entered timing; never automatic video analysis."""
from dataclasses import dataclass, replace
from math import isfinite
import re

from .memory import validate_scope


class StorySession:
    def __init__(self, scope):
        self.scope = validate_scope(scope)
        self.clear()

    def prepare(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 24000:
            raise ValueError('故事需为 1–24000 字')
        paragraphs = [part.strip() for part in re.split(r'\n\s*\n', text) if part.strip()]
        segments = tuple(p[i:i+2000] for p in paragraphs for i in range(0, len(p), 2000))
        if len(segments) > 12:
            raise ValueError('本次最多 12 段，请选择较小范围')
        self.segments = segments
        self.selected = None

    def select(self, index):
        if type(index) is not int or not 0 <= index < len(self.segments):
            raise ValueError('请选择有效段落')
        self.selected = index
        return self.segments[index]

    def clear(self):
        self.segments = ()
        self.selected = None


@dataclass(frozen=True)
class CuePlan:
    duration: float
    entry: float = 0
    rests: tuple[tuple[float, float], ...] = ()
    accents: tuple[float, ...] = ()

    def __post_init__(self):
        if not all(isfinite(v) for v in (self.duration, self.entry)) or not 0 < self.duration <= 120 or not 0 <= self.entry < self.duration:
            raise ValueError('总时长需为 0–120 秒，进入点必须在时长内')
        if len(self.rests) > 16 or len(self.accents) > 16:
            raise ValueError('最多 16 个留白区间和重音点')
        previous = 0.0
        for start, end in self.rests:
            if not all(isfinite(v) for v in (start, end)) or not 0 <= start < end <= self.duration or start < previous:
                raise ValueError('留白区间须按顺序填写，不能重叠或越界')
            previous = end
        if any(not isfinite(v) or not 0 <= v < self.duration for v in self.accents):
            raise ValueError('重音点必须在时长内')
        if any(start <= accent < end for start, end in self.rests for accent in self.accents):
            raise ValueError('重音点不能放在留白区间')

    def apply(self, notes):
        if len(notes) > 256:
            raise ValueError('主题音符超过预算')
        result = []
        for note in notes:
            start = note.start_time + self.entry
            if start >= self.duration or any(a <= start < b for a, b in self.rests):
                continue
            boundary = min([a for a, _ in self.rests if a > start] + [self.duration])
            # Reserve the renderer's 12ms cut tail before every silent boundary.
            end = min(start + note.duration, boundary - .012)
            if end - start < .02:
                continue
            accent = any(abs(start - point) <= .06 for point in self.accents)
            result.append(replace(note, start_time=start, duration=end-start,
                                  velocity=min(100, note.velocity + (10 if accent else 0)),
                                  release_mode='custom', release_ms=12.0, status='saved'))
        if not result:
            raise ValueError('这些时点没有留下可播放音符，请调整进入点或留白')
        return tuple(result)
