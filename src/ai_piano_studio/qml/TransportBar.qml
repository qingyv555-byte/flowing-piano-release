import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: transport
    objectName: "bottomTransportBar"

    required property Theme theme
    property bool playing: false
    property bool preparing: false
    property bool auditionPlaying: false
    property bool recording: false
    property real positionSeconds: 0
    property real totalSeconds: 0
    property int bpm: 120
    property var rhythmBridge: null
    property real pianoToneValue: 0.5
    property bool pianoToneAvailable: true
    property string pianoToneStatusMessage: ""
    property bool sfzPreviewAvailable: false
    property bool sfzPreviewBusy: false
    property string sfzPreviewStatusText: ""
    property real pianoTonePreview: pianoToneValue
    property real volume: 0.65
    property real playbackRate: 1.0
    property bool followPlayback: true
    property bool compact: width < 1280
    readonly property bool nocturneNarrow: theme.nocturne && width < 1200
    property string statusContextText: ""
    property string statusCompactContextText: statusContextText
    property string statusTooltipContextText: statusContextText
    property string statusTemporaryText: ""
    property string statusTemporaryKind: "info"
    property string statusSyncState: "idle"
    property int statusUnsyncedCount: 0
    property real statusDurationSeconds: 0
    property real statusEstimatedDurationSeconds: 0
    property bool statusSaving: false

    signal playPauseRequested()
    signal stopRequested()
    signal recordRequested()
    signal playingSettingsRequested()
    signal volumeEdited(real value)
    signal playbackRateEdited(real value)
    signal followPlaybackEdited(bool value)
    signal pianoToneEdited(real value)
    signal sfzPreviewRequested()
    signal sfzPreviewStopped()

    onPianoToneValueChanged: pianoTonePreview = pianoToneValue

    implicitHeight: theme.transportHeight
    color: theme.elevatedSurface
    border.color: theme.borderStrong

    RowLayout {
        visible: !transport.theme.studio
        anchors.fill: parent
        anchors.leftMargin: transport.theme.space3
        anchors.rightMargin: transport.theme.space3
        spacing: transport.nocturneNarrow
            ? transport.theme.space1 : transport.theme.space2

        AppButton {
            objectName: "transportPlayPauseButton"
            theme: transport.theme
            size: "large"
            text: transport.playing ? "暂停" : "播放"
            iconSource: transport.playing ? "icons/pause.svg" : "icons/play.svg"
            variant: "primary"
            Layout.minimumWidth: transport.nocturneNarrow ? 96 : 120
            ToolTip.visible: hovered
            ToolTip.text: "空格：播放 / 暂停"
            onClicked: transport.playPauseRequested()
            Accessible.name: transport.playing ? "暂停" : "播放"
        }

        AppIconButton {
            objectName: "stopButton"
            theme: transport.theme
            iconSource: "icons/square.svg"
            tooltip: "停止"
            accessibleName: "停止"
            // STOP must also cancel cold-start preparation and temporary
            // selection playback, which leave the main playhead at zero.
            enabled: transport.playing || transport.auditionPlaying
                || transport.positionSeconds > 0 || transport.totalSeconds > 0
            onClicked: transport.stopRequested()
        }

        AppButton {
            objectName: "recordButton"
            theme: transport.theme
            text: transport.nocturneNarrow ? ""
                : (transport.recording ? "停止录制" : "录制")
            iconSource: "icons/circle.svg"
            semanticKind: "record"
            variant: transport.recording ? "danger" : "secondary"
            showSemanticIndicator: false
            Layout.minimumWidth: transport.nocturneNarrow ? 44 : implicitWidth
            Layout.preferredWidth: transport.nocturneNarrow ? 44 : implicitWidth
            onClicked: transport.recordRequested()
            Accessible.name: transport.recording ? "停止录制" : "录制"
        }

        Label {
            objectName: "transportPositionLabel"
            Layout.minimumWidth: transport.nocturneNarrow ? 100 : 132
            Layout.maximumWidth: transport.nocturneNarrow ? 100 : 132
            text: transport.positionSeconds.toFixed(1) + "s / " + transport.totalSeconds.toFixed(1) + "s"
            color: transport.theme.textPrimary
            font.family: transport.theme.numberFont
            horizontalAlignment: Text.AlignHCenter
        }

        RowLayout {
            objectName: "transportPianoToneControl"
            spacing: transport.theme.space1

            Label {
                text: "音色"
                visible: !transport.nocturneNarrow
                color: transport.theme.textSecondary
                font.pixelSize: transport.theme.typeSmall
            }

            StyledSlider {
                id: transportPianoToneSlider
                objectName: "transportPianoToneSlider"
                theme: transport.theme
                Layout.minimumWidth: transport.nocturneNarrow ? 64 : 88
                Layout.maximumWidth: transport.nocturneNarrow ? 64 : 88
                from: 0
                to: 1
                stepSize: 0.05
                modelValue: transport.pianoTonePreview
                accessibleName: "钢琴音色：柔和到明亮"
                enabled: transport.pianoToneAvailable
                property string toneStatusMessage: {
                    var status = transport.pianoToneStatusMessage || ""
                    if (transport.pianoToneAvailable
                            || (status.indexOf("Legacy Piano") >= 0
                                && status.indexOf("日志") >= 0))
                        return status
                    return (status ? status + "；" : "")
                        + "已自动使用 Legacy Piano；详情请查看日志。"
                }
                onValueEdited: value => transport.pianoTonePreview = value
                onValueCommitted: value => {
                    transport.pianoTonePreview = value
                    transport.pianoToneEdited(value)
                }
                HoverHandler { id: pianoToneHover }
                ToolTip.visible: pianoToneHover.hovered
                ToolTip.text: enabled
                    ? "钢琴音色：柔和 — 明亮"
                    : toneStatusMessage
            }
        }

        Label {
            text: "播放速度"
            visible: !transport.nocturneNarrow
            color: transport.theme.textSecondary
        }

        ComboBox {
            id: playbackRateControl
            objectName: "transportPlaybackRateControl"
            Layout.minimumWidth: transport.nocturneNarrow ? 72 : 92
            Layout.maximumWidth: transport.nocturneNarrow ? 72 : 92
            property var rateValues: [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
            model: rateValues
            currentIndex: Math.max(0, rateValues.indexOf(transport.playbackRate))
            contentItem: Label {
                objectName: "playbackRateLabel"
                leftPadding: 10
                rightPadding: 8
                text: Number(playbackRateControl.currentValue).toFixed(1) + "×"
                color: transport.theme.textPrimary
                verticalAlignment: Text.AlignVCenter
            }
            onActivated: index => transport.playbackRateEdited(Number(rateValues[index]))
            Accessible.name: "播放速度"
        }

        Label {
            text: "音量"
            visible: !transport.nocturneNarrow
            color: transport.theme.textSecondary
        }

        StyledSlider {
            objectName: "transportVolumeSlider"
            theme: transport.theme
            Layout.minimumWidth: transport.nocturneNarrow ? 72 : 100
            Layout.maximumWidth: transport.nocturneNarrow ? 72 : 100
            from: 0
            to: 1
            modelValue: transport.volume
            accessibleName: "主音量"
            onValueEdited: value => transport.volumeEdited(value)
        }

        Label {
            objectName: "volumePercentLabel"
            visible: !transport.nocturneNarrow
            Layout.minimumWidth: 36
            Layout.maximumWidth: 36
            text: Math.round(transport.volume * 100) + "%"
            color: transport.theme.textPrimary
            font.family: transport.theme.numberFont
            font.pixelSize: transport.theme.typeSmall
        }

        Item { Layout.fillWidth: true }

        EditorStatusBar {
            id: embeddedEditorStatus
            theme: transport.theme
            embedded: true
            contextText: transport.statusContextText
            compactContextText: transport.statusCompactContextText
            tooltipContextText: transport.statusTooltipContextText
            temporaryText: transport.statusTemporaryText
            temporaryKind: transport.statusTemporaryKind
            syncState: transport.statusSyncState
            unsyncedCount: transport.statusUnsyncedCount
            durationSeconds: transport.statusDurationSeconds
            estimatedDurationSeconds: transport.statusEstimatedDurationSeconds
            saving: transport.statusSaving
            Layout.preferredWidth: transport.nocturneNarrow ? 220
                : (transport.width >= 1600 ? 620 : 360)
            Layout.minimumWidth: transport.nocturneNarrow ? 160
                : (transport.width >= 1200 ? 250 : 180)
            Layout.maximumWidth: transport.nocturneNarrow ? 220
                : (transport.width >= 1600 ? 620 : 360)
            Layout.fillHeight: true
        }
    }

    StudioTransportBar {
        anchors.fill: parent
        anchors.leftMargin: 16; anchors.rightMargin: 16
        visible: transport.theme.studio
        theme: transport.theme; transport: transport
    }

    Rectangle {
        objectName: "playbackProgressTrack"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 3
        color: transport.theme.borderSubtle
        Rectangle {
            width: parent.width * Math.max(0, Math.min(1,
                transport.totalSeconds > 0 ? transport.positionSeconds / transport.totalSeconds : 0))
            height: parent.height
            color: transport.recording ? transport.theme.record : transport.theme.accentPrimary
        }
    }

}
