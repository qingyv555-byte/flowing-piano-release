import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects

Control {
    id: control

    required property Theme theme
    property string text: ""
    property url iconSource
    property string variant: "secondary"
    property string semanticKind: ""
    property bool showSemanticIndicator: true
    property string size: "default"
    property int labelPixelSize: theme.typeBody
    property int labelWeight: theme.weightSemibold
    property bool loading: false
    property bool checkable: false
    property bool checked: false
    property bool checkedExternallyControlled: false
    property string accessibleName: text
    readonly property bool interactive: enabled && !loading
    readonly property bool pressed: tap.pressed
    readonly property color semanticColor: semanticKind === "record" ? theme.record
        : semanticKind === "success" ? theme.success
        : semanticKind === "warning" ? theme.warning
        : semanticKind === "danger" ? theme.danger
        : semanticKind === "info" ? theme.info : theme.textPrimary
    readonly property color iconColor: variant === "primary" ? theme.textOnAccent
        : variant === "danger" ? theme.textOnDanger
        : semanticKind.length > 0 ? semanticColor
        : checkable && checked ? theme.accentSelection : theme.textPrimary
    readonly property bool hasIcon: iconSource.toString().length > 0
    readonly property real normalContentWidth: (hasIcon ? theme.iconMedium : 0)
        + (hasIcon && labelText.implicitWidth > 0 ? theme.space2 : 0)
        + labelText.implicitWidth
    readonly property real loadingContentWidth: theme.iconSmall
        + (labelText.implicitWidth > 0 ? theme.space2 : 0)
        + labelText.implicitWidth

    signal clicked()
    signal toggled(bool checked)

    activeFocusOnTab: true
    hoverEnabled: interactive
    opacity: enabled ? 1 : 0.42
    implicitHeight: size === "large" ? theme.primaryControlHeight : theme.controlHeight
    implicitWidth: Math.max(implicitHeight,
                            Math.max(normalContentWidth, loadingContentWidth) + theme.space6)
    Accessible.role: Accessible.Button
    Accessible.name: accessibleName
    Accessible.checkable: checkable
    Accessible.checked: checked
    Accessible.onPressAction: activate()

    function activate() {
        if (!interactive)
            return
        if (checkable) {
            var nextChecked = !checked
            if (!checkedExternallyControlled)
                checked = nextChecked
            toggled(nextChecked)
        }
        clicked()
    }

    Keys.onPressed: event => {
        if (!event.isAutoRepeat && (event.key === Qt.Key_Return
                || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)) {
            event.accepted = true
            control.activate()
        }
    }

    HoverHandler {
        id: hover
        objectName: "buttonHoverHandler"
        enabled: control.interactive
        cursorShape: Qt.PointingHandCursor
    }

    TapHandler {
        id: tap
        enabled: control.interactive
        onTapped: control.activate()
    }

    background: Item {
        implicitWidth: control.implicitWidth
        implicitHeight: control.implicitHeight
        scale: control.pressed ? 0.98 : 1

        Behavior on scale {
            NumberAnimation { duration: control.theme.motionPress }
        }

        Rectangle {
            objectName: "buttonBackground"
            anchors.fill: parent
            radius: control.theme.radiusM
            color: control.variant === "primary"
                ? (control.pressed ? control.theme.accentPrimaryPressed
                    : control.hovered ? control.theme.accentPrimaryHover
                    : control.theme.accentPrimary)
                : control.variant === "danger" ? control.theme.danger
                : control.variant === "ghost"
                    ? (control.pressed ? control.theme.surfacePressed
                        : control.hovered ? control.theme.surfaceHover
                        : "transparent")
                : control.checkable && control.checked ? control.theme.surfaceSelected
                : control.hovered ? control.theme.surfaceHover : control.theme.surface
            border.width: control.variant === "secondary" || (control.checkable && control.checked) ? 1 : 0
            border.color: control.checkable && control.checked
                ? control.theme.accentPrimary : control.theme.borderStrong
        }

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: control.theme.radiusM - 1
            color: "transparent"
            border.width: control.activeFocus ? 2 : 0
            border.color: control.theme.focusRing
        }
    }

    contentItem: Item {
        implicitWidth: contentRow.implicitWidth
        implicitHeight: contentRow.implicitHeight

        Row {
            id: contentRow
            anchors.centerIn: parent
            spacing: control.theme.space2

            Item {
                width: control.theme.iconMedium
                height: control.theme.iconMedium
                visible: control.hasIcon && !control.loading

                Button {
                    anchors.fill: parent
                    visible: control.theme.studio
                    enabled: false
                    padding: 0
                    display: AbstractButton.IconOnly
                    icon.source: control.iconSource
                    icon.color: control.iconColor
                    icon.width: control.theme.iconMedium
                    icon.height: control.theme.iconMedium
                    background: null
                }
                Image {
                    id: iconImage
                    anchors.fill: parent
                    source: control.iconSource
                    fillMode: Image.PreserveAspectFit
                    visible: parent.visible && !control.theme.studio
                }

                ColorOverlay {
                    objectName: "iconTint"
                    anchors.fill: iconImage
                    source: iconImage
                    color: control.iconColor
                    visible: iconImage.visible
                }
            }

            Item {
                width: control.theme.iconSmall
                height: control.theme.iconSmall
                visible: control.loading

                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    color: "transparent"
                    border.width: 2
                    border.color: control.semanticKind.length > 0
                        ? control.semanticColor : control.theme.textPrimary
                    opacity: 0.85
                }

                Rectangle {
                    x: width / 2 - 1
                    y: 0
                    width: 2
                    height: height / 2
                    color: control.semanticKind.length > 0
                        ? control.semanticColor : control.theme.textPrimary
                    transformOrigin: Item.Bottom
                    RotationAnimator on rotation {
                        running: control.loading
                        from: 0
                        to: 360
                        duration: 800
                        loops: Animation.Infinite
                    }
                }
            }

            Text {
                id: labelText
                text: control.text
                visible: text.length > 0
                color: control.variant === "primary" ? control.theme.textOnAccent
                    : control.variant === "danger" ? control.theme.textOnDanger
                    : control.checkable && control.checked ? control.theme.accentSelection
                    : control.theme.textPrimary
                font.family: control.theme.fontUi
                font.pixelSize: control.labelPixelSize
                font.weight: control.labelWeight
                verticalAlignment: Text.AlignVCenter
            }
        }

        Rectangle {
            objectName: "semanticIndicator"
            visible: control.showSemanticIndicator && control.semanticKind.length > 0
            width: 6
            height: 6
            radius: 3
            color: control.semanticColor
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.rightMargin: control.theme.space2
            anchors.topMargin: control.theme.space2
        }
    }
}
