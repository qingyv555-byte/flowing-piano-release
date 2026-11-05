from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, QObject, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtTest import QSignalSpy, QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles


QML_DIR = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"
SCREENSHOT_DIR = Path(".pytest-tmp") / "ui-welcome"


def _capture_dpr(root, image) -> float:
    return float(root.devicePixelRatio() or image.devicePixelRatio() or 1.0)


def _logical_capture_size(root, image) -> tuple[int, int]:
    dpr = _capture_dpr(root, image)
    return round(image.width() / dpr), round(image.height() / dpr)


def _logical_grid_sample_count(root, image, width: int, height: int) -> int:
    dpr = _capture_dpr(root, image)
    return len(
        {
            image.pixelColor(
                min(image.width() - 1, round(x * dpr)),
                min(image.height() - 1, round(y * dpr)),
            ).rgba()
            for x in range(0, width, 64)
            for y in range(0, height, 64)
        }
    )


def activate_with_key(item, key) -> None:
    for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        assert QCoreApplication.sendEvent(
            item, QKeyEvent(event_type, key, Qt.KeyboardModifier.NoModifier)
        )


def load_welcome(qapp, tmp_path, with_recovery=False, project_library=None):
    experience_settings = QSettings(
        str(tmp_path / "experience.ini"), QSettings.Format.IniFormat
    )
    appearance_settings = QSettings(
        str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
    )
    recent_settings = QSettings(
        str(tmp_path / "recent.ini"), QSettings.Format.IniFormat
    )
    recovery = RecoveryStore(tmp_path / "recovery")
    if with_recovery:
        recovery.write(
            ProjectState((Note.create(pitch=67, start_time=0.0, duration=0.5),)),
            "C:/projects/draft.pianoai.json",
        )
    controller = EditorController(
        recent_files=RecentFiles(settings=recent_settings),
        recovery_store=recovery,
        project_library=project_library,
    )
    experience = ExperienceSettings(settings=experience_settings)
    app, engine, controller, playback, keyboard = build_engine(
        controller=controller,
        experience_settings=experience,
        appearance_settings=appearance_settings,
    )
    root = engine.rootObjects()[0]
    appearance = engine.rootContext().contextProperty("appearance")
    return engine, root, controller, appearance


def _visual_item(item, object_name):
    if item.objectName() == object_name:
        return item
    for child in item.childItems():
        found = _visual_item(child, object_name)
        if found is not None:
            return found
    return None


def _project_library_with_one_entry(tmp_path):
    library = ProjectLibrary(tmp_path / "projects")
    project_path = library.next_project_path()
    project_path.parent.mkdir(parents=True, exist_ok=True)
    save_project(
        project_path,
        ProjectState((Note.create(pitch=60, start_time=0.0, duration=0.5),)),
    )
    return library


def _click_item_center(root, item) -> None:
    center = item.mapToItem(
        root.contentItem(),
        QPointF(item.property("width") / 2, item.property("height") / 2),
    )
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        center.toPoint(),
    )


def test_launch_assets_are_1080p_and_backdrop_has_independent_keys(qapp, tmp_path) -> None:
    for name in ("launch-cosmos.webp", "launch-cosmos.png"):
        image = QImage(str(QML_DIR / "assets" / name))
        assert not image.isNull(), name
        assert (image.width(), image.height()) == (1920, 1080)

    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    backdrop = root.findChild(QObject, "cosmicKeyBackdrop")
    assert backdrop is not None
    assert backdrop.property("whiteKeyCount") == 7
    assert backdrop.property("blackKeyCount") == 5
    assert backdrop.property("ambientRunning") is True
    assert backdrop.property("powerSaveEnabled") is False

    keys = [
        item for item in root.findChildren(QObject)
        if item.objectName().startswith("cosmicKey-")
    ]
    assert len(keys) == 12
    assert {item.property("keyType") for item in keys} == {"white", "black"}
    for item in keys:
        assert item.property("keyType") in {"white", "black"}
        if item.property("depth") in {"near", "mid"}:
            inside_safe_zone = (
                0.30 <= item.property("normalizedX") <= 0.70
                and 0.25 <= item.property("normalizedY") <= 0.72
            )
            assert not inside_safe_zone

    appearance.setMotionLevel("low")
    qapp.processEvents()
    assert backdrop.property("ambientRunning") is False
    appearance.setDynamicEnabled(False)
    assert backdrop.property("ambientRunning") is False
    appearance.setDynamicEnabled(True)
    appearance.setMotionLevel("medium")
    appearance.setPowerSaveEnabled(True)
    qapp.processEvents()
    assert backdrop.property("ambientRunning") is False
    appearance.setPowerSaveEnabled(False)
    assert backdrop.property("ambientRunning") is True
    root.close()


def test_launch_hierarchy_recovery_and_focus(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(
        qapp, tmp_path, with_recovery=True
    )
    welcome = root.findChild(QObject, "welcomeOverlay")
    launch = root.findChild(QObject, "welcomeLaunchView")
    play = root.findChild(QObject, "homeBrowseExamples")
    create = root.findChild(QObject, "homePrimaryCreate")
    library = root.findChild(QObject, "homeOpenProject")
    qapp.processEvents()

    assert welcome.property("pageState") == "launch"
    assert launch.property("visible") is True
    assert play.property("variant") == "ghost"
    assert create.property("height") == 48
    assert create.property("variant") == "primary"
    assert library.property("variant") == "secondary"
    assert launch.property("contentWidth") == 1100
    assert create.property("activeFocus") is True

    recovery = root.findChild(QObject, "recoveryBanner")
    assert recovery is not None
    assert recovery.property("visible") is True
    recovery_pos = recovery.mapToItem(launch, QPointF(0, 0))
    play_pos = play.mapToItem(launch, QPointF(0, 0))
    assert recovery_pos.y() < play_pos.y()

    library_requested = QSignalSpy(welcome.libraryRequested)
    assert QMetaObject.invokeMethod(launch, "focusLibraryAction", Qt.ConnectionType.DirectConnection)
    qapp.processEvents()
    assert library.property("activeFocus") is True
    activate_with_key(library, Qt.Key_Space)
    qapp.processEvents()
    assert library_requested.count() == 1

    welcome.setProperty("visible", True)
    new_requested = QSignalSpy(welcome.newRequested)
    assert QMetaObject.invokeMethod(launch, "focusPrimaryAction", Qt.ConnectionType.DirectConnection)
    qapp.processEvents()
    assert create.property("activeFocus") is True
    activate_with_key(create, Qt.Key_Return)
    assert new_requested.count() == 1

    root.setPage("launch", False)
    qapp.processEvents()
    for _ in range(30):
        if not welcome.property("launchAnimationRunning"):
            break
        qapp.processEvents()
        QTest.qWait(50)
    assert welcome.property("launchAnimationRunning") is False
    theme_picker = root.findChild(QObject, "welcomeThemePicker")
    theme_popup = root.findChild(QObject, "backgroundSettingsDialog")
    root.setWidth(1280)
    root.setHeight(720)
    assert theme_picker.property("tooltip") == "设置"
    assert theme_picker.property("accessibleName") == "设置"
    theme_picker.forceActiveFocus()
    activate_with_key(theme_picker, Qt.Key_Return)
    qapp.processEvents()
    assert theme_popup.property("visible") is True
    assert theme_popup.property("x") + theme_popup.property("width") <= root.width()
    assert theme_popup.property("y") + theme_popup.property("height") <= root.height()
    root.findChild(QObject, "backgroundEnabledToggle").forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert theme_popup.property("visible") is False
    assert theme_picker.property("activeFocus") is True

    discard_requested = QSignalSpy(welcome.discardRequested)
    discard = root.findChild(QObject, "discardRecoveryButton")
    discard.forceActiveFocus()
    activate_with_key(discard, Qt.Key_Space)
    qapp.processEvents()
    discard_dialog = root.findChild(QObject, "discardRecoveryDialog")
    assert discard_dialog.property("visible") is True
    assert discard_requested.count() == 0
    discard_confirm = root.findChild(QObject, "discardRecoveryConfirmButton")
    discard_confirm.forceActiveFocus()
    activate_with_key(discard_confirm, Qt.Key_Return)
    qapp.processEvents()
    assert discard_requested.count() == 1

    controller._recycle_job = object()
    controller.recycleProgressChanged.emit()
    qapp.processEvents()
    for name in (
        "restoreButton", "discardRecoveryButton", "homePrimaryCreate",
        "homeAiCreate", "homeOpenProject", "homeImportMusic",
        "homeBrowseExamples", "welcomeBlankButton",
    ):
        assert root.findChild(QObject, name).property("enabled") is False
    root.close()


def test_home_actions_use_clear_hierarchy_and_standard_language(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)

    expected = {
        "homePrimaryCreate": ("开始创作", "primary"),
        "homeAiCreate": ("AI 帮我创作", "secondary"),
        "homeOpenProject": ("打开已有作品", "secondary"),
        "homeImportMusic": ("导入 MIDI / 音频", "ghost"),
        "homeBrowseExamples": ("浏览示例", "ghost"),
    }
    for object_name, (text, variant) in expected.items():
        item = root.findChild(QObject, object_name)
        assert item is not None, object_name
        assert item.property("text") == text
        assert item.property("variant") == variant

    launch_source = (QML_DIR / "WelcomeLaunchView.qml").read_text(encoding="utf-8")
    for childish_label in ("玩这首", "听听看", "怎么玩"):
        assert childish_label not in launch_source
    recent = root.findChild(QObject, "homeRecentProjects")
    assert recent is not None
    assert root.findChild(QObject, "homeRecentProjectsEmpty").property("visible") is True
    navigation = root.findChild(QObject, "homeNavigation")
    assert navigation is not None
    for name, label in (
        ("homeNavHome", "首页"),
        ("homeNavProjects", "我的工程"),
        ("homeNavAssets", "音乐素材"),
        ("homeNavExamples", "示例作品"),
        ("homeNavHelp", "使用帮助"),
    ):
        action = root.findChild(QObject, name)
        assert action is not None
        assert action.property("text") == label
    root.close()


def test_first_run_home_keeps_primary_actions_clear_of_tutorial_chrome(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    root.setWidth(1366)
    root.setHeight(768)
    qapp.processEvents()

    launch = root.findChild(QObject, "welcomeLaunchView")
    create = root.findChild(QObject, "homePrimaryCreate")
    assert launch.property("firstRun") is True
    assert root.findChild(QObject, "firstRunGuide") is None

    create_position = create.mapToItem(root.contentItem(), QPointF(0, 0))
    assert create_position.y() + create.property("height") < root.height() * 0.62
    root.close()


def test_home_uses_wide_top_anchored_content_lane_on_desktop(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    root.setWidth(1920)
    root.setHeight(1080)
    qapp.processEvents()

    launch = root.findChild(QObject, "welcomeLaunchView")
    assert launch.property("width") >= 1040
    launch_position = launch.mapToItem(root.contentItem(), QPointF(0, 0))
    assert launch_position.y() <= 150
    root.close()


def test_home_recent_projects_open_existing_project(qapp, tmp_path) -> None:
    library = _project_library_with_one_entry(tmp_path)
    engine, root, controller, appearance = load_welcome(
        qapp, tmp_path, project_library=library
    )
    welcome = root.findChild(QObject, "welcomeOverlay")
    launch = root.findChild(QObject, "welcomeLaunchView")
    row = _visual_item(root.contentItem(), "homeRecentProjectRow")

    assert len(launch.property("recentEntries").toVariant()) == 1
    assert row is not None
    chosen = QSignalSpy(welcome.recentChosen)
    row.activated.emit(controller.libraryEntries[0]["path"])
    assert chosen.count() == 1
    root.close()


def test_launch_tab_order_and_input_shield(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    create = root.findChild(QObject, "homePrimaryCreate")
    ai = root.findChild(QObject, "homeAiCreate")
    library = root.findChild(QObject, "homeOpenProject")
    import_music = root.findChild(QObject, "homeImportMusic")
    play = root.findChild(QObject, "homeBrowseExamples")
    settings = root.findChild(QObject, "welcomeThemePicker")
    shield = root.findChild(QObject, "welcomeInputShield")
    qapp.processEvents()

    assert shield is not None
    assert shield.property("enabled") is True
    assert create.property("activeFocus") is True
    for expected in (ai, library, import_music, play, settings):
        QTest.keyClick(root, Qt.Key_Tab)
        qapp.processEvents()
        assert expected.property("activeFocus") is True
    root.close()


def test_library_is_internal_page_with_back_and_search_focus(qapp, tmp_path, qtbot) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    welcome.libraryRequested.emit()
    qapp.processEvents()

    assert welcome.property("visible") is True
    assert welcome.property("pageState") == "library"
    assert welcome.property("projectsPerPage") == 10
    library_view = root.findChild(QObject, "welcomeLibraryView")
    back = root.findChild(QObject, "libraryBackButton")
    search = library_view.findChild(QObject, "projectSearchField")
    assert library_view.property("visible") is True
    assert back.property("activeFocus") is True

    QTest.keyClick(root, Qt.Key_Tab)
    qapp.processEvents()
    assert search.property("activeFocus") is True
    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert welcome.property("pageState") == "launch"
    qtbot.waitUntil(
        lambda: root.findChild(QObject, "welcomeLaunchView").property("returnAnimationRunning") is False,
        timeout=1000,
    )
    assert root.findChild(QObject, "homePrimaryCreate").property("activeFocus") is True
    root.close()


def test_editor_rail_more_button_opens_project_menu(qapp, tmp_path, qtbot) -> None:
    library = _project_library_with_one_entry(tmp_path)
    engine, root, controller, appearance = load_welcome(
        qapp, tmp_path, project_library=library
    )
    root.setWidth(1920)
    assert QMetaObject.invokeMethod(
        root,
        "setPage",
        Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", "editor"),
        Q_ARG("QVariant", False),
    )
    qapp.processEvents()

    rail = root.findChild(QObject, "projectRail")
    action = _visual_item(rail, "projectRowAction")
    menu = root.findChild(QObject, "projectCardMenu")
    assert action is not None
    assert menu is not None

    _click_item_center(root, action)

    qtbot.waitUntil(lambda: menu.property("visible") is True, timeout=1000)
    assert root.findChild(QObject, "projectMenuOpenAction").property("width") > 0
    assert root.findChild(QObject, "projectDeleteAction").property("width") > 0
    root.close()


def test_library_more_button_opens_project_menu(qapp, tmp_path, qtbot) -> None:
    library = _project_library_with_one_entry(tmp_path)
    engine, root, controller, appearance = load_welcome(
        qapp, tmp_path, project_library=library
    )
    welcome = root.findChild(QObject, "welcomeOverlay")
    welcome.libraryRequested.emit()
    qapp.processEvents()

    library_view = root.findChild(QObject, "welcomeLibraryView")
    action = _visual_item(library_view, "projectRowAction")
    assert action is not None

    _click_item_center(root, action)

    menu = root.findChild(QObject, "libraryProjectCardMenu")
    assert menu is not None
    qtbot.waitUntil(lambda: menu.property("visible") is True, timeout=1000)

    expected_actions = (
        ("libraryProjectOpenAction", "打开"),
        ("libraryProjectRenameAction", "重命名"),
        ("libraryProjectCopyAction", "创建副本"),
        ("libraryProjectRevealAction", "在资源管理器中显示"),
        ("libraryProjectFavoriteAction", "收藏"),
        ("libraryProjectExportAction", "导出"),
        ("libraryProjectDeleteAction", "移动到回收站"),
    )
    for object_name, text in expected_actions:
        action_item = root.findChild(QObject, object_name)
        assert action_item is not None
        assert action_item.property("text") == text

    root.findChild(QObject, "libraryProjectRenameAction").triggered.emit()
    qapp.processEvents()
    assert root.findChild(QObject, "renameProjectDialog").property("visible") is True
    root.close()


@pytest.mark.parametrize("width,height", [(1280, 720), (1366, 768), (1920, 1080)])
def test_welcome_key_actions_fit_supported_viewports(
    qapp, tmp_path, width, height
) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    root.setFlag(Qt.FramelessWindowHint, True)
    root.setWidth(width)
    root.setHeight(height)
    assert QTest.qWaitForWindowExposed(root)
    qapp.processEvents()

    for name in (
        "homePrimaryCreate", "homeAiCreate", "homeOpenProject",
        "homeImportMusic", "homeBrowseExamples",
    ):
        item = root.findChild(QObject, name)
        point = item.mapToItem(root.contentItem(), QPointF(0, 0))
        assert point.x() >= 0
        assert point.y() >= 0
        assert point.x() + item.property("width") <= width
        assert point.y() + item.property("height") <= height

    image = root.grabWindow()
    assert not image.isNull()
    assert _logical_capture_size(root, image) == (width, height)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    output = SCREENSHOT_DIR / f"launch-{width}x{height}.png"
    assert image.save(str(output))
    assert _logical_grid_sample_count(root, image, width, height) >= 16
    root.close()


@pytest.mark.parametrize("width,height", [(1280, 720), (1366, 768), (1920, 1080)])
def test_welcome_diagnostic_screenshots_cover_launch_library_and_fallback_states(
    qapp, qtbot, tmp_path, width, height
) -> None:
    def capture(root, state: str) -> None:
        image = root.grabWindow()
        assert not image.isNull()
        assert _logical_capture_size(root, image) == (width, height)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        assert image.save(str(SCREENSHOT_DIR / f"{state}-{width}x{height}.png"))
        assert _logical_grid_sample_count(root, image, width, height) >= 16

    def show(root) -> None:
        root.setFlag(Qt.FramelessWindowHint, True)
        root.setWidth(width)
        root.setHeight(height)
        assert QTest.qWaitForWindowExposed(root)
        qapp.processEvents()

    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    show(root)
    welcome = root.findChild(QObject, "welcomeOverlay")
    backdrop = root.findChild(QObject, "cosmicKeyBackdrop")
    capture(root, "launch-no-recovery")

    welcome.libraryRequested.emit()
    qapp.processEvents()
    assert welcome.property("pageState") == "library"
    assert root.findChild(QObject, "welcomeLibraryView").property("visible") is True
    capture(root, "library-empty")

    welcome.setProperty("pageState", "launch")
    appearance.setMotionLevel("low")
    qapp.processEvents()
    assert backdrop.property("ambientRunning") is False
    capture(root, "launch-low-motion")

    appearance.setMotionLevel("medium")
    appearance.setDynamicEnabled(False)
    qapp.processEvents()
    assert backdrop.property("ambientRunning") is False
    capture(root, "launch-dynamic-disabled")

    backdrop.setProperty(
        "backgroundSource", QUrl.fromLocalFile(str(QML_DIR / "assets" / "missing.webp"))
    )
    launch_image = backdrop.findChild(QObject, "launchBackdropImage")
    assert launch_image is not None
    qtbot.waitUntil(lambda: backdrop.property("triedPngFallback") is True, timeout=2000)
    qtbot.waitUntil(lambda: launch_image.property("imageReady") is True, timeout=2000)
    assert backdrop.property("fallbackActive") is False
    capture(root, "launch-bitmap-fallback")
    root.close()

    engine, root, controller, appearance = load_welcome(qapp, tmp_path, with_recovery=True)
    show(root)
    assert root.findChild(QObject, "recoveryBanner").property("visible") is True
    capture(root, "launch-with-recovery")
    root.close()

    library = ProjectLibrary(tmp_path / "projects")
    for pitch in (60, 64):
        project_path = library.next_project_path()
        project_path.parent.mkdir(parents=True, exist_ok=True)
        save_project(
            project_path,
            ProjectState((Note.create(pitch=pitch, start_time=0.0, duration=0.5),)),
        )
    engine, root, controller, appearance = load_welcome(
        qapp, tmp_path, project_library=library
    )
    show(root)
    welcome = root.findChild(QObject, "welcomeOverlay")
    welcome.libraryRequested.emit()
    qapp.processEvents()
    assert welcome.property("pageState") == "library"
    assert root.findChild(QObject, "recentList").property("visible") is True
    capture(root, "library-normal")

    welcome.setProperty("libraryManaging", True)
    qapp.processEvents()
    assert root.findChild(QObject, "libraryManagementButton").property("visible") is True
    capture(root, "library-management")

    controller._recycle_job = object()
    controller._recycle_progress = 1
    controller._recycle_total = 2
    controller.recycleProgressChanged.emit()
    qapp.processEvents()
    assert root.findChild(QObject, "bulkRecycleProgressBar").property("visible") is True
    capture(root, "library-recycle-progress")
    root.close()


def test_my_projects_does_not_reveal_editor_rail(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    assert root.findChild(QObject, "projectRail") is None

    welcome.libraryRequested.emit()
    qapp.processEvents()

    assert welcome.property("visible") is True
    assert welcome.property("pageState") == "library"
    assert root.findChild(QObject, "projectRail") is None
    root.close()


def test_editor_return_opens_library_not_launch(qapp, tmp_path) -> None:
    engine, root, controller, appearance = load_welcome(qapp, tmp_path)
    welcome = root.findChild(QObject, "welcomeOverlay")
    assert QMetaObject.invokeMethod(
        root, "setPage", Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", "editor"), Q_ARG("QVariant", False),
    )
    qapp.processEvents()
    back = root.findChild(QObject, "backToLibraryButton")
    assert back is not None
    back.clicked.emit()
    qapp.processEvents()

    assert welcome.property("visible") is True
    assert welcome.property("pageState") == "library"
    library_view = root.findChild(QObject, "welcomeLibraryView")
    assert library_view.findChild(QObject, "projectSearchField").property("activeFocus") is True
    root.close()
