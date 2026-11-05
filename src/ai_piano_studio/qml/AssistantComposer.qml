import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: composer
    objectName: "assistantComposerPanel"
    required property var theme
    required property var workspace
    property alias input: messageField
    spacing: 4
    readonly property bool busy: workspace.assistantTurnActive
    readonly property real fieldHeight: Math.max(58, Math.min(128, messageField.contentHeight + 20))
    implicitHeight: fieldHeight + 32

    Rectangle {
        objectName: "assistantComposerField"
        Layout.fillWidth: true
        Layout.preferredHeight: composer.fieldHeight
        radius: 8
        color: composer.theme.inputBackground
        border.color: messageField.activeFocus ? composer.theme.focusRing : composer.theme.borderStrong
        ScrollView {
            anchors.fill: parent
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            TextArea {
                id: messageField
                objectName: "assistantInput"
                placeholderText: composer.busy ? "可以继续补充要求…" : "说说你的想法…"
                wrapMode: TextEdit.Wrap
                color: composer.theme.textPrimary
                placeholderTextColor: composer.theme.textMuted
                font.family: composer.theme.fontUi
                font.pixelSize: 14
                selectByMouse: true
                leftPadding: 10; rightPadding: composer.busy ? 78 : 44; topPadding: 10; bottomPadding: 10
                enabled: composer.workspace.ready
                background: null
                Accessible.name: "给 AI 助手的消息"
                onTextChanged: if (composer.workspace.ready) composer.workspace.assistant.setConversationDraft(text)
                Keys.onPressed: event => {
                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                            && !(event.modifiers & Qt.ShiftModifier) && !messageField.inputMethodComposing) {
                        event.accepted = true
                        composer.workspace.sendAssistantMessage()
                    }
                }
            }
        }
        AppButton {
            id: sendButton
            objectName: "assistantSendButton"
            theme: composer.theme
            anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.rightMargin: composer.busy ? 40 : 6; anchors.bottomMargin: 6
            implicitWidth: 28; implicitHeight: 28
            text: "↑"
            labelPixelSize: 17
            variant: "primary"
            enabled: composer.workspace.ready && messageField.text.trim().length > 0 && !messageField.inputMethodComposing
            accessibleName: composer.busy ? "发送补充消息" : "发送消息"
            ToolTip.visible: hovered
            ToolTip.delay: 350
            ToolTip.text: accessibleName
            onClicked: composer.workspace.sendAssistantMessage()
        }
        AppButton {
            id: stopButton
            objectName: "assistantStopButton"
            theme: composer.theme; variant: "primary"
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 6
            implicitWidth: 28; implicitHeight: 28
            visible: composer.busy
            accessibleName: "停止生成"
            ToolTip.visible: hovered
            ToolTip.delay: 350
            ToolTip.text: accessibleName
            onClicked: composer.workspace.assistant.cancelGeneration()
            Rectangle {
                objectName: "assistantStopIcon"
                width: 8; height: 8
                anchors.centerIn: parent
                color: stopButton.iconColor
            }
        }
    }
    Label {
        Layout.fillWidth: true
        visible: composer.busy && text.length > 0
        text: composer.workspace.ready && composer.workspace.assistant.pendingMessageCount > 0
            ? "已收到 " + composer.workspace.assistant.pendingMessageCount + " 条补充，将在当前步骤结束后接入" : ""
        color: composer.theme.textSecondary; font.pixelSize: 12
        wrapMode: Text.Wrap
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 2
        AppButton {
            id: attach
            objectName: "assistantAttachmentMenuButton"
            theme: composer.theme; text: "+"; variant: "ghost"
            implicitWidth: 28; implicitHeight: 28; labelPixelSize: 18
            accessibleName: "附件与导入"
            ToolTip.visible: hovered
            ToolTip.delay: 350
            ToolTip.text: accessibleName
            enabled: composer.workspace.ready && !composer.busy
            onClicked: attachments.open()
            StudioOptionPopup {
                id: attachments; theme: composer.theme; y: -height - 4
                StudioOptionItem {
                    objectName: "assistantAttachButton"; theme: composer.theme; text: "导入歌曲…"
                    onClicked: { attachments.close(); composer.workspace.assistant.requestAudioImport() }
                }
            }
        }
        AppButton {
            id: quick
            objectName: "studioAssistantQuickActions"
            theme: composer.theme; text: "✧"; variant: "ghost"
            implicitWidth: 28; implicitHeight: 28
            accessibleName: "快捷操作"
            ToolTip.visible: hovered
            ToolTip.delay: 350
            ToolTip.text: accessibleName
            enabled: composer.workspace.ready && !composer.busy
            onClicked: quickMenu.open()
            StudioOptionPopup {
                id: quickMenu; theme: composer.theme; y: -height - 4
                Repeater {
                    model: composer.workspace.quickActions
                    StudioOptionItem {
                        required property var modelData
                        theme: composer.theme; text: modelData.label
                        onClicked: { quickMenu.close(); composer.workspace.prefillQuickAction(modelData.id, modelData.prompt) }
                    }
                }
            }
        }
        AppButton {
            objectName: "assistantEditCreationScopeButton"
            theme: composer.theme; text: "范围：" + composer.workspace.compactScopeLabel ; variant: "ghost"
            implicitHeight: 28; labelPixelSize: 12; labelWeight: Font.Normal
            enabled: composer.workspace.ready && !composer.busy
            onClicked: composer.workspace.openScopeMenu(this)
            ToolTip.visible: hovered
            ToolTip.delay: 350
            ToolTip.text: "选择助手处理的音乐范围"
        }
        AssistantContextControl {
            theme: composer.theme
            assistant: composer.workspace.ready ? composer.workspace.assistant : null
            panelWidth: composer.width
            visible: composer.workspace.ready && composer.workspace.assistant.creatorEnabled
            enabled: composer.workspace.ready
        }
        Item { Layout.fillWidth: true }
        AppButton {
            id: mode
            objectName: "studioAssistantModeButton"
            theme: composer.theme; text: composer.workspace.ready && composer.workspace.assistant.mode === "collaboration" ? "直接修改" : "提案"
            variant: "ghost"; implicitHeight: 28; labelPixelSize: 12; labelWeight: Font.Normal
            enabled: composer.workspace.ready && !composer.busy
            Accessible.description: composer.workspace.ready && composer.workspace.assistant.mode === "collaboration" ? "直接修改当前工程，Ctrl+Z 撤销" : "提案模式：先试听，再决定是否应用"
            ToolTip.visible: hovered
            ToolTip.text: Accessible.description
            onClicked: modeMenu.open()
            StudioOptionPopup {
                id: modeMenu; objectName: "studioAssistantModePopup"; theme: composer.theme; y: -height - 4
                StudioOptionItem { objectName: "studioAssistantSuggestionMode"; theme: composer.theme; text: "先试听再应用"; checked: composer.workspace.ready && composer.workspace.assistant.mode === "suggestion"; onClicked: { modeMenu.close(); composer.workspace.assistant.returnToSuggestionMode() } }
                StudioOptionItem { objectName: "studioAssistantDirectMode"; theme: composer.theme; text: "直接修改"; detail: "先确认，修改后可撤销"; checked: composer.workspace.ready && composer.workspace.assistant.mode === "collaboration"; onClicked: { modeMenu.close(); composer.workspace.assistant.requestCollaborationMode() } }
            }
        }
    }
}
