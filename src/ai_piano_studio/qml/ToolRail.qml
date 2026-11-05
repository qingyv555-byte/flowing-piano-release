import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    required property var theme
    color: theme.toolbar
    width: theme.railWidth
    border.color: theme.border
    Column {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 8
        spacing: 6
        Repeater {
            model: [{icon:"↖", tip:"选择"}, {icon:"＋", tip:"添加音符"}, {icon:"□", tip:"框选"}, {icon:"✂", tip:"切分"}, {icon:"⌁", tip:"力度"}, {icon:"T", tip:"标记"}]
            delegate: ToolButton {
                required property var modelData
                required property int index
                width: 32; height: 32
                text: modelData.icon
                Accessible.name: modelData.tip
                ToolTip.text: modelData.tip
                ToolTip.visible: hovered
                background: Rectangle { radius: 4; color: index === 0 ? "#21453A" : "transparent" }
                contentItem: Text { text: parent.text; color: index === 0 ? root.theme.accent : root.theme.muted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            }
        }
    }
}
