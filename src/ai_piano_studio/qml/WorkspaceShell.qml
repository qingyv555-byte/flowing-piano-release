import QtQuick

Item {
    id: shell
    objectName: "workspaceShell"

    property real availableWidth: 1440
    property real minimumCanvasWidth: 640
    property real leftExpandedWidth: 224
    property real leftIconWidth: 64
    property real inspectorExpandedWidth: 304
    property real compactDualPanelWidth: 1366

    property bool leftContentOpen: true
    property bool inspectorOpen: true
    property bool arrangementOpen: false
    property bool focusMode: false
    property bool panelsAvailableInFocus: false
    property bool focusTransitioning: false

    readonly property real leftLayoutWidth: focusMode
        ? 0
        : (leftContentOpen ? leftExpandedWidth : leftIconWidth)
    readonly property real inspectorLayoutWidth: (!focusMode || panelsAvailableInFocus) && inspectorOpen
        ? inspectorExpandedWidth : 0

    property bool _leftBeforeFocus: true
    property bool _inspectorBeforeFocus: true
    property bool _arrangementBeforeFocus: false

    signal leftContentResolved(bool open)
    signal inspectorResolved(bool open)
    signal arrangementResolved(bool open)
    signal focusResolved(bool enabled)

    function panelsFit(leftOpen: bool, rightOpen: bool): bool {
        if (leftOpen && rightOpen && availableWidth <= compactDualPanelWidth)
            return false
        var leftWidth = leftOpen ? leftExpandedWidth : leftIconWidth
        var rightWidth = rightOpen ? inspectorExpandedWidth : 0
        return leftWidth + minimumCanvasWidth + rightWidth <= availableWidth
    }

    function requestLeftContent(open: bool): void {
        if (focusMode)
            return
        if (open && inspectorOpen && !panelsFit(true, true)) {
            inspectorOpen = false
            inspectorResolved(false)
        }
        leftContentOpen = open
        leftContentResolved(open)
    }

    function requestInspector(open: bool): void {
        if (focusMode && !panelsAvailableInFocus)
            return
        if (open && leftContentOpen && !panelsFit(true, true)) {
            leftContentOpen = false
            leftContentResolved(false)
        }
        inspectorOpen = open
        if (focusMode)
            _inspectorBeforeFocus = open
        inspectorResolved(open)
    }

    function requestArrangement(open: bool): void {
        if (focusMode && !panelsAvailableInFocus)
            return
        arrangementOpen = open
        if (focusMode)
            _arrangementBeforeFocus = open
        arrangementResolved(open)
    }

    function enterFocus(enabled: bool): void {
        if (enabled === focusMode)
            return
        focusTransitioning = true
        if (enabled) {
            _leftBeforeFocus = leftContentOpen
            _inspectorBeforeFocus = inspectorOpen
            _arrangementBeforeFocus = arrangementOpen
            leftContentOpen = false
            inspectorOpen = false
            arrangementOpen = false
            focusMode = true
        } else {
            focusMode = false
            leftContentOpen = _leftBeforeFocus
            inspectorOpen = _inspectorBeforeFocus
            arrangementOpen = _arrangementBeforeFocus
            if (leftContentOpen && inspectorOpen && !panelsFit(true, true))
                leftContentOpen = false
        }
        leftContentResolved(leftContentOpen)
        inspectorResolved(inspectorOpen)
        arrangementResolved(arrangementOpen)
        focusResolved(focusMode)
        focusTransitioning = false
    }

    onAvailableWidthChanged: {
        if (!focusMode && leftContentOpen && inspectorOpen && !panelsFit(true, true)) {
            leftContentOpen = false
            leftContentResolved(false)
        }
    }
}
