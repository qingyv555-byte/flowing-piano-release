import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    visible: true
    width: 1440
    height: 900
    minimumWidth: 1000
    minimumHeight: 640
    flags: Qt.Window | Qt.FramelessWindowHint
    title: studioMode && pageState === "launch" ? "Flowing Piano"
        : (studioMode ? "Flowing Piano" : "AI Piano Studio") + " - " + editor.currentFileName + (editor.isDirty ? " *" : "")
    color: theme.appBackground
    opacity: 1
    readonly property bool studioMode: typeof studioUiVariant !== "undefined" && studioUiVariant === "studio"
    property bool projectRailCollapsed: false
    readonly property bool studioAnalysisRequested: studioMode && editorShellLoader.item !== null
        && editorShellLoader.item.feedbackDemand
    readonly property bool nocturneMode: typeof studioUiVariant !== "undefined"
        && studioUiVariant === "nocturne"
    property bool templateEditorMode: false
    property bool inspectorCollapsed: false
    property bool focusMode: studioMode ? false : experience.focusMode
    property string pageState: "launch"
    property bool deferNoncriticalStartup: typeof startupDeferNoncritical !== "undefined"
        && startupDeferNoncritical
    property bool deferredAssistantReady: false
    property bool deferredSupportReady: false
    property bool deferredAppearanceReady: false
    property bool assistantWindowRequested: false
    property bool studioAssistantOpen: false
    property bool studioAssistantDetached: false
    onStudioAssistantOpenChanged: if (!studioAssistantOpen) studioAssistantDetached = false
    property real studioAssistantWidth: 440
    property Item studioAssistantHost: null
    readonly property var creativeController: typeof creative !== "undefined" ? creative : null
    property bool supportDialogRequested: false
    property bool appearancePopupRequested: false
    property bool midiImportDialogRequested: false
    property bool appearanceDockOpen: false
    property real appearanceDockX: 0
    property real appearanceDockTop: 0
    property real appearanceDockHeight: 0
    readonly property int appearanceDockWidth: 360
    property bool audioImportDialogRequested: false
    property var deferredAssistantController: null
    property var deferredSupportController: null
    property var pendingProject: null
    property var pendingProjectExport: null
    readonly property var appTheme: theme
    property Item surfaceFocusReturnTarget: null
    property bool explicitSavePending: false
    property string pendingLibraryExitPage: ""
    property bool libraryExitSavePending: false
    property bool creativeDiscardOnce: false
    signal exitAccepted()
    property string pendingNameOperation: ""
    property string pendingSaveOperation: ""
    property string pendingSpecializedFeedback: ""
    property string pendingAssistantPrompt: ""
    property string pendingAssistantActionId: ""
    property Item pendingHarmonyHelpOpener: null
    property Item harmonyModeOpener: null
    property int pendingHarmonyMeasure: -1
    readonly property bool harmonyAvailable: typeof harmony !== "undefined" && harmony !== null
    readonly property var assistantController: deferNoncriticalStartup
        ? deferredAssistantController
        : (typeof assistant !== "undefined" ? assistant : null)
    readonly property var supportController: deferNoncriticalStartup
        ? deferredSupportController
        : (typeof support !== "undefined" ? support : null)
    readonly property var audioImportController: typeof audioImport !== "undefined" ? audioImport : null
    readonly property var midiImportController: typeof midiImport !== "undefined" ? midiImport : null
    readonly property var editorController: typeof editor !== "undefined" ? editor : null
    readonly property var playbackController: typeof playback !== "undefined" ? playback : null
    readonly property var sceneController: typeof scene !== "undefined" ? scene : null
    readonly property var pianoToneController: typeof pianoTone !== "undefined"
        ? pianoTone : null
    readonly property bool audioImportDialogOpen: audioImportDialogLoader.item !== null
        && audioImportDialogLoader.item.opened
    readonly property bool midiImportDialogOpen: midiImportDialogLoader.item !== null
        && midiImportDialogLoader.item.opened
    property var templateService: typeof templates !== "undefined" ? templates : null
    property bool playingSettingsOpen: false
    readonly property bool editorSurfaceOpen: (scoreLabWindowLoader.item !== null && scoreLabWindowLoader.item.visible)
        || newConfirmDialog.opened
        || playingSettingsOpen
        || unsavedExitDialog.opened
        || creativeExitDialog.opened
        || renameProjectDialog.opened
        || deleteProjectDialog.opened
        || unsyncedPlaybackDialog.opened
        || invalidTimelineDialog.opened
        || (appearancePopupLoader.item !== null && appearancePopupLoader.item.opened)
        || (supportDialogLoader.item !== null && supportDialogLoader.item.opened)
        || (window.studioMode && assistantWindowLoader.item !== null && assistantWindowLoader.item.modalSurfaceOpen)
        || harmonyModePopup.opened
        || harmonyClearConfirmDialog.opened
        || harmonyTemporaryConfirmDialog.opened
        || projectNameDialog.opened
        || renameProjectDialog.opened
        || externalSaveChoiceDialog.opened
        || exportProjectDialog.visible
        || openDialog.visible
        || backgroundFileDialog.visible
        || midiDialog.visible
        || midiImportFileDialog.visible
        || audioImportFileDialog.visible
        || audioDialog.visible
        || technicalDetailsDialog.opened
        || audioImportDialogOpen
        || midiImportDialogOpen
    readonly property bool editorInputEnabled: pageState === "editor"
        && window.active
        && !editorSurfaceOpen
        && !studioFileDrop.confirmationOpen
        && !activeTextInputFocused()

    StudioFileDrop {
        id: studioFileDrop
        anchors.fill: parent
        z: 1000
        theme: window.appTheme
        available: window.studioMode && !editor.isSaving && !editor.recycleInProgress
            && !window.editorSurfaceOpen
        onFileConfirmed: function(source) { window.importSelectedFile(source) }
        onFocusRestoreRequested: {
            if (!window.editorSurfaceOpen)
                window.restoreSurfaceFocus()
        }
        onConfirmationRequested: window.surfaceFocusReturnTarget = window.activeFocusItem
    }

    Connections {
        target: window.editorController
        function onStateChanged() { studioFileDrop.invalidatePending() }
        function onCurrentFileChanged() { studioFileDrop.invalidatePending() }
    }

    onFocusModeChanged: {
        if (!studioMode && experience.focusMode !== focusMode)
            experience.setFocusMode(focusMode)
    }

    Overlay.modal: Rectangle {
        objectName: "modalScrim"
        color: theme.scrim
    }

    function openSurface(surface, focusReturnTarget) {
        surfaceFocusReturnTarget = focusReturnTarget || window.activeFocusItem
        surface.open()
    }

    function restoreSurfaceFocus() {
        var target = surfaceFocusReturnTarget
        surfaceFocusReturnTarget = null
        // A page transition can temporarily hide the opener while the
        // non-modal surface is closing.  Only restore to an item that is
        // still rendered in this window; otherwise focus the active page's
        // stable entry point instead of sending focus to a hidden control.
        if (target && target.visible && target.enabled) {
            target.forceActiveFocus(Qt.PopupFocusReason)
            return
        }
        if (window.pageState === "launch"
                && typeof welcomeOverlay !== "undefined"
                && welcomeOverlay.visible) {
            Qt.callLater(welcomeOverlay.focusForCurrentPage)
        } else if (window.pageState === "templates"
                && typeof templateMusicLibrary !== "undefined"
                && templateMusicLibrary.visible) {
            Qt.callLater(templateMusicLibrary.focusBack)
        }
    }

    function feedbackKind(message) {
        if (/失败|无法|无效|错误/.test(message))
            return "danger"
        if (/未同步|请稍候|没有可|未能/.test(message))
            return "warning"
        if (/^已/.test(message))
            return "success"
        return "info"
    }

    function showFeedback(kind, message, options) {
        feedbackHost.show(kind, message, options || ({}))
    }

    function openTechnicalDetails(title, detail) {
        technicalDetailsDialog.openWithDetails(title, detail)
    }

    function recordDiagnosticError(category) {
        if (supportController !== null
                && typeof supportController.recordErrorCategory === "function")
            supportController.recordErrorCategory(category)
    }

    function exportedFileName(path) {
        var value = String(path || "")
        var separator = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"))
        return separator >= 0 ? value.substring(separator + 1) : value
    }

    function openExportFolder(path) {
        var value = String(path || "")
        var separator = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"))
        var folder = separator >= 0 ? value.substring(0, separator) : value
        if (folder.indexOf("file:") !== 0) {
            folder = "file:///" + folder.replace(/\\/g, "/")
        }
        Qt.openUrlExternally(encodeURI(folder))
    }

    function revealProject(project) {
        if (!project || !project.path)
            return
        window.openExportFolder(project.path)
    }

    function showSpecializedFeedback(kind, message, options, genericMessage) {
        pendingSpecializedFeedback = genericMessage || "*"
        var marker = pendingSpecializedFeedback
        showFeedback(kind, message, options)
        Qt.callLater(function() {
            if (pendingSpecializedFeedback === marker)
                pendingSpecializedFeedback = ""
        })
    }

    function routeGenericFeedback(message) {
        if (pendingSpecializedFeedback.length > 0) {
            var consumed = pendingSpecializedFeedback === "*"
                || message === pendingSpecializedFeedback
            pendingSpecializedFeedback = ""
            if (consumed)
                return
        }
        showFeedback(feedbackKind(message), message)
    }

    function requestNewProject(focusReturnTarget) {
        if (editor.isSaving)
            return
        if (editor.recycleInProgress)
            return
        if (editor.isDirty)
            openSurface(newConfirmDialog, focusReturnTarget || window.activeFocusItem)
        else
            editor.newProject()
    }

    function requestOpenProject() {
        if (!editor.isSaving && !editor.recycleInProgress)
            openDialog.open()
    }

    function importSelectedFile(url) {
        if (editor.isSaving || editor.recycleInProgress)
            return
        var path = url.toString().toLowerCase()
        if (path.endsWith(".json"))
            editor.openProject(url.toString())
        else if (path.endsWith(".mid") || path.endsWith(".midi"))
            window.openMidiImportDialog(url)
        else if (path.endsWith(".wav") || path.endsWith(".mp3"))
            window.openAudioImportDialog(url)
        else
            window.showFeedback("danger", "请选择工程 JSON、MIDI 或 WAV/MP3 音频文件")
    }

    function requestExplicitSave() {
        if (editor.isSaving)
            return
        explicitSavePending = true
        pendingSaveOperation = "save"
        editor.saveCurrent()
    }

    function suggestedProjectName() {
        var value = String(editor.currentFileName || "未命名工程")
        value = value.replace(/\.pianoai\.json$/i, "").replace(/\.json$/i, "")
        return value.length > 0 ? value : "未命名工程"
    }

    function openProjectNameSurface(title, operation, initialName, excludePath, opener) {
        if (opener)
            surfaceFocusReturnTarget = opener
        else if (!surfaceFocusReturnTarget)
            surfaceFocusReturnTarget = window.activeFocusItem
        pendingNameOperation = operation || "save"
        projectNameDialog.openFor(title, initialName || suggestedProjectName(),
            pendingNameOperation, excludePath || "")
    }

    function openRenameProjectSurface(project, opener) {
        pendingProject = project
        surfaceFocusReturnTarget = opener || window.activeFocusItem
        renameProjectDialog.openFor("重命名工程", project.name, "重命名", project.path)
    }

    function requestSaveCopy() {
        if (editor.isSaving)
            return
        explicitSavePending = true
        pendingSaveOperation = "copy"
        openProjectNameSurface("保存副本", "copy", suggestedProjectName() + " 副本", "")
    }

    function clearLibraryExitRequest() {
        pendingLibraryExitPage = ""
        libraryExitSavePending = false
    }

    function completeLibraryExit(targetPage) {
        surfaceFocusReturnTarget = null
        if (targetPage === "__window_close__") {
            window.close()
            return
        }
        if (targetPage === "launch")
            templateEditorMode = false
        setPage(targetPage, targetPage === "library")
    }

    function requestLibraryExit(targetPage, focusReturnTarget) {
        if (editor.isSaving || editor.recycleInProgress) {
            showFeedback("warning", "当前工程正在处理，请稍后再返回工程库")
            return
        }
        if (!editor.isDirty) {
            clearLibraryExitRequest()
            completeLibraryExit(targetPage)
            return
        }
        pendingLibraryExitPage = targetPage
        openSurface(unsavedExitDialog, focusReturnTarget || window.activeFocusItem)
    }

    function saveThenLeaveLibrary() {
        libraryExitSavePending = true
        explicitSavePending = true
        pendingSaveOperation = "save"
        editor.saveCurrent()
    }

    function retryLibraryExitSave(targetPage) {
        if (!targetPage || targetPage.length === 0)
            return
        if (editor.isSaving || editor.recycleInProgress) {
            showFeedback("warning", "当前工程正在处理，请稍后再返回工程库")
            return
        }
        pendingLibraryExitPage = targetPage
        saveThenLeaveLibrary()
    }

    function activeInputConsumesSpace(item) {
        return (item && (item instanceof TextInput || item instanceof TextEdit))
            || (item && item.consumesSpaceKey === true)
    }

    function activeInputConsumesArrows(item) {
        var current = item
        while (current) {
            if (current instanceof TextInput
                    || current instanceof TextEdit
                    || current instanceof Slider
                    || current instanceof SpinBox
                    || current instanceof ComboBox
                    || current instanceof Button
                    || current.consumesArrowKeys === true)
                return true
            current = current.parent
        }
        return false
    }

    function activeTextInputFocused() {
        var item = window.activeFocusItem
        return item && (item instanceof TextInput || item instanceof TextEdit)
    }

    function setPage(nextPage, focusCurrentProject) {
        if (["launch", "templates", "library", "editor", "harmonyHelp"].indexOf(nextPage) === -1)
            return
        if (nextPage === pageState)
            return
        if (pageState === "editor" && nextPage !== "editor") {
            keyboard.releaseAll()
            playback.stopAudition()
            playback.pause()
            if (nextPage !== "harmonyHelp" && window.harmonyAvailable)
                harmony.leaveEditor()
            if (window.activeFocusItem)
                window.activeFocusItem.focus = false
        }
        if (pageState === "templates" || (window.nocturneMode && pageState === "launch"))
            if (templateService)
                templateService.stopPreview()
        if (nextPage === "library")
            welcomeOverlay.focusCurrentProject = focusCurrentProject === true
        if (nextPage === "editor" && pageState !== "harmonyHelp")
            inspectorCollapsed = experience.inspectorPanelMode === "collapsed"
        pageState = nextPage
    }

    function openHarmonyHelp(sectionId, opener) {
        if (typeof harmonyHelp === "undefined")
            return
        pendingHarmonyHelpOpener = opener || window.activeFocusItem
        harmonyHelp.openSection(sectionId)
        if (typeof harmony !== "undefined")
            harmony.windowDeactivated()
        setPage("harmonyHelp")
    }

    function closeHarmonyHelp() {
        var opener = pendingHarmonyHelpOpener
        pendingHarmonyHelpOpener = null
        setPage("editor")
        if (opener && opener.visible && opener.enabled)
            opener.forceActiveFocus(Qt.PopupFocusReason)
    }

    function togglePlayback() {
        if (playback.playing)
            playback.pause()
        else {
            var validation = editor.validateTimeline()
            if (!validation.valid) {
                openSurface(invalidTimelineDialog, window.activeFocusItem)
                return
            }
            if (editor.unsyncedNoteCount > 0) {
                openSurface(unsyncedPlaybackDialog, window.activeFocusItem)
                return
            }
            playback.play()
        }
    }

    function synchronizeAndPlay() {
        editor.synchronizeNow()
        playback.play()
    }

    Component.onCompleted: {
        playback.setDragPreviewMode(experience.dragPreviewMode)
        keyboard.setInputEnabled(editorInputEnabled)
        Qt.callLater(welcomeOverlay.focusForCurrentPage)
    }

    onEditorInputEnabledChanged: keyboard.setInputEnabled(editorInputEnabled)
    function unloadOptionalSurfaces() {
        if (scoreLabWindowLoader.item !== null)
            scoreLabWindowLoader.item.close()
        if (creativeWindowLoader.item !== null)
            creativeWindowLoader.item.close()
        if (creativeOverlayLoader.item !== null)
            creativeOverlayLoader.item.close()
        if (assistantWindowLoader.item !== null)
            assistantWindowLoader.item.close()
        if (supportDialogLoader.item !== null)
            supportDialogLoader.item.close()
        if (appearancePopupLoader.item !== null)
            appearancePopupLoader.item.close()
        if (audioImportDialogLoader.item !== null)
            audioImportDialogLoader.item.close()
        if (midiImportDialogLoader.item !== null)
            midiImportDialogLoader.item.close()
    }
    onClosing: function(close) {
        var discardCreative = window.creativeDiscardOnce
        window.creativeDiscardOnce = false
        if (keyboard.recording)
            keyboard.stopRecording()
        if (window.studioMode && detachedAssistantLoader.item !== null
                && detachedAssistantLoader.item.hasUnsavedConnection) {
            close.accepted = false
            detachedAssistantLoader.item.requestWindowClose(function() { window.close() })
            return
        }
        if (window.studioMode && assistantWindowLoader.item !== null
                && assistantWindowLoader.item.hasUnsavedConnection) {
            close.accepted = false
            assistantWindowLoader.item.requestWindowClose(function() { window.close() })
            return
        }
        if (editor.isSaving || editor.recycleInProgress) {
            close.accepted = false
            window.showFeedback("warning", "工程正在处理，请完成后再退出")
            return
        }
        if (editor.isDirty) {
            close.accepted = false
            window.requestLibraryExit("__window_close__", window.activeFocusItem)
            return
        }
        if (!discardCreative && window.creativeController !== null
                && window.creativeController.hasUnsavedTheme) {
            close.accepted = false
            window.openSurface(creativeExitDialog, window.activeFocusItem)
            return
        }
        window.unloadOptionalSurfaces()
        if (window.assistantController)
            window.assistantController.shutdown()
        window.exitAccepted()
    }
    onActiveChanged: {
        if (!active) {
            keyboard.releaseAll()
            if (window.harmonyAvailable)
                harmony.windowDeactivated()
        }
    }

    Connections {
        target: experience
        function onChanged() { playback.setDragPreviewMode(experience.dragPreviewMode) }
    }

    Theme { id: theme; nocturne: window.nocturneMode; studio: window.studioMode }

    WindowChrome {
        id: windowChrome
        theme: window.appTheme
        hostWindow: window
        integratedToolbar: window.studioMode && window.pageState === "editor"
        productName: window.studioMode ? "Flowing Piano" : window.nocturneMode ? "AI Piano Studio · 夜庭" : "AI Piano Studio"
        contextText: window.studioMode && window.pageState === "launch" ? "" : editor.currentFileName
        statusText: window.studioMode && window.pageState === "launch" ? "" : editor.isDirty ? "未保存" : "已保存"
        statusColor: editor.isDirty ? theme.warning : theme.textMuted
        showContextAction: editor.currentProjectIsLibrary && (!window.studioMode || window.pageState !== "launch")
        onContextActionRequested: opener => window.openRenameProjectSurface(
            { path: editor.currentFilePath, name: editor.currentFileName }, opener
        )
        anchors.fill: parent
    }

    Loader {
        id: assistantWindowLoader
        objectName: "assistantWindowLoader"
        active: !window.deferNoncriticalStartup
            || (window.deferredAssistantReady && window.assistantWindowRequested)
        property bool pendingOpen: false
        onLoaded: {
            if (pendingOpen && item !== null) {
                pendingOpen = false
                if (window.assistantController)
                    window.assistantController.openWindow()
            }
            Qt.callLater(window.applyPendingAssistantPrompt)
        }
        sourceComponent: window.studioMode ? studioAssistantComponent : classicAssistantComponent
    }
    Component {
        id: classicAssistantComponent
            AssistantWindow {
                objectName: "assistantWindow"
                assistant: window.assistantController
            }
    }
    Component {
        id: studioAssistantComponent
        AssistantWorkspace {
            assistant: window.assistantController
            docked: true
            parent: window.studioAssistantHost || window.contentItem
            anchors.fill: parent
            visible: window.studioAssistantOpen && !window.studioAssistantDetached && window.pageState === "editor"
            onCloseRequested: window.studioAssistantOpen = false
            onDetachRequested: window.studioAssistantDetached = true
            onConnectionSettingsRequested: window.openAIServiceSettings()
        }
    }
    Loader {
        id: detachedAssistantLoader
        active: window.studioMode && window.studioAssistantDetached
        sourceComponent: Component {
            AssistantWindow {
                assistant: window.assistantController
                onVisibleChanged: if (!visible && window.studioAssistantDetached) window.studioAssistantDetached = false
            }
        }
        onLoaded: { item.visible = true; item.raise() }
    }
    Connections {
        target: window.assistantController
        function onWindowRequested() {
            if (!window.studioMode) return
            window.assistantWindowRequested = true
            if (window.pageState !== "editor") window.setPage("editor")
            window.studioAssistantOpen = true
        }
        function onWindowCloseRequested() { if (window.studioMode) window.studioAssistantOpen = false }
    }

    Loader {
        id: scoreLabWindowLoader
        active: window.studioMode && typeof scoreLab !== "undefined" && scoreLab !== null
        sourceComponent: ScoreLabWindow { lab: scoreLab }
    }

    Loader {
        id: creativeWindowLoader
        active: window.studioMode && typeof creative !== "undefined" && creative !== null
        sourceComponent: Component {
            CreativeWindow { creative: window.creativeController }
        }
    }

    Loader {
        id: creativeOverlayLoader
        active: window.studioMode && window.creativeController !== null
        sourceComponent: Component {
            CreativeOverlay { creative: window.creativeController }
        }
    }

    function openAssistantWindow() {
        if (window.deferNoncriticalStartup && !window.deferredAssistantReady) {
            // Preserve the user's intent across the deferred-service boundary
            // instead of showing a loading message and silently dropping the
            // click. The Loader will open the window as soon as the controller
            // is attached by the post-window initializer.
            assistantWindowLoader.pendingOpen = true
            window.assistantWindowRequested = true
            window.showFeedback("info", "AI助手正在加载，请稍候")
            return
        }
        assistantWindowLoader.pendingOpen = true
        window.assistantWindowRequested = true
        if (assistantWindowLoader.item !== null) {
            assistantWindowLoader.pendingOpen = false
            if (window.assistantController)
                window.assistantController.openWindow()
        } else if (!window.assistantController) {
            window.showFeedback("error", "AI助手暂不可用")
        }
    }

    function applyPendingAssistantPrompt() {
        if (pendingAssistantPrompt.length === 0 || assistantWindowLoader.item === null)
            return
        var prompt = pendingAssistantPrompt
        var actionId = pendingAssistantActionId
        pendingAssistantPrompt = ""
        pendingAssistantActionId = ""
        if (actionId.length > 0 && assistantWindowLoader.item.prefillQuickAction)
            assistantWindowLoader.item.prefillQuickAction(actionId, prompt)
        else if (assistantWindowLoader.item.prefillMessage)
            assistantWindowLoader.item.prefillMessage(prompt)
    }

    function openAssistantWithPrompt(prompt) {
        pendingAssistantPrompt = String(prompt || "")
        pendingAssistantActionId = ""
        window.openAssistantWindow()
        Qt.callLater(window.applyPendingAssistantPrompt)
    }

    function openAssistantWithQuickAction(actionId, prompt) {
        pendingAssistantActionId = String(actionId || "")
        pendingAssistantPrompt = String(prompt || "")
        window.openAssistantWindow()
        Qt.callLater(window.applyPendingAssistantPrompt)
    }

    function openSupportDialog(focusReturnTarget) {
        if (window.deferNoncriticalStartup && !window.deferredSupportReady) {
            window.showFeedback("info", "帮助功能正在加载，请稍候")
            return
        }
        window.supportDialogRequested = true
        if (supportDialogLoader.item !== null) {
            window.openSurface(supportDialogLoader.item, focusReturnTarget)
            return
        }
        supportDialogLoader.pendingFocusReturnTarget = focusReturnTarget
    }

    function openAppearancePopup(focusReturnTarget) {
        var returnTarget = focusReturnTarget || window.activeFocusItem || window.contentItem
        appearancePopupLoader.pendingFocusReturnTarget = returnTarget
        window.appearancePopupRequested = true
        if (window.deferNoncriticalStartup && !window.deferredAppearanceReady) {
            window.showFeedback("info", "界面设置正在加载，请稍候")
            return
        }
        if (appearancePopupLoader.item !== null) {
            appearancePopupLoader.pendingFocusReturnTarget = null
            window.openSurface(appearancePopupLoader.item, returnTarget)
            return
        }
        appearancePopupLoader.pendingFocusReturnTarget = returnTarget
    }

    function openAIServiceSettings() {
        window.openAssistantWindow()
        Qt.callLater(function() {
            if (assistantWindowLoader.item !== null) assistantWindowLoader.item.openConnectionSettings()
        })
    }

    Loader {
        id: supportDialogLoader
        objectName: "supportDialogLoader"
        active: !window.deferNoncriticalStartup
            || (window.deferredSupportReady && window.supportDialogRequested)
        property var pendingFocusReturnTarget: null
        onLoaded: {
            if (pendingFocusReturnTarget !== null && item !== null) {
                var target = pendingFocusReturnTarget
                pendingFocusReturnTarget = null
                Qt.callLater(function() {
                    window.openSurface(item, target)
                })
            }
        }
        sourceComponent: Component {
            SupportDialog {
                objectName: "supportDialog"
                theme: window.appTheme
                support: window.supportController
                assistant: window.assistantController
            }
        }
    }

    Loader {
        id: audioImportDialogLoader
        objectName: "audioImportDialogLoader"
        active: window.audioImportController !== null
            && (!window.deferNoncriticalStartup || window.audioImportDialogRequested)
        property var pendingAudioImportUrl: null
        onLoaded: {
            if (pendingAudioImportUrl !== null && item !== null) {
                var source = pendingAudioImportUrl
                pendingAudioImportUrl = null
                item.openForFile(source)
            }
        }
        sourceComponent: Component {
            AudioImportDialog {
                objectName: "audioImportDialog"
                theme: window.appTheme
                importer: window.audioImportController
                editor: window.editorController
                playback: window.playbackController
            }
        }
    }

    function openAudioImportDialog(url) {
        audioImportDialogLoader.pendingAudioImportUrl = url
        window.audioImportDialogRequested = true
        if (audioImportDialogLoader.item
                && audioImportDialogLoader.pendingAudioImportUrl !== null) {
            var source = audioImportDialogLoader.pendingAudioImportUrl
            audioImportDialogLoader.pendingAudioImportUrl = null
            audioImportDialogLoader.item.openForFile(source)
        }
    }

    Loader {
        id: midiImportDialogLoader
        objectName: "midiImportDialogLoader"
        active: window.midiImportController !== null && window.midiImportDialogRequested
        property var pendingMidiImportUrl: null
        onLoaded: {
            if (pendingMidiImportUrl !== null && item !== null) {
                var source = pendingMidiImportUrl
                pendingMidiImportUrl = null
                item.openForFile(source)
            }
        }
        sourceComponent: Component {
            MidiImportDialog {
                objectName: "midiImportDialog"
                theme: window.appTheme
                importer: window.midiImportController
                editor: window.editorController
            }
        }
    }

    function openMidiImportDialog(url) {
        midiImportDialogLoader.pendingMidiImportUrl = url
        window.midiImportDialogRequested = true
        if (midiImportDialogLoader.item
                && midiImportDialogLoader.pendingMidiImportUrl !== null) {
            var source = midiImportDialogLoader.pendingMidiImportUrl
            midiImportDialogLoader.pendingMidiImportUrl = null
            midiImportDialogLoader.item.openForFile(source)
        }
    }

    Loader {
        id: appearancePopupLoader
        objectName: "appearancePopupLoader"
        active: !window.deferNoncriticalStartup
            || (window.deferredAppearanceReady && window.appearancePopupRequested)
        property var pendingFocusReturnTarget: null
        property int pendingPage: -1
        onLoaded: {
            if (pendingPage >= 0 && item !== null) {
                item.page = pendingPage
                pendingPage = -1
            }
            if (pendingFocusReturnTarget !== null && item !== null) {
                var target = pendingFocusReturnTarget
                pendingFocusReturnTarget = null
                Qt.callLater(function() {
                    window.openSurface(item, target)
                })
            }
        }
        sourceComponent: window.studioMode ? studioAppearanceComponent : classicAppearanceComponent
    }

    Component {
        id: studioAppearanceComponent
        StudioAppearanceDialog {
            theme: window.appTheme
            settings: studioAppearance
            assistant: window.assistantController
            preferencesAvailable: true
            toneAvailable: playback.soundParametersAvailable || (window.pianoToneController !== null && window.pianoToneController.available)
            recoveryAvailable: editor.recoveryAvailable
            onTonePreviewRequested: value => {
                if (playback.soundParametersAvailable) {
                    playback.previewSoundParameters(preferencesDraft)
                    return
                }
                if (window.pianoToneController === null) return
                try {
                    if (window.pianoToneController.previewValue(value))
                        playback.auditionNoteWithDuration(60, 80, 0.8)
                } finally {
                    window.pianoToneController.restorePreview()
                }
            }
            preferences: ({volume: playback.volume, rate: playback.playbackRate,
                soundParametersAvailable: playback.soundParametersAvailable,
                soundId: playback.projectSoundId, sourceTrim: playback.projectSoundTrim,
                response: playback.projectSoundResponse, reverb: playback.projectSoundReverb, width: playback.projectSoundWidth,
                tone: playback.soundParametersAvailable ? playback.projectSoundBrightness : (window.pianoToneController !== null ? window.pianoToneController.value : 0.5),
                noteLength: experience.newNoteLengthBeats, velocity: experience.newNoteVelocity,
                previewLength: experience.pianoPreviewLengthBeats, dragPreview: experience.dragPreviewMode,
                autosave: savePreferences.autosaveEnabled, saveDelay: savePreferences.autosaveDelaySeconds})
            onPreferencesApplied: values => {
                playback.setVolume(values.volume)
                playback.setPlaybackRate(values.rate)
                if (values.soundParametersAvailable)
                    playback.applySoundSettings(values.soundId, values)
                else if (window.pianoToneController !== null && window.pianoToneController.available)
                    window.pianoToneController.setValue(values.tone)
                experience.setNewNoteLengthBeats(values.noteLength)
                experience.setNewNoteVelocity(values.velocity)
                experience.setPianoPreviewLengthBeats(values.previewLength)
                experience.setDragPreviewMode(values.dragPreview)
                savePreferences.setAutosaveEnabled(values.autosave)
                savePreferences.setAutosaveDelaySeconds(values.saveDelay)
            }
            onRecoveryRequested: window.requestLibraryExit("launch", null)
            onClosed: { playback.restoreSoundParameterPreview(); window.restoreSurfaceFocus() }
        }
    }
    Component {
        id: classicAppearanceComponent
            BackgroundSettingsDialog {
                theme: window.appTheme
                scene: window.sceneController
                onUploadRequested: backgroundFileDialog.open()
                onClosed: window.restoreSurfaceFocus()
            }
    }

    component DarkButton: Rectangle {
        id: control
        property string text: ""
        property bool highlighted: false
        property bool checkable: false
        property bool checked: false
        property bool compact: false
        property font font: Qt.font({ pixelSize: 12 })
        property bool hovered: mouseArea.containsMouse
        signal clicked()
        signal toggled(bool checked)
        implicitWidth: compact ? 30 : Math.max(74, buttonText.implicitWidth + 22)
        implicitHeight: 30
        radius: 7
        color: !enabled ? theme.surface : (highlighted || checked ? theme.accentPrimary : (hovered ? theme.surfaceHover : theme.elevatedSurface))
        border.color: highlighted || checked ? theme.accentPrimary : theme.borderStrong
        opacity: enabled ? 1 : 0.5
        Text {
            id: buttonText
            anchors.centerIn: parent
            text: control.text
            color: control.highlighted || control.checked ? theme.textOnAccent : theme.textPrimary
            font.family: control.font.family
            font.pixelSize: control.compact ? 15 : control.font.pixelSize
            font.bold: control.highlighted || control.checked
            elide: Text.ElideRight
        }
        MouseArea {
            id: mouseArea
            anchors.fill: parent
            hoverEnabled: true
            enabled: control.enabled
            onClicked: {
                if (control.checkable) {
                    control.checked = !control.checked
                    control.toggled(control.checked)
                }
                control.clicked()
            }
        }
    }

    component DarkComboBox: ComboBox {
        id: control
        implicitWidth: 70
        implicitHeight: 30
        contentItem: Text {
            leftPadding: 10
            rightPadding: 24
            text: control.displayText
            color: control.enabled ? theme.textPrimary : theme.textMuted
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 7
            color: control.hovered ? theme.surfaceHover : theme.elevatedSurface
            border.color: theme.borderStrong
        }
        indicator: Text {
            x: control.width - width - 9
            y: (control.height - height) / 2
            text: "⌄"
            color: theme.textSecondary
            font.pixelSize: 14
        }
        delegate: ItemDelegate {
            width: control.width
            contentItem: Text { text: modelData; color: theme.textPrimary; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: parent.highlighted ? theme.surfaceHover : theme.elevatedSurface }
        }
        popup: Popup {
            y: control.height + 4
            width: control.width
            implicitHeight: contentItem.implicitHeight
            padding: 2
            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: control.popup.visible ? control.delegateModel : null }
            background: Rectangle { color: theme.elevatedSurface; border.color: theme.borderStrong; radius: 7 }
        }
    }

    component DarkSpinBox: SpinBox {
        id: control
        implicitWidth: 76
        implicitHeight: 30
        contentItem: TextInput {
            z: 2
            text: control.displayText
            color: theme.textPrimary
            font.pixelSize: 12
            font.family: theme.numberFont
            horizontalAlignment: Qt.AlignHCenter
            verticalAlignment: Qt.AlignVCenter
            readOnly: !control.editable
            validator: control.validator
            inputMethodHints: control.inputMethodHints
            selectByMouse: true
            onEditingFinished: control.value = control.valueFromText(text, control.locale)
        }
        background: Rectangle { radius: 7; color: control.hovered ? theme.surfaceHover : theme.elevatedSurface; border.color: theme.borderStrong }
        up.indicator: Rectangle {
            x: control.width - width; height: control.height / 2; width: 20
            color: control.up.pressed ? theme.surfaceHover : "transparent"
            Text { anchors.centerIn: parent; text: "▴"; color: theme.textSecondary; font.pixelSize: 10 }
        }
        down.indicator: Rectangle {
            x: control.width - width; y: control.height / 2; height: control.height / 2; width: 20
            color: control.down.pressed ? theme.surfaceHover : "transparent"
            Text { anchors.centerIn: parent; text: "▾"; color: theme.textSecondary; font.pixelSize: 10 }
        }
    }

    readonly property color appTextColor: theme.text
    palette.window: theme.canvas
    palette.windowText: theme.text
    palette.text: theme.text
    palette.button: theme.control
    palette.buttonText: theme.text
    palette.base: theme.field
    palette.placeholderText: theme.muted
    palette.highlight: theme.accent
    palette.highlightedText: theme.accentText

    Component {
        id: editorShellComponent

    Item {
        id: editorShell
        objectName: "editorShell"
        property var shellTheme: window.appTheme
        readonly property bool feedbackDemand: editorTimeline.analysisRequested
        property bool workspaceStateReady: false
        anchors.fill: parent
        visible: window.pageState === "editor"
        enabled: visible
        focus: visible

    Rectangle {
        objectName: "editorProtectionLayer"
        anchors.fill: parent
        z: 1
        color: window.appTheme.sceneProtection
    }

    WorkspaceShell {
        id: workspaceShell
        panelsAvailableInFocus: window.studioMode
        availableWidth: editorShell.width
        minimumCanvasWidth: window.appTheme.minimumPianoRollWidth
        leftExpandedWidth: window.studioMode ? 0 : window.appTheme.projectRailWidth
        leftIconWidth: window.studioMode ? 0 : window.appTheme.projectRailCollapsedWidth
        inspectorExpandedWidth: window.appTheme.inspectorWidth
        width: 0
        height: 0

        Component.onCompleted: {
            leftContentOpen = !window.studioMode && experience.sidebarMode === "expanded"
                && !window.projectRailCollapsed
            inspectorOpen = experience.inspectorPanelMode !== "collapsed"
            arrangementOpen = experience.arrangementVisible
            editorShell.workspaceStateReady = true
            if (window.focusMode)
                enterFocus(true)
            else if (leftContentOpen && inspectorOpen && !panelsFit(true, true))
                requestInspector(true)
        }

        onLeftContentResolved: open => {
            if (!editorShell.workspaceStateReady || workspaceShell.focusTransitioning)
                return
            window.projectRailCollapsed = !open
            experience.setSidebarMode(open ? "expanded" : "icon")
        }
        onInspectorResolved: open => {
            if (!editorShell.workspaceStateReady || workspaceShell.focusTransitioning)
                return
            window.inspectorCollapsed = !open
            experience.setInspectorPanelMode(open ? "expanded" : "collapsed")
        }
        onArrangementResolved: open => {
            if (editorShell.workspaceStateReady && !workspaceShell.focusTransitioning)
                experience.setArrangementVisible(open)
        }
        onFocusResolved: enabled => {
            if (editorShell.workspaceStateReady)
                experience.setFocusMode(enabled)
        }
    }

    Connections {
        target: window
        function onStudioAssistantOpenChanged() {
            if (window.studioMode && window.studioAssistantOpen) {
                workspaceShell.requestInspector(false)
                workspaceShell.requestArrangement(false)
            }
        }
    }
    Connections {
        target: experience

        function onChanged() {
            if (!editorShell.workspaceStateReady)
                return
            var requestedFocus = window.studioMode ? false : experience.focusMode
            if (workspaceShell.focusMode !== requestedFocus) {
                workspaceShell.enterFocus(requestedFocus)
                return
            }
            if (workspaceShell.focusMode)
                return
            var leftOpen = !window.studioMode && experience.sidebarMode === "expanded"
            if (workspaceShell.leftContentOpen !== leftOpen)
                workspaceShell.requestLeftContent(leftOpen)
            var inspectorOpen = experience.inspectorPanelMode !== "collapsed"
            if (workspaceShell.inspectorOpen !== inspectorOpen)
                workspaceShell.requestInspector(inspectorOpen)
            if (workspaceShell.arrangementOpen !== experience.arrangementVisible)
                workspaceShell.requestArrangement(experience.arrangementVisible)
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.bottomMargin: bottomTransportBar.height
        z: 2
        spacing: 0

        ProjectRail {
            id: projectRail
            theme: window.appTheme
            entries: editor.libraryEntries
            editorBridge: window.editorController
            currentPath: editor.currentFilePath
            isSaving: editor.isSaving
            recycleInProgress: editor.recycleInProgress
            templateMode: window.templateEditorMode
            displayMode: workspaceShell.leftContentOpen ? "expanded" : "icon"
            focusMode: window.focusMode
            compactForViewport: false
            visible: !window.studioMode && workspaceShell.leftLayoutWidth > 0
            Layout.fillHeight: true
            // ProjectRail may become a template-specific icon rail while the
            // workspace preference remains expanded. Follow the rendered rail
            // width so the closed content never leaves a blank 224 px column.
            Layout.preferredWidth: projectRail.railWidth
            Layout.minimumWidth: projectRail.railWidth
            Layout.maximumWidth: projectRail.railWidth

            onCollapseRequested: value => workspaceShell.requestLeftContent(!value)
            onDisplayModeRequested: mode => {
                workspaceShell.requestLeftContent(mode === "expanded")
            }
            onLibraryRequested: focusReturnTarget => window.requestLibraryExit(
                projectRail.templateMode ? "launch" : "library", focusReturnTarget
            )
            onNewRequested: focusReturnTarget => window.requestNewProject(focusReturnTarget)
            onOpenRequested: window.requestOpenProject()
            onProjectChosen: path => {
                if (!editor.isSaving && !editor.recycleInProgress) {
                    window.templateEditorMode = false
                    editor.openProject(path)
                }
            }
            onRenameRequested: function(project, focusReturnTarget) {
                window.openRenameProjectSurface(project, focusReturnTarget)
            }
            onDeleteRequested: function(project, focusReturnTarget) {
                window.pendingProject = project
                window.openSurface(deleteProjectDialog, focusReturnTarget)
            }
            onCopyRequested: project => editor.copyProject(project.path)
            onFavoriteRequested: project => editor.toggleProjectFavorite(project.path)
            onRevealRequested: project => window.revealProject(project)
            onExportRequested: project => {
                window.pendingProjectExport = project
                midiDialog.open()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

        EditorTopBar {
            onScoreLabRequested: { if (typeof scoreLab !== "undefined" && scoreLab !== null) scoreLab.open() }
            onPlayingSettingsRequested: playingSettingsDialog.open()
            id: editorTopBar
            studioCommandHost: window.studioMode ? windowChrome.toolbarHost : null
            assistantVisible: window.studioAssistantOpen
            visible: !window.studioMode
            Layout.preferredHeight: window.studioMode ? 0 : implicitHeight
            theme: window.appTheme
            templateMode: window.templateEditorMode
            templateName: editor.currentFileName
            focusMode: window.focusMode
            noteDisplayMode: experience.compositeDisplayMode
            Layout.fillWidth: true
            followPlayback: editorTimeline.followPlayback
            audioExporting: playback.audioExporting
            harmonyAvailable: window.harmonyAvailable
            harmonyMode: window.harmonyAvailable ? harmony.mode : "off"
            arrangementVisible: workspaceShell.arrangementOpen
            inspectorVisible: workspaceShell.inspectorOpen
            autosaveEnabled: savePreferences.autosaveEnabled
            contextualActionsVisible: false
            onAppearanceRequested: focusReturnTarget =>
                window.openAppearancePopup(focusReturnTarget)
            onUndoRequested: editor.undo()
            onRedoRequested: editor.redo()
            onOpenRequested: window.requestOpenProject()
            onNewRequested: opener => window.requestNewProject(opener)
            onRenameRequested: opener => window.openRenameProjectSurface({ path: editor.currentFilePath, name: editor.currentFileName }, opener)
            onHelpRequested: opener => window.openSupportDialog(opener)
            onMidiImportRequested: midiImportFileDialog.open()
            onAudioImportRequested: audioImportFileDialog.open()
            onExportMidiRequested: midiDialog.open()
            onExportAudioRequested: audioDialog.open()
            onSaveRequested: window.requestExplicitSave()
            onSaveCopyRequested: window.requestSaveCopy()
            onExportProjectRequested: exportProjectDialog.open()
            onFollowPlaybackRequested: function(value) {
                editorTimeline.setFollowPlayback(value)
            }
            onTemplateBackRequested: focusReturnTarget =>
                window.requestLibraryExit("templates", focusReturnTarget)
            onHarmonyModeRequested: focusReturnTarget => {
                if (window.harmonyAvailable) {
                    window.harmonyModeOpener = focusReturnTarget
                    harmonyModePopup.opener = focusReturnTarget
                    window.openSurface(harmonyModePopup, focusReturnTarget)
                }
            }
            onAssistantRequested: {
                if (window.studioMode) {
                    if (window.studioAssistantOpen) { window.studioAssistantOpen = false; return }
                    workspaceShell.requestInspector(false)
                    workspaceShell.requestArrangement(false)
                }
                window.openAssistantWindow()
            }
            onFocusModeRequested: function(value) {
                workspaceShell.enterFocus(value)
            }
            onArrangementRequested: value => {
                if (window.studioMode && value) window.studioAssistantOpen = false
                workspaceShell.requestArrangement(value)
            }
            onInspectorRequested: value => {
                if (window.studioMode && value) window.studioAssistantOpen = false
                workspaceShell.requestInspector(value)
            }
            onHomeRequested: focusReturnTarget => window.requestLibraryExit(
                "launch", focusReturnTarget)
            onLibraryRequested: focusReturnTarget => window.requestLibraryExit(
                "library", focusReturnTarget)
            onNoteDisplayModeRequested: function(mode) {
                experience.setCompositeDisplayMode(mode)
            }
            onAutosaveRequested: enabled => savePreferences.setAutosaveEnabled(enabled)
        }

        EditorTips {
            id: editorTips
            objectName: "editorTips"
            theme: window.appTheme
            visible: !window.focusMode
                && experience.mode === "beginner" && experience.firstRunCompleted
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            Layout.minimumHeight: 0
        }

        // Beginner actions retain one stable teaching rail. Advanced selection
        // actions are reparented into the Timeline overlay, so selecting a note
        // cannot change the command bar or Piano Roll geometry.
        Item {
            id: editorActionRail
            objectName: "editorActionRail"
            readonly property bool hasActionModule:
                experience.mode === "beginner"
            readonly property real railHeight:
                beginnerActionBar.implicitHeight
            implicitHeight: hasActionModule && !window.focusMode ? railHeight : 0
            visible: hasActionModule && !window.focusMode
            height: hasActionModule && !window.focusMode ? railHeight : 0
            Layout.fillWidth: true
            Layout.preferredHeight: hasActionModule && !window.focusMode ? railHeight : 0
            Layout.minimumHeight: 0
            Layout.maximumHeight: hasActionModule && !window.focusMode ? railHeight : 0
            // A collapsed rail has zero layout height, but its small reopen
            // arrow is allowed to render just above the ruler.
            clip: false

            SelectionActionBar {
                id: selectionActionBar
                theme: window.appTheme
                editor: window.editorController
                playback: window.playbackController
                parent: editorTimeline
                suppressAutoExpand: window.studioMode && editorTimeline.creatingNote
                x: editorTimeline.keyWidth + window.appTheme.space3
                y: editorTimeline.rulerHeight + window.appTheme.space2
                width: Math.min(implicitWidth,
                    Math.max(0, editorTimeline.width - x - window.appTheme.space3))
                height: implicitHeight
                visible: hasSelection && (window.studioMode || window.nocturneMode || !window.focusMode)
                enabled: visible
                z: 20
            }

            BeginnerActionBar {
                id: beginnerActionBar
                objectName: "beginnerActionBar"
                theme: window.appTheme
                anchors.fill: parent
                visible: experience.mode === "beginner"
                onActionRequested: function(actionId, prompt) {
                    window.openAssistantWithQuickAction(actionId, prompt)
                }
            }
        }

        RowLayout {
            id: editorWorkspaceRow
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                HarmonySuggestionLane {
                    id: harmonySuggestionLane
                    theme: window.appTheme
                    model: typeof harmonyPreview !== "undefined" ? harmonyPreview : null
                    viewStartSeconds: editorTimeline.viewStartSeconds
                    pixelsPerSecond: editorTimeline.pixelsPerSecond
                    keyWidth: editorTimeline.keyWidth
                    playheadSeconds: editorTimeline.cursorSeconds
                    statusText: window.harmonyAvailable
                        ? harmony.previewStatusText : "先画几个旋律音符，我才能推荐和弦。"
                    analysisPending: window.harmonyAvailable && harmony.analysisPending
                    Layout.fillWidth: true
                    Layout.preferredHeight: window.harmonyAvailable
                        && harmony.mode === "preview" && (window.studioMode || !window.focusMode) ? 60 : 0
                    onCandidateRequested: harmony.selectCandidate(measureIndex, candidateIndex)
                    onDismissRequested: harmony.dismissMeasurePreview(measureIndex)
                    onAddRequested: harmony.requestManualAdd(measureIndex)
                    onWhyRequested: harmony.requestExplanation(measureIndex)
                    onRegenerateRequested: harmony.regenerateMeasure(measureIndex)
                }

                Timeline {
                    id: editorTimeline
                    objectName: "timeline"
                    theme: window.appTheme
                    sceneSettings: scene
                    visualizer: typeof audioVisualizer !== "undefined" ? audioVisualizer : null
                    proposalSource: window.assistantController
                    dockMiniMapToBottomRight: inspector.effectivePanelMode === "collapsed"
                    hideEmptyOverview: window.studioMode
                    focusMode: window.focusMode && !window.studioMode
                    displayMode: experience.compositeDisplayMode
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onEditorFocusRequested: {
                        projectRail.clearSearchFocus()
                        editorTimeline.forceActiveFocus(Qt.MouseFocusReason)
                    }
                }

                ArrangementPanel {
                    id: arrangementPanel
                    theme: window.appTheme
                    editorBridge: editor
                    model: editor.layerModel
                    noteItemsByLayer: visible ? JSON.parse(editor.noteModel.overviewJson) : ({})
                    activeLayerId: editor.activeLayerId
                    pixelsPerSecond: editorTimeline.pixelsPerSecond
                    contentX: editorTimeline.viewStartSeconds
                        * editorTimeline.pixelsPerSecond
                    durationSeconds: editor.estimatedDurationSeconds
                    visibleSeconds: editorTimeline.visibleSeconds
                    panelHeight: experience.arrangementHeight
                    layerViewMode: experience.layerViewMode
                    visible: workspaceShell.arrangementOpen && (window.studioMode || !window.focusMode)
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? effectivePanelHeight : 0
                    Layout.minimumHeight: visible ? effectivePanelHeight : 0
                    Layout.maximumHeight: visible ? effectivePanelHeight : 0
                    height: visible ? effectivePanelHeight : 0
                    onLayerActivated: function(layerId) {
                        editor.setActiveLayer(layerId)
                        inspector.showLayerContext()
                        workspaceShell.requestInspector(true)
                    }
                    onContentXRequested: value => {
                        editorTimeline.viewStartSeconds = Math.max(
                            0, value / editorTimeline.pixelsPerSecond)
                    }
                    onHeightCommitted: value => experience.setArrangementHeight(value)
                    onLayerViewModeRequested: mode => experience.setLayerViewMode(mode)
                }
            }
            Inspector {
                id: inspector
                objectName: "inspector"
                theme: window.appTheme
                panelMode: workspaceShell.inspectorOpen ? "expanded" : "collapsed"
                focusMode: window.focusMode && !window.studioMode
                visible: workspaceShell.inspectorLayoutWidth > 0
                Layout.fillHeight: true
                Layout.preferredWidth: workspaceShell.inspectorLayoutWidth
                Layout.minimumWidth: workspaceShell.inspectorLayoutWidth
                Layout.maximumWidth: workspaceShell.inspectorLayoutWidth
                onCollapsedChanged: window.inspectorCollapsed = collapsed
                onPanelModeRequested: mode => {
                    workspaceShell.requestInspector(mode !== "collapsed")
                }
            }
            Rectangle {
                id: studioAssistantHost
                objectName: "studioAssistantHost"
                visible: window.studioMode && window.studioAssistantOpen && !window.studioAssistantDetached
                Layout.fillHeight: true
                Layout.preferredWidth: Math.max(360, Math.min(560, window.studioAssistantWidth))
                Layout.minimumWidth: Layout.preferredWidth
                Layout.maximumWidth: Layout.preferredWidth
                color: window.appTheme.popupBackground
                border.color: window.appTheme.borderSubtle
                MouseArea {
                    objectName: "assistantSidebarResizeHandle"
                    anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.leftMargin: -4; width: 8; z: 10
                    cursorShape: Qt.SplitHCursor
                    property real startX: 0
                    property real startWidth: 440
                    onPressed: mouse => { forceActiveFocus(Qt.MouseFocusReason); startX = mapToItem(window.contentItem, mouse.x, mouse.y).x; startWidth = window.studioAssistantWidth }
                    onPositionChanged: mouse => {
                        if (pressed) window.studioAssistantWidth = Math.max(360, Math.min(560,
                            startWidth + startX - mapToItem(window.contentItem, mouse.x, mouse.y).x))
                    }
                    activeFocusOnTab: true
                    Accessible.name: "调整 AI 助手侧栏宽度"
                    Keys.onShortcutOverride: event => {
                        if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) event.accepted = true
                    }
                    Keys.onLeftPressed: window.studioAssistantWidth = Math.min(560, window.studioAssistantWidth + 20)
                    Keys.onRightPressed: window.studioAssistantWidth = Math.max(360, window.studioAssistantWidth - 20)
                }
                Component.onCompleted: {
                    window.studioAssistantHost = studioAssistantHost
                    if (window.studioAssistantOpen) {
                        workspaceShell.requestInspector(false)
                        workspaceShell.requestArrangement(false)
                    }
                }
                Component.onDestruction: if (window.studioAssistantHost === studioAssistantHost) window.studioAssistantHost = null
            }

        }

        }

    }

    // This affordance is deliberately outside the main RowLayout. A
    // collapsed action rail must release all of its layout height while
    // retaining one small, discoverable reopen target above the ruler.
    AppButton {
        id: editorActionRailCollapsedToggle
        objectName: "editorActionRailCollapsedToggle"
        theme: window.appTheme
        variant: "ghost"
        implicitWidth: 28
        implicitHeight: 28
        text: "⌄"
        readonly property bool shouldShow: (window.studioMode || !window.focusMode)
            && ((editor.selectedCount > 0 && !selectionActionBar.expanded)
                || (editor.selectedCount === 0
                    && experience.mode === "beginner"
                    && !beginnerActionBar.expanded))
        visible: shouldShow
        opacity: 1
        enabled: shouldShow
        parent: editorTimeline
        z: 4
        // Keep the reopen affordance in the ruler's left prefix, immediately
        // before the 0-second marker, so it never covers the inspector.
        x: Math.max(0, editorTimeline.keyWidth - width - window.appTheme.space1)
        y: 0
        accessibleName: editor.selectedCount > 0
            ? "展开选中音符操作" : "展开快速开始"
        Accessible.description: "折叠状态不占用编辑区空间"
        onClicked: {
            if (editor.selectedCount > 0)
                selectionActionBar.expanded = true
            else if (experience.mode === "beginner")
                beginnerActionBar.expanded = true
        }
    }

    PlayingSettingsDialog {
        id: playingSettingsDialog; theme: window.appTheme
        onOpened: window.playingSettingsOpen = true
        onClosed: window.playingSettingsOpen = false
    }

    TransportBar {
        id: bottomTransportBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: implicitHeight
        z: 5
        theme: window.appTheme
        playing: playback.playing
        preparing: playback.preparing
        auditionPlaying: playback.selectionAudition.playing
        recording: keyboard.recording
        positionSeconds: keyboard.recording ? keyboard.recordCursorSeconds : playback.positionSeconds
        totalSeconds: playback.totalSeconds
        bpm: editor.bpm
        rhythmBridge: editorTopBar
        pianoToneValue: window.pianoToneController !== null
            ? window.pianoToneController.value : 0.5
        pianoToneAvailable: window.pianoToneController !== null
            && window.pianoToneController.available
        pianoToneStatusMessage: window.pianoToneController !== null
            ? (window.pianoToneController.statusMessage || "") : "钢琴音色不可用"
        sfzPreviewAvailable: window.studioMode && typeof sfzPreview !== "undefined"
            && sfzPreview !== null && sfzPreview.available
        sfzPreviewBusy: typeof sfzPreview !== "undefined"
            && sfzPreview !== null && sfzPreview.busy
        sfzPreviewStatusText: typeof sfzPreview !== "undefined"
            && sfzPreview !== null ? sfzPreview.statusMessage : ""
        volume: playback.volume
        followPlayback: editorTimeline.followPlayback
        onFollowPlaybackEdited: value => editorTimeline.setFollowPlayback(value)
        playbackRate: playback.playbackRate
        statusContextText: (editor.selectedCount > 0
            ? "已选 " + editor.selectedCount : "未选择")
            + (editor.snapEnabled
                ? " · 1/" + editor.snapDivision + " · 吸附开" : " · 吸附关")
        statusCompactContextText: editor.selectedCount > 0
            ? "已选 " + editor.selectedCount : (window.appTheme.studio ? "" : "未选择")
        statusTooltipContextText: (editor.selectedCount > 0
            ? "已选择 " + editor.selectedCount + " 个音符"
            : "点击空白处创建音符")
            + " · 网格 " + editor.beatsPerBar + "/" + editor.beatUnit
            + (editor.snapEnabled
                ? " · 吸附 1/" + editor.snapDivision + " 拍" : " · 吸附关")
        statusTemporaryKind: "info"
        statusSyncState: editor.syncState
        statusUnsyncedCount: editor.unsyncedNoteCount
        statusDurationSeconds: editor.playbackDurationSeconds
        statusEstimatedDurationSeconds: editor.estimatedDurationSeconds
        statusSaving: editor.isSaving
        opacity: !window.studioMode && window.focusMode ? 0.68 : 1.0
        onPlayPauseRequested: window.togglePlayback()
        onStopRequested: playback.stop()
        onRecordRequested: keyboard.toggleRecording()
        onPlayingSettingsRequested: playingSettingsDialog.open()
        onVolumeEdited: value => playback.setVolume(value)
        onPlaybackRateEdited: value => playback.setPlaybackRate(value)
        onPianoToneEdited: value => {
            if (window.pianoToneController !== null)
                window.pianoToneController.setValue(value)
        }
        onSfzPreviewRequested: {
            if (typeof sfzPreview !== "undefined" && sfzPreview !== null)
                sfzPreview.audition()
        }
        onSfzPreviewStopped: {
            if (typeof sfzPreview !== "undefined" && sfzPreview !== null)
                sfzPreview.stop()
        }
    }

    // Keep a single, predictable wheel owner for the Inspector even when a
    // scrolled field's visual bounds have moved above the Flickable viewport.
    // The x guard prevents this boundary handler from stealing piano-roll or
    // transport scrolling.
    WheelHandler {
        id: inspectorBoundaryWheelHandler
        objectName: "inspectorBoundaryWheelHandler"
        enabled: window.pageState === "editor"
            && inspector.effectivePanelMode === "expanded"

        onWheel: wheel => {
            var point = inspector.mapFromItem(
                editorShell, wheel.x, wheel.y)
            if (point.x < 0 || point.x > inspector.width)
                return
            var delta = wheel.angleDelta.y !== 0
                ? wheel.angleDelta.y : wheel.pixelDelta.y
            if (delta === 0)
                return
            inspector.scrollByWheel(delta)
            wheel.accepted = true
        }
    }

    Rectangle {
        id: editorStarterGuide
        objectName: "editorStarterGuide"
        visible: experience.mode === "beginner" && experience.firstRunCompleted
            && !experience.editorGuideCompleted && !window.focusMode
        anchors.centerIn: parent
        width: Math.min(430, parent.width - 48)
        height: guideContent.implicitHeight + 36
        z: 9
        radius: 14
        color: window.appTheme.elevatedSurface
        border.color: window.appTheme.accentPrimary
        border.width: 1

        Column {
            id: guideContent
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10

            Text {
                text: "从这里开始创作"
                color: window.appTheme.textPrimary
                font.pixelSize: 18
                font.bold: true
            }
            Text {
                text: "1. 点击空白处创建音符"
                color: window.appTheme.textSecondary
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                width: parent.width
            }
            Text {
                text: "2. 拖动音符调整位置，拖右边缘调整长度"
                color: window.appTheme.textSecondary
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                width: parent.width
            }
            Text {
                text: "3. Ctrl+Z 可以撤销"
                color: window.appTheme.textSecondary
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                width: parent.width
            }
            DarkButton {
                objectName: "editorStarterGuideDismiss"
                anchors.right: parent.right
                text: "开始创作"
                highlighted: true
                onClicked: experience.completeEditorGuide()
            }
        }
    }

    }
    }

    Loader {
        id: editorShellLoader
        objectName: "editorShellLoader"
        anchors.fill: parent
        anchors.topMargin: windowChrome.titleBarHeight
        active: window.pageState === "editor" || window.pageState === "harmonyHelp"
        visible: active
        enabled: active
        sourceComponent: editorShellComponent
    }

    HarmonyHelpViewer {
        id: harmonyHelpViewer
        anchors.fill: parent
        anchors.topMargin: window.appTheme.windowChromeHeight
        z: 9
        theme: window.appTheme
        helpContent: typeof harmonyHelp !== "undefined" ? harmonyHelp : null
        visible: window.pageState === "harmonyHelp"
        enabled: visible
        onCloseRequested: window.closeHarmonyHelp()
    }

    Shortcut {
        id: focusModeShortcut
        objectName: "focusModeShortcut"
        sequence: "Ctrl+Shift+F"
        enabled: !window.studioMode && window.pageState === "editor"
            && window.active && !window.editorSurfaceOpen
            && !window.activeTextInputFocused()
        onActivated: experience.setFocusMode(!window.focusMode)
    }
    Shortcut {
        objectName: "studioSettingsShortcut"
        sequence: "Ctrl+,"
        enabled: window.studioMode && window.editorInputEnabled
        onActivated: window.openAppearancePopup(window.activeFocusItem)
    }
    Shortcut { sequence: "Ctrl+Z"; enabled: window.editorInputEnabled; onActivated: editor.undo() }
    Shortcut { sequence: "Ctrl+Shift+Z"; enabled: window.editorInputEnabled; onActivated: editor.redo() }
    Shortcut { sequence: "Ctrl+Y"; enabled: window.editorInputEnabled; onActivated: editor.redo() }
    Shortcut { sequence: "Ctrl+S"; enabled: window.editorInputEnabled; onActivated: window.requestExplicitSave() }
    Shortcut { sequence: "Ctrl+Shift+S"; enabled: window.editorInputEnabled; onActivated: window.requestSaveCopy() }
    Shortcut { sequence: "Ctrl+O"; enabled: window.editorInputEnabled; onActivated: window.requestOpenProject() }
    Shortcut { sequence: "Ctrl+N"; enabled: window.editorInputEnabled; onActivated: window.requestNewProject() }
    Shortcut { sequence: "Ctrl+C"; enabled: window.editorInputEnabled && !window.activeTextInputFocused(); onActivated: editor.copySelected() }
    Shortcut { sequence: "Ctrl+V"; enabled: window.editorInputEnabled; onActivated: editor.pasteCopied() }
    Shortcut { sequence: "Ctrl+D"; enabled: window.editorInputEnabled; onActivated: editor.duplicateSelected() }
    Shortcut { sequence: "Ctrl+Shift+Up"; enabled: window.editorInputEnabled; onActivated: editor.shiftSelectedOctave(12) }
    Shortcut { sequence: "Ctrl+Shift+Down"; enabled: window.editorInputEnabled; onActivated: editor.shiftSelectedOctave(-12) }
    Shortcut {
        sequence: "Left"
        enabled: window.editorInputEnabled && !window.activeInputConsumesArrows(window.activeFocusItem)
        onActivated: editor.moveSelectedByKeyboard(-1, 0)
    }
    Shortcut {
        sequence: "Right"
        enabled: window.editorInputEnabled && !window.activeInputConsumesArrows(window.activeFocusItem)
        onActivated: editor.moveSelectedByKeyboard(1, 0)
    }
    Shortcut {
        sequence: "Up"
        enabled: window.editorInputEnabled && !window.activeInputConsumesArrows(window.activeFocusItem)
        onActivated: editor.moveSelectedByKeyboard(0, 1)
    }
    Shortcut {
        sequence: "Down"
        enabled: window.editorInputEnabled && !window.activeInputConsumesArrows(window.activeFocusItem)
        onActivated: editor.moveSelectedByKeyboard(0, -1)
    }
    Shortcut {
        sequence: "Space"
        enabled: window.editorInputEnabled
            && !window.activeInputConsumesSpace(window.activeFocusItem)
        onActivated: window.togglePlayback()
    }
    Shortcut {
        id: selectionAuditionShortcut
        objectName: "selectionAuditionShortcut"
        sequence: "Shift+Space"
        enabled: window.editorInputEnabled
            && editor.selectedCount > 0
            && !window.activeTextInputFocused()
        onActivated: playback.selectionAudition.playSelection()
    }
    Shortcut {
        id: selectionAuditionStopShortcut
        objectName: "selectionAuditionStopShortcut"
        sequence: "Escape"
        enabled: window.editorInputEnabled
            && playback.selectionAudition.playing
            && !window.activeTextInputFocused()
        onActivated: playback.selectionAudition.stop()
    }
    Shortcut { sequence: "Delete"; enabled: window.editorInputEnabled; onActivated: editor.deleteSelected() }
    Shortcut { sequence: "Backspace"; enabled: window.editorInputEnabled; onActivated: editor.deleteSelected() }

    FileDialog {
        id: exportProjectDialog
        objectName: "exportProjectDialog"
        title: "导出工程文件"
        fileMode: FileDialog.SaveFile
        nameFilters: ["AI Piano JSON (*.pianoai.json)", "JSON (*.json)"]
        defaultSuffix: "pianoai.json"
        onAccepted: editor.exportProjectFile(selectedFile.toString())
    }

    FileDialog {
        id: openDialog
        title: "打开钢琴工程"
        fileMode: FileDialog.OpenFile
        nameFilters: ["AI Piano JSON (*.pianoai.json)", "JSON (*.json)"]
        onAccepted: {
            editor.openProject(selectedFile.toString())
        }
    }

    FileDialog {
        id: backgroundFileDialog
        objectName: "backgroundFileDialog"
        title: "选择背景图片"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "图片 (*.jpg *.jpeg *.png *.webp *.gif)",
            "所有文件 (*)"
        ]
        onAccepted: scene.setCustomBackgroundPath(selectedFile.toString())
    }

    Dialog {
        id: newConfirmDialog
        objectName: "newConfirmDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 400
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onOpened: Qt.callLater(newProjectCancelAction.forceActiveFocus)
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
            border.width: 1
        }
        header: Label {
            leftPadding: theme.space5
            rightPadding: theme.space5
            topPadding: theme.space5
            text: "未保存的更改"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSection
            font.weight: theme.weightBold
        }
        contentItem: Label {
            text: "当前工程有未保存的更改，直接新建将丢失这些内容。\n是否继续新建空白工程？"
            color: theme.textSecondary
            font.family: theme.fontUi
            font.pixelSize: theme.typeBody
            wrapMode: Text.WordWrap
        }
        footer: Item {
            implicitHeight: theme.controlHeight + theme.space6
            RowLayout {
                anchors.fill: parent
                anchors.margins: theme.space3
                Item { Layout.fillWidth: true }
                AppButton {
                    id: newProjectCancelAction
                    objectName: "newProjectCancelAction"
                    text: "取消"
                    theme: window.appTheme
                    KeyNavigation.backtab: newProjectConfirmAction
                    onClicked: newConfirmDialog.close()
                }
                AppButton {
                    id: newProjectConfirmAction
                    objectName: "newProjectConfirmAction"
                    text: "继续新建"
                    variant: "primary"
                    theme: window.appTheme
                    KeyNavigation.tab: newProjectCancelAction
                    onClicked: {
                        if (!editor.isSaving && !editor.recycleInProgress)
                            editor.newProject()
                        newConfirmDialog.close()
                    }
                }
            }
        }
    }

    ProjectNameDialog {
        id: projectNameDialog
        theme: window.appTheme
        editor: window.editorController
        onAcceptedName: function(name) {
            var accepted = false
            if (window.pendingNameOperation === "rename" && window.pendingProject) {
                accepted = editor.renameProject(window.pendingProject.path, name)
            } else if (window.pendingNameOperation === "copy") {
                accepted = editor.saveCopy(name)
            } else {
                accepted = editor.saveNamedProject(name)
            }
            if (accepted)
                projectNameDialog.close()
        }
        onClosed: {
            if (window.pendingNameOperation === "save"
                    && window.libraryExitSavePending
                    && !window.editorController.isSaving) {
                window.clearLibraryExitRequest()
            }
            if (!window.editorController.isSaving)
                window.explicitSavePending = false
            window.pendingNameOperation = ""
            window.restoreSurfaceFocus()
            Qt.callLater(window.restoreSurfaceFocus)
        }
    }

    ProjectNameDialog {
        id: renameProjectDialog
        objectName: "renameProjectDialog"
        theme: window.appTheme
        editor: window.editorController
        fieldObjectName: "renameProjectField"
        cancelButtonObjectName: "renameProjectCancelAction"
        confirmButtonObjectName: "renameProjectConfirmAction"
        onAcceptedName: function(name) {
            if (window.pendingProject
                    && editor.renameProject(window.pendingProject.path, name))
                renameProjectDialog.close()
        }
        onClosed: {
            window.pendingProject = null
            window.restoreSurfaceFocus()
            Qt.callLater(window.restoreSurfaceFocus)
        }
    }

    ExternalSaveChoiceDialog {
        id: externalSaveChoiceDialog
        theme: window.appTheme
        externalPath: editor.currentFilePath
        onSaveToLibraryRequested: editor.chooseExternalSaveDestination("library")
        onKeepOriginalRequested: editor.chooseExternalSaveDestination("original")
        onClosed: {
            if (!projectNameDialog.opened
                    && !window.editorController.isSaving) {
                if (window.libraryExitSavePending)
                    window.clearLibraryExitRequest()
                window.explicitSavePending = false
                window.restoreSurfaceFocus()
            }
            if (!projectNameDialog.opened && window.editorController.isSaving)
                window.restoreSurfaceFocus()
            if (!projectNameDialog.opened)
                Qt.callLater(window.restoreSurfaceFocus)
        }
    }

    Dialog {
        id: deleteProjectDialog
        objectName: "deleteProjectDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 420
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onOpened: Qt.callLater(projectDeleteCancelAction.forceActiveFocus)
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
            border.width: 1
        }
        header: Text {
            leftPadding: theme.space5
            rightPadding: theme.space5
            topPadding: theme.space5
            text: "确定删除这个工程？"
            color: theme.textPrimary
            font.pixelSize: 18
            font.bold: true
        }
        contentItem: ColumnLayout {
            spacing: theme.space3
            Label {
                Layout.fillWidth: true
                text: window.pendingProject ? window.pendingProject.name : ""
                color: theme.textPrimary
                font.pixelSize: 15
                font.bold: true
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                text: window.pendingProject ? "创建时间：" + window.pendingProject.createdAt : ""
                color: theme.textSecondary
                font.pixelSize: 12
            }
            Label {
                Layout.fillWidth: true
                text: window.pendingProject ? "最后修改时间：" + window.pendingProject.lastOpened : ""
                color: theme.textSecondary
                font.pixelSize: 12
            }
            Label {
                Layout.fillWidth: true
                Layout.topMargin: theme.space2
                text: "工程将移动到 Windows 回收站，你可以在系统中恢复它。"
                color: theme.textMuted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }
        footer: Item {
            implicitHeight: 58
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: theme.space5
                anchors.rightMargin: theme.space5
                anchors.bottomMargin: theme.space3
                spacing: theme.space2
                Item { Layout.fillWidth: true }
                AppButton {
                    id: projectDeleteCancelAction
                    objectName: "projectDeleteCancelAction"
                    text: "取消"
                    theme: window.appTheme
                    KeyNavigation.backtab: projectDeleteConfirmAction
                    onClicked: deleteProjectDialog.close()
                }
                AppButton {
                    id: projectDeleteConfirmAction
                    objectName: "projectDeleteConfirmAction"
                    text: "移动到回收站"
                    variant: "danger"
                    semanticKind: "danger"
                    theme: window.appTheme
                    KeyNavigation.tab: projectDeleteCancelAction
                    onClicked: {
                        if (window.pendingProject)
                            editor.recycleProject(window.pendingProject.path)
                        deleteProjectDialog.close()
                        window.pendingProject = null
                    }
                }
            }
        }
    }

    Dialog {
        id: unsyncedPlaybackDialog
        objectName: "unsyncedPlaybackDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 410
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onOpened: Qt.callLater(syncAndPlayAction.forceActiveFocus)
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
            border.width: 1
        }
        header: Text {
            leftPadding: theme.space5
            rightPadding: theme.space5
            topPadding: theme.space5
            text: "检测到未同步修改"
            color: theme.textPrimary
            font.pixelSize: 18
            font.bold: true
        }
        contentItem: ColumnLayout {
            spacing: theme.space3
            Label {
                Layout.fillWidth: true
                text: "有 " + editor.unsyncedNoteCount + " 个未同步音符或修改。"
                color: theme.textPrimary
                font.pixelSize: 14
            }
            Label {
                Layout.fillWidth: true
                text: "当前播放版本为 " + editor.playbackDurationSeconds.toFixed(1)
                    + " 秒；同步后的预计长度为 " + editor.estimatedDurationSeconds.toFixed(1) + " 秒。"
                color: theme.textSecondary
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }
        footer: Item {
            implicitHeight: 58
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: theme.space5
                anchors.rightMargin: theme.space5
                anchors.bottomMargin: theme.space3
                spacing: theme.space2
                Item { Layout.fillWidth: true }
                AppButton {
                    id: unsyncedCancelAction
                    objectName: "unsyncedCancelAction"
                    text: "取消"
                    theme: window.appTheme
                    KeyNavigation.backtab: syncAndPlayAction
                    KeyNavigation.tab: playCurrentAction
                    onClicked: unsyncedPlaybackDialog.close()
                }
                AppButton {
                    id: playCurrentAction
                    objectName: "playCurrentAction"
                    text: "播放当前版本"
                    theme: window.appTheme
                    KeyNavigation.backtab: unsyncedCancelAction
                    KeyNavigation.tab: syncAndPlayAction
                    onClicked: {
                        unsyncedPlaybackDialog.close()
                        playback.play()
                    }
                }
                AppButton {
                    id: syncAndPlayAction
                    objectName: "syncAndPlayAction"
                    text: "同步并播放"
                    variant: "primary"
                    theme: window.appTheme
                    KeyNavigation.backtab: playCurrentAction
                    KeyNavigation.tab: unsyncedCancelAction
                    onClicked: {
                        unsyncedPlaybackDialog.close()
                        window.synchronizeAndPlay()
                    }
                }
            }
        }
    }

    Dialog {
        id: invalidTimelineDialog
        objectName: "invalidTimelineDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 390
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onOpened: Qt.callLater(invalidTimelineConfirmAction.forceActiveFocus)
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
            border.width: 1
        }
        header: Label {
            leftPadding: theme.space5
            rightPadding: theme.space5
            topPadding: theme.space5
            text: "无法播放当前工程"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSection
            font.weight: theme.weightBold
        }
        contentItem: Label {
            leftPadding: theme.space5
            rightPadding: theme.space5
            bottomPadding: theme.space3
            text: "发现 " + editor.validateTimeline().invalidPitchCount + " 个无效音高和 "
                + editor.validateTimeline().emptyDurationCount + " 个空时长音符，请修正后再播放。"
            color: theme.textSecondary
            wrapMode: Text.WordWrap
        }
        footer: Item {
            implicitHeight: theme.controlHeight + theme.space6
            AppButton {
                id: invalidTimelineConfirmAction
                objectName: "invalidTimelineConfirmAction"
                anchors.right: parent.right
                anchors.rightMargin: theme.space3
                anchors.verticalCenter: parent.verticalCenter
                text: "知道了"
                variant: "primary"
                theme: window.appTheme
                KeyNavigation.tab: invalidTimelineConfirmAction
                KeyNavigation.backtab: invalidTimelineConfirmAction
                onClicked: invalidTimelineDialog.close()
            }
        }
    }

    Component {
        id: appearancePopupComponent
        Dialog {
        id: appearancePopup
        objectName: "themePicker"
        parent: Overlay.overlay
        x: window.appearanceDockX
        y: window.appearanceDockTop
        width: window.appearanceDockWidth
        height: Math.max(theme.controlHeight, window.appearanceDockHeight)
        padding: theme.space3
        modal: false
        focus: true
        property bool backgroundAdvancedOptionsOpen: true
        property bool visualizerAdvancedOptionsOpen: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: Qt.callLater(backgroundEnabledToggle.forceActiveFocus)
        onClosed: {
            window.appearanceDockOpen = false
            window.restoreSurfaceFocus()
        }
        background: Rectangle {
            radius: 0
            color: theme.elevatedSurface
            border.color: theme.borderStrong
            border.width: 1
        }
        contentItem: ScrollView {
            id: appearanceScroll
            objectName: "appearanceScroll"
            clip: true
            rightPadding: theme.scrollbarGutter
            contentWidth: availableWidth
            ScrollBar.vertical: SafeScrollBar { theme: window.appTheme }

            ColumnLayout {
                id: appearanceOptions
                width: appearanceScroll.availableWidth
                spacing: theme.space2
            Label {
                objectName: "backgroundSection"
                Layout.fillWidth: true
                text: "背景"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            SwitchToggle {
                id: backgroundEnabledToggle
                objectName: "backgroundEnabledToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                checked: scene.backgroundEnabled
                text: "显示编辑器背景"
                Accessible.description: "在钢琴卷帘下显示背景图片"
                KeyNavigation.tab: backgroundPresetPicker
                KeyNavigation.backtab: autosaveDelayPicker
                onToggled: value => scene.setBackgroundEnabled(value)
            }
            SegmentedControl {
                id: backgroundPresetPicker
                objectName: "backgroundPresetPicker"
                Layout.fillWidth: true
                theme: window.appTheme
                model: ["纯净", "律动", "舞台"]
                currentIndex: scene.backgroundPreset === "clean" ? 0
                    : (scene.backgroundPreset === "rhythm" ? 1 : 2)
                Accessible.name: "背景预设"
                KeyNavigation.tab: backgroundUploadButton
                KeyNavigation.backtab: backgroundEnabledToggle
                onActivated: index =>
                    scene.setBackgroundPreset(["clean", "rhythm", "stage"][index])
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: theme.space2
                AppButton {
                    id: backgroundUploadButton
                    objectName: "customBackgroundUploadButton"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    text: scene.customBackgroundPath.length > 0 ? "更换图片" : "上传图片"
                    variant: "secondary"
                    Accessible.name: scene.customBackgroundPath.length > 0 ? "更换背景图片" : "上传背景图片"
                    Accessible.description: "支持 JPG、PNG、WebP 和 GIF 图片"
                    KeyNavigation.tab: backgroundClearButton.visible
                        ? backgroundClearButton : backgroundAdvancedOptionsToggle
                    KeyNavigation.backtab: backgroundPresetPicker
                    onClicked: backgroundFileDialog.open()
                }
                AppButton {
                    id: backgroundClearButton
                    objectName: "clearCustomBackgroundButton"
                    Layout.preferredWidth: theme.controlHeight * 2.2
                    theme: window.appTheme
                    text: "清除"
                    variant: "ghost"
                    visible: scene.customBackgroundPath.length > 0
                    enabled: visible
                    Accessible.name: "清除自定义背景"
                    KeyNavigation.tab: backgroundAdvancedOptionsToggle
                    KeyNavigation.backtab: backgroundUploadButton
                    onClicked: scene.clearCustomBackground()
                }
            }
            Label {
                objectName: "customBackgroundPathLabel"
                Layout.fillWidth: true
                text: scene.customBackgroundPath.length > 0
                    ? "自定义：" + scene.customBackgroundPath
                    : "使用内置背景预设"
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                elide: Text.ElideMiddle
                maximumLineCount: 1
                Accessible.name: text
            }
            AppButton {
                id: backgroundAdvancedOptionsToggle
                objectName: "backgroundAdvancedOptionsToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                text: appearancePopup.backgroundAdvancedOptionsOpen
                    ? "高级背景参数 ︿" : "高级背景参数 ﹀"
                variant: "ghost"
                Accessible.name: "高级背景参数"
                Accessible.description: "调节背景透明度、模糊、暗化、光晕和动态强度"
                KeyNavigation.tab: dynamicBackgroundToggle
                KeyNavigation.backtab: backgroundUploadButton
                onClicked: appearancePopup.backgroundAdvancedOptionsOpen =
                    !appearancePopup.backgroundAdvancedOptionsOpen
            }
            ColumnLayout {
                objectName: "backgroundAdvancedOptions"
                Layout.fillWidth: true
                spacing: theme.space1
                visible: appearancePopup.backgroundAdvancedOptionsOpen

                Label {
                    text: "背景视觉参数"
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "透明度"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                    StyledSlider {
                        id: backgroundOpacitySlider
                        objectName: "backgroundOpacitySlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 1
                        stepSize: 0.01
                        modelValue: scene.backgroundOpacity
                        accessibleName: "背景透明度"
                        onValueCommitted: value => scene.setBackgroundOpacity(value)
                    }
                    Label {
                        objectName: "backgroundOpacityValue"
                        text: Math.round(backgroundOpacitySlider.value * 100) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "模糊"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                    StyledSlider {
                        id: backgroundBlurSlider
                        objectName: "backgroundBlurSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 1
                        stepSize: 0.01
                        modelValue: scene.backgroundBlur
                        accessibleName: "背景模糊"
                        onValueCommitted: value => scene.setBackgroundBlur(value)
                    }
                    Label {
                        objectName: "backgroundBlurValue"
                        text: Math.round(backgroundBlurSlider.value * 100) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "暗化"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                    StyledSlider {
                        id: backgroundDarknessSlider
                        objectName: "backgroundDarknessSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 1
                        stepSize: 0.01
                        modelValue: scene.backgroundDarkness
                        accessibleName: "背景暗化"
                        onValueCommitted: value => scene.setBackgroundDarkness(value)
                    }
                    Label {
                        objectName: "backgroundDarknessValue"
                        text: Math.round(backgroundDarknessSlider.value * 100) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "光晕"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                    StyledSlider {
                        id: backgroundGlowSlider
                        objectName: "backgroundGlowSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 1
                        stepSize: 0.01
                        modelValue: scene.backgroundGlow
                        accessibleName: "背景光晕"
                        onValueCommitted: value => scene.setBackgroundGlow(value)
                    }
                    Label {
                        objectName: "backgroundGlowValue"
                        text: Math.round(backgroundGlowSlider.value * 100) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "动态"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                    StyledSlider {
                        id: backgroundMotionSlider
                        objectName: "backgroundMotionSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 1
                        stepSize: 0.01
                        modelValue: scene.backgroundMotion
                        accessibleName: "背景动态强度"
                        onValueCommitted: value => scene.setBackgroundMotion(value)
                    }
                    Label {
                        objectName: "backgroundMotionValue"
                        text: Math.round(backgroundMotionSlider.value * 100) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
            }
            SwitchToggle {
                id: dynamicBackgroundToggle
                objectName: "dynamicBackgroundToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                checked: scene.dynamicEnabled
                text: "动态背景"
                KeyNavigation.tab: motionLevelPicker
                KeyNavigation.backtab: backgroundClearButton.visible
                    ? backgroundClearButton : backgroundAdvancedOptionsToggle
                onToggled: value => scene.setDynamicEnabled(value)
            }
            SegmentedControl {
                id: motionLevelPicker
                objectName: "motionLevelPicker"
                Layout.fillWidth: true
                theme: window.appTheme
                model: ["低动态", "中动态", "高动态"]
                currentIndex: scene.motionLevel === "low" ? 0 : (scene.motionLevel === "high" ? 2 : 1)
                Accessible.name: "背景动态级别"
                KeyNavigation.tab: focusModeToggle
                KeyNavigation.backtab: dynamicBackgroundToggle
                onActivated: index => scene.setMotionLevel(["low", "medium", "high"][index])
            }
            SwitchToggle {
                id: focusModeToggle
                objectName: "focusModeToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                checked: scene.focusModeEnabled
                text: "专注模式（关闭动态视觉）"
                Accessible.description: "关闭背景动画、律动层、强光晕和重模糊，优先保证编辑清晰度与性能"
                KeyNavigation.tab: powerSaveToggle
                KeyNavigation.backtab: motionLevelPicker
                onToggled: value => scene.setFocusModeEnabled(value)
            }
            SwitchToggle {
                id: powerSaveToggle
                objectName: "powerSaveToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                checked: scene.powerSaveEnabled
                text: "省电模式"
                KeyNavigation.tab: autosaveToggle
                KeyNavigation.backtab: focusModeToggle
                onToggled: value => scene.setPowerSaveEnabled(value)
            }
            Label {
                objectName: "visualizerSection"
                Layout.topMargin: theme.space2
                text: "律动可视化"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            Label {
                objectName: "visualizerIndependentDescription"
                Layout.fillWidth: true
                text: "独立于背景；只响应真实 Master 音频，不修改音符。"
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                wrapMode: Text.WordWrap
            }
            SwitchToggle {
                id: visualizerEnabledToggle
                objectName: "visualizerEnabledToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                checked: scene.visualizerEnabled
                text: "启用律动可视化"
                Accessible.name: "启用律动可视化"
                Accessible.description: "关闭后停止频谱分析；背景设置保持独立"
                onToggled: value => scene.setVisualizerEnabled(value)
            }
            SegmentedControl {
                id: visualizerPresetPicker
                objectName: "visualizerPresetPicker"
                Layout.fillWidth: true
                theme: window.appTheme
                model: ["简约", "均衡", "沉浸", "自定义"]
                currentIndex: Math.max(
                    0, ["minimal", "balanced", "immersive", "custom"].indexOf(scene.visualizerPreset)
                )
                Accessible.name: "律动可视化预设"
                onActivated: index => scene.setVisualizerPreset(
                    ["minimal", "balanced", "immersive", "custom"][index]
                )
            }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "强度"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                StyledSlider {
                    id: visualizerIntensitySlider
                    objectName: "visualizerIntensitySlider"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    from: 0
                    to: 100
                    stepSize: 1
                    modelValue: scene.visualizerIntensity
                    accessibleName: "律动强度"
                    onValueCommitted: value => scene.setVisualizerIntensity(value)
                }
                Label {
                    text: Math.round(visualizerIntensitySlider.value) + "%"
                    color: theme.textSecondary
                    Layout.preferredWidth: 36
                    horizontalAlignment: Text.AlignRight
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "灵敏度"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                StyledSlider {
                    id: visualizerSensitivitySlider
                    objectName: "visualizerSensitivitySlider"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    from: 0
                    to: 100
                    stepSize: 1
                    modelValue: scene.visualizerSensitivity
                    accessibleName: "律动灵敏度"
                    onValueCommitted: value => scene.setVisualizerSensitivity(value)
                }
                Label {
                    text: Math.round(visualizerSensitivitySlider.value) + "%"
                    color: theme.textSecondary
                    Layout.preferredWidth: 36
                    horizontalAlignment: Text.AlignRight
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "平滑"; color: theme.textPrimary; Layout.preferredWidth: 52 }
                StyledSlider {
                    id: visualizerSmoothnessSlider
                    objectName: "visualizerSmoothnessSlider"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    from: 0
                    to: 100
                    stepSize: 1
                    modelValue: scene.visualizerSmoothness
                    accessibleName: "律动平滑度"
                    onValueCommitted: value => scene.setVisualizerSmoothness(value)
                }
                Label {
                    text: Math.round(visualizerSmoothnessSlider.value) + "%"
                    color: theme.textSecondary
                    Layout.preferredWidth: 36
                    horizontalAlignment: Text.AlignRight
                }
            }
            AppButton {
                id: visualizerAdvancedOptionsToggle
                objectName: "visualizerAdvancedOptionsToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                text: appearancePopup.visualizerAdvancedOptionsOpen
                    ? "高级律动参数 ︿" : "高级律动参数 ﹀"
                variant: "ghost"
                Accessible.name: "高级律动参数"
                onClicked: appearancePopup.visualizerAdvancedOptionsOpen =
                    !appearancePopup.visualizerAdvancedOptionsOpen
            }
            ColumnLayout {
                id: visualizerAdvancedOptions
                objectName: "visualizerAdvancedOptions"
                Layout.fillWidth: true
                spacing: theme.space1
                visible: appearancePopup.visualizerAdvancedOptionsOpen

                SwitchToggle {
                    id: visualizerSpectrumToggle
                    objectName: "visualizerSpectrumToggle"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    checked: scene.visualizerSpectrumEnabled
                    text: "频谱柱"
                    onToggled: value => scene.setVisualizerSpectrumEnabled(value)
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "频谱强度"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        objectName: "visualizerSpectrumStrengthSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 100
                        stepSize: 1
                        modelValue: scene.visualizerSpectrumStrength
                        accessibleName: "频谱强度"
                        onValueCommitted: value => scene.setVisualizerSpectrumStrength(value)
                    }
                }
                SegmentedControl {
                    id: visualizerSpectrumHeightPicker
                    objectName: "visualizerSpectrumHeightPicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["低", "中", "高"]
                    currentIndex: Math.max(
                        0, ["low", "mid", "high"].indexOf(scene.visualizerSpectrumHeight)
                    )
                    Accessible.name: "频谱高度"
                    onActivated: index => scene.setVisualizerSpectrumHeight(
                        ["low", "mid", "high"][index]
                    )
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "频谱透明度"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        objectName: "visualizerSpectrumOpacitySlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 100
                        stepSize: 1
                        modelValue: scene.visualizerSpectrumOpacity
                        accessibleName: "频谱透明度"
                        onValueCommitted: value => scene.setVisualizerSpectrumOpacity(value)
                    }
                }
                SegmentedControl {
                    id: visualizerBandCountPicker
                    objectName: "visualizerBandCountPicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["24 频段", "32 频段", "48 频段"]
                    currentIndex: Math.max(0, [24, 32, 48].indexOf(scene.visualizerBandCount))
                    Accessible.name: "频段数量"
                    onActivated: index => scene.setVisualizerBandCount([24, 32, 48][index])
                }
                SegmentedControl {
                    id: visualizerBarWidthPicker
                    objectName: "visualizerBarWidthPicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["细", "标准"]
                    currentIndex: scene.visualizerBarWidth === "standard" ? 1 : 0
                    Accessible.name: "频谱条宽度"
                    onActivated: index => scene.setVisualizerBarWidth(
                        ["thin", "standard"][index]
                    )
                }
                SwitchToggle {
                    id: visualizerGlowToggle
                    objectName: "visualizerGlowToggle"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    checked: scene.visualizerGlowEnabled
                    text: "低强度光晕"
                    onToggled: value => scene.setVisualizerGlowEnabled(value)
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "光晕强度"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        objectName: "visualizerGlowIntensitySlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 100
                        stepSize: 1
                        modelValue: scene.visualizerGlowIntensity
                        accessibleName: "光晕强度"
                        onValueCommitted: value => scene.setVisualizerGlowIntensity(value)
                    }
                }
                SegmentedControl {
                    id: visualizerGlowSpreadPicker
                    objectName: "visualizerGlowSpreadPicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["低扩散", "中扩散", "宽扩散"]
                    currentIndex: Math.max(
                        0, ["low", "mid", "wide"].indexOf(scene.visualizerGlowSpread)
                    )
                    Accessible.name: "光晕扩散范围"
                    onActivated: index => scene.setVisualizerGlowSpread(
                        ["low", "mid", "wide"][index]
                    )
                }
                SwitchToggle {
                    id: visualizerRibbonToggle
                    objectName: "visualizerRibbonToggle"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    checked: scene.visualizerRibbonEnabled
                    text: "轻量光带"
                    onToggled: value => scene.setVisualizerRibbonEnabled(value)
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "光带强度"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        id: visualizerRibbonIntensitySlider
                        objectName: "visualizerRibbonIntensitySlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 100
                        stepSize: 1
                        modelValue: scene.visualizerRibbonIntensity
                        accessibleName: "光带强度"
                        onValueCommitted: value => scene.setVisualizerRibbonIntensity(value)
                    }
                    Label {
                        text: Math.round(visualizerRibbonIntensitySlider.value) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
                SegmentedControl {
                    id: visualizerRibbonCountPicker
                    objectName: "visualizerRibbonCountPicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["少", "标准", "多"]
                    currentIndex: Math.max(
                        0, ["few", "standard", "many"].indexOf(scene.visualizerRibbonCount)
                    )
                    Accessible.name: "光带数量"
                    onActivated: index => scene.setVisualizerRibbonCount(
                        ["few", "standard", "many"][index]
                    )
                }
                SegmentedControl {
                    id: visualizerRibbonSpeedPicker
                    objectName: "visualizerRibbonSpeedPicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["慢", "标准", "快"]
                    currentIndex: Math.max(
                        0, ["slow", "standard", "fast"].indexOf(scene.visualizerRibbonSpeed)
                    )
                    Accessible.name: "光带速度"
                    onActivated: index => scene.setVisualizerRibbonSpeed(
                        ["slow", "standard", "fast"][index]
                    )
                }
                SwitchToggle {
                    id: visualizerBeatToggle
                    objectName: "visualizerBeatToggle"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    checked: scene.visualizerBeatEnabled
                    text: "节拍脉冲（安全强度）"
                    onToggled: value => scene.setVisualizerBeatEnabled(value)
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "节拍强度"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        id: visualizerBeatIntensitySlider
                        objectName: "visualizerBeatIntensitySlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 100
                        stepSize: 1
                        modelValue: scene.visualizerBeatIntensity
                        accessibleName: "节拍脉冲强度"
                        onValueCommitted: value => scene.setVisualizerBeatIntensity(value)
                    }
                    Label {
                        text: Math.round(visualizerBeatIntensitySlider.value) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 36
                        horizontalAlignment: Text.AlignRight
                    }
                }
                Label {
                    objectName: "visualizerColorSection"
                    Layout.topMargin: theme.space1
                    text: "频率颜色与视觉增益"
                    color: theme.textSecondary
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeSmall
                }
                SegmentedControl {
                    id: visualizerColorModePicker
                    objectName: "visualizerColorModePicker"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    model: ["自动频率", "单色", "自定义"]
                    currentIndex: Math.max(
                        0, ["frequency", "monochrome", "custom"].indexOf(scene.visualizerColorMode)
                    )
                    Accessible.name: "频率颜色模式"
                    onActivated: index => scene.setVisualizerColorMode(
                        ["frequency", "monochrome", "custom"][index]
                    )
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "低频增益"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        id: visualizerLowGainSlider
                        objectName: "visualizerLowGainSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 200
                        stepSize: 1
                        modelValue: scene.visualizerLowGain
                        accessibleName: "低频视觉增益"
                        onValueCommitted: value => scene.setVisualizerLowGain(value)
                    }
                    Label {
                        text: Math.round(visualizerLowGainSlider.value) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 42
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "中频增益"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        id: visualizerMidGainSlider
                        objectName: "visualizerMidGainSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 200
                        stepSize: 1
                        modelValue: scene.visualizerMidGain
                        accessibleName: "中频视觉增益"
                        onValueCommitted: value => scene.setVisualizerMidGain(value)
                    }
                    Label {
                        text: Math.round(visualizerMidGainSlider.value) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 42
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "高频增益"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        id: visualizerHighGainSlider
                        objectName: "visualizerHighGainSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 200
                        stepSize: 1
                        modelValue: scene.visualizerHighGain
                        accessibleName: "高频视觉增益"
                        onValueCommitted: value => scene.setVisualizerHighGain(value)
                    }
                    Label {
                        text: Math.round(visualizerHighGainSlider.value) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 42
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "色彩饱和度"; color: theme.textPrimary; Layout.preferredWidth: 64 }
                    StyledSlider {
                        id: visualizerSaturationSlider
                        objectName: "visualizerSaturationSlider"
                        Layout.fillWidth: true
                        theme: window.appTheme
                        from: 0
                        to: 100
                        stepSize: 1
                        modelValue: scene.visualizerSaturation
                        accessibleName: "色彩饱和度"
                        onValueCommitted: value => scene.setVisualizerSaturation(value)
                    }
                    Label {
                        text: Math.round(visualizerSaturationSlider.value) + "%"
                        color: theme.textSecondary
                        Layout.preferredWidth: 42
                        horizontalAlignment: Text.AlignRight
                    }
                }
                AppColorField {
                    id: visualizerLowColorField
                    objectName: "visualizerLowColorField"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    label: "低频颜色"
                    text: scene.visualizerLowColor
                    onEditingFinished: {
                        scene.setVisualizerLowColor(text)
                        text = scene.visualizerLowColor
                    }
                }
                AppColorField {
                    id: visualizerMidColorField
                    objectName: "visualizerMidColorField"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    label: "中频颜色"
                    text: scene.visualizerMidColor
                    onEditingFinished: {
                        scene.setVisualizerMidColor(text)
                        text = scene.visualizerMidColor
                    }
                }
                AppColorField {
                    id: visualizerHighColorField
                    objectName: "visualizerHighColorField"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    label: "高频颜色"
                    text: scene.visualizerHighColor
                    onEditingFinished: {
                        scene.setVisualizerHighColor(text)
                        text = scene.visualizerHighColor
                    }
                }
                SwitchToggle {
                    id: visualizerReadabilityToggle
                    objectName: "visualizerReadabilityToggle"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    checked: scene.visualizerReadabilityProtection
                    text: "保护编辑器可读性"
                    Accessible.description: "限制透明度、亮度和光晕，避免遮挡音符与网格"
                    onToggled: value => scene.setVisualizerReadabilityProtection(value)
                }
                AppButton {
                    id: visualizerResetButton
                    objectName: "visualizerResetButton"
                    Layout.fillWidth: true
                    theme: window.appTheme
                    text: "恢复律动默认"
                    variant: "secondary"
                    Accessible.name: "恢复律动默认设置"
                    onClicked: scene.resetVisualizerDefaults()
                }
            }
            Label {
                Layout.topMargin: theme.space2
                text: "保存"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            SwitchToggle {
                id: autosaveToggle
                objectName: "autosaveToggle"
                Layout.fillWidth: true
                theme: window.appTheme
                checked: savePreferences.autosaveEnabled
                text: "自动保存"
                Accessible.description: "开启后，在停止操作一段时间后保存；未命名工程需要先手动保存一次"
                KeyNavigation.tab: autosaveDelayPicker
                KeyNavigation.backtab: powerSaveToggle
                onToggled: value => savePreferences.setAutosaveEnabled(value)
            }
            Label {
                text: "自动保存等待时间"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
            }
            ComboBox {
                id: autosaveDelayPicker
                objectName: "autosaveDelayPicker"
                Layout.fillWidth: true
                implicitHeight: theme.controlHeight
                enabled: savePreferences.autosaveEnabled
                model: ["15 秒", "30 秒", "1 分钟", "2 分钟", "5 分钟"]
                property var seconds: [15, 30, 60, 120, 300]
                currentIndex: Math.max(
                    0, seconds.indexOf(savePreferences.autosaveDelaySeconds)
                )
                Accessible.name: "自动保存等待时间"
                KeyNavigation.tab: dynamicBackgroundToggle
                KeyNavigation.backtab: autosaveToggle
                onActivated: index =>
                    savePreferences.setAutosaveDelaySeconds(seconds[index])

                contentItem: Text {
                    leftPadding: theme.space3
                    rightPadding: theme.space7
                    text: autosaveDelayPicker.displayText
                    color: autosaveDelayPicker.enabled
                        ? theme.textPrimary : theme.textDisabled
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeBody
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    radius: theme.radiusM
                    color: autosaveDelayPicker.hovered
                        ? theme.surfaceHover : theme.elevatedSurface
                    border.width: autosaveDelayPicker.activeFocus ? 2 : 1
                    border.color: autosaveDelayPicker.activeFocus
                        ? theme.focusRing : theme.borderStrong
                }
                indicator: Text {
                    x: autosaveDelayPicker.width - width - theme.space3
                    y: (autosaveDelayPicker.height - height) / 2
                    text: "⌄"
                    color: autosaveDelayPicker.enabled
                        ? theme.textSecondary : theme.textDisabled
                    font.family: theme.fontUi
                    font.pixelSize: theme.typeBody
                }
                delegate: ItemDelegate {
                    width: autosaveDelayPicker.width
                    contentItem: Text {
                        text: modelData
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBody
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: parent.highlighted
                            ? theme.surfaceSelected : theme.elevatedSurface
                    }
                }
            }
            Label {
                objectName: "autosaveDelayDescription"
                Layout.fillWidth: true
                text: savePreferences.autosaveEnabled
                    ? "停止操作后等待 " + savePreferences.autosaveDelaySeconds + " 秒自动保存"
                    : "自动保存已关闭"
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                wrapMode: Text.WordWrap
            }
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.borderSubtle
            }
            AppButton {
                id: supportSettingsButton
                objectName: "supportSettingsButton"
                Layout.fillWidth: true
                theme: window.appTheme
                text: "帮助与诊断"
                variant: "secondary"
                Accessible.name: "帮助与诊断"
                Accessible.description: "查看版本、打开日志、导出诊断信息和恢复界面设置"
                onClicked: {
                    var returnTarget = window.activeFocusItem
                    appearancePopup.close()
                    window.openSupportDialog(returnTarget)
                }
            }
            AppButton {
                id: resetEditorTipsButton
                objectName: "resetEditorTipsButton"
                Layout.fillWidth: true
                theme: window.appTheme
                text: "重新显示新手提示"
                variant: "ghost"
                Accessible.name: "重新显示新手提示"
                onClicked: {
                    experience.resetEditorTips()
                    window.showFeedback("success", "新手提示已重新启用")
                }
            }
            }
        }
    }
    }

    Dialog {
        id: unsavedExitDialog
        objectName: "unsavedExitDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 460
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onOpened: unsavedExitCancelAction.forceActiveFocus(Qt.PopupFocusReason)
        onClosed: {
            if (!window.libraryExitSavePending) {
                window.clearLibraryExitRequest()
                window.restoreSurfaceFocus()
            }
        }
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
            border.width: 1
        }
        header: Label {
            leftPadding: theme.space5
            rightPadding: theme.space5
            topPadding: theme.space5
            text: "保存对工程的修改吗？"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSection
            font.weight: theme.weightBold
        }
        contentItem: Label {
            text: "不保存会丢弃从上次保存后所做的修改。"
            color: theme.textSecondary
            font.family: theme.fontUi
            font.pixelSize: theme.typeBody
            wrapMode: Text.WordWrap
        }
        footer: Item {
            implicitHeight: theme.controlHeight + theme.space6
            RowLayout {
                anchors.fill: parent
                anchors.margins: theme.space3
                Item { Layout.fillWidth: true }
                AppButton {
                    id: unsavedExitCancelAction
                    objectName: "unsavedExitCancelAction"
                    text: "取消"
                    theme: window.appTheme
                    KeyNavigation.tab: unsavedExitDiscardAction
                    KeyNavigation.backtab: unsavedExitSaveAction
                    onClicked: {
                        window.clearLibraryExitRequest()
                        unsavedExitDialog.close()
                    }
                }
                AppButton {
                    id: unsavedExitDiscardAction
                    objectName: "unsavedExitDiscardAction"
                    text: "不保存"
                    variant: "danger"
                    theme: window.appTheme
                    KeyNavigation.tab: unsavedExitSaveAction
                    KeyNavigation.backtab: unsavedExitCancelAction
                    onClicked: {
                        var target = window.pendingLibraryExitPage
                        var discarded = editor.discardUnsavedChanges()
                        window.clearLibraryExitRequest()
                        unsavedExitDialog.close()
                        if (discarded)
                            window.completeLibraryExit(target)
                    }
                }
                AppButton {
                    id: unsavedExitSaveAction
                    objectName: "unsavedExitSaveAction"
                    text: "保存"
                    variant: "primary"
                    theme: window.appTheme
                    KeyNavigation.tab: unsavedExitCancelAction
                    KeyNavigation.backtab: unsavedExitDiscardAction
                    onClicked: {
                        window.saveThenLeaveLibrary()
                        unsavedExitDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: creativeExitDialog
        objectName: "creativeExitDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 480
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onOpened: creativeExitCancelAction.forceActiveFocus(Qt.PopupFocusReason)
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            color: theme.elevatedSurface
            radius: theme.radiusL
            border.color: theme.borderSubtle
        }
        header: DialogHeader { theme: window.appTheme; title: "灵感草稿尚未保存" }
        contentItem: Label {
            text: "退出会丢弃独立主题草稿的修改。请返回“灵感与陪伴”保存主题，或明确放弃后退出。"
            color: theme.textPrimary
            wrapMode: Text.WordWrap
        }
        footer: DialogFooter {
            theme: window.appTheme
            Item { Layout.fillWidth: true }
            AppButton {
                id: creativeExitCancelAction
                objectName: "creativeExitCancelAction"
                theme: window.appTheme; text: "取消"
                onClicked: creativeExitDialog.close()
            }
            AppButton {
                objectName: "creativeExitDiscardAction"
                theme: window.appTheme; text: "放弃并退出"; variant: "danger"
                onClicked: {
                    creativeExitDialog.close()
                    window.creativeDiscardOnce = true
                    window.close()
                }
            }
            AppButton {
                objectName: "creativeExitSaveAction"
                theme: window.appTheme; text: "返回保存"; variant: "primary"
                onClicked: {
                    creativeExitDialog.close()
                    window.creativeController.open()
                }
            }
        }
    }

    Dialog {
        id: harmonyModePopup
        objectName: "harmonyModePopup"
        property Item opener: null
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 300
        padding: theme.space4
        modal: false
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: Qt.callLater(harmonyModePicker.forceActiveFocus)
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
            border.width: 1
        }
        contentItem: ColumnLayout {
            spacing: theme.space3
            Label {
                text: "和弦"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSection
                font.weight: theme.weightBold
            }
            SegmentedControl {
                id: harmonyModePicker
                objectName: "harmonyModePicker"
                Layout.fillWidth: true
                theme: window.appTheme
                model: ["关闭", "预览", "自动"]
                enabled: window.harmonyAvailable
                currentIndex: window.harmonyAvailable
                    ? ["off", "preview", "auto"].indexOf(harmony.mode) : 0
                onActivated: index => {
                    if (window.harmonyAvailable)
                        harmony.setMode(["off", "preview", "auto"][index])
                }
                Accessible.name: "和弦模式"
            }
            Label {
                objectName: "harmonyAutoDescription"
                Layout.fillWidth: true
                text: "仅处理已完成的小节"
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeCaption
                wrapMode: Text.WordWrap
            }
            AppButton {
                objectName: "harmonyGenerateWholeProjectAction"
                Layout.fillWidth: true
                theme: window.appTheme
                text: window.harmonyAvailable && harmony.wholeProjectPending
                    ? "正在检测整首…" : "检测整首并生成"
                Accessible.name: "检测整首旋律并在合适的小节生成和弦"
                Accessible.description: window.harmonyAvailable && harmony.wholeProjectPending
                    ? "正在分析整首旋律，请稍候。"
                    : "一次性检测整首旋律，不会更改当前和弦模式。"
                enabled: window.harmonyAvailable && !harmony.wholeProjectPending
                onClicked: {
                    if (window.harmonyAvailable)
                        harmony.generateWholeProjectHarmony()
                }
            }
            AppButton {
                id: harmonyClearGeneratedAction
                objectName: "harmonyClearGeneratedAction"
                Layout.fillWidth: true
                text: "清除自动和弦"
                variant: "danger"
                semanticKind: "danger"
                theme: window.appTheme
                enabled: typeof harmony !== "undefined" && harmony.hasGeneratedHarmony
                onClicked: harmony.requestClearGeneratedHarmony()
            }
            AppButton {
                id: harmonyHelpButton
                objectName: "harmonyHelpButton"
                Layout.fillWidth: true
                theme: window.appTheme
                text: "?  和弦帮助"
                Accessible.name: "和弦帮助"
                onClicked: {
                    var returnTarget = harmonyModePopup.opener || window.harmonyModeOpener
                    window.surfaceFocusReturnTarget = null
                    harmonyModePopup.close()
                    window.openHarmonyHelp("harmony-basics", returnTarget)
                }
            }
        }
    }

    Connections {
        target: typeof harmony !== "undefined" ? harmony : null
        function onFeedbackRequested(message) {
            window.routeGenericFeedback(message)
        }
        function onTemporaryConfirmationRequested(measureIndex) {
            window.pendingHarmonyMeasure = measureIndex
            window.openSurface(harmonyTemporaryConfirmDialog, window.activeFocusItem)
        }
        function onClearConfirmationRequested() {
            window.openSurface(harmonyClearConfirmDialog, window.activeFocusItem)
        }
        function onHelpRequested(sectionId) {
            window.openHarmonyHelp(sectionId, window.activeFocusItem)
        }
    }

    Dialog {
        id: harmonyTemporaryConfirmDialog
        objectName: "harmonyTemporaryConfirmDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 430
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
        }
        contentItem: Label {
            text: "当前小节还未完成，后续继续弹奏可能需要重新调整和弦。"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeBody
            wrapMode: Text.WordWrap
        }
        footer: DialogButtonBox {
            Button { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            Button {
                text: "仍然应用"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
                onClicked: {
                    harmony.confirmTemporarySuggestion(window.pendingHarmonyMeasure)
                    harmonyTemporaryConfirmDialog.close()
                }
            }
        }
    }

    Dialog {
        id: harmonyClearConfirmDialog
        objectName: "harmonyClearConfirmDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 430
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: theme.space5
        onClosed: window.restoreSurfaceFocus()
        background: Rectangle {
            radius: theme.radiusL
            color: theme.elevatedSurface
            border.color: theme.borderSubtle
        }
        contentItem: Label {
            text: "只删除和弦生成的音符，不会删除你自己添加的音符。"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeBody
            wrapMode: Text.WordWrap
        }
        footer: DialogButtonBox {
            Button { text: "取消"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            Button {
                text: "清除自动和弦"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
                onClicked: {
                    harmony.clearGeneratedHarmony()
                    harmonyClearConfirmDialog.close()
                }
            }
        }
    }

    Welcome {
        onScoreLabRequested: { if (typeof scoreLab !== "undefined" && scoreLab !== null) scoreLab.open() }
        id: welcomeOverlay
        anchors.fill: parent
        anchors.topMargin: window.appTheme.windowChromeHeight
        z: 10
        theme: window.appTheme
        introAnimation: experience.introAnimation
        firstRun: !experience.firstRunCompleted
        visible: window.pageState === "launch" || window.pageState === "library"
        enabled: visible
        pageState: window.pageState
        onPageChangeRequested: function(nextPage, focusCurrentProject) {
            window.setPage(nextPage, focusCurrentProject)
        }
        onNewRequested: {
            if (editor.recycleInProgress)
                return
            window.templateEditorMode = false
            experience.completeFirstRun()
            window.setPage("editor")
            window.requestNewProject()
        }
        onPlayRequested: {
            if (!editor.recycleInProgress) {
                experience.completeFirstRun()
                window.setPage("templates")
            }
        }
        onAiRequested: {
            if (!editor.recycleInProgress) {
                experience.completeFirstRun()
                window.setPage("editor")
                Qt.callLater(function() {
                    window.openAssistantWithPrompt("请先查看当前工程，然后帮我生成一小段好听的钢琴旋律。")
                })
            }
        }
        onImportRequested: {
            if (!editor.recycleInProgress) {
                experience.completeFirstRun()
                window.templateEditorMode = false
                window.setPage("editor")
                Qt.callLater(audioImportFileDialog.open)
            }
        }
        onHelpRequested: focusReturnTarget =>
            window.openSupportDialog(focusReturnTarget)
        onMidiImportRequested: {
            if (!editor.recycleInProgress) {
                experience.completeFirstRun()
                window.templateEditorMode = false
                window.setPage("editor")
                Qt.callLater(midiImportFileDialog.open)
            }
        }
        onOpenRequested: {
            if (!editor.recycleInProgress)
                window.requestOpenProject()
        }
        onRecentChosen: function(path) {
            window.templateEditorMode = false
            editor.openProject(path)
        }
        onRestoreRequested: {
            if (editor.recycleInProgress || !editor.recoveryAvailable)
                return
            if (editor.restoreRecovery()) {
                experience.completeFirstRun()
                window.setPage("editor")
            }
        }
        onDiscardRequested: editor.discardRecovery()
        onAppearanceRequested: focusReturnTarget =>
            window.openAppearancePopup(focusReturnTarget)
        onRenameRequested: function(project, focusReturnTarget) {
            window.openRenameProjectSurface(project, focusReturnTarget)
        }
        onDeleteRequested: function(project, focusReturnTarget) {
            window.pendingProject = project
            window.openSurface(deleteProjectDialog, focusReturnTarget)
        }
        onCopyRequested: project => editor.copyProject(project.path)
        onFavoriteRequested: project => editor.toggleProjectFavorite(project.path)
        onRevealRequested: project => window.revealProject(project)
        onExportRequested: project => {
            window.pendingProjectExport = project
            midiDialog.open()
        }
        onDismissed: {
            if (!editor.recycleInProgress)
                window.setPage("editor")
        }
    }

    TemplateMusicLibrary {
        id: templateMusicLibrary
        objectName: "templateMusicLibrary"
        anchors.fill: parent
        anchors.topMargin: window.appTheme.windowChromeHeight
        z: 10
        theme: window.appTheme
        service: window.templateService
        visible: window.pageState === "templates"
        enabled: visible
        onBackRequested: window.setPage("launch")
    }

    Connections {
        target: window.templateService
        function onProjectCreated(path) {
            experience.completeFirstRun()
            window.templateEditorMode = true
            editor.openProject(path)
        }
        function onErrorRaised(message) {
            window.showFeedback("danger", message)
        }
    }

    FeedbackHost {
        id: feedbackHost
        objectName: "feedbackHost"
        anchors.fill: parent
        anchors.rightMargin: window.studioMode && window.studioAssistantOpen && !window.studioAssistantDetached
            && window.studioAssistantHost !== null ? window.studioAssistantHost.width : 0
        z: 20
        theme: theme
    }

    TechnicalDetailsDialog {
        id: technicalDetailsDialog
        theme: theme
    }

    FileDialog {
        id: midiDialog
        title: "导出 MIDI 文件"
        fileMode: FileDialog.SaveFile
        nameFilters: ["MIDI (*.mid)"]
        defaultSuffix: "mid"
        onAccepted: {
            if (window.pendingProjectExport) {
                editor.exportProjectMidi(window.pendingProjectExport.path, selectedFile.toString())
                window.pendingProjectExport = null
            } else {
                editor.exportMidi(selectedFile.toString())
            }
        }
    }

    FileDialog {
        id: audioDialog
        objectName: "audioExportFileDialog"
        title: "导出音频"
        fileMode: FileDialog.SaveFile
        nameFilters: ["WAV 音频 (*.wav)"]
        defaultSuffix: "wav"
        onAccepted: playback.exportAudio(selectedFile.toString())
    }

    FileDialog {
        id: midiImportFileDialog
        objectName: "midiImportFileDialog"
        title: window.studioMode ? "导入文件" : "导入 MIDI 文件"
        fileMode: FileDialog.OpenFile
        nameFilters: window.studioMode ? [
            "所有支持的文件 (*.pianoai.json *.json *.mid *.midi *.wav *.mp3)",
            "钢琴工程 (*.pianoai.json *.json)",
            "MIDI 文件 (*.mid *.midi)",
            "音频文件 (*.wav *.mp3)"
        ] : ["MIDI 文件 (*.mid *.midi)"]
        onVisibleChanged: {
            if (visible && window.studioMode)
                selectedNameFilter.index = 0
        }
        onAccepted: window.studioMode
            ? window.importSelectedFile(selectedFile)
            : window.openMidiImportDialog(selectedFile)
    }

    FileDialog {
        id: audioImportFileDialog
        objectName: "audioImportFileDialog"
        title: "导入音频"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "WAV 音频 (*.wav)",
            "MP3 音频 (*.mp3)"
        ]
        onAccepted: window.openAudioImportDialog(selectedFile)
    }

    Connections {
        target: window.assistantController
        function onAudioImportRequested() {
            audioImportFileDialog.open()
        }
    }

    Connections {
        target: window.audioImportController
        function onFeedbackRequested(message) {
            window.showFeedback("danger", message)
        }
        function onImported(summary) {
            window.showSpecializedFeedback("success", "已导入旋律候选", {}, "")
        }
    }

    Connections {
        target: window.midiImportController
        function onFeedbackRequested(message) {
            window.showFeedback("danger", message)
        }
        function onImported(summary) {
            window.showSpecializedFeedback("success", summary, {}, "")
        }
    }

    Connections {
        target: editor
        function onSaveSucceeded(path) {
            window.pendingSaveOperation = ""
            if (window.libraryExitSavePending && editor.isDirty) {
                editor.saveProject(editor.currentFilePath)
                return
            }
            if (window.libraryExitSavePending && window.pendingLibraryExitPage.length > 0) {
                var target = window.pendingLibraryExitPage
                window.clearLibraryExitRequest()
                window.explicitSavePending = false
                window.showSpecializedFeedback("success", "已保存 “" + editor.currentFileName + "”", {}, "")
                window.completeLibraryExit(target)
                return
            }
            if (window.explicitSavePending)
                window.showSpecializedFeedback("success", "已保存 “" + editor.currentFileName + "”", {}, "")
            window.explicitSavePending = false
        }
        function onSaveFailed(message) {
            window.recordDiagnosticError("save_failed")
            var saveAndLeaveFailed = window.libraryExitSavePending
            var retryTarget = saveAndLeaveFailed
                ? window.pendingLibraryExitPage : ""
            var failedOperation = window.pendingSaveOperation
            if (saveAndLeaveFailed)
                window.clearLibraryExitRequest()
            window.pendingSaveOperation = ""
            window.explicitSavePending = false
            if (retryTarget.length > 0)
                window.restoreSurfaceFocus()
            window.showSpecializedFeedback("danger", "保存失败。请检查保存位置后重试", {
                    actionLabel: "重试",
                    actionCallback: retryTarget.length > 0
                        ? function() { window.retryLibraryExitSave(retryTarget) }
                        : failedOperation === "copy"
                            ? function() { window.requestSaveCopy() }
                        : function() { window.requestExplicitSave() },
                    secondaryActionLabel: "查看详情",
                    secondaryActionCallback: function() {
                        window.openTechnicalDetails("保存失败详情", message)
                    }
                }, "")
        }
        function onSavePathRequired() {
            if (window.pendingSaveOperation.length === 0)
                window.pendingSaveOperation = "save"
            window.openProjectNameSurface(
                "保存到工程库", "save", window.suggestedProjectName(), ""
            )
        }
        function onExternalSaveChoiceRequired() {
            externalSaveChoiceDialog.externalPath = editor.currentFilePath
            window.openSurface(externalSaveChoiceDialog, window.activeFocusItem)
        }
        function onProjectExported(path) {
            window.showSpecializedFeedback(
                "success", "已导出工程文件：" + window.exportedFileName(path), {
                    dismissAfterMs: 2000,
                    dismissible: true,
                    actionLabel: "打开文件夹",
                    actionCallback: function() { window.openExportFolder(path) }
                }, ""
            )
        }
        function onProjectExportFailed(message) {
            window.openTechnicalDetails("导出工程文件失败详情", message)
            window.showFeedback("danger", "导出工程文件失败")
        }
        function onLoadSucceeded(path) {
            window.setPage("editor")
            window.showSpecializedFeedback("success", "已打开", {}, "")
        }
        function onLoadFailed(message) {
            window.recordDiagnosticError("load_failed")
            window.showSpecializedFeedback("danger", "打开失败，请检查文件", {
                actionLabel: "重新选择",
                actionCallback: function() { openDialog.open() },
                secondaryActionLabel: "查看详情",
                secondaryActionCallback: function() {
                    window.openTechnicalDetails("打开工程失败详情", message)
                }
            }, "无法打开工程文件")
        }
        function onMidiExported(path) {
            window.showSpecializedFeedback("success", "已导出 MIDI：" + window.exportedFileName(path), {
                dismissAfterMs: 2000,
                dismissible: true,
                actionLabel: "打开文件夹",
                actionCallback: function() { window.openExportFolder(path) }
            }, "")
        }
        function onMidiFailed(message) {
            window.recordDiagnosticError("midi_export_failed")
            window.showSpecializedFeedback("danger", "导出 MIDI 失败", {
                actionLabel: "重试",
                actionCallback: function() { midiDialog.open() },
                secondaryActionLabel: "查看详情",
                secondaryActionCallback: function() {
                    window.openTechnicalDetails("导出 MIDI 失败详情", message)
                }
            }, "无法导出 MIDI")
        }
        function onRecycleFinished(succeeded, failed) {
            if (failed === 0) {
                window.showSpecializedFeedback("success", "已移动 " + succeeded + " 个工程到回收站", {},
                    "已移动 " + succeeded + " 个工程到回收站")
            } else {
                window.showSpecializedFeedback("warning", "已移动 " + succeeded + " 个工程到回收站，"
                    + failed + " 个未能移动", {
                        persistent: true,
                        actionLabel: "重试",
                        actionCallback: function() { welcomeOverlay.retryLastRecycle() }
                    }, "已移动 " + succeeded + " 个工程到回收站，" + failed + " 个未能移动")
            }
        }
        function onFeedbackRequested(message) {
            window.routeGenericFeedback(message)
        }
    }

    Connections {
        target: keyboard
        function onFeedbackRequested(message) {
            window.routeGenericFeedback(message)
        }
    }

    Connections {
        target: playback
        function onFeedbackRequested(message) {
            window.routeGenericFeedback(message)
        }
        function onAudioExported(path) {
            window.showSpecializedFeedback("success", "已导出 WAV：" + window.exportedFileName(path), {
                dismissAfterMs: 2000,
                dismissible: true,
                actionLabel: "打开文件夹",
                actionCallback: function() { window.openExportFolder(path) }
            }, "")
        }
        function onAudioFailed(message) {
            window.recordDiagnosticError("wav_export_failed")
            window.showSpecializedFeedback("danger", "导出音频失败", {
                actionLabel: "重试",
                actionCallback: function() { audioDialog.open() },
                secondaryActionLabel: "查看详情",
                secondaryActionCallback: function() {
                    window.openTechnicalDetails("导出音频失败详情", message)
                }
            }, "无法导出音频")
        }
    }
}
