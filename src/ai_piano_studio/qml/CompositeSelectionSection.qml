import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var theme
    required property var editorBridge
    required property var experienceBridge
    required property var summary
    required property real secondsPerBeat
    property bool sectionStateReady: false
    property bool overallMode: true

    readonly property bool hasGroup: summary !== null
        && summary !== undefined
        && summary.groupId !== undefined
        && Number(summary.count) > 1
    readonly property var childNoteIds: hasGroup
        ? (summary.childNoteIds || []) : []

    visible: hasGroup
    implicitHeight: visible ? compositeSection.implicitHeight : 0

    function beatText(seconds) {
        return (Number(seconds) / root.secondsPerBeat + 1).toFixed(2)
    }

    function rangeText() {
        if (!root.hasGroup)
            return ""
        return root.beatText(root.summary.startTime) + "–"
            + root.beatText(root.summary.endTime) + " 拍"
    }

    function pitchText() {
        if (!root.hasGroup)
            return ""
        var low = Number(root.summary.pitchMin)
        var high = Number(root.summary.pitchMax)
        if (!Number.isFinite(low) || !Number.isFinite(high))
            return "音高 —"
        return low === high ? "MIDI " + low : "MIDI " + low + "–" + high
    }

    function segmentText(noteId) {
        var details = root.editorBridge.noteDetails(String(noteId))
        if (!details || details.id === undefined)
            return String(noteId)
        return String(details.noteName || details.id) + "  ·  "
            + root.beatText(details.startTime) + " 拍  ·  "
            + Number(details.velocity).toFixed(0)
    }

    function apply(operation, value) {
        if (!root.hasGroup || root.childNoteIds.length < 2)
            return
        root.editorBridge.applyCompositeOperation(
            root.childNoteIds, operation, value)
    }

    PropertySection {
        id: compositeSection
        objectName: "compositeSelectionSummaryCard"
        width: parent.width
        theme: root.theme
        title: "复合音符"
        subtitle: root.summary.count + " 个触发段"
        badge: String(root.summary.count)
        expanded: root.experienceBridge.inspectorSectionExpanded(
            "composite", true)
        onExpandedChanged: {
            if (root.sectionStateReady)
                root.experienceBridge.setInspectorSectionExpanded(
                    "composite", expanded)
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: root.theme.space2

            SegmentedControl {
                id: compositeModeSwitch
                objectName: "compositeModeSwitch"
                theme: root.theme
                model: ["整体", "分段"]
                currentIndex: root.overallMode ? 0 : 1
                Layout.fillWidth: true
                onActivated: index => root.overallMode = index === 0
                Accessible.name: "复合音符编辑范围"
            }

            GridLayout {
                visible: root.overallMode
                columns: 2
                columnSpacing: root.theme.space2
                rowSpacing: root.theme.space1
                Layout.fillWidth: true

                Label {
                    text: "范围"
                    color: root.theme.textMuted
                    font.pixelSize: root.theme.typeSmall
                }
                Label {
                    objectName: "compositeRangeLabel"
                    text: root.rangeText()
                    color: root.theme.textSecondary
                    font.family: root.theme.numberFont
                    font.pixelSize: root.theme.typeSmall
                    horizontalAlignment: Text.AlignRight
                    Layout.fillWidth: true
                }

                Label {
                    text: "音高"
                    color: root.theme.textMuted
                    font.pixelSize: root.theme.typeSmall
                }
                Label {
                    objectName: "compositePitchLabel"
                    text: root.pitchText()
                    color: root.theme.textSecondary
                    font.family: root.theme.numberFont
                    font.pixelSize: root.theme.typeSmall
                    horizontalAlignment: Text.AlignRight
                    Layout.fillWidth: true
                }
            }

            Label {
                visible: root.overallMode
                text: root.summary.pitchVariant ? "包含相邻音高变化" : "同音高触发段"
                color: root.theme.textMuted
                font.pixelSize: root.theme.typeCaption
                Layout.fillWidth: true
            }

            GridLayout {
                visible: root.overallMode
                columns: 2
                columnSpacing: root.theme.space2
                rowSpacing: root.theme.space1
                Layout.fillWidth: true

                AppButton {
                    objectName: "compositeMoveLeftButton"
                    theme: root.theme
                    text: "移动 ←"
                    accessibleName: "复合音符整体提前一拍"
                    Layout.fillWidth: true
                    onClicked: root.apply("move", {
                        deltaTime: -root.secondsPerBeat,
                        deltaPitch: 0
                    })
                }
                AppButton {
                    objectName: "compositeMoveRightButton"
                    theme: root.theme
                    text: "移动 →"
                    accessibleName: "复合音符整体延后一拍"
                    Layout.fillWidth: true
                    onClicked: root.apply("move", {
                        deltaTime: root.secondsPerBeat,
                        deltaPitch: 0
                    })
                }

                AppButton {
                    objectName: "compositeTransposeDownButton"
                    theme: root.theme
                    text: "降八度"
                    accessibleName: "复合音符整体降低八度"
                    Layout.fillWidth: true
                    onClicked: root.apply("transpose", -12)
                }
                AppButton {
                    objectName: "compositeTransposeUpButton"
                    theme: root.theme
                    text: "升八度"
                    accessibleName: "复合音符整体升高八度"
                    Layout.fillWidth: true
                    onClicked: root.apply("transpose", 12)
                }

                AppButton {
                    objectName: "compositeVelocityDownButton"
                    theme: root.theme
                    text: "力度 −10"
                    accessibleName: "复合音符整体降低力度十"
                    Layout.fillWidth: true
                    onClicked: root.apply("velocity_offset", -10)
                }
                AppButton {
                    objectName: "compositeVelocityUpButton"
                    theme: root.theme
                    text: "力度 +10"
                    accessibleName: "复合音符整体增加力度十"
                    Layout.fillWidth: true
                    onClicked: root.apply("velocity_offset", 10)
                }

                AppButton {
                    objectName: "compositeTimeShiftLeftButton"
                    theme: root.theme
                    text: "提前 ¼拍"
                    accessibleName: "复合音符整体提前四分之一拍"
                    Layout.fillWidth: true
                    onClicked: root.apply("time_shift", -root.secondsPerBeat / 4)
                }
                AppButton {
                    objectName: "compositeTimeShiftRightButton"
                    theme: root.theme
                    text: "延后 ¼拍"
                    accessibleName: "复合音符整体延后四分之一拍"
                    Layout.fillWidth: true
                    onClicked: root.apply("time_shift", root.secondsPerBeat / 4)
                }
            }

            AppButton {
                objectName: "compositeDeleteButton"
                theme: root.theme
                variant: "danger"
                text: "删除整个复合组"
                accessibleName: "删除整个复合音符组"
                visible: root.overallMode
                Layout.fillWidth: true
                onClicked: root.apply("delete", null)
            }

            ColumnLayout {
                objectName: "compositeSegmentList"
                visible: !root.overallMode
                Layout.fillWidth: true
                spacing: root.theme.space1

                Label {
                    text: "点击一段后，在基础参数中单独编辑"
                    color: root.theme.textMuted
                    font.pixelSize: root.theme.typeCaption
                    Layout.fillWidth: true
                }

                Repeater {
                    model: root.childNoteIds
                    delegate: AppButton {
                        required property var modelData
                        objectName: "compositeSegmentButton-" + modelData
                        theme: root.theme
                        text: root.segmentText(modelData)
                        accessibleName: "选择复合音符分段 " + modelData
                        Layout.fillWidth: true
                        onClicked: root.editorBridge.selectNotes([String(modelData)])
                    }
                }
            }
        }
    }
}
