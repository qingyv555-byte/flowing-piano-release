import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: launch
    objectName: "welcomeLaunchView"

    required property Theme theme
    property bool recoveryAvailable: false
    property var recoveryInfo: ({})
    property bool firstRun: false
    property bool actionsEnabled: true
    property var recentEntries: []
    property bool introAnimation: true
    property bool introComplete: !introAnimation
    readonly property bool introRunning: introAnimation && !introComplete
    property string returnAnimationMode: "none"
    property bool returnAnimationRunning: false
    property int returnAnimationCount: 0
    readonly property int contentWidth: 1100
    implicitWidth: Math.min(contentWidth, parent ? parent.width : contentWidth)
    implicitHeight: launchColumn.implicitHeight

    signal playRequested()
    signal newRequested()
    signal libraryRequested()
    signal aiRequested()
    signal importRequested()
    signal recentChosen(string path)
    signal restoreRequested()
    signal discardRequested()
    signal appearanceRequested(var focusReturnTarget)
    signal returnAnimationFinished()

    function focusPrimaryAction() { newButton.forceActiveFocus() }
    function focusLibraryAction() { libraryButton.forceActiveFocus() }

    function finishReturnAnimation() {
        returnAnimationRunning = false
        launchColumn.opacity = 1
        launchColumn.y = 0
        titleLabel.opacity = 1
        subtitleLabel.opacity = 1
        playButton.opacity = 1
        newButton.opacity = 1
        libraryButton.opacity = 1
        returnAnimationFinished()
    }

    function playReturnTransition(mode) {
        if (returnAnimationRunning)
            return
        returnAnimationMode = ["full", "fade", "none"].indexOf(mode) !== -1 ? mode : "full"
        returnAnimationCount += 1
        if (returnAnimationMode === "none") {
            finishReturnAnimation()
            return
        }
        returnAnimationRunning = true
        launchColumn.opacity = 0
        launchColumn.y = returnAnimationMode === "full" ? 8 : 0
        titleLabel.opacity = returnAnimationMode === "full" ? 0 : 1
        subtitleLabel.opacity = returnAnimationMode === "full" ? 0 : 1
        playButton.opacity = returnAnimationMode === "full" ? 0 : 1
        newButton.opacity = returnAnimationMode === "full" ? 0 : 1
        libraryButton.opacity = returnAnimationMode === "full" ? 0 : 1
        if (returnAnimationMode === "fade")
            returnFadeSequence.start()
        else
            returnSequence.start()
    }

    Component.onCompleted: {
        if (introAnimation)
            introSequence.start()
    }

    onIntroAnimationChanged: {
        if (!introAnimation)
            introComplete = true
    }

    SequentialAnimation {
        id: introSequence
        loops: 1
        NumberAnimation { target: titleLabel; property: "opacity"; from: 0; to: 1; duration: 320; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 70 }
        NumberAnimation { target: playButton; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 55 }
        NumberAnimation { target: newButton; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 55 }
        NumberAnimation { target: libraryButton; property: "opacity"; from: 0; to: 1; duration: 180; easing.type: Easing.OutCubic }
        onStopped: launch.introComplete = true
    }

    SequentialAnimation {
        id: returnSequence
        ParallelAnimation {
            NumberAnimation {
                target: launchColumn
                property: "opacity"
                from: 0
                to: 1
                duration: 180
                easing.type: Easing.OutCubic
            }
            NumberAnimation {
                target: launchColumn
                property: "y"
                from: 8
                to: 0
                duration: 240
                easing.type: Easing.OutCubic
            }
        }
        PauseAnimation { duration: 50 }
        NumberAnimation { target: titleLabel; property: "opacity"; from: 0; to: 1; duration: 120; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 60 }
        NumberAnimation { target: subtitleLabel; property: "opacity"; from: 0; to: 1; duration: 120; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 40 }
        NumberAnimation { target: playButton; property: "opacity"; from: 0; to: 1; duration: 100; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 25 }
        NumberAnimation { target: newButton; property: "opacity"; from: 0; to: 1; duration: 100; easing.type: Easing.OutCubic }
        PauseAnimation { duration: 25 }
        NumberAnimation { target: libraryButton; property: "opacity"; from: 0; to: 1; duration: 90; easing.type: Easing.OutCubic }
        onStopped: launch.finishReturnAnimation()
    }

    SequentialAnimation {
        id: returnFadeSequence
        NumberAnimation {
            target: launchColumn
            property: "opacity"
            from: 0
            to: 1
            duration: 120
            easing.type: Easing.OutCubic
        }
        onStopped: launch.finishReturnAnimation()
    }

    function relativeTime(iso) {
        if (!iso)
            return ""
        var then = new Date(iso)
        if (isNaN(then.getTime()))
            return ""
        var diff = (Date.now() - then.getTime()) / 1000
        if (diff < 60)
            return "刚刚"
        if (diff < 3600)
            return Math.floor(diff / 60) + " 分钟前"
        if (diff < 86400)
            return Math.floor(diff / 3600) + " 小时前"
        if (diff < 7 * 86400)
            return Math.floor(diff / 86400) + " 天前"
        return then.getFullYear() + "/" + (then.getMonth() + 1) + "/" + then.getDate()
    }

    function statsText(noteCount, durationSeconds) {
        var parts = []
        if (noteCount !== null && noteCount !== undefined)
            parts.push(noteCount + " 个音符")
        if (durationSeconds !== null && durationSeconds !== undefined && durationSeconds > 0)
            parts.push("约 " + Math.round(durationSeconds) + " 秒")
        return parts.join(" · ")
    }

    ColumnLayout {
        id: launchColumn
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(launch.contentWidth, launch.width)
        spacing: 0

        Label {
            id: titleLabel
            text: "AI Piano Studio"
            opacity: launch.introRunning ? 0 : 1
            color: launch.theme.textPrimary
            font.bold: true
            font.pixelSize: 40
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredHeight: 52
            verticalAlignment: Text.AlignVCenter
        }

        Label {
            id: subtitleLabel
            text: "从一段旋律开始，听见自己的作品"
            color: launch.theme.textSecondary
            font.pixelSize: 14
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredHeight: 22
            verticalAlignment: Text.AlignVCenter
        }

        Rectangle {
            id: recoveryBanner
            objectName: "recoveryBanner"
            visible: launch.recoveryAvailable
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 28
            Layout.preferredWidth: Math.min(560, launchColumn.width)
            Layout.minimumHeight: 92
            Layout.preferredHeight: Math.max(92, recoveryRow.implicitHeight + 28)
            radius: launch.theme.radiusL
            color: launch.theme.elevatedSurface
            border.color: launch.theme.borderSubtle

            Rectangle {
                width: 4
                radius: 2
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                height: parent.height - 36
                color: launch.theme.playhead
            }

            RowLayout {
                id: recoveryRow
                anchors.fill: parent
                anchors.margins: 14
                anchors.leftMargin: 26
                spacing: 12

                ColumnLayout {
                    spacing: 3
                    Layout.fillWidth: true

                    Label {
                        text: "继续上次创作"
                        color: launch.theme.playhead
                        font.pixelSize: 12
                        font.bold: true
                    }
                    Label {
                        text: launch.recoveryInfo.name || "未命名工程"
                        color: launch.theme.textPrimary
                        font.pixelSize: 16
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Label {
                        text: {
                            var parts = []
                            var when = launch.relativeTime(launch.recoveryInfo.savedAt)
                            if (when)
                                parts.push("最后编辑 " + when)
                            var stats = launch.statsText(
                                launch.recoveryInfo.noteCount,
                                launch.recoveryInfo.durationSeconds)
                            if (stats)
                                parts.push(stats)
                            return parts.join(" · ")
                        }
                        color: launch.theme.textMuted
                        font.pixelSize: 12
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                AppButton {
                    objectName: "restoreButton"
                    text: "继续创作"
                    variant: "primary"
                    enabled: launch.actionsEnabled
                    theme: launch.theme
                    onClicked: launch.restoreRequested()
                    accessibleName: "恢复未保存的工程"
                }

                AppButton {
                    objectName: "discardRecoveryButton"
                    text: "放弃恢复"
                    variant: "ghost"
                    semanticKind: "danger"
                    enabled: launch.actionsEnabled
                    theme: launch.theme
                    onClicked: discardRecoveryDialog.open()
                    accessibleName: "放弃恢复内容"
                }
            }
        }

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: launch.recoveryAvailable ? 16 : 30
            Layout.preferredWidth: Math.min(launch.contentWidth, launchColumn.width)
            spacing: 12

            AppButton {
                id: newButton
                objectName: "homePrimaryCreate"
                text: "开始创作"
                iconSource: Qt.resolvedUrl("icons/plus.svg")
                variant: "primary"
                height: 48
                enabled: launch.actionsEnabled
                theme: launch.theme
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                opacity: launch.introRunning ? 0 : 1
                KeyNavigation.tab: aiButton
                KeyNavigation.backtab: welcomeThemePicker
                onClicked: launch.newRequested()
                accessibleName: "开始创作新作品"
            }

            AppButton {
                id: aiButton
                objectName: "homeAiCreate"
                text: "AI 帮我创作"
                iconSource: Qt.resolvedUrl("icons/lightbulb.svg")
                variant: "secondary"
                height: 48
                enabled: launch.actionsEnabled
                theme: launch.theme
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                opacity: launch.introRunning ? 0 : 1
                KeyNavigation.tab: libraryButton
                KeyNavigation.backtab: newButton
                onClicked: launch.aiRequested()
                accessibleName: "使用 AI 创建音乐"
            }

            AppButton {
                id: libraryButton
                objectName: "homeOpenProject"
                text: "打开已有作品"
                iconSource: Qt.resolvedUrl("icons/folder.svg")
                variant: "secondary"
                height: 48
                enabled: launch.actionsEnabled
                theme: launch.theme
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                opacity: launch.introRunning ? 0 : 1
                KeyNavigation.tab: importButton
                KeyNavigation.backtab: aiButton
                onClicked: launch.libraryRequested()
                accessibleName: "打开已有作品"
            }
        }

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 10
            Layout.preferredWidth: Math.min(launch.contentWidth, launchColumn.width)
            spacing: 10

            AppButton {
                id: importButton
                objectName: "homeImportMusic"
                text: "导入 MIDI / 音频"
                iconSource: Qt.resolvedUrl("icons/music-2.svg")
                variant: "ghost"
                enabled: launch.actionsEnabled
                theme: launch.theme
                Layout.fillWidth: true
                KeyNavigation.tab: playButton
                KeyNavigation.backtab: libraryButton
                accessibleName: "导入音频或 MIDI 开始编辑"
                onClicked: launch.importRequested()
            }

            AppButton {
                id: playButton
                objectName: "homeBrowseExamples"
                text: "浏览示例"
                iconSource: Qt.resolvedUrl("icons/play.svg")
                variant: "ghost"
                enabled: launch.actionsEnabled
                theme: launch.theme
                Layout.fillWidth: true
                KeyNavigation.tab: welcomeThemePicker
                KeyNavigation.backtab: importButton
                onClicked: launch.playRequested()
                accessibleName: "浏览示例作品"
            }
        }

        Rectangle {
            id: recentProjects
            objectName: "homeRecentProjects"
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 18
            Layout.preferredWidth: Math.min(launch.contentWidth, launchColumn.width)
            Layout.preferredHeight: recentColumn.implicitHeight + 24
            radius: launch.theme.radiusL
            color: launch.theme.surface
            border.width: 1
            border.color: launch.theme.borderSubtle

            ColumnLayout {
                id: recentColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                RowLayout {
                    Layout.fillWidth: true

                    Label {
                        text: "最近工程"
                        color: launch.theme.textPrimary
                        font.family: launch.theme.fontUi
                        font.pixelSize: launch.theme.typePanelTitle
                        font.weight: launch.theme.weightSemibold
                    }

                    Item { Layout.fillWidth: true }

                    AppButton {
                        text: "查看全部"
                        variant: "ghost"
                        theme: launch.theme
                        accessibleName: "查看全部工程"
                        onClicked: launch.libraryRequested()
                    }
                }

                Label {
                    objectName: "homeRecentProjectsEmpty"
                    visible: launch.recentEntries.length === 0
                    text: "暂无最近工程，可新建或导入音乐。"
                    color: launch.theme.textSecondary
                    font.family: launch.theme.fontUi
                    font.pixelSize: launch.theme.typeSmall
                    Layout.fillWidth: true
                    Layout.preferredHeight: 42
                    verticalAlignment: Text.AlignVCenter
                }

                Repeater {
                    model: launch.recentEntries

                    delegate: ProjectRow {
                        required property var modelData
                        objectName: "homeRecentProjectRow"
                        entry: modelData
                        mode: "full"
                        current: false
                        blocked: !launch.actionsEnabled
                        theme: launch.theme
                        Layout.fillWidth: true
                        Layout.preferredHeight: 54
                        onActivated: path => launch.recentChosen(path)
                    }
                }
            }
        }
    }

    AppIconButton {
        id: welcomeThemePicker
        objectName: "welcomeThemePicker"
        anchors.top: parent.top
        anchors.right: parent.right
        theme: launch.theme
        tooltip: "设置"
        iconSource: Qt.resolvedUrl("icons/settings-2.svg")
        accessibleName: "设置"
        KeyNavigation.tab: newButton
        KeyNavigation.backtab: playButton
        onClicked: launch.appearanceRequested(welcomeThemePicker)
    }

    Dialog {
        id: discardRecoveryDialog
        objectName: "discardRecoveryDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 400
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        padding: 18
        background: Rectangle {
            radius: launch.theme.radiusM
            color: launch.theme.elevatedSurface
            border.color: launch.theme.danger
        }
        header: Label {
            text: "放弃恢复内容？"
            color: launch.theme.textPrimary
            font.pixelSize: 18
            font.bold: true
            leftPadding: 18
            rightPadding: 18
            topPadding: 18
        }
        contentItem: Label {
            text: "这会删除未保存的恢复内容，且无法撤销。"
            color: launch.theme.textSecondary
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        footer: Item {
            implicitHeight: 58
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 18
                anchors.rightMargin: 18
                anchors.bottomMargin: 10
                spacing: 8
                Item { Layout.fillWidth: true }
                AppButton {
                    text: "取消"
                    variant: "secondary"
                    theme: launch.theme
                    onClicked: discardRecoveryDialog.close()
                }
                AppButton {
                    objectName: "discardRecoveryConfirmButton"
                    text: "放弃恢复"
                    variant: "danger"
                    enabled: launch.actionsEnabled
                    theme: launch.theme
                    onClicked: {
                        launch.discardRequested()
                        discardRecoveryDialog.close()
                    }
                }
            }
        }
    }
}
