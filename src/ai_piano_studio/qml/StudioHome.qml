import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: home
    objectName: "studioHome"
    required property var welcome
    readonly property Theme theme: welcome.theme
    readonly property var values: studioAppearance.values
    readonly property bool compact: width < 1100
    readonly property bool hasCurrent: editor.noteModel.totalCount > 0 || editor.currentFilePath.length > 0
    readonly property var recentEntries: {
        var byPath = {}, entries = editor.libraryEntries
        for (var i = 0; i < entries.length; i++) byPath[entries[i].path] = entries[i]
        return editor.recentEntries.map(function(recent) {
            var entry = byPath[recent.path]
            return entry && entry.exists ? Object.assign({}, entry, {lastOpened: recent.lastOpened}) : null
        }).filter(function(entry) { return entry !== null }).slice(0, 3)
    }
    readonly property bool ambientRunning: visible && values.homeAmbient === true && !values.reducedMotion
        && Window.window && Window.window.visible && Window.window.visibility !== Window.Minimized
    readonly property color ink: theme.textPrimary
    readonly property color muted: theme.textMuted
    readonly property bool customBackground: String(values.homeSource).indexOf("file:") === 0
    property bool leaving: false
    function focusPrimaryAction() { (continueButton.visible ? continueButton : createButton).forceActiveFocus() }
    function recentTime(value) {
        var date = new Date(value)
        return !value || isNaN(date.getTime()) ? "钢琴工程" : "钢琴工程 · 打开于 " + Qt.formatDateTime(date, "MM-dd hh:mm")
    }
    Rectangle { anchors.fill: parent; color: home.theme.appBackground }
    HoverHandler { id: homePointer; enabled: home.ambientRunning }
    StudioBackground {
        anchors.fill: parent; theme: home.theme; settings: studioAppearance; target: "home"
        visible: home.values.homeSource !== "builtin"
        active: visible && home.visible
        motionActive: home.ambientRunning
        protectText: false
    }
    Canvas {
        id: field
        objectName: "studioHomeField"
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: parent.height * 0.44
        enabled: false
        visible: home.values.homeSource === "builtin"
        property real phase: 0
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onVisibleChanged: requestPaint()
        Connections { target: studioAppearance; function onChanged() { field.requestPaint() } }
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.strokeStyle = home.theme.studioColor(home.theme.paper ? "#557D84" : "#9EC4BE")
            for (var j = 0; j < 28; j++) {
                var depth = j / 27
                ctx.beginPath()
                for (var i = 0; i <= 48; i++) {
                    var t = i / 48
                    var y = height * (0.38 + depth * 0.32)
                        + Math.sin(t * 5.6 + phase * 0.19 + depth * 2.8) * height * (0.12 + depth * 0.22)
                        + Math.cos(t * 3.2 - depth * 2 + phase * 0.1) * height * 0.08
                        + (homePointer.hovered ? (homePointer.point.position.x / home.width - 0.5) * 18 * depth : 0)
                    if (i === 0) ctx.moveTo(0, y); else ctx.lineTo(t * width, y)
                }
                ctx.globalAlpha = 0.09 + depth * (home.theme.paper ? 0.15 : 0.24)
                ctx.lineWidth = depth > 0.85 ? 1.2 : 0.7
                ctx.stroke()
            }
            ctx.globalAlpha = 1
        }
    }
    Timer {
        objectName: "studioHomeAmbientTimer"
        interval: home.values.motionFps === 15 ? 100 : 66
        running: home.ambientRunning && field.visible; repeat: true
        onTriggered: { field.phase += interval / 1000; field.requestPaint() }
    }
    Timer { id: enterTimer; interval: 180; onTriggered: { home.leaving = false; home.welcome.newRequested() } }
    ColumnLayout {
        id: content
        z: 1
        anchors.fill: parent
        anchors.leftMargin: home.compact ? 28 : home.width * 0.052
        anchors.rightMargin: anchors.leftMargin
        anchors.topMargin: home.height < 700 ? 12 : 26
        anchors.bottomMargin: 24
        spacing: home.height < 700 ? 12 : 22
        opacity: home.leaving ? 0.35 : 1
        Behavior on opacity { NumberAnimation { duration: home.values.reducedMotion ? 0 : 180 } }
        RowLayout {
            id: homeNavigation
            Layout.fillWidth: true; spacing: 8
            Label { text: "音乐创作"; color: home.muted; font.family: home.theme.fontUi; font.pixelSize: 14 }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "studioHelpButton"; theme: home.theme; text: "帮助"; variant: "ghost"; onClicked: home.welcome.helpRequested(this) }
            AppButton { objectName: "studioAppearanceButton"; theme: home.theme; text: "设置"; variant: "ghost"; onClicked: home.welcome.appearanceRequested(this) }
        }
        Item { Layout.preferredHeight: home.height < 700 ? 22 : 28 }
        ColumnLayout {
            id: homeHero
            Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: home.width - 80
            spacing: home.height < 700 ? 14 : 24
            Label {
                objectName: "studioProductHeading"
                text: "Flowing Piano"; color: home.ink
                font.family: "Georgia"; font.pixelSize: Math.min(home.width * 0.064, home.height * 0.10, 88)
                font.letterSpacing: -3
                Layout.alignment: Qt.AlignHCenter
                Accessible.name: "Flowing Piano"
            }
            Label { text: "让灵感，成为音乐。"; color: home.ink; font.family: home.theme.fontUi; font.pixelSize: home.height < 700 ? 18 : 22; Layout.alignment: Qt.AlignHCenter }
            RowLayout {
                Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 8; spacing: 12
                AppButton {
                    id: continueButton
                    objectName: "studioContinueButton"; theme: home.theme; size: "large"; variant: "primary"
                    Layout.preferredHeight: 48; Layout.preferredWidth: 200
                    text: home.hasCurrent ? "继续创作" : "继续最近作品"
                    visible: home.hasCurrent || home.recentEntries.length > 0
                    onClicked: { if (home.hasCurrent) home.welcome.dismissed(); else home.welcome.recentChosen(home.recentEntries[0].path) }
                }
                AppButton {
                    id: createButton; objectName: "studioCreateButton"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 200; Layout.preferredHeight: 48
                    theme: home.theme; variant: home.hasCurrent || home.recentEntries.length > 0 ? "secondary" : "primary"; size: "large"; text: home.hasCurrent || home.recentEntries.length > 0 ? "新建作品" : "开始创作"
                    labelPixelSize: 16
                    enabled: !editor.recycleInProgress && !home.leaving
                    onClicked: {
                        if (home.values.reducedMotion || !home.values.homeAmbient) home.welcome.newRequested()
                        else { home.leaving = true; enterTimer.start() }
                    }
                }
            }
            RowLayout {
                Layout.alignment: Qt.AlignHCenter; spacing: 12
                AppButton { objectName: "studioScoreLabButton"; theme: home.theme; variant: "ghost"; size: "large"; text: "找歌实验室 · 实验"; onClicked: home.welcome.scoreLabRequested() }
                AppButton { objectName: "studioLibraryButton"; theme: home.theme; variant: "ghost"; size: "large"; text: "作品库"; onClicked: home.welcome.libraryRequested() }
                AppButton { objectName: "studioTemplatesButton"; theme: home.theme; variant: "ghost"; size: "large"; text: "示例作品"; onClicked: home.welcome.playRequested() }
                AppButton {
                    objectName: "studioImportButton"; theme: home.theme; variant: "ghost"; text: "导入文件"
                    Layout.alignment: Qt.AlignHCenter
                    onClicked: home.welcome.midiImportRequested()
                }
            }

        }
        Item { Layout.fillHeight: true; Layout.minimumHeight: 12 }
        Rectangle { Layout.fillWidth: true; height: 1; color: home.theme.borderSubtle }
        RowLayout {
            id: homeRecents
            Layout.fillWidth: true; spacing: 16
            Label { text: "最近作品"; color: home.muted; font.pixelSize: 14; Layout.preferredWidth: home.compact ? 88 : 140 }
            Repeater {
                model: home.recentEntries
                delegate: AbstractButton {
                    id: recentButton
                    required property var modelData
                    required property int index
                    objectName: "studioRecent-" + index
                    Layout.fillWidth: true; implicitHeight: 84
                    hoverEnabled: true; activeFocusOnTab: true
                    Accessible.name: "打开作品 " + modelData.name
                    onClicked: home.welcome.recentChosen(modelData.path)
                    background: Rectangle { color: recentButton.hovered || recentButton.activeFocus ? home.theme.surfaceHover : Qt.alpha(home.theme.surface, 0.82); radius: 6 }
                    padding: 12
                    contentItem: RowLayout {
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 6
                            Label { text: recentButton.modelData.name; color: home.ink; font.family: home.theme.fontUi; font.pixelSize: 15; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label { objectName: "studioRecentMetadata-" + recentButton.index; text: home.recentTime(recentButton.modelData.lastOpened); color: home.muted; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                        }
                        Label { text: "↗"; color: home.muted; font.pixelSize: 21; Layout.rightMargin: 10 }
                    }
                }
            }
            Label { visible: home.recentEntries.length === 0; Layout.fillWidth: true; text: "还没有作品"; color: home.muted; font.pixelSize: 15 }
            AppButton { objectName: "studioAiButton"; theme: home.theme; text: "AI 辅助创作"; variant: "ghost"; onClicked: home.welcome.aiRequested() }
        }
        RowLayout {
            id: homeRecovery
            visible: editor.recoveryAvailable; Layout.fillWidth: true; spacing: 12
            Label { text: "发现上次未完成的创作"; color: home.theme.warning; font.pixelSize: 14 }
            AppButton { objectName: "studioRestoreButton"; theme: home.theme; text: "恢复创作"; onClicked: home.welcome.restoreRequested() }
            AppButton { theme: home.theme; text: "放弃恢复"; variant: "ghost"; onClicked: home.welcome.discardRequested() }
        }
    }
    // Protect only the content regions; the surrounding image stays clear.
    Repeater {
        model: [homeNavigation, homeHero, homeRecents, homeRecovery]
        Rectangle {
            required property var modelData
            objectName: "studioHomeTextProtection"
            z: 0.5
            visible: home.customBackground && modelData.visible
            x: content.x + modelData.x - 16; y: content.y + modelData.y - 10
            width: modelData.width + 32; height: modelData.height + 20
            radius: 12
            color: home.theme.appBackground
            opacity: 0.96
        }
    }
}
