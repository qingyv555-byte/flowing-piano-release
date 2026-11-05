import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt5Compat.GraphicalEffects

Rectangle {
    id: rail
    objectName: "projectRail"

    required property Theme theme
    property var entries: []
    property string currentPath: ""
    property bool isSaving: false
    property bool recycleInProgress: false
    property bool templateMode: false
    property bool templateRailExpanded: false
    property bool collapsed: false
    // `collapsed` remains a compatibility bridge for older callers and maps
    // to the icon-only state. The old intermediate rail is gone.
    property string displayMode: collapsed ? "icon" : "expanded"
    property bool focusMode: false
    property bool compactForViewport: false
    readonly property string effectiveDisplayMode: focusMode
        ? "icon"
        : (collapsed ? "icon"
            : (templateMode && !templateRailExpanded ? "icon"
                : (compactForViewport && displayMode === "expanded" ? "icon" : displayMode)))
    readonly property bool expanded: effectiveDisplayMode === "expanded"
    readonly property bool iconOnly: effectiveDisplayMode === "icon"
    readonly property bool compact: !expanded
        || (templateMode && !templateRailExpanded)
    readonly property bool blocked: isSaving || recycleInProgress
    property var menuProject: null
    property var menuAnchor: null
    property var editorBridge: null

    signal collapseRequested(bool collapsed)
    signal displayModeRequested(string displayMode)
    signal libraryRequested(var focusReturnTarget)
    signal newRequested(var focusReturnTarget)
    signal openRequested()
    signal projectChosen(string path)
    signal renameRequested(var project, var focusReturnTarget)
    signal deleteRequested(var project, var focusReturnTarget)
    signal copyRequested(var project)
    signal favoriteRequested(var project)
    signal exportRequested(var project)
    signal revealRequested(var project)

    onCollapsedChanged: collapseRequested(collapsed)

    function clearSearchFocus() {
        projectRailSearchField.clearInputFocus()
    }

    onTemplateModeChanged: {
        if (!templateMode)
            templateRailExpanded = false
    }

    readonly property real railWidth: expanded ? theme.projectRailWidth
        : theme.projectRailCollapsedWidth
    width: railWidth
    implicitWidth: width
    Layout.preferredWidth: railWidth
    Layout.minimumWidth: railWidth
    Layout.maximumWidth: railWidth
    color: theme.elevatedSurface

    Rectangle {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        width: 1
        color: rail.theme.borderSubtle
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: rail.theme.space2
        spacing: rail.theme.space2

        RowLayout {
            Layout.fillWidth: true
            spacing: rail.theme.space2

            Item {
                visible: !rail.templateMode && rail.expanded
                width: rail.theme.iconMedium
                height: rail.theme.iconMedium

                Image {
                    id: railMusicIcon
                    anchors.fill: parent
                    source: "icons/music-2.svg"
                    fillMode: Image.PreserveAspectFit
                }

                ColorOverlay {
                    objectName: "iconTint"
                    anchors.fill: railMusicIcon
                    source: railMusicIcon
                    color: rail.theme.textPrimary
                }
            }

            Label {
                Layout.fillWidth: true
                visible: !rail.templateMode && !rail.iconOnly
                text: "我的工程"
                color: rail.theme.textPrimary
                font.family: rail.theme.fontUi
                font.pixelSize: rail.theme.typeBodyStrong
                font.weight: rail.theme.weightBold
                elide: Text.ElideRight
            }

            AppButton {
                id: templateBackButton
                objectName: "templateBackButton"
                theme: rail.theme
                visible: rail.templateMode && !rail.compact
                Layout.fillWidth: true
                text: "返回首页"
                iconSource: "icons/arrow-left.svg"
                enabled: !rail.blocked
                accessibleName: "返回首页"
                onClicked: rail.libraryRequested(templateBackButton)
            }

            AppIconButton {
                id: projectRailToggle
                objectName: "projectRailToggle"
                theme: rail.theme
                visible: !rail.templateMode
                    || (rail.templateMode && rail.templateRailExpanded && !rail.compact)
                tooltip: rail.templateMode
                    ? "收起侧栏"
                    : (rail.expanded ? "收起为图标栏" : "展开工程栏")
                accessibleName: tooltip
                iconSource: rail.iconOnly
                    ? "icons/panel-left-open.svg" : "icons/panel-left-close.svg"
                onClicked: {
                    if (rail.templateMode)
                        rail.templateRailExpanded = false
                    else {
                        var nextMode = rail.expanded ? "icon" : "expanded"
                        rail.displayModeRequested(nextMode)
                        rail.collapseRequested(nextMode === "icon")
                    }
                }
            }
        }

        AppIconButton {
            id: templateCompactBackButton
            objectName: "templateCompactBackButton"
            theme: rail.theme
            visible: rail.templateMode && rail.compact
            Layout.alignment: Qt.AlignHCenter
            tooltip: "返回首页"
            accessibleName: tooltip
            iconSource: "icons/arrow-left.svg"
            enabled: !rail.blocked
            onClicked: rail.libraryRequested(templateCompactBackButton)
        }

        AppIconButton {
            id: templateCompactRailToggle
            objectName: "templateCompactRailToggle"
            theme: rail.theme
            visible: rail.templateMode && rail.compact
                && !rail.collapsed && !rail.compactForViewport
            Layout.alignment: Qt.AlignHCenter
            tooltip: "展开侧栏"
            accessibleName: tooltip
            iconSource: "icons/panel-left-open.svg"
            onClicked: {
                rail.templateRailExpanded = true
                rail.displayModeRequested("expanded")
            }
        }

        AppButton {
            id: backToLibraryButton
            objectName: "backToLibraryButton"
            theme: rail.theme
            visible: !rail.templateMode
            Layout.fillWidth: !rail.iconOnly
            Layout.alignment: Qt.AlignHCenter
            text: rail.iconOnly ? "" : "返回工程库"
            iconSource: "icons/arrow-left.svg"
            enabled: !rail.blocked
            accessibleName: "返回工程库"
            onClicked: rail.libraryRequested(backToLibraryButton)

            ToolTip.visible: rail.iconOnly && hovered
            ToolTip.delay: 400
            ToolTip.text: accessibleName
        }

        AppButton {
            id: libraryNewProjectButton
            objectName: "libraryNewProjectButton"
            theme: rail.theme
            visible: !rail.templateMode
            Layout.fillWidth: !rail.iconOnly
            Layout.alignment: Qt.AlignHCenter
            text: rail.iconOnly ? "" : "新建旋律"
            iconSource: "icons/plus.svg"
            enabled: !rail.blocked
            accessibleName: "新建工程"
            onClicked: rail.newRequested(libraryNewProjectButton)

            ToolTip.visible: rail.iconOnly && hovered
            ToolTip.delay: 400
            ToolTip.text: accessibleName
        }

        AppButton {
            id: projectRailOpenButton
            objectName: "projectRailOpenButton"
            theme: rail.theme
            visible: !rail.templateMode
            Layout.fillWidth: !rail.iconOnly
            Layout.alignment: Qt.AlignHCenter
            text: rail.iconOnly ? "" : "打开外部工程"
            iconSource: "icons/folder-open.svg"
            enabled: !rail.blocked
            accessibleName: "打开外部工程"
            onClicked: rail.openRequested()

            ToolTip.visible: rail.iconOnly && hovered
            ToolTip.delay: 400
            ToolTip.text: accessibleName
        }

        AppField {
            id: projectRailSearchField
            objectName: "projectRailSearchField"
            theme: rail.theme
            Layout.fillWidth: true
            visible: !rail.templateMode && rail.expanded
            label: "搜索工程"
            placeholderText: "按工程名称筛选"
            Accessible.name: "搜索工程"
        }

        Label {
            Layout.fillWidth: true
            visible: !rail.templateMode && rail.expanded
            text: "工程库"
            color: rail.theme.textSecondary
            font.family: rail.theme.fontUi
            font.pixelSize: rail.theme.typeCaption
            font.weight: rail.theme.weightSemibold
            Layout.topMargin: rail.theme.space1
        }

        ListView {
            id: libraryProjectList
            objectName: "libraryProjectList"
            Layout.fillWidth: true
            Layout.fillHeight: !rail.templateMode
            clip: true
            visible: !rail.templateMode
            spacing: rail.theme.space1
            model: rail.templateMode ? [] : rail.entries.filter(function(entry) {
                return rail.compact || !projectRailSearchField.text
                    || entry.name.toLowerCase().indexOf(projectRailSearchField.text.toLowerCase()) >= 0
            })
            delegate: ProjectRow {
                required property var modelData
                width: libraryProjectList.width
                theme: rail.theme
                entry: modelData
                mode: rail.expanded ? "full" : "compact"
                current: modelData.path === rail.currentPath
                blocked: rail.blocked
                enabled: modelData.exists !== false
                accessibleName: (modelData.path === rail.currentPath ? "当前工程：" : "工程：")
                    + (modelData.name || "未命名工程")
                onActivated: path => rail.projectChosen(path)
                onMenuRequested: function(project, anchor) {
                    rail.menuProject = project
                    rail.menuAnchor = anchor
                    projectCardMenu.popup()
                }
            }
        }

        // Keep an explicit icon-mode surface in the object tree so the
        // icon-only rail is discoverable to accessibility and UI smoke tests.
        // The action buttons above remain the live controls; this item adds
        // no layout height and therefore cannot leave a blank strip.
        Item {
            id: projectRailIconMode
            objectName: "projectRailIconMode"
            visible: rail.iconOnly
            Layout.fillWidth: true
            Layout.preferredHeight: 0
            Layout.minimumHeight: 0
            Layout.maximumHeight: 0
            height: 0
            implicitHeight: 0
        }
    }

    Column {
        id: templateModeIdentity
        objectName: "templateModeIdentity"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: rail.theme.space2
        anchors.rightMargin: rail.theme.space2
        anchors.topMargin: rail.theme.space2 + rail.theme.iconButtonSize + rail.theme.space3
        visible: rail.templateMode && !rail.compact
        spacing: rail.theme.space1

        Item {
            width: rail.theme.iconLarge
            height: rail.theme.iconLarge

            Image {
                id: templateModeMusicIcon
                anchors.fill: parent
                source: "icons/music-2.svg"
                fillMode: Image.PreserveAspectFit
            }

            ColorOverlay {
                anchors.fill: templateModeMusicIcon
                source: templateModeMusicIcon
                color: rail.theme.accentPrimary
            }
        }

        Label {
            id: templateModeTitle
            objectName: "templateModeTitle"
            width: parent.width
            text: "示例乐曲"
            color: rail.theme.textPrimary
            font.family: rail.theme.fontUi
            font.pixelSize: rail.theme.typeSection
            font.weight: rail.theme.weightBold
            elide: Text.ElideRight
        }

        Label {
            objectName: "templateModeSubtitle"
            width: parent.width
            text: "正在改编内置乐曲"
            color: rail.theme.textSecondary
            font.family: rail.theme.fontUi
            font.pixelSize: rail.theme.typeSmall
            wrapMode: Text.Wrap
        }
    }

    component ProjectCardMenuItem: AppMenuItem {
        theme: rail.theme
        readonly property color textColor: foregroundColor
        readonly property color hoverBackgroundColor: theme.surfaceHover
    }

    AppMenu {

        theme: rail.theme
        id: projectCardMenu
        objectName: "projectCardMenu"
        background: Rectangle {
            implicitWidth: 200
            radius: rail.theme.radiusM
            color: rail.theme.elevatedSurface
            border.color: rail.theme.borderStrong
        }
        ProjectCardMenuItem {
            objectName: "projectMenuOpenAction"
            text: "打开"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: {
                if (rail.menuProject)
                    rail.projectChosen(rail.menuProject.path)
            }
        }
        ProjectCardMenuItem {
            text: "重命名"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: rail.renameRequested(rail.menuProject, rail.menuAnchor)
        }
        ProjectCardMenuItem {
            text: "创建副本"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: rail.copyRequested(rail.menuProject)
        }
        ProjectCardMenuItem {
            objectName: "projectMenuRevealAction"
            text: "在资源管理器中显示"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: rail.revealRequested(rail.menuProject)
        }
        ProjectCardMenuItem {
            text: rail.menuProject && rail.menuProject.favorite ? "取消收藏" : "收藏"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: rail.favoriteRequested(rail.menuProject)
        }
        ProjectCardMenuItem {
            text: "导出"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: rail.exportRequested(rail.menuProject)
        }
        AppMenuSeparator { theme: rail.theme;}
        ProjectCardMenuItem {
            objectName: "projectDeleteAction"
            destructive: true
            text: "移动到回收站"
            enabled: !rail.blocked && rail.menuProject !== null
            onTriggered: rail.deleteRequested(rail.menuProject, rail.menuAnchor)
        }
    }
}
