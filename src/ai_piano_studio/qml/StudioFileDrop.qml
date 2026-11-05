import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "studioFileDrop"
    required property Theme theme
    property bool available: false
    readonly property bool confirmationOpen: confirmation.visible
    readonly property url pendingSource: pending.source
    signal fileConfirmed(url source)
    signal confirmationRequested()
    signal focusRestoreRequested()

    QtObject {
        id: pending
        property url source: ""
        property string name: ""
        property string action: ""
    }

    function candidate(urls) {
        if (!urls || urls.length !== 1)
            return null
        var value = urls[0].toString()
        // Only absolute local file URLs, excluding remote/UNC authorities.
        if (!/^file:\/\/\//i.test(value) || /^file:\/\/\/\//i.test(value))
            return null
        var path
        try { path = decodeURIComponent(value.substring(8)) } catch (error) { return null }
        if (!path || /[?#]/.test(value))
            return null
        var name = path.substring(path.lastIndexOf("/") + 1)
        if (!name || !/\.(json|mid|midi|wav|mp3)$/i.test(name))
            return null
        return { source: urls[0], name: name,
            action: /\.json$/i.test(name) ? "打开工程" : "导入音乐" }
    }

    function invalidatePending() {
        pending.source = ""
        pending.name = ""
        pending.action = ""
        confirmation.close()
    }

    function confirmPending() {
        if (!available || !confirmation.visible || pending.source.toString().length === 0) {
            invalidatePending()
            return
        }
        var source = pending.source
        // Clear before routing: the router may synchronously open another modal.
        invalidatePending()
        root.fileConfirmed(source)
    }

    onAvailableChanged: if (!available) invalidatePending()

    DropArea {
        id: area
        anchors.fill: parent
        enabled: root.available && !root.confirmationOpen
        onEntered: function(drag) {
            drag.accepted = root.available && drag.hasUrls
                && (drag.supportedActions & Qt.CopyAction) !== 0
                && root.candidate(drag.urls) !== null
        }
        onPositionChanged: function(drag) {
            drag.accepted = root.available && root.candidate(drag.urls) !== null
        }
        onDropped: function(drop) {
            var file = root.candidate(drop.urls)
            if (!root.available || !file || !(drop.supportedActions & Qt.CopyAction)) {
                drop.accepted = false
                return
            }
            pending.source = file.source
            pending.name = file.name
            pending.action = file.action
            drop.accept(Qt.CopyAction)
            root.confirmationRequested()
            confirmation.open()
        }
    }

    Rectangle {
        objectName: "studioFileDropHint"
        anchors.fill: parent
        anchors.margins: root.theme.space4
        visible: area.containsDrag && root.available && !root.confirmationOpen
        color: root.theme.elevatedSurface
        opacity: 0.95
        radius: root.theme.radiusL
        border.width: 2
        border.color: root.theme.borderStrong
        Label {
            anchors.centerIn: parent
            text: "松开以选择文件，确认后打开或导入"
            color: root.theme.textPrimary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSection
        }
    }

    Dialog {
        id: confirmation
        objectName: "studioFileDropConfirmation"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, parent.width - 32)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: root.theme.space5
        onOpened: cancelButton.forceActiveFocus(Qt.PopupFocusReason)
        onClosed: {
            pending.source = ""
            pending.name = ""
            pending.action = ""
            root.focusRestoreRequested()
        }
        background: Rectangle {
            radius: root.theme.radiusL
            color: root.theme.elevatedSurface
            border.color: root.theme.borderStrong
        }
        header: DialogHeader { theme: root.theme; title: "确认文件操作" }
        contentItem: ColumnLayout {
            spacing: root.theme.space3
            Label {
                Layout.fillWidth: true
                text: pending.name
                elide: Text.ElideMiddle
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeBody
            }
            Label {
                Layout.fillWidth: true
                text: pending.action === "打开工程"
                    ? "打开此工程？当前未保存的音乐会先进入保存流程。"
                    : "导入此音乐文件？确认后将进入导入预览。"
                wrapMode: Text.WordWrap
                color: root.theme.textSecondary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeBody
            }
        }
        footer: DialogFooter {
            theme: root.theme
            AppButton {
                id: cancelButton
                objectName: "studioFileDropCancel"
                theme: root.theme
                text: "取消"
                onClicked: root.invalidatePending()
            }
            AppButton {
                objectName: "studioFileDropConfirm"
                theme: root.theme
                text: pending.action
                variant: "primary"
                onClicked: root.confirmPending()
            }
        }
    }
}
