import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Basic.Popup {
    id: dialog
    required property var theme
    property string title: "选择颜色"
    property alias selectedColor: picker.selectedColor
    property bool allowReset: false
    signal accepted()
    signal rejected()
    signal resetRequested()
    readonly property string inputScope: "colorDialog:" + dialog.toString()
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(368, parent ? parent.width - 32 : 368)
    height: Math.min(body.implicitHeight + padding * 2, parent ? parent.height - 32 : 500)
    padding: 20
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onOpened: picker.advanced = false
    onAboutToShow: {
        if (typeof keyboard !== "undefined" && keyboard !== null)
            keyboard.suspendInput(inputScope, true)
    }
    onClosed: {
        if (typeof keyboard !== "undefined" && keyboard !== null)
            keyboard.suspendInput(inputScope, false)
    }
    background: Rectangle {
        color: dialog.theme.popupBackground
        radius: dialog.theme.radiusL
        border.color: dialog.theme.borderStrong
    }
    contentItem: Flickable {
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ColumnLayout {
            id: body
            width: parent.width
            spacing: 16
            Text {
                text: dialog.title
                Layout.fillWidth: true
                color: dialog.theme.textPrimary
                font.family: dialog.theme.fontUi
                font.pixelSize: 14
                font.weight: dialog.theme.weightSemibold
            }
            AppColorPicker { id: picker; theme: dialog.theme; Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                AppButton {
                    theme: dialog.theme; text: "恢复默认"; variant: "ghost"
                    visible: dialog.allowReset
                    labelPixelSize: 12; labelWeight: dialog.theme.weightRegular
                    onClicked: { dialog.resetRequested(); dialog.close() }
                }
                Item { Layout.fillWidth: true }
                AppButton { theme: dialog.theme; text: "取消"; variant: "ghost"; onClicked: { dialog.rejected(); dialog.close() } }
                AppButton {
                    objectName: "colorApplyButton"
                    theme: dialog.theme; text: "应用"; variant: "primary"
                    onClicked: { dialog.accepted(); dialog.close() }
                }
            }
        }
        ScrollBar.vertical: Basic.ScrollBar { policy: ScrollBar.AsNeeded }
    }
}
