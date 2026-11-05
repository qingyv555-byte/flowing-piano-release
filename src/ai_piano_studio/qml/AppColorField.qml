import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: control
    required property var theme
    property string text: "#53a79b"
    property string label: "颜色"
    property bool allowReset: false
    signal editingFinished()
    spacing: 4
    implicitWidth: 240
    Text {
        text: control.label
        color: control.theme.textSecondary
        font.family: control.theme.fontUi
        font.pixelSize: 12
    }
    AppButton {
        objectName: "colorFieldButton"
        theme: control.theme
        Layout.fillWidth: true
        text: "选择颜色…"
        labelPixelSize: 13; labelWeight: control.theme.weightRegular
        leftPadding: 36
        Rectangle {
            x: 12; anchors.verticalCenter: parent.verticalCenter
            width: 18; height: 18; radius: 4
            color: /^#[0-9A-Fa-f]{6}$/.test(control.text) ? control.text : control.theme.layerOther
            border.color: control.theme.borderStrong
        }
        onClicked: {
            picker.selectedColor = /^#[0-9A-Fa-f]{6}$/.test(control.text) ? control.text : control.theme.layerOther
            picker.open()
        }
    }
    AppColorDialog {
        id: picker
        theme: control.theme
        title: control.label
        allowReset: control.allowReset
        onAccepted: { control.text = selectedColor.toString(); control.editingFinished() }
        onResetRequested: { control.text = ""; control.editingFinished() }
    }
}
