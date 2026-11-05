import gc
import weakref
from pathlib import Path

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QSettings, Qt, Signal
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles
from ai_piano_studio.persistence.project_document import (
    load_project_document,
    save_project_document,
)
from ai_piano_studio.templates.library import TemplateMusicLibrary
from tests.harmony.test_persistence_flows import harmony_document
from tests.qt.test_studio_workflows import no_audio_device


def load_template_ui(qapp, tmp_path):
    project_library = ProjectLibrary(tmp_path / "Projects")
    controller = EditorController(
        project_library=project_library,
        recent_files=RecentFiles(
            settings=QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat)
        ),
    )
    app, engine, controller, playback, keyboard = build_engine(
        controller=controller,
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        ),
    )
    return engine, engine.rootObjects()[0], controller


def set_page(root: QObject, page_state: str) -> None:
    assert QMetaObject.invokeMethod(
        root,
        "setPage",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", page_state),
        Q_ARG("QVariant", False),
    )


class FakeTemplatePlayer(QObject):
    positionChanged = Signal(float)
    playingChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.stop_modes: list[ReleaseMode] = []
        self.played_template_ids: list[str] = []
        self._template_ids: dict[tuple[str, ...], str] = {}
        self._pending_after_stopped = None
        self._playing = False

    def set_template_ids(self, templates: TemplateMusicLibrary) -> None:
        self._template_ids = {
            tuple(note.id for note in document.state.playback_notes): template_id
            for template_id, document in templates._documents.items()
        }

    def stop(
        self, mode: ReleaseMode = ReleaseMode.STOP, *, after_stopped=None
    ) -> None:
        self.stop_modes.append(mode)
        if not self._playing:
            if after_stopped is not None:
                after_stopped()
            return
        self._pending_after_stopped = after_stopped

    def play_notes(self, notes) -> None:
        self.played_template_ids.append(
            self._template_ids[tuple(note.id for note in notes)]
        )
        self._playing = True
        self.playingChanged.emit(True)

    def finish_pending_fade(self) -> None:
        callback = self._pending_after_stopped
        self._pending_after_stopped = None
        self._playing = False
        self.playingChanged.emit(False)
        if callback is not None:
            callback()


def make_template_service_with_fake_player():
    player = FakeTemplatePlayer()
    templates = TemplateMusicLibrary(preview_player=player)
    player.set_template_ids(templates)
    return templates, player


def test_template_replacement_waits_for_safe_retirement(qtbot) -> None:
    templates, player = make_template_service_with_fake_player()

    templates.preview("ode-to-joy")
    templates.preview("fur-elise")

    assert player.stop_modes[-1] == ReleaseMode.SAFE
    assert player.played_template_ids == ["ode-to-joy"]
    player.finish_pending_fade()
    assert player.played_template_ids[-1] == "fur-elise"


def test_template_route_leave_cancels_replacement_and_settles_previewing(qtbot) -> None:
    templates, player = make_template_service_with_fake_player()

    templates.preview("ode-to-joy")
    templates.preview("fur-elise")
    templates.stopPreview()

    assert player.stop_modes[-1] == ReleaseMode.SAFE
    assert templates.previewing is False
    assert templates.activeTemplateId == ""
    player.finish_pending_fade()
    assert player.played_template_ids == ["ode-to-joy"]


def test_pending_template_callback_does_not_retain_closed_service(qtbot) -> None:
    templates, player = make_template_service_with_fake_player()
    templates.preview("ode-to-joy")
    templates.preview("fur-elise")
    reference = weakref.ref(templates)

    templates.deleteLater()
    del templates
    gc.collect()

    qtbot.waitUntil(lambda: reference() is None, timeout=500)
    player.finish_pending_fade()


def test_template_library_renders_six_cards_and_stays_outside_editor(qapp, tmp_path) -> None:
    engine, root, controller = load_template_ui(qapp, tmp_path)
    templates = engine.rootContext().contextProperty("templates")
    assert templates is not None
    assert len(templates.templates) == 6

    set_page(root, "templates")
    qapp.processEvents()
    view = root.findChild(QObject, "templateMusicLibrary")
    assert view is not None and view.property("visible") is True
    grid = view.findChild(QObject, "templateGrid")
    assert grid is not None
    assert grid.property("count") == 6
    assert root.findChild(QObject, "editorShell") is None
    root.close()


def test_template_preview_and_adaptation_keep_editor_state_isolated(qapp, qtbot, tmp_path) -> None:
    engine, root, controller = load_template_ui(qapp, tmp_path)
    templates = engine.rootContext().contextProperty("templates")
    content_before = controller.state.content
    root.setWidth(1920)

    set_page(root, "templates")
    qapp.processEvents()
    templates.preview("ode-to-joy")
    assert templates.activeTemplateId == "ode-to-joy"
    assert templates.previewing is True
    assert controller.state.content == content_before
    assert controller.currentFilePath == ""

    created: list[str] = []
    templates.projectCreated.connect(created.append)
    templates.adapt("ode-to-joy")
    qtbot.waitUntil(lambda: len(created) == 1, timeout=1000)
    qapp.processEvents()
    assert Path(created[0]).is_file()
    assert root.property("pageState") == "editor"
    assert controller.currentFilePath == created[0]
    assert controller.state.content != content_before
    assert templates.previewing is False

    project_rail = root.findChild(QObject, "projectRail")
    back_button = root.findChild(QObject, "templateBackButton")
    project_list = root.findChild(QObject, "libraryProjectList")
    template_identity = root.findChild(QObject, "templateModeIdentity")
    template_title = root.findChild(QObject, "templateModeTitle")
    template_editor_identity = root.findChild(QObject, "templateEditorIdentity")
    assert project_rail.property("templateMode") is True
    assert project_rail.property("width") <= 96
    assert project_list.property("visible") is False
    assert root.findChild(QObject, "libraryNewProjectButton").property("visible") is False
    assert root.findChild(QObject, "projectRailOpenButton").property("visible") is False
    assert template_identity.property("visible") is False
    assert template_title.property("visible") is False
    assert template_editor_identity is not None
    assert template_editor_identity.property("visible") is True
    assert back_button.property("y") < project_rail.property("height") / 2
    assert back_button.property("text") == "返回首页"
    editor_back_button = root.findChild(QObject, "templateEditorBackButton")
    assert editor_back_button is not None
    assert editor_back_button.property("tooltip") == "返回上一层"
    assert editor_back_button.property("accessibleName") == "返回上一层"
    editor_back_button.clicked.emit()
    qapp.processEvents()
    assert root.property("pageState") == "templates"
    assert root.property("templateEditorMode") is True
    root.findChild(QObject, "welcomeOverlay").newRequested.emit()
    qapp.processEvents()
    assert root.property("pageState") == "editor"
    assert root.property("templateEditorMode") is False
    root.close()


def test_template_library_escape_returns_to_launch_and_stops_preview(qapp, tmp_path) -> None:
    engine, root, controller = load_template_ui(qapp, tmp_path)
    templates = engine.rootContext().contextProperty("templates")
    set_page(root, "templates")
    qapp.processEvents()
    templates.preview("twinkle-star")
    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert root.property("pageState") == "launch"
    assert templates.activeTemplateId == ""
    assert templates.previewing is False
    root.close()


def test_leaving_template_route_stops_preview_before_showing_next_page(qapp, tmp_path) -> None:
    engine, root, controller = load_template_ui(qapp, tmp_path)
    templates = engine.rootContext().contextProperty("templates")
    set_page(root, "templates")
    qapp.processEvents()
    templates.preview("fur-elise")
    assert templates.previewing is True

    set_page(root, "library")
    qapp.processEvents()
    assert templates.previewing is False
    assert templates.activeTemplateId == ""
    assert root.property("pageState") == "library"
    root.close()


def test_template_adaptation_preserves_harmony_document(qtbot, tmp_path) -> None:
    resource_root = tmp_path / "templates"
    template_dir = resource_root / "harmony-template"
    template_dir.mkdir(parents=True)
    (template_dir / "metadata.json").write_text(
        '{"id":"harmony-template","name":"和弦模板"}', encoding="utf-8"
    )
    (template_dir / "thumbnail.png").write_bytes(b"png")
    save_project_document(template_dir / "template.pianoai.json", harmony_document())
    project_library = ProjectLibrary(tmp_path / "Projects")
    from ai_piano_studio.templates.library import TemplateMusicLibrary

    templates = TemplateMusicLibrary(
        project_library=project_library,
        resource_root=resource_root,
    )
    created: list[str] = []
    templates.projectCreated.connect(created.append)

    templates.adapt("harmony-template")
    qtbot.waitUntil(lambda: len(created) == 1, timeout=1000)

    adapted = load_project_document(created[0])
    assert adapted.harmony.groups[0].group_id == "stable-group-id"
    assert adapted.harmony.to_dict()["futureRoot"] == {"version": 2}
