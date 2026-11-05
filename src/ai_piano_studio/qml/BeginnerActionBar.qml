import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A compact, intent-first surface for users who do not want to start with
// ticks, velocities, or tool names. It only emits text; the Assistant remains
// the owner of provider calls, permissions, and proposal isolation.
Item {
    id: root
    objectName: "beginnerActionBar"

    required property var theme
    property bool compact: width < 760
    property bool sectionStateReady: false
    Component.onCompleted: sectionStateReady = true
    property bool expanded: typeof experience !== "undefined" && experience !== null
        ? experience.inspectorSectionExpanded("beginnerActions", true) : true

    signal actionRequested(string actionId, string prompt)

    onExpandedChanged: {
        if (sectionStateReady && typeof experience !== "undefined" && experience !== null)
            experience.setInspectorSectionExpanded("beginnerActions", expanded)
    }

    readonly property var actions: [
        { id: "continue", label: "续写这段", prompt: "续写当前选中的这段，保持主题和节奏感觉，先给我建议。" },
        { id: "improve_melody", label: "优化旋律", prompt: "优化当前旋律，让它更自然、更好听，先给我建议。" },
        { id: "add_chords", label: "添加和弦", prompt: "为当前旋律添加合适的和弦，先给我建议。" },
        { id: "add_bass", label: "添加低音", prompt: "为当前旋律添加稳妥的低音支撑，先给我建议。" },
        { id: "add_variation", label: "增加变化", prompt: "在保留主题的前提下增加适度变化，先给我建议。" },
        { id: "make_climax", label: "制造高潮", prompt: "在当前目标范围内加入一个有层次的高潮，先给我建议。" },
        { id: "simplify", label: "简化", prompt: "简化当前部分，保留旋律轮廓并减少不必要的密度，先给我建议。" },
        { id: "sadder", label: "更悲伤", prompt: "让当前选中的部分更悲伤，但保留原来的主题，先给我建议。" },
        { id: "warmer", label: "更温暖", prompt: "让当前选中的部分更温暖、更柔和，先给我建议。" },
        { id: "dreamy", label: "更梦幻", prompt: "让当前选中的部分更梦幻，但保持清晰的主题和节拍，先给我建议。" }
    ]

    readonly property int expandedHeight: theme.controlHeight + theme.space1 * 2
    // Collapsed is a zero-height layout state. The arrow is intentionally
    // rendered just above the rail by Main.qml, so reopening never costs a
    // permanent row of canvas space.
    readonly property int collapsedHeight: 0
    implicitHeight: root.expanded ? root.expandedHeight : root.collapsedHeight

    Rectangle {
        objectName: "beginnerActionBarBackground"
        anchors.fill: parent
        color: theme.surface
        visible: root.expanded
    }

    RowLayout {
        id: expandedContent
        anchors.left: parent.left
        anchors.right: beginnerActionBarToggle.left
        anchors.leftMargin: theme.space1
        anchors.rightMargin: theme.space1
        height: theme.controlHeight
        visible: root.expanded
        spacing: theme.space2

        RowLayout {
            id: headerRow
            Layout.preferredWidth: 68
            Layout.minimumWidth: 68
            Layout.maximumWidth: 68
            Layout.preferredHeight: theme.controlHeight
            spacing: theme.space2

            Label {
                text: "快速开始"
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                font.weight: theme.weightSemibold
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
            }
        }

        Flickable {
            id: actionFlow
            objectName: "beginnerActionFlow"
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            Layout.minimumWidth: 0
            Layout.preferredHeight: root.expanded ? theme.controlHeight : 0
            Layout.minimumHeight: 0
            Layout.maximumHeight: root.expanded ? 10000 : 0
            height: root.expanded ? theme.controlHeight : 0
            visible: root.expanded
            clip: true
            contentWidth: actionRow.implicitWidth
            contentHeight: height
            flickableDirection: Flickable.HorizontalFlick
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentWidth > width

            Row {
                id: actionRow
                height: parent.height
                spacing: theme.space1

                Repeater {
                    model: root.actions
                    delegate: AppButton {
                        required property var modelData
                        theme: root.theme
                        text: modelData.label
                        variant: "secondary"
                        implicitHeight: 30
                        accessibleName: "AI 快速操作：" + modelData.label
                        onClicked: root.actionRequested(modelData.id, modelData.prompt)
                    }
                }
            }
        }
    }

    AppButton {
        id: beginnerActionBarToggle
        objectName: "beginnerActionBarToggle"
        theme: root.theme
        variant: "ghost"
        text: root.expanded ? "⌃" : "⌄"
        implicitWidth: root.expanded ? 36 : 32
        implicitHeight: 30
        visible: root.expanded
        accessibleName: root.expanded ? "收起快速开始" : "展开快速开始"
        Accessible.description: root.expanded
            ? "隐藏快速开始操作，只保留展开箭头"
            : "展开快速开始操作"
        anchors.right: parent.right
        anchors.rightMargin: root.expanded ? root.theme.space1 : 0
        y: root.expanded ? Math.max(0, (parent.height - height) / 2) : -height
        onClicked: root.expanded = !root.expanded
    }
}
