import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Pane {
    id: card
    objectName: "songSourceConfirmationCard"
    required property Theme theme
    property var source: ({})
    signal confirmed()
    signal rejected()
    property string copiedLink: ""
    readonly property string pageUrl: {
        var items = source.evidence || []
        for (var i = 0; i < items.length; ++i) {
            var url = items[i].url || ""
            if (url && url !== source.selectedUrl)
                return url
        }
        return ""
    }
    onSourceChanged: copiedLink = ""
    function copyLink(url) {
        if (!url)
            return
        clipboardText.text = url
        clipboardText.selectAll()
        clipboardText.copy()
        clipboardText.deselect()
        copiedLink = url
        copyFeedback.restart()
    }
    TextEdit { id: clipboardText; visible: false; textFormat: Text.PlainText; readOnly: true }
    Timer { id: copyFeedback; interval: 1800; onTriggered: card.copiedLink = "" }
    padding: 12
    background: Rectangle {
        color: card.theme.elevatedSurface
        radius: card.theme.radiusL
        border.color: card.theme.borderStrong
    }
    contentItem: ColumnLayout {
        spacing: 8
        Label {
            Layout.fillWidth: true
            text: "先核对是不是这首歌"
            textFormat: Text.PlainText
            color: card.theme.textPrimary
            font.family: card.theme.fontUi
            font.pixelSize: 14
            font.weight: card.theme.weightSemibold
        }
        ScrollView {
            id: details
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(body.implicitHeight, 148)
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                id: body
                width: details.availableWidth
                spacing: 6
                Label {
                    objectName: "songSourceIdentity"
                    Layout.fillWidth: true
                    text: "《" + (card.source.title || "") + "》 · " + (card.source.artist || "歌手待核对")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: card.theme.textPrimary
                    font.family: card.theme.fontUi
                    font.pixelSize: 13
                }
                TextEdit {
                    objectName: "songSourceSelectedResource"
                    Layout.fillWidth: true
                    Layout.preferredHeight: implicitHeight
                    text: "选定乐谱资源：\n" + (card.source.selectedUrl || "")
                    textFormat: Text.PlainText
                    wrapMode: TextEdit.WrapAnywhere
                    readOnly: true
                    selectByMouse: true
                    color: card.theme.textSecondary
                    font.family: card.theme.fontUi
                    font.pixelSize: 12
                }
                Label {
                    Layout.fillWidth: true
                    text: "版本：" + (card.source.edition || "待核对") + "\n段落：" + (card.source.section || "待核对")
                          + " · 约 " + (card.source.durationSeconds || 15) + " 秒"
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: card.theme.textSecondary
                    font.family: card.theme.fontUi
                    font.pixelSize: 12
                }
                Label {
                    Layout.fillWidth: true
                    text: card.source.summary || ""
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: card.theme.textSecondary
                    font.family: card.theme.fontUi
                    font.pixelSize: 12
                }
                Repeater {
                    model: card.source.evidence || []
                    TextEdit {
                        required property var modelData
                        objectName: "songSourceEvidence"
                        Layout.fillWidth: true
                        Layout.preferredHeight: implicitHeight
                        text: modelData.quote + "\n" + modelData.url
                        textFormat: Text.PlainText
                        wrapMode: TextEdit.WrapAnywhere
                        readOnly: true
                        selectByMouse: true
                        color: card.theme.textSecondary
                        font.family: card.theme.fontUi
                        font.pixelSize: 12
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: card.source.uncertainty || ""
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: card.theme.warning
                    font.family: card.theme.fontUi
                    font.pixelSize: 12
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: !!card.source.selectedUrl || !!card.pageUrl
            AppButton {
                objectName: "copySongScoreLink"; theme: card.theme; variant: "ghost"; labelPixelSize: 12
                visible: !!card.source.selectedUrl
                text: card.copiedLink === card.source.selectedUrl ? "谱图链接已复制" : "复制谱图链接"
                onClicked: card.copyLink(card.source.selectedUrl)
            }
            AppButton {
                objectName: "copySongPageLink"; theme: card.theme; variant: "ghost"; labelPixelSize: 12
                visible: !!card.pageUrl
                text: card.copiedLink === card.pageUrl ? "来源页已复制" : "复制来源页"
                onClicked: card.copyLink(card.pageUrl)
            }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AppButton {
                objectName: "songSourceRejectButton"
                theme: card.theme
                text: "不是这首，重新找"
                labelPixelSize: 12
                onClicked: card.rejected()
            }
            Item { Layout.fillWidth: true }
            AppButton {
                objectName: "songSourceConfirmButton"
                theme: card.theme
                text: "就是这首，继续"
                labelPixelSize: 12
                variant: "primary"
                onClicked: card.confirmed()
            }
        }
    }
}
