import QtQuick

Item {
    id: backdrop

    required property Theme theme
    property url backgroundSource: Qt.resolvedUrl("assets/launch-cosmos.webp")
    property url pngFallbackSource: Qt.resolvedUrl("assets/launch-cosmos.png")
    property bool dynamicEnabled: true
    property string motionLevel: "medium"
    property bool powerSaveEnabled: false
    property bool introAnimation: true
    property bool fallbackActive: false
    property bool triedPngFallback: false
    property bool warnedAboutFallback: false
    readonly property bool ambientRunning: visible && dynamicEnabled
        && !powerSaveEnabled && motionLevel !== "low"
    readonly property bool introRunning: visible && dynamicEnabled && !powerSaveEnabled
    readonly property int whiteKeyCount: 7
    readonly property int blackKeyCount: 5
    readonly property var keyModel: [
        { keyType: "white", depth: "near", x: 0.06, y: 0.78, scale: 1.35, rotation: -18 },
        { keyType: "black", depth: "near", x: 0.85, y: 0.70, scale: 1.20, rotation: 16 },
        { keyType: "white", depth: "near", x: 0.62, y: 0.92, scale: 1.15, rotation: 8 },
        { keyType: "white", depth: "mid", x: 0.15, y: 0.40, scale: 0.85, rotation: 12 },
        { keyType: "black", depth: "mid", x: 0.73, y: 0.36, scale: 0.82, rotation: -12 },
        { keyType: "white", depth: "mid", x: 0.78, y: 0.83, scale: 0.72, rotation: 14 },
        { keyType: "black", depth: "mid", x: 0.30, y: 0.82, scale: 0.70, rotation: -7 },
        { keyType: "white", depth: "far", x: 0.10, y: 0.18, scale: 0.42, rotation: -9 },
        { keyType: "black", depth: "far", x: 0.36, y: 0.16, scale: 0.38, rotation: 7 },
        { keyType: "white", depth: "far", x: 0.61, y: 0.12, scale: 0.46, rotation: -5 },
        { keyType: "black", depth: "far", x: 0.90, y: 0.22, scale: 0.40, rotation: 11 },
        { keyType: "white", depth: "far", x: 0.88, y: 0.50, scale: 0.50, rotation: -13 }
    ]

    onBackgroundSourceChanged: {
        if (!triedPngFallback)
            launchImage.source = backgroundSource
    }

    Image {
        id: launchImage
        objectName: "launchBackdropImage"
        anchors.fill: parent
        fillMode: Image.PreserveAspectCrop
        source: backdrop.backgroundSource
        readonly property bool imageReady: status === Image.Ready
        visible: status === Image.Ready
        onStatusChanged: {
            if (status !== Image.Error)
                return
            if (!backdrop.triedPngFallback) {
                backdrop.triedPngFallback = true
                source = backdrop.pngFallbackSource
            } else {
                backdrop.fallbackActive = true
                if (!backdrop.warnedAboutFallback) {
                    backdrop.warnedAboutFallback = true
                    console.warn("Launch background failed; using static fallback")
                }
            }
        }
    }

    Rectangle {
        objectName: "backdropFallback"
        anchors.fill: parent
        visible: backdrop.fallbackActive
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#071326" }
            GradientStop { position: 1.0; color: "#101B33" }
        }
    }

    Rectangle {
        id: blueGlow
        objectName: "launchBlueGlow"
        property real baseOpacity: 0.10
        property bool ambientActive: backdrop.ambientRunning
        width: backdrop.width * 0.58
        height: backdrop.height * 0.44
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        transformOrigin: Item.BottomLeft
        radius: width / 2
        color: "#2F8BFF"
        opacity: baseOpacity
        scale: 1.0
        onAmbientActiveChanged: {
            if (!ambientActive) {
                opacity = baseOpacity
                scale = 1.0
            }
        }
        SequentialAnimation {
            objectName: "launchBlueGlowAnimation"
            running: blueGlow.ambientActive
            loops: Animation.Infinite
            ParallelAnimation {
                NumberAnimation { target: blueGlow; property: "opacity"; to: 0.16; duration: 6000; easing.type: Easing.InOutSine }
                NumberAnimation { target: blueGlow; property: "scale"; to: 1.025; duration: 6000; easing.type: Easing.InOutSine }
            }
            ParallelAnimation {
                NumberAnimation { target: blueGlow; property: "opacity"; to: blueGlow.baseOpacity; duration: 6000; easing.type: Easing.InOutSine }
                NumberAnimation { target: blueGlow; property: "scale"; to: 1.0; duration: 6000; easing.type: Easing.InOutSine }
            }
        }
    }

    Rectangle {
        id: goldBeam
        objectName: "launchGoldBeam"
        property real baseOpacity: 0.07
        property bool ambientActive: backdrop.ambientRunning
        width: backdrop.width * 0.44
        height: backdrop.height * 0.35
        anchors.right: parent.right
        anchors.top: parent.top
        transformOrigin: Item.TopRight
        radius: width / 2
        color: "#F6C45B"
        opacity: baseOpacity
        scale: 1.0
        onAmbientActiveChanged: {
            if (!ambientActive) {
                opacity = baseOpacity
                scale = 1.0
            }
        }
        SequentialAnimation {
            objectName: "launchGoldBeamAnimation"
            running: goldBeam.ambientActive
            loops: Animation.Infinite
            ParallelAnimation {
                NumberAnimation { target: goldBeam; property: "opacity"; to: 0.12; duration: 7000; easing.type: Easing.InOutSine }
                NumberAnimation { target: goldBeam; property: "scale"; to: 1.018; duration: 7000; easing.type: Easing.InOutSine }
            }
            ParallelAnimation {
                NumberAnimation { target: goldBeam; property: "opacity"; to: goldBeam.baseOpacity; duration: 7000; easing.type: Easing.InOutSine }
                NumberAnimation { target: goldBeam; property: "scale"; to: 1.0; duration: 7000; easing.type: Easing.InOutSine }
            }
        }
    }

    Repeater {
        model: [
            { x: 0.08, y: 0.10, opacity: 0.34 }, { x: 0.21, y: 0.25, opacity: 0.22 },
            { x: 0.39, y: 0.08, opacity: 0.28 }, { x: 0.54, y: 0.20, opacity: 0.20 },
            { x: 0.68, y: 0.09, opacity: 0.30 }, { x: 0.82, y: 0.16, opacity: 0.24 },
            { x: 0.94, y: 0.34, opacity: 0.28 }, { x: 0.13, y: 0.56, opacity: 0.18 },
            { x: 0.89, y: 0.60, opacity: 0.22 }, { x: 0.47, y: 0.76, opacity: 0.16 }
        ]
        delegate: Rectangle {
            id: star
            required property int index
            required property var modelData
            objectName: "launchStar-" + index
            property real baseOpacity: modelData.opacity
            property bool ambientActive: backdrop.ambientRunning
            width: 2
            height: 2
            radius: 1
            x: modelData.x * backdrop.width
            y: modelData.y * backdrop.height
            color: backdrop.theme.textPrimary
            opacity: baseOpacity
            onAmbientActiveChanged: {
                if (!ambientActive)
                    opacity = baseOpacity
            }
            SequentialAnimation {
                running: star.ambientActive
                loops: Animation.Infinite
                NumberAnimation { target: star; property: "opacity"; to: Math.min(0.52, star.baseOpacity + 0.14); duration: 5500 + index * 300; easing.type: Easing.InOutSine }
                NumberAnimation { target: star; property: "opacity"; to: star.baseOpacity; duration: 5500 + index * 300; easing.type: Easing.InOutSine }
            }
        }
    }

    component CosmicKey: Rectangle {
            id: cosmicKey
            required property int keyIndex
            objectName: "cosmicKey-" + keyIndex
            readonly property var keyEntry: backdrop.keyModel[keyIndex]
            readonly property string keyType: keyEntry.keyType
            readonly property string depth: keyEntry.depth
            readonly property real normalizedX: keyEntry.x
            readonly property real normalizedY: keyEntry.y
            readonly property real baseY: normalizedY * backdrop.height - height / 2
            readonly property real yAmplitude: backdrop.motionLevel === "high"
                ? 6 + (keyIndex % 5) * 2 : 4 + (keyIndex % 4)
            readonly property real rotationAmplitude: backdrop.motionLevel === "high"
                ? 1.8 + (keyIndex % 4) * 0.35 : 1.2 + (keyIndex % 3) * 0.3
            readonly property int phaseDuration: 3800 + (keyIndex % 5) * 350
            property bool ambientActive: backdrop.ambientRunning
            width: (keyType === "white" ? 34 : 26) * keyEntry.scale
            height: (keyType === "white" ? 116 : 82) * keyEntry.scale
            radius: backdrop.theme.radiusS
            x: normalizedX * backdrop.width - width / 2
            y: baseY
            rotation: keyEntry.rotation
            color: keyType === "white" ? backdrop.theme.keyWhite : backdrop.theme.keyBlack
            border.color: keyType === "white"
                ? backdrop.theme.keyWhiteHighlight : backdrop.theme.keyBlackHighlight
            border.width: 1
            opacity: depth === "near" ? 0.92 : (depth === "mid" ? 0.68 : 0.42)
            transformOrigin: Item.Center
            onAmbientActiveChanged: {
                if (!ambientActive) {
                    y = baseY
                    rotation = keyEntry.rotation
                }
            }

            SequentialAnimation {
                running: cosmicKey.ambientActive
                loops: Animation.Infinite
                ParallelAnimation {
                    NumberAnimation {
                        target: cosmicKey
                        property: "y"
                        to: cosmicKey.baseY + cosmicKey.yAmplitude
                        duration: cosmicKey.phaseDuration
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        target: cosmicKey
                        property: "rotation"
                        to: keyEntry.rotation + cosmicKey.rotationAmplitude
                        duration: cosmicKey.phaseDuration
                        easing.type: Easing.InOutSine
                    }
                }
                ParallelAnimation {
                    NumberAnimation {
                        target: cosmicKey
                        property: "y"
                        to: cosmicKey.baseY - cosmicKey.yAmplitude
                        duration: cosmicKey.phaseDuration
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        target: cosmicKey
                        property: "rotation"
                        to: keyEntry.rotation - cosmicKey.rotationAmplitude
                        duration: cosmicKey.phaseDuration
                        easing.type: Easing.InOutSine
                    }
                }
            }
    }

    CosmicKey { keyIndex: 0 }
    CosmicKey { keyIndex: 1 }
    CosmicKey { keyIndex: 2 }
    CosmicKey { keyIndex: 3 }
    CosmicKey { keyIndex: 4 }
    CosmicKey { keyIndex: 5 }
    CosmicKey { keyIndex: 6 }
    CosmicKey { keyIndex: 7 }
    CosmicKey { keyIndex: 8 }
    CosmicKey { keyIndex: 9 }
    CosmicKey { keyIndex: 10 }
    CosmicKey { keyIndex: 11 }
}
