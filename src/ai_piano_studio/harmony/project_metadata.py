from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState


_LOGGER = logging.getLogger(__name__)
HARMONY_SCHEMA_VERSION = 1
_GROUP_KEYS = frozenset(
    {
        "groupId",
        "generatedBy",
        "measureIndex",
        "startTick",
        "durationTicks",
        "chordSymbol",
        "voicingPitches",
        "generatedNoteIds",
        "originalSignatures",
        "detached",
        "applicationOrigin",
        "replacePolicy",
    }
)


def _warning(logger: logging.Logger | None, message: str, *args: object) -> None:
    (logger or _LOGGER).warning(message, *args)


def _canonical_unknown(raw: Mapping[str, object], known_keys: frozenset[str]) -> str:
    unknown = {key: value for key, value in raw.items() if key not in known_keys}
    return json.dumps(unknown, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _restore_unknown(encoded: str) -> dict[str, object]:
    restored = json.loads(encoded)
    return restored if isinstance(restored, dict) else {}


def _is_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_number(value: object, *, minimum: float, strict: bool = False) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        numeric = float(value)
    except OverflowError:
        return False
    return isfinite(numeric) and (numeric > minimum if strict else numeric >= minimum)


@dataclass(frozen=True, slots=True)
class NoteSignature:
    """The note content that proves a generated note is still unchanged."""

    pitch: int
    start_time: float
    duration: float
    velocity: int
    instrument: str

    @classmethod
    def from_note(cls, note: Note) -> NoteSignature:
        return cls(
            pitch=note.pitch,
            start_time=note.start_time,
            duration=note.duration,
            velocity=note.velocity,
            instrument=note.instrument,
        )

    @classmethod
    def _from_dict(cls, raw: object) -> NoteSignature | None:
        if not isinstance(raw, dict):
            return None
        pitch = raw.get("pitch")
        start_time = raw.get("startTime")
        duration = raw.get("duration")
        velocity = raw.get("velocity")
        instrument = raw.get("instrument")
        if not (
            _is_int(pitch)
            and pitch <= 127
            and _is_number(start_time, minimum=0.0)
            and _is_number(duration, minimum=0.0, strict=True)
            and _is_int(velocity)
            and velocity <= 127
            and isinstance(instrument, str)
            and bool(instrument.strip())
        ):
            return None
        return cls(pitch, float(start_time), float(duration), velocity, instrument)

    def to_dict(self) -> dict[str, object]:
        return {
            "pitch": self.pitch,
            "startTime": self.start_time,
            "duration": self.duration,
            "velocity": self.velocity,
            "instrument": self.instrument,
        }


@dataclass(frozen=True, slots=True)
class HarmonyGroup:
    group_id: str
    measure_index: int
    start_tick: int
    duration_ticks: int
    chord_symbol: str
    voicing_pitches: tuple[int, int, int]
    generated_note_ids: tuple[str, ...]
    original_signatures: Mapping[str, NoteSignature]
    detached: bool
    application_origin: str
    replace_policy: str
    _unknown_json: str = "{}"

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_signatures", MappingProxyType(dict(self.original_signatures)))

    def to_dict(self) -> dict[str, object]:
        result = _restore_unknown(self._unknown_json)
        result.update(
            {
                "groupId": self.group_id,
                "generatedBy": "harmony-assist",
                "measureIndex": self.measure_index,
                "startTick": self.start_tick,
                "durationTicks": self.duration_ticks,
                "chordSymbol": self.chord_symbol,
                "voicingPitches": list(self.voicing_pitches),
                "generatedNoteIds": list(self.generated_note_ids),
                "originalSignatures": {
                    note_id: signature.to_dict()
                    for note_id, signature in self.original_signatures.items()
                },
                "detached": self.detached,
                "applicationOrigin": self.application_origin,
                "replacePolicy": self.replace_policy,
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class HarmonyProjectMetadata:
    groups: tuple[HarmonyGroup, ...] = ()
    _unknown_json: str = "{}"

    @classmethod
    def empty(cls) -> HarmonyProjectMetadata:
        return cls()

    @classmethod
    def from_dict(
        cls,
        raw: object,
        state: ProjectState,
        logger: logging.Logger | None = None,
    ) -> HarmonyProjectMetadata:
        if raw is None:
            _warning(logger, "Ignoring malformed harmony metadata: expected an object, got null")
            return cls.empty()
        if not isinstance(raw, dict):
            _warning(logger, "Ignoring malformed harmony metadata: expected an object, got %s", type(raw).__name__)
            return cls.empty()
        schema_version = raw.get("schemaVersion")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != HARMONY_SCHEMA_VERSION
        ):
            _warning(
                logger,
                "Ignoring harmony metadata with unsupported schemaVersion %r",
                schema_version,
            )
            return cls.empty()

        raw_groups = raw.get("groups", [])
        if not isinstance(raw_groups, list):
            _warning(logger, "Ignoring malformed harmony groups: expected a list, got %s", type(raw_groups).__name__)
            raw_groups = []

        groups: list[HarmonyGroup] = []
        for index, item in enumerate(raw_groups):
            parsed = _group_from_dict(item, logger, index)
            if parsed is not None:
                groups.append(parsed)
        try:
            unknown_json = _canonical_unknown(raw, frozenset({"schemaVersion", "groups"}))
        except (TypeError, ValueError):
            _warning(logger, "Ignoring non-JSON unknown harmony metadata fields")
            unknown_json = "{}"
        return cls(tuple(groups), unknown_json).reconciled(state, logger)

    def to_dict(self) -> dict[str, object]:
        result = _restore_unknown(self._unknown_json)
        result["schemaVersion"] = HARMONY_SCHEMA_VERSION
        result["groups"] = [group.to_dict() for group in self.groups]
        return result

    def validated(
        self, state: ProjectState, logger: logging.Logger | None = None
    ) -> HarmonyProjectMetadata:
        """Return metadata whose note ownership and detached evidence match ``state``."""
        return self.reconciled(state, logger)

    def reconciled(
        self, state: ProjectState, logger: logging.Logger | None = None
    ) -> HarmonyProjectMetadata:
        notes_by_id = {note.id: note for note in state.active_notes}
        owned_note_ids: set[str] = set()
        groups: list[HarmonyGroup] = []
        for group in self.groups:
            generated_note_ids: list[str] = []
            signatures: dict[str, NoteSignature] = {}
            detached = group.detached
            for note_id in group.generated_note_ids:
                note = notes_by_id.get(note_id)
                if note is None:
                    _warning(logger, "Removing missing harmony note id %r from group %r", note_id, group.group_id)
                    detached = True
                    continue
                if note_id in owned_note_ids:
                    _warning(
                        logger,
                        "Removing duplicate harmony note owner %r from later group %r",
                        note_id,
                        group.group_id,
                    )
                    detached = True
                    continue
                owned_note_ids.add(note_id)
                generated_note_ids.append(note_id)
                signature = group.original_signatures.get(note_id)
                if signature is None:
                    _warning(
                        logger,
                        "Harmony group %r is detached because note %r has no valid original signature",
                        group.group_id,
                        note_id,
                    )
                    detached = True
                    continue
                signatures[note_id] = signature
                if signature != NoteSignature.from_note(note):
                    _warning(
                        logger,
                        "Harmony group %r is detached because note %r no longer matches its signature",
                        group.group_id,
                        note_id,
                    )
                    detached = True
            if not generated_note_ids:
                _warning(
                    logger,
                    "Removing harmony group %r because it has no remaining generated notes",
                    group.group_id,
                )
                continue
            groups.append(
                HarmonyGroup(
                    group_id=group.group_id,
                    measure_index=group.measure_index,
                    start_tick=group.start_tick,
                    duration_ticks=group.duration_ticks,
                    chord_symbol=group.chord_symbol,
                    voicing_pitches=group.voicing_pitches,
                    generated_note_ids=tuple(generated_note_ids),
                    original_signatures=signatures,
                    detached=detached,
                    application_origin=group.application_origin,
                    replace_policy=group.replace_policy,
                    _unknown_json=group._unknown_json,
                )
            )
        return HarmonyProjectMetadata(tuple(groups), self._unknown_json)

    @property
    def is_empty(self) -> bool:
        return not self.groups and _restore_unknown(self._unknown_json) == {}

    @property
    def generated_note_ids(self) -> frozenset[str]:
        return frozenset(
            note_id for group in self.groups for note_id in group.generated_note_ids
        )

    def groups_for_measure(self, measure_index: int) -> tuple[HarmonyGroup, ...]:
        return tuple(
            group for group in self.groups if group.measure_index == measure_index
        )

    def without_measure(self, measure_index: int) -> HarmonyProjectMetadata:
        return HarmonyProjectMetadata(
            tuple(
                group for group in self.groups if group.measure_index != measure_index
            ),
            self._unknown_json,
        )


def _group_from_dict(
    raw: object,
    logger: logging.Logger | None,
    index: int,
) -> HarmonyGroup | None:
    if not isinstance(raw, dict):
        _warning(logger, "Skipping malformed harmony group at index %d: expected an object", index)
        return None
    group_id = raw.get("groupId")
    if not isinstance(group_id, str) or not group_id.strip():
        _warning(logger, "Skipping malformed harmony group at index %d: invalid groupId", index)
        return None
    generated_by = raw.get("generatedBy")
    measure_index = raw.get("measureIndex")
    start_tick = raw.get("startTick")
    duration_ticks = raw.get("durationTicks")
    chord_symbol = raw.get("chordSymbol")
    voicing_pitches = raw.get("voicingPitches")
    generated_note_ids = raw.get("generatedNoteIds")
    detached = raw.get("detached")
    application_origin = raw.get("applicationOrigin")
    replace_policy = raw.get("replacePolicy")
    if not (
        generated_by == "harmony-assist"
        and _is_int(measure_index)
        and _is_int(start_tick)
        and _is_int(duration_ticks, minimum=1)
        and isinstance(chord_symbol, str)
        and bool(chord_symbol.strip())
        and isinstance(voicing_pitches, list)
        and len(voicing_pitches) == 3
        and all(_is_int(pitch) and pitch <= 127 for pitch in voicing_pitches)
        and isinstance(generated_note_ids, list)
        and isinstance(detached, bool)
        and application_origin in {"auto", "preview-manual", "whole-project"}
        and replace_policy in {"automatic", "explicit-only"}
    ):
        _warning(logger, "Skipping malformed harmony group %r", group_id)
        return None

    note_ids: list[str] = []
    seen_note_ids: set[str] = set()
    for note_id in generated_note_ids:
        if not isinstance(note_id, str) or not note_id.strip():
            _warning(logger, "Removing invalid generated note id from harmony group %r", group_id)
            continue
        if note_id in seen_note_ids:
            _warning(logger, "Removing duplicate generated note id %r from harmony group %r", note_id, group_id)
            continue
        seen_note_ids.add(note_id)
        note_ids.append(note_id)

    raw_signatures = raw.get("originalSignatures")
    signatures: dict[str, NoteSignature] = {}
    if not isinstance(raw_signatures, dict):
        _warning(logger, "Harmony group %r has malformed originalSignatures", group_id)
    else:
        for note_id in note_ids:
            if note_id not in raw_signatures:
                _warning(logger, "Harmony group %r has an invalid signature for note %r", group_id, note_id)
                continue
            signature = NoteSignature._from_dict(raw_signatures[note_id])
            if signature is None:
                _warning(
                    logger,
                    "Skipping malformed harmony group %r: invalid signature for note %r",
                    group_id,
                    note_id,
                )
                return None
            signatures[note_id] = signature
    try:
        unknown_json = _canonical_unknown(raw, _GROUP_KEYS)
    except (TypeError, ValueError):
        _warning(logger, "Skipping harmony group %r with non-JSON unknown fields", group_id)
        return None
    return HarmonyGroup(
        group_id=group_id,
        measure_index=measure_index,
        start_tick=start_tick,
        duration_ticks=duration_ticks,
        chord_symbol=chord_symbol,
        voicing_pitches=tuple(voicing_pitches),
        generated_note_ids=tuple(note_ids),
        original_signatures=signatures,
        detached=detached,
        application_origin=application_origin,
        replace_policy=replace_policy,
        _unknown_json=unknown_json,
    )
