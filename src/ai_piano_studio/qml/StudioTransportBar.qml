import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "ToneDescription.js" as ToneDescription

RowLayout {
    id: controls
    required property Theme theme
    required property var transport
    spacing: 8
    component TransportTool: AppButton {
        labelPixelSize: 12
        labelWeight: variant === "primary" ? Font.Medium : controls.theme.weightRegular
        // Narrow footers reserve space for the content currently displayed,
        // rather than an invisible loading spinner beside every text button.
        implicitWidth: Math.max(implicitHeight,
            (controls.width < 1100 && !loading ? normalContentWidth
                : Math.max(normalContentWidth, loadingContentWidth)) + theme.space6)
    }
    TransportTool {
        objectName: "studioPlayPauseButton"; theme: controls.theme
        text: controls.transport.preparing ? "准备中…" : controls.transport.playing ? "暂停" : "播放"; variant: "primary"; size: "large"
        labelPixelSize: 14
        enabled: !controls.transport.preparing
        iconSource: controls.transport.playing ? "icons/pause.svg" : "icons/play.svg"
        onClicked: controls.transport.playPauseRequested()
    }
    AppIconButton {
        objectName: "studioStopButton"; theme: controls.theme
        iconSource: "icons/square.svg"; tooltip: "停止并回到开头"; accessibleName: tooltip
        onClicked: controls.transport.stopRequested()
    }
    TransportTool {
        objectName: "studioRecordButton"; theme: controls.theme; text: controls.transport.recording ? "结束录制" : "录制"
        semanticKind: "record"; variant: "ghost"; onClicked: controls.transport.recordRequested()
    }
    Label {
        objectName: "studioPosition"; Layout.preferredWidth: 116
        text: controls.transport.positionSeconds.toFixed(1) + " / " + controls.transport.totalSeconds.toFixed(1) + " s"
        color: controls.theme.textPrimary; font.family: controls.theme.numberFont; font.pixelSize: 14
    }
    StudioRhythmControl { theme: controls.theme; bar: controls.transport.rhythmBridge }
    TransportTool {
        objectName:"playingSettingsButton"; theme:controls.theme; text:"演奏"; variant:"ghost"; enabled:!keyboard.recording
        onClicked:controls.transport.playingSettingsRequested()
    }
    TransportTool {
        objectName:"sustainPedalButton"; theme:controls.theme; text:"踏板"; variant:keyboard.pedalDown ? "primary" : "ghost"
        onPressedChanged:keyboard.setPedal(pressed)
    }
    TransportTool { objectName: "studioCancelRecording"; theme:controls.theme; text:"取消本段"; visible:keyboard.recording; onClicked:keyboard.cancelRecording() }
    Item { Layout.fillWidth: true }
    Label {
        id: statusSummary
        objectName: "studioStatusSummary"
        Layout.maximumWidth: controls.width > 1200 ? 270 : 150; Layout.fillWidth: true
        readonly property string syncText: controls.transport.statusSyncState === "syncing"
            ? "同步中" : controls.transport.statusUnsyncedCount > 0
            ? "待同步 " + controls.transport.statusUnsyncedCount : "已同步"
        readonly property string fullText: (keyboard.recording ? keyboard.recordingStatus + " · " : "")
            + (controls.transport.statusSaving ? "正在保存 · " : "")
            + controls.transport.statusTooltipContextText + " · " + syncText
            + " · 长度 " + controls.transport.statusDurationSeconds.toFixed(1) + "s"
            + (controls.transport.statusUnsyncedCount > 0
                ? " · 预计 " + controls.transport.statusEstimatedDurationSeconds.toFixed(1) + "s" : "")
        text: keyboard.recording ? keyboard.recordingStatus : controls.transport.statusSaving ? "正在保存…"
            : controls.transport.statusCompactContextText
        color: controls.theme.textSecondary
        font.pixelSize: 12; elide: Text.ElideRight
        Accessible.name: fullText
        HoverHandler { id: statusHover }
        ToolTip.visible: statusHover.hovered
        ToolTip.delay: 400
        ToolTip.text: fullText
    }
    TransportTool {
        id: soundButton
        objectName: "studioSoundButton"; theme: controls.theme; text: "声响调节"; variant: "ghost"
        onClicked: {
            if (soundPopup.visible) {
                soundPopup.close()
                return
            }
            var overlay = Overlay.overlay
            var point = soundButton.mapToItem(overlay, 0, 0)
            soundPopup.x = Math.max(8, Math.min(overlay.width - soundPopup.width - 8,
                point.x + soundButton.width - soundPopup.width)) - point.x
            soundPopup.y = Math.max(8, point.y - soundPopup.height - 12) - point.y
            soundPopup.open()
        }
        Popup {
            id: soundPopup; objectName: "studioSoundPopup"
            parent: soundButton
            width: 320; padding: 20; modal: false; focus: true
            height: Math.min(soundContent.implicitHeight + topPadding + bottomPadding, Overlay.overlay ? Overlay.overlay.height - 24 : 700)
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
            onAboutToShow: keyboard.suspendInput("sound-popup", true)
            onClosed: keyboard.suspendInput("sound-popup", false)
            background: Rectangle { color: controls.theme.popupBackground; radius: 12; border.color: controls.theme.borderStrong }
            contentItem: ScrollView {
                id: soundScroll
                objectName: "studioSoundScroll"
                clip: true
                rightPadding: 10
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical: SafeScrollBar {
                    theme: controls.theme; policy: ScrollBar.AsNeeded
                    parent: soundScroll
                    anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.right: parent.right
                    visible: size < 1
                }
                ColumnLayout {
                id: soundContent
                width: soundScroll.availableWidth
                spacing: 10
                Label { text: "声响调节"; color: controls.theme.textPrimary; font.pixelSize: 16; font.weight: Font.Medium }
                StyledComboBox {
                    objectName: "studioProjectSound"; theme: controls.theme; Layout.fillWidth: true
                    visible: playback.projectSoundAvailable
                    model: ["Salamander 三角钢琴", "FreePats 立式钢琴"]
                    currentIndex: playback.projectSoundId === "upright-sfz" ? 1 : 0
                    displayText: playback.projectSoundPreparing ? "正在预热立式钢琴…" : currentText
                    Accessible.name: "主播放器音源"
                    indicator: DropdownIndicator { theme: controls.theme }
                    onActivated: function(index) {
                        playback.selectProjectSound(index === 1 ? "upright-sfz" : "salamander")
                        currentIndex = Qt.binding(function() { return playback.projectSoundId === "upright-sfz" ? 1 : 0 })
                    }
                }
                Label {
                    Layout.fillWidth: true; visible: playback.projectSoundStatus.length > 0
                    text: playback.projectSoundStatus; textFormat: Text.PlainText; wrapMode: Text.Wrap
                    color: controls.theme.textSecondary; font.pixelSize: 12
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: playback.soundParametersAvailable
                    Label {
                        Layout.fillWidth: true
                        text: "音源音量 · " + (soundTrim.value > 0 ? "+" : "") + soundTrim.value.toFixed(1) + " dB"
                        color: controls.theme.textPrimary; font.pixelSize: 14
                    }
                    TransportTool {
                        theme: controls.theme; text: "恢复默认"; variant: "ghost"
                        onClicked: playback.resetProjectSoundParameters()
                        ToolTip.visible: hovered; ToolTip.text: "恢复当前音源的音量与调音参数"
                    }
                }
                StyledSlider {
                    id: soundTrim; objectName: "studioSourceTrim"
                    Layout.fillWidth: true; theme: controls.theme
                    visible: playback.soundParametersAvailable
                    from: -9; to: 9; stepSize: 0.5; modelValue: playback.projectSoundTrim
                    accessibleName: "当前音源音量微调"
                    onValueEdited: value => playback.setProjectSoundTrim(value)
                }
                Label { text: "音色亮度 · " + ToneDescription.describe(toneSlider.value); color: controls.theme.textPrimary; font.pixelSize: 14 }
                StyledSlider {
                    id: toneSlider; objectName: "studioToneSlider"; theme: controls.theme; Layout.fillWidth: true
                    from: 0; to: 1; stepSize: 0.05
                    modelValue: playback.soundParametersAvailable ? playback.projectSoundBrightness : controls.transport.pianoToneValue
                    enabled: playback.soundParametersAvailable || controls.transport.pianoToneAvailable; accessibleName: "音色亮度：柔和到明亮"
                    onValueEdited: value => { if (playback.soundParametersAvailable) playback.setProjectSoundParameter("brightness", value) }
                    onValueCommitted: value => { if (!playback.soundParametersAvailable) controls.transport.pianoToneEdited(value) }
                }
                Label { visible: playback.soundParametersAvailable; text: "力度响应 · " + Math.round(responseSlider.value * 100) + "%"; color: controls.theme.textPrimary; font.pixelSize: 14 }
                StyledSlider {
                    id: responseSlider; objectName: "studioSoundResponse"; theme: controls.theme; Layout.fillWidth: true
                    visible: playback.soundParametersAvailable
                    from: 0; to: 1; stepSize: .05; modelValue: playback.projectSoundResponse
                    accessibleName: "力度响应：轻触响到力度分明，50%保持原力度"
                    onValueEdited: value => playback.setProjectSoundParameter("response", value)
                }
                Label { visible: playback.soundParametersAvailable; text: "回响 / 混响 · " + Math.round(reverbSlider.value * 100) + "%"; color: controls.theme.textPrimary; font.pixelSize: 14 }
                StyledSlider {
                    id: reverbSlider; objectName: "studioSoundReverb"; theme: controls.theme; Layout.fillWidth: true
                    visible: playback.soundParametersAvailable
                    from: 0; to: 1; stepSize: .05; modelValue: playback.projectSoundReverb
                    accessibleName: "回响与混响量，0%关闭"
                    onValueEdited: value => playback.setProjectSoundParameter("reverb", value)
                }
                Label { visible: playback.soundParametersAvailable; text: "空间宽度 · " + Math.round(widthSlider.value * 200) + "%"; color: controls.theme.textPrimary; font.pixelSize: 14 }
                StyledSlider {
                    id: widthSlider; objectName: "studioSoundWidth"; theme: controls.theme; Layout.fillWidth: true
                    visible: playback.soundParametersAvailable
                    from: 0; to: 1; stepSize: .05; modelValue: playback.projectSoundWidth
                    accessibleName: "立体声空间宽度，100%保持原声场"
                    onValueEdited: value => playback.setProjectSoundParameter("width", value)
                }
                TransportTool {
                    objectName: "studioTonePreview"; theme: controls.theme; text: "试听音色"
                    enabled: !playback.projectSoundPreparing && (playback.soundParametersAvailable || controls.transport.pianoToneAvailable)
                    onClicked: playback.auditionCurrentSound()
                }
                Label { text: "播放速度"; color: controls.theme.textSecondary; font.pixelSize: 14 }
                StyledComboBox {
                    theme: controls.theme
                    objectName: "studioPlaybackRate"; model: ["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"]
                    Accessible.name: "播放速度"
                    indicator: DropdownIndicator { theme: controls.theme }
                    property var rates: [0.5,0.75,1,1.25,1.5,2]
                    currentIndex: rates.indexOf(controls.transport.playbackRate)
                    displayText: currentIndex >= 0 ? currentText : String(controls.transport.playbackRate) + "×"
                    onActivated: controls.transport.playbackRateEdited(rates[currentIndex])
                }

                Label { Layout.fillWidth: true; visible: !controls.transport.pianoToneAvailable; text: controls.transport.pianoToneStatusMessage; color: controls.theme.textSecondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
                Label {
                    visible: playback.soundParametersAvailable || controls.transport.sfzPreviewAvailable
                    text: playback.soundParametersAvailable ? "当前音源试听" : "立式钢琴试听"
                    color: controls.theme.textPrimary
                    font.pixelSize: 13
                }
                RowLayout {
                    visible: playback.soundParametersAvailable || controls.transport.sfzPreviewAvailable
                    Layout.fillWidth: true
                    spacing: 8
                    TransportTool {
                        objectName: "studioSfzPreview"
                        theme: controls.theme
                        text: !playback.soundParametersAvailable && controls.transport.sfzPreviewBusy ? "准备中…" : "试听当前歌曲 8 秒"
                        enabled: !playback.projectSoundPreparing && !controls.transport.sfzPreviewBusy
                        onClicked: { if (playback.soundParametersAvailable) playback.auditionCurrentSoundExcerpt(); else controls.transport.sfzPreviewRequested() }
                    }
                    TransportTool {
                        objectName: "studioSfzStopPreview"
                        theme: controls.theme
                        text: "停止试听"
                        variant: "ghost"
                        onClicked: { if (playback.soundParametersAvailable) playback.stopCurrentSoundPreview(); else controls.transport.sfzPreviewStopped() }
                    }
                }
                Label {
                    visible: playback.soundParametersAvailable || controls.transport.sfzPreviewAvailable
                    Layout.fillWidth: true
                    text: playback.soundParametersAvailable ? "使用当前音源与调音参数。力度响应从后续音符开始生效。" : controls.transport.sfzPreviewStatusText
                    textFormat: Text.PlainText
                    color: controls.theme.textSecondary
                    wrapMode: Text.Wrap
                    font.pixelSize: 11
                }
                }
            }
        }
    }
    Label { objectName: "studioVolumeLabel"; text: "总音量"; color: controls.theme.textSecondary; font.pixelSize: 12 }
    StyledSlider {
        objectName: "studioVolumeSlider"; theme: controls.theme
        Layout.preferredWidth: 90; from: 0; to: 1; modelValue: controls.transport.volume; accessibleName: "主音量"
        onValueEdited: value => controls.transport.volumeEdited(value)
    }
    Label { objectName: "studioVolumeValue"; Layout.preferredWidth: 38; text: Math.round(controls.transport.volume * 100) + "%"; color: controls.theme.textPrimary; font.family: controls.theme.numberFont; font.pixelSize: 12 }
}
