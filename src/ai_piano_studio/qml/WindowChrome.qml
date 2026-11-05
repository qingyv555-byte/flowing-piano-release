import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "windowChrome"

    required property var theme
    required property var hostWindow
    property string productName: "AI Piano Studio"
    property string contextText: ""
    property string statusText: ""
    property color statusColor: theme.textMuted
    property bool showContextAction: false
    property string contextActionText: "改名"
    signal contextActionRequested(var opener)
    property bool integratedToolbar: false
    property alias toolbarHost: toolbarHost
    readonly property int titleBarHeight: integratedToolbar ? 56 : theme.windowChromeHeight
    readonly property int resizeHandleSize: 6
    readonly property bool maximized: hostWindow
        && hostWindow.visibility === Window.Maximized

    z: 100
    visible: true

    function toggleMaximize() {
        if (!root.hostWindow)
            return
        if (root.maximized)
            root.hostWindow.showNormal()
        else
            root.hostWindow.showMaximized()
    }

    function beginResize(edge) {
        if (root.hostWindow && root.hostWindow.startSystemResize)
            root.hostWindow.startSystemResize(edge)
    }

    Rectangle {
        id: titleStrip
        objectName: "windowChromeTitleStrip"
        x: 0
        y: 0
        width: parent.width
        height: root.titleBarHeight
        color: root.theme.chromeBackground
        border.color: root.theme.chromeBorder
        border.width: 1

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: root.theme.space3
            anchors.rightMargin: root.theme.space1
            spacing: root.theme.space2

            Rectangle {
                Layout.preferredWidth: root.theme.iconMedium
                Layout.preferredHeight: root.theme.iconMedium
                radius: root.theme.radiusM
                color: "#F5F3EC"

                Image {
                    anchors.fill: parent
                    anchors.margins: 0
                    source: "../resources/brand/flowing-piano.svg"
                    fillMode: Image.PreserveAspectFit
                }
            }

            Label {
                objectName: "windowChromeProductName"
                visible: !root.integratedToolbar
                text: root.productName
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeBody
                font.weight: root.theme.weightBold
                elide: Text.ElideRight
                Layout.maximumWidth: 220
            }

            Label {
                objectName: "windowChromeContextText"
                visible: !root.integratedToolbar && text.length > 0
                text: root.contextText
                color: root.theme.textSecondary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeSmall
                elide: Text.ElideRight
                Layout.maximumWidth: 260
            }

            AppButton {
                id: contextActionButton
                objectName: "windowChromeContextActionButton"
                visible: !root.integratedToolbar && root.showContextAction
                theme: root.theme
                text: root.contextActionText
                variant: "ghost"
                implicitWidth: 52
                implicitHeight: root.theme.iconButtonSize
                onClicked: root.contextActionRequested(contextActionButton)
            }

            Label {
                objectName: "windowChromeStatusText"
                visible: !root.integratedToolbar && text.length > 0
                text: root.statusText
                color: root.statusColor
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeCaption
            }

            Item {
                id: toolbarHost
                objectName: "windowChromeToolbarHost"
                visible: root.integratedToolbar
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            Item { visible: !root.integratedToolbar; Layout.fillWidth: true }

            AppButton {
                id: minimizeButton
                objectName: "windowChromeMinimizeButton"
                theme: root.theme
                text: "−"
                variant: "ghost"
                accessibleName: "最小化"
                implicitWidth: 40
                implicitHeight: root.theme.iconButtonSize
                onClicked: root.hostWindow.showMinimized()
            }

            AppButton {
                id: maximizeButton
                objectName: "windowChromeMaximizeButton"
                theme: root.theme
                text: root.maximized ? "❐" : "□"
                variant: "ghost"
                accessibleName: root.maximized ? "还原窗口" : "最大化"
                implicitWidth: 40
                implicitHeight: root.theme.iconButtonSize
                onClicked: root.toggleMaximize()
            }

            AppButton {
                id: closeButton
                objectName: "windowChromeCloseButton"
                theme: root.theme
                text: "×"
                variant: "ghost"
                semanticKind: "danger"
                accessibleName: "关闭"
                implicitWidth: 40
                implicitHeight: root.theme.iconButtonSize
                onClicked: root.hostWindow.close()
            }
        }

        MouseArea {
            id: titleDragArea
            objectName: "windowChromeTitleDragArea"
            z: -1
            anchors.fill: titleStrip
            acceptedButtons: Qt.LeftButton
            hoverEnabled: true
            onPressed: mouse => {
                if (mouse.button === Qt.LeftButton && root.hostWindow.startSystemMove)
                    root.hostWindow.startSystemMove()
            }
            onDoubleClicked: root.toggleMaximize()
        }
    }

    MouseArea {
        objectName: "windowChromeLeftResizeHandle"
        x: 0
        y: root.resizeHandleSize
        width: root.resizeHandleSize
        height: Math.max(0, root.height - root.resizeHandleSize * 2)
        cursorShape: Qt.SizeHorCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.LeftEdge)
    }
    MouseArea {
        objectName: "windowChromeRightResizeHandle"
        x: Math.max(0, root.width - root.resizeHandleSize)
        y: root.resizeHandleSize
        width: root.resizeHandleSize
        height: Math.max(0, root.height - root.resizeHandleSize * 2)
        cursorShape: Qt.SizeHorCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.RightEdge)
    }
    MouseArea {
        objectName: "windowChromeTopResizeHandle"
        x: root.resizeHandleSize
        y: 0
        width: Math.max(0, root.width - root.resizeHandleSize * 2)
        height: root.resizeHandleSize
        cursorShape: Qt.SizeVerCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.TopEdge)
    }
    MouseArea {
        objectName: "windowChromeBottomResizeHandle"
        x: root.resizeHandleSize
        y: Math.max(0, root.height - root.resizeHandleSize)
        width: Math.max(0, root.width - root.resizeHandleSize * 2)
        height: root.resizeHandleSize
        cursorShape: Qt.SizeVerCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.BottomEdge)
    }
    MouseArea {
        objectName: "windowChromeTopLeftResizeHandle"
        x: 0
        y: 0
        width: root.resizeHandleSize
        height: root.resizeHandleSize
        cursorShape: Qt.SizeFDiagCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.TopEdge | Qt.LeftEdge)
    }
    MouseArea {
        objectName: "windowChromeTopRightResizeHandle"
        x: Math.max(0, root.width - root.resizeHandleSize)
        y: 0
        width: root.resizeHandleSize
        height: root.resizeHandleSize
        cursorShape: Qt.SizeBDiagCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.TopEdge | Qt.RightEdge)
    }
    MouseArea {
        objectName: "windowChromeBottomLeftResizeHandle"
        x: 0
        y: Math.max(0, root.height - root.resizeHandleSize)
        width: root.resizeHandleSize
        height: root.resizeHandleSize
        cursorShape: Qt.SizeBDiagCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.BottomEdge | Qt.LeftEdge)
    }
    MouseArea {
        objectName: "windowChromeBottomRightResizeHandle"
        x: Math.max(0, root.width - root.resizeHandleSize)
        y: Math.max(0, root.height - root.resizeHandleSize)
        width: root.resizeHandleSize
        height: root.resizeHandleSize
        cursorShape: Qt.SizeFDiagCursor
        acceptedButtons: Qt.LeftButton
        onPressed: root.beginResize(Qt.BottomEdge | Qt.RightEdge)
    }
}
