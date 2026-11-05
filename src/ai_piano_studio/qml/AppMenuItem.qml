import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.MenuItem {
    id: control
    required property var theme
    property bool destructive: false
    readonly property color foregroundColor: !enabled ? theme.textDisabled
        : destructive ? theme.danger : theme.textPrimary
    readonly property color rowColor: down ? theme.surfacePressed
        : highlighted ? theme.surfaceHover : checked ? theme.surfaceSelected : theme.popupBackground
    implicitWidth: 224
    implicitHeight: 34
    leftPadding: checkable ? 32 : 12
    rightPadding: 12
    topPadding: 4
    bottomPadding: 4
    hoverEnabled: true
    font.family: theme.fontUi
    font.pixelSize: 13
    font.weight: theme.weightRegular
    contentItem: Text {
        text: control.text
        font: control.font
        color: control.foregroundColor
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        x: 10
        y: (control.height - height) / 2
        text: "✓"
        visible: control.checkable && control.checked
        color: control.foregroundColor
        font.family: control.theme.fontUi
        font.pixelSize: 15
    }
    background: Rectangle {
        color: control.rowColor
        radius: control.theme.radiusS
        border.width: control.visualFocus ? 2 : 0
        border.color: control.theme.focusRing
    }
}
