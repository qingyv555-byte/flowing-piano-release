import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "CatalogOrder.js" as CatalogOrder

Item {
    id: root
    objectName: "assistantWorkspace"
    property bool docked: false
    readonly property var activeMessageInput: composer.input
    signal closeRequested()
    signal detachRequested()
    signal connectionSettingsRequested()
    readonly property bool modalSurfaceOpen: projectInfoDialog.opened || clearConversationDialog.opened || creationScopeDialog.opened || collaborationConfirmDialog.opened
        || proposalChangesDialog.opened || settingsDialog.opened || thirdPartyWarning.opened || aiServiceDialog.opened
    readonly property bool hasUnsavedConnection: aiServiceDialog.hasUnsavedConnection
    function requestWindowClose(action) { aiServiceDialog.requestWindowClose(action) }
    function close() { aiServiceDialog.close(); root.closeRequested() }
    property var assistant: null
    readonly property var recoveryLab: assistant && assistant.songRecoveryTask !== undefined ? assistant.songRecoveryTask : null
    readonly property bool ready: assistant !== null
    readonly property var connectionProfile: {
        var profiles = root.ready ? root.assistant.connectionProfiles : []
        for (var i = 0; i < profiles.length; ++i)
            if (profiles[i].id === root.assistant.currentProviderId)
                return profiles[i]
        return ({})
    }
    readonly property bool connectionConfigured: connectionProfile.credentialStatus === "saved"
        || (connectionProfile.credentialStatus === "not_required" && connectionProfile.configured)
    readonly property string connectionCode: root.ready ? root.assistant.connectionResult.code : "UNKNOWN_ERROR"
    readonly property string connectionTitle: !connectionConfigured ? "连接模型"
        : connectionCode === "UNKNOWN_ERROR" && !root.assistant.connectionTested ? "未测试连接"
        : connectionCode !== "SUCCESS" ? "连接失败"
        : modelStatus.canCreate ? "可创作"
        : modelStatus.state === "chat_only" ? "可对话" : "验证创作"
    readonly property var modelStatus: ready && assistant !== null
        ? assistant.currentModelStatus
        : ({ "state": "unverified", "title": "创作能力待验证", "detail": "尚未选择模型。",
            "actionLabel": "验证创作能力", "canCreate": false, "canVerify": false })
    readonly property var configuredCreationScope: ready && assistant !== null
        ? assistant.creationScope
    : ({ "mode": "automatic", "startBar": 1, "endBar": 8,
            "label": "不限小节（可手动调整范围）", "unlimited": true })
    readonly property var creationScopePreview: {
        var configured = root.configuredCreationScope
        if (!root.ready || root.assistant === null)
            return configured
        return root.assistant.previewCreationScope(root.activeMessageInput.text)
    }
    property var taskHistory: ({})
    readonly property string compactConnectionLabel: !root.ready ? "加载中" : root.assistant.connectionTesting ? "连接中"
        : !root.connectionConfigured ? "未连接" : root.connectionCode === "SUCCESS" ? (root.modelStatus.canCreate ? "已连接" : "仅对话")
        : !root.assistant.connectionTested ? "未测试" : "连接失败"
    readonly property bool connectionProblem: root.ready && (root.assistant.connectionTested && root.connectionCode !== "SUCCESS"
        || root.modelStatus.state === "verification_failed" || root.modelStatus.state === "chat_only")
    readonly property string compactScopeLabel: {
        var scope = root.configuredCreationScope
        return scope.mode === "manual" ? "第 " + scope.startBar + "–" + scope.endBar + " 小节"
            : "全曲"
    }
    function openScopeMenu(opener) { scopeMenu.parent = opener; scopeMenu.y = -scopeMenu.height - 4; scopeMenu.open() }
    function captureTask() {
        if (!root.ready) return
        var task = root.assistant.taskProgress
        var row = root.assistant.taskMessageRow
        if (!task.taskId || row < 0) return
        var next = Object.assign({}, root.taskHistory)
        var previous = ({})
        for (var key in next) {
            if (next[key].task.taskId === task.taskId) { previous = next[key]; delete next[key] }
        }
        next[String(row)] = {task: task, previewInfo: root.assistant.isBusy ? ({}) : root.assistant.taskPreviewCurrent ? root.assistant.taskPreviewInfo : previous.previewInfo || ({}), executionSummary: root.assistant.executionSummary, notes: root.assistant.taskNotes, saved: root.assistant.taskProjectAvailable}
        root.taskHistory = next
        root.assistant.rememberTaskDisplayReceipts(next)
    }
    function revealPendingTask() {
        if (!root.ready) return
        var source = root.assistant.taskMessageRow
        for (var i = 0; i < messageList.count; ++i)
            if (root.assistant.conversationMessages.sourceRow(i) === source) { messageList.positionViewAtIndex(i, ListView.Center); return }
        root.scrollToBottom()
    }
    StudioOptionPopup {
        id: scopeMenu; objectName: "assistantScopeMenu"; theme: root.theme
        StudioOptionItem { theme: root.theme; objectName: "assistantScopeAll"; text: "全曲（自动范围）"; onClicked: { scopeMenu.close(); root.assistant.setCreationScopeAutomatic() } }
        StudioOptionItem { theme: root.theme; objectName: "assistantScopeSelection"; text: "当前选中区域"; enabled: root.ready && root.assistant.projectContext.hasSelection; onClicked: {
            scopeMenu.close()
            var bars = root.assistant.projectContext.rangeText.match(/\d+/g)
            if (bars && bars.length >= 2) root.assistant.setCreationScopeBars(Number(bars[0]), Number(bars[1]))
        } }
        StudioOptionItem { theme: root.theme; objectName: "assistantScopeManual"; text: "手动选择小节…"; onClicked: { scopeMenu.close(); creationScopeDialog.open() } }
    }
    Dialog {
        id: projectInfoDialog; objectName: "assistantProjectInfoDialog"
        parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        width: Math.min(420, parent ? parent.width - 32 : 420)
        padding: 16
        header: DialogHeader { theme: root.theme; title: "当前工程" }
        background: Rectangle { color: root.theme.popupBackground; radius: 8; border.color: root.theme.borderStrong }
        contentItem: Label {
            text: root.ready ? root.assistant.projectContext.title + "\n"
                + root.assistant.projectContext.bpm + " BPM · " + root.assistant.projectContext.timeSignature
                + "\n" + root.assistant.projectContext.rangeText : ""
            wrapMode: Text.Wrap; color: root.theme.textPrimary; font.pixelSize: 14
        }
        footer: DialogFooter { theme: root.theme; Item { Layout.fillWidth: true } AppButton { theme: root.theme; text: "关闭"; onClicked: projectInfoDialog.close() } }
    }

    onAssistantChanged: Qt.callLater(function() {
        if (root.ready && root.activeMessageInput) root.activeMessageInput.text = root.assistant.conversationDraft
        root.taskHistory = root.ready ? root.assistant.taskDisplayReceipts : ({})
        if (root && typeof root.captureTask === "function") root.captureTask()
    })
    property bool contextExpanded: false
    property bool followLatest: true
    property bool userIsAtBottom: true
    property bool hasUnreadIncoming: false
    function openConnectionSettings() { aiServiceDialog.open() }
    AssistantAIServiceDialog { id: aiServiceDialog; theme: root.theme; assistant: root.assistant }
    property string pendingQuickActionId: ""
    readonly property var quickActions: [
        { id: "continue", label: "续写这段", prompt: "续写当前选中的这段，保持主题和节奏感觉，先给我建议。" },
        { id: "improve_melody", label: "优化旋律", prompt: "优化当前旋律，让它更自然、更好听，先给我建议。" },
        { id: "add_chords", label: "添加和弦", prompt: "为当前旋律添加合适的和弦，先给我建议。" },
        { id: "add_bass", label: "添加低音", prompt: "为当前旋律添加稳妥的低音支撑，先给我建议。" },
        { id: "add_variation", label: "增加变化", prompt: "在保留主题的前提下增加适度变化，先给我建议。" },
        { id: "make_climax", label: "制造高潮", prompt: "在当前目标范围内加入一个有层次的高潮，先给我建议。" },
        { id: "simplify", label: "简化", prompt: "简化当前部分，保留旋律轮廓并减少不必要的密度，先给我建议。" },
        { id: "sadder", label: "更悲伤", prompt: "让当前选中的部分更悲伤，但保留原来的主题，先给我建议。" },
        { id: "warmer", label: "更温暖", prompt: "让当前选中的部分更温暖、更柔和，先给我建议。" },
        { id: "dreamy", label: "更梦幻", prompt: "让当前选中的部分更梦幻，但保持清晰的主题和节拍，先给我建议。" }
    ]

    Theme {
        id: assistantTheme
        studio: typeof studioUiVariant !== "undefined" && studioUiVariant === "studio"
        nocturne: typeof studioUiVariant !== "undefined" && studioUiVariant === "nocturne"
    }
    readonly property var theme: assistantTheme

    readonly property bool assistantTurnActive: root.ready
        && (root.assistant.assistantStatus === "thinking"
            || root.assistant.assistantStatus === "tool_running"
            || root.assistant.assistantStatus === "repairing")

    function catalogIndex(catalog, currentId) {
        for (var index = 0; index < catalog.length; index += 1) {
            if (catalog[index].id === currentId)
                return index
        }
        return 0
    }

    function catalogLabel(catalog, currentId, fallback) {
        for (var index = 0; index < catalog.length; index += 1) {
            if (catalog[index].id === currentId)
                return catalog[index].label || catalog[index].displayName || catalog[index].id
        }
        return fallback
    }

    function providerLabel(catalog, currentId) {
        var label = root.catalogLabel(catalog, currentId, "选择服务商")
        if (label !== currentId)
            return label
        if (currentId === "openai")
            return "OpenAI"
        if (currentId === "deepseek")
            return "DeepSeek"
        if (currentId === "compatible")
            return "OpenAI Compatible"
        if (currentId === "ollama")
            return "Ollama"
        return label
    }

    function profileIndex(catalog, currentId) {
        for (var index = 0; index < catalog.length; index += 1) {
            if (catalog[index].id === currentId)
                return index
        }
        return 0
    }

    function modelCapabilityLabel(capabilityState) {
        if (capabilityState === "known_supported")
            return "MIDI 可编辑"
        if (capabilityState === "known_unsupported")
            return "仅聊天"
        return "待验证"
    }

    function proposalValue(value, fallback) {
        return value === undefined || value === null ? fallback : String(value)
    }

    function proposalInspector(changes) {
        if (!changes || !changes.available || !changes.inspector)
            return ({ available: false })
        return changes.inspector
    }

    function proposalConcreteDiff(changes, maximumItems) {
        var inspector = root.proposalInspector(changes)
        if (!inspector.available)
            return ""
        var summary = inspector.summary || ({})
        var parts = []
        if ((summary.velocityChanged || 0) > 0)
            parts.push("力度 " + summary.velocityChanged)
        if ((summary.pitchChanged || 0) > 0)
            parts.push("音高 " + summary.pitchChanged)
        if ((summary.positionChanged || 0) + (summary.durationChanged || 0) > 0)
            parts.push("节奏 " + ((summary.positionChanged || 0) + (summary.durationChanged || 0)))
        if ((summary.notesAdded || 0) > 0)
            parts.push("新增 " + summary.notesAdded)
        if ((summary.notesDeleted || 0) > 0)
            parts.push("删除 " + summary.notesDeleted)
        if ((summary.instrumentChanged || 0) > 0)
            parts.push("音色 " + summary.instrumentChanged)
        if ((summary.harmonyChanged || 0) > 0)
            parts.push("和弦 " + summary.harmonyChanged)
        if (summary.bpmChanged)
            parts.push("BPM")
        return (inspector.headline || "本次包含音乐变化。")
            + (parts.length > 0 ? " · " + parts.join(" · ") : "")
    }

    function proposalNumber(value, fallback) {
        return value === undefined || value === null ? fallback : String(value)
    }

    function proposalPercent(value) {
        if (value === undefined || value === null)
            return "—"
        return (Number(value) > 0 ? "+" : "") + String(value) + "%"
    }

    function proposalPitchChanged(detail) {
        var before = detail.before || ({})
        var after = detail.after || ({})
        return before.pitch !== undefined || after.pitch !== undefined
    }

    function hasConfiguredProvider() {
        if (!root.ready)
            return false
        var profiles = root.assistant.connectionProfiles || []
        for (var index = 0; index < profiles.length; ++index) {
            var profile = profiles[index]
            if (profile.credentialStatus === "saved"
                    || (profile.credentialStatus === "not_required" && profile.configured))
                return true
        }
        return false
    }

    function sendAssistantMessage() {
        if (!root.ready || root.activeMessageInput.inputMethodComposing)
            return
        var sent = root.pendingQuickActionId.length > 0
            ? assistant.sendQuickAction(root.pendingQuickActionId, root.activeMessageInput.text)
            : assistant.sendMessage(root.activeMessageInput.text)
        if (sent) {
            root.activeMessageInput.text = ""
            root.pendingQuickActionId = ""
            scrollToBottom()
        }
    }

    function prefillMessage(prompt) {
        root.pendingQuickActionId = ""
        root.activeMessageInput.text = String(prompt || "")
        root.activeMessageInput.forceActiveFocus()
        root.activeMessageInput.cursorPosition = root.activeMessageInput.length
    }

    function prefillQuickAction(actionId, prompt) {
        root.pendingQuickActionId = String(actionId || "")
        root.activeMessageInput.text = String(prompt || "")
        root.activeMessageInput.forceActiveFocus()
        root.activeMessageInput.cursorPosition = root.activeMessageInput.length
    }

    function scrollToBottom() {
        followLatest = true
        userIsAtBottom = true
        hasUnreadIncoming = false
        messageList.forceLayout()
        messageList.positionViewAtEnd()
        settleChatTail.restart()
    }

    Timer {
        id: settleChatTail
        interval: 0
        onTriggered: {
            if (!root.followLatest) return
            messageList.forceLayout()
            messageList.positionViewAtEnd()
        }
    }

    function proposalSummaryText(proposal) {
        var parts = []
        if (proposal.notesAdded > 0)
            parts.push("+ " + proposal.notesAdded + " 个音符")
        if (proposal.notesUpdated > 0)
            parts.push("~ " + proposal.notesUpdated + " 个音符")
        if (proposal.notesDeleted > 0)
            parts.push("− " + proposal.notesDeleted + " 个音符")
        if (proposal.barStart > 0)
            parts.push("第 " + proposal.barStart + "–" + proposal.barEnd + " 小节")
        if (proposal.bpmBefore === null || proposal.bpmAfter === null)
            parts.push("BPM 未改变")
        else
            parts.push("BPM " + proposal.bpmBefore + " → " + proposal.bpmAfter)
        return parts.join(" · ")
    }

    Connections {
        target: root.assistant
        function onCollaborationConfirmationRequested() { collaborationConfirmDialog.open() }
        function onChanged() { Qt.callLater(function() { if (root && typeof root.captureTask === "function") root.captureTask() }) }
        function onTaskProgressChanged() { Qt.callLater(function() { if (root && typeof root.captureTask === "function") root.captureTask() }) }
        function onConversationChanged() {
            root.taskHistory = root.assistant.taskDisplayReceipts
            Qt.callLater(function() { if (root && typeof root.captureTask === "function") root.captureTask() })
            root.pendingQuickActionId = ""
            root.activeMessageInput.text = root.assistant.conversationDraft
            root.followLatest = true
            root.hasUnreadIncoming = false
            Qt.callLater(function() { if (root && typeof root.scrollToBottom === "function") root.scrollToBottom() })
        }
    }
    Dialog {
        id: clearConversationDialog
        parent: Overlay.overlay
        padding: 16
        objectName: "assistantClearConversationDialog"
        anchors.centerIn: parent
        modal: true
        title: "清空聊天记录"
        width: Math.min(420, parent ? parent.width - 32 : 420)
        background: Rectangle { color: root.theme.popupBackground; radius: root.theme.radiusM; border.color: root.theme.borderStrong }
        header: DialogHeader { theme: root.theme; title: clearConversationDialog.title }
        Label { text: "清空当前工程的 AI 聊天记录？其他工程的记录会保留，此操作无法撤销。"; color: root.theme.textPrimary; wrapMode: Text.Wrap }
        footer: DialogFooter {
            theme: root.theme
            AppButton { theme: root.theme; text: "取消"; variant: "ghost"; onClicked: clearConversationDialog.reject() }
            AppButton { theme: root.theme; text: "清空记录"; variant: "secondary"; onClicked: clearConversationDialog.accept() }
        }
        onAccepted: if (root.ready) root.assistant.clearConversation()
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 6
        RowLayout {
            objectName: "assistantHeaderBar"
            Layout.fillWidth: true
            Label { text: "AI 助手"; color: theme.textPrimary; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true }
            AppButton {
                id: workspaceMore; objectName: "assistantMoreButton"; theme: root.theme; text: "···"; variant: "ghost"; implicitWidth: 28; implicitHeight: 28
                accessibleName: "助手更多操作"; ToolTip.visible: hovered; ToolTip.delay: 350; ToolTip.text: accessibleName; onClicked: moreMenu.visible ? moreMenu.close() : moreMenu.open()
                StudioOptionPopup {
                    id: moreMenu; theme: root.theme; x: Math.min(0, workspaceMore.width - width); y: workspaceMore.height
                    StudioOptionItem { objectName: "studioAssistantContext"; theme: root.theme; text: "当前工程信息"; onClicked: { moreMenu.close(); projectInfoDialog.open() } }
                    StudioOptionItem { objectName: "assistantClearConversationButton"; theme: root.theme; text: "清空当前对话…"; enabled: root.ready; onClicked: { moreMenu.close(); clearConversationDialog.open() } }
                    StudioOptionItem { objectName: "assistantDetachButton"; theme: root.theme; text: "在独立窗口中打开"; visible: root.docked; onClicked: { moreMenu.close(); root.detachRequested() } }
                    StudioOptionItem { theme: root.theme; text: "AI 服务设置…"; onClicked: { moreMenu.close(); root.openConnectionSettings() } }
                }
            }
            AppButton { objectName: "studioAssistantClose"; theme: root.theme; text: "×"; variant: "ghost"; implicitWidth: 28; implicitHeight: 28; accessibleName: "关闭 AI 助手"; ToolTip.visible: hovered; ToolTip.delay: 350; ToolTip.text: accessibleName; onClicked: root.requestWindowClose(function() { root.closeRequested() }) }
        }
        RowLayout {
            objectName: "assistantModelBar"
            Layout.fillWidth: true; spacing: 6
            StudioModelComboBox {
                objectName: "studioAssistantModelSelector"
                theme: root.theme; providerId: root.ready ? root.connectionProfile.brandId || root.assistant.currentProviderId : ""
                Layout.fillWidth: true; Layout.minimumWidth: 0; implicitHeight: 32
                model: root.ready ? CatalogOrder.ordered(root.assistant.modelCatalog, root.assistant.catalogSortMode, "label") : []
                textRole: "label"
                currentIndex: root.catalogIndex(model, root.ready ? root.assistant.currentModelId : "")
                enabled: root.ready && !root.assistantTurnActive && !root.assistant.connectionTesting && model.length > 0
                Accessible.name: "当前创作模型"
                onActivated: root.assistant.selectModel(model[currentIndex].id)
                ToolTip.visible: hovered; ToolTip.text: displayText
            }
            Label {
                objectName: "studioAssistantConnectionStatus"
                text: "● " + root.compactConnectionLabel
                color: root.connectionCode === "SUCCESS" ? theme.success : theme.textSecondary
                font.pixelSize: 12
                Accessible.description: root.ready ? root.assistant.connectionResult.message + " " + root.modelStatus.detail : "助手加载中"
                ToolTip.visible: statusHover.hovered
                ToolTip.text: Accessible.description
                HoverHandler { id: statusHover }
            }
            AppButton { objectName: "studioAssistantConnectionSettings"; theme: root.theme; iconSource: "icons/settings-2.svg"; variant: "ghost"; implicitWidth: 28; implicitHeight: 28; accessibleName: "AI 服务与模型设置"; ToolTip.visible: hovered; ToolTip.delay: 350; ToolTip.text: accessibleName; onClicked: root.openConnectionSettings() }
        }
        Label {
            objectName: "assistantConnectionProblem"
            Layout.fillWidth: true
            visible: root.ready && (root.assistant.conversationError.length > 0 || root.connectionProblem)
            text: root.assistant ? (root.assistant.conversationError || (!root.connectionConfigured
                ? "尚未配置服务，点击模型旁的设置按钮连接。" : root.assistant.connectionResult.message || root.modelStatus.detail)) : ""
            wrapMode: Text.Wrap; color: theme.warning; font.pixelSize: 12
        }
        AppButton {
            objectName: "assistantCreationGuidanceAction"; theme: root.theme; text: root.modelStatus.actionLabel || "检查创作能力"
            visible: root.connectionConfigured && root.modelStatus.state !== "available" && !root.modelStatus.canCreate
            implicitHeight: 28; labelPixelSize: 12
            enabled: root.modelStatus.canVerify
            onClicked: root.assistant.verifyMusicToolsAsync()
        }
        Item {
            id: conversationPane; objectName: "assistantConversationPane"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 80
                    ListView {
                        id: messageList
                        objectName: "assistantMessageList"
                        anchors.fill: parent
                        spacing: 12
                        clip: true
                        model: root.ready ? root.assistant.conversationMessages : null
                        rightMargin: root.theme.scrollbarGutter
                        ScrollBar.vertical: SafeScrollBar { theme: root.theme }

                    WheelHandler {
                        id: chatWheelHandler
                        target: null
                        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                        onWheel: wheel => {
                            var delta = wheel.pixelDelta.y !== 0
                                ? wheel.pixelDelta.y : wheel.angleDelta.y
                            if (delta === 0)
                                return
                            messageList.cancelFlick()
                            root.followLatest = false
                            var minimumContentY = messageList.originY
                            var maximumContentY = Math.max(minimumContentY,
                                minimumContentY + messageList.contentHeight - messageList.height)
                            messageList.contentY = Math.max(
                                minimumContentY,
                                Math.min(maximumContentY, messageList.contentY - delta))
                            root.userIsAtBottom = messageList.contentY >= maximumContentY - 1
                            root.followLatest = root.userIsAtBottom
                            if (root.userIsAtBottom)
                                root.hasUnreadIncoming = false
                            wheel.accepted = true
                        }
                    }

                    onContentYChanged: {
                    var distanceToBottom = Math.max(0, originY + contentHeight - height - contentY)
                    userIsAtBottom = distanceToBottom <= 60
                    if (moving || dragging)
                        followLatest = userIsAtBottom
                }
                    onCountChanged: {
                    if (followLatest)
                        Qt.callLater(function() {
                            if (root && root.followLatest && typeof root.scrollToBottom === "function")
                                root.scrollToBottom()
                        })
                    else
                        root.hasUnreadIncoming = true
                }
                    onContentHeightChanged: {
                    if (followLatest)
                        Qt.callLater(function() {
                            if (root && root.followLatest && typeof root.scrollToBottom === "function")
                                root.scrollToBottom()
                        })
                    else
                        root.hasUnreadIncoming = true
                }

                    delegate: Item {
                    id: messageDelegate
                    required property string kind
                    required property string text
                    required property string title
                    required property string detail
                    required property string proposalId
                    required property int index
                    readonly property bool executionReceipt: kind === "SYSTEM_NOTICE" && text.indexOf("执行回执：") === 0
                    readonly property int sourceRow: root.ready ? root.assistant.conversationMessages.sourceRow(index) : -1
                    readonly property var taskReceipt: root.taskHistory[String(sourceRow)] || ({})
                    readonly property bool currentRunningTask: root.ready && root.assistant.isBusy
                        && root.assistant.taskProgress.taskId === (taskReceipt.task || ({})).taskId
                    readonly property bool ownsTask: taskReceipt.task !== undefined && (!!(taskReceipt.previewInfo && taskReceipt.previewInfo.title)
                        || taskReceipt.task.modelDriven === true
                        || currentRunningTask
                        || !!taskReceipt.saved
                        || ["failed", "cancelled"].indexOf(taskReceipt.task.status) >= 0)
                    property bool noticeExpanded: false
                    width: Math.max(0, messageList.width - root.theme.scrollbarGutter)
                    implicitHeight: content.implicitHeight
                    height: implicitHeight

                    ColumnLayout {
                        id: content
                        width: parent.width
                        spacing: theme.space1

                        Rectangle {
                            Layout.alignment: messageDelegate.kind === "USER_MESSAGE" ? Qt.AlignRight : Qt.AlignLeft
                            Layout.maximumWidth: Math.min(messageDelegate.kind === "USER_MESSAGE" ? 360 : 580, parent.width * (messageDelegate.kind === "USER_MESSAGE" ? 0.8 : 0.96))
                            implicitWidth: Math.max(72, messageMeasure.advanceWidth + theme.space4)
                            implicitHeight: messageText.implicitHeight + theme.space4
                            visible: messageDelegate.executionReceipt || messageDelegate.kind === "USER_MESSAGE" || (messageDelegate.kind === "ASSISTANT_MESSAGE" && (messageDelegate.taskReceipt.task && messageDelegate.taskReceipt.task.modelDriven || !(messageDelegate.ownsTask && messageDelegate.taskReceipt.previewInfo && messageDelegate.taskReceipt.previewInfo.title)))
                            radius: theme.radiusL
                            color: messageDelegate.kind === "USER_MESSAGE" ? theme.surfaceSelected : "transparent"
                            border.color: "transparent"

                            TextMetrics { id: messageMeasure; text: messageDelegate.text; font.family: theme.fontUi; font.pixelSize: theme.typeBody }

                            TextEdit {
                                id: messageText
                                objectName: messageDelegate.executionReceipt ? "assistantExecutionReceipt" : "assistantMessageText"
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: theme.space2
                                height: implicitHeight
                                text: messageDelegate.text
                                textFormat: Text.MarkdownText
                                wrapMode: Text.Wrap
                                readOnly: true
                                selectByMouse: true
                                color: theme.textPrimary
                                font.family: theme.fontUi
                                font.pixelSize: 14
                            }
                        }

                        AppButton {
                            readonly property bool representsActionableAdvice:
                                root.ready
                                && messageDelegate.kind === "ASSISTANT_MESSAGE"
                                && root.assistant.conversationMessages.sourceRow(messageDelegate.index) === root.assistant.advicePreviewMessageRow
                                && root.assistant.canGenerateAdvicePreview
                            objectName: representsActionableAdvice
                                ? "assistantAdvicePreviewButton" : ""
                            Layout.alignment: Qt.AlignLeft
                            Layout.preferredWidth: 276
                            implicitHeight: 34
                            visible: representsActionableAdvice
                            theme: root.theme
                            iconSource: "icons/play.svg"
                            text: "按这个建议生成可试听方案"
                            variant: "secondary"
                            enabled: representsActionableAdvice
                            accessibleName: text
                            Accessible.description: "沿用上一轮目标、范围和约束，生成可试听但不直接应用的修改方案"
                            onClicked: root.assistant.generateAdvicePreview()
                        }

                        Rectangle {
                            objectName: "assistantActivityTimeline"
                            Layout.fillWidth: true
                            visible: messageDelegate.kind === "ACTIVITY"
                            implicitHeight: activityContent.implicitHeight + theme.space5
                            radius: theme.radiusL
                            color: theme.elevatedSurface
                            border.color: theme.borderSubtle

                            ColumnLayout {
                                id: activityContent
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.margins: 10
                                spacing: theme.space2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: theme.space2

                                    Image {
                                        Layout.preferredWidth: theme.iconMedium
                                        Layout.preferredHeight: theme.iconMedium
                                        source: "icons/music-2.svg"
                                        fillMode: Image.PreserveAspectFit
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        text: messageDelegate.title.length > 0
                                            ? messageDelegate.title : "音乐处理"
                                        color: theme.textPrimary
                                        font.family: theme.fontUi
                                        font.pixelSize: theme.typePanelTitle
                                        font.weight: theme.weightBold
                                    }
                                }

                                Repeater {
                                    model: messageDelegate.text.length > 0
                                        ? messageDelegate.text.split("\n") : []

                                    delegate: Item {
                                        required property string modelData
                                        required property int index
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: Math.max(24, activityStepText.implicitHeight + theme.space1)

                                        RowLayout {
                                            anchors.fill: parent
                                            spacing: theme.space2

                                            Item {
                                                Layout.preferredWidth: 20
                                                Layout.fillHeight: true

                                                Rectangle {
                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                    anchors.top: parent.top
                                                    anchors.topMargin: 3
                                                    width: 14
                                                    height: 14
                                                    radius: 7
                                                    color: theme.accentPrimary

                                                    Rectangle {
                                                        anchors.centerIn: parent
                                                        width: 5
                                                        height: 5
                                                        radius: 3
                                                        color: theme.textOnAccent
                                                    }
                                                }

                                                Rectangle {
                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                    anchors.top: parent.top
                                                    anchors.topMargin: 17
                                                    anchors.bottom: parent.bottom
                                                    width: 2
                                                    color: theme.accentPrimary
                                                    opacity: 0.55
                                                    visible: index < messageDelegate.text.split("\n").length - 1
                                                }
                                            }

                                            Label {
                                                id: activityStepText
                                                Layout.fillWidth: true
                                                text: modelData
                                                wrapMode: Text.Wrap
                                                color: theme.textSecondary
                                                font.family: theme.fontUi
                                                font.pixelSize: theme.typeSmall
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: (messageDelegate.kind === "SYSTEM_NOTICE" || messageDelegate.kind === "ERROR")
                                && !messageDelegate.executionReceipt
                                && (!messageDelegate.ownsTask || messageDelegate.noticeExpanded)
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true; Layout.minimumWidth: 0
                                    text: "! " + messageDelegate.text.split("\n")[0]
                                    color: theme.textSecondary; font.pixelSize: 12
                                    wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                                }
                                AppButton {
                                    theme: root.theme; variant: "ghost"; implicitHeight: 28
                                    text: messageDelegate.noticeExpanded ? "收起" : "详情"
                                    labelPixelSize: 12; labelWeight: Font.Normal
                                    onClicked: messageDelegate.noticeExpanded = !messageDelegate.noticeExpanded
                                    Accessible.description: messageDelegate.text
                                }
                            }
                            Label {
                                visible: messageDelegate.noticeExpanded
                                Layout.fillWidth: true; text: messageDelegate.text
                                textFormat: Text.PlainText; wrapMode: Text.Wrap
                                color: theme.warning; font.pixelSize: 12
                            }
                        }
                        AssistantTaskCard {
                            Layout.fillWidth: true
                            visible: messageDelegate.ownsTask
                            runningOnly: messageDelegate.currentRunningTask && messageDelegate.taskReceipt.task.modelDriven !== true
                            theme: root.theme; assistant: root.assistant
                            snapshot: messageDelegate.taskReceipt
                            currentTask: root.ready && root.assistant.taskProgress.taskId === (messageDelegate.taskReceipt.task || ({})).taskId
                            onExpandedChanged: messageDelegate.noticeExpanded = expanded
                        }


                        Rectangle {
                            id: proposalCard
                            objectName: "assistantProposalCard"
                            readonly property var proposal: root.ready ? root.assistant.proposal : ({"available": false})
                            readonly property var inspector: root.ready
                                ? ((root.assistant.proposalChanges || ({})).inspector || ({}))
                                : ({})
                            readonly property bool representsCurrentProposal:
                                messageDelegate.kind === "PROPOSAL"
                                && messageDelegate.proposalId.length > 0
                                && proposal.available
                                && proposal.proposalId === messageDelegate.proposalId
                            property bool expanded: false
                            Layout.fillWidth: true
                            visible: representsCurrentProposal
                            implicitHeight: visible ? proposalContent.implicitHeight + theme.space6 : 0
                            radius: theme.radiusL
                            color: theme.elevatedSurface
                            border.width: 1
                            border.color: proposal.status === "stale" ? theme.warning : theme.accentPrimary

                            ColumnLayout {
                                id: proposalContent
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.margins: 10
                                spacing: theme.space2

                                Label {
                                    objectName: "assistantProposalActualDiff"
                                    Layout.fillWidth: true
                                    text: "修改建议 · " + root.proposalSummaryText(proposalCard.proposal) + " · " + (proposalCard.proposal.statusText || "待应用")
                                    color: theme.textPrimary; font.pixelSize: 13; wrapMode: Text.Wrap
                                }


                                Label {
                                    objectName: "assistantProposalIntent"
                                    visible: proposalCard.expanded
                                    Layout.fillWidth: true
                                    text: "意图：" + (proposalCard.proposal.intentLabel || "按当前请求")
                                    color: theme.accentPrimary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeSmall
                                    elide: Text.ElideRight
                                }

                                Label {
                                    objectName: "assistantProposalConcreteDiff"
                                    Layout.fillWidth: true
                                    text: root.proposalConcreteDiff(
                                        root.ready ? root.assistant.proposalChanges : ({ available: false }),
                                        3
                                    )
                                    visible: proposalCard.expanded && text.length > 0
                                    wrapMode: Text.Wrap
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                    color: theme.textSecondary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeSmall
                                    Accessible.name: text
                                }

                                Label {
                                    objectName: "assistantProposalQualityWarning"
                                    Layout.fillWidth: true
                                    visible: String(proposalCard.inspector.qualityWarning || "").length > 0
                                    text: "注意：" + (proposalCard.inspector.qualityWarning || "")
                                    wrapMode: Text.Wrap
                                    color: theme.warning
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeSmall
                                }

                                Label {
                                    Layout.fillWidth: true
                                    visible: proposalCard.expanded
                                    text: proposalCard.proposal.barStart > 0
                                        ? "第 " + proposalCard.proposal.barStart + "–"
                                            + proposalCard.proposal.barEnd + " 小节"
                                        : "基于当前工程范围"
                                    wrapMode: Text.Wrap
                                    color: theme.textSecondary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeSmall
                                }

                                RowLayout {
                                    objectName: "assistantProposalMetrics"
                                    visible: proposalCard.expanded
                                    Layout.fillWidth: true
                                    spacing: theme.space2

                                    Rectangle {
                                        implicitWidth: addedMetric.implicitWidth + theme.space4
                                        implicitHeight: 30
                                        radius: theme.radiusS
                                        color: theme.surfaceSelected
                                        border.color: theme.success

                                        Label {
                                            id: addedMetric
                                            anchors.centerIn: parent
                                            text: "+ " + (proposalCard.proposal.notesAdded || 0) + " 个音符"
                                            color: theme.success
                                            font.family: theme.fontUi
                                            font.pixelSize: theme.typeSmall
                                        }
                                    }

                                    Rectangle {
                                        implicitWidth: changedMetric.implicitWidth + theme.space4
                                        implicitHeight: 30
                                        radius: theme.radiusS
                                        color: theme.surface
                                        border.color: theme.warning

                                        Label {
                                            id: changedMetric
                                            anchors.centerIn: parent
                                            text: "调整 " + ((proposalCard.proposal.notesUpdated || 0)
                                                + (proposalCard.proposal.notesDeleted || 0)) + " 个音符"
                                            color: theme.warning
                                            font.family: theme.fontUi
                                            font.pixelSize: theme.typeSmall
                                        }
                                    }

                                    Rectangle {
                                        implicitWidth: bpmMetric.implicitWidth + theme.space4
                                        implicitHeight: 30
                                        radius: theme.radiusS
                                        color: theme.surface
                                        border.color: theme.info

                                        Label {
                                            id: bpmMetric
                                            anchors.centerIn: parent
                                            text: proposalCard.proposal.bpmBefore === null
                                                    || proposalCard.proposal.bpmAfter === null
                                                ? "BPM 未改变"
                                                : proposalCard.proposal.bpmBefore === proposalCard.proposal.bpmAfter
                                                    ? "BPM 未改变"
                                                    : "BPM " + proposalCard.proposal.bpmBefore
                                                        + " → " + proposalCard.proposal.bpmAfter
                                            color: theme.info
                                            font.family: theme.fontUi
                                            font.pixelSize: theme.typeSmall
                                        }
                                    }

                                    Item { Layout.fillWidth: true }
                                }

                                RowLayout {
                                    visible: proposalCard.expanded
                                    Layout.minimumWidth: 0
                                    Layout.fillWidth: true
                                    spacing: theme.space2
                                    AppButton {
                                        objectName: "assistantProposalOnlyChangesToggle"
                                        theme: root.theme
                                        Layout.preferredWidth: 150
                                        Layout.minimumWidth: 0
                                        checkable: true
                                        checked: root.ready && root.assistant.proposalOnlyChanges
                                        checkedExternallyControlled: true
                                        text: "仅显示改动"
                                        accessibleName: "仅显示改动（编辑器预览）"
                                        enabled: root.ready && proposalCard.proposal.status === "pending"
                                        variant: checked ? "secondary" : "ghost"
                                        onToggled: root.assistant.setProposalOnlyChanges(checked)
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        visible: !root.docked
                                        text: "编辑器预览：新增绿色 · 删除红色 · 修改黄色轮廓"
                                        color: theme.textSecondary
                                        font.family: theme.fontUi
                                        font.pixelSize: theme.typeSmall
                                        elide: Text.ElideRight
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    AppButton { objectName: "assistantProposalAuditionButton"; theme: root.theme; text: "试听"; iconSource: "icons/play.svg"; accessibleName: "试听修改版"; implicitHeight: 28; labelPixelSize: 12; enabled: root.ready && proposalCard.proposal.status === "pending"; onClicked: root.assistant.auditionProposal() }
                                    AppButton { objectName: "assistantProposalApplyButton"; theme: root.theme; text: "应用"; variant: "primary"; implicitHeight: 28; labelPixelSize: 12; enabled: root.ready && proposalCard.proposal.status === "pending"; onClicked: root.assistant.applyProposal() }
                                    Item { Layout.fillWidth: true }
                                    AppButton {
                                        id: proposalMore; objectName: "assistantProposalMore"; theme: root.theme; text: "···"; variant: "ghost"; implicitHeight: 28; implicitWidth: 28
                                        accessibleName: "修改建议详情与操作"
                                        onClicked: proposalMenu.open()
                                        StudioOptionPopup {
                                            id: proposalMenu; theme: root.theme; x: Math.min(0, proposalMore.width - width); y: proposalMore.height
                                            StudioOptionItem { theme: root.theme; text: proposalCard.expanded ? "收起摘要" : "展开摘要"; onClicked: { proposalMenu.close(); proposalCard.expanded = !proposalCard.expanded } }
                                            StudioOptionItem { objectName: "assistantProposalOriginalAuditionButton"; theme: root.theme; text: "试听原版"; enabled: proposalCard.proposal.status === "pending"; onClicked: { proposalMenu.close(); root.assistant.auditionOriginalProposal() } }
                                            StudioOptionItem { objectName: "assistantProposalChangesButton"; theme: root.theme; text: "查看音符变化与作品信息"; onClicked: { proposalMenu.close(); proposalChangesDialog.open() } }
                                            StudioOptionItem { theme: root.theme; text: "仅显示改动"; checked: root.ready && root.assistant.proposalOnlyChanges; onClicked: { proposalMenu.close(); root.assistant.setProposalOnlyChanges(!root.assistant.proposalOnlyChanges) } }
                                            StudioOptionItem { objectName: "assistantProposalDiscardButton"; theme: root.theme; text: "放弃建议"; enabled: proposalCard.proposal.status === "pending"; onClicked: { proposalMenu.close(); root.assistant.discardProposal() } }
                                        }
                                    }
                                }

                            }
                        }
                    }
                    }
                }
            AppButton {
                objectName: "assistantNewMessagesButton"
                anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom
                theme: root.theme; text: "↓ 新消息"; implicitHeight: 28
                visible: !root.followLatest && root.hasUnreadIncoming
                onClicked: root.scrollToBottom()
            }
        }
        AppButton {
            objectName: "assistantPendingWorkButton"; theme: root.theme; text: "待应用作品 ↑"; variant: "ghost"; implicitHeight: 26; labelPixelSize: 12
            visible: !root.followLatest && root.ready && (root.assistant.taskPreviewAvailable || (root.assistant.proposal.available && root.assistant.proposal.status === "pending"))
            onClicked: root.revealPendingTask()
        }
            Frame {
                objectName: "songRecoveryFeedback"
                Layout.fillWidth: true
                visible: recoveryLab !== null && (recoveryLab.busy || recoveryLab.liveCandidates.length > 0 || recoveryLab.failure.message !== undefined)
                ColumnLayout {
                    anchors.fill: parent
                    Label { text: "找歌 · " + (recoveryLab ? recoveryLab.status : ""); textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Label { visible: recoveryLab !== null && recoveryLab.failure.message !== undefined; text: recoveryLab ? (recoveryLab.failure.message || "") : ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Repeater {
                        model: recoveryLab ? recoveryLab.liveCandidates.slice(-3) : []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Label { text: [modelData.title, modelData.artist, modelData.edition].filter(Boolean).join(" · "); textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Label { text: modelData.message; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Button { text: modelData.url; Layout.fillWidth: true; onClicked: recoveryLab.openSource(modelData.url) }
                        }
                    }
                    TextField {
                        objectName: "songRecoveryClue"
                        Layout.fillWidth: true; maximumLength: 1000
                        placeholderText: "补充歌手或版本，继续找歌…"
                        onAccepted: { recoveryLab.supplementClue(text); clear() }
                    }
                    RowLayout {
                        Button { text: "查看找歌任务 / 确认新谱源"; onClicked: recoveryLab.open() }
                        Button { text: "取消找歌"; enabled: recoveryLab !== null && recoveryLab.busy; onClicked: recoveryLab.cancel() }
                    }
                }
            }
        SongSourceConfirmationCard {
            Layout.fillWidth: true
            theme: root.theme
            source: root.ready ? (root.assistant.songSourceConfirmation || ({})) : ({})
            visible: root.ready && Object.keys(source).length > 0
            onConfirmed: root.assistant.confirmSongSource()
            onRejected: root.assistant.rejectSongSource()
        }
        AssistantComposer {
            id: composer
            Layout.fillWidth: true; Layout.maximumWidth: 410; Layout.alignment: Qt.AlignHCenter
            theme: root.theme; workspace: root
        }
    }

    Dialog {
        id: creationScopeDialog
        objectName: "assistantCreationScopeDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(440, parent ? parent.width - 32 : 440)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: 16
        title: "修改范围"
        header: DialogHeader { theme: root.theme; title: creationScopeDialog.title }
        property bool invalidScope: false
        onOpened: {
            var scope = root.configuredCreationScope
            scopeStartBarField.text = String(scope.startBar)
            scopeEndBarField.text = String(scope.endBar)
            invalidScope = false
        }
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderStrong
        }
        contentItem: ColumnLayout {
            spacing: theme.space3

            Label {
                Layout.fillWidth: true
                text: "AI 只能修改授权范围内的音乐。"
                wrapMode: Text.Wrap
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: theme.space2

                Label {
                    text: "从第"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                            font.pixelSize: theme.typeBody
                }

                TextField {
                    id: scopeStartBarField
                    objectName: "assistantCreationScopeStartBar"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 48
                    inputMethodHints: Qt.ImhDigitsOnly
                    validator: IntValidator { bottom: 1; top: 9999 }
                    color: theme.textPrimary
                    font.family: theme.numberFont
                    font.pixelSize: theme.typeBody
                    selectByMouse: true
                    Accessible.name: "授权起始小节"
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.inputBackground
                        border.color: scopeStartBarField.activeFocus
                            ? theme.focusRing : theme.borderStrong
                        border.width: scopeStartBarField.activeFocus ? 2 : 1
                    }
                }

                Label {
                    text: "到第"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeBody
                }

                TextField {
                    id: scopeEndBarField
                    objectName: "assistantCreationScopeEndBar"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 48
                    inputMethodHints: Qt.ImhDigitsOnly
                    validator: IntValidator { bottom: 1; top: 9999 }
                    color: theme.textPrimary
                    font.family: theme.numberFont
                    font.pixelSize: theme.typeBody
                    selectByMouse: true
                    Accessible.name: "授权结束小节"
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.inputBackground
                        border.color: scopeEndBarField.activeFocus
                            ? theme.focusRing : theme.borderStrong
                        border.width: scopeEndBarField.activeFocus ? 2 : 1
                    }
                }

                Label {
                    text: "小节"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeBody
                }
            }

            Label {
                Layout.fillWidth: true
                visible: creationScopeDialog.invalidScope
                text: "结束小节必须大于或等于起始小节。"
                color: theme.danger
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
            }
        }
        footer: DialogFooter {
            theme: root.theme
            spacing: theme.space2
            Item { Layout.fillWidth: true }
            AppButton {
                theme: root.theme
                text: "不限范围"
                onClicked: {
                    if (root.ready)
                        root.assistant.setCreationScopeAutomatic()
                    creationScopeDialog.close()
                }
            }
            AppButton {
                theme: root.theme
                text: "取消"
                onClicked: creationScopeDialog.close()
            }
            AppButton {
                objectName: "assistantCreationScopeConfirmButton"
                theme: root.theme
                text: "保存范围"
                variant: "primary"
                onClicked: {
                    var startBar = Number(scopeStartBarField.text)
                    var endBar = Number(scopeEndBarField.text)
                    if (!root.ready || startBar < 1 || endBar < startBar
                            || !root.assistant.setCreationScopeBars(startBar, endBar)) {
                        creationScopeDialog.invalidScope = true
                        return
                    }
                    creationScopeDialog.close()
                }
            }
        }
    }

    Dialog {
        id: collaborationConfirmDialog
        objectName: "assistantCollaborationConfirmDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(430, parent ? parent.width - 32 : 430)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        title: "开启协作模式？"
        header: DialogHeader { theme: root.theme; title: collaborationConfirmDialog.title }
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderStrong
        }
        contentItem: Label {
            text: "AI 可以直接修改当前工程；本轮修改仍然可以使用 Ctrl+Z 一次撤销。"
            wrapMode: Text.Wrap
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeBody
        }
        footer: DialogFooter {
            theme: root.theme
            spacing: theme.space2
            Item { Layout.fillWidth: true }
            AppButton {
                theme: root.theme
                text: "取消"
                onClicked: collaborationConfirmDialog.close()
            }
            AppButton {
                theme: root.theme
                text: "开启协作"
                variant: "primary"
                onClicked: {
                    if (root.ready)
                        root.assistant.confirmCollaborationMode()
                    collaborationConfirmDialog.close()
                }
            }
        }
    }

    Dialog {
        id: proposalChangesDialog
        header: DialogHeader { theme: root.theme; title: "音乐修改详情"; padding: 16; bottomPadding: 8 }
        objectName: "assistantProposalChangesDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(720, parent ? parent.width - 32 : 720)
        height: Math.min(
            (parent ? parent.height : 760) - 32,
            Math.max(
                420,
                Math.min(
                    (parent ? parent.height : 760) - 16,
                    proposalInspectorContent.implicitHeight + theme.space6 + 72
                )
            )
        )
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        property var inspector: root.ready
            ? root.proposalInspector(root.assistant.proposalChanges)
            : ({ available: false })
        property bool detailsExpanded: false
        property bool technicalDetailsExpanded: false
        property bool showAllMetrics: false
        property var displayedDetails: {
            var data = inspector.details || ({})
            if (detailsExpanded) {
                return (data.added || []).concat(data.updated || []).concat(data.deleted || [])
            }
            return inspector.representativeDetails || []
        }

        function summaryRows() {
            var summary = inspector.summary || ({})
            var bpm = inspector.bpm || ({})
            var rows = [
                { label: "力度", value: proposalNumber(summary.velocityChanged, 0), tone: "warning" },
                { label: "音高", value: proposalNumber(summary.pitchChanged, 0), tone: "accent" },
                { label: "节奏", value: proposalNumber(
                    (summary.positionChanged || 0) + (summary.durationChanged || 0), 0), tone: "info" },
                { label: "新增", value: proposalNumber(summary.notesAdded, 0), tone: "success" },
                { label: "删除", value: proposalNumber(summary.notesDeleted, 0), tone: "danger" },
                { label: "乐器", value: proposalNumber(summary.instrumentChanged, 0), tone: "accent" },
                { label: "和弦", value: proposalNumber(summary.harmonyChanged, 0), tone: "success" },
                { label: "BPM", value: bpm.changed
                        ? (proposalNumber(bpm.before, "—") + " → " + proposalNumber(bpm.after, "—"))
                        : "未改变", tone: "muted" }
            ]
            return showAllMetrics ? rows : rows.filter(function(row) {
                return Number(row.value) !== 0 && row.value !== "未改变"
            })
        }

        function summaryTone(tone) {
            if (tone === "warning")
                return theme.warning
            if (tone === "accent")
                return theme.accentPrimary
            if (tone === "info")
                return theme.info
            if (tone === "success")
                return theme.success
            if (tone === "danger")
                return theme.danger
            return theme.textSecondary
        }

        onOpened: {
            detailsExpanded = false
            technicalDetailsExpanded = false
            showAllMetrics = false
        }
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderStrong
        }
        contentItem: ScrollView {
            id: proposalInspectorScroll
            clip: true
            rightPadding: root.theme.scrollbarGutter
            contentWidth: availableWidth
            ScrollBar.vertical: SafeScrollBar { theme: root.theme }
            ColumnLayout {
                id: proposalInspectorContent
                width: proposalInspectorScroll.availableWidth
                spacing: theme.space2

                Label {
                    objectName: "assistantProposalInspectorTitle"
                    visible: false // The dialog header stays visible while details scroll.
                    Layout.fillWidth: true
                    text: "AI 修改建议"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSection
                    font.weight: theme.weightBold
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: inspectorHeadline.implicitHeight + theme.space4
                    radius: theme.radiusM
                    color: theme.surfaceSelected
                    border.color: theme.accentPrimary

                    Label {
                        id: inspectorHeadline
                        objectName: "assistantProposalInspectorHeadline"
                        anchors.fill: parent
                        anchors.margins: theme.space2
                        text: proposalChangesDialog.inspector.available
                            ? (proposalChangesDialog.inspector.headline || "本次包含音乐变化。")
                            : "当前没有可查看的修改建议。"
                        wrapMode: Text.Wrap
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBodyStrong
                        font.weight: theme.weightSemibold
                        Accessible.name: text
                    }
                }

                Rectangle {
                    objectName: "assistantProposalIntentActual"
                    Layout.fillWidth: true
                    implicitHeight: intentActualLabel.implicitHeight + theme.space4
                    radius: theme.radiusM
                    color: theme.surface
                    border.color: theme.borderSubtle

                    Label {
                        id: intentActualLabel
                        anchors.fill: parent
                        anchors.margins: theme.space2
                        text: "目标：" + ((proposalChangesDialog.inspector.intent || ({})).label || "按当前请求")
                            + "\n实际：" + ((proposalChangesDialog.inspector.actual || ({})).headline
                                || proposalChangesDialog.inspector.headline || "暂无可用变化")
                        wrapMode: Text.Wrap
                        color: theme.textSecondary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeSmall
                    }
                }

                Rectangle {
                    objectName: "assistantProposalInspectorQualityWarning"
                    visible: String(proposalChangesDialog.inspector.qualityWarning || "").length > 0
                    Layout.fillWidth: true
                    implicitHeight: qualityWarningLabel.implicitHeight + theme.space4
                    radius: theme.radiusM
                    color: Qt.rgba(theme.warning.r, theme.warning.g, theme.warning.b, 0.10)
                    border.color: theme.warning

                    Label {
                        id: qualityWarningLabel
                        anchors.fill: parent
                        anchors.margins: theme.space2
                        text: "注意：" + (proposalChangesDialog.inspector.qualityWarning || "")
                        wrapMode: Text.Wrap
                        color: theme.warning
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeSmall
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "范围"
                        color: theme.textSecondary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeSmall
                    }
                    Label {
                        Layout.fillWidth: true
                        text: proposalChangesDialog.inspector.scope
                            ? proposalChangesDialog.inspector.scope.label : "当前工程"
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBody
                        font.weight: theme.weightSemibold
                    }
                }

                Label {
                    text: "变化摘要"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typePanelTitle
                    font.weight: theme.weightBold
                }

                GridLayout {
                    objectName: "assistantProposalSummaryGrid"
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: theme.space2
                    rowSpacing: theme.space2

                    Repeater {
                        model: proposalChangesDialog.summaryRows()
                        objectName: "assistantProposalSummaryItems"
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 42
                            radius: theme.radiusM
                            color: theme.surface
                            border.color: proposalChangesDialog.summaryTone(modelData.tone)

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: theme.space2
                                anchors.rightMargin: theme.space2
                                Label {
                                    text: modelData.label
                                    color: theme.textSecondary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeBody
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: modelData.value
                                    color: proposalChangesDialog.summaryTone(modelData.tone)
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeBodyStrong
                                    font.weight: theme.weightBold
                                }
                            }
                        }
                    }
                }

                AppButton {
                    objectName: "assistantProposalAllMetricsToggle"
                    theme: root.theme
                    variant: "ghost"
                    text: proposalChangesDialog.showAllMetrics ? "只看发生的变化" : "查看全部指标"
                    onClicked: proposalChangesDialog.showAllMetrics = !proposalChangesDialog.showAllMetrics
                }

                Rectangle {
                    visible: (proposalChangesDialog.inspector.velocity || ({ changed: 0 })).changed > 0
                    Layout.fillWidth: true
                    implicitHeight: velocitySummary.implicitHeight + theme.space4
                    radius: theme.radiusM
                    color: theme.surface
                    border.color: theme.warning

                    Label {
                        id: velocitySummary
                        anchors.fill: parent
                        anchors.margins: theme.space2
                        text: {
                            var value = proposalChangesDialog.inspector.velocity || ({})
                            return "力度变化  ·  平均 "
                                + proposalNumber(value.beforeAverage, "—") + " → "
                                + proposalNumber(value.afterAverage, "—")
                                + "\n增强 " + proposalNumber(value.increaseCount, 0)
                                + "  ·  减弱 " + proposalNumber(value.decreaseCount, 0)
                                + "  ·  不变 " + proposalNumber(value.unchangedCount, 0)
                                + "  ·  整体 " + proposalPercent(value.changePercent)
                        }
                        wrapMode: Text.Wrap
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBody
                    }
                }

                Rectangle {
                    visible: (proposalChangesDialog.inspector.pitch || ({ changed: 0 })).changed > 0
                    Layout.fillWidth: true
                    implicitHeight: pitchSummary.implicitHeight + theme.space4
                    radius: theme.radiusM
                    color: theme.surface
                    border.color: theme.accentPrimary

                    Label {
                        id: pitchSummary
                        anchors.fill: parent
                        anchors.margins: theme.space2
                        text: {
                            var value = proposalChangesDialog.inspector.pitch || ({})
                            return "音高变化  ·  升高 " + proposalNumber(value.upCount, 0)
                                + "  ·  降低 " + proposalNumber(value.downCount, 0)
                                + "  ·  最大 " + proposalNumber(value.maxSemitones, 0) + " 半音"
                                + (value.rangeStart ? "\n主要音区 " + value.rangeStart + "–" + value.rangeEnd : "")
                        }
                        wrapMode: Text.Wrap
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBody
                    }
                }

                Rectangle {
                    visible: (proposalChangesDialog.inspector.timing || ({ positionChanged: 0, durationChanged: 0 })).positionChanged > 0
                        || (proposalChangesDialog.inspector.timing || ({ positionChanged: 0, durationChanged: 0 })).durationChanged > 0
                    Layout.fillWidth: true
                    implicitHeight: timingSummary.implicitHeight + theme.space4
                    radius: theme.radiusM
                    color: theme.surface
                    border.color: theme.info

                    Label {
                        id: timingSummary
                        anchors.fill: parent
                        anchors.margins: theme.space2
                        text: {
                            var value = proposalChangesDialog.inspector.timing || ({})
                            return "节奏变化  ·  提前 " + proposalNumber(value.earlyCount, 0)
                                + "  ·  延后 " + proposalNumber(value.lateCount, 0)
                                + "\n延长 " + proposalNumber(value.longerCount, 0)
                                + "  ·  缩短 " + proposalNumber(value.shorterCount, 0)
                        }
                        wrapMode: Text.Wrap
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBody
                    }
                }

                Label {
                    text: "主要修改区域"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typePanelTitle
                    font.weight: theme.weightBold
                }

                ListView {
                    id: proposalBarList
                    objectName: "assistantProposalBarList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, 132)
                    visible: (proposalChangesDialog.inspector.bars || []).length > 0
                    clip: true
                    model: proposalChangesDialog.inspector.bars || []
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: proposalBarList.width
                        height: 30
                        color: index % 2 === 0 ? theme.surface : theme.elevatedSurface
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: theme.space2
                            anchors.rightMargin: theme.space2
                            Label {
                                text: "第 " + modelData.bar + " 小节"
                                color: theme.textPrimary
                                font.family: theme.fontUi
                                font.pixelSize: theme.typeBody
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: modelData.count + " 个音符"
                                color: theme.textSecondary
                                font.family: theme.fontUi
                                font.pixelSize: theme.typeBody
                            }
                        }
                    }
                }

                Label {
                    objectName: "assistantProposalHarmonyDetailsTitle"
                    visible: (proposalChangesDialog.inspector.harmony || []).length > 0
                    Layout.fillWidth: true
                    text: "和弦变化 · " + (proposalChangesDialog.inspector.harmonyDetailCount || 0) + " 项"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typePanelTitle
                    font.weight: theme.weightBold
                }

                ListView {
                    id: proposalHarmonyList
                    objectName: "assistantProposalHarmonyList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? Math.min(contentHeight, 196) : 0
                    visible: (proposalChangesDialog.inspector.harmony || []).length > 0
                    clip: true
                    model: proposalChangesDialog.inspector.harmony || []
                    delegate: Rectangle {
                        required property var modelData
                        width: proposalHarmonyList.width
                        implicitHeight: harmonyDetailContent.implicitHeight + theme.space3
                        radius: theme.radiusM
                        color: theme.surface
                        border.color: modelData.kind === "added" ? theme.success
                            : modelData.kind === "deleted" ? theme.danger : theme.warning

                        ColumnLayout {
                            id: harmonyDetailContent
                            anchors.fill: parent
                            anchors.margins: theme.space2
                            spacing: theme.space1
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.kindLabel
                                    color: theme.textPrimary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeBody
                                    font.weight: theme.weightSemibold
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: modelData.location
                                    color: theme.textSecondary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeSmall
                                }
                            }
                            Repeater {
                                model: modelData.fields || []
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.label
                                        color: theme.textSecondary
                                        font.family: theme.fontUi
                                        font.pixelSize: theme.typeSmall
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.display
                                        color: theme.textPrimary
                                        font.family: theme.fontUi
                                        font.pixelSize: theme.typeSmall
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                            Label {
                                visible: proposalChangesDialog.technicalDetailsExpanded
                                Layout.fillWidth: true
                                text: "和弦组 ID：" + ((modelData.technical || ({})).groupId || "")
                                color: theme.textMuted
                                font.family: theme.numberFont
                                font.pixelSize: theme.typeCaption
                                elide: Text.ElideMiddle
                            }
                        }
                    }
                }

                Label {
                    visible: (proposalChangesDialog.inspector.detailCount || 0) > 0
                    Layout.fillWidth: true
                    text: "具体修改 · " + proposalChangesDialog.inspector.detailCount
                        + " 项" + (proposalChangesDialog.detailsExpanded ? "" : "（默认收起）")
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typePanelTitle
                    font.weight: theme.weightBold
                }

                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        objectName: "assistantProposalDetailsToggle"
                        theme: root.theme
                        Layout.fillWidth: true
                        text: proposalChangesDialog.detailsExpanded ? "收起具体修改" : "查看具体修改"
                        iconSource: proposalChangesDialog.detailsExpanded ? "icons/chevron-left.svg" : "icons/chevron-right.svg"
                        enabled: (proposalChangesDialog.inspector.detailCount || 0) > 0
                        accessibleName: text
                        onClicked: proposalChangesDialog.detailsExpanded = !proposalChangesDialog.detailsExpanded
                    }
                    AppButton {
                        objectName: "assistantProposalTechnicalDetailsToggle"
                        theme: root.theme
                        Layout.preferredWidth: 144
                        checkable: true
                        checked: proposalChangesDialog.technicalDetailsExpanded
                        checkedExternallyControlled: true
                        enabled: proposalChangesDialog.detailsExpanded
                        text: "技术详情"
                        variant: proposalChangesDialog.technicalDetailsExpanded ? "secondary" : "ghost"
                        accessibleName: "技术详情（包含音符 ID）"
                        onToggled: proposalChangesDialog.technicalDetailsExpanded = checked
                    }
                }

                ListView {
                    id: proposalDetailsList
                    objectName: "assistantProposalDetailsList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, 320)
                    visible: proposalChangesDialog.detailsExpanded
                        && (proposalChangesDialog.inspector.detailCount || 0) > 0
                    clip: true
                    model: proposalChangesDialog.displayedDetails
                    delegate: Rectangle {
                        required property var modelData
                        width: proposalDetailsList.width
                        implicitHeight: detailContent.implicitHeight + theme.space3
                        radius: theme.radiusM
                        color: theme.surface
                        border.color: modelData.kind === "added" ? theme.success
                            : modelData.kind === "deleted" ? theme.danger : theme.warning

                        ColumnLayout {
                            id: detailContent
                            anchors.fill: parent
                            anchors.margins: theme.space2
                            spacing: theme.space1
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.kindLabel + "  " + modelData.noteName
                                    color: theme.textPrimary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeBody
                                    font.weight: theme.weightSemibold
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: modelData.location
                                    color: theme.textSecondary
                                    font.family: theme.fontUi
                                    font.pixelSize: theme.typeSmall
                                }
                            }
                            Repeater {
                                model: modelData.fields || []
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.label
                                        color: theme.textSecondary
                                        font.family: theme.fontUi
                                        font.pixelSize: theme.typeSmall
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.display
                                        color: theme.textPrimary
                                        font.family: theme.fontUi
                                        font.pixelSize: theme.typeSmall
                                    }
                                }
                            }
                            Label {
                                visible: proposalChangesDialog.technicalDetailsExpanded
                                Layout.fillWidth: true
                                text: "ID：" + modelData.technical.noteId
                                color: theme.textMuted
                                font.family: theme.numberFont
                                font.pixelSize: theme.typeCaption
                                elide: Text.ElideMiddle
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            Accessible.name: "定位到" + (modelData.noteName || "音符")
                            onClicked: {
                                if (root.ready && modelData.technical
                                        && modelData.technical.noteId)
                                    root.assistant.locateProposalNote(
                                        String(modelData.technical.noteId)
                                    )
                            }
                        }
                    }
                }

                Label {
                    visible: (proposalChangesDialog.inspector.detailCount || 0) > 8
                        && !proposalChangesDialog.detailsExpanded
                    Layout.fillWidth: true
                    text: "已显示 8 项代表性变化；点击“查看具体修改”查看全部。"
                    wrapMode: Text.Wrap
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
            }
        }
        footer: DialogFooter {
            theme: root.theme
            Item { Layout.fillWidth: true }
            AppButton {
                theme: root.theme
                text: "关闭"
                onClicked: proposalChangesDialog.close()
            }
        }
    }

    Dialog {
        id: settingsDialog
        objectName: "assistantSettingsDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(760, (parent ? parent.width : 792) - 32)
        height: Math.min(720, (parent ? parent.height : 752) - 32)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        property bool testAfterSave: false
        property bool creatingCustomConnection: false
        property bool showSetupHelp: false
        readonly property bool connectionDirty: creatingCustomConnection
            || providerNameField.text !== (setup.displayName || "")
            || baseUrlField.text !== (setup.baseUrl || "")
            || modelIdField.text !== (setup.modelId || "")
            || apiKeyField.text.length > 0
        onOpened: loadCurrentConnection()
        function loadCurrentConnection() {
            setup = root.ready ? root.assistant.providerSetup(root.assistant.currentProviderId) : ({})
            creatingCustomConnection = false
            providerNameField.text = setup.displayName || ""
            baseUrlField.text = setup.baseUrl || ""
            modelIdField.text = setup.modelId || ""
            apiKeyField.text = ""
        }
        function startConnection(name, endpoint) {
            root.assistant.selectProvider("compatible")
            creatingCustomConnection = true
            // Never carry a key or a model from one endpoint into another.
            providerNameField.text = name
            baseUrlField.text = endpoint
            modelIdField.text = ""
            apiKeyField.text = ""
        }
        property var setup: root.ready ? root.assistant.providerSetup(root.assistant.currentProviderId) : ({})

        function saveCurrent(acknowledged, testAfter) {
            if (!root.ready)
                return
            if (!acknowledged && root.assistant.requiresThirdPartyWarning(
                    root.assistant.currentProviderId, baseUrlField.text)) {
                settingsDialog.testAfterSave = testAfter
                thirdPartyWarning.open()
                return
            }
            var result = creatingCustomConnection
                ? root.assistant.createCustomConnection(
                    providerNameField.text,
                    baseUrlField.text,
                    apiKeyField.text,
                    modelIdField.text,
                    acknowledged
                )
                : root.assistant.saveProviderSettings(
                    root.assistant.currentProviderId,
                    providerNameField.text,
                    baseUrlField.text,
                    apiKeyField.text,
                    modelIdField.text,
                    acknowledged
                )
            if (result.needsAcknowledgement) {
                settingsDialog.testAfterSave = testAfter
                thirdPartyWarning.open()
                return
            }
            apiKeyField.text = ""
            if (result.success)
                loadCurrentConnection()
            if (result.success && testAfter)
                root.assistant.testConnectionAsync(root.assistant.currentProviderId)
        }

        title: "模型连接"
        header: Label {
            text: settingsDialog.title
            color: root.theme.textPrimary
            font.family: root.theme.fontUi
            font.pixelSize: 20
            font.weight: Font.DemiBold
            leftPadding: settingsDialog.padding
            rightPadding: settingsDialog.padding
            topPadding: 20
            bottomPadding: 8
        }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.42) }
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderStrong
        }
        contentItem: ScrollView {
            id: connectionSettingsScroll
            objectName: "assistantConnectionScroll"
            clip: true
            rightPadding: root.theme.scrollbarGutter
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical: SafeScrollBar {
                objectName: "assistantConnectionScrollBar"
                theme: root.theme
                parent: connectionSettingsScroll
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                visible: size < 1
            }
            ColumnLayout {
                width: connectionSettingsScroll.availableWidth
                spacing: theme.space3

                Label {
                    Layout.fillWidth: true
                    text: settingsDialog.creatingCustomConnection ? "新建连接" : "当前连接"
                    color: theme.textPrimary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSection
                    font.weight: theme.weightBold
                }
                ListView {
                    id: connectionProfileSelector
                    objectName: "assistantConnectionProfileSelector"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 86
                    orientation: ListView.Horizontal
                    spacing: 8; clip: true
                    model: root.ready ? root.assistant.connectionProfiles : []
                    currentIndex: settingsDialog.creatingCustomConnection ? -1
                        : root.profileIndex(model, root.ready ? root.assistant.currentProviderId : "")
                    Accessible.name: "连接列表"
                    function selectConnection(index, focusCard) {
                        if (!root.ready || index < 0 || index >= count)
                            return
                        if (!root.assistant.selectProvider(model[index].id))
                            return
                        settingsDialog.loadCurrentConnection()
                        positionViewAtIndex(index, ListView.Contain)
                        if (focusCard) Qt.callLater(function() {
                            var card = connectionProfileSelector.itemAtIndex(index)
                            if (card) card.forceActiveFocus(Qt.TabFocusReason)
                        })
                    }
                    ScrollBar.horizontal: SafeScrollBar { theme: root.theme }
                    delegate: AppButton {
                        required property var modelData
                        required property int index
                        objectName: "assistantConnection-" + modelData.id
                        width: Math.max(146, (connectionProfileSelector.width - 24) / 4); height: 62
                        theme: root.theme
                        text: modelData.displayName || modelData.id
                        checkable: true; checkedExternallyControlled: true
                        checked: connectionProfileSelector.currentIndex === index
                        onClicked: connectionProfileSelector.selectConnection(index, false)
                        onActiveFocusChanged: if (activeFocus)
                            connectionProfileSelector.positionViewAtIndex(index, ListView.Contain)
                        Keys.onLeftPressed: connectionProfileSelector.selectConnection(index - 1, true)
                        Keys.onRightPressed: connectionProfileSelector.selectConnection(index + 1, true)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        objectName: "assistantAddConnectionButton"
                        theme: root.theme; text: "添加连接"; variant: "secondary"
                        onClicked: connectionTemplates.open()
                        AppMenu {
                            id: connectionTemplates
                            objectName: "assistantConnectionTemplates"
                            theme: root.theme
                            Action { text: "自定义兼容接口"; onTriggered: settingsDialog.startConnection("", "https://") }
                            AppMenuSeparator { theme: root.theme }
                            Action { text: "通义 · 百炼（北京）"; onTriggered: settingsDialog.startConnection("百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1") }
                            Action { text: "智谱"; onTriggered: settingsDialog.startConnection("智谱", "https://open.bigmodel.cn/api/paas/v4") }
                            Action { text: "硅基流动"; onTriggered: settingsDialog.startConnection("硅基流动", "https://api.siliconflow.cn/v1") }
                            Action { text: "Kimi / Moonshot（国际）"; onTriggered: settingsDialog.startConnection("Kimi", "https://api.moonshot.ai/v1") }
                            Action { text: "Gemini（兼容接口）"; onTriggered: settingsDialog.startConnection("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai") }
                            Action { text: "OpenRouter"; onTriggered: settingsDialog.startConnection("OpenRouter", "https://openrouter.ai/api/v1") }
                        }
                    }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        objectName: "assistantDeleteConnectionButton"
                        theme: root.theme
                        text: "删除连接"
                        visible: !settingsDialog.creatingCustomConnection && settingsDialog.setup.canDelete === true
                        enabled: visible && root.ready
                        variant: "ghost"
                        onClicked: {
                            if (root.assistant.deleteCurrentCustomConnection())
                                settingsDialog.loadCurrentConnection()
                        }
                    }
                }
                AppButton {
                    theme: root.theme; variant: "ghost"
                    text: settingsDialog.showSetupHelp ? "收起连接帮助" : "连接帮助"
                    onClicked: settingsDialog.showSetupHelp = !settingsDialog.showSetupHelp
                }
                Label {
                    objectName: "assistantModelStatusDetail"
                    Layout.fillWidth: true
                    visible: settingsDialog.showSetupHelp || root.modelStatus.state === "verification_failed"
                    text: root.ready ? root.modelStatus.detail : "请选择一个模型后再验证创作能力。"
                    wrapMode: Text.Wrap
                    color: root.modelStatus.state === "ready" ? theme.success
                        : root.modelStatus.state === "verification_failed" ? theme.warning
                            : theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                Repeater {
                    model: settingsDialog.showSetupHelp && settingsDialog.setup.setupSteps ? settingsDialog.setup.setupSteps : []
                    delegate: Label {
                        required property int index
                        required property string modelData
                        Layout.fillWidth: true
                        text: (index + 1) + ". " + modelData
                        wrapMode: Text.Wrap
                        color: theme.textSecondary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeSmall
                    }
                }
                AppButton {
                    objectName: "assistantApiKeyLinkButton"
                    theme: root.theme
                    text: "获取 " + (settingsDialog.setup.label ? settingsDialog.setup.label : "Provider") + " API Key"
                    visible: settingsDialog.setup.requiresApiKey === true
                        && !!settingsDialog.setup.documentationUrl
                        && settingsDialog.setup.documentationUrl.length > 0
                    Accessible.name: text
                    onClicked: Qt.openUrlExternally(settingsDialog.setup.documentationUrl)
                }
                AppButton {
                    theme: root.theme
                    text: "打开官方说明"
                    visible: settingsDialog.setup.requiresApiKey !== true
                        && !!settingsDialog.setup.documentationUrl
                        && settingsDialog.setup.documentationUrl.length > 0
                    onClicked: Qt.openUrlExternally(settingsDialog.setup.documentationUrl)
                }

                Label {
                    visible: settingsDialog.setup.supportsDisplayName === true
                    text: "连接名称"
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                TextField {
                    id: providerNameField
                    objectName: "assistantProviderNameField"
                    visible: settingsDialog.setup.supportsDisplayName === true
                    Layout.fillWidth: true
                    text: settingsDialog.setup.displayName ? settingsDialog.setup.displayName : ""
                    placeholderText: "例如：我的创作模型"
                    selectByMouse: true
                    Accessible.name: "连接名称"
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    selectionColor: theme.selectedBackground
                    selectedTextColor: theme.textPrimary
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.inputBackground
                        border.color: providerNameField.activeFocus ? theme.focusRing : theme.borderStrong
                        border.width: providerNameField.activeFocus ? 2 : 1
                    }
                }

                Label {
                    text: "API 地址"
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                TextField {
                    id: baseUrlField
                    objectName: "assistantBaseUrlField"
                    Layout.fillWidth: true
                    text: settingsDialog.setup.baseUrl ? settingsDialog.setup.baseUrl : ""
                    placeholderText: "https://…"
                    selectByMouse: true
                    inputMethodHints: Qt.ImhUrlCharactersOnly
                    Accessible.name: "Base URL"
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    selectionColor: theme.selectedBackground
                    selectedTextColor: theme.textPrimary
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.inputBackground
                        border.color: baseUrlField.activeFocus ? theme.focusRing : theme.borderStrong
                        border.width: baseUrlField.activeFocus ? 2 : 1
                    }
                }

                Label {
                    visible: settingsDialog.setup.requiresApiKey === true
                    text: "API Key"
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                Label {
                    objectName: "assistantSavedApiKeyMask"
                    Layout.fillWidth: true
                    visible: settingsDialog.setup.requiresApiKey === true
                        && !settingsDialog.creatingCustomConnection && settingsDialog.setup.credentialStatus === "saved"
                    text: "已保存：••••••••••••"
                    color: theme.success
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                    Accessible.name: "API Key 已保存"
                }
                TextField {
                    id: apiKeyField
                    objectName: "assistantApiKeyField"
                    visible: settingsDialog.setup.requiresApiKey === true
                    Layout.fillWidth: true
                    placeholderText: !settingsDialog.creatingCustomConnection && settingsDialog.setup.credentialStatus === "saved"
                        ? "留空保留已保存的密钥"
                        : "粘贴 API Key"
                    echoMode: TextInput.Password
                    inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                    selectByMouse: true
                    Accessible.name: "API Key"
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    selectionColor: theme.selectedBackground
                    selectedTextColor: theme.textPrimary
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.inputBackground
                        border.color: apiKeyField.activeFocus ? theme.focusRing : theme.borderStrong
                        border.width: apiKeyField.activeFocus ? 2 : 1
                    }
                }

                    ComboBox {
                        id: reasoningSelector
                        objectName: "assistantReasoningSelector"
                        readonly property bool reasoningSupported: {
                            var choices = root.ready ? root.assistant.reasoningCatalog : []
                            for (var index = 0; index < choices.length; ++index) {
                                if (choices[index].id !== "off" && choices[index].enabled)
                                    return true
                            }
                            return false
                        }
                        Layout.preferredWidth: 126
                        Layout.preferredHeight: 44
                        visible: reasoningSupported
                        model: root.ready ? root.assistant.reasoningCatalog : []
                        textRole: "label"
                        displayText: "思考：" + currentText
                        currentIndex: root.catalogIndex(model, root.ready ? root.assistant.currentReasoningId : "off")
                        enabled: root.ready
                        Accessible.name: "思考强度，当前为 " + currentText
                        contentItem: Text {
                            leftPadding: theme.space3
                            rightPadding: theme.space7
                            text: reasoningSelector.displayText
                            color: reasoningSelector.enabled ? theme.textPrimary : theme.textDisabled
                            font.family: theme.fontUi
                            font.pixelSize: theme.typeSmall
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                        background: Rectangle {
                            radius: theme.radiusM
                            color: reasoningSelector.hovered ? theme.surfaceHover : theme.elevatedSurface
                            border.color: theme.accentSelection
                            border.width: reasoningSelector.activeFocus ? 2 : 1
                        }
                        indicator: Text {
                            x: reasoningSelector.width - width - theme.space2
                            y: (reasoningSelector.height - height) / 2
                            text: "⌄"
                            color: theme.accentSelection
                            font.family: theme.fontSymbol
                            font.pixelSize: theme.typeSmall
                        }
                        delegate: ItemDelegate {
                            required property int index
                            required property var modelData
                            width: Math.max(0, reasoningSelector.width - root.theme.scrollbarGutter)
                            implicitHeight: 44
                            text: modelData.label + (modelData.enabled ? "" : "（不支持）")
                            enabled: modelData.enabled
                            highlighted: reasoningSelector.highlightedIndex === index
                            background: Rectangle {
                                color: parent.enabled
                                    ? (parent.hovered ? theme.surfaceHover : theme.popupBackground)
                                    : theme.surface
                                border.color: parent.highlighted ? theme.accentSelection : "transparent"
                            }
                            onClicked: {
                                reasoningSelector.activated(index)
                                reasoningSelector.popup.close()
                            }
                        }
                        popup: Popup {
                            y: reasoningSelector.height
                            width: reasoningSelector.width
                            height: Math.min(
                                Math.max(contentItem.implicitHeight, 52),
                                reasoningSelector.Window.height - topMargin - bottomMargin)
                            topMargin: 6
                            bottomMargin: 6
                            contentItem: ListView {
                                clip: true
                                implicitHeight: contentHeight
                                model: reasoningSelector.delegateModel
                                currentIndex: reasoningSelector.highlightedIndex
                                highlightMoveDuration: 0
                                rightMargin: root.theme.scrollbarGutter
                                ScrollBar.vertical: SafeScrollBar { theme: root.theme }
                            }
                            background: Rectangle {
                                color: theme.popupBackground
                                border.color: theme.borderStrong
                            }
                        }
                        onActivated: {
                            if (root.ready)
                                root.assistant.selectReasoning(model[currentIndex].id)
                        }
                    }

                Label {
                    text: "默认模型"
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                Label {
                    objectName: "assistantActiveModelHint"
                    Layout.fillWidth: true
                    visible: root.ready
                        && root.assistant
                        && root.assistant.currentModelId
                        && root.assistant.currentModelId !== settingsDialog.setup.modelId
                    text: root.ready && root.assistant
                        ? "当前创作与验证使用：" + root.assistant.currentModelId
                            + "；下方为保存的默认模型。"
                        : ""
                    wrapMode: Text.Wrap
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                TextField {
                    id: modelIdField
                    objectName: "assistantModelField"
                    Layout.fillWidth: true
                    text: settingsDialog.setup.modelId ? settingsDialog.setup.modelId : ""
                    placeholderText: "模型 ID"
                    selectByMouse: true
                    Accessible.name: "模型 ID"
                    color: theme.textPrimary
                    placeholderTextColor: theme.textMuted
                    selectionColor: theme.selectedBackground
                    selectedTextColor: theme.textPrimary
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.inputBackground
                        border.color: modelIdField.activeFocus ? theme.focusRing : theme.borderStrong
                        border.width: modelIdField.activeFocus ? 2 : 1
                    }
                }

                ColumnLayout {
                    objectName: "assistantModelMatches"
                    Layout.fillWidth: true
                    visible: modelIdField.activeFocus && !settingsDialog.creatingCustomConnection
                    Repeater {
                        model: root.ready ? root.assistant.modelCatalog.filter(function(entry) {
                            var query = modelIdField.text.toLowerCase().trim()
                            return entry.id !== modelIdField.text
                                && (query.length === 0 || entry.id.toLowerCase().indexOf(query) >= 0
                                    || String(entry.label || "").toLowerCase().indexOf(query) >= 0)
                        }).slice(0, 6) : []
                        AppButton {
                            required property var modelData
                            Layout.fillWidth: true; theme: root.theme; variant: "ghost"
                            text: modelData.label || modelData.id
                            Accessible.name: "使用模型 " + modelData.id
                            onClicked: { modelIdField.text = modelData.id; modelIdField.focus = false }
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: settingsDialog.connectionDirty
                    text: "保存后可测试连接"; color: theme.textSecondary
                    font.pixelSize: theme.typeSmall
                }

                RowLayout {
                    Layout.fillWidth: true
            AppButton {
                objectName: "assistantRefreshModelsButton"
                theme: root.theme
                text: root.ready && root.assistant !== null && root.assistant.catalogRefreshing
                    ? "刷新中…" : "刷新模型"
                enabled: !settingsDialog.creatingCustomConnection && root.ready && root.assistant !== null && !root.assistant.catalogRefreshing
                Layout.fillWidth: true
                onClicked: {
                    if (root.assistant !== null)
                        root.assistant.refreshModelCatalogAsync()
                }
            }
            AppButton {
                objectName: "assistantVerifyMusicToolsButton"
                theme: root.theme
                text: root.ready ? root.modelStatus.actionLabel : "验证创作能力"
                loading: root.ready && root.modelStatus.state === "verifying"
                enabled: !settingsDialog.creatingCustomConnection && root.ready && root.assistant !== null && root.modelStatus.canVerify
                Layout.fillWidth: true
                onClicked: root.assistant.verifyMusicToolsAsync()
            }
                }
                Label {
                    objectName: "assistantConnectionResult"
                    Layout.fillWidth: true
                    text: root.ready ? root.assistant.connectionResult.message : ""
                    wrapMode: Text.Wrap
                    color: root.ready && root.assistant.connectionResult.code === "SUCCESS" ? theme.success
                        : root.ready && root.assistant.connectionResult.code === "UNKNOWN_ERROR" ? theme.textSecondary
                        : theme.warning
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
            }
        }
        footer: Item {
            implicitHeight: connectionFooter.implicitHeight + 32
            GridLayout {
                id: connectionFooter
                objectName: "assistantSettingsFooter"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: settingsDialog.padding
                anchors.rightMargin: settingsDialog.padding
                anchors.top: parent.top
                anchors.topMargin: 8
                columns: 3
                columnSpacing: theme.space2
                rowSpacing: theme.space2
                AppButton {
                    theme: root.theme
                    text: "关闭"
                    Layout.fillWidth: true
                    onClicked: settingsDialog.close()
                }




                AppButton {
                    objectName: "assistantSaveProviderButton"
                    theme: root.theme
                    text: "保存"
                    enabled: root.ready
                    Layout.fillWidth: true
                    onClicked: settingsDialog.saveCurrent(false, false)
                }
                AppButton {
                    objectName: "assistantTestConnectionButton"
                    theme: root.theme
                    text: root.ready && root.assistant.connectionTesting ? "正在测试…" : "测试连接"
                    variant: "primary"
                    enabled: root.ready && !settingsDialog.connectionDirty && !root.assistant.connectionTesting
                    Layout.fillWidth: true
                    Accessible.description: settingsDialog.connectionDirty ? "先保存修改，再测试连接" : "测试当前已保存的连接"
                    ToolTip.visible: hovered && settingsDialog.connectionDirty
                    ToolTip.text: "先保存修改"
                    onClicked: root.assistant.testConnectionAsync(root.assistant.currentProviderId)
                }
            }
        }
    }

    Dialog {
        id: thirdPartyWarning
        objectName: "assistantThirdPartyWarning"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 470
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        title: "第三方服务提示"
        header: DialogHeader { theme: root.theme; title: thirdPartyWarning.title }
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.warning
        }
        contentItem: Label {
            text: "API Key 和发送给模型的音乐内容会经过该第三方服务器。AI Piano Studio 无法保证第三方服务器如何处理这些数据。"
            wrapMode: Text.Wrap
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeBody
        }
        footer: DialogFooter {
            theme: root.theme
            spacing: theme.space2
            Item { Layout.fillWidth: true }
            AppButton {
                theme: root.theme
                text: "取消"
                onClicked: thirdPartyWarning.close()
            }
            AppButton {
                theme: root.theme
                text: "我已了解并保存"
                variant: "primary"
                onClicked: {
                    thirdPartyWarning.close()
                    settingsDialog.saveCurrent(true, settingsDialog.testAfterSave)
                }
            }
        }
    }

}
