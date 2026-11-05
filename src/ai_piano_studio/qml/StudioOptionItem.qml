import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

CheckDelegate {
    id: option
    required property Theme theme
    property string detail: ""
    Layout.fillWidth: true
    implicitHeight: 34
    padding: 8
    Accessible.description: detail
    ToolTip.visible: detail.length > 0 && (hovered || activeFocus)
    ToolTip.delay: 500
    ToolTip.text: detail
    indicator: Text {
        x: 10; y: (option.height - height) / 2
        text: "✓"; visible: option.checked
        color: option.theme.accentPrimary
        font.family: option.theme.fontUi; font.pixelSize: 16; font.bold: true
    }
    contentItem: Text {
        leftPadding: 26
        text: option.text; color: option.theme.textPrimary
        font.family: option.theme.fontUi; font.pixelSize: 13
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: option.hovered ? option.theme.surfaceHover : option.checked ? option.theme.surfaceSelected : "transparent"
        border.color: option.activeFocus ? option.theme.focusRing : "transparent"
        radius: 6
    }
}
