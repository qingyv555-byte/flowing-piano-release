import QtQuick
import QtQuick.Window
import Qt5Compat.GraphicalEffects

Item {
    id: root
    objectName: "editorBackgroundLayer"
    required property var scene
    required property var theme
    property bool focusMode: false

    anchors.fill: parent
    // Keep the layer above Timeline's opaque base color while declaring it
    // before the ruler/canvas so all editor content remains on top.
    z: 0
    enabled: false
    visible: scene.backgroundEnabled && !scene.focusModeEnabled && !root.focusMode
        && root.theme.backgroundSourceFor(root.scene).toString().length > 0
    clip: true

    readonly property bool windowAllowsDecoration: Window.window !== null
        && Window.window.visible && Window.window.visibility !== Window.Minimized
    readonly property bool ambientRunning: visible && root.windowAllowsDecoration
        && scene.dynamicEnabled
        && scene.backgroundMotion > 0.01
        && scene.motionLevel !== "low"
        && !scene.powerSaveEnabled
        && !scene.focusModeEnabled

    Image {
        id: backgroundImage
        objectName: "editorBackgroundImage"
        anchors.fill: parent
        asynchronous: true
        cache: true
        fillMode: Image.PreserveAspectCrop
        source: root.theme.backgroundSourceFor(root.scene)
        visible: status === Image.Ready
        opacity: root.scene.backgroundOpacity * (1 - root.scene.backgroundBlur * 0.35)

        SequentialAnimation on scale {
            running: root.ambientRunning
            loops: Animation.Infinite
            NumberAnimation {
                from: 1.0
                to: 1.0 + root.scene.backgroundMotion * 0.012
                duration: 18000 - root.scene.backgroundMotion * 4000
                easing.type: Easing.InOutSine
            }
            NumberAnimation {
                from: 1.0 + root.scene.backgroundMotion * 0.012
                to: 1.0
                duration: 18000 - root.scene.backgroundMotion * 4000
                easing.type: Easing.InOutSine
            }
        }
        onVisibleChanged: if (!visible) scale = 1.0
    }

    FastBlur {
        objectName: "editorBackgroundBlur"
        anchors.fill: backgroundImage
        source: backgroundImage
        radius: root.scene.backgroundBlur * 18
        transparentBorder: true
        visible: backgroundImage.visible && root.scene.backgroundBlur > 0.01
        opacity: root.scene.backgroundBlur * 0.45
    }

    Rectangle {
        id: glowSource
        objectName: "editorBackgroundGlowSource"
        width: parent.width * 0.18
        height: parent.height * 0.16
        anchors.right: parent.right
        anchors.top: parent.top
        radius: width / 2
        color: root.theme.sceneGlow
        opacity: root.scene.backgroundGlow * 0.012
    }

    FastBlur {
        objectName: "editorBackgroundGlow"
        anchors.fill: glowSource
        source: glowSource
        radius: 36
        transparentBorder: true
        visible: root.scene.backgroundGlow > 0.01
        opacity: root.scene.backgroundGlow > 0.01 ? 0.35 : 0
    }

    Rectangle {
        objectName: "editorBackgroundOverlay"
        anchors.fill: parent
        color: Qt.rgba(
            root.theme.sceneTint.r,
            root.theme.sceneTint.g,
            root.theme.sceneTint.b,
            Math.max(0.24, Math.min(0.86, root.scene.backgroundDarkness * 0.78))
        )
    }
}
