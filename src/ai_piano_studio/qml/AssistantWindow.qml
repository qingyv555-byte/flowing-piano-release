import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "assistantWindow"
    width: 1080
    height: 900
    minimumWidth: 860
    minimumHeight: 640
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "AI 助手 · " + (theme.studio ? "Flowing Piano" : "AI Piano Studio")
    visible: false
    transientParent: null
    color: theme.appBackground
    palette.window: theme.popupBackground
    palette.windowText: theme.textPrimary
    palette.text: theme.textPrimary
    palette.button: theme.surface
    palette.buttonText: theme.textPrimary
    palette.base: theme.inputBackground
    palette.placeholderText: theme.textMuted
    palette.highlight: theme.accentPrimary
    palette.highlightedText: theme.textOnAccent
    palette.light: theme.elevatedSurface
    palette.mid: theme.borderStrong
    palette.dark: theme.borderSubtle

    property alias assistant: workspace.assistant
    readonly property bool ready: assistant !== null
    readonly property var theme: workspace.theme
    property alias contextExpanded: workspace.contextExpanded
    property alias followLatest: workspace.followLatest
    property alias userIsAtBottom: workspace.userIsAtBottom
    property alias hasUnreadIncoming: workspace.hasUnreadIncoming
    readonly property var modelStatus: workspace.modelStatus
    readonly property bool connectionConfigured: workspace.connectionConfigured
    readonly property string connectionTitle: workspace.connectionTitle
    readonly property int configuredMinimumWidth: 860
    readonly property int configuredMinimumHeight: 640
    property bool applyingRestoredGeometry: false
    property bool geometryRestored: false
    readonly property bool hasUnsavedConnection: workspace.hasUnsavedConnection
    function requestWindowClose(action) { workspace.requestWindowClose(action) }
    function openConnectionSettings() { workspace.openConnectionSettings() }
    function prefillMessage(prompt) { workspace.prefillMessage(prompt) }
    function prefillQuickAction(actionId, prompt) { workspace.prefillQuickAction(actionId, prompt) }
    function scrollToBottom() { workspace.scrollToBottom() }
    function sendAssistantMessage() { workspace.sendAssistantMessage() }
    WindowChrome {
        theme: root.theme; hostWindow: root
        productName: root.theme.studio ? "Flowing Piano" : "AI Piano Studio"
        contextText: "AI 助手"
        anchors.fill: parent
    }
    AssistantWorkspace {
        id: workspace
        anchors.fill: parent
        anchors.topMargin: root.theme.windowChromeHeight
        onCloseRequested: root.close()
    }
    function restoreNormalGeometry() {
        if (!root.ready)
            return
        var geometry = root.assistant.restoreWindowGeometry()
        root.applyingRestoredGeometry = true
        root.minimumWidth = geometry.minimumWidth === undefined
            ? root.configuredMinimumWidth
            : geometry.minimumWidth
        root.minimumHeight = geometry.minimumHeight === undefined
            ? root.configuredMinimumHeight
            : geometry.minimumHeight
        root.x = geometry.x
        root.y = geometry.y
        root.width = geometry.width
        root.height = geometry.height
        root.applyingRestoredGeometry = false
        root.geometryRestored = true
    }

    function saveNormalGeometry() {
        if (!root.ready || !root.geometryRestored || root.applyingRestoredGeometry)
            return
        if (root.visibility !== Window.Windowed)
            return
        root.assistant.saveWindowGeometry(root.x, root.y, root.width, root.height)
    }

    onXChanged: root.saveNormalGeometry()
    onYChanged: root.saveNormalGeometry()
    onWidthChanged: root.saveNormalGeometry()
    onHeightChanged: root.saveNormalGeometry()
    onVisibilityChanged: root.saveNormalGeometry()
    Component.onCompleted: root.restoreNormalGeometry()

    onClosing: function(close) {
        close.accepted = false
        if (root.hasUnsavedConnection) workspace.requestWindowClose(function() { root.visible = false })
        else root.visible = false
    }

    Connections {
        target: root.assistant
        function onWindowRequested() {
            if (root.visibility === Window.Minimized)
                root.showNormal()
            root.visible = true
            root.raise()
            root.requestActivate()
        }
        function onWindowCloseRequested() {
            root.visible = false
        }
    }

}
