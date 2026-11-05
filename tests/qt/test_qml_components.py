from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, Qt
from PySide6.QtGui import QAccessible, QColor
from PySide6.QtTest import QSignalSpy, QTest

from tests.qt.qml_test_utils import load_inline_qml


def test_theme_exposes_approved_semantic_tokens(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import "."
        Item { Theme { objectName: "subject" } }
    """)
    theme = root.findChild(QObject, "subject")

    expected_colors = {
        "appBackground": "#07111E", "canvasBackground": "#091827",
        "surface": "#0B1A2A", "elevatedSurface": "#0E2032",
        "surfaceHover": "#122D43", "surfacePressed": "#081520",
        "surfaceSelected": "#103744", "scrim": "#A6000000",
        "textPrimary": "#DCE8F5", "textSecondary": "#8FA8BF",
        "textMuted": "#8299AE", "textDisabled": "#52697D",
        "textOnAccent": "#061824", "textOnGold": "#261A05",
        "textOnDanger": "#2B0A11", "accentPrimary": "#49D7FF",
        "accentPrimaryHover": "#7BE7FF",
        "accentPrimaryPressed": "#24B4D7",
        "accentMusic": "#F2B84B", "accentSelection": "#7BE7FF",
        "info": "#66D9F4", "success": "#3FD0A8",
        "warning": "#F2B84B", "danger": "#F06A7E",
        "record": "#FF5F83", "focusRing": "#7BE7FF",
        "playingNote": "#9DEBFF", "noteColor1": "#42E7C9",
        "noteColor2": "#F2B84B", "noteColor3": "#34CFFF",
        "noteColor4": "#7AF5D0", "noteColor5": "#7B5CFF",
        "keyWhite": "#C8D5E2", "keyWhiteEdge": "#8FA8BF",
        "keyWhiteHighlight": "#DCE8F5", "keyBlack": "#152230",
        "keyBlackEdge": "#30465A", "keyBlackHighlight": "#415A70",
    }
    for name, value in expected_colors.items():
        assert theme.property(name) == QColor(value), name

    expected_sizes = {
        "typeDisplay": 40, "typePageTitle": 24,
        "typeSection": 18, "typePanelTitle": 15,
        "typeBody": 14, "typeBodyStrong": 14,
        "typeSmall": 12, "typeCaption": 11,
        "lineDisplay": 52, "linePageTitle": 34,
        "lineSection": 27, "linePanelTitle": 24,
        "lineBody": 22, "lineBodyStrong": 22,
        "lineSmall": 18, "lineCaption": 16,
        "weightRegular": 400, "weightSemibold": 600,
        "controlHeight": 36, "primaryControlHeight": 48,
        "iconButtonSize": 36, "topBarHeight": 52, "windowChromeHeight": 40,
        "transportHeight": 64, "statusBarHeight": 28,
        "rulerHeight": 30, "keyWidth": 96,
        "inspectorWidth": 304, "inspectorCollapsedWidth": 44,
        "projectRailWidth": 224, "projectRailCollapsedWidth": 52,
        "contentMaxWidth": 1120, "radiusS": 4,
        "radiusM": 6, "radiusL": 8,
        "motionPress": 90, "motionHover": 140,
        "motionState": 180, "motionPanel": 220,
        "motionModal": 260, "motionToastIn": 200,
        "motionToastOut": 160,
    }
    for name, value in expected_sizes.items():
        assert theme.property(name) == value, name

    assert theme.property("canvas") == theme.property("appBackground")
    assert theme.property("commandHeight") == theme.property("topBarHeight")
    assert theme.property("fontUi") == "Microsoft YaHei"
    assert theme.property("fontFallback") == "Microsoft YaHei"
    assert theme.property("fontSymbol") == "Segoe UI Symbol"
    assert theme.property("numberFont") == "Consolas"
    root.deleteLater()


def test_theme_exposes_music_layer_roles_and_workspace_dimensions(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import "."
        Item {
            Theme { id: appTheme; objectName: "appTheme" }
            property color melodyResolved: appTheme.layerRoleColor("melody")
            property color fallbackResolved: appTheme.layerRoleColor("unknown")
            property color melodyFill: appTheme.layerRoleFill("melody", 0.2)
            property color melodyMuted: appTheme.layerRoleMuted("melody")
        }
    """)
    theme = root.findChild(QObject, "appTheme")

    expected = {
        "layerMelody": "#39C8FF",
        "layerAccompaniment": "#45D6A8",
        "layerBass": "#8B72FF",
        "layerDrums": "#F2A65A",
        "layerOther": "#7F9BB4",
    }
    for name, value in expected.items():
        assert theme.property(name) == QColor(value), name

    # The shipped resizable drawer uses 240px; 184 predates that workflow.
    assert theme.property("arrangementDefaultHeight") == 240
    assert theme.property("arrangementCompactHeight") == 132
    assert theme.property("musicLayerRowHeight") == 34
    assert theme.property("minimumPianoRollWidth") == 640
    assert theme.property("panelEdgeAffordanceWidth") == 36
    assert root.property("melodyResolved") == QColor("#39C8FF")
    assert root.property("fallbackResolved") == QColor("#7F9BB4")
    assert root.property("melodyFill").alphaF() == pytest.approx(0.2, abs=0.01)
    assert root.property("melodyMuted") != root.property("melodyResolved")
    root.deleteLater()


def test_app_button_keyboard_and_blocked_states(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 320; height: 180
            Theme { id: appTheme }
            property int hits: 0
            property int toggleCount: 0
            property bool toggledValue: false
            AppButton {
                objectName: "subject"; theme: appTheme
                text: "创建旋律"; accessibleName: text
                onClicked: root.hits += 1
                onToggled: function(checked) {
                    root.toggleCount += 1
                    root.toggledValue = checked
                }
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    accessible = QAccessible.queryAccessibleInterface(subject)
    assert accessible is not None
    assert accessible.text(QAccessible.Text.Name) == "创建旋律"
    assert not accessible.state().disabled
    subject.forceActiveFocus()
    for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        QTest.keyClick(root, key)
    assert root.property("hits") == 3
    assert subject.property("activeFocusOnTab") is True

    click_position = QPoint(
        int(subject.property("x") + subject.property("width") / 2),
        int(subject.property("y") + subject.property("height") / 2),
    )
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     click_position)
    assert root.property("hits") == 4

    action_interface = accessible.actionInterface()
    assert action_interface is not None
    action_interface.doAction(action_interface.pressAction())
    qapp.processEvents()
    assert root.property("hits") == 5

    subject.setProperty("checkable", True)
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     click_position)
    assert subject.property("checked") is True
    assert root.property("hits") == 6
    assert root.property("toggleCount") == 1
    assert root.property("toggledValue") is True

    width_before_disable = subject.property("width")
    height_before_disable = subject.property("height")
    subject.setProperty("enabled", False)
    qapp.processEvents()
    assert QAccessible.queryAccessibleInterface(subject).state().disabled
    assert subject.property("opacity") == 0.42
    assert subject.property("width") == width_before_disable
    assert subject.property("height") == height_before_disable
    QTest.keyClick(root, Qt.Key_Space)
    assert root.property("hits") == 6
    subject.setProperty("enabled", True)
    subject.setProperty("loading", True)
    QTest.keyClick(root, Qt.Key_Return)
    assert root.property("hits") == 6
    root.close()


def test_app_button_ghost_variant_has_runtime_hover_feedback_and_pointer_cursor(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 320; height: 180
            Theme { id: appTheme; objectName: "appTheme" }
            AppButton {
                objectName: "subject"
                x: 20; y: 20
                theme: appTheme
                variant: "ghost"
                text: "快捷操作"
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    theme = root.findChild(QObject, "appTheme")
    background = subject.findChild(QObject, "buttonBackground")
    hover_handler = subject.findChild(QObject, "buttonHoverHandler")

    assert subject is not None
    assert background is not None
    assert hover_handler is not None
    assert background.property("color") == QColor("transparent")
    assert hover_handler.property("cursorShape") == Qt.CursorShape.PointingHandCursor

    point = QPoint(
        int(subject.property("x") + subject.property("width") / 2),
        int(subject.property("y") + subject.property("height") / 2),
    )
    QTest.mouseMove(root, point)
    qapp.processEvents()
    assert subject.property("hovered") is True
    assert background.property("color") == QColor(theme.property("surfaceHover"))

    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qapp.processEvents()
    assert subject.property("pressed") is True
    assert background.property("color") == QColor(theme.property("surfacePressed"))
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    root.close()


def test_feedback_host_distinguishes_kinds_and_runs_action(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 640; height: 360
            property int actionCallbacks: 0
            property int detailCallbacks: 0
            property int actionSignals: 0
            Theme { id: appTheme; objectName: "appTheme" }
            FeedbackHost {
                id: subject
                objectName: "subject"; anchors.fill: parent; theme: appTheme
                onActionTriggered: root.actionSignals += 1
            }
            function showWarningWithAction() {
                subject.show("warning", "还有 3 处修改未同步", {
                    "actionLabel": "查看修改",
                    "actionCallback": function() { root.actionCallbacks += 1 },
                    "secondaryActionLabel": "查看详情",
                    "secondaryActionCallback": function() { root.detailCallbacks += 1 },
                    "persistent": true,
                    "progress": 0.3
                })
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    theme = root.findChild(QObject, "appTheme")

    assert QMetaObject.invokeMethod(root, "showWarningWithAction", Qt.DirectConnection)
    assert subject.property("kind") == "warning"
    assert subject.property("message") == "还有 3 处修改未同步"
    assert subject.property("actionLabel") == "查看修改"
    assert subject.property("secondaryActionLabel") == "查看详情"
    assert subject.property("persistent") is True
    assert subject.property("progress") == 0.3
    assert subject.property("semanticColor") == theme.property("warning")
    assert subject.property("visible") is True

    accessible = QAccessible.queryAccessibleInterface(subject)
    assert accessible is not None
    assert accessible.text(QAccessible.Text.Name) == "警告：还有 3 处修改未同步"

    action_spy = QSignalSpy(subject.actionTriggered)
    assert QMetaObject.invokeMethod(subject, "triggerAction", Qt.DirectConnection)
    assert root.property("actionCallbacks") == 1
    assert root.property("actionSignals") == 1
    assert action_spy.count() == 1
    assert QMetaObject.invokeMethod(subject, "triggerSecondaryAction", Qt.DirectConnection)
    assert root.property("detailCallbacks") == 1
    assert action_spy.count() == 2

    expected_colors = {
        "info": theme.property("info"),
        "success": theme.property("success"),
        "warning": theme.property("warning"),
        "danger": theme.property("danger"),
    }
    seen_colors = set()
    for kind, color in expected_colors.items():
        assert QMetaObject.invokeMethod(
            subject, "show", Qt.DirectConnection,
            Q_ARG("QVariant", kind),
            Q_ARG("QVariant", "状态已更新"),
            Q_ARG("QVariant", {}),
        )
        assert subject.property("kind") == kind
        assert subject.property("semanticColor") == color
        seen_colors.add(color.name())
    assert len(seen_colors) == 4

    subject.setProperty("kind", "danger")
    assert subject.property("semanticColor") == theme.property("danger")
    root.close()


def test_feedback_host_action_supports_keyboard_activation(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 640; height: 360
            property int actionCallbacks: 0
            Theme { id: appTheme }
            FeedbackHost {
                id: subject
                objectName: "subject"; anchors.fill: parent; theme: appTheme
            }
            function showAction() {
                subject.show("info", "可通过键盘查看修改", {
                    "actionLabel": "查看修改",
                    "actionCallback": function() { root.actionCallbacks += 1 },
                    "persistent": true
                })
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    assert QMetaObject.invokeMethod(root, "showAction", Qt.DirectConnection)

    def find_named_accessible_object(interface, name):
        if interface.text(QAccessible.Text.Name) == name:
            return interface.object()
        for index in range(interface.childCount()):
            match = find_named_accessible_object(interface.child(index), name)
            if match is not None:
                return match
        return None

    host_accessible = QAccessible.queryAccessibleInterface(subject)
    assert host_accessible is not None
    action_control = find_named_accessible_object(host_accessible, "查看修改")
    assert action_control is not None
    assert action_control.property("activeFocusOnTab") is True

    action_spy = QSignalSpy(subject.actionTriggered)
    for expected_callbacks, key in enumerate(
            (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space), start=1):
        action_control.forceActiveFocus()
        assert action_control.property("activeFocus") is True
        QTest.keyClick(root, key)
        assert root.property("actionCallbacks") == expected_callbacks
        assert action_spy.count() == expected_callbacks

    action_control.setProperty("enabled", False)
    action_control.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)
    assert root.property("actionCallbacks") == 3

    action_control.setProperty("enabled", True)
    assert QMetaObject.invokeMethod(subject, "clear", Qt.DirectConnection)
    QTest.keyClick(root, Qt.Key_Space)
    assert root.property("actionCallbacks") == 3
    root.close()


def test_feedback_host_clears_state_and_normalizes_invalid_kind(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true; width: 640; height: 360
            Theme { id: appTheme; objectName: "appTheme" }
            FeedbackHost { objectName: "subject"; anchors.fill: parent; theme: appTheme }
        }
    """)
    subject = root.findChild(QObject, "subject")
    theme = root.findChild(QObject, "appTheme")

    assert QMetaObject.invokeMethod(
        subject, "show", Qt.DirectConnection,
        Q_ARG("QVariant", "unknown"),
        Q_ARG("QVariant", "无法同步"),
        Q_ARG("QVariant", {"actionLabel": "重试", "progress": 0.5}),
    )
    assert subject.property("kind") == "info"
    assert subject.property("semanticColor") == theme.property("info")
    assert subject.property("visible") is True
    assert QMetaObject.invokeMethod(subject, "clear", Qt.DirectConnection)
    assert subject.property("message") == ""
    assert subject.property("actionCallback") is None
    assert subject.property("progress") == -1
    assert subject.property("visible") is False
    root.close()


def test_feedback_host_uses_event_driven_auto_dismiss_policy(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true; width: 640; height: 360
            Theme { id: appTheme; objectName: "appTheme" }
            FeedbackHost { objectName: "subject"; anchors.fill: parent; theme: appTheme }
        }
    """)
    subject = root.findChild(QObject, "subject")

    for kind, interval in (("info", 2400), ("success", 2400),
                           ("warning", 4000), ("danger", 4000)):
        assert QMetaObject.invokeMethod(
            subject, "show", Qt.DirectConnection,
            Q_ARG("QVariant", kind),
            Q_ARG("QVariant", "自动关闭"),
            Q_ARG("QVariant", {}),
        )
        assert subject.property("autoDismissInterval") == interval
        assert subject.property("persistent") is False
        assert subject.property("visible") is True

    assert QMetaObject.invokeMethod(
        subject, "show", Qt.DirectConnection,
        Q_ARG("QVariant", "warning"),
        Q_ARG("QVariant", "持续显示"),
        Q_ARG("QVariant", {"persistent": True}),
    )
    assert subject.property("persistent") is True
    assert subject.property("visible") is True

    message_spy = QSignalSpy(subject.messageChanged)
    assert QMetaObject.invokeMethod(
        subject, "show", Qt.DirectConnection,
        Q_ARG("QVariant", "info"),
        Q_ARG("QVariant", "即将自动关闭"),
        Q_ARG("QVariant", {}),
    )
    assert message_spy.wait(3000)
    assert subject.property("message") == ""
    assert subject.property("visible") is False
    root.close()


def test_technical_details_dialog_keeps_diagnostics_behind_explicit_action(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true; width: 640; height: 360
            Theme { id: appTheme }
            TechnicalDetailsDialog {
                id: subject
                objectName: "subject"
                theme: appTheme
            }
            function showDetails() {
                subject.openWithDetails("保存失败详情", "OSError: backend failure\\nC:/private/project.json")
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    assert QMetaObject.invokeMethod(root, "showDetails", Qt.DirectConnection)
    assert subject.property("opened") is True
    assert subject.property("detailTitle") == "保存失败详情"
    assert "backend failure" in subject.property("detailText")
    close = root.findChild(QObject, "technicalDetailsCloseButton")
    assert close is not None
    assert close.property("text") == "关闭"
    subject.close()
    root.close()


def test_field_and_choice_controls_expose_semantics(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 520; height: 240
            property int fieldAccepted: 0
            property int fieldFinished: 0
            property int activations: 0
            property int lastActivation: -1
            property int toggleCount: 0
            property bool toggledValue: false
            Theme { id: appTheme }
            AppField {
                objectName: "field"; theme: appTheme
                label: "搜索工程"; text: "42"; placeholderText: "输入工程名称"
                errorText: "名称不能为空"; validator: IntValidator { bottom: 1; top: 99 }
                onAccepted: root.fieldAccepted += 1
                onEditingFinished: root.fieldFinished += 1
            }
            SegmentedControl {
                objectName: "segments"; theme: appTheme; y: 90
                model: ["初学者", "进阶", "专业"]; currentIndex: 0
                onActivated: index => { root.activations += 1; root.lastActivation = index }
            }
            SwitchToggle {
                objectName: "toggle"; theme: appTheme; y: 150
                text: "动态背景"; checked: false
                onToggled: checked => {
                    root.toggleCount += 1
                    root.toggledValue = checked
                }
            }
        }
    """)
    field = root.findChild(QObject, "field")
    input_field = root.findChild(QObject, "fieldInput")
    segments = root.findChild(QObject, "segments")
    toggle = root.findChild(QObject, "toggle")

    assert field.property("label") == "搜索工程"
    assert field.property("errorText") == "名称不能为空"
    assert field.property("implicitHeight") == 80
    assert input_field.property("placeholderText") == "输入工程名称"
    assert input_field.property("validator") is not None
    field.focusInput(True)
    qapp.processEvents()
    assert input_field.property("activeFocus") is True
    assert input_field.property("selectedText") == "42"
    field.setProperty("errorText", "")
    qapp.processEvents()
    assert input_field.property("activeFocus") is True
    QTest.keyClick(root, Qt.Key_Return)
    assert root.property("fieldAccepted") == 1
    assert root.property("fieldFinished") == 1
    QTest.keyClick(root, Qt.Key_Tab)
    assert segments.property("activeFocus") is True

    assert segments.property("currentIndex") == 0
    second_position = QPoint(
        int(segments.property("x") + segments.property("width") / 2),
        int(segments.property("y") + segments.property("height") / 2),
    )
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     second_position)
    assert segments.property("currentIndex") == 1
    assert root.property("lastActivation") == 1
    assert root.property("activations") == 1
    QTest.keyClick(root, Qt.Key_Left)
    assert segments.property("currentIndex") == 0
    QTest.keyClick(root, Qt.Key_Right)
    assert segments.property("currentIndex") == 1
    QTest.keyClick(root, Qt.Key_Return)
    assert root.property("lastActivation") == 1
    assert root.property("activations") == 2
    QTest.keyClick(root, Qt.Key_Enter)
    assert root.property("lastActivation") == 1
    assert root.property("activations") == 3
    QTest.keyClick(root, Qt.Key_Right)
    assert segments.property("currentIndex") == 2
    QTest.keyClick(root, Qt.Key_Space)
    assert root.property("lastActivation") == 2
    assert root.property("activations") == 4
    segments.setProperty("enabled", False)
    QTest.keyClick(root, Qt.Key_Left)
    QTest.keyClick(root, Qt.Key_Enter)
    assert segments.property("currentIndex") == 2
    assert root.property("activations") == 4

    accessible = QAccessible.queryAccessibleInterface(toggle)
    assert accessible is not None
    assert accessible.state().checkable
    assert not accessible.state().checked
    assert toggle.property("stateText") == "关"
    toggle_position = QPoint(
        int(toggle.property("x") + toggle.property("width") / 2),
        int(toggle.property("y") + toggle.property("height") / 2),
    )
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     toggle_position)
    assert toggle.property("checked") is True
    assert root.property("toggleCount") == 1
    assert root.property("toggledValue") is True
    QTest.keyClick(root, Qt.Key_Return)
    assert toggle.property("checked") is False
    assert root.property("toggleCount") == 2
    assert root.property("toggledValue") is False
    QTest.keyClick(root, Qt.Key_Enter)
    assert toggle.property("checked") is True
    assert root.property("toggleCount") == 3
    assert root.property("toggledValue") is True
    QTest.keyClick(root, Qt.Key_Space)
    assert toggle.property("checked") is False
    assert toggle.property("stateText") == "关"
    assert not QAccessible.queryAccessibleInterface(toggle).state().checked
    toggle.setProperty("enabled", False)
    QTest.keyClick(root, Qt.Key_Return)
    assert toggle.property("checked") is False
    assert root.property("toggleCount") == 4
    root.close()


def test_styled_slider_preserves_signals_and_has_stable_focus_ring(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 220; height: 80
            property int editedCount: 0
            property int committedCount: 0
            Theme { id: appTheme }
            StyledSlider {
                objectName: "slider"; theme: appTheme
                from: 0; to: 10; stepSize: 1; modelValue: 5
                onValueEdited: root.editedCount += 1
                onValueCommitted: root.committedCount += 1
            }
        }
    """)
    slider = root.findChild(QObject, "slider")
    slider.setProperty("modelValue", 4)
    qapp.processEvents()
    slider.setProperty("modelValue", 5)
    qapp.processEvents()

    assert slider.property("value") == 5
    assert slider.property("implicitHeight") == 36
    assert slider.clamp(-1) == 0
    assert slider.clamp(11) == 10
    assert slider.valueAt(-1) == 0
    assert slider.valueAt(slider.property("width") + 1) == 10
    slider.forceActiveFocus()
    qapp.processEvents()
    assert slider.property("activeFocus") is True
    assert slider.property("height") == 36
    QTest.keyClick(root, Qt.Key_Right)
    assert slider.property("value") == 6
    assert root.property("editedCount") == 1
    assert root.property("committedCount") == 1
    QTest.keyClick(root, Qt.Key_Left)
    assert slider.property("value") == 5
    assert root.property("editedCount") == 2
    assert root.property("committedCount") == 2

    slider.previewValue(7.4)
    assert slider.property("value") == 7
    assert root.property("editedCount") == 3
    assert root.property("committedCount") == 2
    slider.setProperty("enabled", False)
    QTest.keyClick(root, Qt.Key_Right)
    assert slider.property("value") == 7
    assert root.property("committedCount") == 2
    root.close()


def test_project_row_modes_keep_stable_actions(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true; width: 760; height: 220
            Theme { id: appTheme }
            ProjectRow {
                objectName: "subject"; theme: appTheme; width: 680
                entry: ({name: "晨光旋律", path: "C:/music/demo.pianoai.json",
                    exists: true, noteCount: 12, durationSeconds: 8,
                    sceneTheme: "cosmic-pulse", lastOpened: "刚刚"})
                mode: "full"; current: true
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    assert subject.property("implicitHeight") >= 64
    assert subject.property("current") is True
    assert subject.property("accessibleName") == "当前工程：晨光旋律"
    subject.setProperty("mode", "compact")
    qapp.processEvents()
    assert subject.property("implicitWidth") == 36
    root.close()


def test_project_row_routes_actions_and_blocks_unavailable_entries(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 760; height: 160
            Theme { id: appTheme }
            ProjectRow {
                objectName: "subject"; theme: appTheme
                x: 20; y: 20; width: 680
                entry: ({name: "测试工程", path: "C:/music/demo.pianoai.json",
                    exists: true, noteCount: 12, durationSeconds: 8,
                    sceneTheme: "cosmic-pulse", lastOpened: "刚刚"})
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    activated = QSignalSpy(subject.activated)
    selection_toggled = QSignalSpy(subject.selectionToggled)
    menu_requested = QSignalSpy(subject.menuRequested)

    body_position = QPoint(
        int(subject.property("x") + 40),
        int(subject.property("y") + subject.property("height") / 2),
    )
    action_position = QPoint(
        int(subject.property("x") + subject.property("width") - 30),
        int(subject.property("y") + subject.property("height") / 2),
    )

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     body_position)
    assert activated.count() == 1
    assert selection_toggled.count() == 0
    assert menu_requested.count() == 0

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     action_position)
    assert activated.count() == 1
    assert menu_requested.count() == 1

    width_before_managing = subject.property("width")
    implicit_width_before_managing = subject.property("implicitWidth")
    subject.setProperty("managing", True)
    subject.setProperty("selected", False)
    qapp.processEvents()
    assert subject.property("width") == width_before_managing
    assert subject.property("implicitWidth") == implicit_width_before_managing

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     body_position)
    assert selection_toggled.count() == 1
    assert subject.property("selected") is True
    assert menu_requested.count() == 1

    subject.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Space)
    assert selection_toggled.count() == 2
    assert subject.property("selected") is False
    QTest.keyClick(root, Qt.Key_Return)
    assert activated.count() == 2

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     action_position)
    assert selection_toggled.count() == 3
    assert menu_requested.count() == 1

    subject.setProperty("managing", False)
    subject.setProperty("entry", {
        "name": "已移动工程", "path": "C:/music/missing.pianoai.json",
        "exists": False, "noteCount": 0, "durationSeconds": 0,
        "sceneTheme": "", "lastOpened": "昨天",
    })
    qapp.processEvents()
    subject.forceActiveFocus()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     body_position)
    QTest.keyClick(root, Qt.Key_Return)
    QTest.keyClick(root, Qt.Key_Space)
    assert activated.count() == 2

    subject.setProperty("blocked", True)
    subject.setProperty("managing", True)
    subject.setProperty("entry", {
        "name": "受阻工程", "path": "C:/music/blocked.pianoai.json",
        "exists": True, "noteCount": 1, "durationSeconds": 1,
        "sceneTheme": "cosmic-pulse", "lastOpened": "刚刚",
    })
    qapp.processEvents()
    subject.forceActiveFocus()
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     body_position)
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     action_position)
    QTest.keyClick(root, Qt.Key_Return)
    QTest.keyClick(root, Qt.Key_Space)
    assert activated.count() == 2
    assert selection_toggled.count() == 3
    assert menu_requested.count() == 1
    root.close()


def test_project_row_action_slot_supports_tab_and_keyboard_activation(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            id: root
            visible: true; width: 760; height: 160
            Theme { id: appTheme }
            ProjectRow {
                objectName: "subject"; theme: appTheme
                x: 20; y: 20; width: 680
                entry: ({name: "测试工程", path: "C:/music/demo.pianoai.json",
                    exists: true, noteCount: 12, durationSeconds: 8,
                    sceneTheme: "cosmic-pulse", lastOpened: "刚刚"})
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    action = root.findChild(QObject, "projectRowAction")
    assert action is not None
    assert action.property("width") == 36
    assert action.property("height") == 36
    assert action.property("activeFocusOnTab") is True
    accessible = QAccessible.queryAccessibleInterface(action)
    assert accessible is not None
    assert accessible.role() == QAccessible.Role.PushButton
    assert accessible.text(QAccessible.Text.Name) == "更多操作"

    activated = QSignalSpy(subject.activated)
    selection_toggled = QSignalSpy(subject.selectionToggled)
    menu_requested = QSignalSpy(subject.menuRequested)

    subject.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Tab)
    assert action.property("activeFocus") is True
    QTest.keyClick(root, Qt.Key_Return)
    QTest.keyClick(root, Qt.Key_Space)
    assert activated.count() == 0
    assert selection_toggled.count() == 0
    assert menu_requested.count() == 2

    subject.setProperty("managing", True)
    accessible = QAccessible.queryAccessibleInterface(action)
    assert accessible is not None
    assert accessible.state().checkable
    assert not accessible.state().checked
    action.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Enter)
    qapp.processEvents()
    assert subject.property("selected") is True
    assert QAccessible.queryAccessibleInterface(action).state().checked
    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert activated.count() == 0
    assert selection_toggled.count() == 2
    assert menu_requested.count() == 2
    assert subject.property("selected") is False
    assert not QAccessible.queryAccessibleInterface(action).state().checked

    subject.setProperty("blocked", True)
    action.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)
    QTest.keyClick(root, Qt.Key_Space)
    assert activated.count() == 0
    assert selection_toggled.count() == 2
    assert menu_requested.count() == 2
    root.close()


def test_app_icon_button_has_stable_hit_target(qapp) -> None:
    engine, root = load_inline_qml(qapp, """
        import QtQuick
        import QtQuick.Controls
        import "."
        ApplicationWindow {
            visible: true
            Theme { id: appTheme }
            AppIconButton {
                objectName: "subject"; theme: appTheme
                accessibleName: "返回"; tooltip: "返回"
            }
        }
    """)
    subject = root.findChild(QObject, "subject")
    assert subject.property("width") == 36
    assert subject.property("height") == 36
    assert subject.property("accessibleName") == "返回"
    root.close()
