import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.MenuSeparator {
    id: control
    required property var theme
    topPadding: 4
    bottomPadding: 4
    leftPadding: 8
    rightPadding: 8
    contentItem: Rectangle { implicitHeight: 1; color: control.theme.borderSubtle }
    background: Item {}
}
