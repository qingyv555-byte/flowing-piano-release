import QtQuick

// Passive rendering only. Frequency runs left to right, independently of score time.
Canvas {
    id: glass
    required property var feedback
    readonly property var values: feedback.values
    width: feedback.visualWidth
    height: parent.height * Number(values.glassHeight)
    x: feedback.visualLeft
    y: (parent.height - height) * Number(values.glassPositionY)
    enabled: false
    opacity: feedback.editing ? 0.65 : 1
    Behavior on opacity { NumberAnimation { duration: 120 } }
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onVisibleChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d")
        ctx.clearRect(0, 0, width, height)
        if (!visible || width < 1 || height < 1) return
        var paper = values.themeId === "paper"
        var hue = Number(values.glassHue)
        var sat = Number(values.glassSaturation) * 0.65
        // One flat ink keeps the spectrum legible without implying glass depth.
        var ink = Qt.hsla(hue, sat, paper ? 0.36 : 0.72, 1)
        var sounding = feedback.analysisRequested && feedback.displayRms > 0.00001
        var bands = sounding ? feedback.visualizer.bands : []
        var count = feedback.powerSave ? 48 : Math.min(128, Math.max(32, Math.floor(width / 10)))
        var floor = height - 3
        var step = width / count
        var barWidth = Math.max(1.25, Math.min(3.2, step * 0.30))
        ctx.fillStyle = ink
        for (var i = 0; i < count; i++) {
            var index = i / Math.max(1, count - 1) * Math.max(0, bands.length - 1)
            var low = Math.floor(index)
            var value = bands.length ? Math.max(0, bands[low]) * (1 - index + low)
                + Math.max(0, bands[Math.min(bands.length - 1, low + 1)]) * (index - low) : 0
            // Fixed mapping preserves silence and dynamics. Visual subdivision is not FFT resolution.
            var level = Math.min(1, value * feedback.masterGain * 3.5 * feedback.strength)
            var h = 1 + level * Math.max(0, height - 6)
            var left = i * step + (step - barWidth) / 2
            var top = floor - h
            ctx.globalAlpha = sounding ? 0.26 + level * 0.64 : 0.18
            ctx.fillRect(left, top, barWidth, h)
        }
        ctx.globalAlpha = sounding ? 0.18 : 0.10
        ctx.fillRect(0, floor, width, 1)
        ctx.globalAlpha = 1
    }
}
