"""Shared release-tail validation for notes and music-layer defaults."""

from __future__ import annotations

from math import isfinite


NOTE_RELEASE_MODES = frozenset({"natural", "custom", "cut"})
DEFAULT_RELEASE_MODE = "natural"
DEFAULT_CUSTOM_RELEASE_MS = 250.0
MAX_RELEASE_MS = 5000.0


def normalize_release(
    release_mode: str, release_ms: float | None
) -> tuple[str, float | None]:
    if not isinstance(release_mode, str) or release_mode not in NOTE_RELEASE_MODES:
        raise ValueError(f"release_mode must be one of {sorted(NOTE_RELEASE_MODES)}")
    if release_ms is not None:
        if (
            isinstance(release_ms, bool)
            or not isinstance(release_ms, (int, float))
            or not isfinite(float(release_ms))
            or release_ms < 0
            or release_ms > MAX_RELEASE_MS
        ):
            raise ValueError(
                f"release_ms must be a finite number from 0 to {MAX_RELEASE_MS:g}"
            )
    if release_mode == "custom":
        return release_mode, (
            DEFAULT_CUSTOM_RELEASE_MS if release_ms is None else float(release_ms)
        )
    return release_mode, None
