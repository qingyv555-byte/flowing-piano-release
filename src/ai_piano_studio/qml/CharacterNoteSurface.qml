import QtQuick
import "CharacterNote.js" as CharacterNote

Canvas {
    id: surface
    objectName: "characterNoteSurface"
    property string character: "none"
    property real startTime: 0
    property real duration: 1
    property real velocity: 96
    property real position: 0
    property bool motionActive: false
    property var noteRenderer: null
    property color fillColor: "#53a79b"
    property string styleKey: "flat"
    onFillColorChanged: requestPaint()
    onStyleKeyChanged: requestPaint()
    // Clamped elapsed time avoids repainting every idle/future note on each
    // transport update. There is no per-note timer or free-running animation.
    readonly property real elapsed: motionActive && visible
        ? Math.max(-1, Math.min(duration + .4, position - startTime)) : -1
    enabled: false
    onElapsedChanged: requestPaint()
    onCharacterChanged: requestPaint()
    onDurationChanged: requestPaint()
    onVelocityChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onVisibleChanged: if (visible) requestPaint()
    onPaint: {
        var c = getContext("2d")
        c.clearRect(0, 0, width, height)
        if (noteRenderer) {
            c.fillStyle = fillColor
            noteRenderer.paintNoteBody(c, 0, 0, width, height)
        } else {
            CharacterNote.paint(c, 0, 0, width, height, character, elapsed, duration, velocity)
        }
    }
}
