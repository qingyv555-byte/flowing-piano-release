"""Bounded local phrase scheduling. No text capture, network or audio device access."""
from __future__ import annotations

from dataclasses import replace
from math import ceil, isfinite

from ai_piano_studio.domain.note import Note
from .audio_lease import PlaybackArbiter


class CompanionScheduler:
    """Tick from a GUI timer; a collector may submit only aggregate activity.

    Note times are seconds, matching detached playback. Missed phrases are
    dropped, never queued. The owning integration must pause on session loss.
    """

    def __init__(self, arbiter: PlaybackArbiter):
        self._arbiter = arbiter
        self.state = 'off'
        self._notes: tuple[Note, ...] = ()
        self._lease: int | None = None
        self._activity = 0.0
        self._level = 0.0
        self._phrase_peak = 0.0
        self._next_phrase = 0.0
        self._last_tick = 0.0
        self._phrase_seconds = 4.0

    def start(self, notes: tuple[Note, ...], *, now: float) -> None:
        if not isfinite(now) or not 1 <= len(notes) <= 256:
            raise ValueError('select a bounded theme')
        if any(not isinstance(n, Note) or n.status != 'saved' for n in notes):
            raise ValueError('theme must contain saved notes')
        end = max(n.start_time + n.duration for n in notes)
        if not 0 < end <= 60:
            raise ValueError('theme must be at most 60 seconds')
        self.pause()
        self._notes = tuple(sorted(notes, key=lambda n: (n.start_time, n.pitch)))
        self._phrase_seconds = max(4.0, ceil(end))
        self._last_tick = self._next_phrase = now
        self.state = 'running'

    def activity(self, *, movement: float = 0, wheel: float = 0) -> None:
        if self.state not in {'running', 'yielding'}:
            return
        if not all(isfinite(v) and v >= 0 for v in (movement, wheel)):
            raise ValueError('activity must be finite nonnegative magnitudes')
        self._activity = min(1.0, self._activity + movement / 500 + wheel / 20)

    def tick(self, now: float) -> None:
        if self.state not in {'running', 'yielding'}:
            return
        if not isfinite(now) or now < self._last_tick:
            raise ValueError('clock must be monotonic')
        elapsed = now - self._last_tick
        self._arbiter.check_studio()
        if self._lease is not None and not self._arbiter.is_current(self._lease):
            self._lease = None
            self.state = 'yielding'
        # Immediate activity enters slowly; decay also takes several samples.
        if elapsed >= .25 or now >= self._next_phrase:
            weight = min(1.0, max(.25, elapsed / 1.5))
            self._level += (self._activity - self._level) * weight
            self._phrase_peak = max(self._phrase_peak, self._level)
            self._activity = 0.0
            self._last_tick = now
        if now < self._next_phrase:
            return
        stride = 2 if self._phrase_peak >= .1 else 1
        self._phrase_peak = 0.0
        notes = tuple(replace(n, velocity=min(n.velocity, 48 if stride == 2 else 64))
                      for i, n in enumerate(self._notes) if i % stride == 0)
        self._lease = self._arbiter.play('companion', notes)
        self.state = 'running' if self._lease is not None else 'yielding'
        self._next_phrase = now + self._phrase_seconds

    def pause(self) -> None:
        if self._lease is not None:
            self._arbiter.release(self._lease)
        self._lease = None
        self._activity = self._level = 0.0
        self._phrase_peak = 0.0
        self.state = 'paused'

    def close(self) -> None:
        self.pause()
        self._notes = ()
        self.state = 'off'
