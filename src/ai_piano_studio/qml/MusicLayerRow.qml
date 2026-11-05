import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import Qt5Compat.GraphicalEffects

Rectangle {
    id: root
    objectName: "musicLayerRow-" + layerId

    required property Theme theme
    required property string layerId
    required property string name
    required property string role
    property string customColor: ""
    property bool active: false
    property bool muted: false
    property bool solo: false
    property bool layerVisible: true
    property bool locked: false
    property bool pinned: false
    property int noteCount: 0
    property int order: 0
    property int layerCount: 1
    readonly property bool canSelectAll: !locked && noteCount > 0
    readonly property bool canReceiveSelection: !locked
    property bool renaming: false
    property bool reorderEnabled: false
    property string accessibleName: "音乐层：" + name

    signal activated(string layerId)
    signal muteToggled(string layerId, bool value)
    signal soloToggled(string layerId, bool value)
    signal visibilityToggled(string layerId, bool value)
    signal lockToggled(string layerId, bool value)
    signal renameCommitted(string layerId, string name)
    signal duplicateRequested(string layerId)
    signal colorRequested(string layerId)
    signal selectAllRequested(string layerId)
    signal moveSelectionHereRequested(string layerId)
    signal pinToggled(string layerId, bool value)
    signal deleteRequested(string layerId)
    signal moveFocusRequested(int direction)
    signal reorderDragStarted(string layerId, real sceneY)
    signal reorderDragMoved(string layerId, real sceneY)
    signal reorderDragFinished(string layerId)
    signal reorderDragCanceled(string layerId)

    function beginRename(): void {
        renaming = true
        renameInput.text = name
        Qt.callLater(function() {
            renameInput.forceActiveFocus(Qt.OtherFocusReason)
            renameInput.selectAll()
        })
    }

    function finishRename(save): void {
        if (!renaming)
            return
        var value = renameInput.text.trim()
        renaming = false
        if (save && value.length > 0 && value !== name)
            renameCommitted(layerId, value)
        else
            renameInput.text = name
    }

    implicitHeight: 46
    radius: 0
    color: active ? theme.surfaceSelected
        : (rowHover.hovered ? theme.surfaceHover : theme.elevatedSurface)
    border.width: active ? 1 : 0
    border.color: customColor.length > 0 ? customColor : theme.layerRoleColor(role)
    Accessible.name: accessibleName
    Accessible.role: Accessible.ListItem
    activeFocusOnTab: true
    Keys.onUpPressed: event => {
        root.moveFocusRequested(-1)
        event.accepted = true
    }
    Keys.onDownPressed: event => {
        root.moveFocusRequested(1)
        event.accepted = true
    }

    HoverHandler {
        id: rowHover
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: active ? 4 : 3
        color: customColor.length > 0 ? customColor : theme.layerRoleColor(role)
    }

    MouseArea {
        id: rowPointer
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.activated(root.layerId)
        onDoubleClicked: root.beginRename()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: root.theme.space3
        anchors.rightMargin: root.theme.space1
        spacing: 3

        ColumnLayout {
            id: layerTextColumn
            Layout.fillWidth: true
            Layout.minimumWidth: 44
            spacing: 0

            Label {
                Layout.fillWidth: true
                visible: !root.renaming
                text: root.name
                color: root.layerVisible ? root.theme.textPrimary : root.theme.textDisabled
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeSmall
                font.weight: root.active ? root.theme.weightSemibold : root.theme.weightRegular
                elide: Text.ElideRight
            }
            Basic.TextField {
                id: renameInput
                objectName: "musicLayerRenameField-" + root.layerId
                Layout.fillWidth: true
                Layout.preferredHeight: 26
                visible: root.renaming
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeSmall
                leftPadding: 6
                rightPadding: 6
                selectByMouse: true
                background: Rectangle {
                    radius: root.theme.radiusS
                    color: root.theme.inputBackground
                    border.width: 1
                    border.color: root.theme.accentPrimary
                }
                Keys.onEscapePressed: event => {
                    root.finishRename(false)
                    event.accepted = true
                }
                onAccepted: root.finishRename(true)
                onActiveFocusChanged: {
                    if (!activeFocus && root.renaming)
                        root.finishRename(true)
                }
            }
            Label {
                Layout.fillWidth: true
                visible: !root.renaming
                text: root.noteCount + " 个音符"
                color: root.theme.textMuted
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeCaption
                elide: Text.ElideRight
            }
        }

        component LayerToggle: Button {
            id: toggle
            property Theme theme
            property url iconSource
            property string tooltip: Accessible.name
            implicitWidth: 22
            implicitHeight: 24
            padding: 0
            focusPolicy: Qt.StrongFocus
            hoverEnabled: true
            opacity: toggle.checked || toggle.hovered || toggle.activeFocus
                ? 1.0 : 0.78
            background: Rectangle {
                radius: toggle.theme.radiusS
                color: toggle.checked ? toggle.theme.surfaceSelected
                    : (toggle.hovered ? toggle.theme.surfaceHover : "transparent")
                border.width: toggle.activeFocus || toggle.checked ? 1 : 0
                border.color: toggle.activeFocus
                    ? toggle.theme.focusRing : toggle.theme.borderStrong
            }
            contentItem: Item {
                Text {
                    anchors.centerIn: parent
                    visible: toggle.iconSource.toString().length === 0
                    text: toggle.text
                    color: toggle.checked ? toggle.theme.accentPrimary
                        : toggle.theme.textSecondary
                    font.pixelSize: toggle.theme.typeCaption
                    font.bold: true
                }
                Image {
                    id: toggleIcon
                    anchors.centerIn: parent
                    width: 14
                    height: 14
                    source: toggle.iconSource
                    visible: toggle.iconSource.toString().length > 0
                }
                ColorOverlay {
                    anchors.fill: toggleIcon
                    source: toggleIcon
                    color: toggle.checked ? toggle.theme.accentPrimary
                        : toggle.theme.textSecondary
                    visible: toggleIcon.visible
                }
            }
            ToolTip.visible: hovered
            ToolTip.delay: 400
            ToolTip.text: tooltip
        }

        LayerToggle {
            objectName: "musicLayerMute-" + root.layerId
            theme: root.theme
            text: "M"
            checkable: true
            checked: root.muted
            Accessible.name: root.muted ? "取消静音 " + root.name : "静音 " + root.name
            onClicked: root.muteToggled(root.layerId, checked)
        }
        LayerToggle {
            objectName: "musicLayerSolo-" + root.layerId
            theme: root.theme
            text: "S"
            checkable: true
            checked: root.solo
            Accessible.name: root.solo ? "取消独奏 " + root.name : "独奏 " + root.name
            onClicked: root.soloToggled(root.layerId, checked)
        }
        LayerToggle {
            objectName: "musicLayerVisible-" + root.layerId
            theme: root.theme
            iconSource: "icons/eye.svg"
            checked: root.layerVisible
            Accessible.name: root.layerVisible ? "隐藏 " + root.name : "显示 " + root.name
            onClicked: root.visibilityToggled(root.layerId, !root.layerVisible)
        }
        LayerToggle {
            objectName: "musicLayerLock-" + root.layerId
            theme: root.theme
            iconSource: root.locked ? "icons/lock.svg" : "icons/unlock.svg"
            checked: root.locked
            Accessible.name: root.locked ? "解锁 " + root.name : "锁定 " + root.name
            onClicked: root.lockToggled(root.layerId, !root.locked)
        }
        LayerToggle {
            id: layerMenuButton
            objectName: "musicLayerMenu-" + root.layerId
            theme: root.theme
            iconSource: "icons/ellipsis.svg"
            Accessible.name: root.name + " 更多操作"
            onClicked: layerMenu.popup(layerMenuButton, 0, layerMenuButton.height)
        }
    }

    MouseArea {
        id: reorderPointer
        objectName: "musicLayerReorderPointer-" + root.layerId
        x: layerTextColumn.mapToItem(root, 0, 0).x
        y: 0
        width: layerTextColumn.width
        height: root.height
        visible: root.reorderEnabled && !root.renaming
        enabled: visible
        hoverEnabled: true
        cursorShape: Qt.SizeVerCursor
        property bool reorderActive: false
        property bool reorderCanceled: false

        onPressed: mouse => {
            reorderActive = true
            reorderCanceled = false
            root.reorderDragStarted(
                root.layerId, mapToItem(null, mouse.x, mouse.y).y)
        }
        onPositionChanged: mouse => {
            if (pressed && reorderActive)
                root.reorderDragMoved(
                    root.layerId, mapToItem(null, mouse.x, mouse.y).y)
        }
        onReleased: {
            if (!reorderActive)
                return
            var canceled = reorderCanceled
            reorderActive = false
            reorderCanceled = false
            if (!canceled)
                root.reorderDragFinished(root.layerId)
        }
        onCanceled: {
            if (!reorderActive)
                return
            reorderCanceled = true
            reorderActive = false
            root.reorderDragCanceled(root.layerId)
        }
        onClicked: root.activated(root.layerId)
        onDoubleClicked: root.beginRename()
    }

    AppMenu {

        theme: root.theme
        id: layerMenu
        objectName: "musicLayerMenuPopup-" + root.layerId

        AppMenuItem { theme: root.theme; text: "重命名"; onTriggered: root.beginRename() }
        AppMenuItem { theme: root.theme; text: "更改颜色"; onTriggered: root.colorRequested(root.layerId) }
        AppMenuItem { theme: root.theme; text: "创建副本"; onTriggered: root.duplicateRequested(root.layerId) }
        AppMenuSeparator { theme: root.theme;}
        AppMenuItem {
            theme: root.theme
            objectName: "musicLayerSelectAll-" + root.layerId
            text: "选择该层全部音符"
            enabled: root.canSelectAll
            onTriggered: root.selectAllRequested(root.layerId)
        }
        AppMenuItem {
            theme: root.theme
            objectName: "musicLayerMoveSelectionHere-" + root.layerId
            text: "将选中音符移到这里"
            enabled: root.canReceiveSelection
            onTriggered: root.moveSelectionHereRequested(root.layerId)
        }
        AppMenuItem {
            theme: root.theme
            text: root.locked ? "解锁音乐层" : "锁定音乐层"
            onTriggered: root.lockToggled(root.layerId, !root.locked)
        }
        AppMenuSeparator { theme: root.theme;}
        AppMenuItem {
            theme: root.theme
            objectName: "musicLayerPin-" + root.layerId
            text: root.pinned ? "取消置顶" : "置顶"
            onTriggered: root.pinToggled(root.layerId, !root.pinned)
        }
        AppMenuSeparator { theme: root.theme;}
        AppMenuItem {
            theme: root.theme
            objectName: "musicLayerDelete-" + root.layerId
            destructive: true
            text: "删除音乐层"
            onTriggered: root.deleteRequested(root.layerId)
        }
    }
}
