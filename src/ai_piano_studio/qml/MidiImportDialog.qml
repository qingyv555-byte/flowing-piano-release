import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "midiImportDialog"
    required property Theme theme
    required property var importer
    required property var editor
    property string sourceUrl: ""
    property string importMode: "append"

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, parent ? parent.width - 32 : 520)
    modal: true
    focus: true
    padding: theme.space5
    closePolicy: Popup.CloseOnEscape

    function openForFile(url) {
        sourceUrl = url.toString()
        if (!root.importer.prepare(sourceUrl))
            return
        importMode = "append"
        root.open()
    }

    onOpened: cancelButton.forceActiveFocus(Qt.PopupFocusReason)
    onClosed: root.importer.cancel()

    background: Rectangle {
        radius: theme.radiusL
        color: theme.elevatedSurface
        border.color: theme.borderStrong
        border.width: 1
    }

    header: DialogHeader {
        theme: root.theme
        spacing: theme.space1

        Label {
            text: "导入 MIDI"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSection
            font.weight: theme.weightBold
        }
        Label {
            text: root.importer.sourceName
            color: theme.textSecondary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSmall
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }
    }

    contentItem: ColumnLayout {
        spacing: theme.space3

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 82
            radius: theme.radiusM
            color: theme.surface
            border.color: theme.borderSubtle

            GridLayout {
                anchors.fill: parent
                anchors.margins: theme.space3
                columns: 2
                rowSpacing: theme.space1
                columnSpacing: theme.space4

                Label { text: "音符"; color: theme.textMuted; font.pixelSize: theme.typeSmall }
                Label { text: root.importer.noteCount; color: theme.textPrimary; font.pixelSize: theme.typeBody; font.weight: theme.weightBold }
                Label { text: "轨道"; color: theme.textMuted; font.pixelSize: theme.typeSmall }
                Label {
                    text: root.importer.noteTrackCount + " / " + root.importer.trackCount
                    color: theme.textPrimary
                    font.pixelSize: theme.typeBody
                    font.weight: theme.weightBold
                }
                Label { text: "速度 / 拍号"; color: theme.textMuted; font.pixelSize: theme.typeSmall }
                Label {
                    text: root.importer.bpm + " BPM · " + root.importer.beatsPerBar + "/" + root.importer.beatUnit
                    color: theme.textPrimary
                    font.pixelSize: theme.typeBody
                }
            }
        }

        Label {
            text: "导入位置"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSmall
            font.weight: theme.weightBold
        }

        RowLayout {
            spacing: theme.space2
            Layout.fillWidth: true

            AppButton {
                objectName: "midiAppendButton"
                theme: root.theme
                text: "添加到当前工程"
                checkable: true
                checkedExternallyControlled: true
                checked: root.importMode === "append"
                Layout.fillWidth: true
                onToggled: if (checked) root.importMode = "append"
            }
            AppButton {
                objectName: "midiReplaceButton"
                theme: root.theme
                text: "替换当前音符"
                checkable: true
                checkedExternallyControlled: true
                checked: root.importMode === "replace"
                Layout.fillWidth: true
                onToggled: if (checked) root.importMode = "replace"
            }
        }

        Label {
            visible: root.importMode === "replace"
            text: "替换会删除当前工程中的音符，可用 Ctrl+Z 撤销。"
            color: theme.warning
            font.pixelSize: theme.typeCaption
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Rectangle {
            visible: root.importer.warnings.length > 0
            Layout.fillWidth: true
            implicitHeight: warningText.implicitHeight + theme.space3 * 2
            radius: theme.radiusM
            color: theme.surface
            border.color: theme.warning

            Label {
                id: warningText
                anchors.fill: parent
                anchors.margins: theme.space3
                text: root.importer.warnings.join("\n")
                color: theme.textPrimary
                font.pixelSize: theme.typeCaption
                wrapMode: Text.WordWrap
            }
        }
    }

    footer: DialogFooter {
        theme: root.theme
        spacing: theme.space2

        Item { Layout.fillWidth: true }
        AppButton {
            id: cancelButton
            objectName: "midiImportCancelButton"
            theme: root.theme
            text: "取消"
            onClicked: root.close()
        }
        AppButton {
            objectName: "midiImportConfirmButton"
            theme: root.theme
            text: "导入"
            variant: "primary"
            enabled: root.importer.ready && (root.importer.noteCount > 0 || root.importer.controlCount > 0)
            onClicked: {
                if (root.importer.importResult(root.importMode))
                    root.close()
            }
        }
    }
}
