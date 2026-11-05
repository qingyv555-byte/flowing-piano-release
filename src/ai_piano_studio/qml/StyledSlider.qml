import QtQuick

Item {
    id: control
    required property var theme
    property real from: 0
    property real to: 1
    property real modelValue: 0
    property real stepSize: 0
    property string accessibleName: "滑条"
    // Let window-level editor shortcuts yield to this control's arrow-key
    // editing instead of moving selected notes underneath it.
    readonly property bool consumesArrowKeys: true
    readonly property real value: localValue
    readonly property real progress: to === from ? 0 : Math.max(0, Math.min(1, (localValue - from) / (to - from)))
    property real localValue: clamp(modelValue)
    signal valueEdited(real value)
    signal valueCommitted(real value)

    implicitWidth: 116
    implicitHeight: theme.controlHeight
    activeFocusOnTab: true
    opacity: enabled ? 1 : 0.45
    Accessible.role: Accessible.Slider
    Accessible.name: accessibleName

    function clamp(raw) {
        var bounded = Math.max(from, Math.min(to, Number(raw)))
        if (stepSize > 0)
            bounded = from + Math.round((bounded - from) / stepSize) * stepSize
        return Math.max(from, Math.min(to, bounded))
    }

    function previewValue(raw) {
        localValue = clamp(raw)
        valueEdited(localValue)
    }

    function valueAt(x) {
        var usableWidth = Math.max(1, track.width - handle.width)
        return from + Math.max(0, Math.min(1,
            (x - track.x - handle.width / 2) / usableWidth)) * (to - from)
    }

    onModelValueChanged: {
        if (!pointer.pressed)
            localValue = clamp(modelValue)
    }

    Keys.onLeftPressed: {
        if (!enabled)
            return
        previewValue(localValue - (stepSize > 0 ? stepSize : (to - from) / 20))
        valueCommitted(localValue)
    }
    Keys.onRightPressed: {
        if (!enabled)
            return
        previewValue(localValue + (stepSize > 0 ? stepSize : (to - from) / 20))
        valueCommitted(localValue)
    }

    Rectangle {
        id: track
        x: 7
        y: Math.round((parent.height - height) / 2)
        width: parent.width - 14
        height: 6
        radius: 3
        color: control.theme.surfaceHover
        border.color: control.theme.borderStrong
        border.width: 1

        Rectangle {
            width: Math.max(3, parent.width * control.progress)
            height: parent.height
            radius: parent.radius
            color: control.theme.accentPrimary
        }
    }

    Rectangle {
        id: handle
        objectName: "sliderHandle"
        width: 18
        height: 18
        radius: 9
        x: track.x + control.progress * (track.width - width)
        y: Math.round((parent.height - height) / 2)
        color: pointer.pressed ? control.theme.textPrimary : control.theme.elevatedSurface
        border.color: control.theme.accentPrimary
        border.width: 2
        Behavior on x { NumberAnimation { duration: pointer.pressed ? 0 : 100 } }
    }

    MouseArea {
        id: pointer
        anchors.fill: parent
        enabled: control.enabled
        preventStealing: true
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        property real grabOffset: 0
        onPressed: mouse => {
            control.forceActiveFocus()
            var onHandle = mouse.x >= handle.x && mouse.x <= handle.x + handle.width
                && mouse.y >= handle.y && mouse.y <= handle.y + handle.height
            grabOffset = onHandle ? mouse.x - (handle.x + handle.width / 2) : 0
            control.previewValue(control.valueAt(mouse.x - grabOffset))
        }
        onPositionChanged: mouse => {
            if (pressed)
                control.previewValue(control.valueAt(mouse.x - grabOffset))
        }
        onReleased: mouse => {
            control.previewValue(control.valueAt(mouse.x - grabOffset))
            control.valueCommitted(control.value)
        }
        onCanceled: control.localValue = control.clamp(control.modelValue)
    }

    Rectangle {
        id: focusRing
        objectName: "sliderFocusRing"
        anchors.fill: parent
        anchors.margins: 2
        color: "transparent"
        border.width: control.activeFocus ? 2 : 0
        border.color: control.theme.focusRing
        radius: control.theme.radiusM
    }
}
