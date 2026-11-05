"""Ownership-only arbitration; adapters release their own voices, never Studio's."""
from __future__ import annotations

from collections.abc import Callable


class PlaybackArbiter:
    def __init__(self, studio_busy: Callable[[], bool], start: Callable, release_own: Callable[[], None]):
        self._studio_busy = studio_busy
        self._start = start
        self._release_own = release_own
        self._epoch = 0
        self.owner = ''

    def play(self, owner: str, notes: tuple) -> int | None:
        if owner not in {'preview', 'companion'} or not notes:
            raise ValueError('invalid playback request')
        if self._studio_busy():
            self.release()
            return None
        if self.owner == 'preview' and owner == 'companion':
            return None
        self.release()
        self._epoch += 1
        lease = self._epoch
        self.owner = owner
        try:
            self._start(owner, notes)
        except Exception:
            self.release()
            raise
        return lease if self.is_current(lease) else None

    def is_current(self, lease: int | None) -> bool:
        return bool(self.owner) and lease == self._epoch

    def release(self, lease: int | None = None) -> None:
        if lease is not None and not self.is_current(lease):
            return
        had_owner = bool(self.owner)
        self.owner = ''
        self._epoch += 1
        if had_owner:
            self._release_own()

    def check_studio(self) -> None:
        if self.owner and self._studio_busy():
            self.release()
