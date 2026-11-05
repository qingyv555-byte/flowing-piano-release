import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Welcome shell: focused launch actions and a retained project library.
Rectangle {
    id: welcome
    objectName: "welcomeOverlay"
    color: "transparent"

    required property Theme theme
    property bool firstRun: false
    property bool introAnimation: true
    property string pageState: "launch"
    property bool libraryManaging: false
    property var librarySelection: ({})
    property int projectsPerPage: 10
    property int libraryPage: 1
    property string librarySearchQuery: ""
    property var lastRecyclePaths: []
    property bool focusCurrentProject: false
    property bool launchVisitRecorded: false
    property bool returningToLaunch: false
    readonly property string returnAnimationMode: !scene.dynamicEnabled || scene.powerSaveEnabled
        ? "none"
        : (scene.motionLevel === "low" ? "fade" : "full")
    readonly property bool launchAnimationRunning: !theme.studio && launchView.returnAnimationRunning
    readonly property int selectedProjectCount: Object.keys(librarySelection).length
    readonly property var filteredLibraryEntries: {
        var query = librarySearchQuery.trim().toLocaleLowerCase()
        if (!query)
            return editor.libraryEntries
        return editor.libraryEntries.filter(function(entry) {
            return String(entry.name).toLocaleLowerCase().indexOf(query) !== -1
        })
    }
    readonly property int libraryPageCount: Math.max(
        1, Math.ceil(filteredLibraryEntries.length / projectsPerPage)
    )
    readonly property var pagedLibraryEntries: filteredLibraryEntries.slice(
        (libraryPage - 1) * projectsPerPage, libraryPage * projectsPerPage
    )

    onLibrarySearchQueryChanged: libraryPage = 1
    onLibraryPageCountChanged: {
        if (libraryPage > libraryPageCount)
            libraryPage = libraryPageCount
    }

    signal newRequested()
    signal playRequested()
    signal libraryRequested()
    signal openRequested()
    signal scoreLabRequested()
    signal aiRequested()
    signal importRequested()
    signal midiImportRequested()
    signal helpRequested(var focusReturnTarget)
    signal recentChosen(string path)
    signal restoreRequested()
    signal discardRequested()
    signal dismissed()
    signal appearanceRequested(var focusReturnTarget)
    signal pageChangeRequested(string nextPage, bool focusCurrentProject)
    signal renameRequested(var project, var focusReturnTarget)
    signal deleteRequested(var project, var focusReturnTarget)
    signal copyRequested(var project)
    signal favoriteRequested(var project)
    signal exportRequested(var project)
    signal revealRequested(var project)

    function isProjectSelected(path) {
        return librarySelection[path] === true
    }

    function toggleProjectSelection(path) {
        var next = Object.assign({}, librarySelection)
        if (next[path])
            delete next[path]
        else
            next[path] = true
        librarySelection = next
    }

    function selectedProjectPaths() {
        return Object.keys(librarySelection)
    }

    function clearProjectSelection() {
        librarySelection = ({})
    }

    function rememberRecycle(paths) {
        lastRecyclePaths = paths.slice(0)
    }

    function retryLastRecycle() {
        if (lastRecyclePaths.length > 0 && !editor.recycleInProgress)
            editor.recycleProjects(lastRecyclePaths)
    }

    function goToLibraryPage(value) {
        var requested = Number(value)
        if (!isFinite(requested))
            return
        libraryPage = Math.max(1, Math.min(libraryPageCount, Math.round(requested)))
    }

    function showLibrary(focusCurrentProject) {
        welcome.focusCurrentProject = focusCurrentProject === true
        pageChangeRequested("library", focusCurrentProject === true)
    }

    function focusForCurrentPage() {
        if (pageState === "library") {
            if (focusCurrentProject)
                libraryView.focusCurrentProject(editor.currentFilePath)
            else
                libraryView.focusBack()
        } else if (pageState === "launch") {
            if (welcome.theme.studio) {
                if (studioLaunch.item) studioLaunch.item.focusPrimaryAction()
                return
            }
            if (welcome.theme.nocturne) {
                if (nocturneLaunch.item) nocturneLaunch.item.focusPrimaryAction()
                return
            }
            if (launchAnimationRunning)
                return
            launchView.focusPrimaryAction()
        }
    }

    function showLaunch() {
        focusCurrentProject = false
        pageChangeRequested("launch", false)
    }

    Component.onCompleted: {
        launchVisitRecorded = pageState === "launch"
        Qt.callLater(focusForCurrentPage)
    }

    onPageStateChanged: {
        if (pageState === "launch" && launchVisitRecorded) {
            returningToLaunch = true
            if (!welcome.theme.studio && !welcome.theme.nocturne) launchView.playReturnTransition(returnAnimationMode)
        } else if (pageState === "launch") {
            launchVisitRecorded = true
        }
        focusForCurrentPage()
    }

    onLibraryRequested: showLibrary(false)

    Shortcut {
        sequence: "Escape"
        enabled: welcome.visible && welcome.pageState === "library"
        onActivated: welcome.showLaunch()
    }

    CosmicKeyBackdrop {
        objectName: "cosmicKeyBackdrop"
        anchors.fill: parent
        theme: welcome.theme
        visible: !welcome.theme.studio && !welcome.theme.nocturne
        dynamicEnabled: visible && scene.dynamicEnabled
        motionLevel: scene.motionLevel
        powerSaveEnabled: scene.powerSaveEnabled
        introAnimation: welcome.introAnimation
    }

    MouseArea {
        objectName: "welcomeInputShield"
        anchors.fill: parent
        z: -1
        enabled: welcome.visible
        acceptedButtons: Qt.AllButtons
        preventStealing: true
        onPressed: mouse => mouse.accepted = true
        onReleased: mouse => mouse.accepted = true
        onWheel: wheel => wheel.accepted = true
    }

    Item {
        id: launchPage
        anchors.fill: parent
        visible: welcome.pageState === "launch" && !welcome.theme.studio && !welcome.theme.nocturne

        Rectangle {
            id: homeNavigation
            objectName: "homeNavigation"
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: Math.min(190, Math.max(160, parent.width * 0.15))
            color: Qt.rgba(
                welcome.theme.surface.r,
                welcome.theme.surface.g,
                welcome.theme.surface.b,
                0.82
            )
            border.width: 0

            ColumnLayout {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: welcome.theme.space3
                spacing: welcome.theme.space2

                AppButton {
                    objectName: "homeNavHome"
                    text: "首页"
                    iconSource: Qt.resolvedUrl("icons/music-2.svg")
                    variant: "secondary"
                    checkable: true
                    checked: true
                    checkedExternallyControlled: true
                    theme: welcome.theme
                    Layout.fillWidth: true
                    accessibleName: "首页，当前页面"
                }

                AppButton {
                    objectName: "homeNavProjects"
                    text: "我的工程"
                    iconSource: Qt.resolvedUrl("icons/folder.svg")
                    variant: "ghost"
                    theme: welcome.theme
                    Layout.fillWidth: true
                    onClicked: welcome.libraryRequested()
                }

                AppButton {
                    objectName: "homeNavAssets"
                    text: "音乐素材"
                    iconSource: Qt.resolvedUrl("icons/file-up.svg")
                    variant: "ghost"
                    theme: welcome.theme
                    Layout.fillWidth: true
                    onClicked: welcome.importRequested()
                }

                AppButton {
                    objectName: "homeNavExamples"
                    text: "示例作品"
                    iconSource: Qt.resolvedUrl("icons/play.svg")
                    variant: "ghost"
                    theme: welcome.theme
                    Layout.fillWidth: true
                    onClicked: welcome.playRequested()
                }

                AppButton {
                    objectName: "homeNavHelp"
                    text: "使用帮助"
                    iconSource: Qt.resolvedUrl("icons/lightbulb.svg")
                    variant: "ghost"
                    theme: welcome.theme
                    Layout.fillWidth: true
                    onClicked: welcome.helpRequested(this)
                }
            }
        }

        WelcomeLaunchView {
            id: launchView
            anchors.top: parent.top
            anchors.topMargin: Math.max(
                welcome.theme.space6,
                Math.min(96, (contentArea.height - launchView.implicitHeight) * 0.18)
            )
            anchors.horizontalCenter: contentArea.horizontalCenter
            width: Math.min(contentArea.width - welcome.theme.space8, 1100)
            theme: welcome.theme
            firstRun: welcome.firstRun
            recoveryAvailable: editor.recoveryAvailable
            recoveryInfo: editor.recoveryInfo
            recentEntries: editor.libraryEntries.slice(0, 4)
            actionsEnabled: !editor.recycleInProgress
                && !welcome.launchAnimationRunning
            introAnimation: !welcome.theme.studio && welcome.introAnimation && scene.dynamicEnabled
                && !scene.powerSaveEnabled
            onReturnAnimationFinished: {
                welcome.returningToLaunch = false
                welcome.focusForCurrentPage()
            }
            onPlayRequested: welcome.playRequested()
            onNewRequested: welcome.newRequested()
            onLibraryRequested: welcome.libraryRequested()
            onAiRequested: welcome.aiRequested()
            onImportRequested: welcome.importRequested()
            onRecentChosen: path => welcome.recentChosen(path)
            onRestoreRequested: welcome.restoreRequested()
            onDiscardRequested: welcome.discardRequested()
            onAppearanceRequested: focusReturnTarget =>
                welcome.appearanceRequested(focusReturnTarget)
        }

        Item {
            id: contentArea
            anchors.left: homeNavigation.right
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
        }
    }

    MouseArea {
        id: returnInputShield
        objectName: "welcomeReturnInputShield"
        anchors.fill: parent
        z: 8
        visible: welcome.pageState === "launch" && welcome.launchAnimationRunning
        enabled: visible
        acceptedButtons: Qt.AllButtons
        preventStealing: true
        onPressed: mouse => mouse.accepted = true
        onReleased: mouse => mouse.accepted = true
        onWheel: wheel => wheel.accepted = true
    }

    Loader {
        id: nocturneLaunch
        readonly property var welcomeHost: welcome
        anchors.fill: parent
        active: welcome.theme.nocturne
        visible: welcome.pageState === "launch"
        sourceComponent: NocturneHome { welcome: nocturneLaunch.welcomeHost }
    }

    Loader {
        id: studioLaunch
        readonly property var welcomeHost: welcome
        anchors.fill: parent
        active: welcome.theme.studio && welcome.pageState === "launch"
        sourceComponent: StudioHome { welcome: studioLaunch.welcomeHost }
    }

    WelcomeLibraryView {
        id: libraryView
        anchors.fill: parent
        visible: welcome.pageState === "library"
        theme: welcome.theme
        entries: welcome.pagedLibraryEntries
        totalCount: editor.libraryEntries.length
        filteredCount: welcome.filteredLibraryEntries.length
        currentPath: editor.currentFilePath
        searchQuery: welcome.librarySearchQuery
        page: welcome.libraryPage
        pageCount: welcome.libraryPageCount
        managing: welcome.libraryManaging
        selectedPaths: welcome.librarySelection
        selectedCount: welcome.selectedProjectCount
        recycleInProgress: editor.recycleInProgress
        recycleProgress: editor.recycleProgress
        recycleTotal: editor.recycleTotal
        onBackRequested: welcome.showLaunch()
        onSearchEdited: query => welcome.librarySearchQuery = query
        onManagementToggled: {
            welcome.libraryManaging = !welcome.libraryManaging
            welcome.clearProjectSelection()
        }
        onProjectChosen: path => welcome.recentChosen(path)
        onOpenRequested: welcome.openRequested()
        onSelectionToggled: path => welcome.toggleProjectSelection(path)
        onRemoveRequested: path => editor.removeRecent(path)
        onPageRequested: page => welcome.goToLibraryPage(page)
        onBulkRecycleRequested: bulkDeleteProjectDialog.open()
        onRenameRequested: (project, focusReturnTarget) =>
            welcome.renameRequested(project, focusReturnTarget)
        onDeleteRequested: (project, focusReturnTarget) =>
            welcome.deleteRequested(project, focusReturnTarget)
        onCopyRequested: project => welcome.copyRequested(project)
        onFavoriteRequested: project => welcome.favoriteRequested(project)
        onExportRequested: project => welcome.exportRequested(project)
        onRevealRequested: project => welcome.revealRequested(project)
    }

    Action {
        objectName: "welcomeBlankButton"
        enabled: !editor.recycleInProgress
        onTriggered: welcome.dismissed()
    }

    Dialog {
        id: bulkDeleteProjectDialog
        objectName: "bulkDeleteProjectDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 430
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        padding: 18
        background: Rectangle {
            radius: welcome.theme.radiusM
            color: welcome.theme.elevatedSurface
            border.color: welcome.theme.danger
            border.width: 1
        }
        header: Label {
            leftPadding: 18
            rightPadding: 18
            topPadding: 18
            text: "将所选工程移到回收站？"
            color: welcome.theme.textPrimary
            font.pixelSize: 18
            font.bold: true
        }
        contentItem: Label {
            width: parent.width
            text: "将移动 " + welcome.selectedProjectCount
                + " 个工程到 Windows 回收站，你可以随后在系统中恢复它们。"
            color: welcome.theme.textSecondary
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
                    theme: welcome.theme
                    onClicked: bulkDeleteProjectDialog.close()
                }
                AppButton {
                    objectName: "bulkDeleteProjectsConfirmButton"
                    text: "移到回收站"
                    variant: "danger"
                    theme: welcome.theme
                    onClicked: {
                        var paths = welcome.selectedProjectPaths()
                        welcome.rememberRecycle(paths)
                        editor.recycleProjects(paths)
                        welcome.clearProjectSelection()
                        bulkDeleteProjectDialog.close()
                    }
                }
            }
        }
    }
}
