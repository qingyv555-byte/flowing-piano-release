import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

ColumnLayout {
    id: picker
    required property var theme
    property color selectedColor: "#53a79b"
    property real hue: 0.47
    property bool advanced: false
    spacing: 12
    implicitWidth: 304
    onSelectedColorChanged: {
        if (selectedColor.hsvSaturation > 0.001 && selectedColor.hsvHue >= 0)
            hue = selectedColor.hsvHue
    }
    function choose(saturation, value) {
        selectedColor = Qt.hsva(hue, Math.max(0, Math.min(1, saturation)),
            Math.max(0, Math.min(1, value)), 1)
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Repeater {
            model: [picker.theme.layerMelody, picker.theme.layerAccompaniment,
                picker.theme.layerBass, picker.theme.layerDrums, picker.theme.layerOther,
                "#d56b78", "#8975b6", "#3b4654"]
            delegate: Basic.Button {
                required property color modelData
                required property int index
                objectName: "colorSwatch-" + index
                Layout.fillWidth: true
                implicitHeight: 28
                Accessible.name: "选择颜色 " + modelData
                background: Rectangle {
                    color: modelData
                    radius: 6
                    border.width: parent.activeFocus || parent.hovered ? 2 : 1
                    border.color: picker.theme.borderStrong
                }
                onClicked: picker.selectedColor = modelData
            }
        }
    }
    Rectangle {
        id: spectrum
        objectName: "colorSpectrum"
        Layout.fillWidth: true
        implicitHeight: 164
        color: Qt.hsva(picker.hue, 1, 1, 1)
        activeFocusOnTab: true
        Accessible.role: Accessible.Slider
        Accessible.name: "饱和度与明度；左右调整饱和度，上下调整明度"
        Keys.onLeftPressed: picker.choose(picker.selectedColor.hsvSaturation - 0.02, picker.selectedColor.hsvValue)
        Keys.onRightPressed: picker.choose(picker.selectedColor.hsvSaturation + 0.02, picker.selectedColor.hsvValue)
        Keys.onUpPressed: picker.choose(picker.selectedColor.hsvSaturation, picker.selectedColor.hsvValue + 0.02)
        Keys.onDownPressed: picker.choose(picker.selectedColor.hsvSaturation, picker.selectedColor.hsvValue - 0.02)
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "white" }
                GradientStop { position: 1; color: "transparent" }
            }
        }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: "transparent" }
                GradientStop { position: 1; color: "black" }
            }
        }
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: spectrum.activeFocus ? picker.theme.focusRing : picker.theme.borderStrong
            border.width: spectrum.activeFocus ? 2 : 1
        }
        Rectangle {
            x: Math.max(0, Math.min(parent.width - width, picker.selectedColor.hsvSaturation * parent.width - width / 2))
            y: Math.max(0, Math.min(parent.height - height, (1 - picker.selectedColor.hsvValue) * parent.height - height / 2))
            width: 14; height: 14; radius: 7
            color: "transparent"
            border.width: 2
            border.color: picker.theme.textOnColor(picker.selectedColor)
        }
        MouseArea {
            anchors.fill: parent
            function pick(mouse) {
                spectrum.forceActiveFocus()
                picker.choose(mouse.x / width, 1 - mouse.y / height)
            }
            onPressed: mouse => pick(mouse)
            onPositionChanged: mouse => { if (pressed) pick(mouse) }
        }
    }
    Basic.Slider {
        id: hueSlider
        objectName: "colorHueSlider"
        Layout.fillWidth: true
        implicitHeight: 28
        from: 0; to: 1; stepSize: 0.005
        value: picker.hue
        Accessible.name: "色相"
        onMoved: {
            picker.hue = value
            picker.choose(picker.selectedColor.hsvSaturation, picker.selectedColor.hsvValue)
        }
        background: Rectangle {
            x: hueSlider.leftPadding
            y: (hueSlider.height - height) / 2
            width: hueSlider.availableWidth
            height: 12; radius: 6
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "#ff0000" }
                GradientStop { position: 1/6; color: "#ffff00" }
                GradientStop { position: 2/6; color: "#00ff00" }
                GradientStop { position: 3/6; color: "#00ffff" }
                GradientStop { position: 4/6; color: "#0000ff" }
                GradientStop { position: 5/6; color: "#ff00ff" }
                GradientStop { position: 1; color: "#ff0000" }
            }
        }
        handle: Rectangle {
            x: hueSlider.leftPadding + hueSlider.visualPosition * (hueSlider.availableWidth - width)
            y: (hueSlider.height - height) / 2
            width: 16; height: 22; radius: 5
            color: picker.theme.elevatedSurface
            border.width: hueSlider.activeFocus ? 2 : 1
            border.color: picker.theme.textPrimary
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Rectangle {
            Layout.preferredWidth: 72; implicitHeight: 32
            color: picker.selectedColor; radius: 6
            border.color: picker.theme.borderStrong
            Text {
                anchors.centerIn: parent
                text: "预览"
                color: picker.theme.textOnColor(picker.selectedColor)
                font.family: picker.theme.fontUi; font.pixelSize: 12
            }
        }
        Item { Layout.fillWidth: true }
        AppButton {
            objectName: "colorAdvancedToggle"
            theme: picker.theme
            variant: "ghost"
            text: picker.advanced ? "收起色值" : "精确色值"
            labelPixelSize: 12; labelWeight: picker.theme.weightRegular
            onClicked: picker.advanced = !picker.advanced
        }
    }
    AppField {
        objectName: "colorHexField"
        theme: picker.theme
        Layout.fillWidth: true
        visible: picker.advanced
        label: "十六进制色值（可选）"
        text: picker.selectedColor.toString()
        validator: RegularExpressionValidator { regularExpression: /^#[0-9A-Fa-f]{6}$/ }
        onEditingFinished: {
            if (/^#[0-9A-Fa-f]{6}$/.test(text.trim()))
                picker.selectedColor = text.trim()
            text = Qt.binding(function() { return picker.selectedColor.toString() })
        }
    }
}
