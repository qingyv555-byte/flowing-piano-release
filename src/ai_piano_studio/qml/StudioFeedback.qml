import QtQuick

// A passive sibling below editable notes, never a second transport or FFT bus.
Item {
    id: root
    objectName: "studioFeedback"
    required property var theme
    required property var settings
    required property var visualizer
    required property var playbackController
    required property var timeline
    property var feedbackController: null
    property var overview: null
    property bool active: true
    property bool windowActive: true
    property bool powerSave: false
    // Settings thumbnails reuse the renderer without an idle animation loop.
    property bool previewFrozen: false
    readonly property var values: settings ? settings.values : ({})
    readonly property bool reducedMotion: values.reducedMotion === true
    readonly property bool focusMode: timeline ? timeline.focusMode : false
    readonly property bool rich: values.feedbackLevel === "rich"
    readonly property string style: values.feedbackStyle || "glass"
    readonly property real strength: values.feedbackStrength === "gentle" ? 0.6
        : values.feedbackStrength === "vivid" ? 1.35 : 1.0
    readonly property bool editing: timeline && (timeline.gestureFeedbackVisible === true
        || timeline.creatingNote === true || timeline.compositeDragPreviewActive === true)
    readonly property bool effectsEnabled: values.feedbackLevel !== "off"
    readonly property bool playing: playbackController !== null
        && (playbackController.playing || (playbackController.selectionAudition
            && playbackController.selectionAudition.playing)
            || (feedbackController && feedbackController.liveActive === true))
    readonly property bool viewActive: active && visible && windowActive && effectsEnabled
    readonly property bool noteEnabled: viewActive && values.noteFeedback !== false
    readonly property bool keyEnabled: viewActive && values.keyFeedback !== false
    readonly property bool spectrumEnabled: viewActive && values.spectrum !== false
        && ((!powerSave && !focusMode) || style === "glass")
    readonly property bool atmosphereEnabled: viewActive && values.atmosphere !== false
        && !powerSave && !focusMode && !reducedMotion
    // Parent aggregates this demand with any other legitimate visualizer views.
    readonly property bool analysisRequested: playing && masterGain > 0
        && (spectrumEnabled || atmosphereEnabled)
    readonly property bool eventsRequested: playing && (noteEnabled || keyEnabled)
    readonly property bool animationRunning: viewActive && playing && !previewFrozen
        && (eventsRequested || analysisRequested)
    readonly property int refreshFps: powerSave ? 15
        : (values.motionFps === 15 ? 15 : values.motionFps === 60 ? 60 : 30)
    readonly property real masterGain: playbackController
        ? Math.max(0, Math.min(1, playbackController.volume)) : 0
    // PCM is post voice gain/limiter, pre QAudioSink volume. This output-scaled
    // display is indicative (FFT bands are compressed), not a calibrated meter.
    readonly property real displayRms: analysisRequested && visualizer && visualizer.enabled
        ? Math.max(0, visualizer.rms) * masterGain : 0
    readonly property int bandCount: visualizer && visualizer.bands
        ? Math.min(32, visualizer.bands.length) : 0
    readonly property color playbackColor: theme ? theme.playheadLine : "#F1C778"
    readonly property color accentColor: theme ? theme.accentPrimary : "#D9E6DF"
    readonly property color coreColor: theme ? theme.feedbackCore : playbackColor
    readonly property var frameVoices: feedbackController ? feedbackController.voices : []
    readonly property real keyWidth: timeline ? timeline.keyWidth : 72
    readonly property real rowHeight: timeline ? timeline.rowHeight : 18
    readonly property real musicCanvasWidth: Math.max(0, width - keyWidth)
    readonly property real visualWidth: musicCanvasWidth * Number(values.glassWidth)
    readonly property real visualLeft: keyWidth
        + (musicCanvasWidth - visualWidth) * Number(values.glassPositionX)
    clip: true
    enabled: false
    z: 8

    function syncSubscription() {
        if (feedbackController)
            feedbackController.setEnabled(eventsRequested)
        if (!animationRunning) {
            pcmLayer.requestPaint()
            glassLayer.requestPaint()
        }
    }
    function keyStrength(pitch) {
        if (!keyEnabled || !playing)
            return 0
        var strength = 0
        for (var i = 0; i < frameVoices.length; i++) {
            var voice = frameVoices[i]
            if (visibleVoice(voice) && voice.pitch === pitch && voice.phase === "held")
                strength = Math.max(strength, reducedMotion ? 0.6 : 0.6 + 0.4 * voice.attack)
        }
        return strength
    }
    function rowStrength(pitch) {
        if (!noteEnabled || !playing || masterGain <= 0)
            return 0
        var level = 0
        for (var i = 0; i < frameVoices.length; ++i) {
            var voice = frameVoices[i]
            if (visibleVoice(voice) && voice.pitch === pitch && voice.phase === "held")
                level = Math.max(level, (0.65 + 0.35 * Math.max(0, voice.attack))
                    * Math.sqrt(Math.max(0, voice.velocity)))
        }
        return level
    }
    function repaint() {
        if (feedbackController && eventsRequested)
            feedbackController.sample()
        pcmLayer.requestPaint()
        glassLayer.requestPaint()
    }
    function weaveLevel(rawBand, strongestBand) {
        var absoluteLevel = Math.pow(Math.max(0, rawBand) * masterGain, 1.15)
            * 2.2 * strength
        var relativeLevel = strongestBand > 0.00001
            ? Math.pow(Math.max(0, rawBand) / strongestBand, 0.8) * Math.min(1,
                Math.sqrt(displayRms) * 3.2) * strength : 0
        // Absolute energy keeps phrase dynamics; peak-relative energy exposes
        // the real spectral contour instead of flattening all audible bands.
        return Math.min(1, absoluteLevel * 0.65 + relativeLevel * 0.75)
    }
    onEventsRequestedChanged: syncSubscription()
    onFeedbackControllerChanged: syncSubscription()
    onAnimationRunningChanged: syncSubscription()
    onAnalysisRequestedChanged: { pcmLayer.requestPaint(); glassLayer.requestPaint() }
    onValuesChanged: repaint()
    onWidthChanged: repaint()
    onHeightChanged: repaint()
    Component.onCompleted: syncSubscription()
    Component.onDestruction: {
        if (feedbackController)
            feedbackController.setEnabled(false)
    }

    Timer {
        interval: Math.ceil(1000 / root.refreshFps)
        running: root.animationRunning
        repeat: true
        triggeredOnStart: true
        onTriggered: root.repaint()
    }

    // Only the peripheral soundform uses a texture. It never uploads a full
    // piano-roll image, and makes room for the independently movable overview.
    Canvas {
        id: pcmLayer
        visible: root.style !== "glass"
        objectName: "studioPcmFeedback"
        x: root.visualLeft
        y: parent.height - height - 14
        width: root.visualWidth
        height: Math.min(88, parent.height)
        enabled: false
        opacity: root.editing ? 0.25 : 1
        onWidthChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            if (root.style === "glass" || !root.analysisRequested || root.displayRms <= 0.00001 || width < 40)
                return
            // Fixed perceptual compression reveals quiet piano detail without
            // normalizing each phrase. Zero remains zero; dynamics remain ordered.
            var energy = Math.min(1, Math.sqrt(root.displayRms) * 3.2)
            var bands = root.visualizer.bands
            var count = root.bandCount
            var amp = Math.min(1, energy * root.strength)
            var floor = height - 9
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            if (root.style === "legacy") {
                if (root.atmosphereEnabled) {
                    var legacyWash = ctx.createLinearGradient(0, 0, 0, height)
                    legacyWash.addColorStop(0, "transparent")
                    legacyWash.addColorStop(1, root.accentColor)
                    ctx.fillStyle = legacyWash
                    ctx.globalAlpha = Math.min(1, root.displayRms * 4) * (root.rich ? 0.18 : 0.1)
                    ctx.fillRect(0, 0, width, height)
                }
                if (root.spectrumEnabled) {
                    var spacing = Math.min(12, width / Math.max(1, count))
                    var origin = (width - spacing * count) / 2
                    ctx.fillStyle = root.accentColor
                    ctx.globalAlpha = root.rich ? 0.66 : 0.48
                    for (var b = 0; b < count; b++) {
                        var bar = Math.max(0, bands[b]) * root.masterGain * 40
                        ctx.fillRect(origin + b * spacing, floor - bar, 2, bar)
                    }
                }
            } else if (root.style === "string") {
                // Two restrained resonance contours. Geometry is an artistic
                // envelope of RMS and frequency energy, NOT sampled waveform.
                var strands = root.atmosphereEnabled ? 2 : 1
                if (root.spectrumEnabled || root.atmosphereEnabled) {
                    for (var strand = 0; strand < strands; strand++) {
                        ctx.beginPath()
                        for (var j = 0; j <= 48; j++) {
                            var t = j / 48
                            var bi = Math.min(count - 1, Math.floor(t * count))
                            var band = bi >= 0 ? Math.max(0, bands[bi]) * root.masterGain : 0
                            var envelope = Math.sin(Math.PI * t)
                            var yy = floor - 11 - strand * 9
                                - envelope * (9 + 27 * amp + 15 * band) * (strand ? 0.58 : 1)
                            if (j === 0) ctx.moveTo(t * width, yy)
                            else ctx.lineTo(t * width, yy)
                        }
                        ctx.strokeStyle = strand ? root.coreColor : root.accentColor
                        ctx.lineWidth = strand ? 1 : 2
                        ctx.globalAlpha = Math.min(0.8, amp * (strand ? 0.5 : 0.95))
                        ctx.stroke()
                    }
                }
                if (root.atmosphereEnabled) {
                    var wash = ctx.createLinearGradient(0, height - 65, 0, height)
                    wash.addColorStop(0, "transparent")
                    wash.addColorStop(1, root.accentColor)
                    ctx.fillStyle = wash
                    ctx.globalAlpha = amp * 0.12
                    ctx.fillRect(0, height - 65, width, 65)
                }
            } else {
                // Frequency is abstracted into a fixed woven selvedge, not
                // aligned with project time, pitch rows, or instrument labels.
                var step = width / Math.max(1, count)
                var strongestBand = 0
                for (var peakIndex = 0; peakIndex < count; peakIndex++)
                    strongestBand = Math.max(strongestBand, Math.max(0, bands[peakIndex]))
                if (root.atmosphereEnabled) {
                    // Whole-signal energy is one calm selvedge, rather than a
                    // repeated row of equal-height marks across every band.
                    ctx.fillStyle = root.accentColor
                    ctx.globalAlpha = 0.07 + amp * 0.13
                    ctx.fillRect(0, floor, width, 1)
                }
                for (var k = 0; k < count; k++) {
                    // The analyser already applies logarithmic compression. A second
                    // square root flattened quiet and loud bands into nearly equal
                    // marks, so keep a contrast curve here for visible dynamics.
                    var rawBand = Math.max(0, bands[k])
                    var value = root.weaveLevel(rawBand, strongestBand)
                    var rows = root.atmosphereEnabled ? 3 : 1
                    for (var r = 0; r < rows; r++) {
                        if (!root.spectrumEnabled && !root.atmosphereEnabled) continue
                        // Frequency detail stays local; each higher thread has a
                        // stricter energy threshold so chord and accent peaks grow
                        // taller while quiet notes remain close to the selvedge.
                        var threshold = r * 0.26
                        var localAmount = Math.max(0, (value - threshold) / (1 - threshold))
                        var amount = localAmount
                        if (amount < 0.012) continue
                        var xx = k * step + step * 0.2
                        var yy2 = floor - r * (4 + value * 10) - (k % 2) * 2
                        ctx.strokeStyle = r === 1 ? root.coreColor : root.accentColor
                        ctx.globalAlpha = Math.min(0.82, 0.16 + amount * 0.66)
                        ctx.lineWidth = r === 0 ? 2 : 1
                        ctx.beginPath()
                        var slant = root.reducedMotion ? 4 : 2 + Math.pow(amount, 0.9) * (10 + r * 7)
                        ctx.moveTo(xx, yy2)
                        ctx.lineTo(xx + step * (0.2 + amount * 0.72), yy2 - slant)
                        ctx.stroke()
                    }
                }
            }
            ctx.globalAlpha = 1
        }
    }

    GlassSoundscape {
        id: glassLayer
        objectName: "studioGlassSoundscape"
        feedback: root
        visible: root.style === "glass" && root.viewActive && root.values.spectrum !== false
    }

    // Stable slots per visible pitch: same-pitch voices share one subtle wash.
    // The existing voice snapshots provide timing; no new timer or audio bus.
    Repeater {
        // Layouts may briefly assign a negative height before their first polish.
        model: Math.max(0, Math.min(128, Math.ceil(root.height / Math.max(1, root.rowHeight))))
        Rectangle {
            id: pitchRow
            required property int index
            readonly property int pitch: root.timeline ? root.timeline.highestPitch - index : -1
            readonly property real targetOpacity: root.rowStrength(pitch) * root.strength * 0.10
            objectName: "studioPitchRow-" + pitch
            x: root.keyWidth
            y: index * root.rowHeight
            width: root.musicCanvasWidth
            height: root.rowHeight
            color: root.theme ? root.theme.feedbackPitch : root.accentColor
            visible: root.noteEnabled && pitch >= 0 && pitch <= 127
            opacity: targetOpacity
            Behavior on opacity {
                enabled: !root.reducedMotion && root.viewActive
                NumberAnimation { duration: pitchRow.targetOpacity > 0 ? 35 : 160; easing.type: Easing.OutQuad }
            }
        }
    }

    function visibleVoice(voice) {
        return voice !== null && voice !== undefined && voice.layerVisible !== false
            && (voice.positioned === false || !timeline || typeof timeline.layerVisibleInCurrentMode !== "function"
                || timeline.layerVisibleInCurrentMode(voice.layerId))
    }
    function voiceColor(voice) {
        if (voice && timeline && typeof timeline.notePresentationColor === "function")
            return timeline.notePresentationColor(voice.pitch, voice.layerRole || "other",
                false, voice.layerCustomColor || "")
        return accentColor
    }

    // Fixed scene-graph slots: no delegate recreation on every voice snapshot,
    // no per-note timers, shaders, full-canvas rasterization or hit regions.
    Item {
        id: eventLayer
        objectName: "studioNoteFeedback"
        anchors.fill: parent
        enabled: false
        Repeater {
            model: 24
            Item {
                id: mark
                required property int index
                readonly property var voice: index < root.frameVoices.length ? root.frameVoices[index] : null
                readonly property bool held: voice !== null && voice.phase === "held"
                readonly property real attack: !root.reducedMotion && voice
                    ? Math.max(0, voice.attack) : 0
                readonly property real onset: root.masterGain > 0 ? Math.min(1, attack * root.strength) : 0
                readonly property color ink: root.voiceColor(voice)
                readonly property real rawX: voice && root.timeline
                    ? root.keyWidth + (voice.startTime - root.timeline.viewStartSeconds)
                        * root.timeline.pixelsPerSecond : 0
                readonly property real noteWidth: voice && root.timeline
                    ? Math.max(18, voice.duration * root.timeline.pixelsPerSecond) : 0
                readonly property real noteLeft: Math.max(root.keyWidth + 5, rawX)
                readonly property real noteRight: Math.min(root.width, rawX + noteWidth)
                readonly property real progressEnd: voice ? Math.min(noteRight,
                    rawX + noteWidth * voice.progress) : 0
                readonly property bool positioned: voice && voice.positioned !== false
                visible: root.eventsRequested && root.visibleVoice(voice)
                    && y >= 0 && y < root.height
                y: voice && root.timeline ? (root.timeline.highestPitch - voice.pitch) * root.rowHeight : 0
                width: root.width
                height: root.rowHeight
                opacity: voice && voice.positioned !== false && root.timeline && typeof root.timeline.layerOpacityInCurrentMode === "function"
                    ? root.timeline.layerOpacityInCurrentMode(voice.layerId) : 1
                Rectangle {
                    x: root.keyWidth + 1; y: 3
                    width: 3; height: Math.max(2, root.rowHeight - 6)
                    color: mark.ink
                    visible: root.keyEnabled && mark.held
                    opacity: root.reducedMotion ? 0.65 : Math.min(1, 0.45 + mark.onset * 0.55)
                }
                Item {
                    x: mark.noteLeft
                    width: Math.max(0, mark.noteRight - mark.noteLeft)
                    height: parent.height
                    visible: root.noteEnabled && mark.positioned && width > 0
                    clip: true
                    // Hold progress lives outside the note body so labels,
                    // selection borders and composite editing stay above it.
                    Rectangle {
                        y: parent.height - 2
                        width: Math.max(0, mark.progressEnd - mark.noteLeft)
                        height: root.style === "weave" ? 1 : 2
                        color: root.style === "legacy" ? root.playbackColor : mark.ink
                        opacity: mark.held ? 0.8 : 0.25
                    }
                    Rectangle {
                        y: 0; width: parent.width; height: 2
                        color: mark.ink
                        opacity: mark.onset * 0.85
                        visible: root.style !== "weave"
                    }
                    Rectangle {
                        y: 0; width: Math.min(parent.width, 28 + 18 * mark.onset)
                        height: parent.height
                        radius: 3
                        color: "transparent"
                        border.width: 2
                        border.color: root.coreColor
                        opacity: mark.onset
                        visible: root.style === "string"
                    }
                    Repeater {
                        model: root.style === "weave" ? 4 : 0
                        Rectangle {
                            required property int index
                            x: index * 9
                            y: index % 2 ? parent.height - 2 : 0
                            width: 6; height: 2
                            color: index % 2 ? mark.ink : root.coreColor
                            opacity: mark.onset
                        }
                    }
                }
            }
        }
    }
}
