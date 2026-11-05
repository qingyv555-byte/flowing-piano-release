from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot

DEFAULT_AUTOSAVE_ENABLED = False
DEFAULT_AUTOSAVE_DELAY_SECONDS = 60
VALID_AUTOSAVE_DELAYS_SECONDS = frozenset({15, 30, 60, 120, 300})


class SavePreferences(QObject):
    changed = Signal()

    def __init__(self, parent=None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = (
            settings
            if settings is not None
            else QSettings("AIPianoStudio", "AI Piano Studio")
        )
        self._autosave_enabled = self._settings.value(
            "save/autosaveEnabled", DEFAULT_AUTOSAVE_ENABLED, type=bool
        )
        stored_delay = self._settings.value(
            "save/autosaveDelaySeconds", DEFAULT_AUTOSAVE_DELAY_SECONDS
        )
        self._autosave_delay_seconds = self._validated_delay(stored_delay)
        stored_delay_is_valid = self._is_valid_delay(stored_delay)
        if not stored_delay_is_valid:
            self._settings.setValue(
                "save/autosaveDelaySeconds", self._autosave_delay_seconds
            )

    @Property(bool, notify=changed)
    def autosaveEnabled(self) -> bool:
        return self._autosave_enabled

    @Property(int, notify=changed)
    def autosaveDelaySeconds(self) -> int:
        return self._autosave_delay_seconds

    @Slot(bool)
    def setAutosaveEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._autosave_enabled:
            return
        self._autosave_enabled = value
        self._settings.setValue("save/autosaveEnabled", value)
        self.changed.emit()

    @Slot(int)
    def setAutosaveDelaySeconds(self, seconds: int) -> None:
        value = self._validated_delay(seconds)
        if value == self._autosave_delay_seconds:
            return
        self._autosave_delay_seconds = value
        self._settings.setValue("save/autosaveDelaySeconds", value)
        self.changed.emit()

    @staticmethod
    def _validated_delay(value: object) -> int:
        if not SavePreferences._is_valid_delay(value):
            return DEFAULT_AUTOSAVE_DELAY_SECONDS
        return int(value)

    @staticmethod
    def _is_valid_delay(value: object) -> bool:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return False
        if not isinstance(value, str) and value != parsed:
            return False
        return parsed in VALID_AUTOSAVE_DELAYS_SECONDS
