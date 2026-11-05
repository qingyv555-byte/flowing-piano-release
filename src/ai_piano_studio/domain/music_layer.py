from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Any, Mapping
from uuid import uuid4

from ai_piano_studio.domain.release import DEFAULT_RELEASE_MODE, normalize_release


DEFAULT_LAYER_ID = "layer-default"
UNCLASSIFIED_LAYER_ID = "layer-unclassified"
MUSIC_LAYER_COLOR_ROLES = frozenset({
    "melody", "accompaniment", "bass", "drums", "other",
})
MUSIC_LAYER_ROLES = MUSIC_LAYER_COLOR_ROLES | {"custom"}
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def visual_role_for_layer(layer: "MusicLayer", layers: tuple["MusicLayer", ...]) -> str:
    """Presentation-only role for recognizable MIDI tracks in legacy projects.

    Never changes MusicLayer.role, color_role, note identity or saved project
    data. Explicit authored semantics take priority over inferred track names.
    Unclear names stay 'other' instead of guessing a melody from pitch/order.
    """
    if layer.role in MUSIC_LAYER_COLOR_ROLES and layer.role != "other":
        return layer.role
    if layer.role == "custom":
        return "other"
    if layer.color_role in MUSIC_LAYER_COLOR_ROLES and layer.color_role != "other":
        return layer.color_role

    def track_kind(name: str) -> str:
        name = name.strip().casefold()
        if (re.search(r"(?:^|[^a-z])(?:melody|vocal|lead)(?:$|[^a-z])", name)
                or "主旋律" in name or "人声旋律" in name):
            return "melody"
        if (re.search(r"(?:^|[^a-z])(?:left|lh|bass)(?:$|[^a-z])", name)
                or "左手" in name or "低音" in name):
            return "bass"
        if (re.search(r"(?:^|[^a-z])(?:right|rh)(?:$|[^a-z])", name)
                or "右手" in name):
            return "right"
        if (re.search(r"(?:^|[^a-z])(?:accomp|accompaniment|chord|harmony)(?:$|[^a-z])", name)
                or "伴奏" in name or "和弦" in name):
            return "accompaniment"
        return "other"

    kind = track_kind(layer.name)
    if kind == "right":
        # A dedicated vocal/melody track means 'right' is a separate piano
        # part. Without one, the right-hand track normally carries the tune.
        has_melody = any(other.id != layer.id and
                         (other.role == "melody" or other.color_role == "melody" or
                          track_kind(other.name) == "melody") for other in layers)
        return "accompaniment" if has_melody else "melody"
    return kind


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return value.strip()


def _integer(value: object, field_name: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{field_name} must be an integer from {minimum} to {maximum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class MusicLayer:
    id: str
    name: str
    role: str = "other"
    color_role: str = "other"
    custom_color: str | None = None
    instrument: str = "piano"
    volume: float = 1.0
    velocity_offset: int = 0
    transpose: int = 0
    time_offset: float = 0.0
    muted: bool = False
    solo: bool = False
    visible: bool = True
    locked: bool = False
    pinned: bool = False
    default_release_mode: str = DEFAULT_RELEASE_MODE
    default_release_ms: float | None = None
    order: int = 0

    @classmethod
    def default(cls) -> MusicLayer:
        return cls.create(id=DEFAULT_LAYER_ID, name="默认音层", order=0)

    @classmethod
    def unclassified(cls, *, order: int = 0) -> MusicLayer:
        return cls.create(
            id=UNCLASSIFIED_LAYER_ID,
            name="未分类",
            role="other",
            color_role="other",
            order=order,
        )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        id: str | None = None,
        role: str = "other",
        color_role: str | None = None,
        custom_color: str | None = None,
        instrument: str = "piano",
        volume: float = 1.0,
        velocity_offset: int = 0,
        transpose: int = 0,
        time_offset: float = 0.0,
        muted: bool = False,
        solo: bool = False,
        visible: bool = True,
        locked: bool = False,
        pinned: bool = False,
        default_release_mode: str = DEFAULT_RELEASE_MODE,
        default_release_ms: float | None = None,
        order: int = 0,
    ) -> MusicLayer:
        layer_id = str(uuid4()) if id is None else _text(id, "id")
        layer_name = _text(name, "name")
        if role not in MUSIC_LAYER_ROLES:
            raise ValueError(f"role must be one of {sorted(MUSIC_LAYER_ROLES)}")
        resolved_color_role = (
            "other" if role == "custom" else role
        ) if color_role is None else color_role
        if resolved_color_role not in MUSIC_LAYER_COLOR_ROLES:
            raise ValueError(
                f"color_role must be one of {sorted(MUSIC_LAYER_COLOR_ROLES)}"
            )
        if custom_color is not None and not _HEX_COLOR.fullmatch(custom_color):
            raise ValueError("custom_color must be #RRGGBB or None")
        layer_instrument = _text(instrument, "instrument")
        if (
            isinstance(volume, bool)
            or not isinstance(volume, (int, float))
            or not isfinite(float(volume))
            or not 0.0 <= float(volume) <= 1.0
        ):
            raise ValueError("volume must be a finite number from 0 to 1")
        velocity = _integer(velocity_offset, "velocity_offset", -127, 127)
        semitones = _integer(transpose, "transpose", -48, 48)
        if (
            isinstance(time_offset, bool)
            or not isinstance(time_offset, (int, float))
            or not isfinite(float(time_offset))
        ):
            raise ValueError("time_offset must be a finite number")
        for field_name, value in (
            ("muted", muted), ("solo", solo), ("visible", visible),
            ("locked", locked), ("pinned", pinned),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{field_name} must be a boolean")
        release_mode, release_ms = normalize_release(
            default_release_mode, default_release_ms
        )
        layer_order = _integer(order, "order", 0, 1_000_000)
        return cls(
            id=layer_id,
            name=layer_name,
            role=role,
            color_role=resolved_color_role,
            custom_color=custom_color,
            instrument=layer_instrument,
            volume=float(volume),
            velocity_offset=velocity,
            transpose=semitones,
            time_offset=float(time_offset),
            muted=muted,
            solo=solo,
            visible=visible,
            locked=locked,
            pinned=pinned,
            default_release_mode=release_mode,
            default_release_ms=release_ms,
            order=layer_order,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MusicLayer:
        return cls.create(
            id=data["id"],
            name=data["name"],
            role=data.get("role", "other"),
            color_role=data.get("colorRole"),
            custom_color=data.get("customColor"),
            instrument=data.get("instrument", "piano"),
            volume=data.get("volume", 1.0),
            velocity_offset=data.get("velocityOffset", 0),
            transpose=data.get("transpose", 0),
            time_offset=data.get("timeOffset", 0.0),
            muted=data.get("muted", False),
            solo=data.get("solo", False),
            visible=data.get("visible", True),
            locked=data.get("locked", False),
            pinned=data.get("pinned", False),
            default_release_mode=data.get("defaultReleaseMode", DEFAULT_RELEASE_MODE),
            default_release_ms=data.get("defaultReleaseMs"),
            order=data.get("order", 0),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "colorRole": self.color_role,
            "customColor": self.custom_color,
            "instrument": self.instrument,
            "volume": self.volume,
            "velocityOffset": self.velocity_offset,
            "transpose": self.transpose,
            "timeOffset": self.time_offset,
            "muted": self.muted,
            "solo": self.solo,
            "visible": self.visible,
            "locked": self.locked,
            "pinned": self.pinned,
            "defaultReleaseMode": self.default_release_mode,
            "defaultReleaseMs": self.default_release_ms,
            "order": self.order,
        }

    def with_changes(self, **changes: Any) -> MusicLayer:
        values = {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "color_role": self.color_role,
            "custom_color": self.custom_color,
            "instrument": self.instrument,
            "volume": self.volume,
            "velocity_offset": self.velocity_offset,
            "transpose": self.transpose,
            "time_offset": self.time_offset,
            "muted": self.muted,
            "solo": self.solo,
            "visible": self.visible,
            "locked": self.locked,
            "pinned": self.pinned,
            "default_release_mode": self.default_release_mode,
            "default_release_ms": self.default_release_ms,
            "order": self.order,
        }
        values.update(changes)
        return MusicLayer.create(**values)
