import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "externalSaveChoiceDialog"

    required property var theme
    property string externalPath: ""
    signal saveToLibraryRequested()
    signal keepOriginalRequested()

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 480
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: theme.space5

    onOpened: Qt.callLater(function() {
        externalSaveLibraryButton.forceActiveFocus()
    })

    background: Rectangle {
        radius: root.theme.radiusL
        color: root.theme.elevatedSurface
        border.color: root.theme.borderStrong
        border.width: 1
    }

    header: Label {
        leftPadding: root.theme.space5
        rightPadding: root.theme.space5
        topPadding: root.theme.space5
        text: "选择保存位置"
        color: root.theme.textPrimary
        font.family: root.theme.fontUi
        font.pixelSize: root.theme.typeSection
        font.weight: root.theme.weightBold
    }

    contentItem: ColumnLayout {
        spacing: root.theme.space3

        Label {
            Layout.fillWidth: true
            text: "保存到工程库，或继续保留在原位置。"
            color: root.theme.textSecondary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeBody
            wrapMode: Text.WordWrap
        }
        Label {
            Layout.fillWidth: true
            text: root.externalPath.length > 0 ? root.externalPath : "外部工程"
            color: root.theme.textMuted
            font.family: root.theme.numberFont
            font.pixelSize: root.theme.typeCaption
            elide: Text.ElideMiddle
        }
    }

    footer: Item {
        implicitHeight: root.theme.controlHeight + root.theme.space6

        RowLayout {
            anchors.fill: parent
            anchors.margins: root.theme.space3
            spacing: root.theme.space2

            Item { Layout.fillWidth: true }

            AppButton {
                objectName: "externalSaveCancelButton"
                theme: root.theme
                text: "取消"
                onClicked: root.close()
            }
            AppButton {
                objectName: "externalSaveOriginalButton"
                theme: root.theme
                text: "保留原位置"
                onClicked: {
                    root.keepOriginalRequested()
                    root.close()
                }
            }
            AppButton {
                id: externalSaveLibraryButton
                objectName: "externalSaveLibraryButton"
                theme: root.theme
                text: "保存到工程库"
                variant: "primary"
                onClicked: {
                    root.saveToLibraryRequested()
                    root.close()
                }
            }
        }
    }
}
