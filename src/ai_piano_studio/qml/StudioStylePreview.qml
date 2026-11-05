import QtQuick

// Explicit, silent demonstration data; never subscribes to project audio.
Item {
    id: preview
    objectName: "studioStylePreview-" + style
    required property var theme
    required property var settings
    property string style: "glass"
    property bool animating: false
    property real phase: 0.8
    clip: true
    QtObject {
        id: sampleSettings
        readonly property var values: Object.assign({}, preview.settings.values, {
            feedbackStyle: preview.style,
            noteFeedback: false, keyFeedback: false
        })
    }
    QtObject {
        id: sampleAudio
        readonly property bool enabled: true
        readonly property real rms: 0.12 + Math.sin(preview.phase) * 0.04
        readonly property var bands: {
            var result = []
            for (var i = 0; i < 32; ++i)
                result.push(0.18 + Math.abs(Math.sin(i * 0.27 + preview.phase)) * 0.45)
            return result
        }
    }
    QtObject {
        id: samplePlayback
        readonly property bool playing: true
        readonly property real volume: 0.8
    }
    QtObject {
        id: sampleTimeline
        readonly property int keyWidth: 0
        readonly property int rowHeight: 12
        readonly property bool focusMode: false
        readonly property bool gestureFeedbackVisible: false
        readonly property bool creatingNote: false
        readonly property bool compositeDragPreviewActive: false
    }
    Rectangle { anchors.fill: parent; color: preview.theme.appBackground }
    Repeater {
        model: 5
        Rectangle {
            required property int index
            x: 0; y: index * preview.height / 5
            width: preview.width; height: 1; color: preview.theme.gridMinor
        }
    }
    StudioFeedback {
        id: renderer
        anchors.fill: parent
        theme: preview.theme; settings: sampleSettings
        visualizer: sampleAudio; playbackController: samplePlayback
        timeline: sampleTimeline; active: preview.visible
        previewFrozen: true
    }
    Timer {
        interval: 66; repeat: true
        running: preview.visible && preview.animating && !preview.settings.values.reducedMotion
        onTriggered: { preview.phase += 0.16; renderer.repaint() }
    }
}
