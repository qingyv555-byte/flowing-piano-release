import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: library
    objectName: "welcomeLibraryView"

    required property Theme theme
    property var entries: []
    property int totalCount: 0
    property int filteredCount: 0
    property string currentPath: ""
    property string searchQuery: ""
    property int page: 1
    property int pageCount: 1
    property bool managing: false
    property var selectedPaths: ({})
    property int selectedCount: 0
    property bool recycleInProgress: false
    property int recycleProgress: 0
    property int recycleTotal: 0
    property var menuProject: null
    property var menuAnchor: null
    readonly property bool actionsEnabled: !recycleInProgress
    readonly property int contentMaxWidth: 960

    signal backRequested()
    signal searchEdited(string query)
    signal managementToggled()
    signal projectChosen(string path)
    signal openRequested()
    signal selectionToggled(string path)
    signal removeRequested(string path)
    signal pageRequested(int page)
    signal bulkRecycleRequested()
    signal renameRequested(var project, var focusReturnTarget)
    signal deleteRequested(var project, var focusReturnTarget)
    signal copyRequested(var project)
    signal favoriteRequested(var project)
    signal exportRequested(var project)
    signal revealRequested(var project)

    function focusBack() { backButton.forceActiveFocus() }
    function focusSearch() { searchField.forceActiveFocus() }
    function focusCurrentProject(path) {
        for (var index = 0; index < entries.length; index += 1) {
            if (entries[index].path === path) {
                projectList.currentIndex = index
                projectList.positionViewAtIndex(index, ListView.Contain)
                Qt.callLater(function() {
                    if (projectList.currentItem)
                        projectList.currentItem.forceActiveFocus()
                })
                return
            }
        }
        focusSearch()
    }

    Keys.onEscapePressed: event => {
        event.accepted = true
        backRequested()
    }

    Rectangle {
        id: topBand
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 64
        color: library.theme.elevatedSurface
        border.color: library.theme.borderSubtle

        RowLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 32, library.contentMaxWidth)
            spacing: 12

            AppButton {
                id: backButton
                objectName: "libraryBackButton"
                text: "返回"
                variant: "ghost"
                theme: library.theme
                onClicked: library.backRequested()
                Accessible.name: "返回开始页"
            }

            Label {
                text: "我的工程"
                color: library.theme.textPrimary
                font.pixelSize: 18
                font.bold: true
            }

            Item { Layout.fillWidth: true }

            Label {
                visible: library.totalCount > 0
                text: library.filteredCount + " / " + library.totalCount + " 个工程"
                color: library.theme.textMuted
                font.pixelSize: 12
            }
        }
    }

    ColumnLayout {
        anchors.top: topBand.bottom
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        width: library.theme.studio ? parent.width : Math.min(parent.width - 32, library.contentMaxWidth)
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.maximumWidth: library.theme.studio ? Math.min(library.width - 32, library.contentMaxWidth) : Infinity
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 16
            spacing: 8

            Label {
                text: "搜索工程"
                color: library.theme.textSecondary
                font.pixelSize: 13
                font.bold: true
            }

            TextField {
                id: searchField
                objectName: "projectSearchField"
                Layout.fillWidth: true
                enabled: library.actionsEnabled
                placeholderText: "按工程名称搜索"
                text: library.searchQuery
                selectByMouse: true
                onTextEdited: library.searchEdited(text)
                color: library.theme.textPrimary
                placeholderTextColor: library.theme.textMuted
                font.family: library.theme.fontUi
                implicitHeight: library.theme.controlHeight
                leftPadding: 12
                rightPadding: 12
                background: Rectangle {
                    radius: library.theme.radiusM
                    color: library.theme.inputBackground
                    border.width: searchField.activeFocus ? 2 : 1
                    border.color: searchField.activeFocus ? library.theme.focusRing : library.theme.borderStrong
                }
                Accessible.name: "搜索工程名称"
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.maximumWidth: library.theme.studio ? Math.min(library.width - 32, library.contentMaxWidth) : Infinity
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredHeight: 48
            visible: library.managing
            color: library.theme.elevatedSurface
            border.color: library.theme.borderStrong
            radius: library.theme.radiusM

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 10
                spacing: 10

                Label {
                    visible: !library.recycleInProgress
                    Layout.fillWidth: true
                    text: library.selectedCount === 0
                        ? "选择要整理的工程"
                        : "已选择 " + library.selectedCount + " 个工程"
                    color: library.theme.textSecondary
                    font.pixelSize: 12
                }

                AppButton {
                    objectName: "bulkDeleteProjectsButton"
                    visible: !library.recycleInProgress
                    text: "移到回收站"
                    variant: "danger"
                    enabled: library.selectedCount > 0 && library.actionsEnabled
                    theme: library.theme
                    onClicked: library.bulkRecycleRequested()
                    Accessible.name: "将所选工程移动到回收站"
                }

                ColumnLayout {
                    visible: library.recycleInProgress
                    Layout.fillWidth: true
                    spacing: 4

                    Label {
                        text: "正在移入回收站 " + library.recycleProgress + " / " + library.recycleTotal
                        color: library.theme.textSecondary
                        font.pixelSize: 12
                    }

                    ProgressBar {
                        objectName: "bulkRecycleProgressBar"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, library.recycleTotal)
                        value: library.recycleProgress
                        Accessible.name: "工程回收进度"
                    }
                }
            }
        }

        AppButton {
            objectName: "libraryManagementButton"
            visible: library.totalCount > 0
            Layout.alignment: Qt.AlignRight
            Layout.rightMargin: library.theme.studio ? Math.max(16, (library.width - library.contentMaxWidth) / 2) : 0
            text: library.managing ? "完成" : "整理工程"
            enabled: library.actionsEnabled
            theme: library.theme
            onClicked: library.managementToggled()
            Accessible.name: library.managing ? "完成工程整理" : "整理工程并批量选择"
        }

        Item {
            visible: library.totalCount === 0 || (library.totalCount > 0 && library.filteredCount === 0)
            Layout.fillWidth: true
            Layout.fillHeight: true

            Label {
                anchors.centerIn: parent
                text: library.totalCount === 0
                    ? "还没有作品，返回首页新建或导入音乐。"
                    : "没有找到匹配的工程，请尝试其他名称"
                color: library.theme.textSecondary
                font.pixelSize: 13
                width: Math.min(parent.width - 32, implicitWidth)
                wrapMode: Text.WordWrap
            }
        }

        ListView {
            id: projectList
            objectName: "recentList"
            visible: library.entries.length > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            model: library.entries
            boundsBehavior: Flickable.StopAtBounds
            rightMargin: library.theme.studio ? 0 : library.theme.scrollbarGutter
            ScrollBar.vertical: SafeScrollBar {
                id: libraryScrollBar
                objectName: "libraryScrollBar"
                theme: library.theme
                visible: !library.theme.studio || projectList.contentHeight > projectList.height + 1
                // Keep pointer input clear of the window's 6px resize hit area.
                anchors.right: parent.right
                anchors.rightMargin: library.theme.studio ? 12 : library.theme.scrollbarRightInset
                contentItem: Rectangle {
                    implicitWidth: library.theme.scrollbarWidth
                    radius: width / 2
                    color: library.theme.studio ? library.theme.textMuted
                        : (libraryScrollBar.pressed ? library.theme.accentPrimaryPressed
                            : (libraryScrollBar.hovered ? library.theme.accentPrimaryHover : library.theme.accentPrimary))
                    opacity: libraryScrollBar.active ? 0.94 : 0.7
                }
            }

            delegate: Item {
                id: projectDelegate
                objectName: "recentItem"
                required property var modelData
                width: library.theme.studio ? projectList.width : Math.max(0, projectList.width - library.theme.scrollbarGutter)
                height: 64

                ProjectRow {
                    id: projectRow
                    anchors.horizontalCenter: parent.horizontalCenter
                    height: parent.height
                    width: library.theme.studio ? Math.min(parent.width - 32, library.contentMaxWidth) : parent.width
                    theme: library.theme
                    mode: "full"
                    entry: projectDelegate.modelData
                    current: entry.path === library.currentPath
                    managing: library.managing
                    selected: library.selectedPaths[entry.path] === true
                    enabled: library.actionsEnabled
                    blocked: library.managing && (!entry.exists || !entry.isLibraryProject || entry.path === library.currentPath)
                    onActivated: path => library.projectChosen(path)
                    onSelectionToggled: path => library.selectionToggled(path)
                    onMenuRequested: function(project, anchor) {
                        library.menuProject = project
                        library.menuAnchor = anchor
                        libraryProjectCardMenu.popup()
                    }
                }

                AppButton {
                    anchors.right: projectRow.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    visible: !projectDelegate.modelData.exists && !library.managing
                    enabled: library.actionsEnabled
                    text: "从列表移除"
                    theme: library.theme
                    z: 1
                    onClicked: library.removeRequested(projectDelegate.modelData.path)
                    Accessible.name: "从最近列表移除"
                }
            }
        }

        RowLayout {
            visible: library.filteredCount > 0
            enabled: library.actionsEnabled
            Layout.alignment: Qt.AlignHCenter
            Layout.bottomMargin: 16
            spacing: 8

            AppButton {
                objectName: "previousLibraryPageButton"
                text: "上一页"
                enabled: library.actionsEnabled && library.page > 1
                theme: library.theme
                onClicked: library.pageRequested(library.page - 1)
                Accessible.name: "上一页工程"
            }

            Label {
                text: "第 " + library.page + " / " + library.pageCount + " 页"
                color: library.theme.textSecondary
                font.pixelSize: 12
            }

            TextField {
                id: libraryPageInput
                objectName: "libraryPageInput"
                Layout.preferredWidth: 52
                enabled: library.actionsEnabled
                text: String(library.page)
                horizontalAlignment: TextInput.AlignHCenter
                inputMethodHints: Qt.ImhDigitsOnly
                selectByMouse: true
                validator: IntValidator { bottom: 1; top: library.pageCount }
                onAccepted: library.pageRequested(text)
                Accessible.name: "工程页码"
            }

            AppButton {
                objectName: "jumpLibraryPageButton"
                text: "跳转"
                enabled: library.actionsEnabled
                theme: library.theme
                onClicked: library.pageRequested(libraryPageInput.text)
                Accessible.name: "跳转到输入的工程页码"
            }

            AppButton {
                objectName: "nextLibraryPageButton"
                text: "下一页"
                enabled: library.actionsEnabled && library.page < library.pageCount
                theme: library.theme
                onClicked: library.pageRequested(library.page + 1)
                Accessible.name: "下一页工程"
            }
        }
    }

    AppMenu {

        theme: library.theme
        id: libraryProjectCardMenu
        objectName: "libraryProjectCardMenu"

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectOpenAction"
            text: "打开"
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: {
                if (library.menuProject)
                    library.projectChosen(library.menuProject.path)
            }
        }

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectRenameAction"
            text: "重命名"
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: library.renameRequested(library.menuProject, library.menuAnchor)
        }

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectCopyAction"
            text: "创建副本"
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: library.copyRequested(library.menuProject)
        }

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectRevealAction"
            text: "在资源管理器中显示"
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: library.revealRequested(library.menuProject)
        }

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectFavoriteAction"
            text: library.menuProject && library.menuProject.favorite
                ? "取消收藏" : "收藏"
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: library.favoriteRequested(library.menuProject)
        }

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectExportAction"
            text: "导出"
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: library.exportRequested(library.menuProject)
        }

        AppMenuSeparator { theme: library.theme;}

        AppMenuItem {

            theme: library.theme
            objectName: "libraryProjectDeleteAction"
            text: "移动到回收站"
            destructive: true
            enabled: library.actionsEnabled && library.menuProject !== null
            onTriggered: library.deleteRequested(library.menuProject, library.menuAnchor)
        }
    }
}
