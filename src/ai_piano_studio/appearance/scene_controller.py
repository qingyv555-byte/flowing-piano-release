from PySide6.QtCore import Property, Signal, Slot

from ai_piano_studio.appearance.settings import AppearanceSettings


class SceneController(AppearanceSettings):
    """UI-only scene preference controller; intentionally independent of music data."""

    sceneIdChanged = Signal()
    dynamicEnabledChanged = Signal()

    def __init__(self, parent=None, settings=None) -> None:
        super().__init__(parent=parent, settings=settings)
        self.themeIdChanged.connect(self.sceneIdChanged)
        self.dynamicBackgroundEnabledChanged.connect(self.dynamicEnabledChanged)

    @Property(str, notify=sceneIdChanged)
    def sceneId(self) -> str:
        return self.themeId

    @Property(bool, notify=dynamicEnabledChanged)
    def dynamicEnabled(self) -> bool:
        return self.dynamicBackgroundEnabled

    @Slot(str)
    def setSceneId(self, scene_id: str) -> None:
        self.setThemeId(scene_id)

    @Slot(bool)
    def setDynamicEnabled(self, enabled: bool) -> None:
        self.setDynamicBackgroundEnabled(enabled)
