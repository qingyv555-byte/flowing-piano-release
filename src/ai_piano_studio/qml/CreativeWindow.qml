import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    objectName: "creativeWindow"
    required property var creative
    readonly property var agent: creative ? creative.assistant : null
    width: 760; height: 790
    minimumWidth: 640; minimumHeight: 600
    title: "灵感与陪伴 · Flowing Piano"
    visible: false
    color: theme.appBackground
    palette.windowText: theme.textPrimary
    palette.text: theme.textPrimary
    palette.buttonText: theme.textPrimary
    palette.base: theme.inputBackground
    palette.button: theme.surface
    palette.highlight: theme.accentPrimary
    Theme { id: theme; studio: true }
    function preferenceLabel(code) {
        return ({density: "疏密", register: "音区", dynamics: "力度", mood: "氛围", tempo: "速度", ending: "结尾",
            sparse: "稀疏", balanced: "均衡", rich: "丰富", low: "低音", middle: "中音", high: "高音",
            gentle: "轻柔", strong: "有力", calm: "平静", bright: "明亮", wistful: "惆怅", playful: "俏皮",
            dramatic: "戏剧感", slow: "慢", moderate: "适中", fast: "快", rest: "留白", resolve: "收束", open: "开放"})[code] || code
    }
    function preferenceOptions(codes) { return codes.map(function(code) { return {label: preferenceLabel(code), value: code} }) }
    Connections {
        target: root.creative
        function onOpenRequested() { root.show(); root.raise(); root.requestActivate() }
        function onClearInputRequested() { prompt.clear() }
        function onPopulateInputRequested(text) { prompt.text = text }
    }
    onClosing: { creative.cancel(); creative.pauseCompanion(); prompt.clear() }
    ScrollView {
        objectName: "creativeScroll"
        anchors.fill: parent
        anchors.margins: 24
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 14
            Label { text: "灵感与陪伴"; font.pixelSize: 26; color: theme.textPrimary }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textSecondary
                text: "在独立草稿中创作。生成不会自动出声；接受后可保存主题或开启本地陪伴。" }
            RowLayout {
                ComboBox { id: hotkey; model: ["F8", "F9", "F10"] }
                CheckBox { text: "允许热键读取记事本 / 所选图片"; checked: creative.hotkeyEnabled
                    onClicked: creative.enableHotkey(checked, 0x77 + hotkey.currentIndex) }
                Button { text: "撤销来源直发许可"; onClicked: creative.revokeSources() }
            }
            RowLayout {
                ComboBox {
                    id: provider; Layout.fillWidth: true
                    model: root.agent ? root.agent.providerCatalog : []
                    textRole: "displayName"; valueRole: "id"
                    onActivated: root.agent.selectProvider(currentValue)
                }
                ComboBox {
                    id: model; Layout.fillWidth: true
                    model: root.agent ? root.agent.modelCatalog : []
                    textRole: "label"; valueRole: "id"
                    onActivated: root.agent.selectModel(currentValue)
                }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textMuted
                text: root.agent ? root.agent.currentModelStatus.detail : "打开后选择连接；密钥配置沿用 AI 助手。" }
            TextArea { id: prompt; objectName: "creativePrompt"; Layout.fillWidth: true; Layout.preferredHeight: 120
                placeholderText: "粘贴一句话，或描述这次想表达的场景…（最多 2000 字）"
                wrapMode: TextEdit.Wrap; selectByMouse: true }
            CheckBox { id: useGlobal; text: "本次允许使用我的通用偏好"; checked: false }
            RowLayout {
                Button { text: creative.imageLoading ? "处理图片中…" : "打开一张图片…"; enabled: !creative.imageLoading; onClicked: imageFile.open() }
                Button { text: "使用剪贴板图片"; enabled: !creative.imageLoading; onClicked: creative.pasteImage() }
                Button { text: "移除图片"; onClicked: creative.clearImage() }
                Image { source: creative.imagePreview; cache: false; fillMode: Image.PreserveAspectFit; Layout.preferredWidth: 110; Layout.preferredHeight: 75 }
            }
            CheckBox { text: "我已核实当前端点和模型支持图片输入（非在线验收）"; checked: false
                onClicked: creative.declareImageSupport(checked) }
            RowLayout {
                Button { objectName: "creativeGenerate"; text: "发送并生成主题"; enabled: root.agent && !root.agent.isBusy
                    onClicked: creative.generate(prompt.text, useGlobal.checked) }
                Button { text: "取消"; onClicked: { creative.cancel(); prompt.clear() } }
                Button { text: "打开已有主题…"; onClicked: openTheme.open() }
            }
            Button { text: "将输入作为故事在本地分段（最多 24000 字）"; onClicked: creative.prepareStory(prompt.text) }
            Repeater {
                model: creative.storySegments
                delegate: Button {
                    required property var modelData
                    Layout.fillWidth: true
                    text: (modelData.confirmed ? "已接受 · " : "选择本段 · ") + (modelData.index + 1) + "  " + modelData.preview
                    onClicked: creative.selectStorySegment(modelData.index)
                }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textPrimary
                text: root.agent && root.agent.isBusy ? root.agent.assistantProgressText : creative.message }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textSecondary
                text: root.agent && root.agent.proposal.available
                    ? "提案：新增 " + root.agent.proposal.notesAdded + " · 修改 " + root.agent.proposal.notesUpdated + " · " + root.agent.proposal.statusText
                    : "草稿：" + creative.noteCount + " 个音符" }
            RowLayout {
                Button { text: "试听方案"; onClicked: creative.audition(false) }
                Button { text: "对比原版"; enabled: root.agent && root.agent.proposal.status === "pending"; onClicked: creative.audition(true) }
                Button { text: "接受到草稿"; onClicked: creative.accept() }
                Button { text: "保存主题…"; enabled: creative.noteCount > 0; onClicked: saveTheme.open() }
                Button { text: "撤销草稿"; onClicked: creative.undoTheme() }
            }
            Button { text: "追加到当前工程末尾（独立声部，可撤销一次）"; enabled: creative.noteCount > 0
                onClicked: creative.appendToStudio() }
            Label { text: "短片时点（按你填写的秒数，不自动分析视频）"; color: theme.textSecondary }
            RowLayout {
                TextField { id: cueDuration; text: "10"; placeholderText: "总秒数"; Layout.preferredWidth: 80 }
                TextField { id: cueEntry; text: "0"; placeholderText: "进入秒数"; Layout.preferredWidth: 80 }
                TextField { id: cueRests; placeholderText: "留白：2-3,6-7"; Layout.fillWidth: true }
                TextField { id: cueAccents; placeholderText: "重音：3,8"; Layout.fillWidth: true }
            }
            Button { text: "应用时点到草稿"; enabled: creative.noteCount > 0
                onClicked: creative.applyCues(Number(cueDuration.text), Number(cueEntry.text), cueRests.text, cueAccents.text) }
            Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderSubtle }
            Label { text: "本地陪伴"; font.pixelSize: 19; color: theme.textPrimary }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textMuted
                text: "开启期间只汇总鼠标移动与滚轮强度，不读取键盘内容或保存轨迹；暂停即停止采集。活动仅用于音乐疏密变化。" }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textSecondary
                text: "状态：" + ({off: "已关闭", running: "运行中", paused: "已暂停", yielding: "为工作室或试听让路"}[creative.companionState] || creative.companionState) }
            RowLayout {
                Button { text: "开启所选主题陪伴"; enabled: creative.noteCount > 0; onClicked: creative.startCompanion() }
                Button { text: "暂停陪伴"; onClicked: creative.pauseCompanion() }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderSubtle }
            Label { text: "已确认音乐偏好"; font.pixelSize: 19; color: theme.textPrimary }
            RowLayout {
                TextField { id: scopeField; Layout.fillWidth: true; text: creative.scope; placeholderText: "global 或作品作用域 ID" }
                Button { text: "选择作用域"; onClicked: creative.setScope(scopeField.text) }
                Button { text: "新作品作用域"; onClicked: scopeField.text = creative.newScope() }
            }
            RowLayout {
                ComboBox { id: kind; textRole: "label"; valueRole: "value"
                    model: root.preferenceOptions(["density", "register", "dynamics", "mood", "tempo", "ending"]) }
                ComboBox { id: value; Layout.fillWidth: true
                    textRole: "label"; valueRole: "value"
                    model: root.preferenceOptions(({density: ["sparse", "balanced", "rich"], register: ["low", "middle", "high"], dynamics: ["gentle", "balanced", "strong"], mood: ["calm", "bright", "wistful", "playful", "dramatic"], tempo: ["slow", "moderate", "fast"], ending: ["rest", "resolve", "open"]})[kind.currentValue] || []) }
                Button { text: "确认保存此偏好"; onClicked: creative.remember(kind.currentValue, value.currentValue, true) }
            }
            Repeater {
                model: creative.memories
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: root.preferenceLabel(modelData.kind) + " · " + root.preferenceLabel(modelData.value); color: theme.textPrimary }
                    Button { text: "删除"; onClicked: creative.deleteMemory(modelData.id) }
                }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textMuted
                text: "偏好保存在本机，未加密。这里只保存你确认的音乐选项；导出不会携带输入原文、图片、密钥或设备许可。" }
            CheckBox { id: exportConsent; text: "我允许导出当前作用域的音乐偏好" }
            RowLayout {
                Button { text: "导出偏好…"; enabled: exportConsent.checked; onClicked: exportMemory.open() }
                Button { text: "导入偏好…"; onClicked: importMemory.open() }
                Button { text: "导出主题迁移包…"; enabled: exportConsent.checked && creative.noteCount > 0; onClicked: exportBundle.open() }
                Button { text: "导入迁移包…"; onClicked: importBundle.open() }
            }
            Repeater {
                model: creative.memoryConflicts
                delegate: ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textSecondary
                        text: "冲突 · " + modelData.existing.scope + " / " + modelData.existing.kind + "\n本地：" + (modelData.existing.status === "deleted" ? "已删除" : modelData.existing.value) + " → 导入：" + (modelData.incoming.value || "删除墓碑") }
                    CheckBox { id: restore; visible: modelData.existing.status === "deleted"; text: "明确恢复这条已删除偏好" }
                    RowLayout {
                        Button { text: "保留本地"; onClicked: creative.resolveMemory(index, false, false) }
                        Button { text: "采用导入值"; enabled: modelData.existing.status !== "deleted" || restore.checked
                            onClicked: creative.resolveMemory(index, true, restore.checked) }
                    }
                }
            }
            ColumnLayout {
                visible: creative.importPreview.memories !== undefined
                Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textSecondary
                    text: "待导入：主题 " + (creative.importPreview.themes || 0) + " 个，音符 " + (creative.importPreview.notes || 0) + " 个，偏好 " + (creative.importPreview.memories || 0) + " 条。作用域：" + (creative.importPreview.scopes || "无偏好") }
                RowLayout {
                    Button { text: "确认导入，冲突先保留本地"; onClicked: creative.confirmImport(true) }
                    Button { text: "取消导入"; onClicked: creative.confirmImport(false) }
                }
            }
            ColumnLayout {
                visible: creative.themeConflict.id !== undefined
                Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: theme.textSecondary
                    text: "同一主题已有不同版本：" + (creative.themeConflict.id || "") + "。本地文件保持不变。" }
                RowLayout {
                    Button { text: "保留本地"; onClicked: creative.resolveTheme(false) }
                    Button { text: "导入为独立新主题"; onClicked: creative.resolveTheme(true) }
                }
            }
            Repeater {
                model: creative.savedThemes
                delegate: Button {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "打开迁入主题 · " + modelData.scope + " · 版本 " + modelData.revision
                    onClicked: creative.loadLibraryTheme(modelData.id)
                }
            }
        }
    }
    FileDialog { id: openTheme; nameFilters: ["钢琴工程 (*.json)"]; onAccepted: creative.loadTheme(selectedFile.toString()) }
    FileDialog { id: imageFile; nameFilters: ["静态图片 (*.png *.jpg *.jpeg *.webp *.bmp)"]; onAccepted: creative.loadImage(selectedFile.toString()) }
    FileDialog { id: saveTheme; fileMode: FileDialog.SaveFile; defaultSuffix: "pianoai.json"; onAccepted: creative.saveTheme(selectedFile.toString()) }
    FileDialog { id: exportMemory; fileMode: FileDialog.SaveFile; defaultSuffix: "json"; onAccepted: creative.exportMemory(selectedFile.toString(), exportConsent.checked) }
    FileDialog { id: importMemory; nameFilters: ["音乐偏好 (*.json)"]; onAccepted: creative.previewImport(selectedFile.toString(), false) }
    FileDialog { id: exportBundle; fileMode: FileDialog.SaveFile; defaultSuffix: "zip"; onAccepted: creative.exportBundle(selectedFile.toString(), exportConsent.checked) }
    FileDialog { id: importBundle; nameFilters: ["主题迁移包 (*.zip)"]; onAccepted: creative.previewImport(selectedFile.toString(), true) }
}
