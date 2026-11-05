import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "musicLayerList"

    required property Theme theme
    required property var editorBridge
    property bool compact: false

    signal layerActivated(string layerId)

    spacing: theme.space1

    RowLayout {
        Layout.fillWidth: true
        visible: !root.compact
        Label {
            Layout.fillWidth: true
            text: "音乐层"
            color: root.theme.textSecondary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeCaption
            font.weight: root.theme.weightSemibold
        }
        AppIconButton {
            objectName: "addMusicLayerButton"
            theme: root.theme
            iconSource: "icons/plus.svg"
            tooltip: "添加音乐层"
            accessibleName: tooltip
            implicitWidth: 28
            implicitHeight: 28
            onClicked: root.editorBridge.addMusicLayer("新音乐层", "other")
        }
    }

    ListView {
        id: layerListView
        objectName: "musicLayerListView"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(contentHeight, 220)
        Layout.minimumHeight: Math.min(contentHeight, 44)
        clip: true
        spacing: root.theme.space1
        model: root.editorBridge ? root.editorBridge.layerModel : null
        delegate: Item {
            required property string layerId
            required property string name
            required property string role
            required property string customColor
            required property bool muted
            required property bool solo
            required property bool layerVisible
            required property bool locked
            required property bool pinned
            required property int order
            required property int noteCount
            width: layerListView.width
            height: layerRow.implicitHeight

            MusicLayerRow {
                id: layerRow
                width: parent.width
                theme: root.theme
                layerId: parent.layerId
                name: parent.name
                role: parent.role
                customColor: parent.customColor
                active: parent.layerId === root.editorBridge.activeLayerId
                muted: parent.muted
                solo: parent.solo
                layerVisible: parent.layerVisible
                locked: parent.locked
                pinned: parent.pinned
                order: parent.order
                layerCount: layerListView.count
                noteCount: parent.noteCount
                onActivated: function(layerId) {
                    root.editorBridge.setActiveLayer(layerId)
                    root.layerActivated(layerId)
                }
                onMuteToggled: (layerId, value) =>
                    root.editorBridge.setMusicLayerProperty(layerId, "muted", value)
                onSoloToggled: (layerId, value) =>
                    root.editorBridge.setMusicLayerProperty(layerId, "solo", value)
                onVisibilityToggled: (layerId, value) =>
                    root.editorBridge.setMusicLayerProperty(layerId, "visible", value)
                onLockToggled: (layerId, value) =>
                    root.editorBridge.setMusicLayerProperty(layerId, "locked", value)
                onDuplicateRequested: layerId => root.editorBridge.duplicateMusicLayer(layerId)
                onPinToggled: (layerId, value) =>
                    root.editorBridge.setMusicLayerPinned(layerId, value)
                onDeleteRequested: layerId => root.editorBridge.deleteMusicLayer(layerId)
                onRenameCommitted: (layerId, name) =>
                    root.editorBridge.renameMusicLayer(layerId, name)
                onColorRequested: layerId => root.editorBridge.setActiveLayer(layerId)
                onSelectAllRequested: layerId => root.editorBridge.selectMusicLayer(layerId)
                onMoveSelectionHereRequested: layerId =>
                    root.editorBridge.moveSelectionToLayer(layerId)
                onMoveFocusRequested: direction => {
                    var nextIndex = Math.max(0, Math.min(
                        layerListView.count - 1,
                        layerListView.currentIndex + direction
                    ))
                    layerListView.currentIndex = nextIndex
                    var nextItem = layerListView.itemAtIndex(nextIndex)
                    if (nextItem && nextItem.children.length > 0)
                        nextItem.children[0].forceActiveFocus()
                }
            }
        }
    }
}
