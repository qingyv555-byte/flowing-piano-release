import QtQuick
import QtQuick.Window

// Visual time only: never feeds scheduling, project state, or export.
Item {
    id: clock
    objectName: "visualPlaybackClock"
    property bool active: false
    property real sourceSeconds: 0
    property real rate: 1
    property real totalSeconds: 0
    readonly property real maxPredictionSeconds: 0.05
    property real positionSeconds: 0
    property real elapsed: 0
    property real sampleTime: 0
    property real samplePosition: 0
    property bool discontinuity: false
    property real lastFrameMs: 0

    function bounded(value) {
        return Math.max(0, totalSeconds > 0 ? Math.min(totalSeconds, value) : value)
    }
    function synchronize() {
        samplePosition = sourceSeconds
        sampleTime = elapsed
        positionSeconds = bounded(sourceSeconds)
    }
    function expectDiscontinuity() { discontinuity = true }
    onActiveChanged: { elapsed = 0; lastFrameMs = Date.now(); synchronize() }
    onRateChanged: synchronize()
    onSourceSecondsChanged: {
        if (!active || discontinuity || sourceSeconds < samplePosition - 0.0001
                || Math.abs(sourceSeconds - samplePosition) > 0.2 * rate) {
            synchronize()
        } else {
            samplePosition = sourceSeconds
            sampleTime = elapsed
        }
    }
    function advanceFrame(frameTime) {
        clock.elapsed += frameTime
        if (clock.discontinuity || frameTime > 0.12) {
            clock.discontinuity = false
            clock.synchronize()
            return
        }
        var age = Math.min(clock.maxPredictionSeconds,
            Math.max(0, clock.elapsed - clock.sampleTime))
        var reference = clock.samplePosition + age * clock.rate
        var step = Math.max(0, frameTime) * clock.rate
        var candidate = clock.positionSeconds + step
        // Correct a small clock-phase error gradually instead of moving
        // backwards whenever a coarse/late audio position arrives.
        var correction = Math.max(-step * 0.1,
            Math.min(step * 0.1, reference - candidate))
        var ceiling = clock.samplePosition + clock.maxPredictionSeconds * clock.rate
        clock.positionSeconds = clock.bounded(Math.max(clock.positionSeconds,
            Math.min(candidate + correction, ceiling)))
    }
    FrameAnimation { running: clock.active }
    Connections {
        // Update just before scene synchronization, including render passes
        // between animation ticks. One visual time drives line and viewport.
        target: clock.active ? clock.Window.window : null
        function onAfterAnimating() {
            var now = Date.now()
            if (clock.active && clock.lastFrameMs > 0)
                clock.advanceFrame(Math.max(0, (now - clock.lastFrameMs) / 1000))
            clock.lastFrameMs = now
        }
    }
    Component.onCompleted: synchronize()
}
