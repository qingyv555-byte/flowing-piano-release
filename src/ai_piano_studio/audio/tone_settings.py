"""Persistent Qt bridge for committed continuous piano tone changes."""

from __future__ import annotations

import logging
from math import isfinite

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot, QTimer

from ai_piano_studio.audio.tone import PianoToneCoordinator


logger = logging.getLogger(__name__)
_SETTINGS_KEY = "audio/pianoTone"


class PianoToneSettings(QObject):
    changed = Signal()
    availabilityChanged = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        settings: QSettings | None = None,
        coordinator: PianoToneCoordinator | None = None,
        availability_poll_ms: int = 250,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("AIPianoStudio", "AI Piano Studio")
        self._coordinator = coordinator
        self._value = _read_value(self._settings)
        self._available = coordinator is not None and coordinator.available_interval() is not None
        self._status_message = coordinator.status_message() if coordinator else "Legacy Piano"
        self._availability_timer = QTimer(self)
        self._availability_timer.setInterval(max(10, int(availability_poll_ms)))
        self._availability_timer.timeout.connect(self._poll_availability)
        if coordinator is not None:
            self._availability_timer.start()

    @Property(float, notify=changed)
    def value(self) -> float:
        return self._value

    @Slot()
    def shutdown(self) -> None:
        self._availability_timer.stop()
        if self._coordinator is not None:
            self._coordinator.cancel_pending_loads()

    @Property(bool, notify=availabilityChanged)
    def available(self) -> bool:
        return self._available

    @Property(str, notify=availabilityChanged)
    def statusMessage(self) -> str:
        return self._status_message

    @Slot(float)
    def setValue(self, value: float) -> None:
        try:
            normalised = _normalise_value(value)
        except ValueError as error:
            logger.error("Ignoring invalid piano tone setting %r: %s", value, error)
            return
        if self._coordinator is not None:
            selection = self._coordinator.commit(normalised)
            self._refresh_availability()
            if selection is not None:
                normalised = selection.position
        if normalised == self._value:
            return
        self._value = normalised
        try:
            self._settings.setValue(_SETTINGS_KEY, normalised)
            self._settings.sync()
        except Exception:
            logger.exception("Failed to persist piano tone setting")
        self.changed.emit()

    @Slot(int, int)
    def prefetchRange(self, lowest: int, highest: int) -> None:
        selection = self._coordinator.selection() if self._coordinator else None
        if selection is not None:
            for engine in (selection.primary, selection.secondary):
                prepare = getattr(engine, 'prefetch_pitches', None)
                if callable(prepare):
                    prepare(range(max(21, lowest), min(108, highest) + 1))

    @Slot(float, result=bool)
    def previewValue(self, value: float) -> bool:
        """Select a tone for the next audition without changing saved preferences."""
        if self._coordinator is None:
            return False
        try:
            return self._coordinator.commit(_normalise_value(value)) is not None
        except ValueError:
            return False

    @Slot()
    def restorePreview(self) -> None:
        # Voices snapshot their bank selection at start, so restoring here
        # leaves the audition sounding while subsequent project notes stay intact.
        if self._coordinator is not None:
            self._coordinator.commit(self._value)

    def _refresh_availability(self) -> None:
        assert self._coordinator is not None
        available = self._coordinator.available_interval() is not None
        status = self._coordinator.status_message()
        if available == self._available and status == self._status_message:
            return
        self._available = available
        self._status_message = status
        self.availabilityChanged.emit()

    def _poll_availability(self) -> None:
        try:
            self._refresh_availability()
        except Exception:
            logger.exception("Failed to refresh piano tone availability")


def _read_value(settings: QSettings) -> float:
    try:
        return _normalise_value(settings.value(_SETTINGS_KEY, 0.5))
    except Exception as error:
        logger.error("Invalid persisted piano tone setting; using 0.5: %s", error)
        return 0.5


def _normalise_value(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("piano tone must be a finite number")
    try:
        normalised = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("piano tone must be a finite number") from error
    if not isfinite(normalised):
        raise ValueError("piano tone must be finite")
    return min(1.0, max(0.0, normalised))
