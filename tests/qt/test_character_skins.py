"""Promo skins remain local, transactional and usable by the real editor."""
import pytest
from pathlib import Path
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QColor, QImage, QImageReader
from PySide6.QtTest import QTest

from ai_piano_studio.appearance.character_skins import SKINS, artwork_url
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from tests.qt.test_studio_appearance import appearance, appearance_window, visual_child
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session, click
from tools.studio_evidence import install_memory_output
from tests.qt.test_studio_workflows import qml


@pytest.fixture(autouse=True)
def local_skin_artwork(monkeypatch, qapp):
    """Exercise private-artwork support using original generated test images."""
    from ai_piano_studio.appearance.studio_settings import StudioAppearance
    original_init = StudioAppearance.__init__

    def initialize(self, settings, asset_home, parent=None):
        folder = Path(asset_home) / "character-skins"
        folder.mkdir(parents=True, exist_ok=True)
        for key, preset in SKINS.items():
            image = QImage(48, 32, QImage.Format_RGB32)
            image.fill(QColor(preset["seed"]))
            assert image.save(str(folder / f"skin-{key}.png"))
        original_init(self, settings, asset_home, parent)

    monkeypatch.setattr(StudioAppearance, "__init__", initialize)


@pytest.fixture(autouse=True)
def memory_audio(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


@pytest.mark.parametrize("key", SKINS)
def test_skin_preset_cancel_save_and_reopen(appearance, key):
    original = appearance.values
    appearance.beginPreview()
    appearance.applyCharacterSkin(key)
    assert appearance.values["characterSkin"] == key
    assert appearance.values["themeId"] == "ink"
    assert appearance.values["editorSource"] == f"skin:{key}"
    assert appearance.themeSeed.name() == SKINS[key]["seed"]
    assert appearance._settings.allKeys() == []
    url = appearance.backgroundUrl(appearance.values["editorSource"])
    assert QImageReader(QUrl(url).toLocalFile()).canRead()
    appearance.cancelPreview()
    assert appearance.values == original
    appearance.beginPreview()
    appearance.applyCharacterSkin(key)
    assert appearance.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values == appearance.values
    assert reopened.themeSeed == appearance.themeSeed
    reopened.beginPreview()
    reopened.applyCharacterSkin("none")
    assert reopened.values["characterSkin"] == "none"
    assert reopened.values["editorSource"] == "builtin"
    reopened.cancelPreview()
    assert reopened.values == appearance.values


def test_skin_sources_are_allowlisted_and_preserve_motion_preferences(appearance):
    original = appearance.values
    appearance.applyCharacterSkin("../../outside")
    assert appearance.values == original
    assert artwork_url("../../outside") == ""
    appearance.setValue("editorSource", "skin:../outside")
    assert appearance.values["editorSource"] == "builtin"
    appearance.setValue("reducedMotion", True)
    appearance.setValue("feedbackLevel", "off")
    appearance.applyCharacterSkin("hack")
    assert appearance.values["reducedMotion"] is True
    assert appearance.values["feedbackLevel"] == "off"
    assert not appearance._sampling


def test_larger_settings_color_cards_select_and_cancel(appearance_window, qtbot, tmp_path):
    root, appearance = appearance_window
    root.resize(1600, 1000)
    dialog = root.findChild(QObject, "studioAppearanceDialog")
    dialog.open()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    qtbot.waitUntil(lambda: dialog.property("width") == 1120)
    assert dialog.property("height") == 820
    assert visual_child(root.contentItem(), "studioSkin-gale") is None
    card = visual_child(root.contentItem(), "studioNotePalette-gale")
    assert card is not None and card.isVisible()
    card.clicked.emit()
    assert appearance.values["characterSkin"] == "gale"
    assert root.grabWindow().save(str(tmp_path / "skin-settings.png"))
    dialog.close()
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert appearance.values["characterSkin"] == "none"


@pytest.mark.parametrize("dense", [False, True])
def test_character_materials_render_in_both_editor_modes(studio_session, qtbot, tmp_path, dense):
    app, engine, root, editor, playback = studio_session
    root.resize(1440, 900)
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    notes = tuple(Note.create(id=f"skin-{i}", pitch=60 + i % 12,
                             start_time=i * 0.08, duration=0.75)
                  for i in range(610 if dense else 12))
    state = ProjectState(notes=notes)
    editor._replace_project(state, saved_content=state.content, path=None)
    appearance = engine.rootContext().contextProperty("studioAppearance")
    experience = engine.rootContext().contextProperty("experience")
    timeline = root.findChild(QObject, "timeline")
    before = editor.state.content
    appearance.beginPreview()
    for mode in ("original", "smart"):
        experience.setCompositeDisplayMode(mode)
        for key in SKINS:
            appearance.applyCharacterSkin(key)
            def background_ready():
                image = visual_child(timeline, "studioBackgroundImage")
                return image is not None and bool(qml(image, "status === Image.Ready"))
            qtbot.waitUntil(background_ready)
            QTest.qWait(60)
            assert timeline.property("characterSkin") == key
            assert timeline.property("characterMotionActive") is False
            assert root.grabWindow().save(str(tmp_path / f"{key}-{mode}.png"))
            assert editor.state.content == before
    appearance.cancelPreview()
    assert timeline.property("characterSkin") == "none"


def test_character_motion_follows_transport_and_accessibility(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    state = ProjectState(notes=(Note.create(id="held", pitch=60, start_time=0, duration=4),))
    editor._replace_project(state, saved_content=state.content, path=None)
    appearance = engine.rootContext().contextProperty("studioAppearance")
    scene = engine.rootContext().contextProperty("scene")
    scene.setPowerSaveEnabled(False)
    appearance.beginPreview()
    appearance.applyCharacterSkin("gale")
    timeline = root.findChild(QObject, "timeline")
    root.requestActivate()
    playback.play()
    qtbot.waitUntil(lambda: playback.playing, timeout=10000)
    qtbot.waitUntil(lambda: bool(timeline.property("characterMotionActive")))
    surface = visual_child(timeline, "characterNoteSurface")
    assert surface is not None
    playback.seek(0.3)
    qtbot.waitUntil(lambda: surface.property("elapsed") >= 0.3)
    appearance.setValue("reducedMotion", True)
    assert timeline.property("characterMotionActive") is False
    assert surface.property("elapsed") == -1
    appearance.setValue("reducedMotion", False)
    assert timeline.property("characterMotionActive") is True
    appearance.setValue("noteFeedback", False)
    assert timeline.property("characterMotionActive") is False
    appearance.setValue("noteFeedback", True)
    playback.pause()
    assert timeline.property("characterMotionActive") is False
    assert surface.property("elapsed") == -1
    appearance.cancelPreview()
