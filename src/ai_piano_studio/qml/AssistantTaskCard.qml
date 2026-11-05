import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "assistantProgressCard"
    required property var theme
    required property var assistant
    property var snapshot: ({})
    property bool currentTask: true
    property bool runningOnly: false
    readonly property bool ready: assistant !== null && assistant !== undefined
    readonly property bool live: ready && currentTask
    readonly property bool busy: live && assistant.isBusy === true
    readonly property var task: live ? (assistant.taskProgress || ({})) : (snapshot.task || ({}))
    readonly property bool active: task.status === "running" || task.status === "waiting"
    readonly property var steps: task.modelDriven ? (task.modelSteps || []) : (task.steps || [])
    readonly property bool hasPreview: live && !busy && assistant.taskPreviewCurrent === true
    readonly property var previewInfo: busy ? ({}) : hasPreview ? assistant.taskPreviewInfo : (snapshot.previewInfo || ({}))
    readonly property bool hasProject: live && assistant.taskProjectAvailable === true
    readonly property string taskHint: task.modelMessage || (hasPreview ? previewInfo.error : "") || task.safeErrorMessage || (busy ? task.waitHint : "") || ""
    property bool expanded: false
    visible: (task.taskId !== undefined || busy) && (!runningOnly || busy)
    implicitHeight: content.implicitHeight + 16
    radius: theme.radiusM
    color: runningOnly ? "transparent" : theme.surface
    border.color: task.status === "failed" ? theme.borderStrong : theme.borderSubtle
    readonly property string stateText: busy ? (task.waitingForUser ? "等待你的回复" : "继续处理中") : task.modelStatus ? ({running: "进行中", in_progress: "进行中", blocked: "遇到阻碍", needs_input: "需要补充信息", completed: "已完成", failed: "未完成", waiting: "等待中", pending: "待处理", cancelled: "已取消"}[task.modelStatus] || task.modelStatus) : task.status === "failed" ? "未完成"
        : task.status === "cancelled" ? "已取消" : hasPreview ? "待应用"
        : (live && hasProject || snapshot.saved) ? "已应用" : task.status === "completed" ? "已完成"
        : task.status === "responded" ? "本轮回复结束" : !live && previewInfo.title ? "未应用" : "处理中"

    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right
        anchors.top: parent.top; anchors.margins: 8
        spacing: 6
        Label {
            objectName: "assistantContinuingNotice"
            visible: card.busy
            Layout.fillWidth: true
            text: card.task.waitingForUser ? "正在等待你的回复，请在下方输入。" : "仍在继续处理，尚未结束；上方回复是当前进展。你可以继续补充要求，或点击停止。"
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: card.theme.textPrimary
            font.pixelSize: 13
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            BusyIndicator { visible: card.busy; running: visible; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
            Label {
                objectName: "assistantProgressStatus"
                Layout.fillWidth: true
                text: card.runningOnly ? (card.task.modelMessage || card.task.stageLabel || (card.live ? card.assistant.assistantProgressText : "") || "正在处理音乐")
                    : (card.previewInfo.title || card.task.title || "音乐任务")
                        + (card.previewInfo.durationSeconds !== undefined ? " · " + Math.round(card.previewInfo.durationSeconds) + "秒 · " + card.previewInfo.noteCount + "音符" : "")
                        + " · " + card.stateText
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: card.task.status === "failed" ? card.theme.warning : card.theme.textPrimary
                font.pixelSize: 13
                Accessible.name: text
            }
            Label {
                objectName: "assistantTaskElapsed"
                visible: card.busy
                text: card.task.elapsedText || "00:00"
                color: card.theme.textSecondary
                font.family: card.theme.numberFont; font.pixelSize: 12
                Accessible.name: "已等待 " + text
            }
            AppButton {
                objectName: "assistantTaskDetails"
                theme: card.theme; variant: "ghost"; text: card.expanded ? "⌃" : "⌄"
                implicitWidth: 28; implicitHeight: 28
                accessibleName: card.expanded ? "收起任务详情" : "展开任务详情"
                Accessible.expanded: card.expanded
                onClicked: card.expanded = !card.expanded
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: card.hasPreview && !card.runningOnly
            spacing: 6
            AppButton {
                objectName: !card.runningOnly && card.currentTask ? "assistantTaskAudition" : ""; theme: card.theme
                text: card.assistant && card.assistant.taskPreviewPlaying ? "停止试听" : "▶ 试听"
                implicitHeight: 28; labelPixelSize: 12
                enabled: !card.busy
                onClicked: card.assistant.auditionTask()
            }
            AppButton {
                objectName: !card.runningOnly && card.currentTask ? "assistantTaskApply" : ""; theme: card.theme; variant: "primary"
                text: "应用为新作品"; implicitHeight: 28; labelPixelSize: 12
                enabled: !!card.assistant && (card.assistant.creatorEnabled || card.assistant.taskPreviewPlayed === true) && !card.busy
                onClicked: card.assistant.applyTaskProject()
                ToolTip.visible: hovered && !enabled
                ToolTip.text: "先试听，再决定是否采用"
            }
            Item { Layout.fillWidth: true }
        }
        AppButton {
            objectName: "assistantTaskStop"; theme: card.theme; text: card.hasPreview ? "停止试听" : "停止任务"
            implicitHeight: 28; visible: card.expanded && card.busy
            onClicked: card.hasPreview ? card.assistant.stopTaskAudition() : card.assistant.cancelGeneration()
        }
        Label {
            visible: card.task.modelDriven === true && (card.task.status === "failed" || card.task.status === "cancelled")
            Layout.fillWidth: true
            text: card.task.status === "cancelled" ? "软件状态：任务已停止" : "软件状态：" + (card.task.safeErrorMessage || "执行失败")
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: card.theme.warning; font.pixelSize: 12
        }
        Label {
            visible: text.length > 0 && !card.expanded && !card.runningOnly
            Layout.fillWidth: true
            text: card.taskHint
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
            color: card.task.status === "failed" ? card.theme.warning : card.theme.textSecondary
            font.pixelSize: 12
        }
        AppButton {
            objectName: "assistantTaskCreativeAlternative"
            visible: card.live && card.assistant.taskCreativeAlternativeAvailable === true && !card.runningOnly
            theme: card.theme; text: "选择原创替代"; implicitHeight: 28; labelPixelSize: 12
            enabled: !card.busy
            onClicked: card.assistant.generateOriginalAlternative()
        }
        ScrollView {
            id: detailsScroll
            objectName: "assistantTaskExpandedDetails"
            visible: card.expanded
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(196, taskDetails.implicitHeight)
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical: SafeScrollBar { theme: card.theme; policy: ScrollBar.AsNeeded }
            ColumnLayout {
                id: taskDetails
                width: detailsScroll.availableWidth
                spacing: 6
                Label {
                    visible: text.length > 0
                    Layout.fillWidth: true
                    text: card.taskHint; textFormat: Text.PlainText; wrapMode: Text.Wrap
                    color: card.task.status === "failed" ? card.theme.warning : card.theme.textSecondary
                    font.pixelSize: 12
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: !card.runningOnly
                    AppButton { objectName: "assistantTaskOpenProject"; theme: card.theme; text: "打开作品"; visible: card.hasProject; implicitHeight: 28; onClicked: card.assistant.openTaskProject() }
                    AppButton { objectName: "assistantTaskDiscard"; theme: card.theme; text: "放弃草稿"; visible: card.hasPreview; enabled: !card.busy; implicitHeight: 28; onClicked: card.assistant.discardTaskProject() }
                    AppButton { objectName: "assistantTaskRetry"; theme: card.theme; text: "重试"; visible: card.live && ["failed", "cancelled"].indexOf(card.task.status) >= 0; enabled: !card.busy; implicitHeight: 28; onClicked: card.assistant.retryTask() }
                    AppButton { objectName: "assistantTaskContinue"; theme: card.theme; text: "继续处理"; visible: card.live && card.task.modelDriven === true && card.task.status === "responded"; enabled: !card.busy; implicitHeight: 28; onClicked: card.assistant.sendMessage("继续刚才的任务，根据实际情况自行选择下一步，完成后给我可验收的结果。") }
                    Item { Layout.fillWidth: true }
                }
                Label {
                    Layout.fillWidth: true
                    text: "来源：" + ({ai_original: "AI 原创", ai_creative_unverified: "原创替代，未验证原曲", ai_reconstruction_unverified: "AI 尝试还原（待核对）", licensed_score_midi: "已验证曲谱", local_user_reference: "用户 MIDI", online_reference_midi: "网络 MIDI（待试听核对）"}[card.previewInfo.sourceKind] || "见任务记录")
                    color: card.theme.textSecondary; wrapMode: Text.Wrap; font.pixelSize: 12
                }
                Label {
                    visible: card.task.progressTotal !== null && card.task.progressTotal !== undefined
                    text: "已处理 " + card.task.progressCurrent + "/" + card.task.progressTotal
                    color: card.theme.textSecondary; font.pixelSize: 12
                }
                Repeater {
                    model: card.steps
                    Label {
                        required property var modelData
                        Layout.fillWidth: true
                        text: (modelData.status === "completed" ? "✓ " : modelData.status === "failed" ? "× " : "· ") + modelData.label
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap; color: card.theme.textSecondary; font.pixelSize: 12
                    }
                }
                Label {
                    visible: card.task.modelDriven === true
                    text: "连接与执行记录"
                    color: card.theme.textSecondary; font.pixelSize: 12
                }
                Repeater {
                    model: card.task.modelDriven ? (card.task.steps || []) : []
                    Label {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.label + " · " + modelData.status
                        textFormat: Text.PlainText; wrapMode: Text.Wrap
                        color: card.theme.textSecondary; font.pixelSize: 11
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: card.live ? (card.assistant.executionSummary || "") : (card.snapshot.executionSummary || "")
                    textFormat: Text.PlainText; wrapMode: Text.Wrap; color: card.theme.textSecondary; font.pixelSize: 12
                }
                Label {
                    Layout.fillWidth: true
                    text: card.live ? (card.assistant.taskNotes || []).join("\n") : (card.snapshot.notes || []).join("\n")
                    textFormat: Text.PlainText; wrapMode: Text.Wrap; color: card.theme.textSecondary; font.pixelSize: 12
                }
            }
        }
    }
}
