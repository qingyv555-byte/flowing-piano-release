import QtQuick
import QtQuick.Controls

AppButton {
    id: control

    required property string tooltip
    text: ""
    size: "default"
    implicitWidth: theme.iconButtonSize
    implicitHeight: theme.iconButtonSize
    width: theme.iconButtonSize
    height: theme.iconButtonSize

    ToolTip.visible: hovered && tooltip.length > 0
    ToolTip.delay: 400
    ToolTip.text: tooltip
}
