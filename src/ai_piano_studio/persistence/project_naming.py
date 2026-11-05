from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import re


_INVALID_NAME_CHARACTER = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
MAX_PROJECT_DISPLAY_NAME_LENGTH = 120


@dataclass(frozen=True, slots=True)
class ProjectNameResult:
    normalized: str
    valid: bool
    error: str = ""
    suggestion: str = ""


def normalize_project_name(raw: object) -> str:
    """Return the deterministic, display-safe form of a user project name."""
    value = str(raw) if raw is not None else ""
    value = _INVALID_NAME_CHARACTER.sub("_", value)
    value = _CONTROL_CHARACTER.sub("_", value)
    value = value.strip().rstrip(" .")
    return value[:MAX_PROJECT_DISPLAY_NAME_LENGTH]


def inspect_project_name(
    raw: object,
    existing_names: Collection[str] = (),
    current_name: str | None = None,
) -> ProjectNameResult:
    normalized = normalize_project_name(raw)
    if not normalized:
        return ProjectNameResult(normalized, False, "工程名称不能为空")
    if _is_reserved_device_name(normalized):
        return ProjectNameResult(normalized, False, "工程名称不能使用系统保留名称")

    normalized_key = normalized.casefold()
    current_key = (
        normalize_project_name(current_name).casefold()
        if current_name is not None
        else None
    )
    duplicate = any(
        str(name).casefold() == normalized_key
        and str(name).casefold() != current_key
        for name in existing_names
    )
    if duplicate:
        suggestion = _next_unique_name(normalized, existing_names)
        return ProjectNameResult(
            normalized,
            False,
            "工程名称已存在",
            suggestion,
        )
    return ProjectNameResult(normalized, True)


def _is_reserved_device_name(value: str) -> bool:
    stem = value.split(".", 1)[0].casefold()
    return stem.upper() in _RESERVED_DEVICE_NAMES


def _next_unique_name(base: str, existing_names: Collection[str]) -> str:
    existing = {str(name).casefold() for name in existing_names}
    candidate = f"{base} 2"
    index = 2
    while candidate.casefold() in existing:
        index += 1
        candidate = f"{base} {index}"
    return candidate
