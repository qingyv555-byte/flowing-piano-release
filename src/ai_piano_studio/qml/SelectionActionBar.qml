import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A stable, compact action rail for the current note selection.  It deliberately
// owns no layout inside Timeline: changing selection must replace this rail's
// contents without moving the ruler or the piano-roll origin.
Item {
    id: root
    objectName: "selectionActions"

    required property var theme
    required property var editor
    required property var playback
    property bool expanded: true
    property bool suppressAutoExpand: false
    readonly property bool dismissed: !expanded
    readonly property bool hasSelection: editor !== null && editor.selectedCount > 0
    readonly property var selectionAudition: playback !== null
        ? playback.selectionAudition : null

    readonly property int expandedHeight: 40
    // Collapsed is a zero-height layout state. The arrow is intentionally
    // rendered just above the rail by Main.qml, so reopening never costs a
    // permanent row of canvas space.
    readonly property int collapsedHeight: 0
    implicitHeight: root.expanded ? root.expandedHeight : root.collapsedHeight
    // Size the floating palette from the controls that are actually visible.
    // This keeps the remaining piano-roll surface interactive instead of
    // reserving an invisible tail to the right of the More button.
    implicitWidth: root.theme.space1 * 2
        + selectionSummary.Layout.preferredWidth
        + expandedContent.spacing
        + actionRow.implicitWidth

    Component.onCompleted: expanded = hasSelection

    Connections {
        target: root.editor
        function onSelectionChanged() {
            // selectedCount can remain non-zero (or even unchanged) while the
            // selected IDs are replaced. Refresh from the real selection
            // signal so every new editing context exposes its actions.
            root.expanded = !root.suppressAutoExpand && root.editor !== null && root.editor.selectedCount > 0
        }
    }

    Rectangle {
        objectName: "selectionActionsBackground"
        anchors.fill: parent
        color: root.theme.popupBackground
        radius: root.theme.radiusL
        border.width: 1
        border.color: root.theme.borderStrong
        visible: root.expanded
    }

    MouseArea {
        anchors.fill: parent
        visible: root.expanded
        acceptedButtons: Qt.AllButtons
        preventStealing: true
    }

    RowLayout {
        id: expandedContent
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: root.theme.space1
        anchors.rightMargin: root.theme.space1
        height: root.theme.controlHeight
        visible: root.expanded
        spacing: root.theme.space1

        Label {
            id: selectionSummary
            objectName: "selectionActionsSummary"
            Layout.preferredWidth: root.expanded ? 68 : 0
            Layout.minimumWidth: root.expanded ? 58 : 0
            Layout.fillHeight: true
            visible: root.expanded
            verticalAlignment: Text.AlignVCenter
            text: root.selectionAudition !== null && root.selectionAudition.playing
                ? "试听 · " + root.editor.selectedCount
                : "已选 " + root.editor.selectedCount
            color: root.selectionAudition !== null && root.selectionAudition.playing
                ? root.theme.accent : root.theme.textSecondary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSmall
            font.weight: root.theme.weightRegular
            elide: Text.ElideRight
        }

        Label {
            id: collapsedSummary
            objectName: "selectionActionsCollapsedSummary"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: false
            verticalAlignment: Text.AlignVCenter
            text: "已选音符（" + root.editor.selectedCount + "）· 操作已收起"
            color: root.theme.textSecondary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSmall
            font.weight: root.theme.weightRegular
            elide: Text.ElideRight
        }

        Flickable {
            id: actionFlow
            objectName: "selectionActionFlow"
            Layout.fillWidth: true
            Layout.preferredHeight: root.expanded ? root.theme.controlHeight : 0
            Layout.minimumHeight: 0
            Layout.maximumHeight: root.expanded ? root.theme.controlHeight : 0
            implicitHeight: root.expanded ? root.theme.controlHeight : 0
            visible: root.expanded
            clip: true
            contentWidth: actionRow.implicitWidth
            contentHeight: height
            flickableDirection: Flickable.HorizontalFlick
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentWidth > width

            Row {
                id: actionRow
                height: 32
                anchors.verticalCenter: parent.verticalCenter
                spacing: root.theme.space1

                AppIconButton {
                    width: 32
                    height: 32
                    implicitWidth: 32
                    objectName: "auditionSelectionButton"
                    theme: root.theme
                    iconSource: root.selectionAudition !== null
                        && root.selectionAudition.playing
                        ? "icons/pause.svg" : "icons/play.svg"
                    variant: "primary"
                    implicitHeight: 32
                    accessibleName: "试听选中的音符"
                    tooltip: root.selectionAudition !== null
                        && root.selectionAudition.playing ? "停止试听" : "试听选中"
                    onClicked: {
                        if (root.selectionAudition.playing)
                            root.selectionAudition.stop()
                        else
                            root.selectionAudition.playSelection()
                    }
                }
                AppButton {
                    objectName: "selectionAuditionStopButton"
                    theme: root.theme
                    text: "停止试听"
                    implicitHeight: 32
                    visible: false
                    accessibleName: "停止选中音符试听"
                    onClicked: root.selectionAudition.stop()
                }
                AppButton {
                    objectName: "selectionDuplicateButton"
                    theme: root.theme
                    text: "复制"
                    variant: "ghost"
                    labelPixelSize: 13
                    labelWeight: root.theme.weightRegular
                    implicitHeight: 32
                    accessibleName: "复制选中的音符"
                    onClicked: root.editor.duplicateSelected()
                }
                AppIconButton {
                    width: 32
                    height: 32
                    implicitWidth: 32
                    objectName: "selectionOctaveUpButton"
                    variant: "ghost"
                    theme: root.theme
                    iconSource: "icons/arrow-up.svg"
                    implicitHeight: 32
                    accessibleName: "将选中音符升高八度"
                    tooltip: "升高八度"
                    onClicked: root.editor.shiftSelectedOctave(12)
                }
                AppIconButton {
                    width: 32
                    height: 32
                    implicitWidth: 32
                    objectName: "selectionOctaveDownButton"
                    variant: "ghost"
                    theme: root.theme
                    iconSource: "icons/arrow-down.svg"
                    implicitHeight: 32
                    accessibleName: "将选中音符降低八度"
                    tooltip: "降低八度"
                    onClicked: root.editor.shiftSelectedOctave(-12)
                }
                AppIconButton {
                    width: 32
                    height: 32
                    implicitWidth: 32
                    objectName: "selectionQuantizeButton"
                    variant: "ghost"
                    theme: root.theme
                    iconSource: "icons/align-horizontal-space-around.svg"
                    implicitHeight: 32
                    accessibleName: "量化选中的音符"
                    tooltip: "对齐节拍"
                    onClicked: root.editor.quantizeSelected()
                }
                AppButton {
                    objectName: "selectionMoveToLayerButton"
                    visible: false
                    theme: root.theme
                    text: "移动到音乐层"
                    implicitHeight: 32
                    accessibleName: "将选中音符移动到音乐层"
                    onClicked: moveLayerPopup.open()
                }
                AppButton {
                    objectName: "selectionCreateLayerButton"
                    visible: false
                    theme: root.theme
                    text: "新建音乐层"
                    iconSource: "icons/plus.svg"
                    variant: "ghost"
                    implicitHeight: 32
                    accessibleName: "从选中音符新建音乐层"
                    onClicked: {
                        createLayerName.text = "新音乐层"
                        createLayerRole.currentIndex = 3
                        createLayerPopup.open()
                        createLayerName.focusInput(true)
                    }
                }
                AppButton {
                    objectName: "selectionDeleteButton"
                    visible: false
                    theme: root.theme
                    text: "删除"
                    variant: "ghost"
                    semanticKind: "danger"
                    showSemanticIndicator: false
                    iconSource: "icons/trash-2.svg"
                    implicitHeight: 32
                    accessibleName: "删除选中的音符"
                    onClicked: root.editor.deleteSelected()
                }
                AppIconButton {
                    width: 32
                    height: 32
                    implicitWidth: 32
                    id: selectionMoreButton
                    objectName: "selectionMoreButton"
                    variant: "ghost"
                    implicitHeight: 32
                    theme: root.theme
                    iconSource: "icons/ellipsis.svg"
                    tooltip: "更多选择操作"
                    accessibleName: tooltip
                    onClicked: selectionMoreMenu.open()
                }
            }
        }
    }

    AppMenu {

        theme: root.theme
        id: selectionMoreMenu
        objectName: "selectionMoreMenu"
        x: Math.max(0, root.width - width)
        y: root.height + root.theme.space1
        implicitWidth: 236
        padding: root.theme.space1


        AppMenuItem {


            theme: root.theme
            text: "移动到音乐层"
            onTriggered: moveLayerPopup.open()
        }
        AppMenuItem {
            theme: root.theme
            text: "从选中音符新建音乐层"
            onTriggered: {
                createLayerName.text = "新音乐层"
                createLayerRole.currentIndex = 3
                createLayerPopup.open()
                createLayerName.focusInput(true)
            }
        }
        AppMenuSeparator { theme: root.theme;}
        AppMenuItem {
            theme: root.theme
            text: "删除选中音符"
            destructive: true
            onTriggered: root.editor.deleteSelected()
        }
    }

    AppButton {
        id: selectionActionsToggle
        objectName: "selectionActionsToggle"
        theme: root.theme
        variant: root.expanded ? "secondary" : "ghost"
        text: root.expanded ? "⌃" : "⌄"
        implicitWidth: root.expanded ? 36 : 32
        implicitHeight: 32
        visible: false
        enabled: false
        accessibleName: root.expanded ? "收起选中音符操作" : "展开选中音符操作"
        Accessible.description: root.expanded
            ? "隐藏选中音符操作，只保留展开箭头"
            : "展开选中音符操作；重新选择音符时会自动显示"
        anchors.right: parent.right
        anchors.rightMargin: root.expanded ? root.theme.space1 : 0
        y: root.expanded ? Math.max(0, (parent.height - height) / 2) : -height
    }

    Popup {
        id: moveLayerPopup
        objectName: "selectionMoveToLayerPopup"
        width: 264
        height: Math.min(336, 86 + layerTargetList.contentHeight)
        x: Math.max(8, Math.min(root.width - width - 8, root.width * 0.46))
        y: root.height + root.theme.space1
        padding: root.theme.space2
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            color: root.theme.popupBackground
            border.width: 1
            border.color: root.theme.borderStrong
            radius: root.theme.radiusL
        }

        contentItem: ColumnLayout {
            spacing: root.theme.space1
            Label {
                Layout.fillWidth: true
                text: "移动到音乐层"
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeSmall
                font.weight: root.theme.weightRegular
            }
            AppButton {
                objectName: "selectionMoveToUnclassified"
                theme: root.theme
                text: "未分类（移出当前层）"
                variant: "ghost"
                Layout.fillWidth: true
                implicitHeight: 34
                onClicked: {
                    if (root.editor.moveSelectionToUnclassified())
                        moveLayerPopup.close()
                }
            }
            ListView {
                id: layerTargetList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: root.editor ? root.editor.layerModel : null
                spacing: 2
                delegate: Rectangle {
                    id: layerTarget
                    required property string layerId
                    required property string name
                    required property string role
                    required property string customColor
                    required property bool locked
                    width: layerTargetList.width
                    height: layerId === "layer-unclassified" ? 0 : 34
                    visible: layerId !== "layer-unclassified"
                    radius: root.theme.radiusM
                    color: targetPointer.containsMouse
                        ? root.theme.surfaceHover : "transparent"
                    opacity: locked ? 0.42 : 1
                    Rectangle {
                        anchors.left: parent.left
                        anchors.leftMargin: root.theme.space1
                        anchors.verticalCenter: parent.verticalCenter
                        width: 3
                        height: 20
                        radius: 1
                        color: layerTarget.customColor.length > 0
                            ? layerTarget.customColor
                            : root.theme.layerRoleColor(layerTarget.role)
                    }
                    Label {
                        anchors.left: parent.left
                        anchors.leftMargin: root.theme.space3
                        anchors.right: parent.right
                        anchors.rightMargin: root.theme.space2
                        anchors.verticalCenter: parent.verticalCenter
                        text: layerTarget.name + (layerTarget.locked ? " · 已锁定" : "")
                        color: root.theme.textPrimary
                        font.family: root.theme.fontUi
                        font.pixelSize: root.theme.typeSmall
                        elide: Text.ElideRight
                    }
                    MouseArea {
                        id: targetPointer
                        anchors.fill: parent
                        enabled: !layerTarget.locked
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.editor.moveSelectionToLayer(layerTarget.layerId)
                            moveLayerPopup.close()
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: createLayerPopup
        objectName: "selectionCreateLayerPopup"
        width: 320
        height: createLayerContent.implicitHeight + root.theme.space4 * 2
        x: Math.max(8, Math.min(root.width - width - 8, root.width * 0.58))
        y: root.height + root.theme.space1
        padding: root.theme.space4
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            color: root.theme.popupBackground
            border.width: 1
            border.color: root.theme.borderStrong
            radius: root.theme.radiusL
        }

        contentItem: ColumnLayout {
            id: createLayerContent
            spacing: root.theme.space3
            Label {
                Layout.fillWidth: true
                text: "从选中音符新建音乐层"
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typePanelTitle
                font.weight: root.theme.weightRegular
            }
            AppField {
                id: createLayerName
                objectName: "selectionCreateLayerName"
                theme: root.theme
                label: "名称"
                Layout.fillWidth: true
                onAccepted: createLayerConfirm.activate()
            }
            SegmentedControl {
                id: createLayerRole
                objectName: "selectionCreateLayerRole"
                theme: root.theme
                model: ["旋律", "伴奏", "低音", "其他", "自定义"]
                currentIndex: 3
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: root.theme.space2
                Item { Layout.fillWidth: true }
                AppButton {
                    theme: root.theme
                    text: "取消"
                    variant: "ghost"
                    onClicked: createLayerPopup.close()
                }
                AppButton {
                    id: createLayerConfirm
                    objectName: "selectionCreateLayerConfirm"
                    theme: root.theme
                    text: "新建并移动"
                    variant: "primary"
                    onClicked: {
                        var roles = ["melody", "accompaniment", "bass", "other", "custom"]
                        var layerId = root.editor.createMusicLayerFromSelection(
                            createLayerName.text.trim(), roles[createLayerRole.currentIndex])
                        if (layerId.length > 0)
                            createLayerPopup.close()
                    }
                }
            }
        }
    }
}
