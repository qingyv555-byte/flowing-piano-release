import QtQuick
import QtQuick.Controls

Item {
    id: host

    required property Theme theme
    property string kind: "info"
    property string message: ""
    property string actionLabel: ""
    property var actionCallback: null
    property string secondaryActionLabel: ""
    property var secondaryActionCallback: null
    property bool persistent: false
    property int dismissAfterMs: -1
    property bool dismissible: false
    property real progress: -1
    readonly property color semanticColor: kind === "success" ? theme.success
        : kind === "warning" ? theme.warning
        : kind === "danger" ? theme.danger : theme.info
    readonly property string statusText: kind === "success" ? "成功"
        : kind === "warning" ? "警告"
        : kind === "danger" ? "错误" : "提示"
    // Persistence controls lifetime, not layout. Actionable feedback should
    // remain visible without reserving or covering the editor's top edge.
    readonly property bool shownAsBanner: progress >= 0
    readonly property int autoDismissInterval: (kind === "info" || kind === "success")
        ? 2400 : 4000
    readonly property int effectiveDismissInterval: dismissAfterMs >= 0
        ? dismissAfterMs : autoDismissInterval
    readonly property real progressFraction: Math.max(0, Math.min(1, progress))

    implicitWidth: 400
    implicitHeight: visible ? feedbackSurface.height : 0
    visible: message.length > 0
    Accessible.role: Accessible.AlertMessage
    Accessible.name: statusText + "：" + message
    Accessible.description: actionLabel.length > 0 ? actionLabel : ""

    signal actionTriggered()

    onKindChanged: {
        if (["info", "success", "warning", "danger"].indexOf(kind) < 0)
            kind = "info"
    }

    function show(nextKind, nextMessage, options) {
        var settings = options || ({})
        kind = ["info", "success", "warning", "danger"].indexOf(nextKind) >= 0
            ? nextKind : "info"
        message = nextMessage || ""
        actionLabel = settings.actionLabel || ""
        actionCallback = settings.actionCallback || null
        secondaryActionLabel = settings.secondaryActionLabel || ""
        secondaryActionCallback = settings.secondaryActionCallback || null
        persistent = settings.persistent === true
        var configuredDismissAfterMs = settings.dismissAfterMs
        if (configuredDismissAfterMs === undefined) {
            dismissAfterMs = -1
        } else {
            var parsedDismissAfterMs = Number(configuredDismissAfterMs)
            dismissAfterMs = parsedDismissAfterMs >= 0
                    && parsedDismissAfterMs < 2147483647
                ? Math.floor(parsedDismissAfterMs) : -1
        }
        dismissible = settings.dismissible === true
        progress = settings.progress === undefined ? -1 : settings.progress
        if (persistent && dismissAfterMs < 0)
            dismissTimer.stop()
        else
            dismissTimer.restart()
    }

    function clear() {
        dismissTimer.stop()
        message = ""
        actionLabel = ""
        actionCallback = null
        secondaryActionLabel = ""
        secondaryActionCallback = null
        persistent = false
        dismissAfterMs = -1
        dismissible = false
        progress = -1
    }

    function dismiss() {
        clear()
    }

    function triggerAction() {
        if (actionCallback) {
            try {
                actionCallback()
            } catch (error) {
                console.warn("Feedback action failed: " + error)
            }
        }
        actionTriggered()
    }

    function triggerSecondaryAction() {
        if (secondaryActionCallback) {
            try {
                secondaryActionCallback()
            } catch (error) {
                console.warn("Feedback secondary action failed: " + error)
            }
        }
        actionTriggered()
    }

    Timer {
        id: dismissTimer
        interval: host.effectiveDismissInterval
        repeat: false
        onTriggered: host.clear()
    }

    Rectangle {
        id: feedbackSurface
        objectName: "feedbackSurface"
        visible: host.visible
        x: host.shownAsBanner ? host.theme.space4
            : Math.max(host.theme.space4, host.width - width - host.theme.space4)
        y: host.shownAsBanner ? host.theme.space4
            : Math.max(host.theme.space4, host.height - height
                       - host.theme.transportHeight - host.theme.space4)
        width: host.shownAsBanner ? Math.max(0, host.width - host.theme.space8)
            : Math.min(host.implicitWidth, Math.max(0, host.width - host.theme.space8))
        height: host.shownAsBanner && host.progress >= 0 ? 72 : 56
        radius: host.theme.radiusM
        color: host.theme.elevatedSurface
        border.width: 1
        border.color: host.semanticColor
        clip: true

        Rectangle {
            id: semanticIcon
            width: host.theme.iconMedium
            height: host.theme.iconMedium
            radius: width / 2
            anchors.left: parent.left
            anchors.leftMargin: host.theme.space3
            anchors.verticalCenter: parent.verticalCenter
            color: host.semanticColor

            Text {
                anchors.centerIn: parent
                text: host.kind === "success" ? "✓" : host.kind === "info" ? "i" : "!"
                color: host.kind === "danger" ? host.theme.textOnDanger : host.theme.textOnAccent
                font.family: host.theme.fontUi
                font.pixelSize: host.theme.typeBodyStrong
                font.weight: host.theme.weightBold
            }
        }

        Text {
            id: statusLabel
            anchors.left: semanticIcon.right
            anchors.leftMargin: host.theme.space2
            anchors.top: parent.top
            anchors.topMargin: host.theme.space2
            width: Math.max(0, parent.width - x - (actionButton.visible
                ? actionButton.width + host.theme.space3 : 0)
                - (secondaryActionButton.visible
                ? secondaryActionButton.width + host.theme.space3 : host.theme.space3)
                - (closeButton.visible
                ? closeButton.width + host.theme.space3 : 0))
            text: host.statusText
            color: host.semanticColor
            font.family: host.theme.fontUi
            font.pixelSize: host.theme.typeCaption
            font.weight: host.theme.weightSemibold
            elide: Text.ElideRight
        }

        Text {
            id: messageLabel
            anchors.left: statusLabel.left
            anchors.right: statusLabel.right
            anchors.top: statusLabel.bottom
            text: host.message
            color: host.theme.textPrimary
            font.family: host.theme.fontUi
            font.pixelSize: host.theme.typeSmall
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }

        Rectangle {
            id: secondaryActionButton
            visible: host.secondaryActionLabel.length > 0
            anchors.right: actionButton.visible ? actionButton.left : parent.right
            anchors.rightMargin: host.theme.space2
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(76, secondaryActionText.implicitWidth + host.theme.space5)
            height: host.theme.controlHeight
            radius: host.theme.radiusS
            color: secondaryActionHover.hovered ? host.theme.surfaceHover : host.theme.surface
            border.width: 1
            border.color: host.semanticColor
            activeFocusOnTab: true
            Accessible.name: host.secondaryActionLabel
            Accessible.role: Accessible.Button

            function activate() {
                if (visible && enabled)
                    host.triggerSecondaryAction()
            }

            Accessible.onPressAction: activate()

            Keys.onPressed: event => {
                if (!event.isAutoRepeat && (event.key === Qt.Key_Return
                        || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)) {
                    event.accepted = true
                    activate()
                }
            }

            Text {
                id: secondaryActionText
                anchors.fill: parent
                text: host.secondaryActionLabel
                color: host.theme.textPrimary
                font.family: host.theme.fontUi
                font.pixelSize: host.theme.typeSmall
                font.weight: host.theme.weightSemibold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            HoverHandler {
                id: secondaryActionHover
                enabled: secondaryActionButton.visible
            }

            TapHandler {
                enabled: secondaryActionButton.visible && secondaryActionButton.enabled
                onTapped: secondaryActionButton.activate()
            }
        }

        Rectangle {
            id: actionButton
            visible: host.actionLabel.length > 0
            anchors.right: closeButton.visible ? closeButton.left : parent.right
            anchors.rightMargin: host.theme.space2
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(68, actionText.implicitWidth + host.theme.space5)
            height: host.theme.controlHeight
            radius: host.theme.radiusS
            color: actionHover.hovered ? host.theme.surfaceHover : host.theme.surface
            border.width: 1
            border.color: host.semanticColor
            activeFocusOnTab: true
            Accessible.name: host.actionLabel
            Accessible.role: Accessible.Button

            function activate() {
                if (visible && enabled)
                    host.triggerAction()
            }

            Accessible.onPressAction: activate()

            Keys.onPressed: event => {
                if (!event.isAutoRepeat && (event.key === Qt.Key_Return
                        || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)) {
                    event.accepted = true
                    activate()
                }
            }

            Text {
                id: actionText
                anchors.fill: parent
                text: host.actionLabel
                color: host.theme.textPrimary
                font.family: host.theme.fontUi
                font.pixelSize: host.theme.typeSmall
                font.weight: host.theme.weightSemibold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            HoverHandler {
                id: actionHover
                enabled: actionButton.visible
            }

            TapHandler {
                enabled: actionButton.visible && actionButton.enabled
                onTapped: actionButton.activate()
            }
        }

        Rectangle {
            id: closeButton
            objectName: "feedbackCloseButton"
            visible: host.dismissible
            enabled: host.dismissible
            anchors.right: parent.right
            anchors.rightMargin: host.theme.space2
            anchors.verticalCenter: parent.verticalCenter
            width: host.theme.controlHeight
            height: host.theme.controlHeight
            radius: host.theme.radiusS
            color: closeHover.hovered ? host.theme.surfaceHover : "transparent"
            border.width: 1
            border.color: host.theme.borderSubtle
            activeFocusOnTab: host.dismissible
            Accessible.name: "关闭"
            Accessible.role: Accessible.Button

            function activate() {
                if (visible && enabled)
                    host.dismiss()
            }

            Accessible.onPressAction: activate()

            Keys.onPressed: event => {
                if (!event.isAutoRepeat && (event.key === Qt.Key_Return
                        || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)) {
                    event.accepted = true
                    activate()
                }
            }

            Text {
                anchors.fill: parent
                text: "×"
                color: host.theme.textSecondary
                font.family: host.theme.fontUi
                font.pixelSize: host.theme.typeBodyStrong
                font.weight: host.theme.weightSemibold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            HoverHandler {
                id: closeHover
                enabled: closeButton.visible
            }

            TapHandler {
                enabled: closeButton.visible && closeButton.enabled
                onTapped: closeButton.activate()
            }
        }

        Rectangle {
            visible: host.progress >= 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: host.theme.space1
            color: host.theme.borderSubtle

            Rectangle {
                width: parent.width * host.progressFraction
                height: parent.height
                color: host.semanticColor
            }
        }
    }
}
