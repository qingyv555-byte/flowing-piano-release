from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot


DEFAULT_MINI_MAP_WIDTH = 280.0
DEFAULT_MINI_MAP_HEIGHT = 160.0
MINI_MAP_MIN_WIDTH = 160.0
MINI_MAP_MAX_WIDTH = 400.0
MINI_MAP_MIN_HEIGHT = 100.0
MINI_MAP_MAX_HEIGHT = 300.0

SIDEBAR_MODES = {"expanded", "icon"}
INSPECTOR_PANEL_MODES = {"expanded", "collapsed"}
MINI_MAP_DISPLAY_MODES = {"visible", "weak", "hidden"}
COMPOSITE_DISPLAY_MODES = {"original", "smart"}
NOTE_COLOR_MODES = {"layer", "pitch", "mono"}
LAYER_VIEW_MODES = {"ghost", "active", "all"}
NOTE_EMPHASIS_MODES = {"balanced", "melody", "arrangement"}
DEFAULT_ARRANGEMENT_HEIGHT = 240.0
MIN_ARRANGEMENT_HEIGHT = 120.0
MAX_ARRANGEMENT_HEIGHT = 360.0
_SELECTION_ACTIONS_VISIBILITY_VERSION = 1


class ExperienceSettings(QObject):
    changed = Signal()

    def __init__(self, parent=None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("AIPianoStudio", "AI Piano Studio")
        self._first_run = self._settings.value("experience/firstRunCompleted", False, type=bool)
        self._intro = self._settings.value("experience/introAnimation", True, type=bool)
        # The product now has one professional editor experience.  Keep the
        # setter for compatibility with older tests and integrations, but
        # migrate every persisted launch state to the professional default.
        self._mode = "advanced"
        if self._settings.value("experience/mode", "advanced") != "advanced":
            self._settings.setValue("experience/mode", "advanced")
        # A prior build persisted the selection action rail as collapsed. That
        # made audition/duplicate/quantize controls appear to vanish whenever
        # notes were selected. Migrate that one stale preference once while
        # keeping an intentional collapse made in the current build intact.
        try:
            selection_actions_version = int(
                self._settings.value("experience/selectionActionsVisibilityVersion", 0)
            )
        except (TypeError, ValueError):
            selection_actions_version = 0
        selection_actions_key = "experience/inspectorSections/selectionActions"
        if selection_actions_version < _SELECTION_ACTIONS_VISIBILITY_VERSION:
            if self._settings.contains(selection_actions_key) and not self._coerce_bool(
                self._settings.value(selection_actions_key), True
            ):
                self._settings.setValue(selection_actions_key, True)
            self._settings.setValue(
                "experience/selectionActionsVisibilityVersion",
                _SELECTION_ACTIONS_VISIBILITY_VERSION,
            )
        self._editor_guide_completed = self._settings.value(
            "experience/editorGuideCompleted", False, type=bool
        )
        raw_tip_keys = self._settings.value("experience/dismissedTips", [])
        if isinstance(raw_tip_keys, str):
            raw_tip_keys = [raw_tip_keys]
        if not isinstance(raw_tip_keys, (list, tuple, set)):
            raw_tip_keys = []
        self._dismissed_tip_keys = {
            key for value in raw_tip_keys if (key := self._tip_key(value))
        }
        stored_preview = self._settings.value("experience/dragPreviewMode", "grid")
        if stored_preview in {"single", "release"}:
            stored_preview = "grid"
            self._settings.setValue("experience/dragPreviewMode", stored_preview)
        self._drag_preview_mode = stored_preview if stored_preview in {"off", "grid", "continuous"} else "grid"
        self._new_note_length_beats = self._bounded_float(
            self._settings.value("experience/newNoteLengthBeats", 1.0), 1.0
        )
        self._piano_preview_length_beats = self._bounded_float(
            self._settings.value("experience/pianoPreviewLengthBeats", 1.0), 1.0
        )
        self._new_note_velocity = self._bounded_int(
            self._settings.value("experience/newNoteVelocity", 75), 75
        )
        stored_sidebar_mode = str(
            self._settings.value("experience/sidebarMode", "expanded")
        )
        # The former narrow rail was an intermediate width that reduced the
        # canvas without offering a useful compact surface. Migrate saved
        # values to the real icon-only state so it cannot reappear.
        if stored_sidebar_mode == "narrow":
            stored_sidebar_mode = "icon"
            self._settings.setValue("experience/sidebarMode", stored_sidebar_mode)
        self._sidebar_mode = (
            stored_sidebar_mode if stored_sidebar_mode in SIDEBAR_MODES else "expanded"
        )
        stored_inspector_mode = str(
            self._settings.value("experience/inspectorPanelMode", "expanded")
        )
        # The former narrow mode only showed a sparse summary and consumed
        # canvas space. Migrate it to the real collapsed state on first read.
        if stored_inspector_mode == "narrow":
            stored_inspector_mode = "collapsed"
            self._settings.setValue("experience/inspectorPanelMode", stored_inspector_mode)
        self._inspector_panel_mode = (
            stored_inspector_mode
            if stored_inspector_mode in INSPECTOR_PANEL_MODES
            else "expanded"
        )
        self._focus_mode = self._settings.value(
            "experience/focusMode", False, type=bool
        )
        self._arrangement_visible = self._settings.value(
            "experience/arrangementVisible", False, type=bool
        )
        self._arrangement_height = self._bounded_range(
            self._settings.value(
                "experience/arrangementHeight", DEFAULT_ARRANGEMENT_HEIGHT
            ),
            DEFAULT_ARRANGEMENT_HEIGHT,
            MIN_ARRANGEMENT_HEIGHT,
            MAX_ARRANGEMENT_HEIGHT,
        )
        stored_note_color_mode = str(
            self._settings.value("experience/noteColorMode", "layer")
        )
        self._note_color_mode = (
            stored_note_color_mode
            if stored_note_color_mode in NOTE_COLOR_MODES
            else "layer"
        )
        saved_emphasis = str(
            self._settings.value("experience/noteEmphasisMode", "balanced")
        )
        self._note_emphasis_mode = (
            saved_emphasis if saved_emphasis in NOTE_EMPHASIS_MODES else "balanced"
        )
        stored_layer_view_mode = str(
            self._settings.value("experience/layerViewMode", "ghost")
        )
        self._layer_view_mode = (
            stored_layer_view_mode
            if stored_layer_view_mode in LAYER_VIEW_MODES
            else "ghost"
        )
        stored_mini_map_mode = self._settings.value("experience/miniMapDisplayMode", None)
        self._mini_map_visible = self._settings.value(
            "experience/miniMapVisible", True, type=bool
        )
        if stored_mini_map_mode in MINI_MAP_DISPLAY_MODES:
            self._mini_map_display_mode = stored_mini_map_mode
        else:
            self._mini_map_display_mode = "visible" if self._mini_map_visible else "hidden"
        self._mini_map_width = self._bounded_range(
            self._settings.value("experience/miniMapWidth", DEFAULT_MINI_MAP_WIDTH),
            DEFAULT_MINI_MAP_WIDTH,
            MINI_MAP_MIN_WIDTH,
            MINI_MAP_MAX_WIDTH,
        )
        self._mini_map_height = self._bounded_range(
            self._settings.value("experience/miniMapHeight", DEFAULT_MINI_MAP_HEIGHT),
            DEFAULT_MINI_MAP_HEIGHT,
            MINI_MAP_MIN_HEIGHT,
            MINI_MAP_MAX_HEIGHT,
        )
        self._mini_map_aspect_locked = self._settings.value(
            "experience/miniMapAspectLocked", False, type=bool
        )
        self._mini_map_x = self._bounded_coordinate(
            self._settings.value("experience/miniMapX", -1.0), -1.0
        )
        self._mini_map_y = self._bounded_coordinate(
            self._settings.value("experience/miniMapY", -1.0), -1.0
        )
        stored_composite_mode = str(
            self._settings.value("experience/compositeDisplayMode", "original")
        )
        self._composite_display_mode = (
            stored_composite_mode
            if stored_composite_mode in COMPOSITE_DISPLAY_MODES
            else "original"
        )

    @Property(bool, notify=changed)
    def firstRunCompleted(self) -> bool: return self._first_run
    @Property(bool, notify=changed)
    def introAnimation(self) -> bool: return self._intro
    @Property(str, notify=changed)
    def mode(self) -> str: return self._mode
    @Property(bool, notify=changed)
    def editorGuideCompleted(self) -> bool: return self._editor_guide_completed
    @Property(str, notify=changed)
    def dragPreviewMode(self) -> str: return self._drag_preview_mode
    @Property(float, notify=changed)
    def newNoteLengthBeats(self) -> float: return self._new_note_length_beats
    @Property(float, notify=changed)
    def pianoPreviewLengthBeats(self) -> float: return self._piano_preview_length_beats
    @Property(int, notify=changed)
    def newNoteVelocity(self) -> int: return self._new_note_velocity
    @Property(str, notify=changed)
    def sidebarMode(self) -> str: return self._sidebar_mode
    @Property(str, notify=changed)
    def inspectorPanelMode(self) -> str: return self._inspector_panel_mode
    @Property(bool, notify=changed)
    def focusMode(self) -> bool: return self._focus_mode
    @Property(bool, notify=changed)
    def arrangementVisible(self) -> bool: return self._arrangement_visible
    @Property(float, notify=changed)
    def arrangementHeight(self) -> float: return self._arrangement_height
    @Property(str, notify=changed)
    def noteColorMode(self) -> str: return self._note_color_mode
    @Property(str, notify=changed)
    def noteEmphasisMode(self) -> str: return self._note_emphasis_mode
    @Property(str, notify=changed)
    def layerViewMode(self) -> str: return self._layer_view_mode
    @Property(bool, notify=changed)
    def miniMapVisible(self) -> bool: return self._mini_map_visible
    @Property(str, notify=changed)
    def miniMapDisplayMode(self) -> str: return self._mini_map_display_mode
    @Property(str, notify=changed)
    def compositeDisplayMode(self) -> str: return self._composite_display_mode
    @Property(float, notify=changed)
    def miniMapWidth(self) -> float: return self._mini_map_width
    @Property(float, notify=changed)
    def miniMapHeight(self) -> float: return self._mini_map_height
    @Property(bool, notify=changed)
    def miniMapAspectLocked(self) -> bool: return self._mini_map_aspect_locked
    @Property(float, notify=changed)
    def miniMapX(self) -> float: return self._mini_map_x
    @Property(float, notify=changed)
    def miniMapY(self) -> float: return self._mini_map_y

    @Slot(str, result=bool)
    def tipDismissed(self, tip_key: str) -> bool:
        key = self._tip_key(tip_key)
        return bool(key and key in self._dismissed_tip_keys)

    @Slot(str)
    def dismissTip(self, tip_key: str) -> None:
        key = self._tip_key(tip_key)
        if not key or key in self._dismissed_tip_keys:
            return
        self._dismissed_tip_keys.add(key)
        self._settings.setValue("experience/dismissedTips", sorted(self._dismissed_tip_keys))
        self.changed.emit()

    @Slot()
    def resetEditorTips(self) -> None:
        if not self._dismissed_tip_keys:
            return
        self._dismissed_tip_keys.clear()
        self._settings.remove("experience/dismissedTips")
        self.changed.emit()
    @Slot()
    def completeFirstRun(self) -> None:
        if not self._first_run:
            self._first_run = True
            self._settings.setValue("experience/firstRunCompleted", True)
            self.changed.emit()
    @Slot()
    def completeEditorGuide(self) -> None:
        if not self._editor_guide_completed:
            self._editor_guide_completed = True
            self._settings.setValue("experience/editorGuideCompleted", True)
            self.changed.emit()
    @Slot(str)
    def setMode(self, mode: str) -> None:
        value = mode if mode in {"beginner", "advanced"} else "beginner"
        if value != self._mode:
            self._mode = value
            self._settings.setValue("experience/mode", value)
            self.changed.emit()

    @Slot(str)
    def setSidebarMode(self, mode: str) -> None:
        if mode == "narrow":
            mode = "icon"
        value = mode if mode in SIDEBAR_MODES else "expanded"
        if value != self._sidebar_mode:
            self._sidebar_mode = value
            self._settings.setValue("experience/sidebarMode", value)
            self.changed.emit()

    @Slot(str)
    def setInspectorPanelMode(self, mode: str) -> None:
        if mode == "narrow":
            mode = "collapsed"
        value = mode if mode in INSPECTOR_PANEL_MODES else "expanded"
        if value != self._inspector_panel_mode:
            self._inspector_panel_mode = value
            self._settings.setValue("experience/inspectorPanelMode", value)
            self.changed.emit()

    @Slot(bool)
    def setFocusMode(self, enabled: bool) -> None:
        value = bool(enabled)
        if value != self._focus_mode:
            self._focus_mode = value
            self._settings.setValue("experience/focusMode", value)
            self.changed.emit()

    @Slot(bool)
    def setArrangementVisible(self, visible: bool) -> None:
        value = bool(visible)
        if value != self._arrangement_visible:
            self._arrangement_visible = value
            self._settings.setValue("experience/arrangementVisible", value)
            self.changed.emit()

    @Slot(float)
    def setArrangementHeight(self, height: float) -> None:
        value = self._bounded_range(
            height,
            DEFAULT_ARRANGEMENT_HEIGHT,
            MIN_ARRANGEMENT_HEIGHT,
            MAX_ARRANGEMENT_HEIGHT,
        )
        if value != self._arrangement_height:
            self._arrangement_height = value
            self._settings.setValue("experience/arrangementHeight", value)
            self.changed.emit()

    @Slot(str)
    def setNoteColorMode(self, mode: str) -> None:
        value = mode if mode in NOTE_COLOR_MODES else "layer"
        if value != self._note_color_mode:
            self._note_color_mode = value
            self._settings.setValue("experience/noteColorMode", value)
            self.changed.emit()

    @Slot(str)
    def setNoteEmphasisMode(self, mode: str) -> None:
        value = mode if mode in NOTE_EMPHASIS_MODES else "balanced"
        if value != self._note_emphasis_mode:
            self._note_emphasis_mode = value
            self._settings.setValue("experience/noteEmphasisMode", value)
            self.changed.emit()

    @Slot(str)
    def setLayerViewMode(self, mode: str) -> None:
        value = mode if mode in LAYER_VIEW_MODES else "ghost"
        if value != self._layer_view_mode:
            self._layer_view_mode = value
            self._settings.setValue("experience/layerViewMode", value)
            self.changed.emit()

    @Slot(str)
    def setCompositeDisplayMode(self, mode: str) -> None:
        value = mode if mode in COMPOSITE_DISPLAY_MODES else "original"
        if value != self._composite_display_mode:
            self._composite_display_mode = value
            self._settings.setValue("experience/compositeDisplayMode", value)
            self.changed.emit()
    @Slot(str)
    def setDragPreviewMode(self, mode: str) -> None:
        value = mode if mode in {"off", "grid", "continuous"} else "grid"
        if value != self._drag_preview_mode:
            self._drag_preview_mode = value
            self._settings.setValue("experience/dragPreviewMode", value)
            self.changed.emit()

    @Slot(float)
    def setNewNoteLengthBeats(self, beats: float) -> None:
        value = self._bounded_float(beats, 1.0)
        if value != self._new_note_length_beats:
            self._new_note_length_beats = value
            self._settings.setValue("experience/newNoteLengthBeats", value)
            self.changed.emit()

    @Slot(float)
    def setPianoPreviewLengthBeats(self, beats: float) -> None:
        value = self._bounded_float(beats, 1.0)
        if value != self._piano_preview_length_beats:
            self._piano_preview_length_beats = value
            self._settings.setValue("experience/pianoPreviewLengthBeats", value)
            self.changed.emit()

    @Slot(int)
    def setNewNoteVelocity(self, velocity: int) -> None:
        value = self._bounded_int(velocity, 75)
        if value != self._new_note_velocity:
            self._new_note_velocity = value
            self._settings.setValue("experience/newNoteVelocity", value)
            self.changed.emit()

    @Slot(str, bool, result=bool)
    def inspectorSectionExpanded(self, section_key: str, fallback: bool = True) -> bool:
        key = self._inspector_section_key(section_key)
        if not key or not self._settings.contains(key):
            return bool(fallback)
        return self._coerce_bool(self._settings.value(key), bool(fallback))

    @Slot(str, bool)
    def setInspectorSectionExpanded(self, section_key: str, expanded: bool) -> None:
        key = self._inspector_section_key(section_key)
        if not key:
            return
        value = bool(expanded)
        if self._settings.contains(key) and self._coerce_bool(self._settings.value(key), value) == value:
            return
        self._settings.setValue(key, value)
        self.changed.emit()

    @Slot(bool)
    def setMiniMapVisible(self, visible: bool) -> None:
        self.setMiniMapDisplayMode("visible" if visible else "hidden")

    @Slot(str)
    def setMiniMapDisplayMode(self, mode: str) -> None:
        value = mode if mode in MINI_MAP_DISPLAY_MODES else "visible"
        visible = value != "hidden"
        if value != self._mini_map_display_mode or visible != self._mini_map_visible:
            self._mini_map_display_mode = value
            self._mini_map_visible = visible
            self._settings.setValue("experience/miniMapDisplayMode", value)
            self._settings.setValue("experience/miniMapVisible", visible)
            self.changed.emit()

    @Slot(float, float)
    def setMiniMapSize(self, width: float, height: float) -> None:
        bounded_width = self._bounded_range(
            width, DEFAULT_MINI_MAP_WIDTH, MINI_MAP_MIN_WIDTH, MINI_MAP_MAX_WIDTH
        )
        bounded_height = self._bounded_range(
            height, DEFAULT_MINI_MAP_HEIGHT, MINI_MAP_MIN_HEIGHT, MINI_MAP_MAX_HEIGHT
        )
        if (bounded_width, bounded_height) != (
            self._mini_map_width,
            self._mini_map_height,
        ):
            self._mini_map_width = bounded_width
            self._mini_map_height = bounded_height
            self._settings.setValue("experience/miniMapWidth", bounded_width)
            self._settings.setValue("experience/miniMapHeight", bounded_height)
            self.changed.emit()

    @Slot(bool)
    def setMiniMapAspectLocked(self, locked: bool) -> None:
        value = bool(locked)
        if value != self._mini_map_aspect_locked:
            self._mini_map_aspect_locked = value
            self._settings.setValue("experience/miniMapAspectLocked", value)
            self.changed.emit()

    @Slot(float, float)
    def setMiniMapPosition(self, x: float, y: float) -> None:
        bounded_x = self._bounded_coordinate(x, -1.0)
        bounded_y = self._bounded_coordinate(y, -1.0)
        if (bounded_x, bounded_y) != (self._mini_map_x, self._mini_map_y):
            self._mini_map_x = bounded_x
            self._mini_map_y = bounded_y
            self._settings.setValue("experience/miniMapX", bounded_x)
            self._settings.setValue("experience/miniMapY", bounded_y)
            self.changed.emit()

    @Slot()
    def resetMiniMapSize(self) -> None:
        self.setMiniMapSize(DEFAULT_MINI_MAP_WIDTH, DEFAULT_MINI_MAP_HEIGHT)

    @Slot()
    def resetMiniMapLayout(self) -> None:
        changed = (
            not self._mini_map_visible
            or self._mini_map_display_mode != "visible"
            or (self._mini_map_width, self._mini_map_height)
            != (DEFAULT_MINI_MAP_WIDTH, DEFAULT_MINI_MAP_HEIGHT)
            or self._mini_map_aspect_locked
            or (self._mini_map_x, self._mini_map_y) != (-1.0, -1.0)
        )
        self._mini_map_visible = True
        self._mini_map_display_mode = "visible"
        self._mini_map_width = DEFAULT_MINI_MAP_WIDTH
        self._mini_map_height = DEFAULT_MINI_MAP_HEIGHT
        self._mini_map_aspect_locked = False
        self._mini_map_x = -1.0
        self._mini_map_y = -1.0
        self._settings.setValue("experience/miniMapVisible", True)
        self._settings.setValue("experience/miniMapDisplayMode", "visible")
        self._settings.setValue("experience/miniMapWidth", DEFAULT_MINI_MAP_WIDTH)
        self._settings.setValue("experience/miniMapHeight", DEFAULT_MINI_MAP_HEIGHT)
        self._settings.setValue("experience/miniMapAspectLocked", False)
        self._settings.setValue("experience/miniMapX", -1.0)
        self._settings.setValue("experience/miniMapY", -1.0)
        if changed:
            self.changed.emit()

    @staticmethod
    def _bounded_float(value, fallback: float) -> float:
        try:
            return max(0.25, min(4.0, float(value)))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _bounded_int(value, fallback: int) -> int:
        try:
            return max(1, min(127, int(value)))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _bounded_range(value, fallback: float, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(maximum, float(value)))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _bounded_coordinate(value, fallback: float) -> float:
        try:
            return max(-1.0, float(value))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _inspector_section_key(section_key: str) -> str:
        normalized = str(section_key or "").strip()
        if not normalized or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in normalized):
            return ""
        return f"experience/inspectorSections/{normalized}"

    @staticmethod
    def _tip_key(value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in normalized
        ):
            return ""
        return normalized[:64]

    @staticmethod
    def _coerce_bool(value, fallback: bool) -> bool:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            if normalized in {"1", "true", "yes", "on"}:
                return True
            return fallback
        if value is None:
            return fallback
        return bool(value)
