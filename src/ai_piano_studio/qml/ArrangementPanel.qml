import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "arrangementPanel"

    required property Theme theme
    required property var editorBridge
    property var model: null
    property var noteItemsByLayer: ({})
    property string activeLayerId: ""
    property string layerViewMode: "ghost"
    property real pixelsPerSecond: 80
    property real contentX: 0
    property real durationSeconds: 0
    property real visibleSeconds: 1
    property real panelHeight: theme.arrangementDefaultHeight
    property real previewedHeight: -1
    property string pendingDeleteLayerId: ""
    property string pendingDeleteLayerName: ""
    property string pendingColorLayerId: ""
    property string draggedLayerId: ""
    property int reorderSourceIndex: -1
    property int reorderTargetIndex: -1
    property real reorderDragOffset: 0
    property real reorderStartSceneY: 0
    readonly property int layerCount: laneList.count
    readonly property int pinnedCount: model ? model.pinnedCount : 0
    readonly property real chromeHeight: 8 + 38 + 24
    readonly property real layerContentHeight: layerCount > 0
        ? layerCount * laneStride - laneList.spacing : 0
    readonly property real heightCap: previewedHeight >= 0
        ? previewedHeight : panelHeight
    readonly property real contentDrivenHeight: chromeHeight + layerContentHeight
    readonly property real effectivePanelHeight: Math.min(
        contentDrivenHeight, boundedHeight(heightCap))
    readonly property real trackHeaderWidth: Math.min(248, Math.max(214, width * 0.2))
    readonly property real overviewDuration: Math.max(
        1, durationSeconds, visibleSeconds)
    readonly property real overviewContentWidth: Math.max(
        1, laneList.width - trackHeaderWidth)
    readonly property real viewStartSeconds: contentX / Math.max(1, pixelsPerSecond)
    readonly property real maximumViewStartSeconds: Math.max(
        0, overviewDuration - visibleSeconds)
    readonly property real laneStride: 35

    signal layerActivated(string layerId)
    signal contentXRequested(real contentX)
    signal heightCommitted(real height)
    signal layerViewModeRequested(string mode)

    function boundedHeight(value: real): real {
        return Math.max(120, Math.min(360, value))
    }

    function previewHeight(value: real): void {
        previewedHeight = boundedHeight(value)
    }

    function commitHeight(value: real): void {
        previewHeight(value)
        commitPreviewHeight()
    }

    function commitPreviewHeight(): void {
        if (previewedHeight < 0)
            return
        var committed = previewedHeight
        previewedHeight = -1
        heightCommitted(committed)
    }

    function requestLayerDelete(layerId, layerName, noteCount): void {
        if (Number(noteCount) <= 0) {
            editorBridge.deleteMusicLayer(layerId)
            return
        }
        pendingDeleteLayerId = layerId
        pendingDeleteLayerName = layerName
        deleteLayerDialog.open()
    }

    function openLayerColorPicker(layerId, currentColor): void {
        pendingColorLayerId = layerId
        layerColorPopup.selectedColor = currentColor || root.theme.layerOther
        layerColorPopup.open()
    }

    function applyPendingLayerColor(colorValue: string): void {
        if (pendingColorLayerId.length === 0)
            return
        editorBridge.setMusicLayerProperty(
            pendingColorLayerId,
            "customColor",
            colorValue.length > 0 ? colorValue : null)
        layerColorPopup.close()
    }

    function reorderShiftFor(index): real {
        if (draggedLayerId.length === 0 || reorderSourceIndex === reorderTargetIndex)
            return 0
        if (reorderSourceIndex < reorderTargetIndex
                && index > reorderSourceIndex && index <= reorderTargetIndex)
            return -laneStride
        if (reorderSourceIndex > reorderTargetIndex
                && index >= reorderTargetIndex && index < reorderSourceIndex)
            return laneStride
        return 0
    }

    function clearReorderPreview(): void {
        draggedLayerId = ""
        reorderSourceIndex = -1
        reorderTargetIndex = -1
        reorderDragOffset = 0
        reorderStartSceneY = 0
    }

    function beginLayerReorder(layerId, sourceIndex, sceneY): void {
        draggedLayerId = layerId
        reorderSourceIndex = sourceIndex
        reorderTargetIndex = sourceIndex
        reorderStartSceneY = sceneY
        reorderDragOffset = 0
    }

    function updateLayerReorder(sceneY, pinned): void {
        if (draggedLayerId.length === 0)
            return
        reorderDragOffset = sceneY - reorderStartSceneY
        var rawIndex = reorderSourceIndex + Math.round(
            reorderDragOffset / root.laneStride)
        var groupMinimum = pinned ? 0 : root.pinnedCount
        var groupMaximum = pinned ? root.pinnedCount - 1 : root.layerCount - 1
        reorderTargetIndex = Math.max(
            groupMinimum, Math.min(groupMaximum, rawIndex))
    }

    function finishLayerReorder(): void {
        var layerId = draggedLayerId
        var targetIndex = reorderTargetIndex
        var sourceIndex = reorderSourceIndex
        clearReorderPreview()
        if (targetIndex >= 0 && targetIndex !== sourceIndex)
            root.editorBridge.moveMusicLayer(layerId, targetIndex)
    }

    function cancelLiveInteraction(): void {
        previewedHeight = -1
        clearReorderPreview()
    }

    Shortcut {
        sequence: "Escape"
        context: Qt.WindowShortcut
        enabled: root.visible && (root.previewedHeight >= 0
            || root.draggedLayerId.length > 0)
        onActivated: root.cancelLiveInteraction()
    }

    color: theme.surface
    border.width: 0
    clip: true

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.width: 1
        border.color: root.theme.borderStrong
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            id: resizeHandle
            objectName: "arrangementResizeHandle"
            Layout.fillWidth: true
            Layout.preferredHeight: 8
            color: resizePointer.containsMouse || resizePointer.pressed
                ? root.theme.layerRoleFill("melody", 0.26)
                : "transparent"

            Rectangle {
                anchors.centerIn: parent
                width: resizePointer.containsMouse || resizePointer.pressed ? 72 : 44
                height: 2
                radius: 1
                color: resizePointer.containsMouse || resizePointer.pressed
                    ? root.theme.accentPrimary : root.theme.borderStrong
                Behavior on width {
                    NumberAnimation { duration: root.theme.motionHover }
                }
            }

            MouseArea {
                id: resizePointer
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.SizeVerCursor
                property real startY: 0
                property real startHeight: 0
                onPressed: mouse => {
                    startY = mapToItem(null, mouse.x, mouse.y).y
                    startHeight = root.effectivePanelHeight
                }
                onPositionChanged: mouse => {
                    if (!pressed)
                        return
                    var currentY = mapToItem(null, mouse.x, mouse.y).y
                    root.previewHeight(startHeight + startY - currentY)
                }
                onReleased: root.commitPreviewHeight()
                onCanceled: root.previewedHeight = -1
                onDoubleClicked: {
                    root.previewHeight(root.theme.arrangementDefaultHeight)
                    root.commitPreviewHeight()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 38
            Layout.leftMargin: root.theme.space3
            Layout.rightMargin: root.theme.space2
            spacing: root.theme.space2

            Label {
                text: "音乐层"
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typePanelTitle
                font.weight: root.theme.weightSemibold
            }
            Label {
                text: root.layerCount + " 层"
                color: root.theme.textMuted
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeCaption
            }
            AppButton {
                objectName: "arrangementAddLayer"
                theme: root.theme
                text: "添加音乐层"
                iconSource: "icons/plus.svg"
                variant: "secondary"
                implicitHeight: 30
                onClicked: root.editorBridge.addMusicLayer("新音乐层", "other")
            }
            Item {
                Layout.fillWidth: true
            }
            SegmentedControl {
                objectName: "arrangementLayerViewMode"
                theme: root.theme
                model: ["淡显其他", "仅当前", "全部"]
                currentIndex: root.layerViewMode === "active" ? 1
                    : root.layerViewMode === "all" ? 2 : 0
                implicitWidth: 184
                implicitHeight: 30
                onActivated: index => root.layerViewModeRequested(
                    index === 1 ? "active" : index === 2 ? "all" : "ghost")
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 24
            color: root.theme.canvasBackground

            Rectangle {
                width: root.trackHeaderWidth
                height: parent.height
                color: root.theme.elevatedSurface
                border.width: 1
                border.color: root.theme.borderSubtle
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: root.theme.space3
                    anchors.verticalCenter: parent.verticalCenter
                    text: "层 / 快捷控制"
                    color: root.theme.textMuted
                    font.pixelSize: root.theme.typeCaption
                }
            }

            Item {
                id: timeRuler
                objectName: "arrangementTimeRuler"
                anchors.left: parent.left
                anchors.leftMargin: root.trackHeaderWidth
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                clip: true

                Repeater {
                    id: rulerTicks
                    model: Math.max(2, Math.ceil(timeRuler.width / 140) + 1)
                    delegate: Item {
                        required property int index
                        x: index * timeRuler.width / Math.max(1, rulerTicks.count - 1)
                        width: 1
                        height: timeRuler.height
                        Rectangle {
                            width: 1
                            height: parent.height
                            color: root.theme.gridLineMajor
                        }
                        Label {
                            anchors.left: parent.left
                            anchors.leftMargin: 5
                            anchors.verticalCenter: parent.verticalCenter
                            text: Math.round(index * root.overviewDuration
                                / Math.max(1, rulerTicks.count - 1)) + "s"
                            color: root.theme.textMuted
                            font.family: root.theme.numberFont
                            font.pixelSize: root.theme.typeCaption
                        }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: mouse => {
                        var requestedSeconds = mouse.x / Math.max(1, width)
                            * root.overviewDuration - root.visibleSeconds / 2
                        root.contentXRequested(Math.max(0, Math.min(
                            root.maximumViewStartSeconds, requestedSeconds))
                            * root.pixelsPerSecond)
                    }
                }
            }
        }

        ListView {
            id: laneList
            objectName: "arrangementLaneList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 1
            model: root.model
            boundsBehavior: Flickable.StopAtBounds

            delegate: Item {
                id: laneDelegate
                required property string layerId
                required property string name
                required property string role
                required property string customColor
                required property bool layerVisible
                required property bool muted
                required property bool solo
                required property bool locked
                required property bool pinned
                required property int order
                required property int noteCount
                readonly property alias rowFocusTarget: layerRow
                width: laneList.width
                height: 34

                readonly property color layerColor: customColor.length > 0
                    ? customColor : root.theme.layerRoleColor(role)

                transform: Translate {
                    y: laneDelegate.layerId === root.draggedLayerId
                        ? root.reorderDragOffset
                        : root.reorderShiftFor(laneDelegate.order)
                    Behavior on y {
                        enabled: laneDelegate.layerId !== root.draggedLayerId
                        NumberAnimation { duration: root.theme.motionHover }
                    }
                }
                z: laneDelegate.layerId === root.draggedLayerId ? 20 : 1

                Row {
                    anchors.fill: parent
                    spacing: 0

                    MusicLayerRow {
                        id: layerRow
                        width: root.trackHeaderWidth
                        height: parent.height
                        implicitHeight: parent.height
                        theme: root.theme
                        layerId: laneDelegate.layerId
                        name: laneDelegate.name
                        role: laneDelegate.role
                        customColor: laneDelegate.customColor
                        active: laneDelegate.layerId === root.activeLayerId
                        muted: laneDelegate.muted
                        solo: laneDelegate.solo
                        layerVisible: laneDelegate.layerVisible
                        locked: laneDelegate.locked
                        pinned: laneDelegate.pinned
                        order: laneDelegate.order
                        layerCount: laneList.count
                        noteCount: laneDelegate.noteCount
                        reorderEnabled: true
                        radius: 0
                        onActivated: layerId => root.layerActivated(layerId)
                        onMuteToggled: (layerId, value) =>
                            root.editorBridge.setMusicLayerProperty(layerId, "muted", value)
                        onSoloToggled: (layerId, value) =>
                            root.editorBridge.setMusicLayerProperty(layerId, "solo", value)
                        onVisibilityToggled: (layerId, value) =>
                            root.editorBridge.setMusicLayerProperty(layerId, "visible", value)
                        onLockToggled: (layerId, value) =>
                            root.editorBridge.setMusicLayerProperty(layerId, "locked", value)
                        onDuplicateRequested: layerId =>
                            root.editorBridge.duplicateMusicLayer(layerId)
                        onPinToggled: (layerId, value) =>
                            root.editorBridge.setMusicLayerPinned(layerId, value)
                        onRenameCommitted: (layerId, name) =>
                            root.editorBridge.renameMusicLayer(layerId, name)
                        onColorRequested: layerId => root.openLayerColorPicker(layerId,
                            laneDelegate.customColor || root.theme.layerRoleColor(laneDelegate.role))
                        onSelectAllRequested: layerId =>
                            root.editorBridge.selectMusicLayer(layerId)
                        onMoveSelectionHereRequested: layerId =>
                            root.editorBridge.moveSelectionToLayer(layerId)
                        onReorderDragStarted: (layerId, sceneY) =>
                            root.beginLayerReorder(
                                layerId, laneDelegate.order, sceneY)
                        onReorderDragMoved: (layerId, sceneY) =>
                            root.updateLayerReorder(sceneY, laneDelegate.pinned)
                        onReorderDragFinished: layerId => root.finishLayerReorder()
                        onReorderDragCanceled: layerId => root.clearReorderPreview()
                        onDeleteRequested: layerId => root.requestLayerDelete(
                            layerId, laneDelegate.name, laneDelegate.noteCount)
                        onMoveFocusRequested: direction => {
                            var nextIndex = Math.max(0, Math.min(
                                laneList.count - 1,
                                laneDelegate.order + direction))
                            laneList.currentIndex = nextIndex
                            var nextItem = laneList.itemAtIndex(nextIndex)
                            if (nextItem && nextItem.rowFocusTarget)
                                nextItem.rowFocusTarget.forceActiveFocus(
                                    Qt.TabFocusReason)
                        }
                    }

                    Rectangle {
                        id: laneContent
                        width: laneDelegate.width - root.trackHeaderWidth
                        height: parent.height
                        color: laneDelegate.layerId === root.activeLayerId
                            ? root.theme.layerRoleFill(laneDelegate.role, 0.14)
                            : root.theme.layerRoleFill(laneDelegate.role, 0.045)
                        opacity: laneDelegate.layerVisible && !laneDelegate.muted ? 1.0 : 0.38
                        border.width: laneDelegate.layerId === root.activeLayerId ? 1 : 0
                        border.color: laneDelegate.layerColor
                        clip: true

                        Canvas {
                            id: phraseCanvas
                            anchors.fill: parent
                            property string laneId: laneDelegate.layerId
                            property color laneColor: laneDelegate.layerColor

                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                ctx.fillStyle = laneColor
                                var source = root.noteItemsByLayer[laneId] || []
                                for (var index = 0; index < source.length; ++index) {
                                    var note = source[index]
                                    if (String(note.status || "") === "deleted")
                                        continue
                                    var x = Number(note.startTime || 0)
                                        / root.overviewDuration * width
                                    var w = Math.max(3, Number(note.duration || 0)
                                        / root.overviewDuration * width)
                                    if (x + w < 0 || x > width)
                                        continue
                                    var pitchBand = Math.abs(Number(note.pitch || 60)) % 4
                                    ctx.globalAlpha = 0.78
                                    ctx.fillRect(x, 12 + pitchBand * 5, w, 3)
                                }
                                ctx.globalAlpha = 1
                            }

                            Connections {
                                target: root
                                function onNoteItemsByLayerChanged() { phraseCanvas.requestPaint() }
                                function onContentXChanged() { phraseCanvas.requestPaint() }
                                function onPixelsPerSecondChanged() { phraseCanvas.requestPaint() }
                            }
                        }

                        MouseArea {
                            objectName: "arrangementLayerHitArea-" + laneDelegate.layerId
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.layerActivated(laneDelegate.layerId)
                        }
                    }
                }

                MouseArea {
                    id: reorderHandle
                    objectName: "arrangementReorderHandle-" + laneDelegate.layerId
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: 10
                    z: 30
                    hoverEnabled: true
                    cursorShape: Qt.SizeVerCursor

                    onPressed: mouse => root.beginLayerReorder(
                        laneDelegate.layerId,
                        laneDelegate.order,
                        mapToItem(null, mouse.x, mouse.y).y)
                    onPositionChanged: mouse => {
                        if (!pressed)
                            return
                        root.updateLayerReorder(
                            mapToItem(null, mouse.x, mouse.y).y,
                            laneDelegate.pinned)
                    }
                    onReleased: root.finishLayerReorder()
                    onCanceled: root.clearReorderPreview()
                }
            }

            ScrollBar.vertical: ScrollBar {
                policy: laneList.contentHeight > laneList.height
                    ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
            }

            Rectangle {
                objectName: "arrangementPinnedSeparator"
                parent: laneList
                visible: root.pinnedCount > 0
                    && root.pinnedCount < root.layerCount
                x: 0
                y: root.pinnedCount * root.laneStride - 1
                width: laneList.width
                height: 1
                color: root.theme.borderStrong
                z: 24
            }

            Rectangle {
                id: overviewViewport
                objectName: "arrangementViewport"
                visible: root.visibleSeconds < root.overviewDuration - 0.1
                parent: laneList
                x: root.trackHeaderWidth + Math.max(0, root.viewStartSeconds)
                    / root.overviewDuration * root.overviewContentWidth
                y: 0
                width: Math.min(
                    root.overviewContentWidth,
                    Math.max(28, root.visibleSeconds / root.overviewDuration
                        * root.overviewContentWidth)
                )
                height: Math.min(laneList.height, laneList.contentHeight)
                color: root.theme.minimapViewportFill
                border.width: 1
                border.color: root.theme.minimapViewportBorder
                z: 10

                MouseArea {
                    id: viewportDrag
                    objectName: "arrangementViewportDragHandle"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: 12
                    hoverEnabled: true
                    cursorShape: Qt.SizeHorCursor
                    property real startSceneX: 0
                    property real startContentX: 0
                    onPressed: mouse => {
                        startSceneX = mapToItem(null, mouse.x, mouse.y).x
                        startContentX = root.contentX
                    }
                    onPositionChanged: mouse => {
                        if (!pressed)
                            return
                        var sceneX = mapToItem(null, mouse.x, mouse.y).x
                        var secondsDelta = (sceneX - startSceneX)
                            / root.overviewContentWidth * root.overviewDuration
                        root.contentXRequested(Math.max(0, Math.min(
                            root.maximumViewStartSeconds * root.pixelsPerSecond,
                            startContentX + secondsDelta * root.pixelsPerSecond)))
                    }
                }
            }

            Rectangle {
                visible: root.draggedLayerId.length > 0
                    && root.reorderTargetIndex >= 0
                x: 0
                y: root.reorderTargetIndex * root.laneStride
                    + (root.reorderTargetIndex > root.reorderSourceIndex
                        ? root.laneStride - 2 : 0)
                width: root.trackHeaderWidth
                height: 2
                color: root.theme.accentPrimary
                z: 40
            }
        }
    }

    Dialog {
        id: deleteLayerDialog
        objectName: "deleteMusicLayerDialog"
        anchors.centerIn: parent
        width: Math.min(420, root.width - 32)
        modal: true
        closePolicy: Popup.CloseOnEscape
        padding: root.theme.space4

        background: Rectangle {
            color: root.theme.popupBackground
            border.width: 1
            border.color: root.theme.danger
            radius: root.theme.radiusL
        }

        contentItem: ColumnLayout {
            spacing: root.theme.space3
            Label {
                Layout.fillWidth: true
                text: "删除“" + root.pendingDeleteLayerName + "”？"
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typePanelTitle
                font.weight: root.theme.weightSemibold
            }
            Label {
                Layout.fillWidth: true
                text: "该层包含音符。默认会保留音符并移到“未分类”。"
                color: root.theme.textSecondary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeSmall
                wrapMode: Text.Wrap
            }
            AppButton {
                objectName: "moveNotesThenDeleteLayerButton"
                theme: root.theme
                text: "移动音符到未分类并删除"
                variant: "primary"
                Layout.fillWidth: true
                onClicked: {
                    root.editorBridge.deleteMusicLayerWithPolicy(
                        root.pendingDeleteLayerId, "move_to_unclassified")
                    deleteLayerDialog.close()
                }
            }
            AppButton {
                objectName: "deleteLayerAndNotesButton"
                theme: root.theme
                text: "同时删除该层音符"
                variant: "danger"
                Layout.fillWidth: true
                onClicked: {
                    root.editorBridge.deleteMusicLayerWithPolicy(
                        root.pendingDeleteLayerId, "delete_notes")
                    deleteLayerDialog.close()
                }
            }
            AppButton {
                theme: root.theme
                text: "取消"
                variant: "ghost"
                Layout.fillWidth: true
                onClicked: deleteLayerDialog.close()
            }
        }
    }

    AppColorDialog {
        id: layerColorPopup
        objectName: "musicLayerColorPopup"
        theme: root.theme
        title: "音乐层颜色"
        allowReset: true
        onAccepted: root.applyPendingLayerColor(selectedColor.toString())
        onResetRequested: root.applyPendingLayerColor("")
    }
}
