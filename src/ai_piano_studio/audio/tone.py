"""Qt-free continuous selection of the validated Sample Piano banks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from math import cos, isfinite, pi, sin
from pathlib import Path
from threading import RLock
from typing import Callable

from ai_piano_studio.audio.sample_piano import SamplePianoEngine


logger = logging.getLogger(__name__)

EngineFactory = Callable[[Path], SamplePianoEngine]


def _create_tone_engine(manifest_path: Path) -> SamplePianoEngine:
    return SamplePianoEngine(manifest_path, preload_c3_c5_on_init=False)


@dataclass(frozen=True, slots=True)
class PianoToneSelection:
    position: float
    primary: SamplePianoEngine
    primary_gain: float
    secondary: SamplePianoEngine | None
    secondary_gain: float
    primary_bank_id: str = ""
    secondary_bank_id: str | None = None

    @property
    def bank_ids(self) -> tuple[str, ...]:
        """The selected banks, in gain order, for the audio integration layer."""
        ids = (self.primary_bank_id,)
        return ids if self.secondary is None else ids + (self.secondary_bank_id or "",)

    @property
    def gains(self) -> tuple[float, ...]:
        """The non-zero gains aligned with :attr:`bank_ids`."""
        return (
            (self.primary_gain,)
            if self.secondary is None
            else (self.primary_gain, self.secondary_gain)
        )


@dataclass(frozen=True, slots=True)
class _Bank:
    bank_id: str
    position: float
    manifest_path: Path
    engine: SamplePianoEngine | None


class PianoToneCoordinator:
    """Build immutable tone choices without disk I/O on the selection path."""

    def __init__(
        self,
        catalog_path: Path | str,
        *,
        engine_factory: EngineFactory = _create_tone_engine,
    ) -> None:
        self._lock = RLock()
        self._selection: PianoToneSelection | None = None
        self._banks: tuple[_Bank, ...] = ()
        self._status_message = "Legacy Piano"
        self._unavailable_ids: set[str] = set()
        try:
            catalog = _read_catalog(Path(catalog_path))
            _validate_catalog_contract(catalog)
            banks: list[_Bank] = []
            for bank_id, position, manifest_path in catalog:
                try:
                    engine = engine_factory(manifest_path)
                    if engine.legacy_selected:
                        raise RuntimeError(engine.failure_reason or "engine unavailable")
                    banks.append(_Bank(bank_id, position, manifest_path, engine))
                except Exception as error:
                    banks.append(_Bank(bank_id, position, manifest_path, None))
                    self._log_bank_failure_once(bank_id, error)
            self._banks = tuple(banks)
            self._status_message = (
                "Sample Piano" if self._available_interval_locked() is not None else "Legacy Piano"
            )
        except Exception as error:
            logger.error("Piano tone catalog unavailable; using Legacy Piano: %s", error)

    def selection(self) -> PianoToneSelection | None:
        """Return the latest frozen selection; this performs no I/O or decoding."""
        with self._lock:
            return self._selection

    def cancel_pending_loads(self) -> None:
        """Retire queued generations without invalidating audible voice buffers."""
        with self._lock:
            for bank in self._banks:
                if bank.engine is not None:
                    bank.engine.discard_idle_cache()

    def commit(self, position: float) -> PianoToneSelection | None:
        """Commit a clamped equal-power selection without loading sample data."""
        try:
            requested = _normalise_position(position)
        except ValueError:
            return self.selection()
        self._refresh_availability()
        with self._lock:
            interval = self._available_interval_locked()
            if interval is None:
                return self._selection
            requested = min(max(requested, interval[0]), interval[1])
            selection = self._make_selection(requested)
            if selection is None:
                return self._selection
            if selection.primary.legacy_selected or (
                selection.secondary is not None and selection.secondary.legacy_selected
            ):
                return self._selection
            self._selection = selection
            self._warm_selection_locked(selection)
            self._discard_inactive_cache_locked(selection)
            return selection

    def available_interval(self) -> tuple[float, float] | None:
        self._refresh_availability()
        with self._lock:
            return self._available_interval_locked()

    def status_message(self) -> str:
        self._refresh_availability()
        with self._lock:
            return self._status_message

    def _make_selection(self, position: float) -> PianoToneSelection | None:
        if len(self._banks) == 1:
            return self._single_bank_selection(self._banks[0], position)
        for index, bank in enumerate(self._banks):
            if position == bank.position:
                return self._single_bank_selection(bank, position)
            if position < bank.position:
                left = self._banks[index - 1]
                if left.engine is None or bank.engine is None:
                    return None
                span = bank.position - left.position
                blend = (position - left.position) / span
                left_gain = cos(blend * pi / 2)
                right_gain = sin(blend * pi / 2)
                return PianoToneSelection(
                    position,
                    left.engine,
                    left_gain,
                    bank.engine,
                    right_gain,
                    left.bank_id,
                    bank.bank_id,
                )
        return self._single_bank_selection(self._banks[-1], position)

    @staticmethod
    def _single_bank_selection(bank: _Bank, position: float) -> PianoToneSelection | None:
        if bank.engine is None:
            return None
        return PianoToneSelection(position, bank.engine, 1.0, None, 0.0, bank.bank_id)

    def _available_interval_locked(self) -> tuple[float, float] | None:
        positions = tuple(bank.position for bank in self._banks if bank.engine is not None)
        return None if not positions else (positions[0], positions[-1])

    def _refresh_availability(self) -> None:
        failures: list[tuple[str, Exception]] = []
        with self._lock:
            refreshed: list[_Bank] = []
            for bank in self._banks:
                if bank.engine is not None and bank.engine.legacy_selected:
                    error = RuntimeError(bank.engine.failure_reason or "engine unavailable")
                    if bank.bank_id not in self._unavailable_ids:
                        self._unavailable_ids.add(bank.bank_id)
                        failures.append((bank.bank_id, error))
                    refreshed.append(_Bank(bank.bank_id, bank.position, bank.manifest_path, None))
                else:
                    refreshed.append(bank)
            self._banks = tuple(refreshed)
            if self._selection is not None and any(
                bank_id in self._unavailable_ids for bank_id in self._selection.bank_ids
            ):
                self._selection = self._repair_selection_locked(self._selection.position)
            self._status_message = (
                "Sample Piano" if self._available_interval_locked() is not None else "Legacy Piano"
            )
        for bank_id, error in failures:
            logger.error(
                "Piano tone bank %s unavailable; excluding it: %s", bank_id, error
            )

    def _log_bank_failure_once(self, bank_id: str, error: Exception) -> None:
        if bank_id in self._unavailable_ids:
            return
        self._unavailable_ids.add(bank_id)
        logger.error(
            "Piano tone bank %s unavailable; excluding it: %s", bank_id, error
        )

    def _repair_selection_locked(self, position: float) -> PianoToneSelection | None:
        available = [bank for bank in self._banks if bank.engine is not None]
        if not available:
            return None
        nearest = min(available, key=lambda bank: abs(bank.position - position))
        selection = self._single_bank_selection(nearest, position)
        if selection is not None:
            self._warm_selection_locked(selection)
            self._discard_inactive_cache_locked(selection)
        return selection

    def _warm_selection_locked(self, selection: PianoToneSelection) -> None:
        selection.primary.activate_cache()
        selection.primary.preload_c3_c5_async()
        self._warm_remaining(selection.primary)
        if selection.secondary is not None:
            selection.secondary.activate_cache()
            selection.secondary.preload_c3_c5_async()
            self._warm_remaining(selection.secondary)

    @staticmethod
    def _warm_remaining(engine) -> None:
        warm = getattr(engine, "preload_remaining_async", None)
        if callable(warm):
            warm()

    def _discard_inactive_cache_locked(self, selection: PianoToneSelection) -> None:
        active_ids = set(selection.bank_ids)
        for bank in self._banks:
            if bank.engine is not None and bank.bank_id not in active_ids:
                bank.engine.discard_idle_cache()


def _read_catalog(path: Path) -> tuple[tuple[str, float, Path], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("banks"), list):
        raise ValueError("catalog must contain a banks list")
    catalog: list[tuple[str, float, Path]] = []
    ids: set[str] = set()
    positions: set[float] = set()
    previous_position = -1.0
    for item in payload["banks"]:
        if not isinstance(item, dict):
            raise ValueError("catalog banks must be objects")
        bank_id = item.get("id")
        if not isinstance(bank_id, str) or not bank_id:
            raise ValueError("catalog bank id is required")
        if bank_id in ids:
            raise ValueError(f"duplicate catalog bank id {bank_id}")
        position = _catalog_position(item.get("position"))
        if position in positions:
            raise ValueError(f"duplicate catalog bank position {position}")
        if position <= previous_position:
            raise ValueError("catalog bank positions must be strictly increasing")
        manifest = item.get("manifest")
        if not isinstance(manifest, str) or not manifest:
            raise ValueError(f"catalog bank {bank_id} manifest is required")
        manifest_path = path.parent / manifest
        if not manifest_path.is_file():
            raise ValueError(f"catalog bank {bank_id} manifest is missing")
        ids.add(bank_id)
        positions.add(position)
        previous_position = position
        catalog.append((bank_id, position, manifest_path))
    if not catalog:
        raise ValueError("catalog has no banks")
    return tuple(catalog)


def _validate_catalog_contract(catalog: tuple[tuple[str, float, Path], ...]) -> None:
    reference: tuple[int, int, tuple[int, ...], tuple[int, int]] | None = None
    for bank_id, _position, manifest_path in catalog:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest root must be an object")
            sample_rate = _positive_int(payload.get("sample_rate"), "sample_rate")
            channels = _positive_int(payload.get("channels"), "channels")
            roots = payload.get("roots")
            crop = payload.get("crop")
            if not isinstance(crop, dict):
                raise ValueError("crop contract is missing")
            source_start = _positive_or_zero_int(crop.get("source_start_frame"), "source_start_frame")
            tail_fade = _positive_or_zero_int(crop.get("tail_fade_ms"), "tail_fade_ms")
            if not isinstance(roots, list) or not roots:
                raise ValueError("roots must be a non-empty list")
            root_map = tuple(_positive_int(root.get("root_midi"), "root_midi") for root in roots if isinstance(root, dict))
            if len(root_map) != len(roots) or len(set(root_map)) != len(root_map):
                raise ValueError("roots must have unique root_midi values")
            contract = sample_rate, channels, root_map, (source_start, tail_fade)
            if reference is None:
                reference = contract
            elif contract != reference:
                raise ValueError("root map, sample rate, or channel contract differs")
        except Exception as error:
            raise ValueError(f"catalog bank {bank_id} manifest is incompatible: {error}") from error


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_or_zero_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _normalise_position(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("position must be a finite number")
    position = float(value)
    if not isfinite(position):
        raise ValueError("position must be finite")
    return min(1.0, max(0.0, position))


def _catalog_position(value: object) -> float:
    position = _normalise_position(value)
    if position != value:
        raise ValueError("catalog position must be within [0, 1]")
    return position
