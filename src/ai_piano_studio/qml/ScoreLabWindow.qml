import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    objectName: "scoreLabWindow"
    required property var lab
    width: 1000; height: 760
    minimumWidth: 760; minimumHeight: 580
    visible: false
    title: "找歌实验室 · Flowing Piano"
    color: theme.appBackground
    palette.windowText: theme.textPrimary
    palette.text: theme.textPrimary
    palette.buttonText: theme.textPrimary
    palette.base: theme.inputBackground
    palette.button: theme.surface
    palette.highlight: theme.accentPrimary
    Theme { id: theme; studio: true }
    readonly property var recoveryLab: lab
    readonly property var source: lab.candidate
    readonly property var draft: lab.draft
    property bool sourceDetailsOpen: false
    onSourceChanged: sourceDetailsOpen = false
    function bounded(value, limit) { return String(value || "").slice(0, limit || 2400) }
    function readyWarning(value) {
        var sourceUncertainty = (root.draft.candidate || {}).uncertainty
        var historical = typeof value === "string" && value.length > 0
            && value === sourceUncertainty
        return (historical ? "查找阶段的历史说明（草稿现已转谱，可独立试听）：\n" : "")
            + bounded(value, 800)
    }
    component Copy: Label {
        Layout.fillWidth: true
        wrapMode: Text.WrapAnywhere
        textFormat: Text.PlainText
        color: theme.textSecondary
    }
    component ScorePreview: Frame {
        id: previewBox
        required property var inspection
        objectName: "scoreLabPreviewPanel"
        Layout.fillWidth: true
        visible: !!(inspection && inspection.previewUrls && inspection.previewUrls.length > 0)
        background: Rectangle { color: theme.inputBackground; radius: 10; border.color: theme.borderSubtle }
        ColumnLayout {
            anchors.fill: parent
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label { text: "真实谱面预览"; color: theme.textPrimary; font.bold: true }
                Label {
                    text: "仅验证可读性，音符准确度仍待核对"
                    color: theme.textSecondary; font.pixelSize: 12
                }
                Item { Layout.fillWidth: true }
                Label { text: "放大"; color: theme.textSecondary }
                Slider {
                    id: previewZoom
                    objectName: "scoreLabPreviewZoom"
                    Layout.preferredWidth: 120
                    from: 1; to: 3; stepSize: 0.25; value: 1
                }
                Label { text: Math.round(previewZoom.value * 100) + "%"; color: theme.textSecondary }
            }
            Label {
                objectName: "scoreLabPreviewQuality"
                Layout.fillWidth: true; wrapMode: Text.Wrap
                textFormat: Text.PlainText
                color: theme.textSecondary
                text: {
                    var item = previewBox.inspection || ({})
                    var first = (item.dimensions || [])[0] || []
                    var size = first.length === 2 ? first[0] + "×" + first[1] + " 像素" : "尺寸未知"
                    return "谱图尺寸：" + size + " · 可读性：" +
                        (item.status === "readable" ? "已初步判定可读" : "未通过检查") +
                        "。检查说明：" + root.bounded(item.readabilityReason, 240) +
                        "。识谱准确度不由清晰度检查保证。"
                }
            }
            Flickable {
                id: firstPage
                Layout.fillWidth: true
                Layout.preferredHeight: 220
                visible: !!(previewBox.inspection && previewBox.inspection.previewUrls
                    && previewBox.inspection.previewUrls.length > 0)
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentWidth: scoreImage.width
                contentHeight: scoreImage.height
                Image {
                    id: scoreImage
                    objectName: "scoreLabPreviewImage"
                    source: firstPage.visible ? previewBox.inspection.previewUrls[0] : ""
                    asynchronous: false
                    cache: false
                    fillMode: Image.PreserveAspectFit
                    width: firstPage.width * previewZoom.value
                    height: implicitWidth > 0 ? width * implicitHeight / implicitWidth : firstPage.height
                }
                ScrollBar.vertical: ScrollBar { }
                ScrollBar.horizontal: ScrollBar { }
            }
            Flickable {
                id: secondPage
                Layout.fillWidth: true
                Layout.preferredHeight: 220
                visible: !!(previewBox.inspection && previewBox.inspection.previewUrls
                    && previewBox.inspection.previewUrls.length > 1)
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentWidth: secondImage.width
                contentHeight: secondImage.height
                Image {
                    id: secondImage
                    objectName: "scoreLabPreviewImageSecondary"
                    source: secondPage.visible ? previewBox.inspection.previewUrls[1] : ""
                    asynchronous: false
                    cache: false
                    fillMode: Image.PreserveAspectFit
                    width: secondPage.width * previewZoom.value
                    height: implicitWidth > 0 ? width * implicitHeight / implicitWidth : secondPage.height
                }
                ScrollBar.vertical: ScrollBar { }
                ScrollBar.horizontal: ScrollBar { }
            }
        }
    }
    Connections {
        target: root.lab
        function onOpenRequested() { root.show(); root.raise(); root.requestActivate(); query.forceActiveFocus() }
    }
    onVisibleChanged: { if (typeof keyboard !== "undefined" && keyboard !== null) keyboard.suspendInput("score-lab-window", visible) }
    Component.onDestruction: { if (typeof keyboard !== "undefined" && keyboard !== null) keyboard.suspendInput("score-lab-window", false) }
    onClosing: { saveFile.close(); lab.cancel() }
    ScrollView {
        id: scroll
        anchors.fill: parent; anchors.margins: 28
        contentWidth: availableWidth
        ColumnLayout {
            width: scroll.availableWidth; spacing: 16
            RowLayout {
                Layout.fillWidth: true
                Label { text: "找歌实验室"; color: theme.textPrimary; font.pixelSize: 27; font.bold: true }
                Label { text: "实验"; color: theme.accentPrimary; font.pixelSize: 13 }
                Item { Layout.fillWidth: true }
                Copy { Layout.fillWidth: false; text: "Codex · " + root.bounded(lab.modelId, 100) }
            }
            Copy { text: "找歌 → 确认谱源 → 转谱 → 试听。生成独立短片段；请核对编配与段落，当前作品与撤销记录保持原样。" }
            TextField {
                id: query; objectName: "scoreLabQuery"; Layout.fillWidth: true
                placeholderText: "歌曲、歌手、版本或歌词线索（最多 2000 字）"
                maximumLength: 2000; enabled: !lab.busy
                onTextEdited: { if (lab.state === "awaiting_confirmation" || lab.state === "ready") lab.cancel() }
                onAccepted: { if (!lab.busy && text.trim().length) lab.findSong(text, seconds.value) }
            }
            RowLayout {
                Label { text: "片段时长"; color: theme.textPrimary }
                SpinBox { id: seconds; objectName: "scoreLabSeconds"; from: 5; to: 30; value: 15; enabled: !lab.busy
                    onValueModified: { if (lab.state === "awaiting_confirmation" || lab.state === "ready") lab.cancel() } }
                Label { text: "秒"; color: theme.textSecondary }
                Button { objectName: "scoreLabFind"; text: "查找谱源"; enabled: !lab.busy && query.text.trim().length > 0
                    onClicked: lab.findSong(query.text, seconds.value) }
                Item { Layout.fillWidth: true }
                Button { objectName: "scoreLabCancel"; text: lab.busy ? "取消任务" : "清除本次实验"; onClicked: lab.cancel() }
            }
            RowLayout {
                BusyIndicator { running: lab.busy; visible: running; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                Copy { objectName: "scoreLabStatus"; text: root.bounded(lab.status, 400) }
                Label { text: "已用 " + lab.elapsed + " 秒"; color: theme.textMuted; visible: lab.busy || lab.elapsed > 0 }
            }
            Copy { objectName: "scoreLabError"; visible: lab.error.length > 0; text: root.bounded(lab.error, 400); color: theme.accentPrimary }
            Frame {
                objectName: "scoreLabSourceCard"
                Layout.fillWidth: true; visible: lab.state === "awaiting_confirmation"
                background: Rectangle { color: theme.surface; radius: 12; border.color: theme.borderSubtle }
                ColumnLayout {
                    anchors.fill: parent; spacing: 10
                    Label { text: "1 · 核对谱源与段落"; color: theme.textPrimary; font.pixelSize: 19 }
                    Copy { objectName: "scoreLabIdentity"; text: root.bounded(root.source.title, 160) + " · " + (root.bounded(root.source.artist, 160) || "歌手未知") }
                    Copy { text: "版本：" + root.bounded(root.source.edition, 160) + "\n段落：" + root.bounded(root.source.section, 160)
                            + " · 起点 " + (root.source.startSeconds || 0) + " 秒 · 请求 " + (root.source.durationSeconds || 0) + " 秒" }
                    Copy { objectName: "scoreLabArtifact"; text: "实际乐谱资源：\n" + root.bounded(root.source.selectedUrl, 2048) }
                    ScorePreview { inspection: root.source.inspection || ({}) }
                    Copy { objectName: "scoreLabStageExplanation"; color: theme.textPrimary
                        text: "目前仅完成谱源检查，尚未生成或试听音符。下方有关播放能力的说明仅指查找阶段；确认后将转谱，再独立试听草稿。" }
                    Copy { objectName: "scoreLabUncertainty"; text: "谱源查找阶段的不确定处（原文）：" + (root.bounded(root.source.uncertainty, 800) || "尚未核对编配与录音的一致性，请自行核对。") }
                    Button { objectName: "scoreLabDetailsToggle"; text: root.sourceDetailsOpen ? "收起详细依据" : "查看详细身份依据与内容指纹"
                        checkable: true; checked: root.sourceDetailsOpen; onClicked: root.sourceDetailsOpen = checked }
                    ColumnLayout {
                        objectName: "scoreLabSourceDetails"; Layout.fillWidth: true; visible: root.sourceDetailsOpen
                        Copy { objectName: "scoreLabFingerprint"; text: "内容指纹：" + root.bounded(root.source.sourceSha256, 64) }
                        Copy { text: "身份判断：" + root.bounded(root.source.summary, 800) }
                        Repeater {
                            model: root.source.evidence || []
                            delegate: Copy { required property var modelData
                                text: "依据：" + root.bounded(modelData.quote, 500) + "\n" + root.bounded(modelData.url, 2048) }
                        }
                    }
                    Copy { text: "确认仅授权读取以上谱源并生成独立草稿，不会应用到当前作品。" }
                    RowLayout {
                        AppButton { objectName: "scoreLabConfirm"; theme: theme; variant: "primary"; text: "确认此谱源与段落，开始转谱"; enabled: !lab.busy; onClicked: lab.confirmSource() }
                        Button { objectName: "scoreLabOpenSource"; text: "查看乐谱来源"; enabled: !lab.busy
                            onClicked: lab.openSource(root.source.selectedUrl) }
                    }
                    TextField { id: correction; objectName: "scoreLabCorrection"; Layout.fillWidth: true; maximumLength: 1000
                        placeholderText: "可补充纠正：歌手、版本或具体段落…" }
                    Button { objectName: "scoreLabReject"; text: "不是这首，重新找"; enabled: !lab.busy
                        onClicked: { lab.rejectSource(correction.text); correction.clear() } }
                }
            }
            Frame {
                objectName: "scoreLabDraftCard"; Layout.fillWidth: true; visible: lab.state === "ready"
                background: Rectangle { color: theme.surface; radius: 12; border.color: theme.borderSubtle }
                ColumnLayout {
                    anchors.fill: parent; spacing: 12
                    Label { text: "2 · 试听独立草稿"; font.pixelSize: 19; color: theme.textPrimary }
                    Copy { objectName: "scoreLabDraftFacts"; text: Number(root.draft.durationSeconds || 0).toFixed(2) + " 秒 · " + (root.draft.noteCount || 0)
                        + " 个音符 · " + (root.draft.bpm || 0) + " BPM · " + root.bounded((root.draft.hands || []).join(" / "), 500) }
                    Copy { text: "实际模型：" + root.bounded(root.draft.modelId, 100) }
                    Copy { text: "谱源：" + root.bounded((root.draft.candidate || {}).selectedUrl, 2048) }
                    Repeater { model: root.draft.warnings || []
                        delegate: Copy { required property var modelData; text: root.readyWarning(modelData) } }
                    RowLayout {
                        Button { objectName: "scoreLabAudition"; text: lab.previewStarting ? "准备试听中…" : lab.playing ? "正在试听" : "试听草稿"
                            enabled: !lab.busy && !lab.playing && !lab.previewStarting; onClicked: lab.audition() }
                        Button { objectName: "scoreLabStop"; text: "停止试听"; enabled: lab.playing || lab.previewStarting; onClicked: lab.stopAudition() }
                        Button { objectName: "scoreLabExport"; text: "保存独立工程…"; onClicked: saveFile.open() }
                    }
                    ScorePreview { inspection: (root.draft.candidate || {}).inspection || ({}) }
                }
            }
            Frame {
                objectName: "songRecoveryFeedback"
                Layout.fillWidth: true
                visible: recoveryLab !== null && (recoveryLab.busy || recoveryLab.liveCandidates.length > 0 || recoveryLab.failure.message !== undefined)
                ColumnLayout {
                    anchors.fill: parent
                    Label { text: "找歌过程与历史来源 · " + (recoveryLab ? recoveryLab.status : ""); textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Label { visible: recoveryLab !== null && recoveryLab.failure.message !== undefined; text: recoveryLab ? (recoveryLab.failure.message || "") : ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Repeater {
                        model: recoveryLab ? recoveryLab.liveCandidates : []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Label { text: [modelData.title, modelData.artist, modelData.edition].filter(Boolean).join(" · "); textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Label { text: modelData.message; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Button { text: modelData.url; Layout.fillWidth: true; onClicked: recoveryLab.openSource(modelData.url) }
                        }
                    }
                    RowLayout {
                        Button { text: "查看找歌任务 / 确认新谱源"; onClicked: recoveryLab.open() }
                        Button { text: "取消找歌"; enabled: recoveryLab !== null && recoveryLab.busy; onClicked: recoveryLab.cancel() }
                    }
                }
            }
            Copy { text: "短片段实验不承诺整首转写或录音还原。试听并核对谱面后，再决定是否保留独立工程。"; color: theme.textMuted }
        }
    }
    FileDialog {
        id: saveFile; objectName: "scoreLabSaveDialog"; title: "保存独立实验工程"; fileMode: FileDialog.SaveFile
        nameFilters: ["钢琴工程 (*.json)"]; defaultSuffix: "json"
        onAccepted: lab.exportProject(selectedFile)
    }
}
