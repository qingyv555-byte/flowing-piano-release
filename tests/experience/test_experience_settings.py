from PySide6.QtCore import QSettings

from ai_piano_studio.experience.settings import ExperienceSettings


def test_experience_settings_persist_first_run_intro_mode_drag_preview_and_editor_guide(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    settings = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    assert settings.firstRunCompleted is False
    assert settings.introAnimation is True
    assert settings.mode == "advanced"
    assert settings.dragPreviewMode == "grid"
    assert settings.editorGuideCompleted is False
    assert settings.newNoteLengthBeats == 1.0
    assert settings.pianoPreviewLengthBeats == 1.0
    assert settings.newNoteVelocity == 75
    assert settings.compositeDisplayMode == "original"

    settings.completeFirstRun()
    settings.setMode("advanced")
    settings.setDragPreviewMode("continuous")
    settings.setNewNoteLengthBeats(2.0)
    settings.setPianoPreviewLengthBeats(0.5)
    settings.setNewNoteVelocity(96)
    settings.setCompositeDisplayMode("smart")
    settings.completeEditorGuide()

    restored = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert restored.firstRunCompleted is True
    assert restored.introAnimation is True
    assert restored.mode == "advanced"
    assert restored.dragPreviewMode == "continuous"
    assert restored.editorGuideCompleted is True
    assert restored.newNoteLengthBeats == 2.0
    assert restored.pianoPreviewLengthBeats == 0.5
    assert restored.newNoteVelocity == 96
    assert restored.compositeDisplayMode == "smart"


def test_legacy_beginner_launch_setting_migrates_to_professional(tmp_path) -> None:
    path = tmp_path / "legacy-mode.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/mode", "beginner")

    restored = ExperienceSettings(settings=raw)

    assert restored.mode == "advanced"
    assert raw.value("experience/mode") == "advanced"


def test_editor_tips_are_individually_dismissible_and_resettable(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    first = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    assert first.tipDismissed("move-note") is False
    first.dismissTip("move-note")
    assert first.tipDismissed("move-note") is True

    restored = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert restored.tipDismissed("move-note") is True
    assert restored.tipDismissed("resize-note") is False

    restored.resetEditorTips()
    assert restored.tipDismissed("move-note") is False
    assert restored.tipDismissed("resize-note") is False


def test_experience_migrates_legacy_drag_preview_modes_to_grid(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/dragPreviewMode", "single")

    restored = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    assert restored.dragPreviewMode == "grid"


def test_mini_map_preferences_clamp_persist_and_reset(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    first = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    assert (first.miniMapVisible, first.miniMapWidth, first.miniMapHeight) == (
        True,
        280.0,
        160.0,
    )
    assert (first.miniMapAspectLocked, first.miniMapX, first.miniMapY) == (
        False,
        -1.0,
        -1.0,
    )

    first.setMiniMapVisible(False)
    first.setMiniMapSize(999.0, 1.0)
    first.setMiniMapAspectLocked(True)
    first.setMiniMapPosition(31.0, 42.0)

    restored = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert (restored.miniMapVisible, restored.miniMapWidth, restored.miniMapHeight) == (
        False,
        400.0,
        100.0,
    )
    assert (restored.miniMapAspectLocked, restored.miniMapX, restored.miniMapY) == (
        True,
        31.0,
        42.0,
    )

    restored.resetMiniMapLayout()

    assert (restored.miniMapVisible, restored.miniMapWidth, restored.miniMapHeight) == (
        True,
        280.0,
        160.0,
    )
    assert (restored.miniMapAspectLocked, restored.miniMapX, restored.miniMapY) == (
        False,
        -1.0,
        -1.0,
    )


def test_inspector_section_state_uses_fallback_without_persisting(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    settings = ExperienceSettings(settings=raw)

    assert settings.inspectorSectionExpanded("selection", False) is False
    assert raw.contains("experience/inspectorSections/selection") is False


def test_inspector_section_state_persists_and_normalizes_boolean_values(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    first = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    first.setInspectorSectionExpanded("selection", True)

    restored = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert restored.inspectorSectionExpanded("selection", False) is True

    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/inspectorSections/selection", "false")
    normalized = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert normalized.inspectorSectionExpanded("selection", True) is False


def test_selection_actions_legacy_collapsed_state_migrates_to_visible(tmp_path) -> None:
    path = tmp_path / "legacy-selection-actions.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/inspectorSections/selectionActions", False)

    restored = ExperienceSettings(settings=raw)

    assert restored.inspectorSectionExpanded("selectionActions", False) is True
    assert raw.value("experience/inspectorSections/selectionActions", type=bool) is True
    assert raw.value("experience/selectionActionsVisibilityVersion", type=int) == 1


def test_selection_actions_manual_collapse_is_preserved_after_migration(tmp_path) -> None:
    path = tmp_path / "selection-actions.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/selectionActionsVisibilityVersion", 1)
    raw.setValue("experience/inspectorSections/selectionActions", False)

    restored = ExperienceSettings(settings=raw)

    assert restored.inspectorSectionExpanded("selectionActions", True) is False


def test_editor_layout_preferences_persist_and_normalize(tmp_path) -> None:
    path = tmp_path / "experience.ini"
    first = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))

    assert first.sidebarMode == "expanded"
    assert first.inspectorPanelMode == "expanded"
    assert first.focusMode is False
    assert first.miniMapDisplayMode == "visible"

    first.setSidebarMode("icon")
    # The Inspector no longer exposes a sparse narrow state.  Keep old saved
    # values compatible by migrating the legacy name to the real collapsed
    # state.
    first.setInspectorPanelMode("narrow")
    first.setFocusMode(True)
    first.setMiniMapDisplayMode("weak")

    restored = ExperienceSettings(settings=QSettings(str(path), QSettings.Format.IniFormat))
    assert restored.sidebarMode == "icon"
    assert restored.inspectorPanelMode == "collapsed"
    assert restored.focusMode is True
    assert restored.miniMapDisplayMode == "weak"
    assert restored.miniMapVisible is True

    first.setSidebarMode("invalid")
    first.setInspectorPanelMode("invalid")
    first.setMiniMapDisplayMode("invalid")
    assert first.sidebarMode == "expanded"
    assert first.inspectorPanelMode == "expanded"
    assert first.miniMapDisplayMode == "visible"


def test_sidebar_narrow_state_migrates_to_icon_and_stays_two_state(tmp_path) -> None:
    path = tmp_path / "legacy-sidebar.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/sidebarMode", "narrow")

    restored = ExperienceSettings(settings=raw)

    assert restored.sidebarMode == "icon"
    assert raw.value("experience/sidebarMode") == "icon"

    restored.setSidebarMode("narrow")
    assert restored.sidebarMode == "icon"
    assert raw.value("experience/sidebarMode") == "icon"


def test_arrangement_and_note_color_preferences_persist(tmp_path) -> None:
    path = tmp_path / "workspace.ini"
    first = ExperienceSettings(
        settings=QSettings(str(path), QSettings.Format.IniFormat)
    )

    assert first.arrangementVisible is False
    assert first.arrangementHeight == 240.0
    assert first.noteColorMode == "layer"
    assert first.layerViewMode == "ghost"

    first.setArrangementVisible(True)
    first.setArrangementHeight(240.0)
    first.setNoteColorMode("pitch")
    first.setLayerViewMode("active")

    restored = ExperienceSettings(
        settings=QSettings(str(path), QSettings.Format.IniFormat)
    )
    assert restored.arrangementVisible is True
    assert restored.arrangementHeight == 240.0
    assert restored.noteColorMode == "pitch"
    assert restored.layerViewMode == "active"


def test_arrangement_height_and_note_color_mode_are_normalized(tmp_path) -> None:
    path = tmp_path / "workspace-invalid.ini"
    raw = QSettings(str(path), QSettings.Format.IniFormat)
    raw.setValue("experience/arrangementHeight", 9999)
    raw.setValue("experience/noteColorMode", "rainbow")
    raw.setValue("experience/layerViewMode", "invalid")

    settings = ExperienceSettings(settings=raw)

    assert settings.arrangementHeight == 360.0
    assert settings.noteColorMode == "layer"
    assert settings.layerViewMode == "ghost"

    settings.setArrangementHeight(1.0)
    settings.setNoteColorMode("mono")
    settings.setLayerViewMode("all")
    assert settings.arrangementHeight == 120.0
    assert settings.noteColorMode == "mono"
    assert settings.layerViewMode == "all"
