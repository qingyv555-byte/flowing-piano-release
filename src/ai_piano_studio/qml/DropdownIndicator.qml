import QtQuick

Canvas {
    id: indicator
    required property Theme theme
    implicitWidth: 20
    implicitHeight: 20
    anchors.right: parent.right
    anchors.rightMargin: 10
    anchors.verticalCenter: parent.verticalCenter
    property color ink: enabled ? theme.textPrimary : theme.textDisabled
    onInkChanged: requestPaint()
    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        ctx.strokeStyle = ink
        ctx.lineWidth = 2
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
        ctx.beginPath()
        ctx.moveTo(5, 8)
        ctx.lineTo(10, 13)
        ctx.lineTo(15, 8)
        ctx.stroke()
    }
}
