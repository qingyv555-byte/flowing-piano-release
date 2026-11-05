import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One non-modal tip at a time keeps the editor usable while making the first
// few interactions discoverable. Dismissed keys live in app preferences, not
// in a project file.
Item {
    id: root
    objectName: "editorTips"

    required property var theme
    property int tipIndex: 0
    property int tipsRevision: 0

    readonly property var tips: [
        { key: "add-note", text: "双击空白区域添加音符" },
        { key: "move-note", text: "拖动音符改变位置" },
        { key: "resize-note", text: "拖右边缘改变长度" },
        { key: "space-play", text: "按空格播放 / 暂停" },
        { key: "ai-selection", text: "框选音符后可以让 AI 修改" }
    ]
    readonly property var currentTip: root.availableTipAt(root.tipIndex)
    readonly property bool hasTip: root.currentTip !== null
    implicitHeight: root.hasTip ? 42 : 0

    function availableTipAt(startIndex) {
        root.tipsRevision
        for (var offset = 0; offset < root.tips.length; ++offset) {
            var index = (startIndex + offset) % root.tips.length
            var candidate = root.tips[index]
            if (!experience.tipDismissed(candidate.key))
                return candidate
        }
        return null
    }

    function dismissCurrent() {
        if (!root.currentTip)
            return
        experience.dismissTip(root.currentTip.key)
        root.tipIndex = (root.tipIndex + 1) % root.tips.length
    }

    function resetTips() {
        experience.resetEditorTips()
        root.tipIndex = 0
        root.tipsRevision += 1
    }

    Connections {
        target: experience
        function onChanged() { root.tipsRevision += 1 }
    }

    Rectangle {
        anchors.fill: parent
        visible: root.hasTip
        radius: theme.radiusM
        color: theme.elevatedSurface
        border.color: theme.borderSubtle

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: theme.space3
            anchors.rightMargin: theme.space2
            spacing: theme.space2

            Label {
                text: "提示"
                color: theme.accentPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                font.weight: theme.weightSemibold
            }

            Label {
                objectName: "editorTipText"
                Layout.fillWidth: true
                text: root.currentTip ? root.currentTip.text : ""
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                elide: Text.ElideRight
            }

            AppButton {
                objectName: "editorTipDismissButton"
                theme: root.theme
                text: "知道了"
                variant: "ghost"
                implicitHeight: 30
                accessibleName: "关闭当前新手提示"
                onClicked: root.dismissCurrent()
            }
        }
    }
}
