import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: popup
    required property Theme theme
    default property alias options: optionColumn.data
    readonly property int count: optionColumn.children.length
    function itemAt(index) { return optionColumn.children[index] || null }
    implicitWidth: 208
    padding: 6
    modal: false
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
    onAboutToShow: keyboard.suspendInput(objectName, true)
    onClosed: keyboard.suspendInput(objectName, false)
    background: Rectangle {
        color: popup.theme.popupBackground
        radius: 10
        border.color: popup.theme.borderStrong
    }
    contentItem: ColumnLayout { id: optionColumn; spacing: 2 }
}
