from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable


PreviewMode = str
_VALID_MODES = frozenset({"off", "single", "continuous"})
logger = logging.getLogger(__name__)


class AudioPreviewManager:
    """Applies bounded drag-preview policies without touching project notes."""

    def __init__(
        self,
        *,
        play: Callable[[int, int], int | None],
        stop: Callable[[int, str], None] | None = None,
        max_polyphony: int = 4,
    ) -> None:
        self._play = play
        self._stop = stop or (lambda _voice_id, _mode: None)
        self._max_polyphony = max(1, int(max_polyphony))
        self._mode: PreviewMode = "single"
        self._active: deque[tuple[int, int]] = deque()

    @property
    def mode(self) -> PreviewMode:
        return self._mode

    def set_mode(self, mode: PreviewMode) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"unsupported preview mode: {mode}")
        if mode != self._mode:
            self.stop_all()
            self._mode = mode

    def preview(self, pitch: int, velocity: int) -> int | None:
        if self._mode == "off":
            return None
        if self._mode == "single":
            if len(self._active) == 1 and self._active[0][0] == pitch:
                return self._active[0][1]
            self.stop_all()
        elif any(active_pitch == pitch for active_pitch, _voice_id in self._active):
            return None
        while len(self._active) >= self._max_polyphony:
            self._retire(self._active.popleft()[1])
        try:
            voice_id = self._play(pitch, velocity)
        except Exception:
            logger.exception("Preview audition failed")
            return None
        if not isinstance(voice_id, int) or voice_id < 0:
            return None
        self._active.append((pitch, voice_id))
        return voice_id

    def stop_all(self, mode: str = "safe") -> None:
        while self._active:
            self._retire(self._active.popleft()[1], mode)

    def _retire(self, voice_id: int, mode: str = "safe") -> None:
        try:
            self._stop(voice_id, mode)
        except TypeError:
            try:
                self._stop(voice_id)  # type: ignore[call-arg]
            except Exception:
                logger.exception("Preview voice release failed")
        except Exception:
            logger.exception("Preview voice release failed")
