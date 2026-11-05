from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot


logger = logging.getLogger(__name__)

VALID_HARMONY_MODES = frozenset({"off", "preview", "auto"})


class HarmonySettings(QObject):
    changed = Signal()

    def __init__(self, parent=None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("AIPianoStudio", "AI Piano Studio")
        stored_mode = self._settings.value("harmony/mode", "off")
        self._mode = self._validated_mode(stored_mode)
        if self._mode != stored_mode:
            self._settings.setValue("harmony/mode", self._mode)

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Slot(str)
    def setMode(self, mode: str) -> None:
        value = self._validated_mode(mode)
        if not self._is_valid_mode(mode):
            self._settings.setValue("harmony/mode", value)
        if value != self._mode:
            self._mode = value
            self._settings.setValue("harmony/mode", value)
            self.changed.emit()

    @staticmethod
    def _validated_mode(mode: object) -> str:
        if HarmonySettings._is_valid_mode(mode):
            return mode
        logger.warning("Invalid harmony mode %r; falling back to 'off'", mode)
        return "off"

    @staticmethod
    def _is_valid_mode(mode: object) -> bool:
        return isinstance(mode, str) and mode in VALID_HARMONY_MODES
