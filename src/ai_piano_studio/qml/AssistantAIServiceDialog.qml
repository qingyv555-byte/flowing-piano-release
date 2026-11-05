import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "assistantAIServiceDialog"
    required property Theme theme
    property var assistant: null
    readonly property bool hasUnsavedConnection: serviceLoader.item !== null && serviceLoader.item.dirty
    function requestClose() {
        if (serviceLoader.item) serviceLoader.item.requestLeave(function() { dialog.close() })
        else close()
    }
    function requestWindowClose(action) {
        if (serviceLoader.item) serviceLoader.item.requestLeave(function() { dialog.close(); action() })
        else { close(); action() }
    }
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.NoAutoClose
    padding: 16
    width: Math.min(900, parent ? parent.width - 32 : 900)
    height: Math.min(700, parent ? parent.height - 32 : 700)
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    font.family: theme.fontUi
    palette.window: theme.popupBackground
    palette.base: theme.inputBackground
    palette.text: theme.textPrimary
    palette.windowText: theme.textPrimary
    palette.buttonText: theme.textPrimary
    palette.highlight: theme.accentPrimary
    palette.highlightedText: theme.textOnAccent
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.18) }
    background: Rectangle { color: dialog.theme.popupBackground; radius: dialog.theme.radiusL; border.color: dialog.theme.borderStrong }
    header: DialogHeader {
        theme: dialog.theme; padding: 16; bottomPadding: 8
        RowLayout {
            Layout.fillWidth: true
            Label { text: "AI 服务"; color: dialog.theme.textPrimary; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
            AppButton { objectName: "assistantAIServiceDismiss"; theme: dialog.theme; text: "×"; variant: "ghost"; implicitWidth: 28; implicitHeight: 28; accessibleName: "关闭 AI 服务"; onClicked: dialog.requestClose() }
        }
    }
    Shortcut {
        sequence: "Escape"
        enabled: dialog.opened && !(serviceLoader.item && serviceLoader.item.guardOpen)
        onActivated: dialog.requestClose()
    }
    ScrollView {
        id: scroll
        objectName: "assistantAIServiceScroll"
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        rightPadding: dialog.theme.scrollbarGutter
        ScrollBar.vertical: SafeScrollBar { theme: dialog.theme }
        ColumnLayout {
            width: scroll.availableWidth
            Loader {
                id: serviceLoader
                Layout.fillWidth: true
                active: dialog.opened
                sourceComponent: Component {
                    StudioAIServicePane { theme: dialog.theme; assistant: dialog.assistant }
                }
            }
        }
    }
    footer: DialogFooter {
        theme: dialog.theme
        Item { Layout.fillWidth: true }
        AppButton { objectName: "assistantAIServiceClose"; theme: dialog.theme; text: "返回聊天"; onClicked: dialog.requestClose() }
    }
}
