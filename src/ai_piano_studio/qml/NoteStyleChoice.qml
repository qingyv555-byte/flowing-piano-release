import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.AbstractButton {
    id: choice
    required property var theme
    required property string styleKey
    implicitWidth: 128
    implicitHeight: 68
    hoverEnabled: true
    Accessible.role: Accessible.RadioButton
    Accessible.name: text
    Accessible.checked: checked
    background: Rectangle {
        radius: choice.theme.radiusL
        color: choice.checked ? choice.theme.surfaceSelected : choice.hovered ? choice.theme.surfaceHover : choice.theme.surface
        border.width: choice.checked || choice.activeFocus ? 2 : 1
        border.color: choice.checked || choice.activeFocus ? choice.theme.accentPrimary : choice.theme.borderStrong
    }
    contentItem: Item {
        Row {
            x: 12; y: 14; spacing: 6
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    width: [26, 34, 26][index]; height: 10
                    radius: choice.styleKey === "rounded" ? 5 : 1
                    color: choice.styleKey === "velocity" ? choice.theme.mixColor(choice.theme.notePitchMid,
                        choice.theme.pianoRollBackground, [0.34, 0.16, 0][index]) : choice.theme.notePitchMid
                }
            }
        }
        Text {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: 10
            text: choice.text
            color: choice.theme.textPrimary
            font.family: choice.theme.fontUi
            font.pixelSize: 12
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }
}
