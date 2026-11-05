import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt5Compat.GraphicalEffects

Dialog {
    id: dialog
    objectName: "backgroundSettingsDialog"

    required property Theme theme
    required property var scene
    signal uploadRequested()

    parent: Overlay.overlay
    width: Math.min(880, Math.max(720, parent ? parent.width - 64 : 880))
    height: Math.min(620, Math.max(540, parent ? parent.height - 64 : 620))
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    padding: theme.space5
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape

    background: Rectangle {
        color: dialog.theme.elevatedSurface
        border.width: 1
        border.color: dialog.theme.borderStrong
        radius: dialog.theme.radiusL
    }

    header: Item {
        implicitHeight: 72

        Column {
            anchors.left: parent.left
            anchors.leftMargin: dialog.theme.space5
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3

            Label {
                text: "背景设置"
                color: dialog.theme.textPrimary
                font.family: dialog.theme.fontUi
                font.pixelSize: dialog.theme.typePanelTitle
                font.weight: dialog.theme.weightBold
            }
            Label {
                text: "让背景衬托音符，同时保持网格与节拍清晰。"
                color: dialog.theme.textSecondary
                font.family: dialog.theme.fontUi
                font.pixelSize: dialog.theme.typeSmall
            }
        }

        AppIconButton {
            anchors.right: parent.right
            anchors.rightMargin: dialog.theme.space4
            anchors.verticalCenter: parent.verticalCenter
            theme: dialog.theme
            iconSource: "icons/x.svg"
            tooltip: "关闭"
            accessibleName: tooltip
            onClicked: dialog.close()
        }
    }

    contentItem: RowLayout {
        spacing: dialog.theme.space5

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 1.15
            spacing: dialog.theme.space3

            Rectangle {
                id: preview
                objectName: "backgroundSettingsPreview"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 320
                radius: dialog.theme.radiusL
                color: dialog.theme.pianoRollBackground
                border.width: 1
                border.color: dialog.theme.borderStrong
                clip: true

                Image {
                    id: previewImage
                    anchors.fill: parent
                    source: dialog.theme.backgroundSourceFor(dialog.scene)
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    visible: dialog.scene.backgroundEnabled && source.toString().length > 0
                    opacity: dialog.scene.backgroundOpacity
                        * (1 - dialog.scene.backgroundBlur * 0.55)

                    SequentialAnimation on scale {
                        running: previewImage.visible && dialog.scene.dynamicEnabled
                            && dialog.scene.motionLevel !== "off"
                        loops: Animation.Infinite
                        NumberAnimation {
                            from: 1
                            to: 1.012
                            duration: dialog.scene.motionLevel === "high" ? 3800
                                : (dialog.scene.motionLevel === "low" ? 7800 : 5600)
                            easing.type: Easing.InOutSine
                        }
                        NumberAnimation {
                            from: 1.012
                            to: 1
                            duration: dialog.scene.motionLevel === "high" ? 3800
                                : (dialog.scene.motionLevel === "low" ? 7800 : 5600)
                            easing.type: Easing.InOutSine
                        }
                    }
                }

                FastBlur {
                    anchors.fill: previewImage
                    source: previewImage
                    radius: dialog.scene.backgroundBlur * 18
                    transparentBorder: true
                    visible: previewImage.visible && dialog.scene.backgroundBlur > 0.01
                    opacity: Math.min(0.75, dialog.scene.backgroundBlur)
                }

                Rectangle {
                    anchors.fill: parent
                    color: Qt.rgba(0.01, 0.04, 0.09,
                        Math.max(0.18, dialog.scene.backgroundDarkness * 0.78))
                }

                Repeater {
                    model: 10
                    Rectangle {
                        required property int index
                        x: preview.width * index / 10
                        y: 0
                        width: 1
                        height: preview.height
                        color: Qt.rgba(0.25, 0.55, 0.72, 0.18)
                    }
                }
                Repeater {
                    model: 7
                    Rectangle {
                        required property int index
                        x: 0
                        y: preview.height * index / 7
                        width: preview.width
                        height: 1
                        color: Qt.rgba(0.25, 0.55, 0.72, 0.14)
                    }
                }
                Repeater {
                    model: 8
                    Rectangle {
                        required property int index
                        x: 22 + (index * 53) % Math.max(64, preview.width - 80)
                        y: 54 + (index * 43) % Math.max(70, preview.height - 110)
                        width: index % 3 === 0 ? 64 : 30
                        height: 10
                        radius: 3
                        color: dialog.theme.accentPrimary
                        opacity: 0.92
                    }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 38
                    color: Qt.rgba(0.02, 0.08, 0.14, 0.86)

                    Label {
                        anchors.left: parent.left
                        anchors.leftMargin: dialog.theme.space3
                        anchors.verticalCenter: parent.verticalCenter
                        text: dialog.scene.customBackgroundPath.length > 0
                            ? "自定义背景" : (dialog.scene.backgroundPreset === "clean"
                                ? "纯净" : (dialog.scene.backgroundPreset === "rhythm"
                                    ? "律动" : "舞台"))
                        color: dialog.theme.textPrimary
                        font.family: dialog.theme.fontUi
                        font.pixelSize: dialog.theme.typeSmall
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                text: "预览会同步当前参数；设置会立即保存。"
                color: dialog.theme.textMuted
                font.family: dialog.theme.fontUi
                font.pixelSize: dialog.theme.typeCaption
            }
        }

        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 1
            color: dialog.theme.borderSubtle
        }

        ScrollView {
            Layout.fillHeight: true
            Layout.preferredWidth: 320
            clip: true
            contentWidth: availableWidth
            ScrollBar.vertical: SafeScrollBar { theme: dialog.theme }

            ColumnLayout {
                width: 304
                spacing: dialog.theme.space3

                SwitchToggle {
                    id: backgroundEnabledToggle
                    objectName: "backgroundEnabledToggle"
                    Layout.fillWidth: true
                    theme: dialog.theme
                    text: "显示编辑器背景"
                    checked: dialog.scene.backgroundEnabled
                    onToggled: value => dialog.scene.setBackgroundEnabled(value)
                }

                SwitchToggle {
                    id: nocturneVisualizerToggle
                    objectName: "nocturneVisualizerToggle"
                    Layout.fillWidth: true
                    visible: dialog.theme.nocturne
                    enabled: visible
                    theme: dialog.theme
                    text: "音乐律动"
                    checked: dialog.scene.visualizerEnabled
                    Accessible.name: "音乐律动"
                    Accessible.description: "以实际播放声音驱动底部的轻微律动；可独立于背景关闭"
                    onToggled: value => dialog.scene.setVisualizerEnabled(value)
                }

                Label {
                    objectName: "backgroundSection"
                    text: "风格"
                    color: dialog.theme.textPrimary
                    font.family: dialog.theme.fontUi
                    font.pixelSize: dialog.theme.typePanelTitle
                    font.weight: dialog.theme.weightSemibold
                }

                SegmentedControl {
                    id: backgroundPresetPicker
                    objectName: "backgroundPresetPicker"
                    Layout.fillWidth: true
                    theme: dialog.theme
                    model: ["纯净", "律动", "舞台"]
                    currentIndex: dialog.scene.backgroundPreset === "clean" ? 0
                        : (dialog.scene.backgroundPreset === "rhythm" ? 1 : 2)
                    Accessible.name: "背景预设"
                    onActivated: index => dialog.scene.setBackgroundPreset(
                        ["clean", "rhythm", "stage"][index])
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: dialog.theme.space2
                    AppButton {
                        objectName: "customBackgroundUploadButton"
                        Layout.fillWidth: true
                        theme: dialog.theme
                        text: dialog.scene.customBackgroundPath.length > 0
                            ? "更换图片" : "上传图片"
                        variant: "secondary"
                        accessibleName: text
                        onClicked: dialog.uploadRequested()
                    }
                    AppButton {
                        objectName: "clearCustomBackgroundButton"
                        theme: dialog.theme
                        text: "清除"
                        variant: "ghost"
                        visible: dialog.scene.customBackgroundPath.length > 0
                        enabled: visible
                        onClicked: dialog.scene.clearCustomBackground()
                    }
                }

                Label {
                    text: "画面"
                    color: dialog.theme.textPrimary
                    font.family: dialog.theme.fontUi
                    font.pixelSize: dialog.theme.typePanelTitle
                    font.weight: dialog.theme.weightSemibold
                    Layout.topMargin: dialog.theme.space2
                }

                component ParameterSlider: RowLayout {
                    required property string label
                    required property real currentValue
                    required property string accessibleName
                    signal committed(real value)
                    signal valueCommitted(real value)
                    readonly property real value: parameterControl.value
                    onValueCommitted: value => committed(value)
                    Layout.fillWidth: true
                    spacing: dialog.theme.space2

                    Label {
                        text: parent.label
                        color: dialog.theme.textSecondary
                        Layout.preferredWidth: 44
                    }
                    StyledSlider {
                        id: parameterControl
                        Layout.fillWidth: true
                        theme: dialog.theme
                        from: 0
                        to: 1
                        stepSize: 0.01
                        modelValue: parent.currentValue
                        accessibleName: parent.accessibleName
                        onValueCommitted: value => parent.committed(value)
                    }
                    Label {
                        text: Math.round(parent.currentValue * 100) + "%"
                        color: dialog.theme.textMuted
                        font.family: dialog.theme.numberFont
                        Layout.preferredWidth: 38
                        horizontalAlignment: Text.AlignRight
                    }
                }

                ParameterSlider {
                    objectName: "backgroundOpacitySlider"
                    label: "强度"
                    currentValue: dialog.scene.backgroundOpacity
                    accessibleName: "背景强度"
                    onCommitted: value => dialog.scene.setBackgroundOpacity(value)
                }
                ParameterSlider {
                    objectName: "backgroundDarknessSlider"
                    label: "压暗"
                    currentValue: dialog.scene.backgroundDarkness
                    accessibleName: "背景压暗"
                    onCommitted: value => dialog.scene.setBackgroundDarkness(value)
                }
                ParameterSlider {
                    objectName: "backgroundBlurSlider"
                    label: "柔化"
                    currentValue: dialog.scene.backgroundBlur
                    accessibleName: "背景柔化"
                    onCommitted: value => dialog.scene.setBackgroundBlur(value)
                }

                Label {
                    text: "动态"
                    color: dialog.theme.textPrimary
                    font.family: dialog.theme.fontUi
                    font.pixelSize: dialog.theme.typePanelTitle
                    font.weight: dialog.theme.weightSemibold
                    Layout.topMargin: dialog.theme.space2
                }

                SegmentedControl {
                    id: backgroundMotionPicker
                    objectName: "backgroundMotionPicker"
                    Layout.fillWidth: true
                    theme: dialog.theme
                    model: ["关闭", "轻微", "适中", "明显"]
                    currentIndex: !dialog.scene.dynamicEnabled ? 0
                        : (dialog.scene.motionLevel === "low" ? 1
                            : (dialog.scene.motionLevel === "high" ? 3 : 2))
                    Accessible.name: "背景动态"
                    onActivated: index => {
                        dialog.scene.setDynamicEnabled(index > 0)
                        if (index > 0)
                            dialog.scene.setMotionLevel(["off", "low", "medium", "high"][index])
                    }
                }
            }
        }
    }

    footer: RowLayout {
        implicitHeight: 64
        spacing: dialog.theme.space2

        AppButton {
            Layout.leftMargin: dialog.theme.space5
            theme: dialog.theme
            text: "恢复默认"
            variant: "ghost"
            onClicked: {
                dialog.scene.setBackgroundEnabled(true)
                dialog.scene.setBackgroundPreset("stage")
                dialog.scene.setDynamicEnabled(true)
                dialog.scene.setMotionLevel("medium")
            }
        }
        Item { Layout.fillWidth: true }
        AppButton {
            Layout.rightMargin: dialog.theme.space5
            theme: dialog.theme
            text: "完成"
            variant: "primary"
            onClicked: dialog.close()
        }
    }
}
