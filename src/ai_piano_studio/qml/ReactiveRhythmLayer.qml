import QtQuick
import QtQuick.Window

Item {
    id: root
    objectName: "reactiveRhythmLayer"
    required property var theme
    required property var scene
    required property var playbackController
    required property real keyWidth
    property var visualizer: null
    property bool focusMode: false

    anchors.fill: parent
    z: 1
    // This layer is visual-only. It must never steal canvas gestures.
    enabled: false
    readonly property bool hasVisualizer: root.visualizer !== null
        && root.visualizer !== undefined
    visible: root.hasVisualizer
        && root.visualizer.enabled
        && !scene.powerSaveEnabled
        && !scene.focusModeEnabled
        && !root.focusMode
    clip: true
    readonly property bool windowAllowsDecoration: Window.window !== null
        && Window.window.visible && Window.window.visibility !== Window.Minimized
    readonly property bool decorationRunning: root.visible && root.windowAllowsDecoration

    readonly property var spectrumBands: root.hasVisualizer ? root.visualizer.bands : []
    readonly property real audioRms: root.hasVisualizer
        ? Math.max(0, Math.min(1, Number(root.visualizer.rms))) : 0
    readonly property real audioPeak: root.hasVisualizer
        ? Math.max(0, Math.min(1, Number(root.visualizer.peak))) : 0
    readonly property real audioLow: root.hasVisualizer
        ? Math.max(0, Math.min(1, Number(root.visualizer.low))) : 0
    readonly property real audioMid: root.hasVisualizer
        ? Math.max(0, Math.min(1, Number(root.visualizer.mid))) : 0
    readonly property real audioHigh: root.hasVisualizer
        ? Math.max(0, Math.min(1, Number(root.visualizer.high))) : 0
    readonly property real intensity: Math.max(0, Math.min(1, scene.visualizerIntensity / 100))
    readonly property real readabilityCap: scene.visualizerReadabilityProtection ? 0.68 : 1.0
    readonly property real visualizerOpacity: Math.max(
        0, Math.min(1, scene.visualizerSpectrumOpacity / 100)
    )
    readonly property real spectrumHeightRatio: root.theme.nocturne ? 0.15
        : (scene.visualizerSpectrumHeight === "low"
            ? 0.20 : (scene.visualizerSpectrumHeight === "high" ? 0.40 : 0.30))
    readonly property real effectHeight: Math.min(
        root.height * (root.theme.nocturne ? 0.15 : 0.40),
        Math.max(root.theme.nocturne ? 18 : 24, root.height * root.spectrumHeightRatio)
    )
    readonly property real bandAreaHeight: Math.max(24, root.effectHeight * 0.82)
    readonly property real targetEnergy: root.audioRms
    readonly property real pitchBand: root.audioHigh >= root.audioLow
        ? 0.5 + (root.audioHigh - root.audioLow) * 0.5 : 0.5
    readonly property int ribbonCount: scene.visualizerRibbonCount === "many"
        ? 3 : (scene.visualizerRibbonCount === "standard" ? 2 : 1)
    readonly property int ribbonDuration: scene.visualizerRibbonSpeed === "fast"
        ? 5200 : (scene.visualizerRibbonSpeed === "standard" ? 9000 : 15000)
    property real smoothedEnergy: 0

    function bandValue(index) {
        if (index < 0 || index >= root.spectrumBands.length)
            return 0
        return Math.max(0, Math.min(1, Number(root.spectrumBands[index])))
    }

    function bandGain(index) {
        var position = root.spectrumBands.length <= 1
            ? 0 : index / (root.spectrumBands.length - 1)
        if (position < 0.34)
            return Math.max(0, Math.min(2, scene.visualizerLowGain / 100))
        if (position < 0.68)
            return Math.max(0, Math.min(2, scene.visualizerMidGain / 100))
        return Math.max(0, Math.min(2, scene.visualizerHighGain / 100))
    }

    function applySaturation(value, alpha) {
        var color = Qt.color(value)
        var gray = color.r * 0.2126 + color.g * 0.7152 + color.b * 0.0722
        var saturation = 0.22 + Math.max(0, Math.min(1, scene.visualizerSaturation / 100)) * 0.78
        return Qt.rgba(
            gray + (color.r - gray) * saturation,
            gray + (color.g - gray) * saturation,
            gray + (color.b - gray) * saturation,
            Math.max(0, Math.min(1, alpha))
        )
    }

    function bandColor(index, alpha) {
        var position = root.spectrumBands.length <= 1
            ? 0 : index / (root.spectrumBands.length - 1)
        var baseColor = root.theme.accentPrimary
        if (root.theme.nocturne) {
            baseColor = root.theme.mixColor(
                root.theme.accentMusic, root.theme.accentPrimary, 0.42
            )
        } else if (scene.visualizerColorMode === "monochrome") {
            baseColor = root.theme.accentPrimary
        } else if (scene.visualizerColorMode === "custom") {
            var lowColor = Qt.color(scene.visualizerLowColor)
            var midColor = Qt.color(scene.visualizerMidColor)
            var highColor = Qt.color(scene.visualizerHighColor)
            baseColor = position < 0.5
                ? root.theme.mixColor(lowColor, midColor, position * 2)
                : root.theme.mixColor(midColor, highColor, (position - 0.5) * 2)
        } else if (position < 0.5) {
            baseColor = root.theme.mixColor(
                root.theme.notePitchLow, root.theme.accentPrimary, position * 2
            )
        } else {
            baseColor = root.theme.mixColor(
                root.theme.accentPrimary, root.theme.notePitchHigh, (position - 0.5) * 2
            )
        }
        return root.applySaturation(baseColor, alpha)
    }

    onVisibleChanged: {
        if (!visible)
            root.smoothedEnergy = 0
    }

    Timer {
        id: smoothingTimer
        objectName: "rhythmSmoothingTimer"
        interval: 33
        repeat: true
        running: root.decorationRunning
        onTriggered: {
            var target = root.targetEnergy
            var response = target > root.smoothedEnergy ? 0.30 : 0.14
            root.smoothedEnergy += (target - root.smoothedEnergy) * response
            if (Math.abs(root.smoothedEnergy - target) < 0.002)
                root.smoothedEnergy = target
        }
    }

    Rectangle {
        objectName: "reactiveAtmosphere"
        x: root.keyWidth
        y: root.height - root.effectHeight
        width: Math.max(0, root.width - root.keyWidth)
        height: root.effectHeight
        color: root.theme.mixColor(
            root.theme.pianoRollBackground,
            root.theme.accentPrimary,
            0.10,
            Math.min(root.readabilityCap * 0.22,
                root.visualizerOpacity * root.smoothedEnergy * root.intensity * 0.18)
        )
        opacity: root.audioRms > 0.001 ? 1 : 0
    }

    Repeater {
        id: ribbonRepeater
        objectName: "reactiveLightRibbons"
        model: root.ribbonCount
        delegate: Rectangle {
            required property int index
            objectName: "reactiveLightRibbon-" + index
            visible: root.visible && scene.visualizerRibbonEnabled && root.audioRms > 0.001
            x: root.keyWidth - width + (index * width * 0.55)
            width: Math.max(80, (root.width - root.keyWidth) * 0.64)
            y: root.height - root.effectHeight * (0.52 + index * 0.12 + root.audioMid * 0.12)
            height: 2
            radius: 1
            color: root.bandColor(
                Math.floor(root.spectrumBands.length * (0.30 + index * 0.16)),
                Math.min(root.readabilityCap * 0.42,
                    root.visualizerOpacity * (0.14 + root.audioMid * root.intensity
                        * scene.visualizerRibbonIntensity / 100))
            )
            opacity: Math.min(root.readabilityCap, 0.25 + root.smoothedEnergy * 0.30)
            NumberAnimation on x {
                running: parent.visible && root.decorationRunning
                from: root.keyWidth - parent.width
                to: root.width
                duration: root.ribbonDuration
                loops: Animation.Infinite
                easing.type: Easing.InOutSine
            }
        }
    }

    Rectangle {
        objectName: "reactiveBeatPulse"
        visible: root.visible && scene.visualizerBeatEnabled && root.audioPeak > 0.01
        x: root.keyWidth
        y: root.height - root.effectHeight * 0.48
        width: Math.max(0, root.width - root.keyWidth)
        height: root.effectHeight * 0.48
        color: root.theme.accentMusic
        opacity: Math.min(
            root.readabilityCap * 0.18,
            root.audioPeak * root.intensity * scene.visualizerBeatIntensity / 100
        )
    }

    Repeater {
        id: rhythmBars
        objectName: "rhythmBars"
        model: root.spectrumBands.length
        delegate: Item {
            required property int index
            objectName: "spectrumBand-" + index
            readonly property real contentWidth: Math.max(0, root.width - root.keyWidth)
            readonly property real slotWidth: contentWidth / Math.max(1, rhythmBars.model)
            readonly property real value: Math.max(
                0, Math.min(1, root.bandValue(index) * root.bandGain(index))
            )
            readonly property real barHeight: Math.max(
                1,
                root.bandAreaHeight * value * root.intensity
                    * Math.max(0, Math.min(1, scene.visualizerSpectrumStrength / 100))
            )
            x: root.keyWidth + index * slotWidth
            y: root.height - root.effectHeight
            width: slotWidth
            height: root.effectHeight
            visible: root.visible && scene.visualizerSpectrumEnabled

            Rectangle {
                objectName: "spectrumGlow-" + index
                visible: scene.visualizerGlowEnabled && parent.value > 0.01
                x: Math.max(0, (parent.width - width) / 2)
                y: parent.height - parent.barHeight - 3
                width: Math.min(parent.width, scene.visualizerBarWidth === "thin" ? 7 : 11)
                    * (scene.visualizerGlowSpread === "wide" ? 1.8
                        : (scene.visualizerGlowSpread === "mid" ? 1.35 : 1))
                height: parent.barHeight + (scene.visualizerGlowSpread === "wide" ? 12
                    : (scene.visualizerGlowSpread === "mid" ? 9 : 6))
                radius: scene.visualizerGlowSpread === "wide" ? 4 : 2
                color: root.bandColor(
                    parent.index,
                    Math.min(root.readabilityCap * 0.16,
                        root.visualizerOpacity * scene.visualizerGlowIntensity / 100
                            * parent.value * 0.20)
                )
            }

            Rectangle {
                objectName: "spectrumBar-" + index
                x: Math.max(0, (parent.width - width) / 2)
                y: parent.height - parent.barHeight
                width: Math.max(
                    1,
                    Math.min(parent.width - 2,
                        scene.visualizerBarWidth === "thin" ? 2 : 4)
                )
                height: parent.barHeight
                radius: 1
                color: root.bandColor(
                    parent.index,
                    Math.min(root.readabilityCap * 0.78,
                        root.visualizerOpacity * (0.22 + parent.value * root.intensity * 0.46))
                )
            }
        }
    }
}
