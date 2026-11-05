import QtQuick

Item {
    id: control

    required property var theme
    property string text: ""
    property bool checked: false
    readonly property string stateText: checked ? "开" : "关"
    signal toggled(bool checked)

    implicitWidth: label.implicitWidth + theme.space3 + stateLabel.implicitWidth
        + theme.space2 + track.width
    implicitHeight: theme.controlHeight
    width: implicitWidth
    height: implicitHeight
    activeFocusOnTab: true
    Accessible.role: Accessible.CheckBox
    Accessible.name: text
    Accessible.checkable: true
    Accessible.checked: checked
    Accessible.onPressAction: toggle()

    function toggle() {
        if (!enabled)
            return
        checked = !checked
        toggled(checked)
    }

    Keys.onPressed: event => {
        if (!enabled || event.isAutoRepeat)
            return
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                || event.key === Qt.Key_Space) {
            event.accepted = true
            toggle()
        }
    }

    Text {
        id: label
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: control.text
        color: control.enabled ? control.theme.textPrimary : control.theme.textDisabled
        font.family: control.theme.fontUi
        font.pixelSize: control.theme.typeBody
        verticalAlignment: Text.AlignVCenter
    }

    Text {
        id: stateLabel
        objectName: "switchStateText"
        anchors.left: label.right
        anchors.leftMargin: control.theme.space3
        anchors.verticalCenter: parent.verticalCenter
        text: control.stateText
        color: control.enabled ? control.theme.textSecondary : control.theme.textDisabled
        font.family: control.theme.fontUi
        font.pixelSize: control.theme.typeSmall
    }

    Rectangle {
        id: track
        width: 32
        height: 18
        radius: height / 2
        anchors.left: stateLabel.right
        anchors.leftMargin: control.theme.space2
        anchors.verticalCenter: parent.verticalCenter
        color: control.checked ? control.theme.accentPrimary : control.theme.surfaceHover
        border.width: 1
        border.color: control.checked ? control.theme.accentPrimary : control.theme.borderStrong

        Rectangle {
            width: 14
            height: 14
            radius: width / 2
            x: control.checked ? parent.width - width - 2 : 2
            anchors.verticalCenter: parent.verticalCenter
            color: control.checked ? control.theme.textOnAccent : control.theme.textPrimary
            Behavior on x { NumberAnimation { duration: control.theme.motionState } }
        }
    }

    MouseArea {
        anchors.fill: parent
        enabled: control.enabled
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            control.forceActiveFocus(Qt.MouseFocusReason)
            control.toggle()
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 1
        color: "transparent"
        radius: control.theme.radiusM - 1
        border.width: control.activeFocus ? 2 : 0
        border.color: control.theme.focusRing
    }
}
