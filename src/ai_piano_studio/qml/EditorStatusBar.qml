import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: statusBar
    objectName: "statusBar"

    required property Theme theme
    property bool embedded: false
    property string contextText: ""
    property string compactContextText: contextText
    property string tooltipContextText: contextText
    property string temporaryText: ""
    property string temporaryKind: "info"
    property string syncState: "idle"
    property int unsyncedCount: 0
    property real durationSeconds: 0
    property real estimatedDurationSeconds: 0
    property bool saving: false
    readonly property bool compact: embedded && width < 520
    readonly property bool narrow: embedded && width < 300
    readonly property string tooltipText: tooltipContextText
        + " · " + syncText() + " · " + durationText()

    implicitHeight: embedded ? 36 : theme.statusBarHeight
    color: embedded ? "transparent" : theme.status
    border.width: embedded ? 0 : 1
    border.color: theme.border

    function durationText() {
        if (unsyncedCount > 0)
            return "当前 " + durationSeconds.toFixed(1)
                + "s · 预计 " + estimatedDurationSeconds.toFixed(1) + "s"
        return "长度 " + durationSeconds.toFixed(1) + "s"
    }

    function temporaryColor() {
        if (temporaryKind === "warning")
            return theme.warning
        if (temporaryKind === "success")
            return theme.success
        if (temporaryKind === "error")
            return theme.danger
        return theme.info
    }

    function syncText() {
        if (syncState === "syncing")
            return "⟳ 正在同步"
        if (unsyncedCount > 0)
            return "⚠ " + unsyncedCount + " 个未同步修改"
        return "✓ 已同步"
    }

    function syncColor() {
        if (syncState === "syncing")
            return theme.info
        if (syncState === "pending" || unsyncedCount > 0)
            return theme.warning
        return theme.success
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: statusBar.embedded ? statusBar.theme.space1 : statusBar.theme.space3
        anchors.rightMargin: statusBar.embedded ? statusBar.theme.space1 : statusBar.theme.space3
        spacing: statusBar.embedded ? statusBar.theme.space2 : statusBar.theme.space3

        Item {
            objectName: "statusContextSlot"
            Layout.preferredWidth: statusBar.embedded
                ? (statusBar.compact ? 72 : 250) : 320
            Layout.minimumWidth: statusBar.embedded
                ? (statusBar.compact ? 62 : 150) : 240
            Layout.fillHeight: true

            Label {
                objectName: "gridLabel"
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.right: parent.right
                text: statusBar.compact
                    ? statusBar.compactContextText : statusBar.contextText
                color: statusBar.theme.textMuted
                font.pixelSize: statusBar.theme.typeCaption
                elide: Text.ElideRight
            }
        }

        Item {
            objectName: "statusTemporarySlot"
            visible: statusBar.temporaryText.length > 0
            Layout.preferredWidth: visible
                ? (statusBar.embedded ? 120 : 240) : 0
            Layout.minimumWidth: visible
                ? (statusBar.embedded ? 80 : 180) : 0
            Layout.fillHeight: true

            Label {
                id: playbackStatus
                objectName: "playbackStatus"
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.right: parent.right
                text: statusBar.temporaryText
                color: statusBar.temporaryColor()
                font.pixelSize: statusBar.theme.typeCaption
                elide: Text.ElideRight
            }
        }

        Item {
            objectName: "statusPersistentSlot"
            Layout.fillWidth: true
            Layout.minimumWidth: statusBar.embedded
                ? (statusBar.narrow ? 92 : 126) : 320
            Layout.fillHeight: true

            RowLayout {
                anchors.fill: parent
                spacing: statusBar.theme.space2

                Label {
                    objectName: "timelineSyncStatus"
                    text: statusBar.syncText()
                    color: statusBar.syncColor()
                    font.pixelSize: statusBar.theme.typeCaption
                    font.bold: statusBar.syncState === "syncing" || statusBar.unsyncedCount > 0
                    ToolTip.visible: syncStatusPointer.containsMouse
                    ToolTip.text: statusBar.unsyncedCount > 0
                        ? "未同步音符暂不会加入当前播放版本；停止编辑后会自动同步。"
                        : "时间轴与播放版本一致。"

                    MouseArea {
                        id: syncStatusPointer
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton
                    }
                }

                Label {
                    objectName: "timelineDurationStatus"
                    visible: !statusBar.compact
                    text: statusBar.durationText()
                    color: statusBar.theme.textSecondary
                    font.pixelSize: statusBar.theme.typeCaption
                    font.family: statusBar.theme.numberFont
                }

                Item { Layout.fillWidth: true }

                Label {
                    objectName: "saveStatus"
                    visible: statusBar.saving
                    text: "正在保存..."
                    color: statusBar.theme.textMuted
                    font.pixelSize: statusBar.theme.typeCaption
                }
            }
        }
    }

    HoverHandler {
        id: statusHover
        enabled: statusBar.embedded
    }
    ToolTip.visible: statusHover.hovered
    ToolTip.delay: 450
    ToolTip.text: statusBar.tooltipText
}
