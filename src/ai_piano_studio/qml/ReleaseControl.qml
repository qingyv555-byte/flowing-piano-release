import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var theme
    required property var editorBridge
    required property var summary
    property var wheelScrollHandler: null

    objectName: "releaseControl"
    Layout.fillWidth: true
    spacing: root.theme.space1

    function modeIndex() {
        if (!root.summary || root.summary.count <= 0
                || root.summary.releaseModeMixed)
            return 0
        if (root.summary.releaseMode === "custom")
            return 1
        if (root.summary.releaseMode === "cut")
            return 2
        return 0
    }

    Label {
        text: "释放方式 · 音符结束后的尾音"
        color: root.theme.textSecondary
        font.family: root.theme.fontUi
        font.pixelSize: root.theme.typeSmall
        Layout.fillWidth: true
        elide: Text.ElideRight
    }

    SegmentedControl {
        id: releaseModeSwitch
        objectName: "releaseModeSwitch"
        theme: root.theme
        model: ["自然", "自定义", "立即截止"]
        currentIndex: root.modeIndex()
        Layout.fillWidth: true
        Accessible.name: "音符释放方式"
        onActivated: index => {
            if (index === 1)
                root.editorBridge.setSelectedRelease("custom", null)
            else if (index === 2)
                root.editorBridge.setSelectedRelease("cut", null)
            else
                root.editorBridge.setSelectedRelease("natural", null)
        }
    }

    AppField {
        id: releaseMsField
        objectName: "releaseMsField"
        theme: root.theme
        label: "自定义尾音（ms）"
        Layout.fillWidth: true
        visible: root.summary && root.summary.count > 0
            && !root.summary.releaseModeMixed
            && root.summary.releaseMode === "custom"
        wheelScrollEnabled: true
        text: root.summary && root.summary.releaseMs !== null
            && root.summary.releaseMs !== undefined
            ? Number(root.summary.releaseMs).toFixed(0) : ""
        placeholderText: "0 - 5000"
        validator: DoubleValidator { bottom: 0; top: 5000; decimals: 0 }
        onWheelScrollRequested: delta => {
            // The Inspector owns vertical scrolling; the field must not
            // consume a wheel gesture as an accidental value edit.
            if (root.wheelScrollHandler)
                root.wheelScrollHandler(delta)
        }
        onEditingFinished: {
            var parsed = Number(text)
            if (Number.isFinite(parsed))
                root.editorBridge.setSelectedRelease("custom", parsed)
        }
    }
}
