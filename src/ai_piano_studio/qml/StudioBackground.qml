import QtQuick
import QtQuick.Window

Item {
    id: root
    required property Theme theme
    required property var settings
    property string target: "home"
    property bool active: true
    property bool motionActive: true
    // Text-only consumers may request protection. The editor and home protect
    // their labels locally, so the image itself can reach full visibility.
    property bool protectText: true
    readonly property var values: settings ? settings.values : ({})
    readonly property string sourceName: values[target + "Source"] || "builtin"
    readonly property string imageSource: sourceName.indexOf("skin:") === 0
        ? settings.backgroundUrl(sourceName) : sourceName
    readonly property bool customSource: sourceName !== "builtin" && sourceName !== "none"
    readonly property bool animatedSource: /\.gif$/i.test(sourceName)
    readonly property bool renderActive: active && visible
        && (!Window.window || (Window.window.visible && Window.window.visibility !== Window.Minimized))
    readonly property real imageOpacity: Number(values[target + "Opacity"] || 0)
    readonly property real focusX: Number(values[target + "FocusX"])
    readonly property real focusY: Number(values[target + "FocusY"])
    readonly property string fit: values[target + "Fit"] || "cover"
    // Against worst-case white/black pixels these floors keep all three normal
    // text roles above 4.5:1, including the weaker textMuted token. Built-in
    // geometry is already bounded and keeps its original low-cost appearance.
    readonly property real readabilityFloor: customSource && protectText ? (theme.paper ? 0.93 : 0.82) : 0
    readonly property real effectiveDim: readabilityFloor
        + (1 - readabilityFloor) * Number(values[target + "Dim"] || 0)
    clip: true

    Rectangle {
        objectName: "studioBackgroundFloor"
        anchors.fill: parent
        color: Qt.rgba(root.theme.appBackground.r, root.theme.appBackground.g, root.theme.appBackground.b, 1)
    }

    Item {
        anchors.fill: parent
        opacity: root.imageOpacity
        visible: root.renderActive && root.sourceName === "builtin"
        // Static geometry has no idle clocks, blur pass, or image decoding.
        Repeater {
            model: 3
            Rectangle {
                required property int index
                x: parent.width * (0.44 + index * 0.15)
                y: -parent.height * 0.35
                width: Math.max(80, parent.width * 0.14)
                height: parent.height * 1.8
                rotation: 26
                radius: width / 2
                gradient: Gradient {
                    GradientStop { position: 0; color: "transparent" }
                    GradientStop { position: 0.5; color: root.theme.accentPrimary }
                    GradientStop { position: 1; color: "transparent" }
                }
                opacity: index === 1 ? 0.24 : 0.09
            }
        }
    }

    Loader {
        id: imageLoader
        objectName: "studioBackgroundLoader"
        anchors.fill: parent
        active: root.renderActive && root.customSource && root.imageOpacity > 0
        opacity: root.imageOpacity
        sourceComponent: root.animatedSource && root.motionActive && !root.values.reducedMotion ? animated : still
    }

    Component {
        id: still
        Item {
            objectName: "studioBackgroundStill"
            Image {
                id: staticImage
                objectName: "studioBackgroundImage"
                readonly property real aspect: implicitHeight > 0 ? implicitWidth / implicitHeight : 1
                width: root.fit === "cover" ? Math.max(parent.width, parent.height * aspect)
                                            : Math.min(parent.width, parent.height * aspect)
                height: width / aspect
                x: (parent.width - width) * root.focusX
                y: (parent.height - height) * root.focusY
                source: root.animatedSource ? root.imageSource.replace(/\.gif$/i, ".png") : root.imageSource
                asynchronous: true
                cache: false
                sourceSize.width: 1920
                sourceSize.height: 1080
                fillMode: Image.PreserveAspectFit
            }
        }
    }

    Component {
        id: animated
        Item {
            objectName: "studioBackgroundMotion"
            AnimatedImage {
                id: animatedImage
                objectName: "studioBackgroundAnimation"
                readonly property real aspect: implicitHeight > 0 ? implicitWidth / implicitHeight : 1
                width: root.fit === "cover" ? Math.max(parent.width, parent.height * aspect)
                                            : Math.min(parent.width, parent.height * aspect)
                height: width / aspect
                x: (parent.width - width) * root.focusX
                y: (parent.height - height) * root.focusY
                source: root.sourceName
                asynchronous: true
                cache: false
                playing: root.renderActive && !root.values.reducedMotion
                speed: root.settings.animationSpeed(root.sourceName, Number(root.values.motionFps))
                fillMode: Image.PreserveAspectFit
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: root.theme.appBackground
        opacity: root.effectiveDim
    }
}
