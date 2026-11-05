"""Color and native-note presets work independently of private wallpaper."""
import pytest
from PySide6.QtCore import QObject, QSettings
from PySide6.QtGui import QColor, QImage

from ai_piano_studio.appearance.character_skins import SKINS
from ai_piano_studio.appearance.studio_settings import StudioAppearance
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from tests.qt.test_studio_appearance import visual_child
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session, click
from tools.studio_evidence import install_memory_output


@pytest.fixture(autouse=True)
def no_physical_audio(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


@pytest.mark.parametrize("key", SKINS)
def test_color_preset_without_artwork_cancel_commit_reopen(qapp, tmp_path, key):
    settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.IniFormat)
    appearance = StudioAppearance(settings, tmp_path / "backgrounds")
    assert appearance.characterSkins == []
    assert {t["key"] for t in appearance.characterThemes} == set(SKINS)
    before = appearance.values
    appearance.beginPreview()
    appearance.applyCharacterTheme(key)
    assert appearance.values["themeId"] == "ink"
    assert appearance.values["themePalette"] == key
    assert appearance.values["characterSkin"] == "none"
    assert appearance.themeSeed == QColor(SKINS[key]["seed"])
    assert appearance.values["editorSource"] == "builtin"
    assert settings.allKeys() == []
    appearance.cancelPreview()
    assert appearance.values == before
    appearance.beginPreview()
    appearance.applyCharacterTheme(key)
    assert appearance.applyPreview()
    reopened = StudioAppearance(settings, tmp_path / "backgrounds")
    assert reopened.values == appearance.values
    assert reopened.themeSeed == appearance.themeSeed
    assert not (tmp_path / "backgrounds/character-skins").exists()


def test_palette_change_preserves_background_and_motion_choices(qapp, tmp_path):
    appearance = StudioAppearance(QSettings(str(tmp_path / "a.ini"), QSettings.IniFormat), tmp_path / "backgrounds")
    appearance.setValue("reducedMotion", True)
    appearance.setValue("feedbackLevel", "off")
    appearance.setValue("feedbackStyle", "weave")
    appearance.setValue("editorSource", "none")
    appearance.applyCharacterTheme("gale")
    assert appearance.values["editorSource"] == "none"
    assert appearance.values["reducedMotion"] is True
    assert appearance.values["feedbackLevel"] == "off"
    assert appearance.values["feedbackStyle"] == "weave"
    before = appearance.values
    appearance.applyCharacterTheme("../../invalid")
    assert appearance.values == before


def test_note_palette_cards_reach_real_note_material_and_transport(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1440, 1000)
    initialize_deferred_services(engine)
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    qtbot.waitUntil(lambda: visual_child(root.contentItem(), "timeline") is not None)
    appearance = engine.rootContext().contextProperty("studioAppearance")
    original = appearance.values
    root.findChild(QObject, "studioSettingsShortcut").activated.emit()
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioAppearanceDialog") is not None)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    card = visual_child(root.contentItem(), "studioNotePalette-traveler")
    assert card is not None and card.isVisible()
    card.clicked.emit()
    assert appearance.values["characterSkin"] == "traveler"
    assert appearance.values["themeId"] == original["themeId"]
    dialog.close()
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert appearance.values == original
    appearance.applyNotePalette("traveler")
    state = ProjectState(notes=(Note.create(id="held", pitch=60, start_time=0, duration=4),))
    editor._replace_project(state, saved_content=state.content, path=None)
    timeline = visual_child(root.contentItem(), "timeline")
    root.requestActivate()
    playback._on_playing(True)
    playback._on_position(.3)
    qtbot.waitUntil(lambda: bool(timeline.property("characterMotionActive")))
    qtbot.waitUntil(lambda: visual_child(timeline, "characterNoteSurface") is not None)
    surface = visual_child(timeline, "characterNoteSurface")
    qtbot.waitUntil(lambda: surface.property("elapsed") >= .3)
    assert timeline.property("characterSkin") == "traveler"
    assert editor.state.content == state.content
    appearance.setValue("noteFeedback", False)
    assert timeline.property("characterMotionActive") is False
    appearance.setValue("noteFeedback", True)
    assert timeline.property("characterMotionActive") is True
    appearance.setValue("reducedMotion", True)
    assert timeline.property("characterMotionActive") is False
    playback._on_playing(False)


def test_theme_background_is_one_page_without_role_gallery(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    root.resize(1440, 1000)
    root.findChild(QObject, "studioSettingsShortcut").activated.emit()
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioAppearanceDialog") is not None)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    tab = visual_child(root.contentItem(), "studioSettingsCategory0")
    assert tab.property("text") == "主题背景"
    assert visual_child(root.contentItem(), "studioSettingsCategory1") is None
    assert dialog.property("page") == 0
    assert visual_child(root.contentItem(), "studioTheme-paper").isVisible()
    assert visual_child(root.contentItem(), "studioPalette-gale").isVisible()
    assert visual_child(root.contentItem(), "studioBackgroundChooseImage").isVisible()
    assert visual_child(root.contentItem(), "studioSkin-gale") is None
    for page in (2, 3):
        visual_child(root.contentItem(), f"studioSettingsCategory{page}").clicked.emit()
        assert dialog.property("page") == page
    tab.clicked.emit()
    assert dialog.property("page") == 0
    dialog.close()


def test_imported_image_can_tint_theme_without_losing_native_note_style(qapp, qtbot, tmp_path):
    appearance = StudioAppearance(QSettings(str(tmp_path / "a.ini"), QSettings.IniFormat), tmp_path / "backgrounds")
    appearance.applyCharacterTheme("gale")
    appearance.applyNotePalette("gale")
    source = tmp_path / "blue.png"
    image = QImage(48, 32, QImage.Format_RGB32)
    image.fill(QColor("#426dc7"))
    assert image.save(str(source))
    appearance.importBackground("editor", str(source))
    qtbot.waitUntil(lambda: not appearance.importBusy)
    assert appearance.themeSeed == QColor(SKINS["gale"]["seed"])
    appearance.setValue("themeSeedSource", "image")
    qtbot.waitUntil(lambda: appearance.themeSeed.alpha() > 0)
    assert appearance.themeSeed != QColor(SKINS["gale"]["seed"])
    assert appearance.values["characterSkin"] == "gale"
    appearance.applyCharacterTheme("hack")
    assert appearance.themeSeed == QColor(SKINS["hack"]["seed"])
    assert appearance.values["editorSource"].startswith("file:")


def test_custom_color_preview_cancel_save_reopen_and_preset_return(qapp, tmp_path):
    store = QSettings(str(tmp_path / "custom.ini"), QSettings.IniFormat)
    appearance = StudioAppearance(store, tmp_path / "backgrounds")
    appearance.applyCharacterTheme("gale")
    appearance.setValue("themeId", "paper")
    appearance.setValue("reducedMotion", True)
    appearance.setValue("feedbackStyle", "weave")
    before = appearance.values
    appearance.beginPreview()
    appearance.applyCustomThemeColor("#4D79C8")
    assert appearance.themeSeed == QColor("#4d79c8")
    assert appearance.values["customThemeColor"] == "#4d79c8"
    assert appearance.values["themeSeedSource"] == "custom"
    for key in ("characterSkin", "editorSource", "themeId", "reducedMotion", "feedbackStyle"):
        assert appearance.values[key] == before[key]
    assert store.allKeys() == []
    appearance.cancelPreview()
    assert appearance.values == before
    appearance.beginPreview()
    appearance.applyCustomThemeColor("#4d79c8")
    assert appearance.applyPreview()
    reopened = StudioAppearance(store, tmp_path / "backgrounds")
    assert reopened.themeSeed == QColor("#4d79c8")
    assert reopened.values["themeId"] == "paper"
    reopened.applyCharacterTheme("bee")
    assert reopened.themeSeed == QColor(SKINS["bee"]["seed"])
    assert reopened.values["customThemeColor"] == "#4d79c8"


@pytest.mark.parametrize("bad", ["not-a-color", "#123", "#80123456", "", "file:///color"])
def test_invalid_custom_colors_keep_current_theme(qapp, tmp_path, bad):
    appearance = StudioAppearance(QSettings(str(tmp_path / "custom.ini"), QSettings.IniFormat), tmp_path / "backgrounds")
    appearance.applyCharacterTheme("traveler")
    before = appearance.values
    appearance.applyCustomThemeColor(bad)
    assert appearance.values == before


def test_palette_dialog_accept_cancel_and_outer_transaction(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    root.findChild(QObject, "studioSettingsShortcut").activated.emit()
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioAppearanceDialog") is not None)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    appearance = engine.rootContext().contextProperty("studioAppearance")
    original = appearance.values
    visual_child(root.contentItem(), "studioCustomThemeColor").clicked.emit()
    picker = root.findChild(QObject, "studioThemeColorPicker")
    qtbot.waitUntil(lambda: picker.property("visible"))
    picker.setProperty("selectedColor", QColor("#8855cc"))
    picker.rejected.emit()
    picker.close()
    assert appearance.values == original
    visual_child(root.contentItem(), "studioCustomThemeColor").clicked.emit()
    qtbot.waitUntil(lambda: picker.property("visible"))
    picker.setProperty("selectedColor", QColor("#8855cc"))
    picker.accepted.emit()
    picker.close()
    assert appearance.themeSeed == QColor("#8855cc")
    assert appearance.values["themeSeedSource"] == "custom"
    dialog.close()
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert appearance.values == original


@pytest.mark.parametrize("note_key", ["none", *SKINS])
def test_theme_and_note_choices_are_independent(qapp, tmp_path, note_key):
    store = QSettings(str(tmp_path / "independent.ini"), QSettings.IniFormat)
    appearance = StudioAppearance(store, tmp_path / "backgrounds")
    appearance.applyNotePalette(note_key)
    note_before = {k: appearance.values[k] for k in ("characterSkin", "noteColorSource", "customNoteColor")}
    for key in SKINS:
        appearance.applyCharacterTheme(key)
        appearance.setValue("themeId", "paper")
        appearance.applyCustomThemeColor("#356bcc")
        appearance.setValue("themeSeedSource", "image")
        assert {k: appearance.values[k] for k in note_before} == note_before
    appearance.applyCharacterTheme("gale")
    theme_before = appearance.themeSeed
    appearance.beginPreview()
    appearance.applyCustomNoteColor("#DC45A8")
    assert appearance.themeSeed == theme_before
    assert appearance.values["customNoteColor"] == "#dc45a8"
    assert appearance.values["noteColorSource"] == "custom"
    assert store.allKeys() == []
    appearance.cancelPreview()
    assert {k: appearance.values[k] for k in note_before} == note_before
    appearance.beginPreview()
    appearance.applyCustomNoteColor("#dc45a8")
    appearance.applyCharacterTheme("hack")
    assert appearance.values["customNoteColor"] == "#dc45a8"
    assert appearance.values["noteColorSource"] == "custom"
    assert appearance.applyPreview()
    reopened = StudioAppearance(store, tmp_path / "backgrounds")
    assert reopened.values == appearance.values
    reopened.applyNotePalette(note_key)
    assert reopened.themeSeed == QColor(SKINS["hack"]["seed"])
    assert reopened.values["noteColorSource"] == "palette"


def test_old_shared_palette_migrates_without_writing_profile(qapp, tmp_path):
    import json
    store = QSettings(str(tmp_path / "legacy.ini"), QSettings.IniFormat)
    old = json.dumps({"characterSkin": "traveler", "adaptiveTheme": True,
                      "themeId": "ink", "themeSeedSource": "palette"})
    store.setValue("studioAppearance/values", old)
    appearance = StudioAppearance(store, tmp_path / "backgrounds")
    assert appearance.themeSeed == QColor(SKINS["traveler"]["seed"])
    assert appearance.values["characterSkin"] == "traveler"
    assert appearance.values["themePalette"] == "traveler"
    appearance.applyCharacterTheme("bee")
    assert appearance.values["characterSkin"] == "traveler"
    assert store.value("studioAppearance/values") == old


@pytest.mark.parametrize("bad", ["#123", "#80123456", "invalid", "", "file:///color"])
def test_invalid_custom_note_color_does_not_change_either_palette(qapp, tmp_path, bad):
    appearance = StudioAppearance(QSettings(str(tmp_path / "a.ini"), QSettings.IniFormat), tmp_path / "backgrounds")
    appearance.applyCharacterTheme("gale")
    appearance.applyCustomNoteColor("#0e2b90")
    before = appearance.values
    appearance.applyCustomNoteColor(bad)
    appearance.applyNotePalette("../../invalid")
    assert appearance.values == before
    appearance.setValue("customNoteColor", bad)
    assert appearance.values["customNoteColor"] == "#53a79b"


@pytest.mark.parametrize("dense", [False, True])
def test_custom_note_color_reaches_real_renderers_and_survives_theme_changes(studio_session, qtbot, tmp_path, dense):
    from tests.qt.test_studio_workflows import qml
    app, engine, root, editor, playback = studio_session
    root.resize(1440, 1000)
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    notes = (Note.create(id="color-held", pitch=72, start_time=0, duration=3),)
    if dense:
        notes += tuple(Note.create(id=f"outside-{i}", pitch=60, start_time=20+i*.1, duration=.05) for i in range(610))
    state = ProjectState(notes=notes)
    editor._replace_project(state, saved_content=state.content, path=None)
    appearance = engine.rootContext().contextProperty("studioAppearance")
    experience = engine.rootContext().contextProperty("experience")
    timeline = visual_child(root.contentItem(), "timeline")
    qtbot.waitUntil(lambda: timeline.property("width") > 500)
    appearance.applyNotePalette("traveler")
    for mode in ("original", "smart"):
        experience.setCompositeDisplayMode(mode)
        for color_mode in ("layer", "mono", "pitch"):
            experience.setNoteColorMode(color_mode)
            appearance.applyCustomNoteColor("#dc45a8")
            assert timeline.property("characterSkin") == "none"
            assert qml(timeline, "notePresentationColor(72, 'melody', false, '#22ff00')") == QColor("#dc45a8")
            for theme_key in ("gale", "bee"):
                appearance.applyCharacterTheme(theme_key)
                for base in ("paper", "ink"):
                    appearance.setValue("themeId", base)
                    assert qml(timeline, "notePresentationColor(72, 'melody', false, '#22ff00')") == QColor("#dc45a8")
                    qtbot.wait(40)
                    # Sample the actual painted note interior, beyond its label.
                    pos = qml(timeline, "mapToItem(null, keyWidth + 140, rulerHeight + (highestPitch - 72) * rowHeight + 9)")
                    qtbot.waitUntil(lambda: root.grabWindow().pixelColor(round(pos.x()), round(pos.y())) == QColor("#dc45a8"))
            # Repaint must also react when only the custom color changes.
            appearance.applyCustomNoteColor("#337dcc")
            qtbot.waitUntil(lambda: root.grabWindow().pixelColor(round(pos.x()), round(pos.y())) == QColor("#337dcc"))
        assert root.grabWindow().save(str(tmp_path / f"note-color-{dense}-{mode}.png"))
    assert editor.state.content == state.content
    appearance.applyNotePalette("traveler")
    assert timeline.property("characterSkin") == "traveler"


def test_note_color_picker_and_hex_are_independent_and_transactional(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    root.findChild(QObject, "studioSettingsShortcut").activated.emit()
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioAppearanceDialog") is not None)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    appearance = engine.rootContext().contextProperty("studioAppearance")
    original = appearance.values
    button = visual_child(root.contentItem(), "studioCustomNoteColor")
    assert button is not None and button.isVisible()
    button.clicked.emit()
    picker = root.findChild(QObject, "studioNoteColorPicker")
    qtbot.waitUntil(lambda: picker.property("visible"))
    picker.setProperty("selectedColor", QColor("#dc45a8"))
    picker.rejected.emit()
    picker.close()
    assert appearance.values == original
    button.clicked.emit()
    qtbot.waitUntil(lambda: picker.property("visible"))
    picker.setProperty("selectedColor", QColor("#dc45a8"))
    picker.accepted.emit()
    picker.close()
    assert appearance.values["customNoteColor"] == "#dc45a8"
    for key in ("themePalette", "themeId", "themeSeedSource", "customThemeColor", "adaptiveTheme"):
        assert appearance.values[key] == original[key]
    field = visual_child(root.contentItem(), "studioCustomNoteHex")
    field.setProperty("text", "#337dcc")
    field.editingFinished.emit()
    assert appearance.values["customNoteColor"] == "#337dcc"
    dialog.close()
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert appearance.values == original


def test_pitch_and_mono_stay_stable_while_auto_roles_follow_themes(studio_session, qtbot):
    from tests.qt.test_studio_workflows import qml
    app, engine, root, editor, playback = studio_session
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    timeline = visual_child(root.contentItem(), "timeline")
    appearance = engine.rootContext().contextProperty("studioAppearance")
    appearance.applyNotePalette("none")
    expressions = ["theme.noteMono", "theme.notePitchColor(72, 36, 84)",
                   "theme.minimapNoteColor(72, 36, 84)"]
    before = [qml(timeline, expr) for expr in expressions]
    role_initial = qml(timeline, "theme.layerRoleColor('bass')")
    changed = 0
    for key in SKINS:
        appearance.applyCharacterTheme(key)
        for base in ("paper", "ink"):
            appearance.setValue("themeId", base)
            assert [qml(timeline, expr) for expr in expressions] == before
            changed += qml(timeline, "theme.layerRoleColor('bass')") != role_initial
    appearance.applyCustomThemeColor("#ee2255")
    assert [qml(timeline, expr) for expr in expressions] == before
    assert changed > 0, "auto mode must actually respond to different theme seeds"
    assert timeline.property("characterSkin") == "none"


@pytest.mark.parametrize("theme_id", ["ink", "paper"])
@pytest.mark.parametrize("size", [(860, 640), (1440, 900)])
def test_settings_color_choices_are_compact_and_consistent(studio_session, qtbot, theme_id, size):
    from pathlib import Path
    _, engine, root, _, _ = studio_session
    initialize_deferred_services(engine)
    appearance = engine.rootContext().contextProperty("studioAppearance")
    appearance.setValue("themeId", theme_id)
    root.resize(*size)
    root.findChild(QObject, "studioSettingsShortcut").activated.emit()
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    qtbot.wait(80)
    theme_choice = visual_child(root.contentItem(), "studioPalette-gale")
    note_choice = visual_child(root.contentItem(), "studioNotePalette-gale")
    assert theme_choice.height() == note_choice.height() == 32
    # Nov 4 settings regrouping: equal compact chips keep six colors on one row.
    assert theme_choice.width() == note_choice.width() == 72
    assert visual_child(root.contentItem(), "studioTheme-ink").height() == 104
    assert visual_child(root.contentItem(), "studioTheme-ink").width() <= 280
    note_choice.clicked.emit()
    assert note_choice.property("checked")
    theme_choice.clicked.emit()
    assert theme_choice.property("checked") and note_choice.property("checked")
    theme_choice.clicked.emit()
    assert theme_choice.property("checked")
    # Capture the untouched initial theme as well as selected controls.
    appearance.setValue("themeId", theme_id)
    qtbot.wait(80)
    folder = Path("artifacts/settings-compact-20261102")
    folder.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(folder / (theme_id + "-" + str(size[0]) + ".png")))
    dialog.close()


@pytest.mark.parametrize("size", [(860, 640), (1622, 826)])
@pytest.mark.parametrize("theme_id", ["ink", "paper"])
def test_help_summary_expands_and_last_action_remains_reachable(studio_session, qtbot, size, theme_id):
    from pathlib import Path
    from PySide6.QtCore import QPointF
    from tests.qt.test_studio_workflows import qml
    _, engine, root, _, _ = studio_session
    initialize_deferred_services(engine)
    engine.rootContext().contextProperty("studioAppearance").setValue("themeId", theme_id)
    root.resize(*size)
    qtbot.wait(80)
    click(root, "studioHelpButton")
    dialog = root.findChild(QObject, "supportDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    qtbot.wait(50)
    preview = visual_child(root.contentItem(), "diagnosticsPreviewScroll")
    assert not preview.isVisible()
    toggle = visual_child(root.contentItem(), "toggleDiagnosticsPreview")
    toggle.clicked.emit()
    assert preview.isVisible()
    scroll = visual_child(root.contentItem(), "supportBodyScroll")
    qtbot.wait(50)
    assert qml(scroll, "contentHeight > availableHeight")
    qml(scroll, "contentItem.contentY = contentHeight - availableHeight; true")
    qtbot.wait(50)
    last = visual_child(root.contentItem(), "restoreDefaultsButton")
    last_top = last.mapToScene(QPointF(0, 0)).y()
    last_bottom = last.mapToScene(QPointF(0, last.height())).y()
    view_top = scroll.mapToScene(QPointF(0, 0)).y()
    assert last_top >= view_top
    assert last_bottom <= view_top + scroll.height() + 1
    close = visual_child(root.contentItem(), "supportDialogCloseButton")
    assert close.isVisible() and close.isEnabled()
    toggle.clicked.emit()
    qml(scroll, "contentItem.contentY = 0; true")
    qtbot.wait(50)
    folder = Path("artifacts/help-fit-20261102")
    folder.mkdir(parents=True, exist_ok=True)
    root.grabWindow().save(str(folder / (theme_id + "-" + str(size[0]) + ".png")))
    dialog.close()
