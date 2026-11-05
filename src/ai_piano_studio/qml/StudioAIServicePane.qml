import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "CatalogOrder.js" as CatalogOrder

ColumnLayout {
    id: pane
    objectName: "studioAIServicePane"
    required property Theme theme
    property var assistant: null
    property var setup: ({})
    property string selectedId: ""
    property bool creating: false
    property bool advanced: false
    property bool frameworkExpanded: false
    property var leaveAction: null
    property var afterSaveAction: null
    property bool testAfterSave: false
    property bool componentReady: false
    property bool saveFailed: false
    property bool syncAfterSave: false
    readonly property bool ready: assistant !== null
    readonly property bool generating: ready && ["thinking", "tool_running", "repairing"].indexOf(assistant.assistantStatus) >= 0
    readonly property bool checking: ready && (assistant.connectionTesting || assistant.catalogRefreshing || assistant.currentModelStatus.state === "verifying")
    readonly property bool isCodex: !creating && selectedId === "codex"
    readonly property var codex: ready ? assistant.codexStatus : ({})
    readonly property var profiles: ready ? groupedProfiles(assistant.connectionProfiles) : []
    readonly property var models: ready && !creating && selectedId === assistant.currentProviderId ? CatalogOrder.ordered(assistant.modelCatalog.filter(function(item) { return item.source === "synced" }), assistant.catalogSortMode, "label") : []
    readonly property bool dirty: creating || nameField.text !== (setup.displayName || "")
        || addressField.text !== (setup.baseUrl || "") || modelField.text !== (setup.modelId || "") || keyField.text.length > 0
    readonly property bool configured: isCodex ? codex.login === "chatgpt" && codex.supported === true : setup.credentialStatus === "saved" || setup.configured === true
    readonly property bool canSync: ready && !generating && !checking && (isCodex ? configured : setup.requiresApiKey === false || keyField.text.length > 0 || configured)
    readonly property bool guardOpen: unsavedDialog.opened || thirdPartyDialog.opened || deleteDialog.opened || sortPopup.opened
    spacing: 18
    onAssistantChanged: if (componentReady && ready && selectedId.length === 0) loadCurrent(assistant.currentProviderId)
    Component.onCompleted: { componentReady = true; if (ready) { loadCurrent(assistant.currentProviderId); if (!assistant.codexStatus.checked) assistant.detectCodexAsync() } }

    function groupedProfiles(items) {
        items = CatalogOrder.ordered(items, assistant.catalogSortMode, "displayName")
        var groups = ["已配置", "API", "订阅", "本地", "其他 / 自定义"]
        var result = []
        for (var g = 0; g < groups.length; ++g) {
            var first = true
            for (var i = 0; i < items.length; ++i) {
                var item = items[i]
                var group = item.configured ? "已配置" : item.connectionKind === "subscription" ? "订阅"
                    : item.connectionKind === "local" ? "本地" : item.connectionKind === "custom" ? "其他 / 自定义" : "API"
                if (group !== groups[g]) continue
                result.push(Object.assign({}, item, {heading: first ? group : ""}))
                first = false
            }
        }
        return result
    }
    function syncModels() {
        if (!canSync) return
        if (dirty) { syncAfterSave = true; saveCurrent(false, false) }
        else assistant.refreshModelCatalogAsync()
    }

    function loadCurrent(id) {
        if (!ready) return
        saveFailed = false
        selectedId = id
        setup = assistant.providerSetup(id)
        creating = false
        nameField.text = setup.displayName || ""
        addressField.text = setup.baseUrl || ""
        modelField.text = setup.modelId || ""
        keyField.text = ""
        modelSearch.text = ""
        advanced = false
    }
    function requestLeave(action) {
        if (dirty) { leaveAction = action; unsavedDialog.open() }
        else action()
    }
    function selectConnection(id, focusIndex) {
        if (generating || checking || (id === selectedId && !creating)) return
        requestLeave(function() {
            if (assistant.selectProvider(id)) loadCurrent(id)
            if (focusIndex >= 0) Qt.callLater(function() {
                var item = providerButtons.itemAt(focusIndex)
                if (!item) return
                item.focusTarget.forceActiveFocus(Qt.TabFocusReason)
                var flick = item.parent
                while (flick && typeof flick.contentY !== "number") flick = flick.parent
                if (flick) {
                    var point = item.mapToItem(flick, 0, 0)
                    if (point.y < 0 || point.y + item.height > flick.height)
                        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, flick.contentY + point.y - 24))
                }
            })
        })
    }
    function moveConnection(index, direction) {
        var next = index + direction
        while (next >= 0 && next < providerButtons.count) {
            if (providerButtons.itemAt(next).visible) { selectConnection(profiles[next].id, next); return }
            next += direction
        }
    }
    function newConnection() {
        if (generating || checking) return
        requestLeave(function() {
            creating = true; setup = ({}); selectedId = "compatible"
            nameField.text = ""; addressField.text = "https://"; modelField.text = ""; keyField.text = ""
            advanced = true
        })
    }
    function saveCurrent(acknowledged, testAfter) {
        if (!ready || generating || checking) return false
        saveFailed = false
        testAfterSave = testAfter
        if (!acknowledged && assistant.requiresThirdPartyWarning(selectedId, addressField.text)) {
            thirdPartyDialog.open(); return false
        }
        var result = creating
            ? assistant.createCustomConnection(nameField.text, addressField.text, keyField.text, modelField.text, acknowledged)
            : assistant.saveProviderSettings(selectedId, nameField.text, addressField.text, keyField.text, modelField.text, acknowledged)
        if (result.needsAcknowledgement) { thirdPartyDialog.open(); return false }
        if (!result.success) { saveFailed = true; syncAfterSave = false; afterSaveAction = null; return false }
        var shouldSync = syncAfterSave; syncAfterSave = false
        loadCurrent(assistant.currentProviderId)
        if (shouldSync) assistant.refreshModelCatalogAsync()
        if (testAfter) assistant.testConnectionAsync(assistant.currentProviderId)
        var action = afterSaveAction; afterSaveAction = null
        if (action) action()
        return true
    }

    component Caption: Label {
        color: pane.theme.textPrimary; font.family: pane.theme.fontUi; font.pixelSize: 13
        Layout.fillWidth: true; wrapMode: Text.WordWrap
    }
    component Field: TextField {
        Layout.fillWidth: true; implicitHeight: 34
        color: pane.theme.textPrimary; placeholderTextColor: pane.theme.textMuted
        font.family: pane.theme.fontUi; font.pixelSize: 14
        selectByMouse: true; enabled: pane.ready && !pane.generating && !pane.checking
        background: Rectangle { radius: 6; color: pane.theme.inputBackground; border.color: parent.activeFocus ? pane.theme.focusRing : pane.theme.borderStrong }
    }
    RowLayout {
        Layout.fillWidth: true
        Caption { text: "选择服务"; font.pixelSize: 14; font.weight: Font.Medium }
        AppButton { id: sortButton; objectName: "studioAISort"; theme: pane.theme; text: "排序"; variant: "ghost"; labelWeight: Font.Normal; enabled: pane.ready && !pane.checking; onClicked: sortPopup.opened ? sortPopup.close() : sortPopup.open() }
        AppButton { objectName: "studioAIAddConnection"; theme: pane.theme; text: "添加服务"; labelWeight: Font.Normal; enabled: pane.ready && !pane.generating && !pane.checking; onClicked: pane.newConnection() }
    }
    Caption { visible: !pane.ready; text: "正在加载…"; color: pane.theme.textSecondary }
    Caption { visible: pane.generating; text: "AI 正在生成，停止后可修改连接。"; color: pane.theme.textSecondary }
    RowLayout {
        Layout.fillWidth: true
        spacing: 16
        ColumnLayout {
            Layout.preferredWidth: 176; Layout.minimumWidth: 176; Layout.maximumWidth: 176
            Layout.alignment: Qt.AlignTop
            spacing: 4
            Field { id: providerSearch; objectName: "studioAIProviderSearch"; placeholderText: "搜索服务"; Accessible.name: "搜索服务商"; Layout.bottomMargin: 4 }
            ScrollView {
                id: providerScroll
                objectName: "studioAIProviderScroll"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(providerRows.implicitHeight, Math.max(200, Math.min(520, pane.Window.height - 300)))
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    id: providerRows
                    width: providerScroll.availableWidth
                    spacing: 4
            Repeater {
                id: providerButtons
                model: pane.profiles
                delegate: ColumnLayout {
                    id: providerEntry
                    required property int index
                    required property var modelData
                    property alias focusTarget: providerButton
                    Layout.fillWidth: true
                    spacing: 4
                    visible: (modelData.displayName || modelData.id).toLowerCase().indexOf(providerSearch.text.toLowerCase()) >= 0
                    Caption { visible: modelData.heading.length > 0 && providerSearch.text.length === 0; text: modelData.heading; color: pane.theme.textMuted; font.pixelSize: 12; Layout.topMargin: providerEntry.index === 0 ? 4 : 14; Layout.leftMargin: 10 }
                    AppButton {
                        id: providerButton
                        objectName: "studioAIConnection-" + providerEntry.modelData.id
                        Layout.fillWidth: true; implicitHeight: 34
                        leftPadding: 10; rightPadding: 10
                        theme: pane.theme
                        text: providerEntry.modelData.displayName || providerEntry.modelData.id
                        labelWeight: Font.Normal
                        checkable: true; checkedExternallyControlled: true
                        checked: !pane.creating && pane.selectedId === providerEntry.modelData.id
                        variant: checked ? "secondary" : "ghost"
                        enabled: !pane.generating && !pane.checking
                        contentItem: RowLayout {
                            spacing: 10
                            AIBrandIcon { theme: pane.theme; providerId: providerEntry.modelData.brandId || providerEntry.modelData.providerType || providerEntry.modelData.id; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                            Label { text: providerButton.text; font.family: pane.theme.fontUi; font.pixelSize: 13; color: pane.theme.textPrimary; elide: Text.ElideRight; Layout.fillWidth: true }
                            Rectangle { visible: providerEntry.modelData.configured; width: 6; height: 6; radius: 3; color: pane.theme.textMuted; Accessible.name: "已配置" }
                        }
                        onClicked: pane.selectConnection(providerEntry.modelData.id)
                        Keys.onUpPressed: pane.moveConnection(providerEntry.index, -1)
                        Keys.onDownPressed: pane.moveConnection(providerEntry.index, 1)
                    }
                }
            }
                }
            }
        }
        Rectangle { Layout.fillHeight: true; width: 1; color: pane.theme.borderSubtle }
        ColumnLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.alignment: Qt.AlignTop
            spacing: 12
            RowLayout {
                Layout.fillWidth: true; spacing: 12; Layout.bottomMargin: 8
                AIBrandIcon { theme: pane.theme; providerId: pane.setup.brandId || pane.setup.providerType || pane.selectedId; Layout.preferredWidth: 34; Layout.preferredHeight: 34 }
                Caption { text: pane.creating ? "自定义服务" : pane.setup.displayName || "选择服务"; font.pixelSize: 18; font.weight: Font.Medium }
                Label { text: pane.creating ? "尚未保存" : pane.configured ? "已配置" : ""; color: pane.theme.textMuted; font.pixelSize: 12 }
            }
            ColumnLayout {
                visible: pane.isCodex; Layout.fillWidth: true; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Rectangle { width: 7; height: 7; radius: 4; color: pane.configured ? pane.theme.success : pane.theme.textMuted }
                    Caption { objectName: "studioAICodexStatus"; text: pane.ready && pane.assistant.codexDetecting ? "正在检测登录状态…" : pane.codex.message || "尚未检测"; color: pane.theme.textSecondary }
                    AppButton { objectName: "studioAIDetectCodex"; theme: pane.theme; text: "重新检测"; variant: "ghost"; labelWeight: Font.Normal; enabled: pane.ready && !pane.assistant.codexDetecting && !pane.checking && !pane.generating; onClicked: pane.assistant.detectCodexAsync() }
                }
                Caption { text: "使用本机 Codex 的 ChatGPT 登录，无需填写密钥。创作请求使用订阅额度；检测和同步模型不会发起创作。"; color: pane.theme.textSecondary; font.pixelSize: 12 }
                AppButton { theme: pane.theme; text: "安装与登录说明 ↗"; variant: "ghost"; labelWeight: Font.Normal; visible: !pane.configured; onClicked: Qt.openUrlExternally(pane.setup.documentationUrl) }
            }
            Caption { visible: pane.creating || pane.setup.supportsDisplayName === true || pane.setup.canDelete === true; text: "连接名称" }
            Field { id: nameField; objectName: "studioAIConnectionName"; visible: pane.creating || pane.setup.supportsDisplayName === true || pane.setup.canDelete === true; placeholderText: "例如：我的模型服务"; Accessible.name: "连接名称" }
            Caption { text: "API 密钥"; visible: pane.creating || pane.setup.requiresApiKey === true }
            Field {
                id: keyField; objectName: "studioAIApiKey"
                visible: pane.creating || pane.setup.requiresApiKey === true
                echoMode: TextInput.Password
                placeholderText: pane.setup.credentialStatus === "saved" ? "已保存，留空保留原密钥" : "粘贴 API 密钥"
                Accessible.name: "API 密钥"
            }
            AppButton { objectName: "studioAIKeyHelp"; theme: pane.theme; text: "获取 API 密钥 ↗"; variant: "ghost"; labelWeight: Font.Normal; visible: !pane.isCodex && pane.setup.requiresApiKey === true && !!pane.setup.documentationUrl; onClicked: Qt.openUrlExternally(pane.setup.documentationUrl) }
            Caption { visible: !!pane.setup.connectionNotice; text: pane.setup.connectionNotice || ""; color: pane.theme.textSecondary; font.pixelSize: 12 }
            Caption { text: "服务地址"; visible: !pane.isCodex; Layout.topMargin: 4 }
            Field { id: addressField; objectName: "studioAIBaseUrl"; visible: !pane.isCodex; placeholderText: "填写 API 服务地址"; Accessible.name: "API 服务地址" }
            Caption { visible: pane.setup.requiresApiKey === false && !pane.isCodex; text: "本机服务无需 API 密钥，先确认服务已启动。"; color: pane.theme.textSecondary; font.pixelSize: 12 }
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 8
                Caption { text: "模型"; font.pixelSize: 14 }
                AppButton { objectName: "studioAIRefreshModels"; theme: pane.theme; text: pane.ready && pane.assistant.catalogRefreshing ? "正在同步…" : "同步模型列表"; labelWeight: Font.Normal; enabled: pane.canSync; onClicked: pane.syncModels() }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                AIBrandIcon { theme: pane.theme; providerId: pane.setup.brandId || pane.setup.providerType || pane.selectedId; modelId: modelField.text; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                Field { id: modelField; objectName: "studioAIModelName"; placeholderText: "填写模型名，或同步模型列表"; Accessible.name: "默认模型名称" }
            }
            Caption { objectName: "studioAIModelListHint"; visible: pane.models.length === 0; text: !pane.isCodex && !pane.configured && keyField.text.length === 0 && (pane.creating || pane.setup.requiresApiKey === true) ? "先填写 API 密钥，再同步模型；也可手动填写模型名。" : "还没有同步模型列表，可手动填写模型名。"; color: pane.theme.textSecondary; font.pixelSize: 12 }
            Caption { visible: pane.models.length > 0; text: "已同步模型 · " + pane.models.length; color: pane.theme.textMuted; font.pixelSize: 12 }
            Caption { objectName: "studioAICatalogError"; visible: pane.ready && pane.assistant.modelCatalogError.length > 0; text: pane.ready ? pane.assistant.modelCatalogError : ""; color: pane.theme.warning; font.pixelSize: 12 }
            Field { id: modelSearch; objectName: "studioAIModelSearch"; visible: pane.models.length > 8; placeholderText: "搜索模型"; Accessible.name: "搜索模型" }
            ScrollView {
                id: modelScroll
                objectName: "studioAIModelScroll"
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(168, modelRows.implicitHeight)
                Layout.minimumHeight: 0; Layout.maximumHeight: 168
                visible: pane.models.length > 0; clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    id: modelRows; width: modelScroll.availableWidth; spacing: 2
                    Repeater {
                        model: pane.models
                        delegate: AppButton {
                            id: modelOption
                            required property var modelData
                            objectName: "studioAIModel-" + modelData.id
                            Layout.fillWidth: true; implicitHeight: 36
                            leftPadding: 10; rightPadding: 10
                            theme: pane.theme; text: modelData.label || modelData.id
                            visible: text.toLowerCase().indexOf(modelSearch.text.toLowerCase()) >= 0
                            variant: modelData.id === modelField.text ? "secondary" : "ghost"
                            enabled: !pane.generating && !pane.checking
                            contentItem: RowLayout {
                                spacing: 8
                                AIBrandIcon { theme: pane.theme; providerId: pane.setup.brandId || pane.setup.providerType || pane.selectedId; modelId: modelOption.modelData.id; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                                Caption { text: modelOption.text; elide: Text.ElideRight; wrapMode: Text.NoWrap }
                                Label { text: modelOption.modelData.id === modelField.text ? "✓" : ""; color: pane.theme.textPrimary }
                            }
                            onClicked: modelField.text = modelData.id
                        }
                    }
                }
            }
            Caption { visible: pane.dirty; text: "有未保存的修改。同步时会先保存连接。"; color: pane.theme.textSecondary; font.pixelSize: 12 }
            Rectangle { Layout.fillWidth: true; height: 1; color: pane.theme.borderSubtle; Layout.topMargin: 4; Layout.bottomMargin: 4 }
            RowLayout {
                Layout.fillWidth: true
                AppButton { objectName: "studioAIDeleteConnection"; theme: pane.theme; text: "删除服务"; variant: "ghost"; labelWeight: Font.Normal; visible: pane.setup.canDelete === true; enabled: !pane.generating && !pane.checking; onClicked: deleteDialog.open() }
                Item { Layout.fillWidth: true }
                AppButton { objectName: "studioAITestConnection"; theme: pane.theme; text: pane.ready && pane.assistant.connectionTesting ? "正在测试…" : "测试连接"; labelWeight: Font.Normal; enabled: pane.ready && pane.configured && !pane.dirty && !pane.generating && !pane.checking; onClicked: pane.assistant.testConnectionAsync(pane.selectedId) }
                AppButton { objectName: "studioAISaveConnection"; theme: pane.theme; text: pane.creating ? "添加服务" : "保存连接"; variant: "primary"; labelWeight: Font.Normal; enabled: pane.ready && !pane.generating && !pane.checking; onClicked: { pane.syncAfterSave = false; pane.saveCurrent(false, false) } }
            }
            Caption {
                objectName: "studioAIConnectionResult"
                text: pane.creating ? "" : pane.ready && pane.assistant.connectionTesting ? "正在测试当前连接…" : pane.ready ? pane.assistant.connectionResult.message : ""
                color: pane.saveFailed ? pane.theme.warning : pane.ready && pane.assistant.connectionResult.code === "SUCCESS" ? pane.theme.success : pane.ready && (pane.assistant.connectionTested || pane.assistant.connectionResult.code !== "UNKNOWN_ERROR") ? pane.theme.warning : pane.theme.textSecondary
            }
            RowLayout {
                visible: !pane.ready || !pane.assistant.creatorEnabled
                Layout.fillWidth: true
                Caption { text: "历史上下文上限" }
                Item { Layout.fillWidth: true }
                SpinBox {
                    objectName: "studioAIHistoryContextKB"
                    from: 4
                    to: 512
                    stepSize: 4
                    editable: true
                    value: pane.ready ? pane.assistant.historyContextKB : 48
                    enabled: pane.ready && !pane.assistant.isBusy
                    Accessible.name: "历史上下文上限（KB）"
                    onValueModified: pane.assistant.setHistoryContextKB(value)
                }
                Caption { text: "KB" }
            }
            Caption {
                visible: !pane.ready || !pane.assistant.creatorEnabled
                text: "默认 48 KB，适用于所有连接，下次请求生效。调高可带入更多旧消息，实际容量仍取决于模型。"
                color: pane.theme.textSecondary
                font.pixelSize: 12
            }
            RowLayout {
                visible: pane.isCodex && (!pane.ready || !pane.assistant.creatorEnabled)
                Layout.fillWidth: true
                Caption { text: "单次请求最长等待" }
                StyledComboBox {
                    objectName: "studioCodexWaitPolicy"
                    theme: pane.theme
                    Layout.fillWidth: true
                    model: [{label: "5 分钟", seconds: 300}, {label: "10 分钟（推荐）", seconds: 600},
                            {label: "15 分钟", seconds: 900}, {label: "30 分钟", seconds: 1800}]
                    textRole: "label"
                    currentIndex: {
                        for (var i = 0; i < model.length; ++i)
                            if (pane.ready && model[i].seconds === pane.assistant.codexWaitSeconds) return i
                        return 1
                    }
                    enabled: pane.ready && !pane.generating
                    onActivated: pane.assistant.setCodexWaitSeconds(model[currentIndex].seconds)
                }
            }
            AppButton {
                objectName: "studioCreatorFramework"
                visible: pane.ready && pane.assistant.creatorEnabled
                theme: pane.theme; text: pane.frameworkExpanded ? "收起助手框架" : "助手框架与工作目录"
                variant: "ghost"
                onClicked: pane.frameworkExpanded = !pane.frameworkExpanded
            }
            ColumnLayout {
                visible: pane.ready && pane.assistant.creatorEnabled && pane.frameworkExpanded
                Layout.fillWidth: true; spacing: 8
                Caption { text: "默认框架（可修改）" }
                CheckBox {
                    text: "结束前复核可恢复的失败，由模型自行决定如何继续"
                    checked: pane.ready && pane.assistant.creatorRecoveryReview
                    onToggled: if (pane.ready) pane.assistant.setCreatorRecoveryReview(checked)
                }
                TextArea {
                    id: creatorInstructions
                    objectName: "studioCreatorInstructions"
                    Layout.fillWidth: true; Layout.preferredHeight: 150
                    text: pane.ready ? pane.assistant.creatorInstructions : ""
                    wrapMode: TextEdit.Wrap; selectByMouse: true
                    color: pane.theme.textPrimary; font.pixelSize: 13
                    background: Rectangle { radius: 6; color: pane.theme.inputBackground; border.color: pane.theme.borderStrong }
                    Accessible.name: "助手的默认身份与交互框架"
                }
                AppButton {
                    objectName: "studioCreatorSaveInstructions"
                    theme: pane.theme; text: "保存框架"
                    onClicked: pane.assistant.setCreatorInstructions(creatorInstructions.text)
                }
                Caption { text: "命令默认工作目录" }
                Field {
                    objectName: "studioCreatorWorkspace"
                    text: pane.ready ? pane.assistant.creatorWorkspacePath : ""
                    Accessible.name: "助手工作目录"
                    onEditingFinished: if (pane.ready && text.trim().length) pane.assistant.setCreatorWorkspacePath(text)
                }
                Caption {
                    text: "模型可读写代码、执行命令和创建工具。这里的目录只是默认位置；任务步骤由模型提供，可按你的要求调整。"
                    color: pane.theme.textSecondary; font.pixelSize: 12
                }
                Caption { visible: !pane.isCodex; text: "服务请求参数（JSON，可覆盖输出长度等默认值）" }
                TextArea {
                    id: creatorRequestOptions
                    objectName: "studioCreatorRequestOptions"
                    visible: !pane.isCodex
                    Layout.fillWidth: true; Layout.preferredHeight: 70
                    text: pane.ready ? pane.assistant.creatorRequestOptions : "{}"
                    wrapMode: TextEdit.Wrap; selectByMouse: true
                    color: pane.theme.textPrimary
                    background: Rectangle { radius: 6; color: pane.theme.inputBackground; border.color: pane.theme.borderStrong }
                    Accessible.name: "模型服务请求参数"
                }
                AppButton {
                    visible: !pane.isCodex; theme: pane.theme; text: "保存请求参数"
                    onClicked: pane.assistant.setCreatorRequestOptions(creatorRequestOptions.text)
                }
            }
            AppButton { objectName: "studioAIAdvanced"; visible: !pane.isCodex; theme: pane.theme; text: "高级选项"; variant: "ghost"; labelWeight: Font.Normal; checkable: true; checked: pane.advanced; checkedExternallyControlled: true; onClicked: pane.advanced = !pane.advanced }
            ColumnLayout {
                visible: pane.advanced && !pane.isCodex; Layout.fillWidth: true; spacing: 10
                Caption { text: "思考强度" }
                StyledComboBox {
                    objectName: "studioAIReasoning"; theme: pane.theme; Layout.fillWidth: true
                    model: pane.ready ? pane.assistant.reasoningCatalog : []; textRole: "label"
                    currentIndex: { for (var i = 0; i < model.length; ++i) if (model[i].id === pane.assistant.currentReasoningId) return i; return 0 }
                    enabled: pane.ready && !pane.generating && !pane.creating && !pane.checking
                    onActivated: pane.assistant.selectReasoning(model[currentIndex].id)
                }
                AppButton { objectName: "studioAIVerifyModel"; theme: pane.theme; text: pane.ready && pane.assistant.currentModelStatus.state === "verifying" ? "正在检查…" : "检查音乐创作能力"; labelWeight: Font.Normal; visible: !pane.isCodex && pane.ready && !pane.assistant.creatorEnabled; enabled: pane.ready && pane.configured && !pane.dirty && !pane.generating && !pane.checking && pane.assistant.currentModelStatus.canVerify; onClicked: pane.assistant.verifyMusicToolsAsync() }
                Caption { visible: pane.configured; text: pane.ready ? pane.assistant.currentModelStatus.detail : ""; color: pane.theme.textSecondary; font.pixelSize: 12 }
            }
        }
    }
    StudioOptionPopup {
        id: sortPopup; objectName: "studioAISortPopup"
        parent: sortButton; theme: pane.theme
        x: Math.min(0, sortButton.width - width); y: sortButton.height + 4
        StudioOptionItem { objectName: "studioAISortCapability"; theme: pane.theme; text: "综合能力优先"; checked: pane.ready && pane.assistant.catalogSortMode === "capability"; onClicked: { pane.assistant.setCatalogSortMode("capability"); sortPopup.close() } }
        StudioOptionItem { objectName: "studioAISortName"; theme: pane.theme; text: "按名称排序"; checked: pane.ready && pane.assistant.catalogSortMode === "name"; onClicked: { pane.assistant.setCatalogSortMode("name"); sortPopup.close() } }
    }
    Dialog {
        id: unsavedDialog; objectName: "studioAIUnsavedDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(500, parent ? parent.width - 32 : 500)
        modal: true; focus: true; title: "连接信息还没保存"
        closePolicy: Popup.CloseOnEscape
        onClosed: pane.leaveAction = null
        header: DialogHeader { theme: pane.theme; title: unsavedDialog.title }
        background: Rectangle { color: pane.theme.popupBackground; radius: 12; border.color: pane.theme.borderStrong }
        contentItem: Label { text: "离开前要保存这次修改吗？"; color: pane.theme.textPrimary; wrapMode: Text.WordWrap }
        footer: DialogFooter {
            theme: pane.theme
            AppButton { objectName: "studioAIKeepEditing"; theme: pane.theme; text: "继续编辑"; onClicked: { pane.leaveAction = null; unsavedDialog.close() } }
            AppButton { objectName: "studioAIDiscardChanges"; theme: pane.theme; text: "放弃修改"; onClicked: { var action = pane.leaveAction; pane.leaveAction = null; pane.loadCurrent(pane.ready ? pane.assistant.currentProviderId : ""); unsavedDialog.close(); if (action) action() } }
            AppButton { objectName: "studioAISaveAndContinue"; theme: pane.theme; text: "保存并继续"; variant: "primary"; onClicked: { pane.afterSaveAction = pane.leaveAction; pane.leaveAction = null; unsavedDialog.close(); pane.saveCurrent(false, false) } }
        }
    }
    Dialog {
        id: thirdPartyDialog; objectName: "studioAIThirdPartyDialog"
        property bool savingAccepted: false
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(500, parent ? parent.width - 32 : 500)
        modal: true; focus: true; title: "第三方服务提示"
        closePolicy: Popup.CloseOnEscape
        onAboutToShow: savingAccepted = false
        onClosed: if (!savingAccepted) { pane.afterSaveAction = null; pane.syncAfterSave = false }
        header: DialogHeader { theme: pane.theme; title: thirdPartyDialog.title }
        background: Rectangle { color: pane.theme.popupBackground; radius: 12; border.color: pane.theme.borderStrong }
        contentItem: Label { text: "密钥和发送给 AI 的音乐内容会经过这个服务地址。请确认它是你信任的服务。"; color: pane.theme.textPrimary; wrapMode: Text.WordWrap }
        footer: DialogFooter {
            theme: pane.theme
            AppButton { theme: pane.theme; text: "返回编辑"; onClicked: { pane.afterSaveAction = null; thirdPartyDialog.close() } }
            AppButton { objectName: "studioAIThirdPartyAccept"; theme: pane.theme; text: "确认并保存"; variant: "primary"; onClicked: { thirdPartyDialog.savingAccepted = true; thirdPartyDialog.close(); pane.saveCurrent(true, pane.testAfterSave) } }
        }
    }
    Dialog {
        id: deleteDialog; objectName: "studioAIDeleteDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(460, parent ? parent.width - 32 : 460)
        modal: true; focus: true; title: "删除这个连接？"
        header: DialogHeader { theme: pane.theme; title: deleteDialog.title }
        background: Rectangle { color: pane.theme.popupBackground; radius: 12; border.color: pane.theme.borderStrong }
        contentItem: Label { text: "只删除当前自定义连接，不影响作品和其他连接。"; color: pane.theme.textPrimary; wrapMode: Text.WordWrap }
        footer: DialogFooter {
            theme: pane.theme
            AppButton { theme: pane.theme; text: "取消"; onClicked: deleteDialog.close() }
            AppButton { objectName: "studioAIConfirmDelete"; theme: pane.theme; text: "删除连接"; onClicked: { if (pane.assistant.deleteCurrentCustomConnection()) pane.loadCurrent(pane.assistant.currentProviderId); deleteDialog.close() } }
        }
    }
}
