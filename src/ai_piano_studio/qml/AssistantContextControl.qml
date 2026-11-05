import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AppButton {
    id: control
    required property var assistant
    property real panelWidth: 320
    objectName: "assistantContextButton"
    readonly property int characterLimit: assistant ? assistant.contextCharacters : 0
    text: characterLimit === 0 ? "上下文：全部" : "上下文：" + characterLimit.toLocaleString(Qt.locale(), 'f', 0) + " 字符"
    accessibleName: "调整历史上下文字符数"
    variant: "ghost"
    implicitHeight: 28
    labelPixelSize: 12
    labelWeight: Font.Normal
    ToolTip.visible: hovered
    ToolTip.delay: 350
    ToolTip.text: "调整带入下一次请求的历史消息，可输入字符数或拖动滑动条"
    onClicked: settings.opened ? settings.close() : settings.open()

    Popup {
        id: settings
        objectName: "assistantContextPopup"
        width: Math.min(320, control.panelWidth)
        x: Math.min(0, control.panelWidth - control.x - width)
        y: -height - 8
        padding: 16
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: amount.text = String(control.characterLimit)
        onAboutToShow: keyboard.suspendInput(objectName, true)
        onClosed: keyboard.suspendInput(objectName, false)
        background: Rectangle {
            color: control.theme.popupBackground
            border.color: control.theme.borderStrong
            radius: 10
        }
        contentItem: ColumnLayout {
            spacing: 10
            Label { text: "历史上下文"; color: control.theme.textPrimary; font.pixelSize: 14; font.bold: true }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: amount
                    objectName: "assistantContextCharactersInput"
                    Layout.fillWidth: true
                    text: String(control.characterLimit)
                    selectByMouse: true
                    inputMethodHints: Qt.ImhDigitsOnly
                    validator: IntValidator { bottom: 0; top: 2147483647 }
                    color: control.theme.textPrimary
                    font.pixelSize: 14
                    Accessible.name: "历史上下文字符数，零表示全部"
                    background: Rectangle { radius: 6; color: control.theme.inputBackground; border.color: amount.activeFocus ? control.theme.focusRing : control.theme.borderStrong }
                    onEditingFinished: {
                        if (acceptableInput) control.assistant.setContextCharacters(Number(text))
                        text = String(control.characterLimit)
                    }
                }
                Label { text: "字符"; color: control.theme.textSecondary; font.pixelSize: 12 }
            }
            Slider {
                id: budget
                objectName: "assistantContextCharactersSlider"
                Layout.fillWidth: true
                from: 0
                to: Math.max(200000, control.characterLimit)
                stepSize: 1000
                value: control.characterLimit
                Accessible.name: "拖动调整历史上下文字符数"
                onMoved: {
                    control.assistant.setContextCharacters(Math.round(value))
                    amount.text = String(control.characterLimit)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "0 · 全部"; color: control.theme.textMuted; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Label { text: budget.to.toLocaleString(Qt.locale(), 'f', 0) + " 字符"; color: control.theme.textMuted; font.pixelSize: 11 }
            }
            Label {
                Layout.fillWidth: true
                text: "保留最近的历史消息，0 表示全部。下次发送生效；本次输入、工程信息和执行中的工具结果不计入。字符数不等于模型的 Token 数。"
                wrapMode: Text.Wrap
                color: control.theme.textSecondary
                font.pixelSize: 12
            }
        }
    }
}
