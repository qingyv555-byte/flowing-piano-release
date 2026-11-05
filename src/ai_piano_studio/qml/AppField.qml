import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic

Item {
    id: control

    required property var theme
    property string label: ""
    property string errorText: ""
    property alias text: input.text
    property alias placeholderText: input.placeholderText
    property alias validator: input.validator
    property alias readOnly: input.readOnly
    property bool wheelScrollEnabled: false

    signal accepted()
    signal editingFinished()
    signal wheelScrollRequested(real delta)

    implicitWidth: 240
    implicitHeight: (label.length > 0 ? theme.lineSmall + theme.space1 : 0) + theme.controlHeight
        + (errorText.length > 0 ? theme.space1 + theme.lineSmall : 0)
    width: implicitWidth
    height: implicitHeight

    function focusInput(selectAllText) {
        input.forceActiveFocus(Qt.OtherFocusReason)
        if (selectAllText)
            input.selectAll()
    }

    function clearInputFocus() {
        input.focus = false
    }

    Label {
        width: parent.width
        height: control.theme.lineSmall
        text: control.label
        visible: control.label.length > 0
        color: control.theme.textSecondary
        font.family: control.theme.fontUi
        font.pixelSize: control.theme.typeSmall
        verticalAlignment: Text.AlignVCenter
    }

    Basic.TextField {
        id: input
        objectName: "fieldInput"
        y: control.label.length > 0 ? control.theme.lineSmall + control.theme.space1 : 0
        width: parent.width
        height: control.theme.controlHeight
        leftPadding: 12
        rightPadding: 12
        color: control.theme.textPrimary
        placeholderTextColor: control.theme.textMuted
        font.family: control.theme.fontUi
        font.pixelSize: control.theme.typeBody
        selectByMouse: true
        Accessible.name: control.label
        onAccepted: control.accepted()
        onEditingFinished: control.editingFinished()

        background: Item {
            Rectangle {
                anchors.fill: parent
                radius: control.theme.radiusM
                color: input.enabled ? control.theme.elevatedSurface : control.theme.surface
                border.width: 1
                border.color: input.activeFocus ? control.theme.accentPrimary
                    : control.theme.borderStrong
            }

            Rectangle {
                anchors.fill: parent
                anchors.margins: 1
                radius: control.theme.radiusM - 1
                color: "transparent"
                border.width: input.activeFocus ? 2 : 0
                border.color: control.theme.focusRing
            }
        }

        WheelHandler {
            id: inputWheelHandler
            enabled: control.wheelScrollEnabled
            grabPermissions: PointerHandler.CanTakeOverFromAnything
            onWheel: wheel => {
                var delta = wheel.angleDelta.y !== 0
                    ? wheel.angleDelta.y : wheel.pixelDelta.y
                if (delta === 0)
                    return
                control.wheelScrollRequested(delta)
                wheel.accepted = true
            }
        }
    }

    Label {
        x: 0
        y: input.y + input.height + control.theme.space1
        width: parent.width
        height: control.theme.lineSmall
        text: control.errorText
        visible: control.errorText.length > 0
        color: control.theme.danger
        font.family: control.theme.fontUi
        font.pixelSize: control.theme.typeSmall
        verticalAlignment: Text.AlignVCenter
    }

}
