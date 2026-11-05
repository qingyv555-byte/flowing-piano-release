import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: commands
    objectName: "studioCommandBar"
    required property Theme theme
    required property var bar
    component Tool: AppButton {
        theme: commands.theme
        variant: "ghost"
        labelPixelSize: 12
        labelWeight: variant === "primary" ? Font.Medium : commands.theme.weightRegular
        implicitWidth: Math.max(36, normalContentWidth + 18)
        implicitHeight: 36
    }
    component CommandMenu: AppMenu {
        theme: commands.theme
        onAboutToShow: keyboard.suspendInput(objectName, true)
        onClosed: keyboard.suspendInput(objectName, false)
    }

    RowLayout {
        anchors.fill: parent; spacing: 4
        Tool {
            id: homeButton; objectName: "studioHomeNav"
            text: commands.bar.templateMode ? "返回示例" : "首页"
            accessibleName: commands.bar.templateMode ? "返回示例" : "返回首页"
            onClicked: commands.bar.templateMode ? commands.bar.templateBackRequested(homeButton) : commands.bar.homeRequested(homeButton)
        }
        Tool {
            id: fileButton; objectName: "studioFileMenuButton"; text: "文件"
            onClicked: fileMenu.popup(fileButton, 0, fileButton.height)
            CommandMenu {
                id: fileMenu; objectName: "studioFileMenu"
                Action { text: "新建作品…"; onTriggered: commands.bar.newRequested(fileButton) }
                Action { objectName: "studioOpenAction"; text: "打开工程…"; onTriggered: commands.bar.openRequested() }
                Action { objectName: "studioLibraryAction"; text: "作品库"; onTriggered: commands.bar.libraryRequested(fileButton) }
                AppMenuSeparator { theme: commands.theme }
                Action { objectName: "studioImportMidiAction"; text: "导入文件…"; onTriggered: commands.bar.midiImportRequested() }
                Action { objectName: "studioImportAudioAction"; text: "从音频提取…"; onTriggered: commands.bar.audioImportRequested() }
                AppMenuSeparator { theme: commands.theme }
                Action { objectName: "studioSaveCopyAction"; text: "另存副本…"; enabled: !editor.isSaving; onTriggered: commands.bar.saveCopyRequested() }
                Action { objectName: "studioExportMidiAction"; text: "导出 MIDI…"; onTriggered: commands.bar.exportMidiRequested() }
                Action { objectName: "studioExportAudioAction"; text: "导出音频…"; enabled: !commands.bar.audioExporting; onTriggered: commands.bar.exportAudioRequested() }
                Action { objectName: "studioExportProjectAction"; text: "导出工程文件…"; onTriggered: commands.bar.exportProjectRequested() }
                AppMenuSeparator { theme: commands.theme }
                Action { text: "作品改名…"; enabled: editor.currentProjectIsLibrary; onTriggered: commands.bar.renameRequested(fileButton) }
                Action { objectName: "studioScoreLabAction"; text: "找歌实验室…"; enabled: typeof scoreLab !== "undefined" && scoreLab !== null; onTriggered: commands.bar.scoreLabRequested() }
                Action { text: "灵感与陪伴…"; enabled: typeof creative !== "undefined" && creative !== null; onTriggered: creative.open() }
                Action { text: "帮助…"; onTriggered: commands.bar.helpRequested(fileButton) }
            }
        }
        Label {
            id: projectTitle
            objectName: "studioProjectTitle"
            text: editor.currentFileName + (editor.isDirty ? " · 未保存" : " · 已保存")
            color: commands.theme.textSecondary; font.family: commands.theme.fontUi; font.pixelSize: 12
            elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 36
            ToolTip.visible: hoveredTitle.hovered && projectTitle.truncated
            ToolTip.text: editor.currentFileName
            ToolTip.delay: 500
            ToolTip.timeout: 4000
            HoverHandler { id: hoveredTitle }
        }
        AppIconButton { objectName: "studioUndo"; theme: commands.theme; iconSource: Qt.resolvedUrl("icons/undo-2.svg"); tooltip: "撤销 Ctrl+Z"; accessibleName: "撤销"; enabled: editor.canUndo; onClicked: commands.bar.undoRequested() }
        AppIconButton { objectName: "studioRedo"; theme: commands.theme; iconSource: Qt.resolvedUrl("icons/redo-2.svg"); tooltip: "重做 Ctrl+Shift+Z"; accessibleName: "重做"; enabled: editor.canRedo; onClicked: commands.bar.redoRequested() }
        Tool {
            id: snapButton; objectName: "studioSnap"
            text: editor.snapEnabled ? "吸附 1/" + editor.snapDivision : "吸附关"
            onClicked: snapPopup.visible ? snapPopup.close() : snapPopup.open()
            StudioOptionPopup {
                id: snapPopup; objectName: "studioSnapMenu"
                theme: commands.theme; y: snapButton.height + 6
                StudioOptionItem { theme: commands.theme; text: "自由放置"; checked: !editor.snapEnabled; onClicked: editor.setSnapEnabled(false) }
                StudioOptionItem { theme: commands.theme; text: "1/4 拍"; checked: editor.snapEnabled && editor.snapDivision === 4; onClicked: { editor.setSnapDivision(4); editor.setSnapEnabled(true) } }
                StudioOptionItem { theme: commands.theme; text: "1/8 拍"; checked: editor.snapEnabled && editor.snapDivision === 8; onClicked: { editor.setSnapDivision(8); editor.setSnapEnabled(true) } }
                StudioOptionItem { theme: commands.theme; text: "1/16 拍"; checked: editor.snapEnabled && editor.snapDivision === 16; onClicked: { editor.setSnapDivision(16); editor.setSnapEnabled(true) } }
            }
        }
        Tool { objectName: "studioArrangement"; text: "声部"; checkable: true; checked: commands.bar.arrangementVisible; checkedExternallyControlled: true; onClicked: commands.bar.arrangementRequested(!commands.bar.arrangementVisible) }
        Tool { objectName: "studioInspector"; text: "属性"; checkable: true; checked: commands.bar.inspectorVisible; checkedExternallyControlled: true; onClicked: commands.bar.inspectorRequested(!commands.bar.inspectorVisible) }
        Tool {
            id: viewButton; objectName: "studioViewMenuButton"; text: "视图"
            onClicked: viewMenu.visible ? viewMenu.close() : viewMenu.open()
            StudioOptionPopup {
                id: viewMenu; objectName: "studioViewMenu"
                theme: commands.theme; y: viewButton.height + 6
                x: Math.min(0, commands.width - viewButton.x - width)
                StudioOptionItem { theme: commands.theme; text: "跟随播放"; checked: commands.bar.followPlayback; onClicked: commands.bar.followPlaybackRequested(!commands.bar.followPlayback) }
                StudioOptionItem { theme: commands.theme; text: "简化音符"; detail: "合并显示密集音符，音乐内容不变"; checked: commands.bar.noteDisplayMode === "smart"; onClicked: commands.bar.noteDisplayModeRequested(commands.bar.noteDisplayMode === "smart" ? "original" : "smart") }
                StudioOptionItem {
                    theme: commands.theme
                    objectName: "studioOverviewAction"
                    text: "缩略图"
                    checked: experience.miniMapDisplayMode !== "hidden"
                    onClicked: experience.setMiniMapDisplayMode(experience.miniMapDisplayMode === "hidden" ? "visible" : "hidden")
                }
                StudioOptionItem { theme: commands.theme; objectName: "studioPedalLaneAction"; text: "踏板轨迹"; checked: keyboard.showPedalLane; onClicked: keyboard.togglePedalLane() }
                StudioOptionItem {
                    objectName: "studioNoteEmphasisBalanced"
                    theme: commands.theme; text: "音符 · 平衡"
                    checked: experience.noteEmphasisMode === "balanced"
                    onClicked: experience.setNoteEmphasisMode("balanced")
                }
                StudioOptionItem {
                    objectName: "studioNoteEmphasisMelody"
                    theme: commands.theme; text: "音符 · 旋律聚焦"
                    checked: experience.noteEmphasisMode === "melody"
                    onClicked: experience.setNoteEmphasisMode("melody")
                }
                StudioOptionItem {
                    objectName: "studioNoteEmphasisArrangement"
                    theme: commands.theme; text: "音符 · 编曲"
                    checked: experience.noteEmphasisMode === "arrangement"
                    onClicked: experience.setNoteEmphasisMode("arrangement")
                }
            }
        }
        Tool { objectName: "studioScoreLabTopBarButton"; text: "找歌"; enabled: typeof scoreLab !== "undefined" && scoreLab !== null; onClicked: commands.bar.scoreLabRequested() }
        Tool { objectName: "studioAssistant"; text: "AI 助手"; variant: "primary"; checkable: true; checked: commands.bar.assistantVisible; checkedExternallyControlled: true; onClicked: commands.bar.assistantRequested() }
        Tool { objectName: "studioSave"; text: editor.isSaving ? "保存中" : "保存"; variant: "primary"; enabled: !editor.isSaving; onClicked: commands.bar.saveRequested() }
        Tool { id: settingsButton; objectName: "studioSettingsButton"; text: "设置"; accessibleName: "设置"; onClicked: commands.bar.appearanceRequested(settingsButton) }
    }
}
