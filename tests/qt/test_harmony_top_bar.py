import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.harmony.settings import HarmonySettings


def _accessible_name(item: QObject) -> str:
    interface = QAccessible.queryAccessibleInterface(item)
    assert interface is not None
    return interface.text(QAccessible.Text.Name)


def _accessible_description(item: QObject) -> str:
    interface = QAccessible.queryAccessibleInterface(item)
    assert interface is not None
    return interface.text(QAccessible.Text.Description)


def _window_point(root, item: QQuickItem) -> QPoint:
    point = item.mapToItem(root.contentItem(), QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(point.x()), round(point.y()))


@pytest.mark.parametrize("width", (1920, 1366, 1280))
def test_harmony_controls_keep_help_inside_the_harmony_popup(qapp, width) -> None:
    harmony_settings = HarmonySettings()
    harmony_settings.setMode("off")
    app, engine, controller, playback, keyboard = build_engine(harmony_settings=harmony_settings)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    root.setWidth(width)
    qapp.processEvents()

    mode_action = root.findChild(QObject, "moreHarmonyAction")
    assert mode_action is not None
    assert mode_action.property("enabled") is True
    assert mode_action.property("text") == "和弦模式 · 关"
    assert _accessible_name(mode_action) == "和弦模式 · 关"
    help_button = root.findChild(QQuickItem, "harmonyHelpButton")
    assert help_button is not None
    assert help_button.property("visible") is False
    assert _accessible_name(help_button) == "和弦帮助"

    opener = root.findChild(QQuickItem, "moreMenuButton")
    popup = root.findChild(QObject, "harmonyModePopup")
    assert opener is not None and popup is not None
    mode_action.triggered.emit()
    qapp.processEvents()
    assert popup.property("opened") is True
    assert help_button.property("visible") is True

    root.close()


@pytest.mark.parametrize("key", (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space))
def test_whole_project_action_is_tab_reachable_and_handles_keyboard_activation(
    qapp, tmp_path, key
) -> None:
    harmony_settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    harmony_settings.setMode("preview")
    app, engine, editor, playback, keyboard = build_engine(harmony_settings=harmony_settings)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    harmony = engine.rootContext().contextProperty("harmony")
    calls: list[bool] = []
    original_start = harmony._start_analysis_job
    def record_whole_project_start(request, *, whole_project=False):
        calls.append(whole_project)
        harmony._set_whole_project_pending(whole_project)
    harmony._start_analysis_job = record_whole_project_start
    try:
        editor._execute(
            editor.state.add_note(
                Note.create(id="melody", pitch=60, start_time=0.0, duration=0.5)
            )
        )
        qapp.processEvents()
        opener = root.findChild(QQuickItem, "moreMenuButton")
        mode_action = root.findChild(QObject, "moreHarmonyAction")
        picker = root.findChild(QQuickItem, "harmonyModePicker")
        action = root.findChild(QQuickItem, "harmonyGenerateWholeProjectAction")
        assert opener is not None and mode_action is not None
        assert picker is not None and action is not None

        mode_action.triggered.emit()
        qapp.processEvents()
        picker.forceActiveFocus()
        QTest.keyClick(root, Qt.Key_Tab)
        qapp.processEvents()
        assert root.property("activeFocusItem") == action
        assert _accessible_name(action) == "检测整首旋律并在合适的小节生成和弦"
        calls.clear()

        QTest.keyClick(root, key)
        qapp.processEvents()
        assert calls == [True]
        assert action.property("enabled") is False
        assert action.property("text") == "正在检测整首…"
        assert _accessible_description(action) == "正在分析整首旋律，请稍候。"
    finally:
        harmony._start_analysis_job = original_start
        root.close()
        harmony.close()


def test_harmony_mode_popup_sets_only_supported_modes_and_restores_its_opener(qapp) -> None:
    harmony_settings = HarmonySettings()
    app, engine, controller, playback, keyboard = build_engine(harmony_settings=harmony_settings)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    root.setWidth(1366)
    qapp.processEvents()

    opener = root.findChild(QQuickItem, "moreMenuButton")
    mode_action = root.findChild(QObject, "moreHarmonyAction")
    popup = root.findChild(QObject, "harmonyModePopup")
    picker = root.findChild(QQuickItem, "harmonyModePicker")
    assert opener is not None and mode_action is not None
    assert popup is not None
    assert picker is not None
    assert root.findChild(QObject, "harmonyAutoDescription").property("text") == "仅处理已完成的小节"

    opener.forceActiveFocus()
    mode_action.triggered.emit()
    qapp.processEvents()
    assert popup.property("opened") is True
    assert picker.property("model").toVariant() == ["关闭", "预览", "自动"]
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _window_point(root, picker) + QPoint(round(picker.width() / 3), 0),
    )
    qapp.processEvents()
    assert harmony_settings.mode == "auto"

    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert popup.property("opened") is False
    assert opener.property("activeFocus") is True

    opener.forceActiveFocus()
    mode_action.triggered.emit()
    qapp.processEvents()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, root.height() - 4))
    qapp.processEvents()
    assert popup.property("opened") is False
    assert opener.property("activeFocus") is True

    opener.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_H)
    qapp.processEvents()
    assert popup.property("opened") is False
    root.close()
