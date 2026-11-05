import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: home
    objectName: "nocturneHome"
    required property var welcome
    readonly property Theme theme: welcome.theme
    readonly property var service: typeof templates !== "undefined" ? templates : null
    readonly property var featured: service && service.templates.length ? service.templates[0] : null
    readonly property var recent: editor.libraryEntries.length ? editor.libraryEntries[0] : null
    readonly property var previewNotes: featured ? service.previewNotes(featured.id) : []
    readonly property bool previewing: service && featured
        && service.activeTemplateId === featured.id && service.previewing
    readonly property bool decorated: scene.backgroundEnabled && !scene.focusModeEnabled
        && home.theme.backgroundSourceFor(scene).toString().length > 0

    function focusPrimaryAction() { createButton.forceActiveFocus() }
    onVisibleChanged: if (visible) Qt.callLater(focusPrimaryAction)

    Rectangle { anchors.fill: parent; color: home.theme.appBackground }
    Image {
        objectName: "nocturneHomeScene"
        anchors.fill: parent
        visible: home.decorated
        source: home.theme.backgroundSourceFor(scene)
        asynchronous: true
        fillMode: Image.PreserveAspectCrop
        opacity: Math.min(1, scene.backgroundOpacity * 2.1)
    }
    RowLayout {
        id: navigation
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        anchors.margins: home.width < 1200 ? 24 : 40
        spacing: 12
        Label {
            text: "AI Piano Studio"; color: home.theme.textPrimary
            font.family: home.theme.fontUi; font.pixelSize: 19
        }
        Label {
            text: "/ NOCTURNE"; color: home.theme.textMuted
            font.pixelSize: 11; font.letterSpacing: 2
        }
        Item { Layout.fillWidth: true }
        AppButton {
            objectName: "nocturneLibraryButton"
            theme: home.theme; variant: "ghost"; text: "作品库"
            onClicked: home.welcome.libraryRequested()
        }
        AppButton {
            objectName: "nocturneImportButton"
            theme: home.theme; variant: "ghost"; text: "导入旋律"
            onClicked: importMenu.popup()
            Menu {
                id: importMenu
                Action { text: "打开工程…"; onTriggered: home.welcome.openRequested() }
                Action { text: "导入 MIDI…"; onTriggered: home.welcome.midiImportRequested() }
                Action { text: "从音频提取…"; onTriggered: home.welcome.importRequested() }
            }
        }
        AppButton {
            objectName: "nocturneBackgroundButton"
            theme: home.theme; variant: "ghost"; text: "背景设置"
            onClicked: home.welcome.appearanceRequested(this)
        }
    }
    Column {
        id: introduction
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.max(navigation.y + navigation.height + 18, home.height * 0.14)
        width: Math.min(home.width - 80, 850)
        spacing: home.height < 720 ? 10 : 16
        Label {
            width: parent.width; horizontalAlignment: Text.AlignHCenter
            text: home.recent ? "欢迎回来 · 你的音乐在这里" : "一处安静的音乐创作空间"
            color: home.theme.textMuted; font.pixelSize: 12; font.letterSpacing: 2
        }
        Label {
            width: parent.width; horizontalAlignment: Text.AlignHCenter
            text: "让灵感落下，\n让旋律生长。"
            color: home.theme.textPrimary; font.family: home.theme.fontUi
            font.pixelSize: Math.min(60, home.height * 0.068)
            font.weight: Font.Light; lineHeight: 1.2
        }
        Label {
            width: parent.width; horizontalAlignment: Text.AlignHCenter
            text: "从一个音符开始。和 AI 一起，写下属于你的宁静。"
            color: home.theme.textSecondary; font.pixelSize: 14
        }
        RowLayout {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 12
            AppButton {
                id: createButton
                objectName: "nocturneCreateButton"
                theme: home.theme; variant: "primary"; size: "large"
                Layout.maximumWidth: Math.min(360, introduction.width * 0.6)
                text: editor.recoveryAvailable ? "恢复未保存的创作 →"
                    : home.recent ? "继续《" + home.recent.name + "》 →" : "开始创作 →"
                enabled: !editor.recycleInProgress
                onClicked: {
                    if (editor.recoveryAvailable) home.welcome.restoreRequested()
                    else if (home.recent) home.welcome.recentChosen(home.recent.path)
                    else home.welcome.newRequested()
                }
            }
            AppButton {
                objectName: "nocturnePreviewButton"
                theme: home.theme; variant: "ghost"; size: "large"
                text: home.previewing ? "停止试听" : "试听示例"
                enabled: !!home.featured && !editor.recycleInProgress
                onClicked: home.service.preview(home.featured.id)
            }
        }
    }
    Rectangle {
        id: musicCard
        objectName: "nocturneMusicCard"
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.max(introduction.y + introduction.height + 20, home.height * 0.53)
        width: Math.min(home.width * 0.76, 1200)
        height: Math.max(100, Math.min(home.height * 0.33, footer.y - y - 24))
        color: home.theme.surface; radius: 16; border.color: home.theme.borderSubtle
        RowLayout {
            id: cardTitle
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 16
            Label {
                text: home.featured ? home.featured.name : "从第一个音符开始"
                color: home.theme.textPrimary; font.pixelSize: 15
                elide: Text.ElideRight; Layout.fillWidth: true
            }
            Label {
                text: home.featured ? home.featured.noteCount + " 音符 · 内置示例" : ""
                color: home.theme.textMuted; font.pixelSize: 12
            }
            AppButton {
                objectName: "nocturneAdaptButton"
                theme: home.theme; variant: "ghost"; text: "打开改编 ↗"
                enabled: !!home.featured && !editor.recycleInProgress
                onClicked: home.service.adapt(home.featured.id)
            }
        }
        Canvas {
            id: scorePreview
            objectName: "nocturneScorePreview"
            anchors.top: cardTitle.bottom; anchors.bottom: parent.bottom
            anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 16
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            Connections { target: home; function onPreviewNotesChanged() { scorePreview.requestPaint() } }
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var notes = home.previewNotes
                if (!notes.length) return
                var low = 127, high = 0, duration = 1
                for (var n of notes) {
                    low = Math.min(low, n.pitch); high = Math.max(high, n.pitch)
                    duration = Math.max(duration, n.start + n.duration)
                }
                var rows = high - low + 5
                var rowHeight = height / rows
                ctx.strokeStyle = home.theme.borderSubtle
                ctx.lineWidth = 0.5
                for (var i = 0; i <= 8; i++) {
                    ctx.beginPath(); ctx.moveTo(i * width / 8, 0)
                    ctx.lineTo(i * width / 8, height); ctx.stroke()
                }
                for (var r = 0; r < rows; r++) {
                    ctx.beginPath(); ctx.moveTo(0, r * rowHeight)
                    ctx.lineTo(width, r * rowHeight); ctx.stroke()
                }
                ctx.fillStyle = home.theme.layerMelody
                for (var note of notes) {
                    ctx.fillRect(note.start / duration * width,
                        (high - note.pitch + 2) * rowHeight,
                        Math.max(2, note.duration / duration * width), Math.max(2, rowHeight * 0.68))
                }
            }
            Rectangle {
                visible: home.previewing
                x: home.featured ? Math.min(parent.width - width,
                    home.service.previewPosition / Math.max(1, home.featured.durationSeconds) * parent.width) : 0
                height: parent.height; width: 1
                color: home.theme.playheadLine
            }
        }
    }
    RowLayout {
        id: footer
        anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
        anchors.margins: 24
        AppButton {
            theme: home.theme; variant: "ghost"; text: "新建作品"
            enabled: !editor.recycleInProgress
            onClicked: home.welcome.newRequested()
        }
        AppButton {
            theme: home.theme; variant: "ghost"; text: "使用帮助"
            onClicked: home.welcome.helpRequested(this)
        }
        Item { Layout.fillWidth: true }
        Label { text: "安静，直到你按下播放。"; color: home.theme.textMuted; font.pixelSize: 12 }
    }
}
