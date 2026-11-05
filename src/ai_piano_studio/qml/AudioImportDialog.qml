import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "audioImportDialog"
    required property Theme theme
    required property var importer
    required property var editor
    required property var playback
    readonly property var session: importer.session
    property string sourceUrl: ""
    property bool notesVisible: false
    property bool advancedControlsVisible: false
    readonly property var extractionModeValues: ["auto", "melody", "bass", "simplified"]

    function applyExtractionControls() {
        root.importer.setExtractionControls(
            extractionModeBox.values[extractionModeBox.currentIndex],
            minimumDurationBox.values[minimumDurationBox.currentIndex],
            noiseThresholdBox.values[noiseThresholdBox.currentIndex],
            quantizeSlider.value,
            densitySlider.value,
            pitchMinBox.value,
            pitchMaxBox.value
        )
    }

    function resetExtractionControls() {
        root.importer.setExtractionControls("auto", 0.08, 0.0, 1.0, 0.5, 21, 108)
        extractionModeBox.currentIndex = 0
        minimumDurationBox.currentIndex = 1
        noiseThresholdBox.currentIndex = 0
        pitchMinBox.value = 21
        pitchMaxBox.value = 108
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(660, parent ? parent.width - 32 : 660)
    height: Math.min(620, parent ? parent.height - 32 : 620,
        Math.max(360, importContent.implicitHeight + implicitHeaderHeight
            + implicitFooterHeight + topPadding + bottomPadding + spacing * 2))
    modal: true
    focus: true
    padding: theme.space5
    closePolicy: Popup.CloseOnEscape

    function openForFile(url) {
        sourceUrl = url.toString()
        notesVisible = false
        advancedControlsVisible = false
        resetExtractionControls()
        root.open()
        importer.startAnalysis(
            sourceUrl,
            sensitivityBox.values[sensitivityBox.currentIndex],
            extractionModeValues[extractionModeBox.currentIndex]
        )
    }

    function pitchName(pitch) {
        var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return names[Number(pitch) % 12] + (Math.floor(Number(pitch) / 12) - 1)
    }

    function formatDuration(seconds) {
        var total = Math.max(0, Math.round(Number(seconds) || 0))
        var minutes = Math.floor(total / 60)
        var remainder = total % 60
        return minutes + ":" + (remainder < 10 ? "0" : "") + remainder
    }

    onOpened: closeButton.forceActiveFocus(Qt.PopupFocusReason)
    onClosed: {
        importer.stopPreview()
        if (session.status === "loading" || session.status === "analyzing")
            importer.cancel()
    }

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
            text: "导入音频"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSection
            font.weight: theme.weightBold
        }
        Label {
            text: "本地提取旋律候选，试听后再导入。"
            color: theme.textSecondary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSmall
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    contentItem: ScrollView {
        id: importScroll
        objectName: "audioImportScroll"
        clip: true
        rightPadding: root.theme.scrollbarGutter
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical: SafeScrollBar {
            theme: root.theme
            parent: importScroll
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            visible: size < 1
        }
        ColumnLayout {
            id: importContent
            width: importScroll.availableWidth
            spacing: theme.space3

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: fileRow.implicitHeight + theme.space4
                radius: theme.radiusM
                color: theme.surface
                border.color: theme.borderSubtle
                RowLayout {
                    id: fileRow
                    anchors.fill: parent
                    anchors.margins: theme.space2
                    spacing: theme.space2
                    Label {
                        text: "文件"
                        color: theme.textMuted
                        font.pixelSize: theme.typeSmall
                    }
                    Label {
                        text: session.sourceDisplayName || "未选择"
                        color: theme.textPrimary
                        font.pixelSize: theme.typeBody
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                    Label {
                        text: "WAV / MP3"
                        color: theme.textMuted
                        font.pixelSize: theme.typeSmall
                    }
                }
            }

            Label {
                text: "分析内容"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            Label {
                text: "● 主要旋律"
                color: theme.accentPrimary
                font.pixelSize: theme.typeBody
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: theme.space2
                Label {
                    text: "灵敏度"
                    color: theme.textSecondary
                    font.pixelSize: theme.typeSmall
                }
                StyledComboBox {
                    theme: root.theme
                    id: sensitivityBox
                    objectName: "audioSensitivityBox"
                    Layout.preferredWidth: 160
                    model: ["保守", "标准", "灵敏"]
                    property var values: ["conservative", "standard", "sensitive"]
                    enabled: session.status !== "loading" && session.status !== "analyzing"
                    Accessible.name: "音符检测灵敏度"
                }
                AppButton {
                    objectName: "audioReanalyzeButton"
                    text: "按此灵敏度重新分析"
                    theme: root.theme
                    variant: "secondary"
                    enabled: root.sourceUrl !== ""
                        && session.status !== "loading"
                        && session.status !== "analyzing"
                    onClicked: root.importer.startAnalysis(
                        root.sourceUrl,
                        sensitivityBox.values[sensitivityBox.currentIndex],
                        extractionModeBox.values[extractionModeBox.currentIndex]
                    )
                }
                Label {
                    text: "先用标准；复杂歌曲可尝试灵敏。"
                    color: theme.textMuted
                    font.pixelSize: theme.typeSmall
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
            }

            AppButton {
                objectName: "audioImportAdvancedToggle"
                theme: root.theme
                text: root.advancedControlsVisible ? "收起识别设置" : "调整识别结果"
                variant: "ghost"
                Layout.fillWidth: true
                onClicked: root.advancedControlsVisible = !root.advancedControlsVisible
            }

            Rectangle {
                objectName: "audioImportAdvancedControls"
                visible: root.advancedControlsVisible
                Layout.fillWidth: true
                implicitHeight: extractionControlsColumn.implicitHeight + theme.space4
                radius: theme.radiusM
                color: theme.surface
                border.color: theme.borderSubtle
                ColumnLayout {
                    id: extractionControlsColumn
                    anchors.fill: parent
                    anchors.margins: theme.space3
                    spacing: theme.space2

                    Label {
                        text: "提取方式与结果控制"
                        color: theme.textPrimary
                        font.pixelSize: theme.typePanelTitle
                        font.weight: theme.weightSemibold
                    }
                    Label {
                        text: "先试听效果，满意后再导入。"
                        color: theme.textMuted
                        font.pixelSize: theme.typeSmall
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space2
                        Label {
                            text: "提取模式"
                            color: theme.textSecondary
                            font.pixelSize: theme.typeSmall
                        }
                        StyledComboBox {
                            theme: root.theme
                            id: extractionModeBox
                            objectName: "audioExtractionModeBox"
                            Layout.preferredWidth: 180
                            model: ["自动", "主旋律优先", "低音优先", "简化版本"]
                            property var values: root.extractionModeValues
                            Accessible.name: "音频提取模式"
                            onActivated: root.applyExtractionControls()
                        }
                        Label {
                            text: extractionModeBox.currentIndex === 1
                                ? "每个起音点保留一条连续旋律线"
                                : extractionModeBox.currentIndex === 2
                                    ? "优先保留低音区候选"
                                    : extractionModeBox.currentIndex === 3
                                        ? "降低同时发声密度"
                                        : "保留分析结果，按控制项清理"
                            color: theme.textMuted
                            font.pixelSize: theme.typeSmall
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space2
                        Label {
                            text: "最短音符"
                            color: theme.textSecondary
                            font.pixelSize: theme.typeSmall
                        }
                        StyledComboBox {
                            theme: root.theme
                            id: minimumDurationBox
                            objectName: "audioMinimumDurationBox"
                            Layout.preferredWidth: 140
                            model: ["0.05 秒", "0.08 秒", "0.12 秒", "0.20 秒", "0.30 秒"]
                            property var values: [0.05, 0.08, 0.12, 0.20, 0.30]
                            currentIndex: 1
                            Accessible.name: "最短音符时长"
                            onActivated: root.applyExtractionControls()
                        }
                        Label {
                            text: "移除过短检测，减少噪点"
                            color: theme.textMuted
                            font.pixelSize: theme.typeSmall
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space2
                        Label {
                            text: "弱音过滤"
                            color: theme.textSecondary
                            font.pixelSize: theme.typeSmall
                        }
                        StyledComboBox {
                            theme: root.theme
                            id: noiseThresholdBox
                            objectName: "audioNoiseThresholdBox"
                            Layout.preferredWidth: 140
                            model: ["关闭", "轻微", "标准", "严格"]
                            property var values: [0.0, 0.45, 0.60, 0.75]
                            Accessible.name: "弱音过滤阈值"
                            onActivated: root.applyExtractionControls()
                        }
                        Label {
                            text: "仅过滤低置信度候选，不改变原始音频"
                            color: theme.textMuted
                            font.pixelSize: theme.typeSmall
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space2
                        Label {
                            text: "量化强度"
                            color: theme.textSecondary
                            font.pixelSize: theme.typeSmall
                        }
                        StyledSlider {
                            id: quantizeSlider
                            objectName: "audioQuantizeStrengthSlider"
                            theme: root.theme
                            Layout.fillWidth: true
                            from: 0
                            to: 1
                            stepSize: 0.05
                            modelValue: session.quantizeStrength
                            accessibleName: "音符量化强度"
                            onValueCommitted: root.applyExtractionControls()
                        }
                        Label {
                            text: Math.round(quantizeSlider.value * 100) + "%"
                            color: theme.textSecondary
                            font.family: theme.numberFont
                            Layout.preferredWidth: 42
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space2
                        Label {
                            text: "简化密度"
                            color: theme.textSecondary
                            font.pixelSize: theme.typeSmall
                        }
                        StyledSlider {
                            id: densitySlider
                            objectName: "audioSimplifyDensitySlider"
                            theme: root.theme
                            Layout.fillWidth: true
                            from: 0
                            to: 1
                            stepSize: 0.05
                            modelValue: session.simplifyDensity
                            accessibleName: "简化保留密度"
                            onValueCommitted: root.applyExtractionControls()
                        }
                        Label {
                            text: Math.round(densitySlider.value * 100) + "%"
                            color: theme.textSecondary
                            font.family: theme.numberFont
                            Layout.preferredWidth: 42
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.space2
                        Label {
                            text: "音域"
                            color: theme.textSecondary
                            font.pixelSize: theme.typeSmall
                        }
                        SpinBox {
                            id: pitchMinBox
                            objectName: "audioPitchMinBox"
                            from: 21
                            to: 108
                            value: session.pitchMin
                            editable: true
                            Layout.preferredWidth: 128
                            Accessible.name: "最低音高"
                            onValueModified: root.applyExtractionControls()
                        }
                        Label {
                            text: "到"
                            color: theme.textMuted
                            font.pixelSize: theme.typeSmall
                        }
                        SpinBox {
                            id: pitchMaxBox
                            objectName: "audioPitchMaxBox"
                            from: 21
                            to: 108
                            value: session.pitchMax
                            editable: true
                            Layout.preferredWidth: 128
                            Accessible.name: "最高音高"
                            onValueModified: root.applyExtractionControls()
                        }
                        Label {
                            text: "限制候选音域，避免异常高低音"
                            color: theme.textMuted
                            font.pixelSize: theme.typeSmall
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Rectangle {
                visible: session.status === "loading" || session.status === "analyzing"
                Layout.fillWidth: true
                implicitHeight: progressColumn.implicitHeight + theme.space4
                radius: theme.radiusM
                color: theme.surface
                border.color: theme.borderSubtle
                ColumnLayout {
                    id: progressColumn
                    anchors.fill: parent
                    anchors.margins: theme.space3
                    spacing: theme.space2
                    RowLayout {
                        Layout.fillWidth: true
                        BusyIndicator { running: true; implicitWidth: 24; implicitHeight: 24 }
                        Label {
                            text: session.stage || "正在分析…"
                            color: theme.textPrimary
                            font.pixelSize: theme.typeBody
                            Layout.fillWidth: true
                        }
                    }
                    ProgressBar {
                        visible: session.progress >= 0
                        Layout.fillWidth: true
                        from: 0
                        to: 1
                        value: session.progress
                        Accessible.name: "音频分析进度"
                    }
                }
            }

            Rectangle {
                visible: session.status === "ready" || session.status === "imported"
                Layout.fillWidth: true
                implicitHeight: resultColumn.implicitHeight + theme.space4
                radius: theme.radiusM
                color: theme.surfaceSelected
                border.color: theme.accentPrimary
                ColumnLayout {
                    id: resultColumn
                    anchors.fill: parent
                    anchors.margins: theme.space3
                    spacing: theme.space2
                    Label {
                        text: "检测结果"
                        color: theme.textPrimary
                        font.pixelSize: theme.typePanelTitle
                        font.weight: theme.weightSemibold
                    }
                    Label {
                        text: "当前结果 " + session.detectedNotes.length + " 个音符"
                            + " · 原始候选 " + session.rawDetectedNotes.length + " 个"
                            + " · 时长 " + root.formatDuration(session.durationSeconds)
                        color: theme.textSecondary
                        font.pixelSize: theme.typeBody
                    }
                    Label {
                        text: "预计 BPM " + (session.tempoEstimate || "—")
                            + " · 音域 "
                            + (session.pitchRange.lowestName || "—") + "–"
                            + (session.pitchRange.highestName || "—")
                        color: theme.textSecondary
                        font.pixelSize: theme.typeBody
                    }
                    RowLayout {
                        spacing: theme.space2
                        AppButton {
                            objectName: "audioPreviewButton"
                            text: root.importer.previewActive ? "停止试听" : "试听结果"
                            theme: root.theme
                            variant: "secondary"
                            onClicked: root.importer.previewActive
                                ? root.importer.stopPreview()
                                : root.importer.previewResult()
                        }
                        AppButton {
                            objectName: "audioViewNotesButton"
                            text: root.notesVisible ? "隐藏音符" : "查看音符"
                            theme: root.theme
                            variant: "ghost"
                            onClicked: root.notesVisible = !root.notesVisible
                        }
                    }
                    ColumnLayout {
                        visible: root.notesVisible
                        Layout.fillWidth: true
                        spacing: 2
                        Repeater {
                            model: session.detectedNotes.slice(0, 16)
                            delegate: Label {
                                required property var modelData
                                text: root.pitchName(modelData.pitch) + "  ·  "
                                    + Number(modelData.startTime).toFixed(2) + "s  ·  "
                                    + Number(modelData.duration).toFixed(2) + "s"
                                color: theme.textSecondary
                                font.family: theme.numberFont
                                font.pixelSize: theme.typeSmall
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }

            Label {
                visible: session.status === "failed"
                text: session.errorMessage
                color: theme.danger
                font.pixelSize: theme.typeBody
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            ColumnLayout {
                visible: session.status === "ready"
                Layout.fillWidth: true
                spacing: theme.space2
                Label {
                    text: "导入位置"
                    color: theme.textPrimary
                    font.pixelSize: theme.typePanelTitle
                    font.weight: theme.weightSemibold
                }
                StyledComboBox {
                    theme: root.theme
                    id: startLocationBox
                    objectName: "audioStartLocationBox"
                    Layout.fillWidth: true
                    model: ["从第 1 小节开始", "从播放头开始"]
                    Accessible.name: "导入位置"
                }
                StyledComboBox {
                    theme: root.theme
                    id: tempoBox
                    objectName: "audioTempoChoiceBox"
                    Layout.fillWidth: true
                    model: [
                        "保持当前工程 BPM（" + editor.bpm + "）",
                        "使用检测到的 BPM（" + session.tempoEstimate + "）"
                    ]
                    Accessible.name: "导入 BPM 选择"
                }
                Label {
                    text: "使用当前工程拍号。"
                    color: theme.textMuted
                    font.pixelSize: theme.typeSmall
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
    }

    footer: DialogFooter {
        theme: root.theme
        spacing: theme.space2
        Item { Layout.fillWidth: true }
        AppButton {
            id: closeButton
            objectName: "audioImportCancelButton"
            text: session.status === "loading" || session.status === "analyzing" ? "取消分析" : "取消"
            theme: root.theme
            variant: "secondary"
            onClicked: {
                root.importer.cancel()
                root.close()
            }
        }
        AppButton {
            objectName: "audioImportButton"
            text: "导入到工程"
            theme: root.theme
            variant: "primary"
            enabled: session.status === "ready"
            onClicked: {
                var mode = startLocationBox.currentIndex === 1 ? "playhead" : "project_start"
                var position = startLocationBox.currentIndex === 1 ? playback.positionSeconds : 0
                var tempo = tempoBox.currentIndex === 1 ? "detected" : "keep"
                if (root.importer.importResult(mode, position, tempo))
                    root.close()
            }
        }
    }
}
