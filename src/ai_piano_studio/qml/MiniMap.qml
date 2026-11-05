import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    objectName: "miniMap"
    required property var theme
    required property real totalSeconds
    required property real viewStartSeconds
    required property real visibleSeconds
    required property real playheadSeconds
    required property bool playheadVisible
    required property int lowestPitch
    required property int highestPitch
    required property int minimumPitch
    required property int maximumPitch
    property bool dockToBottomRight: false
    property string displayMode: experience.miniMapDisplayMode
    property bool focusMode: false
    readonly property string effectiveDisplayMode: focusMode
        && displayMode === "visible" ? "weak" : displayMode

    signal navigateRequested(real seconds, int pitch)

    readonly property real minimumWidth: 160
    readonly property real maximumStoredWidth: 400
    readonly property real minimumHeight: 100
    readonly property real maximumStoredHeight: 300
    readonly property real edgeMargin: 12
    readonly property real availableWidth: parent ? parent.width : maximumStoredWidth
    readonly property real availableHeight: parent ? parent.height : maximumStoredHeight
    readonly property real maximumWidth: Math.max(
        minimumWidth, Math.min(maximumStoredWidth, availableWidth * 0.35)
    )
    readonly property real maximumHeight: Math.max(
        minimumHeight, Math.min(maximumStoredHeight, availableHeight * 0.4)
    )
    readonly property real maximumX: Math.max(edgeMargin, availableWidth - width - edgeMargin)
    readonly property real maximumY: Math.max(edgeMargin, availableHeight - height - edgeMargin)
    readonly property real contentSeconds: Math.max(1, totalSeconds)
    readonly property int pitchSpan: Math.max(1, maximumPitch - minimumPitch + 1)
    property bool resizing: false
    property real draftWidth: experience.miniMapWidth
    property real draftHeight: experience.miniMapHeight
    property real resizeStartWidth: 0
    property real resizeStartHeight: 0
    property real resizeStartX: 0
    property real resizeStartY: 0
    property bool moving: false
    property real draftX: 0
    property real draftY: 0
    property real moveStartX: 0
    property real moveStartY: 0

    visible: effectiveDisplayMode !== "hidden"
    enabled: visible
    opacity: effectiveDisplayMode === "weak" ? 0.58 : 1.0
    width: visible
        ? Math.min(resizing ? draftWidth : experience.miniMapWidth, maximumWidth)
        : 0
    height: visible
        ? Math.min(resizing ? draftHeight : experience.miniMapHeight, maximumHeight)
        : 0
    x: Math.max(edgeMargin, Math.min(
        maximumX, (moving || resizing) ? draftX
            : (dockToBottomRight ? maximumX
                : (experience.miniMapX < 0 ? maximumX : experience.miniMapX))
    ))
    y: Math.max(edgeMargin, Math.min(
        maximumY, (moving || resizing) ? draftY
            : (dockToBottomRight ? maximumY
                : (experience.miniMapY < 0 ? maximumY : experience.miniMapY))
    ))
    z: 40
    radius: theme.radiusM
    color: theme.minimapBackground
    border.width: 1
    border.color: theme.minimapBorder
    clip: false

    Rectangle {
        id: minimapShadow
        objectName: "miniMapShadow"
        anchors.fill: parent
        anchors.margins: -3
        radius: theme.radiusL + 2
        color: theme.minimapShadow
        z: -1
    }

    function clampWidth(value) {
        return Math.max(minimumWidth, Math.min(maximumWidth, value))
    }

    function clampHeight(value) {
        return Math.max(minimumHeight, Math.min(maximumHeight, value))
    }

    function clampedPlayheadSeconds() {
        var value = Number(root.playheadSeconds)
        if (!isFinite(value))
            value = 0
        return Math.max(0, Math.min(root.contentSeconds, value))
    }

    function beginResize() {
        resizeStartX = x
        resizeStartY = y
        resizeStartWidth = width
        resizeStartHeight = height
        draftWidth = width
        draftHeight = height
        draftX = x
        draftY = y
        resizing = true
    }

    function resizeFromDrag(deltaX, deltaY) {
        if (!resizing)
            return
        draftWidth = clampWidth(resizeStartWidth - deltaX)
        if (experience.miniMapAspectLocked)
            draftHeight = clampHeight(draftWidth * 9.0 / 16.0)
        else
            draftHeight = clampHeight(resizeStartHeight - deltaY)
        draftX = Math.max(edgeMargin, Math.min(
            maximumX, resizeStartX + resizeStartWidth - draftWidth
        ))
        draftY = Math.max(edgeMargin, Math.min(
            maximumY, resizeStartY + resizeStartHeight - draftHeight
        ))
    }

    function finishResize() {
        if (!resizing)
            return
        experience.setMiniMapSize(width, height)
        experience.setMiniMapPosition(x, y)
        resizing = false
    }

    function beginMove() {
        moveStartX = x
        moveStartY = y
        draftX = moveStartX
        draftY = moveStartY
        moving = true
    }

    function moveFromDrag(deltaX, deltaY) {
        if (!moving)
            return
        draftX = Math.max(edgeMargin, Math.min(maximumX, moveStartX + deltaX))
        draftY = Math.max(edgeMargin, Math.min(maximumY, moveStartY + deltaY))
    }

    function finishMove() {
        if (!moving)
            return
        experience.setMiniMapPosition(x, y)
        moving = false
    }

    function cancelLiveInteraction() {
        resizing = false
        moving = false
        draftWidth = experience.miniMapWidth
        draftHeight = experience.miniMapHeight
        draftX = x
        draftY = y
    }

    Shortcut {
        sequence: "Escape"
        context: Qt.WindowShortcut
        enabled: root.visible && (root.resizing || root.moving)
        onActivated: root.cancelLiveInteraction()
    }

    function resetSize() {
        resizing = false
        experience.resetMiniMapSize()
    }

    function cycleDisplayMode() {
        var nextMode = displayMode === "visible" ? "weak"
            : (displayMode === "weak" ? "hidden" : "visible")
        experience.setMiniMapDisplayMode(nextMode)
    }

    Rectangle {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.theme.studio ? 18 : 28
        color: theme.minimapInnerBackground

        Text {
            objectName: "miniMapTitle"
            visible: !root.theme.studio
            anchors.left: parent.left
            anchors.leftMargin: 32
            anchors.right: sizeLabel.visible ? sizeLabel.left : root.theme.studio ? settingsButton.left : displayModeButton.left
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            text: "缩略图"
            elide: Text.ElideRight
            color: theme.minimapTitleText
            font.pixelSize: 11
            font.bold: true
        }
        Text {
            id: sizeLabel
            objectName: "miniMapDimensions"
            visible: root.width >= 250 && (!root.theme.studio || root.resizing)
            anchors.right: root.theme.studio ? settingsButton.left : displayModeButton.left
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: Math.round(root.width) + " × " + Math.round(root.height)
            color: theme.minimapMetaText
            font.pixelSize: 10
            font.family: theme.numberFont
        }
        AppButton {
            id: displayModeButton
            objectName: "miniMapDisplayModeButton"
            visible: !root.theme.studio
            anchors.right: settingsButton.left
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            width: 36
            height: 22
            theme: root.theme
            variant: "ghost"
            text: root.displayMode === "visible" ? "显"
                : (root.displayMode === "weak" ? "弱" : "隐")
            accessibleName: root.displayMode === "visible" ? "缩略图：正常"
                : (root.displayMode === "weak" ? "缩略图：弱化" : "缩略图：隐藏")
            ToolTip.visible: hovered
            ToolTip.text: "切换缩略图显示：正常 / 弱化 / 隐藏"
            onClicked: root.cycleDisplayMode()
        }
        Rectangle {
            id: settingsButton
            objectName: "miniMapSettingsButton"
            anchors.right: parent.right
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            width: root.theme.studio ? 22 : 42
            height: root.theme.studio ? 18 : 22
            radius: 5
            color: settingsPointer.containsMouse ? theme.surfaceHover : "transparent"
            border.color: theme.minimapBorder
            border.width: root.theme.studio ? 0 : 1
            ToolTip.visible: settingsPointer.containsMouse
            ToolTip.text: "缩略图选项"
            Text {
                anchors.centerIn: parent
                text: root.theme.studio ? "⋯" : "设置"
                color: theme.textSecondary
                font.pixelSize: 10
            }
            MouseArea {
                id: settingsPointer
                objectName: "miniMapSettingsAction"
                property bool menuOpenOnPress: false
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                Accessible.name: "缩略图设置"
                onPressed: menuOpenOnPress = miniMapSettingsMenu.visible
                onClicked: menuOpenOnPress ? miniMapSettingsMenu.close() : miniMapSettingsMenu.open()
            }
        }
        MouseArea {
            id: moveArea
            objectName: "miniMapMoveArea"
            anchors.left: parent.left
            anchors.right: settingsButton.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            hoverEnabled: true
            enabled: !root.dockToBottomRight
            cursorShape: root.dockToBottomRight ? Qt.ArrowCursor : Qt.SizeAllCursor
            Accessible.name: "拖动缩略图"
            property real startX: 0
            property real startY: 0
            onPressed: mouse => {
                var point = moveArea.mapToItem(root.parent, mouse.x, mouse.y)
                startX = point.x
                startY = point.y
                root.beginMove()
            }
            onPositionChanged: mouse => {
                if (pressed) {
                    var point = moveArea.mapToItem(root.parent, mouse.x, mouse.y)
                    root.moveFromDrag(point.x - startX, point.y - startY)
                }
            }
            onReleased: root.finishMove()
            onCanceled: root.finishMove()
        }
    }

    Item {
        id: mapCanvas
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: root.theme.studio ? 3 : 5

        Rectangle {
            objectName: "miniMapInnerBackground"
            anchors.fill: parent
            color: theme.minimapInnerBackground
            border.color: theme.minimapBorder
            border.width: 1
            z: -1
        }

        Canvas {
            id: denseNoteCanvas
            objectName: "miniMapDenseNoteCanvas"
            anchors.fill: parent
            visible: editor.noteModel.totalCount > 600
            enabled: false
            readonly property var paintPalette: [root.theme.textMuted,
                root.theme.layerMelody, root.theme.layerAccompaniment,
                root.theme.layerBass, root.theme.layerDrums, root.theme.layerOther]
            onPaintPaletteChanged: requestPaint()

            onPaint: {
                // requestPaint may be queued while an ancestor is hidden.
                // Do not marshal the full score until the overview is visible.
                if (!root.visible || width <= 0 || height <= 0)
                    return
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var grouped = JSON.parse(editor.noteModel.minimapJson)
                var activeLayerId = editor.activeLayerId
                // Resolve QML properties and layer colors once per paint/layer,
                // not across every note in the full score.
                var timeScale = width / root.contentSeconds
                var pitchScale = height / root.pitchSpan
                var topPitch = root.maximumPitch
                var noteHeight = Math.max(2, pitchScale * 1.5)
                var deletedColor = root.theme.textMuted
                for (var layerId in grouped) {
                    var layer = grouped[layerId]
                    var notes = layer.notes
                    if (!notes.length || !layer.visible)
                        continue
                    var activeLayer = String(layerId) === activeLayerId
                    var customColor = layer.color
                    var layerColor = customColor.length > 0 ? customColor
                        : root.theme.layerRoleColor(layer.role)
                    var inactiveOpacity = layer.muted ? 0.24 : 0.42
                    var previousDeleted = false
                    var previousOpacity = activeLayer ? 1.0 : inactiveOpacity
                    ctx.fillStyle = layerColor
                    ctx.globalAlpha = previousOpacity
                    for (var index = 0; index < notes.length; ++index) {
                        var note = notes[index]
                        var deleted = note[3] === "deleted"
                        if (deleted !== previousDeleted) {
                            ctx.fillStyle = deleted ? deletedColor : layerColor
                            previousDeleted = deleted
                        }
                        var opacity = deleted ? 0.26
                            : (activeLayer
                                ? (note[3] === "pending" ? 0.72 : 1.0)
                                : inactiveOpacity)
                        if (opacity !== previousOpacity) {
                            ctx.globalAlpha = opacity
                            previousOpacity = opacity
                        }
                        var noteX = Math.max(0, note[0] * timeScale)
                        var noteY = Math.max(0, (topPitch - note[2]) * pitchScale)
                        var noteWidth = Math.max(2, note[1] * timeScale)
                        ctx.fillRect(noteX, noteY, noteWidth, noteHeight)
                    }
                }
                ctx.globalAlpha = 1.0
            }

            onVisibleChanged: if (visible) requestPaint()
            Connections {
                target: editor.noteModel
                function onOverviewChanged() { denseNoteCanvas.requestPaint() }
            }
            Connections {
                target: editor
                function onActiveLayerChanged() { denseNoteCanvas.requestPaint() }
            }
            Connections {
                target: root
                function onContentSecondsChanged() { denseNoteCanvas.requestPaint() }
                function onMinimumPitchChanged() { denseNoteCanvas.requestPaint() }
                function onMaximumPitchChanged() { denseNoteCanvas.requestPaint() }
                function onWidthChanged() { denseNoteCanvas.requestPaint() }
                function onHeightChanged() { denseNoteCanvas.requestPaint() }
            }
        }

        Repeater {
            model: editor.noteModel.totalCount <= 600 ? editor.noteModel : null
            delegate: Rectangle {
                required property string noteId
                required property int pitch
                required property real startTime
                required property real duration
                required property string status
                required property string layerId
                required property string layerRole
                required property string layerCustomColor
                required property bool layerVisible
                required property bool layerMuted
                objectName: "miniMapNote-" + noteId
                x: Math.max(0, startTime / root.contentSeconds * mapCanvas.width)
                y: Math.max(0, (root.maximumPitch - pitch) / root.pitchSpan * mapCanvas.height)
                width: Math.max(2, duration / root.contentSeconds * mapCanvas.width)
                height: Math.max(2, mapCanvas.height / root.pitchSpan * 1.5)
                radius: 1
                visible: layerVisible
                color: status === "deleted" ? root.theme.textMuted
                    : (layerCustomColor.length > 0
                        ? layerCustomColor : root.theme.layerRoleColor(layerRole))
                opacity: status === "deleted" ? 0.26
                    : (layerId === editor.activeLayerId
                        ? (status === "pending" ? 0.72 : 1.0)
                        : (layerMuted ? 0.24 : 0.42))
            }
        }

        Rectangle {
            id: visibleViewport
            objectName: "miniMapViewport"
            x: Math.max(0, root.viewStartSeconds / root.contentSeconds * mapCanvas.width)
            y: Math.max(0, (root.maximumPitch - root.highestPitch) / root.pitchSpan * mapCanvas.height)
            width: Math.min(
                mapCanvas.width, Math.max(8, root.visibleSeconds / root.contentSeconds * mapCanvas.width)
            )
            height: Math.min(
                mapCanvas.height,
                Math.max(8, (root.highestPitch - root.lowestPitch + 1) / root.pitchSpan * mapCanvas.height)
            )
            color: root.theme.minimapViewportFill
            border.color: root.theme.minimapViewportBorder
            border.width: 1
        }

        Rectangle {
            id: miniMapPlayhead
            objectName: "miniMapPlayhead"
            visible: root.playheadVisible
            x: Math.max(
                0,
                Math.min(
                    Math.max(0, mapCanvas.width - width),
                    root.clampedPlayheadSeconds() / root.contentSeconds * mapCanvas.width
                )
            )
            y: 0
            width: 2
            height: mapCanvas.height
            color: root.theme.playheadLine
            z: 4
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            cursorShape: Qt.CrossCursor
            Accessible.name: "定位钢琴卷帘视图"
            onClicked: mouse => {
                root.navigateRequested(
                    Math.max(0, mouse.x / Math.max(1, mapCanvas.width) * root.contentSeconds),
                    Math.round(root.maximumPitch - mouse.y / Math.max(1, mapCanvas.height) * root.pitchSpan)
                )
            }
        }
    }

    Rectangle {
        id: resizeGrip
        anchors.left: parent.left
        anchors.top: parent.top
        width: root.theme.studio ? 18 : 24
        height: root.theme.studio ? 18 : 24
        color: root.theme.studio
            ? (resizePointer.containsMouse ? theme.surfaceHover : "transparent") : Qt.rgba(
            theme.accentPrimary.r,
            theme.accentPrimary.g,
            theme.accentPrimary.b,
            resizePointer.containsMouse ? 0.20 : 0.10
        )
        border.color: theme.accentPrimary
        border.width: root.theme.studio ? 0 : 1
        radius: 4
        z: 3
        Text {
            anchors.centerIn: parent
            text: "↖"
            color: root.theme.studio ? theme.textSecondary : theme.accentPrimary
            font.pixelSize: root.theme.studio ? 12 : 14
        }
        MouseArea {
            id: resizePointer
            objectName: "miniMapResizeHandle"
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.SizeFDiagCursor
            Accessible.name: "调整缩略图大小"
            property real startX: 0
            property real startY: 0
            onPressed: mouse => {
                var point = resizePointer.mapToItem(root.parent, mouse.x, mouse.y)
                startX = point.x
                startY = point.y
                root.beginResize()
            }
            onPositionChanged: mouse => {
                if (pressed) {
                    var point = resizePointer.mapToItem(root.parent, mouse.x, mouse.y)
                    root.resizeFromDrag(point.x - startX, point.y - startY)
                }
            }
            onReleased: root.finishResize()
            onCanceled: root.finishResize()
        }
    }

    Popup {
        id: miniMapSettingsMenu
        objectName: "miniMapSettingsMenu"
        parent: root.theme.studio ? settingsButton : Overlay.overlay
        x: Math.max(8, root.x + root.width - width)
        y: Math.max(8, root.y + header.height + 4)
        width: 210
        padding: 10
        modal: false
        closePolicy: Popup.CloseOnEscape | (root.theme.studio ? Popup.CloseOnReleaseOutsideParent : Popup.CloseOnPressOutside)
        onAboutToShow: {
            if (root.theme.studio) {
                var point = settingsButton.mapToItem(Overlay.overlay, 0, 0)
                x = Math.max(6 - point.x, settingsButton.width - width)
                y = Math.max(6 - point.y, -height - 6)
                if (typeof keyboard !== "undefined") keyboard.suspendInput("mini-map-settings", true)
            }
        }
        onClosed: if (root.theme.studio && typeof keyboard !== "undefined") keyboard.suspendInput("mini-map-settings", false)
        background: Rectangle {
            radius: theme.radiusS
            color: theme.elevatedSurface
            border.color: theme.borderStrong
        }
        contentItem: Column {
            width: parent.width
            spacing: 6
            Text {
                visible: !root.theme.studio
                width: parent.width
                text: "缩略图设置"
                color: theme.textPrimary
                font.pixelSize: 13
                font.bold: true
            }
            CheckBox {
                objectName: "studioMiniMapSoften"
                visible: root.theme.studio
                width: parent.width
                text: "弱化"
                checked: root.displayMode === "weak"
                onClicked: experience.setMiniMapDisplayMode(checked ? "weak" : "visible")
            }
            CheckBox {
                objectName: "studioMiniMapAspectLock"
                visible: root.theme.studio
                width: parent.width
                text: "锁定比例"
                checked: experience.miniMapAspectLocked
                onClicked: experience.setMiniMapAspectLocked(checked)
            }
            Rectangle {
                visible: !root.theme.studio
                width: parent.width
                height: 30
                radius: 6
                color: aspectPointer.containsMouse ? theme.surfaceHover : theme.surface
                border.color: experience.miniMapAspectLocked ? theme.accentPrimary : theme.borderSubtle
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: experience.miniMapAspectLocked ? "锁定比例：16:9" : "自由调整宽高"
                    color: experience.miniMapAspectLocked ? theme.accentPrimary : theme.textSecondary
                    font.pixelSize: 11
                }
                MouseArea {
                    id: aspectPointer
                    objectName: "miniMapAspectLockToggle"
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    Accessible.name: "锁定缩略图 16 比 9 比例"
                    onClicked: experience.setMiniMapAspectLocked(!experience.miniMapAspectLocked)
                }
            }
            Rectangle {
                width: parent.width
                height: 30
                radius: 6
                color: resetPointer.containsMouse ? theme.surfaceHover : theme.surface
                border.color: theme.borderSubtle
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.theme.studio ? "恢复大小" : "恢复默认大小 280 × 160"
                    color: theme.textSecondary
                    font.pixelSize: 11
                }
                MouseArea {
                    id: resetPointer
                    objectName: "miniMapResetSize"
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    Accessible.name: "恢复缩略图默认大小"
                    onClicked: root.resetSize()
                }
            }
            Rectangle {
                width: parent.width
                height: 30
                radius: 6
                color: hidePointer.containsMouse ? theme.surfaceHover : theme.surface
                border.color: theme.borderSubtle
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.theme.studio ? "隐藏" : "隐藏缩略图"
                    color: theme.textSecondary
                    font.pixelSize: 11
                }
                MouseArea {
                    id: hidePointer
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    Accessible.name: "隐藏缩略图"
                    onClicked: {
                        miniMapSettingsMenu.close()
                        experience.setMiniMapDisplayMode("hidden")
                    }
                }
            }
        }
    }
}
