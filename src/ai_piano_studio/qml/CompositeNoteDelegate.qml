import QtQuick
import QtQuick.Controls

Rectangle {
    id: root

    required property string groupId
    required property var childNoteIds
    required property var childDetails
    required property real startTime
    required property real endTime
    required property int representativePitch
    required property int pitchMin
    required property int pitchMax
    required property int triggerCount
    required property bool pitchVariant
    required property bool selected
    required property int selectedChildCount
    required property bool expanded
    required property string layerId
    required property string layerRole
    required property string visualRole
    required property string layerCustomColor
    required property bool layerVisible
    required property bool layerLocked
    required property var editor
    required property var theme
    required property var timeline
    required property var playback

    readonly property real rawX: timeline.keyWidth
        + (startTime - timeline.viewStartSeconds) * timeline.pixelsPerSecond
    readonly property real rawWidth: Math.max(18,
        (endTime - startTime) * timeline.pixelsPerSecond)
    readonly property bool withinPitchViewport: pitchMax <= timeline.highestPitch
        && pitchMin >= timeline.lowestPitch
    readonly property bool withinTimeViewport: rawX + rawWidth > timeline.keyWidth
        && rawX < timeline.width
    readonly property bool selectionAuditionActive: playback.selectionAudition !== null
        && playback.selectionAudition.playing
    readonly property bool sharedDragPreviewApplies: timeline.compositeDragPreviewAppliesTo(
        childNoteIds
    )
    readonly property real groupHeight: Math.max(timeline.rowHeight - 4,
        (pitchMax - pitchMin + 1) * timeline.rowHeight - 4)
    readonly property bool activeLayer: layerId === timeline.activeLayerId
    readonly property bool interactive: activeLayer && !layerLocked

    objectName: "compositeNoteItem-" + groupId
    x: Math.max(timeline.keyWidth, rawX)
    y: (timeline.highestPitch - pitchMax) * timeline.rowHeight + 2
    width: Math.max(0, Math.min(timeline.width, rawX + rawWidth) - x)
    height: groupHeight
    visible: layerVisible && timeline.layerVisibleInCurrentMode(layerId)
        && withinPitchViewport && withinTimeViewport && width > 0
    radius: 4
    color: timeline.characterSkin !== "none" ? "transparent" : timeline.notePresentationColor(
        representativePitch, visualRole, groupPointer.containsMouse,
        layerCustomColor)
    opacity: timeline.layerOpacityInCurrentMode(layerId, visualRole)
        * timeline.editorNotesOpacity
        * (selectionAuditionActive && selectedChildCount === 0
            ? 0.18 : timeline.proposalNotesOpacity(childNoteIds, selected || theme.studio ? 1.0 : 0.86))
    border.width: selected || groupPointer.containsMouse ? 2 : 1
    border.color: selected ? theme.noteSelectedBorder
        : timeline.notePresentationBorder(
            representativePitch, visualRole, layerCustomColor)
    z: 10
    activeFocusOnTab: true
    transform: Translate {
        x: root.sharedDragPreviewApplies
            ? root.timeline.compositeDragPreviewX : groupPointer.dragOffsetX
        y: root.sharedDragPreviewApplies
            ? root.timeline.compositeDragPreviewY : groupPointer.dragOffsetY
    }
    Accessible.name: "复合音符，" + triggerCount + " 个触发，"
        + timeline.pianoKeyLabel(pitchMin) + " 至 " + timeline.pianoKeyLabel(pitchMax)

    Loader {
        anchors.fill: parent
        anchors.margins: root.border.width
        active: root.timeline.characterSkin !== "none" && root.visible
        sourceComponent: CharacterNoteSurface {
            character: root.timeline.characterSkin
            startTime: root.startTime
            duration: Math.max(0, root.endTime - root.startTime)
            position: root.timeline.cursorSeconds
            motionActive: root.timeline.characterMotionActive
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 2
        radius: 2
        color: "transparent"
        border.width: root.selected ? 1 : 0
        border.color: root.theme.noteSelectedGlow
    }

    Text {
        anchors.left: parent.left
        anchors.leftMargin: 4
        anchors.top: parent.top
        anchors.topMargin: 2
        text: root.pitchVariant ? "~" : ""
        color: root.theme.noteLabelColorForPitch(root.representativePitch,
            root.timeline.minimumPitch, root.timeline.maximumPitch)
        font.bold: true
        font.pixelSize: 10
        visible: root.pitchVariant
    }

    Text {
        anchors.right: expandButton.left
        anchors.rightMargin: 3
        anchors.verticalCenter: parent.verticalCenter
        text: root.triggerCount > 1 ? String(root.triggerCount) : ""
        color: root.theme.noteLabelColorForPitch(root.representativePitch,
            root.timeline.minimumPitch, root.timeline.maximumPitch)
        font.bold: true
        font.pixelSize: 9
        visible: root.width >= 30 && root.triggerCount > 1
    }

    MouseArea {
        id: groupPointer
        anchors.fill: parent
        z: -1
        hoverEnabled: true
        cursorShape: root.interactive ? Qt.SizeAllCursor : Qt.ArrowCursor
        property real pressSceneX: 0
        property real pressSceneY: 0
        property real dragOffsetX: 0
        property real dragOffsetY: 0
        property bool gestureStarted: false

        onPressed: mouse => {
            gestureStarted = false
            if (!root.activeLayer) {
                root.editor.setActiveLayer(root.layerId)
                root.timeline.claimEditorFocus()
                return
            }
            if (root.layerLocked)
                return
            var point = groupPointer.mapToItem(null, mouse.x, mouse.y)
            pressSceneX = point.x
            pressSceneY = point.y
            dragOffsetX = 0
            dragOffsetY = 0
            if (!root.selected)
                root.editor.selectNotes(root.childNoteIds)
            root.timeline.beginCompositeDragPreview(
                (root.editor.compositeSelectionSummary || ({})).childNoteIds || []
            )
            root.timeline.gestureFeedbackVisible = true
            root.timeline.claimEditorFocus()
            gestureStarted = true
        }
        onPositionChanged: mouse => {
            if (!gestureStarted || !pressed || (mouse.buttons & Qt.LeftButton) === 0)
                return
            var point = groupPointer.mapToItem(null, mouse.x, mouse.y)
            dragOffsetX = point.x - pressSceneX
            dragOffsetY = point.y - pressSceneY
            root.timeline.updateCompositeDragPreview(dragOffsetX, dragOffsetY)
        }
        onReleased: {
            if (!gestureStarted)
                return
            var dx = dragOffsetX
            var dy = dragOffsetY
            dragOffsetX = 0
            dragOffsetY = 0
            root.editor.moveSelectedByPixels(dx, dy)
            root.timeline.clearCompositeDragPreview()
            root.timeline.gestureFeedbackVisible = false
            gestureStarted = false
        }
        onCanceled: {
            dragOffsetX = 0
            dragOffsetY = 0
            root.timeline.clearCompositeDragPreview()
            root.timeline.gestureFeedbackVisible = false
            gestureStarted = false
        }
    }

    Repeater {
        id: segmentRepeater
        model: root.triggerCount > 1 && root.expanded ? root.childNoteIds : []
        delegate: Item {
            id: segmentItem
            required property var modelData
            required property int index
            readonly property string childId: String(modelData)
            readonly property var detail: root.childDetails[index] || ({})
            readonly property int childPitch: Number(detail.pitch || 0)
            readonly property real childStart: Number(detail.startTime || 0)
            readonly property real childDuration: Math.max(0, Number(detail.duration || 0))
            readonly property bool childValid: detail && detail.id !== undefined
            readonly property bool childSelected: root.editor.compositeSelectionSummary
                && (root.editor.compositeSelectionSummary.childNoteIds || [])
                    .indexOf(childId) >= 0
            readonly property bool childPlaybackActive: childValid
                && root.playback.playing
                && !root.timeline.detachedAuditionActive
                && root.playback.positionSeconds >= childStart
                && root.playback.positionSeconds <= childStart + childDuration

            objectName: "segmentButton-" + childId
            x: Math.max(0, (childStart - root.startTime) * root.timeline.pixelsPerSecond)
            y: Math.max(0, (root.pitchMax - childPitch) * root.timeline.rowHeight + 2)
            width: Math.max(3, childDuration * root.timeline.pixelsPerSecond)
            height: Math.max(4, root.timeline.rowHeight - 4)
            visible: childValid && root.expanded
            z: 2
            transform: Translate {
                x: root.sharedDragPreviewApplies ? 0 : segmentPointer.dragOffsetX
                y: root.sharedDragPreviewApplies ? 0 : segmentPointer.dragOffsetY
            }

            Rectangle {
                anchors.fill: parent
                radius: 2
                color: root.timeline.characterSkin !== "none" ? "transparent"
                    : root.timeline.notePresentationColor(childPitch, root.visualRole,
                        segmentPointer.containsMouse, root.layerCustomColor,
                        Number(segmentItem.detail.velocity || 96))
                opacity: root.selectionAuditionActive && !childSelected ? 0.18 : 1.0
                border.width: childSelected || segmentPointer.containsMouse
                    || childPlaybackActive ? 2 : 1
                border.color: childSelected ? root.theme.noteSelectedBorder
                    : (childPlaybackActive ? root.theme.notePlaybackBorder
                    : root.timeline.notePresentationBorder(childPitch, root.visualRole,
                        root.layerCustomColor))
            }

            Loader {
                anchors.fill: parent
                anchors.margins: 2
                active: root.timeline.characterSkin !== "none" && segmentItem.visible
                opacity: root.selectionAuditionActive && !segmentItem.childSelected ? 0.18 : 1.0
                sourceComponent: CharacterNoteSurface {
                    character: root.timeline.characterSkin
                    startTime: segmentItem.childStart
                    duration: segmentItem.childDuration
                    velocity: Number(segmentItem.detail.velocity || 96)
                    position: root.timeline.cursorSeconds
                    motionActive: root.timeline.characterMotionActive
                }
            }

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 3
                anchors.verticalCenter: parent.verticalCenter
                text: root.timeline.pianoKeyLabel(childPitch)
                color: root.theme.textOnColor(root.timeline.notePresentationColor(
                    childPitch, root.visualRole, false, root.layerCustomColor,
                    Number(segmentItem.detail.velocity || 96)))
                font.pixelSize: 8
                visible: parent.width >= 28
            }

            MouseArea {
                id: segmentPointer
                objectName: "segmentPointer-" + childId
                anchors.fill: parent
                z: 3
                hoverEnabled: true
                cursorShape: root.interactive ? Qt.SizeAllCursor : Qt.ArrowCursor
                property real pressSceneX: 0
                property real pressSceneY: 0
                property real dragOffsetX: 0
                property real dragOffsetY: 0
                property bool gestureStarted: false
                Accessible.name: root.timeline.pianoKeyLabel(childPitch) + " 分段"
                onPressed: mouse => {
                    gestureStarted = false
                    if (!root.activeLayer) {
                        root.editor.setActiveLayer(root.layerId)
                        root.timeline.claimEditorFocus()
                        return
                    }
                    if (root.layerLocked)
                        return
                    var point = segmentPointer.mapToItem(null, mouse.x, mouse.y)
                    pressSceneX = point.x
                    pressSceneY = point.y
                    dragOffsetX = 0
                    dragOffsetY = 0
                    if (!segmentItem.childSelected)
                        root.editor.selectNotes([childId])
                    root.timeline.beginCompositeDragPreview(
                        (root.editor.compositeSelectionSummary || ({})).childNoteIds || []
                    )
                    root.timeline.gestureFeedbackVisible = true
                    root.timeline.claimEditorFocus()
                    gestureStarted = true
                }
                onPositionChanged: mouse => {
                    if (!gestureStarted || !pressed
                            || (mouse.buttons & Qt.LeftButton) === 0)
                        return
                    var point = segmentPointer.mapToItem(null, mouse.x, mouse.y)
                    dragOffsetX = point.x - pressSceneX
                    dragOffsetY = point.y - pressSceneY
                    root.timeline.updateCompositeDragPreview(dragOffsetX, dragOffsetY)
                }
                onReleased: {
                    if (!gestureStarted)
                        return
                    var dx = dragOffsetX
                    var dy = dragOffsetY
                    dragOffsetX = 0
                    dragOffsetY = 0
                    if (Math.abs(dx) < 3 && Math.abs(dy) < 3)
                        root.editor.selectNotes([childId])
                    else
                        root.editor.moveSelectedByPixels(dx, dy)
                    root.timeline.clearCompositeDragPreview()
                    root.timeline.gestureFeedbackVisible = false
                    gestureStarted = false
                }
                onCanceled: {
                    dragOffsetX = 0
                    dragOffsetY = 0
                    root.timeline.clearCompositeDragPreview()
                    root.timeline.gestureFeedbackVisible = false
                    gestureStarted = false
                }
            }

            ToolTip.visible: segmentPointer.containsMouse
            ToolTip.text: root.timeline.pianoKeyLabel(childPitch)
                + " · 起始 " + childStart.toFixed(3) + " 秒"
                + " · 时长 " + childDuration.toFixed(3) + " 秒"
                + " · 力度 " + Number(detail.velocity || 0).toFixed(0)
                + " · 余音 " + root.releaseText(detail)
        }
    }

    Repeater {
        model: root.triggerCount > 1 && !root.expanded ? root.childNoteIds : []
        delegate: Item {
            required property var modelData
            required property int index
            readonly property var detail: root.childDetails[index] || ({})
            readonly property real markerStart: Number(detail.startTime || 0)
            readonly property real markerX: (markerStart - root.startTime)
                * root.timeline.pixelsPerSecond
            readonly property bool markerSelected: root.editor.compositeSelectionSummary
                && (root.editor.compositeSelectionSummary.childNoteIds || [])
                    .indexOf(String(modelData)) >= 0
            visible: !root.expanded && detail && detail.id !== undefined
            x: Math.max(0, markerX)
            y: 0
            width: 2
            height: root.height
            z: 2

            Rectangle {
                anchors.fill: parent
                radius: 1
                color: markerSelected ? root.theme.noteSelectedBorder
                    : root.theme.notePitchBorderColor(root.representativePitch,
                        root.timeline.minimumPitch, root.timeline.maximumPitch)
                opacity: 0.82
            }
        }
    }

    ToolButton {
        id: expandButton
        objectName: "expandButton"
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 2
        anchors.rightMargin: 2
        width: 22
        height: 22
        z: 20
        visible: root.childNoteIds.length > 1
        text: root.expanded ? "−" : "+"
        font.pixelSize: 14
        padding: 0
        background: Rectangle {
            radius: 4
            color: expandButton.hovered ? root.theme.surfaceHover : root.theme.surface
            border.color: root.theme.borderStrong
            border.width: 1
        }
        contentItem: Text {
            text: expandButton.text
            color: root.theme.textPrimary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 14
        }
        Accessible.name: root.expanded ? "收起复合音符" : "展开复合音符"
        ToolTip.visible: hovered
        ToolTip.text: Accessible.name
        onClicked: root.editor.compositeModel.toggle_expanded(root.groupId)
    }

    ToolTip.visible: groupPointer.containsMouse && !expandButton.hovered
    ToolTip.text: "触发 " + root.triggerCount + " 次 · "
        + root.timeline.pianoKeyLabel(root.pitchMin) + "–"
        + root.timeline.pianoKeyLabel(root.pitchMax) + " · "
        + (root.endTime - root.startTime).toFixed(2) + " 秒"

    function releaseText(details) {
        if (!details || details.releaseMode === "natural")
            return "自然"
        if (details.releaseMode === "cut")
            return "切断"
        return Number(details.releaseMs || 0).toFixed(0) + " ms"
    }
}
