import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "technicalDetailsDialog"
    required property Theme theme
    property string detailTitle: "错误详情"
    property string detailText: ""

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(720, parent ? parent.width - 32 : 720)
    height: Math.min(520, parent ? parent.height - 32 : 520)
    modal: true
    focus: true
    padding: theme.space4
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    function openWithDetails(nextTitle, nextText) {
        detailTitle = nextTitle || "错误详情"
        detailText = nextText || "暂无技术详情。"
        open()
    }

    onOpened: closeButton.forceActiveFocus(Qt.PopupFocusReason)

    background: Rectangle {
        radius: root.theme.radiusL
        color: root.theme.elevatedSurface
        border.color: root.theme.borderStrong
        border.width: 1
    }

    header: DialogHeader {
        theme: root.theme
        spacing: root.theme.space1
        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: root.detailTitle
            color: root.theme.textPrimary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSection
            font.weight: root.theme.weightBold
        }

    }

    contentItem: ScrollView {
        id: detailScroll
        objectName: "technicalDetailsScroll"
        clip: true
        rightPadding: root.theme.scrollbarGutter
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical: SafeScrollBar {
            theme: root.theme
            parent: detailScroll
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            visible: size < 1
        }
        TextArea {
            objectName: "technicalDetailsText"
            width: detailScroll.availableWidth
            text: root.detailText
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            color: root.theme.textPrimary
            selectionColor: root.theme.selectedBackground
            selectedTextColor: root.theme.textPrimary
            font.family: root.theme.numberFont
            font.pixelSize: root.theme.typeSmall
            background: Rectangle {
                color: root.theme.inputBackground
                border.color: root.theme.borderSubtle
                border.width: 1
                radius: root.theme.radiusS
            }
        }
    }

    footer: DialogFooter {
        theme: root.theme
        spacing: root.theme.space2
        Item { Layout.fillWidth: true }
        AppButton {
            id: closeButton
            objectName: "technicalDetailsCloseButton"
            text: "关闭"
            theme: root.theme
            variant: "secondary"
            onClicked: root.close()
        }
    }
}
