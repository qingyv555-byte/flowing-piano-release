import QtQuick
import QtQuick.Controls

Control {
    id: root

    required property Theme theme
    property var entry: ({})
    property string mode: "full"
    property bool current: false
    property bool managing: false
    property bool selected: false
    property bool blocked: false
    property string accessibleName: (current ? "当前工程：" : "工程：")
        + (entry.name || "未命名工程")
    readonly property bool interactive: enabled && !blocked
    readonly property bool missing: entry.exists === false
    readonly property bool isHovered: hover.hovered

    signal activated(string path)
    signal selectionToggled(string path)
    signal menuRequested(var entry, var anchor)

    activeFocusOnTab: true
    hoverEnabled: interactive
    opacity: enabled ? 1 : 0.42
    implicitHeight: mode === "full" ? 64 : theme.iconButtonSize
    implicitWidth: mode === "full" ? 320 : theme.iconButtonSize
    Accessible.role: Accessible.Button
    Accessible.name: accessibleName
    Accessible.checkable: managing
    Accessible.checked: selected
    Accessible.onPressAction: performPrimaryAction()

    function activate() {
        if (!interactive || missing)
            return
        activated(entry.path || "")
    }

    function toggleSelection() {
        if (!interactive)
            return
        selected = !selected
        selectionToggled(entry.path || "")
    }

    function requestMenu() {
        if (!interactive)
            return
        menuRequested(entry, actionSlot)
    }

    function performPrimaryAction() {
        if (managing)
            toggleSelection()
        else
            activate()
    }

    Keys.onPressed: event => {
        if (event.isAutoRepeat || !interactive)
            return
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            event.accepted = true
            activate()
        } else if (event.key === Qt.Key_Space) {
            event.accepted = true
            performPrimaryAction()
        }
    }

    HoverHandler {
        id: hover
        enabled: root.interactive
    }

    TapHandler {
        id: rowTap
        enabled: root.interactive
        onTapped: root.performPrimaryAction()
    }

    ToolTip.visible: root.mode === "compact" && root.isHovered
    ToolTip.delay: 400
    ToolTip.text: root.accessibleName

    background: Rectangle {
        color: root.mode === "full" && root.isHovered ? root.theme.surfaceHover
            : root.theme.surface
        radius: root.theme.radiusM
        border.width: root.current ? 2 : 1
        border.color: root.current ? root.theme.accentPrimary : root.theme.borderSubtle

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: root.theme.radiusM + 2
            color: "transparent"
            border.width: root.activeFocus ? 2 : 0
            border.color: root.theme.focusRing
        }
    }

    contentItem: Item {
        implicitWidth: root.implicitWidth
        implicitHeight: root.implicitHeight

        Text {
            anchors.centerIn: parent
            visible: root.mode === "compact"
            text: root.missing ? "⚠" : "♪"
            color: root.missing ? root.theme.warning : root.theme.accentMusic
            font.family: root.theme.fontSymbol
            font.pixelSize: root.theme.iconMedium
        }

        Rectangle {
            visible: root.mode === "compact" && root.current
            width: 6
            height: 6
            radius: width / 2
            color: root.theme.accentPrimary
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: root.theme.space1
        }

        Item {
            visible: root.mode === "full"
            anchors.fill: parent
            anchors.leftMargin: root.theme.space3
            anchors.rightMargin: root.theme.space3

            Row {
                anchors.left: parent.left
                anchors.right: actionSlot.left
                anchors.rightMargin: root.theme.space2
                anchors.verticalCenter: parent.verticalCenter
                spacing: root.theme.space2

                Text {
                    text: root.missing ? "⚠" : "♪"
                    color: root.missing ? root.theme.warning : root.theme.accentMusic
                    font.family: root.theme.fontSymbol
                    font.pixelSize: root.theme.iconMedium
                    verticalAlignment: Text.AlignVCenter
                }

                Column {
                    width: Math.max(0, parent.width - root.theme.iconMedium
                                    - root.theme.space2)
                    spacing: 0

                    Row {
                        width: parent.width
                        spacing: root.theme.space2

                        Text {
                            width: Math.max(0, parent.width - currentLabel.width)
                            text: root.entry.name || "未命名工程"
                            color: root.theme.textPrimary
                            font.family: root.theme.fontUi
                            font.pixelSize: root.theme.typeBodyStrong
                            font.weight: root.theme.weightSemibold
                            elide: Text.ElideRight
                        }

                        Text {
                            id: currentLabel
                            visible: root.current
                            text: "当前"
                            color: root.theme.accentPrimary
                            font.family: root.theme.fontUi
                            font.pixelSize: root.theme.typeCaption
                            font.weight: root.theme.weightSemibold
                        }
                    }

                    Text {
                        width: parent.width
                        text: root.metadataText(root.entry)
                        color: root.missing ? root.theme.warning : root.theme.textMuted
                        font.family: root.theme.fontUi
                        font.pixelSize: root.theme.typeCaption
                        elide: Text.ElideRight
                    }
                }
            }

            Item {
                id: actionSlot
                width: root.theme.iconButtonSize
                height: root.theme.iconButtonSize
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter

                AppIconButton {
                    id: actionButton
                    objectName: "projectRowAction"
                    anchors.centerIn: parent
                    theme: root.theme
                    width: root.theme.iconButtonSize
                    height: root.theme.iconButtonSize
                    enabled: root.interactive
                    tooltip: root.managing ? "选择工程" : "更多操作"
                    accessibleName: root.managing ? "选择工程" : "更多操作"
                    Accessible.checkable: root.managing
                    Accessible.checked: root.selected
                    onClicked: {
                        if (root.managing)
                            root.toggleSelection()
                        else
                            root.requestMenu()
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: !root.managing
                        text: "⋯"
                        color: root.interactive ? root.theme.textSecondary : root.theme.textDisabled
                        font.family: root.theme.fontSymbol
                        font.pixelSize: root.theme.iconLarge
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        visible: root.managing
                        width: root.theme.iconMedium
                        height: width
                        radius: root.theme.radiusS
                        color: root.selected ? root.theme.accentPrimary : "transparent"
                        border.width: 1
                        border.color: root.selected ? root.theme.accentPrimary : root.theme.borderStrong

                        Text {
                            anchors.centerIn: parent
                            visible: root.selected
                            text: "✓"
                            color: root.theme.textOnAccent
                            font.family: root.theme.fontSymbol
                            font.pixelSize: root.theme.typeSmall
                        }
                    }
                }
            }
        }
    }

    function durationText(seconds) {
        if (seconds === null || seconds === undefined)
            return "未知时长"
        var total = Math.max(0, Number(seconds) || 0)
        var minutes = Math.floor(total / 60)
        var remainder = total % 60
        return minutes > 0 ? minutes + " 分 " + remainder + " 秒" : remainder + " 秒"
    }

    function friendlyTime(value) {
        if (!value)
            return ""
        var date = new Date(value)
        if (isNaN(date.getTime()))
            return value
        var now = new Date()
        var sameDay = date.getFullYear() === now.getFullYear()
            && date.getMonth() === now.getMonth()
            && date.getDate() === now.getDate()
        var yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
        var isYesterday = date.getFullYear() === yesterday.getFullYear()
            && date.getMonth() === yesterday.getMonth()
            && date.getDate() === yesterday.getDate()
        var hour = (date.getHours() < 10 ? "0" : "") + date.getHours()
        var minute = (date.getMinutes() < 10 ? "0" : "") + date.getMinutes()
        if (sameDay)
            return "今天 " + hour + ":" + minute
        if (isYesterday)
            return "昨天 " + hour + ":" + minute
        return date.getFullYear() + "/" + (date.getMonth() + 1) + "/" + date.getDate()
    }

    function metadataText(entry) {
        if (root.missing)
            return "文件已移动"
        var updated = root.friendlyTime(entry.lastOpened)
        if (entry.summaryReady === false && entry.isLibraryProject)
            return updated + "  ·  详情将在打开时读取"
        var bpm = entry.bpm ? "BPM " + entry.bpm : ""
        var key = entry.key ? "调 " + entry.key : ""
        var extras = [bpm, key].filter(function(value) { return value.length > 0 })
        return updated + "  ·  "
            + (entry.noteCount || 0) + " 音符  ·  "
            + root.durationText(entry.durationSeconds)
            + (extras.length > 0 ? "  ·  " + extras.join("  ·  ") : "")
    }
}
