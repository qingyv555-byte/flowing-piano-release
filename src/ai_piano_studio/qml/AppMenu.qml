import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Menu {
    id: control
    required property var theme
    popupType: Basic.Popup.Item
    focus: true
    margins: 8
    readonly property string inputScope: "appMenu:" + control.toString()
    onAboutToShow: {
        if (typeof keyboard !== "undefined" && keyboard !== null)
            keyboard.suspendInput(inputScope, true)
    }
    onClosed: {
        if (typeof keyboard !== "undefined" && keyboard !== null)
            keyboard.suspendInput(inputScope, false)
    }
    implicitWidth: 236
    padding: 6
    spacing: 2
    delegate: AppMenuItem { theme: control.theme }
    background: Rectangle {
        color: control.theme.popupBackground
        radius: control.theme.radiusL
        border.color: control.theme.borderStrong
    }
}
