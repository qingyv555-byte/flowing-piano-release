import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Shapes
import "CharacterNote.js" as CharacterNote

Rectangle {
    id: root
    // Parse only when the cached projection changes. Paint and hit testing use
    // native JS values, avoiding repeated nested QVariant property reads.
    readonly property var rawCanvasItems: JSON.parse(editorBridge.noteModel.visibleItemsJson)
    readonly property var compositeCanvasItems: displayMode === "smart"
        ? JSON.parse(editorBridge.compositeModel.visibleItemsJson) : []
    PedalLane {
        anchors.left:parent.left; anchors.right:parent.right; anchors.bottom:parent.bottom
        z:8; visible:keyboard.showPedalLane; theme:root.theme; timeline:root
    }
    Label {
        x:root.keyWidth+12; y:root.rulerHeight+8; z:8
        visible:keyboard.settingsDraft.showHints && editor.selectedCount===0
        text:keyboard.keyHints.map(p=>(keyboard.heldKeys.indexOf(p.pitch)>=0?"● ":"")+p.key+" "+p.pitch).join(" · ")
        color:root.theme.textSecondary; font.pixelSize:11
        width:Math.max(0,root.width-x-16); elide:Text.ElideRight
    }
    required property var theme
    required property var sceneSettings
    property var visualizer: null
    readonly property var skinSettings: theme.studio && typeof studioAppearance !== "undefined"
        && studioAppearance !== null ? studioAppearance.values : ({})
    readonly property string characterSkin: theme.customNoteColorActive ? "none"
        : (skinSettings.characterSkin || "none")
    readonly property bool characterMotionActive: characterSkin !== "none"
        && skinSettings.noteFeedback !== false && skinSettings.feedbackLevel !== "off"
        && !skinSettings.reducedMotion && !sceneSettings.powerSaveEnabled
        && !detachedAuditionActive
        && visible && (!Window.window || (Window.window.active
            && Window.window.visibility !== Window.Minimized))
        && (playback.playing || (selectionAudition !== null && selectionAudition.playing))
    function paintCharacterNote(ctx, x, y, width, height, start, duration, velocity) {
        if (root.theme.studio) {
            root.paintNoteBody(ctx, x, y, width, height)
            return
        }
        CharacterNote.paint(ctx, x, y, width, height, characterSkin,
            characterMotionActive ? cursorSeconds - start : -1, duration, velocity)
    }
    readonly property bool analysisRequested: studioFeedbackLoader.item !== null
        && studioFeedbackLoader.item.analysisRequested
    // Proposal presentation stays at the editor-surface boundary. The
    // timeline never asks the AgentLoop or Gateway for data; it only renders
    // the controller's frozen before/after projection when one is pending.
    property var proposalSource: null
    readonly property bool detachedAuditionActive: (playback.detachedPreviewNotes || []).length > 0
    readonly property real editorNotesOpacity: detachedAuditionActive ? 0.16 : 1.0
    onDetachedAuditionActiveChanged: {
        rawNoteCanvas.requestPaint()
        smartCompositeCanvas.requestPaint()
    }
    color: theme.pianoRollBackground
    property real pixelsPerSecond: 80
    readonly property bool advancedMode: experience.mode === "advanced"
    readonly property int normalHighestPitch: 84 // C6
    readonly property int normalLowestPitch: 36 // C2
    readonly property int advancedLowestPitch: 12 // C0
    readonly property int advancedHighestPitch: 108 // C8
    readonly property int minimumPitch: advancedMode ? advancedLowestPitch : normalLowestPitch
    readonly property int maximumPitch: advancedMode ? advancedHighestPitch : normalHighestPitch
    property int viewportTop: normalHighestPitch
    readonly property real rowHeight: 18
    readonly property int visiblePitchRows: Math.max(1, Math.floor(canvas.height / rowHeight))
    readonly property int minimumViewportTop: Math.min(maximumPitch, minimumPitch + visiblePitchRows - 1)
    readonly property int highestPitch: Math.max(minimumViewportTop, Math.min(maximumPitch, viewportTop))
    readonly property int lowestPitch: Math.max(minimumPitch, highestPitch - visiblePitchRows + 1)
    property real keyWidth: 72
    readonly property int rulerHeight: 30
    property bool followPlayback: true
    property bool dockMiniMapToBottomRight: false
    property bool hideEmptyOverview: false
    property bool focusMode: false
    property string displayMode: "original"
    readonly property bool denseProject: editor.noteModel.totalCount > 600
    readonly property bool useCompositeCanvas: root.displayMode === "smart"
        && root.denseProject
    readonly property bool useRawCanvas: root.displayMode === "original"
        && editor.noteModel.totalCount > 600
    readonly property string noteColorMode: experience.noteColorMode
    readonly property string noteEmphasisMode: experience.noteEmphasisMode
    readonly property string layerViewMode: experience.layerViewMode
    readonly property string activeLayerId: editor.activeLayerId
    readonly property var editorBridge: editor
    readonly property var playbackBridge: playback
    readonly property var selectionAudition: playback.selectionAudition
    property real viewStartSeconds: 0
    property real modelViewportStartSeconds: -1
    property real modelViewportEndSeconds: -1
    property int modelViewportLowestPitch: -1
    property int modelViewportHighestPitch: -1
    property var activeKeyboardPitches: []
    property bool gestureFeedbackVisible: false
    property bool creatingNote: false
    property bool compositeDragPreviewActive: false
    property var compositeDragPreviewIds: ({})
    property real compositeDragPreviewX: 0
    property real compositeDragPreviewY: 0
    property bool scrubActive: false
    property real scrubPointerX: keyWidth
    signal editorFocusRequested()

    function cancelLiveNoteEdit(): void {
        canvasPointer.cancelMarquee()
        editor.cancelGesture()
        editor.cancelDuplicateGesture()
        root.clearCompositeDragPreview()
        root.gestureFeedbackVisible = false
    }

    Shortcut {
        sequence: "Escape"
        context: Qt.WindowShortcut
        enabled: root.visible && (root.gestureFeedbackVisible
            || root.compositeDragPreviewActive || canvasPointer.marqueeActive)
        onActivated: root.cancelLiveNoteEdit()
    }
    readonly property real edgeScrollZone: 40
    readonly property int edgeScrollInterval: 33
    readonly property real maxViewStartSeconds: Math.max(0, miniMapTotalSeconds - visibleSeconds)
    // Keep a trailing viewport margin so follow mode can hold the playhead at
    // its reading anchor even when playback reaches the final note.
    readonly property real maxFollowViewStartSeconds: Math.max(
        0, miniMapTotalSeconds - visibleSeconds * 0.4
    )
    readonly property bool edgeScrollRunning: edgeScrollTimer.running
    readonly property string latestRecordedNoteId: keyboard.lastRecordedNoteId
    readonly property var harmonyVirtualNotes: typeof harmonyPreview !== "undefined"
        ? harmonyPreview.virtualNotes : []
    readonly property int harmonyPreviewFadeDuration: typeof scene === "undefined"
        || !scene.dynamicEnabled || scene.motionLevel === "low" || scene.powerSaveEnabled
        ? 0 : 120
    readonly property real cursorSeconds: keyboard.recording
        ? keyboard.recordCursorSeconds
        : (root.selectionAudition !== null && root.selectionAudition.playing
            ? root.selectionAudition.positionSeconds : playback.positionSeconds)
    readonly property real playheadScreenSeconds:
        root.followPlayback && playback.playing
        && root.cursorSeconds >= root.visibleSeconds * 0.4
        ? root.visibleSeconds * 0.4
        : root.cursorSeconds - root.viewStartSeconds
    readonly property bool playheadVisible: keyboard.recording || playback.playing
        || (root.selectionAudition !== null && root.selectionAudition.playing)
        || playback.positionSeconds > 0 || playback.totalSeconds > 0
    readonly property var proposalInspector: {
        if (!root.proposalSource)
            return ({ available: false })
        var changes = root.proposalSource.proposalChanges
        return changes && changes.inspector ? changes.inspector : ({ available: false })
    }
    readonly property bool proposalPending: root.proposalSource !== null
        && root.proposalSource.proposal
        && root.proposalSource.proposal.status === "pending"
    readonly property bool proposalOnlyChanges: root.proposalPending
        && root.proposalSource.proposalOnlyChanges === true
    readonly property var proposalChangedNoteIds: {
        var ids = {}
        if (!root.proposalPending)
            return ids
        var groups = root.proposalInspector.details || ({})
        var kinds = ["added", "updated", "deleted"]
        for (var kindIndex = 0; kindIndex < kinds.length; kindIndex++) {
            var details = groups[kinds[kindIndex]] || []
            for (var detailIndex = 0; detailIndex < details.length; detailIndex++) {
                var technical = details[detailIndex].technical || ({})
                if (technical.noteId !== undefined)
                    ids[String(technical.noteId)] = true
            }
        }
        return ids
    }
    readonly property var proposalOverlayNotes: {
        var overlays = []
        if (!root.proposalPending)
            return overlays
        var groups = root.proposalInspector.details || ({})
        var kinds = ["added", "updated", "deleted"]
        for (var kindIndex = 0; kindIndex < kinds.length; kindIndex++) {
            var kind = kinds[kindIndex]
            var details = groups[kind] || []
            for (var detailIndex = 0; detailIndex < details.length; detailIndex++) {
                var detail = details[detailIndex]
                var technical = detail.technical || ({})
                var before = detail.beforeNote || ({})
                var after = detail.afterNote || ({})
                var note = kind === "deleted" ? before : after
                if (note.pitch === undefined
                        || (note.startBeats === undefined && note.start === undefined)
                        || (note.durationBeats === undefined && note.duration === undefined))
                    continue
                var pitch = Number(note.pitch)
                var startTime = root.proposalNoteStartSeconds(note)
                var duration = root.proposalNoteDurationSeconds(note)
                var noteName = detail.noteName || "音符"
                if (isFinite(pitch))
                    noteName = root.pianoKeyLabel(pitch)
                overlays.push({
                    overlayKind: kind,
                    noteId: String(technical.noteId || ""),
                    pitch: pitch,
                    startTime: startTime,
                    duration: duration,
                    noteName: noteName
                })
                if (kind === "updated"
                        && before.pitch !== undefined
                        && (before.startBeats !== undefined || before.start !== undefined)
                        && (before.durationBeats !== undefined || before.duration !== undefined)
                        && (Number(before.pitch) !== Number(after.pitch)
                            || Math.abs(root.proposalNoteStartSeconds(before)
                                - root.proposalNoteStartSeconds(after)) > 0.000001
                            || Math.abs(root.proposalNoteDurationSeconds(before)
                                - root.proposalNoteDurationSeconds(after)) > 0.000001)) {
                    var beforePitch = Number(before.pitch)
                    overlays.push({
                        overlayKind: "ghost",
                        noteId: String(technical.noteId || ""),
                        pitch: beforePitch,
                        startTime: root.proposalNoteStartSeconds(before),
                        duration: root.proposalNoteDurationSeconds(before),
                        noteName: isFinite(beforePitch) ? root.pianoKeyLabel(beforePitch) : "音符"
                    })
                }
            }
        }
        return overlays
    }
    readonly property int proposalOverlayNoteCount: root.proposalOverlayNotes.length
    readonly property bool proposalOverlayUseCanvas: root.proposalOverlayNoteCount > 120
    property string proposalFocusedNoteId: ""
    // Musical grid derived from the project tempo (editor is the source of truth).
    readonly property real secondsPerBeat: 60.0 / editor.bpm
    readonly property real beatWidth: pixelsPerSecond * secondsPerBeat * (4.0 / editor.beatUnit)
    readonly property real barWidth: beatWidth * editor.beatsPerBar
    readonly property real visibleSeconds: Math.max(0.1, (width - keyWidth) / pixelsPerSecond)
    readonly property real proposalTotalSeconds: {
        var totalBeats = Number(root.proposalInspector.totalBeats)
        return isFinite(totalBeats) && totalBeats > 0
            ? totalBeats * root.secondsPerBeat : 0
    }
    property real previousNoteContentDuration: 0
    readonly property real noteContentDuration: editor.noteModel.noteDurationSeconds
    onNoteContentDurationChanged: {
        var shrank = noteContentDuration < previousNoteContentDuration
        previousNoteContentDuration = noteContentDuration
        if (shrank)
            Qt.callLater(root.clampViewAfterContentShrink)
    }
    readonly property real miniMapTotalSeconds: Math.max(
        visibleSeconds,
        noteContentDuration,
        playback.playing && !root.detachedAuditionActive ? playback.totalSeconds : 0,
        proposalTotalSeconds,
        secondsPerBeat * editor.beatsPerBar * 2
    )
    function clampViewAfterContentShrink() {
        if (!playback.playing && !root.scrubActive && (!canvasPointer || !canvasPointer.marqueeActive))
            viewStartSeconds = Math.max(0, Math.min(viewStartSeconds, miniMapTotalSeconds - visibleSeconds))
    }
    readonly property real defaultNewNoteDuration: secondsPerBeat * (4.0 / editor.beatUnit) * experience.newNoteLengthBeats
    readonly property real pianoPreviewDuration: secondsPerBeat * (4.0 / editor.beatUnit) * experience.pianoPreviewLengthBeats

    function syncKeyboardPreviewDefaults() {
        keyboard.setPreviewDefaults(experience.newNoteVelocity, pianoPreviewDuration)
    }

    function claimEditorFocus() {
        root.forceActiveFocus(Qt.MouseFocusReason)
        root.editorFocusRequested()
    }

    function gestureBarLabel(seconds) {
        var beat = Math.max(0, seconds / secondsPerBeat)
        var bar = Math.floor(beat / editor.beatsPerBar) + 1
        var beatInBar = Math.floor(beat % editor.beatsPerBar) + 1
        return "第 " + bar + " 小节 · 第 " + beatInBar + " 拍"
    }

    function updatePitchRange() {
        editor.setPitchRange(highestPitch, lowestPitch)
    }

    function syncCompositeViewport() {
        if (!editor || !editor.compositeModel)
            return
        var visibleStart = Math.max(0, root.viewStartSeconds)
        var visibleEnd = visibleStart + root.visibleSeconds
        var viewportStillCovered = root.modelViewportStartSeconds >= 0
            && visibleStart >= root.modelViewportStartSeconds
            && visibleEnd <= root.modelViewportEndSeconds
            && root.lowestPitch === root.modelViewportLowestPitch
            && root.highestPitch === root.modelViewportHighestPitch
        if (viewportStillCovered)
            return

        // Keep half a screen of notes on both sides. Smooth scrolling then
        // translates existing delegates without repeatedly filtering Python
        // models or resetting the Smart QAbstractListModel at frame rate.
        var overscanSeconds = Math.max(1, root.visibleSeconds * 0.5)
        root.modelViewportStartSeconds = Math.max(0, visibleStart - overscanSeconds)
        root.modelViewportEndSeconds = visibleEnd + overscanSeconds
        root.modelViewportLowestPitch = root.lowestPitch
        root.modelViewportHighestPitch = root.highestPitch
        if (editor.noteModel)
            editor.noteModel.set_viewport(
                root.modelViewportStartSeconds,
                root.modelViewportEndSeconds,
                root.lowestPitch,
                root.highestPitch
            )
        editor.compositeModel.set_viewport(
            root.modelViewportStartSeconds,
            root.modelViewportEndSeconds,
            root.lowestPitch,
            root.highestPitch
        )
    }

    function singletonAt(pointX, pointY) {
        if (pointX < root.keyWidth)
            return null
        var time = root.viewStartSeconds
            + (pointX - root.keyWidth) / root.pixelsPerSecond
        var pitch = root.highestPitch - Math.floor(pointY / root.rowHeight)
        var items = root.compositeCanvasItems.filter(item => item.triggerCount === 1)
        for (var index = items.length - 1; index >= 0; index--) {
            var item = items[index]
            if (time >= Number(item.startTime)
                    && time <= Number(item.endTime)
                    && pitch >= Number(item.pitchMin)
                    && pitch <= Number(item.pitchMax))
                return item
        }
        return null
    }

    function compositeAt(pointX, pointY) {
        if (pointX < root.keyWidth)
            return null
        var items = root.compositeCanvasItems
        for (var index = items.length - 1; index >= 0; index--) {
            var item = items[index]
            if (!Boolean(item.layerVisible)
                    || !root.layerVisibleInCurrentMode(String(item.layerId || "")))
                continue
            var start = Number(item.startTime || 0)
            var end = Number(item.endTime || start)
            var groupX = root.keyWidth
                + (start - root.viewStartSeconds) * root.pixelsPerSecond
            var groupWidth = Math.max(18, (end - start) * root.pixelsPerSecond)
            var groupY = (root.highestPitch - Number(item.pitchMax))
                * root.rowHeight + 2
            var groupHeight = Math.max(root.rowHeight - 4,
                (Number(item.pitchMax) - Number(item.pitchMin) + 1)
                    * root.rowHeight - 4)
            var visibleX = Math.max(root.keyWidth, groupX)
            var visibleRight = Math.min(root.width, groupX + groupWidth)
            if (pointX < visibleX || pointX > visibleRight
                    || pointY < groupY || pointY > groupY + groupHeight)
                continue

            var childDetails = item.childDetails || []
            var canExpand = childDetails.length > 1
                || Number(item.triggerCount || 0) > 1
            var expandX = visibleRight - 24
            if (canExpand && pointX >= expandX && pointY <= groupY + 26)
                return { kind: "group", item: item, expand: true }
            if (Boolean(item.expanded) && Number(item.triggerCount || 0) > 1) {
                for (var childIndex = childDetails.length - 1; childIndex >= 0; childIndex--) {
                    var detail = childDetails[childIndex] || ({})
                    var childStart = Number(detail.startTime || 0)
                    var childDuration = Math.max(0, Number(detail.duration || 0))
                    var childPitch = Number(detail.pitch || 0)
                    var childX = root.keyWidth
                        + (childStart - root.viewStartSeconds)
                            * root.pixelsPerSecond
                    var childY = (root.highestPitch - childPitch)
                        * root.rowHeight + 2
                    var childWidth = Math.max(3,
                        childDuration * root.pixelsPerSecond)
                    var childHeight = Math.max(4, root.rowHeight - 4)
                    if (pointX >= Math.max(root.keyWidth, childX)
                            && pointX <= Math.min(root.width, childX + childWidth)
                            && pointY >= childY
                            && pointY <= childY + childHeight)
                        return {
                            kind: "child",
                            noteId: String(detail.id || ""),
                            item: item,
                        }
                }
            }
            return {
                kind: "group",
                item: item,
                expand: false,
            }
        }
        return null
    }

    function rawNoteAt(pointX, pointY) {
        if (pointX < root.keyWidth)
            return null
        var time = root.viewStartSeconds
            + (pointX - root.keyWidth) / root.pixelsPerSecond
        var pitch = root.highestPitch - Math.floor(pointY / root.rowHeight)
        var items = root.rawCanvasItems
        for (var index = items.length - 1; index >= 0; index--) {
            var item = items[index]
            if (!Boolean(item.layerVisible)
                    || !root.layerVisibleInCurrentMode(String(item.layerId || "")))
                continue
            var start = Number(item.startTime || 0)
            var end = start + Number(item.duration || 0)
            if (time >= start && time <= end && Number(item.pitch) === pitch)
                return item
        }
        return null
    }

    function layerVisibleInCurrentMode(layerId) {
        if (root.layerViewMode === "active")
            return String(layerId) === root.activeLayerId
        return true
    }

    function layerOpacityInCurrentMode(layerId, visualRole) {
        if (root.layerViewMode === "active")
            return String(layerId) === root.activeLayerId ? 1.0 : 0.0
        if (root.layerViewMode === "all")
            return 1.0
        if (!root.theme.studio)
            return String(layerId) === root.activeLayerId ? 1.0 : 0.28
        // The old 0.28 opacity made the other tracks disappear on photos.
        // Style modes never change which tracks can play or be edited.
        if (root.noteEmphasisMode === "arrangement")
            return 1.0
        if (root.noteEmphasisMode === "melody")
            return visualRole === "melody" ? 1.0
                : String(layerId) === root.activeLayerId ? 0.78 : 0.72
        return visualRole === "melody" || String(layerId) === root.activeLayerId
            ? 1.0 : 0.88
    }

    function layerIsEditable(layerId, locked) {
        return String(layerId) === root.activeLayerId && !Boolean(locked)
    }

    function notePresentationColor(pitch, layerRole, hovered, layerCustomColor, velocity) {
        return root.theme.noteBodyColor(baseNotePresentationColor(pitch, layerRole, hovered, layerCustomColor),
            velocity === undefined ? 96 : velocity)
    }

    function paintNoteBody(ctx, x, y, width, height) {
        var r = Math.min(root.theme.noteCornerRadius, width / 2, height / 2)
        if (r <= 1) {
            ctx.fillRect(x, y, width, height)
            return
        }
        ctx.beginPath()
        ctx.moveTo(x + r, y)
        ctx.lineTo(x + width - r, y)
        ctx.quadraticCurveTo(x + width, y, x + width, y + r)
        ctx.lineTo(x + width, y + height - r)
        ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
        ctx.lineTo(x + r, y + height)
        ctx.quadraticCurveTo(x, y + height, x, y + height - r)
        ctx.lineTo(x, y + r)
        ctx.quadraticCurveTo(x, y, x + r, y)
        ctx.closePath()
        ctx.fill()
    }

    function baseNotePresentationColor(pitch, layerRole, hovered, layerCustomColor) {
        if (root.theme.noteSettings.roleColorMode === "custom") {
            var chosen = String(layerCustomColor || "").length > 0
                ? layerCustomColor : root.theme.layerRoleColor(layerRole)
            return hovered ? root.theme.mixColor(chosen, root.theme.noteLabelLight, 0.10)
                : chosen
        }
        if (root.theme.customNoteColorActive)
            return hovered ? root.theme.mixColor(root.theme.customNoteColor, root.theme.noteLabelLight, 0.10)
                : root.theme.customNoteColor
        if (root.noteColorMode === "layer") {
            if (String(layerCustomColor || "").length > 0)
                return layerCustomColor
            var layerColor = root.theme.layerRoleColor(layerRole)
            return hovered
                ? root.theme.mixColor(layerColor, root.theme.noteLabelLight, 0.10)
                : layerColor
        }
        if (root.noteColorMode === "mono")
            return hovered
                ? root.theme.mixColor(root.theme.noteMono, root.theme.noteLabelLight, 0.10)
                : root.theme.noteMono
        return root.theme.notePitchHoverColor(
            pitch, root.minimumPitch, root.maximumPitch, hovered)
    }

    function notePresentationBorder(pitch, layerRole, layerCustomColor) {
        return root.theme.mixColor(
            root.notePresentationColor(
                pitch, layerRole, false, layerCustomColor),
            root.theme.paper ? root.theme.noteLabelDark : root.theme.noteLabelLight,
            root.theme.studio ? 0.38 : 0.28
        )
    }

    function proposalNotesOpacity(noteIds, normalOpacity) {
        if (!root.proposalOnlyChanges)
            return normalOpacity
        for (var i = 0; i < noteIds.length; ++i) {
            if (root.proposalNoteIsChanged(String(noteIds[i])))
                return normalOpacity
        }
        return 0.14
    }

    function beginCompositeDragPreview(noteIds) {
        var ids = ({})
        for (var index = 0; index < noteIds.length; ++index)
            ids[String(noteIds[index])] = true
        compositeDragPreviewIds = ids
        compositeDragPreviewX = 0
        compositeDragPreviewY = 0
        compositeDragPreviewActive = true
    }

    function updateCompositeDragPreview(dx, dy) {
        if (!compositeDragPreviewActive)
            return
        compositeDragPreviewX = dx
        compositeDragPreviewY = dy
    }

    function clearCompositeDragPreview() {
        compositeDragPreviewActive = false
        compositeDragPreviewIds = ({})
        compositeDragPreviewX = 0
        compositeDragPreviewY = 0
    }

    function compositeDragPreviewAppliesTo(noteIds) {
        if (!compositeDragPreviewActive || noteIds.length === 0)
            return false
        for (var index = 0; index < noteIds.length; ++index) {
            if (compositeDragPreviewIds[String(noteIds[index])] !== true)
                return false
        }
        return true
    }

    function proposalNoteIsChanged(noteId) {
        return root.proposalChangedNoteIds[String(noteId)] === true
    }

    function proposalNoteStartSeconds(note) {
        if (!note)
            return 0
        if (note.startBeats !== undefined) {
            var beats = Number(note.startBeats)
            return isFinite(beats) ? beats * root.secondsPerBeat : 0
        }
        var seconds = Number(note.start || 0)
        return isFinite(seconds) ? seconds : 0
    }

    function proposalNoteDurationSeconds(note) {
        if (!note)
            return 0
        if (note.durationBeats !== undefined) {
            var beats = Number(note.durationBeats)
            return isFinite(beats) ? Math.max(0, beats * root.secondsPerBeat) : 0
        }
        var seconds = Number(note.duration || 0)
        return isFinite(seconds) ? Math.max(0, seconds) : 0
    }

    function clampViewportTop(value) {
        return Math.max(minimumViewportTop, Math.min(maximumPitch, Math.round(value)))
    }

    function clampViewStart(seconds) {
        var value = Number(seconds)
        if (!isFinite(value))
            value = 0
        return Math.max(0, Math.min(maxViewStartSeconds, value))
    }

    function clampFollowViewStart(seconds) {
        var value = Number(seconds)
        if (!isFinite(value))
            value = 0
        return Math.max(0, Math.min(maxFollowViewStartSeconds, value))
    }

    function revealPlaybackPosition(force) {
        if (!root.followPlayback && !keyboard.recording)
            return

        var cursor = Math.max(0, Number(root.cursorSeconds))
        var targetStart = root.clampFollowViewStart(
            cursor - root.visibleSeconds * 0.4
        )
        // Keep the cursor at the same 40% anchor.  The transport may update
        // every 10 ms for audio scheduling, while the viewport itself is
        // deliberately refreshed at display rate by followPlaybackTimer.
        if (force || playback.playing
                || (root.selectionAudition !== null && root.selectionAudition.playing)
                || keyboard.recording) {
            root.viewStartSeconds = targetStart
        }
    }

    function setFollowPlayback(enabled) {
        root.followPlayback = Boolean(enabled)
        if (root.followPlayback)
            root.revealPlaybackPosition(true)
    }

    function requestPlaybackFollow() {
        if (!root.followPlayback)
            return
        if (!followPlaybackTimer.running)
            followPlaybackTimer.start()
    }

    Timer {
        id: followPlaybackTimer
        interval: 16
        repeat: false
        onTriggered: root.revealPlaybackPosition(false)
    }

    Connections {
        target: root.proposalSource
        function onProposalNoteLocateRequested(noteId, requestedStartBeats, requestedPitch, kind) {
            var startBeats = Number(requestedStartBeats)
            var startTime = isFinite(startBeats) ? startBeats * root.secondsPerBeat : 0
            root.followPlayback = false
            root.viewStartSeconds = root.clampViewStart(
                startTime - root.visibleSeconds * 0.25
            )
            var pitch = Number(requestedPitch)
            if (isFinite(pitch))
                root.viewportTop = root.clampViewportTop(
                    pitch + Math.floor(root.visiblePitchRows * 0.5)
                )
            root.proposalFocusedNoteId = String(noteId || "")
            proposalFocusTimer.restart()
        }
    }

    Timer {
        id: proposalFocusTimer
        interval: 1800
        onTriggered: root.proposalFocusedNoteId = ""
    }

    function scrollPitchViewport(direction) {
        viewportTop = clampViewportTop(highestPitch + direction * 6)
    }

    function focusPitchRange(range) {
        if (!range || range.lowest === undefined || range.highest === undefined)
            return
        var middle = (Number(range.lowest) + Number(range.highest)) / 2
        viewportTop = clampViewportTop(middle + (visiblePitchRows - 1) / 2)
    }

    function revealPitchRangeIfNeeded(range) {
        if (!range || range.lowest === undefined || range.highest === undefined)
            return
        var lowest = Number(range.lowest)
        var highest = Number(range.highest)
        if (lowest >= root.lowestPitch && highest <= root.highestPitch)
            return
        focusPitchRange(range)
    }

    function wheelDirection(wheel) {
        var delta = wheel.angleDelta.y !== 0 ? wheel.angleDelta.y : wheel.pixelDelta.y
        return delta === 0 ? 0 : (delta > 0 ? 1 : -1)
    }

    function zoomAt(x, direction) {
        var focusTime = Math.max(0, (x - keyWidth) / pixelsPerSecond + viewStartSeconds)
        var next = Math.max(30, Math.min(320, pixelsPerSecond * (direction > 0 ? 1.15 : 1 / 1.15)))
        pixelsPerSecond = next
        viewStartSeconds = Math.max(0, focusTime - (x - keyWidth) / next)
        editor.setViewScale(next, rowHeight)
    }

    function setKeyboardPitchActive(pitch, active) {
        var next = activeKeyboardPitches.slice()
        var index = next.indexOf(pitch)
        if (active && index < 0)
            next.push(pitch)
        else if (!active && index >= 0)
            next.splice(index, 1)
        activeKeyboardPitches = next
    }

    function seekAt(x) {
        playback.seek(Math.max(0, (x - keyWidth) / pixelsPerSecond + viewStartSeconds))
    }

    function clampedScrubX(x) {
        return Math.max(keyWidth, Math.min(width, Number(x)))
    }

    function edgeScrollDelta(x) {
        var pointer = clampedScrubX(x)
        var leftEdge = keyWidth + edgeScrollZone
        var rightEdge = width - edgeScrollZone
        if (pointer < leftEdge) {
            var leftDepth = Math.min(1, (leftEdge - pointer) / edgeScrollZone)
            return -(12 + 36 * leftDepth)
        }
        if (pointer > rightEdge) {
            var rightDepth = Math.min(1, (pointer - rightEdge) / edgeScrollZone)
            return 12 + 36 * rightDepth
        }
        return 0
    }

    function canEdgeScroll(delta) {
        if (!scrubActive || delta === 0)
            return false
        return delta < 0 ? viewStartSeconds > 0 : viewStartSeconds < maxViewStartSeconds
    }

    function updateEdgeScrollTimer() {
        var delta = edgeScrollDelta(scrubPointerX)
        edgeScrollTimer.running = canEdgeScroll(delta)
    }

    function beginScrub(rootX) {
        scrubActive = true
        followPlayback = false
        updateScrub(rootX)
    }

    function updateScrub(rootX) {
        if (!scrubActive)
            return
        scrubPointerX = clampedScrubX(rootX)
        seekAt(scrubPointerX)
        updateEdgeScrollTimer()
    }

    function finishScrub() {
        scrubActive = false
        edgeScrollTimer.stop()
        setFollowPlayback(true)
    }

    function tickEdgeScroll() {
        var delta = edgeScrollDelta(scrubPointerX)
        if (!canEdgeScroll(delta)) {
            edgeScrollTimer.stop()
            return
        }
        var previous = viewStartSeconds
        viewStartSeconds = Math.max(
            0,
            Math.min(maxViewStartSeconds, previous + delta / pixelsPerSecond)
        )
        seekAt(scrubPointerX)
        if (viewStartSeconds === previous)
            edgeScrollTimer.stop()
    }

    function returnToPlayback() {
        setFollowPlayback(true)
    }

    function navigateFromMiniMap(seconds, pitch) {
        followPlayback = false
        viewStartSeconds = Math.max(0, seconds - visibleSeconds * 0.5)
        viewportTop = clampViewportTop(pitch + (visiblePitchRows - 1) * 0.5)
    }

    Timer {
        id: edgeScrollTimer
        interval: root.edgeScrollInterval
        repeat: true
        running: false
        onTriggered: root.tickEdgeScroll()
    }

    function isBlackKey(pitch) {
        var pitchClass = ((pitch % 12) + 12) % 12
        return pitchClass === 1 || pitchClass === 3 || pitchClass === 6
            || pitchClass === 8 || pitchClass === 10
    }

    function pianoKeyLabel(pitch) {
        var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return names[((pitch % 12) + 12) % 12] + (Math.floor(pitch / 12) - 1)
    }

    Component.onCompleted: {
        prefetchVisiblePitches()
        focusPitchRange(editor.notePitchRange)
        updatePitchRange()
        syncKeyboardPreviewDefaults()
        syncCompositeViewport()
    }
    onHighestPitchChanged: {
        prefetchVisiblePitches()
        updatePitchRange()
        syncCompositeViewport()
    }
    onLowestPitchChanged: { syncCompositeViewport(); prefetchVisiblePitches() }
    function prefetchVisiblePitches() {
        if (root.theme && root.theme.studio && typeof pianoTone !== "undefined"
                && pianoTone !== null && typeof pianoTone.prefetchRange === "function")
            pianoTone.prefetchRange(root.lowestPitch, root.highestPitch)
    }
    onViewportTopChanged: syncCompositeViewport()
    onViewStartSecondsChanged: syncCompositeViewport()
    onVisibleSecondsChanged: syncCompositeViewport()
    onPixelsPerSecondChanged: syncCompositeViewport()
    onDisplayModeChanged: syncCompositeViewport()
    onWidthChanged: syncCompositeViewport()
    onRowHeightChanged: {
        editor.setViewScale(pixelsPerSecond, rowHeight)
        syncCompositeViewport()
    }
    onDefaultNewNoteDurationChanged: syncKeyboardPreviewDefaults()

    Loader {
        anchors.fill: parent
        active: !root.theme.studio
        sourceComponent: EditorBackgroundLayer {
            theme: root.theme
            scene: root.sceneSettings
            focusMode: root.theme.nocturne && root.focusMode
        }
    }
    Loader {
        anchors.fill: parent
        active: root.theme.studio
        sourceComponent: StudioBackground {
            objectName: "studioEditorBackground"
            theme: root.theme; settings: studioAppearance; target: "editor"
            protectText: false
            active: root.visible && !root.focusMode
            motionActive: playback.playing && !root.sceneSettings.powerSaveEnabled
        }
    }

    Rectangle {
        id: ruler
        objectName: "barRuler"
        anchors.top: parent.top
        width: parent.width
        height: root.rulerHeight
        color: root.theme.toolbar
        border.color: root.theme.border

        Item {
            anchors.fill: parent
            Repeater {
                model: Math.max(2, Math.ceil((root.width - root.keyWidth) / root.pixelsPerSecond) + 2)
                delegate: Item {
                    required property int index
                    readonly property int secondIndex: Math.floor(root.viewStartSeconds) + index
                    x: root.keyWidth + (secondIndex - root.viewStartSeconds) * root.pixelsPerSecond
                    width: root.pixelsPerSecond
                    height: parent.height
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: 1
                        height: 7
                        color: root.theme.borderStrong
                    }
                    Text {
                        x: 4
                        anchors.verticalCenter: parent.verticalCenter
                        text: secondIndex + " 秒"
                        color: root.theme.muted
                        font.pixelSize: 10
                        font.family: root.theme.numberFont
                    }
                }
            }
        }
        MouseArea {
            objectName: "timelineSeekArea"
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            onPressed: mouse => {
                root.claimEditorFocus()
                var point = mapToItem(root, mouse.x, mouse.y)
                root.beginScrub(point.x)
            }
            onPositionChanged: mouse => {
                if (!pressed)
                    return
                var point = mapToItem(root, mouse.x, mouse.y)
                root.updateScrub(point.x)
            }
            onReleased: root.finishScrub()
            onCanceled: root.finishScrub()
        }
    }

    Item {
        id: canvas
        objectName: "pianoCanvas"
        anchors.top: ruler.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        clip: true

        Item {
            id: pitchViewport
            objectName: "pitchViewport"
            anchors.fill: parent
        }

        Item {
            objectName: "activeKeyboardKeys"
            anchors.fill: parent
        }

        Item {
            objectName: "timelineZoomArea"
            anchors.fill: parent
        }

        Loader {
            anchors.fill: parent
            active: !root.theme.studio
            sourceComponent: ReactiveRhythmLayer {
                theme: root.theme
                scene: root.sceneSettings
                playbackController: playback
                keyWidth: root.keyWidth
                visualizer: root.visualizer
                focusMode: root.theme.nocturne && root.focusMode
            }
        }
        Loader {
            id: studioFeedbackLoader
            anchors.fill: parent
            z: 8
            active: root.theme.studio
            sourceComponent: StudioFeedback {
                theme: root.theme; settings: studioAppearance
                visualizer: root.visualizer; playbackController: root.playbackBridge
                timeline: root; feedbackController: studioFeedback
                overview: timelineMiniMap
                active: root.visible
                windowActive: root.Window.window !== null && root.Window.window.visible
                    && root.Window.window.visibility !== Window.Minimized
                powerSave: root.sceneSettings.powerSaveEnabled
            }
        }

        Item {
            id: pianoKeyboard
            objectName: "pianoKeyboard"
            width: root.keyWidth
            height: parent.height
            z: 12

            Repeater {
                model: root.visiblePitchRows
                delegate: Rectangle {
                    id: pianoKey
                    required property int index
                    readonly property int pitchValue: root.highestPitch - index
                    readonly property bool blackKey: root.isBlackKey(pitchValue)
                    readonly property bool active: root.activeKeyboardPitches.indexOf(pitchValue) >= 0
                    readonly property bool middleC: pitchValue === 60
                    objectName: "pianoKey-" + pitchValue
                    x: 0
                    y: index * root.rowHeight
                    width: blackKey ? root.keyWidth * 0.62 : root.keyWidth
                    height: root.rowHeight
                    radius: blackKey ? 2 : 0
                    scale: active ? 0.96 : 1.0
                    transformOrigin: Item.Right
                    color: active ? root.theme.playingNote
                        : (blackKey ? root.theme.keyDark : root.theme.keyLight)
                    border.width: middleC ? 2 : 1
                    border.color: middleC ? root.theme.middleCHighlight : root.theme.border
                    z: blackKey ? 2 : 1
                    Behavior on scale { NumberAnimation { duration: 180 } }

                    Rectangle {
                        visible: pianoKey.middleC
                        anchors.left: parent.left
                        width: 4
                        height: parent.height
                        color: root.theme.middleCHighlight
                    }

                    Text {
                        anchors.right: parent.right
                        anchors.rightMargin: 4
                        anchors.verticalCenter: parent.verticalCenter
                        text: pianoKey.middleC ? "中央 C4" : root.pianoKeyLabel(pianoKey.pitchValue)
                        color: pianoKey.active ? root.theme.textOnAccent
                            : (pianoKey.blackKey
                                ? (root.theme.studio ? root.theme.noteLabelLight : root.theme.textPrimary)
                                : root.theme.keyDark)
                        font.pixelSize: root.theme.studio ? 11 : (pianoKey.middleC ? 10 : 8)
                        font.bold: pianoKey.middleC
                    }

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        Accessible.name: root.pianoKeyLabel(pianoKey.pitchValue) + " 钢琴键"
                        onPressed: {
                            root.claimEditorFocus()
                            if (keyboard.recording)
                                keyboard.pressPitch(pianoKey.pitchValue)
                            else
                                keyboard.tapPitch(pianoKey.pitchValue)
                        }
                        onReleased: {
                            if (keyboard.recording)
                                keyboard.releasePitch(pianoKey.pitchValue)
                        }
                        onCanceled: {
                            if (keyboard.recording)
                                keyboard.releasePitch(pianoKey.pitchValue)
                        }
                    }
                }
            }

            Rectangle {
                id: centerCMarker
                objectName: "centerCPianoKey"
                visible: 60 <= root.highestPitch && 60 >= root.lowestPitch
                x: 0
                y: (root.highestPitch - 60) * root.rowHeight
                width: 4
                height: root.rowHeight
                color: root.theme.middleCHighlight
                z: 4
            }
        }

        Repeater {
            model: root.visiblePitchRows + 1
            delegate: Rectangle {
                required property int index
                x: root.keyWidth
                y: index * root.rowHeight
                width: canvas.width - root.keyWidth
                height: 1
                color: index % 12 === 0 ? root.theme.gridLineMajor : root.theme.gridLineMinor
            }
        }

        Repeater {
            model: Math.ceil((canvas.width - root.keyWidth) / root.beatWidth) + 3
            delegate: Rectangle {
                required property int index
                readonly property int beatIndex: Math.floor(root.viewStartSeconds * root.pixelsPerSecond / root.beatWidth) + index
                readonly property bool barLine: beatIndex % editor.beatsPerBar === 0
                x: root.keyWidth + beatIndex * root.beatWidth - root.viewStartSeconds * root.pixelsPerSecond
                y: 0
                width: 1
                height: canvas.height
                color: barLine ? root.theme.barLine : root.theme.gridLineMajor
            }
        }

        Repeater {
            model: root.harmonyVirtualNotes
            delegate: Rectangle {
                id: harmonyVirtualNote
                required property int measureIndex
                required property real startSeconds
                required property real durationSeconds
                required property int pitch
                required property bool temporary
                readonly property real rawNoteX: root.keyWidth
                    + (startSeconds - root.viewStartSeconds) * root.pixelsPerSecond
                readonly property real rawNoteWidth: Math.max(18, durationSeconds * root.pixelsPerSecond)
                readonly property bool withinPitchViewport: pitch <= root.highestPitch
                    && pitch >= root.lowestPitch
                readonly property bool withinTimeViewport: rawNoteX + rawNoteWidth > root.keyWidth
                    && rawNoteX < canvas.width
                property bool appeared: false
                objectName: "harmonyVirtualNote-" + measureIndex + "-" + pitch
                x: Math.max(root.keyWidth, rawNoteX)
                y: (root.highestPitch - pitch) * root.rowHeight + 2
                width: Math.max(0, Math.min(canvas.width, rawNoteX + rawNoteWidth) - x)
                height: Math.max(4, root.rowHeight - 4)
                visible: withinPitchViewport && withinTimeViewport && width > 0
                color: root.theme.notePitchColor(pitch, root.minimumPitch, root.maximumPitch)
                opacity: appeared ? root.theme.notePreviewAlpha : 0
                z: 5
                Behavior on opacity {
                    NumberAnimation {
                        duration: root.harmonyPreviewFadeDuration
                        easing.type: Easing.OutQuad
                    }
                }
                Component.onCompleted: appeared = true
                Shape {
                    id: harmonyPreviewOutline
                    anchors.fill: parent
                    ShapePath {
                        fillColor: "transparent"
                        strokeColor: root.theme.notePitchBorderColor(
                            pitch, root.minimumPitch, root.maximumPitch
                        )
                        strokeWidth: 1
                        strokeStyle: ShapePath.DashLine
                        dashPattern: [3, 2]
                        startX: 0.5
                        startY: 0.5
                        PathLine { x: harmonyPreviewOutline.width - 0.5; y: 0.5 }
                        PathLine {
                            x: harmonyPreviewOutline.width - 0.5
                            y: harmonyPreviewOutline.height - 0.5
                        }
                        PathLine { x: 0.5; y: harmonyPreviewOutline.height - 0.5 }
                        PathLine { x: 0.5; y: 0.5 }
                    }
                }
            }
        }

        Rectangle {
            objectName: "recordingStatusBadge"
            visible: keyboard.recording
            x: root.keyWidth + 12
            y: 10
            width: 88
            height: 26
            radius: 13
            color: root.theme.record
            border.color: root.theme.record
            border.width: 1
            z: 26
            Accessible.name: "正在录制 " + keyboard.recordSeconds.toFixed(1) + " 秒"
            Text {
                anchors.centerIn: parent
                text: "● 正在录制 " + keyboard.recordSeconds.toFixed(1) + "秒"
                color: root.theme.textOnDanger
                font.bold: true
                font.pixelSize: 11
            }
        }

        Rectangle {
            id: gestureFeedback
            objectName: "gestureFeedback"
            visible: root.gestureFeedbackVisible && editor.selectionSummary.count === 1
            z: 70
            x: root.keyWidth + 14
            y: 12
            width: Math.min(parent.width - x - root.theme.space3,
                            Math.max(244, feedbackContent.implicitWidth + root.theme.space5))
            height: feedbackContent.implicitHeight + root.theme.space3
            radius: root.theme.radiusM
            color: root.theme.feedbackSurface
            border.color: root.theme.feedbackBorder
            border.width: 1
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: root.theme.durFeedback } }

            Column {
                id: feedbackContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: root.theme.space3
                anchors.rightMargin: root.theme.space3
                spacing: root.theme.space1

                Row {
                    spacing: root.theme.space2

                    Text {
                        text: "正在移动"
                        color: root.theme.textSecondary
                        font.pixelSize: 12
                        font.bold: true
                    }

                    Rectangle {
                        width: 1
                        height: 14
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.theme.borderStrong
                    }

                    Text {
                        text: editor.selectionSummary.noteName || "—"
                        color: root.theme.feedbackPitch
                        font.pixelSize: 14
                        font.bold: true
                    }
                }

                Row {
                    spacing: root.theme.space2

                    Text {
                        text: "时间"
                        color: root.theme.textSecondary
                        font.pixelSize: 12
                    }

                    Text {
                        text: Number(editor.selectionSummary.startTime || 0).toFixed(2) + " 秒"
                        color: root.theme.textPrimary
                        font.pixelSize: 12
                        font.family: root.theme.numberFont
                    }

                    Rectangle {
                        width: 1
                        height: 14
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.theme.borderStrong
                    }

                    Text {
                        text: root.gestureBarLabel(Number(editor.selectionSummary.startTime || 0))
                        color: root.theme.textPrimary
                        font.pixelSize: 12
                    }
                }
            }
        }

        Rectangle {
            objectName: "recordingLane"
            visible: keyboard.recording
            x: root.keyWidth + 10
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 68
            width: Math.min(310, Math.max(210, parent.width - root.keyWidth - 20))
            height: 32
            radius: 8
            color: root.theme.elevatedSurface
            border.color: root.theme.record
            border.width: 1
            z: 25

            Row {
                anchors.centerIn: parent
                spacing: 8
                Rectangle {
                    width: 8
                    height: 8
                    radius: 4
                    color: root.theme.record
                    SequentialAnimation on opacity {
                        running: keyboard.recording
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.35; duration: 500 }
                        NumberAnimation { to: 1.0; duration: 500 }
                    }
                }
                Text {
                    text: "正在录制 · " + keyboard.recordSeconds.toFixed(1) + "s"
                    color: root.theme.textPrimary
                    font.pixelSize: 12
                    font.bold: true
                }
                Text {
                    text: "A S D F G H J 弹奏"
                    color: root.theme.textSecondary
                    font.pixelSize: 11
                }
            }
        }

        MouseArea {
            id: canvasPointer
            objectName: "drawNoteArea"
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            property real pressX: 0
            property real pressY: 0
            property real pressSeconds: 0
            property real pressPitch: 0
            property real pointerX: 0
            property real pointerY: 0
            property bool marqueeActive: false
            property bool marqueeAdditive: false
            property bool suppressRelease: false
            property string drawingNoteId: ""

            function boundedX() { return Math.max(root.keyWidth, Math.min(width, pointerX)) }
            function boundedY() { return Math.max(0, Math.min(height, pointerY)) }
            function anchorX() { return root.keyWidth + (pressSeconds - root.viewStartSeconds) * root.pixelsPerSecond }
            function anchorY() { return (root.highestPitch - pressPitch) * root.rowHeight }
            function updateMarquee() {
                var x = anchorX(), y = anchorY()
                marquee.visible = true
                marquee.x = Math.max(root.keyWidth, Math.min(x, boundedX()))
                marquee.y = Math.min(y, boundedY())
                marquee.width = Math.max(0, Math.min(width, Math.max(x, boundedX())) - marquee.x)
                marquee.height = Math.abs(boundedY() - y)
            }
            function cancelMarquee() {
                suppressRelease = true
                marquee.visible = false
                marqueeActive = false
                marqueeAdditive = false
                drawingNoteId = ""
            }
            readonly property real horizontalScroll: root.edgeScrollDelta(pointerX)
            readonly property int verticalScroll: pointerY < root.edgeScrollZone ? 1
                : pointerY > height - root.edgeScrollZone ? -1 : 0
            Timer {
                interval: root.edgeScrollInterval
                repeat: true
                running: canvasPointer.pressed && canvasPointer.marqueeActive && (
                    canvasPointer.horizontalScroll < 0 && root.viewStartSeconds > 0
                    || canvasPointer.horizontalScroll > 0 && root.viewStartSeconds < root.maxViewStartSeconds
                    || canvasPointer.verticalScroll > 0 && root.highestPitch < root.maximumPitch
                    || canvasPointer.verticalScroll < 0 && root.highestPitch > root.minimumViewportTop)
                onTriggered: {
                    root.viewStartSeconds = Math.max(0, Math.min(root.maxViewStartSeconds,
                        root.viewStartSeconds + canvasPointer.horizontalScroll / root.pixelsPerSecond))
                    root.viewportTop = root.clampViewportTop(root.highestPitch + canvasPointer.verticalScroll)
                    canvasPointer.updateMarquee()
                }
            }

            onPressed: mouse => {
                root.claimEditorFocus()
                pressX = mouse.x
                pressY = mouse.y
                pressSeconds = (mouse.x - root.keyWidth) / root.pixelsPerSecond + root.viewStartSeconds
                pressPitch = root.highestPitch - mouse.y / root.rowHeight
                pointerX = mouse.x
                pointerY = mouse.y
                marqueeActive = false
                suppressRelease = false
                marqueeAdditive = (mouse.modifiers & Qt.ControlModifier) !== 0
                    || (mouse.modifiers & Qt.ShiftModifier) !== 0
                drawingNoteId = ""
                marquee.visible = false
            }
            onPositionChanged: mouse => {
                if (!pressed || pressX < root.keyWidth)
                    return
                var dx = mouse.x - pressX
                var dy = mouse.y - pressY
                if (!marqueeActive && (Math.abs(dx) >= 6 || Math.abs(dy) >= 6))
                    marqueeActive = true
                if (marqueeActive) {
                    root.followPlayback = false
                    pointerX = mouse.x
                    pointerY = mouse.y
                    updateMarquee()
                }
            }
            onReleased: mouse => {
                if (suppressRelease)
                    return
                if (pressX < root.keyWidth)
                    return
                if (marqueeActive) {
                    pointerX = mouse.x
                    pointerY = mouse.y
                    editor.selectRect(
                        pressSeconds * root.pixelsPerSecond,
                        anchorY(),
                        boundedX() - anchorX(),
                        boundedY() - anchorY(),
                        marqueeAdditive,
                        root.layerViewMode === "active" ? root.activeLayerId : ""
                    )
                } else if (!root.theme.studio && editor.selectedCount > 0) {
                    // A short click on the canvas exits the current editing
                    // context. Modifier clicks preserve it, matching additive
                    // marquee semantics. Dragging still takes the branch above.
                    if (!marqueeAdditive)
                        editor.clearSelection()
                } else if (!root.theme.studio || !marqueeAdditive) {
                    // Writing is continuous: selection cannot consume the next
                    // empty-canvas click just to dismiss its editing context.
                    root.creatingNote = true
                    try {
                        if (root.theme.studio) {
                            editor.createNoteAt(
                                pressX - root.keyWidth + root.viewStartSeconds * root.pixelsPerSecond,
                                pressY, root.defaultNewNoteDuration, experience.newNoteVelocity)
                        } else {
                            drawingNoteId = editor.beginDrawNoteAt(
                                pressX - root.keyWidth + root.viewStartSeconds * root.pixelsPerSecond,
                                pressY,
                                root.defaultNewNoteDuration,
                                experience.newNoteVelocity
                            )
                            if (drawingNoteId.length > 0)
                                editor.commitGesture()
                        }
                    } finally {
                        root.creatingNote = false
                    }
                }
                cancelMarquee()
            }
            onCanceled: cancelMarquee()
            onWheel: wheel => {
                var direction = root.wheelDirection(wheel)
                if (direction === 0)
                    return
                if (wheel.modifiers & Qt.ControlModifier) {
                    root.zoomAt(wheel.x, direction)
                    wheel.accepted = true
                } else if (wheel.modifiers & Qt.ShiftModifier) {
                    root.followPlayback = false
                    root.viewStartSeconds = Math.max(0, root.viewStartSeconds - direction)
                    wheel.accepted = true
                } else {
                    root.scrollPitchViewport(direction)
                    wheel.accepted = true
                }
            }
        }

        Canvas {
            id: rawNoteCanvas
            objectName: "rawNoteCanvas"
            opacity: root.editorNotesOpacity
            anchors.fill: parent
            visible: root.useRawCanvas
            enabled: visible
            z: 9
            readonly property var paintPalette: [root.theme.muted, root.theme.textMuted,
                root.theme.noteSelectedBorder, root.theme.notePlaybackBorder,
                root.theme.noteMono, root.theme.noteLabelLight, root.theme.noteLabelDark,
                root.theme.notePitchLow, root.theme.notePitchLowMid, root.theme.notePitchMid,
                root.theme.notePitchHighMid, root.theme.notePitchHigh,
                root.theme.layerMelody, root.theme.layerAccompaniment,
                root.theme.layerBass, root.theme.layerDrums, root.theme.layerOther]
            onPaintPaletteChanged: requestPaint()
            onVisibleChanged: if (visible) requestPaint()

            onPaint: {
                if (root.displayMode !== "original")
                    return
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var items = root.rawCanvasItems
                var audition = root.playbackBridge.selectionAudition
                var auditioning = audition !== null && audition.playing
                // Styles depend on layer/pitch, not individual note identity.
                // Reuse only within this paint so theme and view changes cannot
                // leave stale colors behind.
                var stylesByLayer = Object.create(null)
                var keyWidth = root.keyWidth
                var viewStart = root.viewStartSeconds
                var timeScale = root.pixelsPerSecond
                var rightEdge = root.width
                var topPitch = root.highestPitch
                var rowHeight = root.rowHeight
                var noteHeight = Math.max(4, rowHeight - 4)
                var playing = root.playbackBridge.playing && !root.detachedAuditionActive
                var position = root.playbackBridge.positionSeconds
                var onlyChanges = root.proposalOnlyChanges
                var deletedFill = root.theme.muted
                var deletedBorder = root.theme.textMuted
                var playingBorder = root.theme.notePlaybackBorder
                var selectedBorder = root.theme.noteSelectedBorder
                ctx.font = "9px " + JSON.stringify(root.theme.fontUi)
                for (var index = 0; index < items.length; index++) {
                    var item = items[index]
                    if (!Boolean(item.layerVisible)
                            || !root.layerVisibleInCurrentMode(
                                String(item.layerId || "")))
                        continue
                    var pitch = Number(item.pitch)
                    var start = Number(item.startTime || 0)
                    var duration = Math.max(0, Number(item.duration || 0))
                    var noteX = keyWidth + (start - viewStart) * timeScale
                    var noteWidth = Math.max(18, duration * timeScale)
                    // The model retains scroll overscan. Skip its offscreen
                    // notes before color, opacity and Canvas drawing work.
                    if (noteX + noteWidth < keyWidth || noteX > rightEdge)
                        continue
                    var layerId = String(item.layerId || "")
                    var layerStyles = stylesByLayer[layerId]
                    if (!layerStyles) {
                        layerStyles = Object.create(null)
                        stylesByLayer[layerId] = layerStyles
                    }
                    var styleKey = pitch + ":" + Number(item.velocity || 96)
                    var style = layerStyles[styleKey]
                    if (!style) {
                        var role = String(item.visualRole || item.layerRole || "other")
                        var customColor = String(item.layerCustomColor || "")
                        style = [root.notePresentationColor(pitch, role, false, customColor, Number(item.velocity || 96)),
                            root.notePresentationBorder(pitch, role, customColor),
                            root.theme.textOnColor(root.notePresentationColor(pitch, role, false, customColor, Number(item.velocity || 96)))]
                        layerStyles[styleKey] = style
                    }
                    var noteY = (topPitch - pitch) * rowHeight + 2
                    var selected = Boolean(item.selected)
                    var deleted = String(item.status || "") === "deleted"
                    var playbackActive = playing && position >= start
                        && position <= start + duration
                    var alpha = auditioning && !selected ? 0.18
                        : (deleted ? 0.55 : 1.0)
                    alpha *= root.layerOpacityInCurrentMode(
                        layerId, String(item.visualRole || item.layerRole || "other"))
                    ctx.globalAlpha = onlyChanges
                        ? root.proposalNotesOpacity([String(item.noteId)], alpha) : alpha
                    ctx.fillStyle = deleted ? deletedFill : style[0]
                    if (root.characterSkin !== "none" && !deleted)
                        root.paintCharacterNote(ctx, noteX, noteY, noteWidth, noteHeight,
                            start, duration, Number(item.velocity || 96))
                    else
                        root.paintNoteBody(ctx, noteX, noteY, noteWidth, noteHeight)
                    ctx.globalAlpha = 1.0
                    ctx.lineWidth = selected || playbackActive ? 2 : 1
                    ctx.strokeStyle = deleted ? deletedBorder
                        : (playbackActive ? playingBorder
                            : (selected ? selectedBorder : style[1]))
                    if (deleted || selected || playbackActive
                            || (root.theme.studio && root.noteColorMode === "layer"
                                && String(item.visualRole || item.layerRole) === "melody"))
                        ctx.strokeRect(noteX + 0.5, noteY + 0.5,
                            Math.max(0, noteWidth - 1), Math.max(0, noteHeight - 1))
                    if (noteWidth >= 28) {
                        ctx.fillStyle = !root.theme.studio && root.characterSkin !== "none" && !deleted ? "#201d26" : style[2]
                        ctx.fillText(String(item.noteName || root.pianoKeyLabel(pitch)),
                            noteX + 4, noteY + noteHeight * 0.68)
                    }
                }
                ctx.globalAlpha = 1.0
            }

            MouseArea {
                id: rawCanvasPointer
                objectName: "rawCanvasPointer"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                hoverEnabled: true
                preventStealing: true
                property real previousX: 0
                property real previousY: 0
                property bool gestureStarted: false
                onPressed: mouse => {
                    var hit = root.rawNoteAt(mouse.x, mouse.y)
                    if (!hit) {
                        mouse.accepted = false
                        return
                    }
                    root.claimEditorFocus()
                    gestureStarted = false
                    if (String(hit.layerId || "") !== root.activeLayerId)
                        root.editorBridge.setActiveLayer(String(hit.layerId || ""))
                    if (Boolean(hit.layerLocked))
                        return
                    var point = rawCanvasPointer.mapToItem(null, mouse.x, mouse.y)
                    previousX = point.x
                    previousY = point.y
                    var append = (mouse.modifiers & Qt.ControlModifier) !== 0
                        || (mouse.modifiers & Qt.ShiftModifier) !== 0
                    if (!Boolean(hit.selected) || append)
                        root.editorBridge.selectNote(String(hit.noteId), append)
                    root.editorBridge.beginGesture()
                    gestureStarted = true
                    root.gestureFeedbackVisible = true
                }
                onPositionChanged: mouse => {
                    if (!gestureStarted || !pressed
                            || (mouse.buttons & Qt.LeftButton) === 0)
                        return
                    var point = rawCanvasPointer.mapToItem(null, mouse.x, mouse.y)
                    root.editorBridge.previewMovePixels(
                        point.x - previousX, point.y - previousY)
                    previousX = point.x
                    previousY = point.y
                }
                onReleased: mouse => {
                    if (gestureStarted)
                        root.editorBridge.commitGesture()
                    gestureStarted = false
                    root.gestureFeedbackVisible = false
                }
                onCanceled: {
                    if (gestureStarted)
                        root.editorBridge.cancelGesture()
                    gestureStarted = false
                    root.gestureFeedbackVisible = false
                }
            }

            Connections {
                target: root.editorBridge.noteModel
                function onChanged() { rawNoteCanvas.requestPaint() }
            }
            Connections {
                target: root.playbackBridge.selectionAudition
                function onPlayingChanged() { rawNoteCanvas.requestPaint() }
            }
            Connections {
                target: root
                function onViewStartSecondsChanged() { rawNoteCanvas.requestPaint() }
                function onViewportTopChanged() { rawNoteCanvas.requestPaint() }
                function onHighestPitchChanged() { rawNoteCanvas.requestPaint() }
                function onPixelsPerSecondChanged() { rawNoteCanvas.requestPaint() }
                function onVisibleSecondsChanged() { rawNoteCanvas.requestPaint() }
                function onWidthChanged() { rawNoteCanvas.requestPaint() }
                function onNoteColorModeChanged() { rawNoteCanvas.requestPaint() }
                function onNoteEmphasisModeChanged() { rawNoteCanvas.requestPaint() }
                function onCharacterSkinChanged() { rawNoteCanvas.requestPaint() }
                function onSkinSettingsChanged() { rawNoteCanvas.requestPaint() }
                function onCharacterMotionActiveChanged() { rawNoteCanvas.requestPaint() }
                function onLayerViewModeChanged() { rawNoteCanvas.requestPaint() }
                function onActiveLayerIdChanged() { rawNoteCanvas.requestPaint() }
                function onMinimumPitchChanged() { rawNoteCanvas.requestPaint() }
                function onMaximumPitchChanged() { rawNoteCanvas.requestPaint() }
            }
            Connections {
                target: root.playbackBridge
                function onPlayingChanged() { rawNoteCanvas.requestPaint() }
                function onPositionChanged() { rawNoteCanvas.requestPaint() }
            }
        }

        Repeater {
            id: rawNoteRepeater
            objectName: "rawNoteRepeater"
            model: root.displayMode === "original" && editor.noteModel.totalCount <= 600
                ? editor.noteModel : null
            delegate: Rectangle {
                id: noteItem
                required property string noteId
                required property int pitch
                required property string noteName
                required property int velocity
                required property real startTime
                required property real duration
                required property bool selected
                required property string status
                required property string layerId
                required property string layerRole
                required property string visualRole
                required property string layerCustomColor
                required property bool layerVisible
                required property bool layerLocked
                readonly property bool latestRecorded: noteId === root.latestRecordedNoteId
                readonly property bool pending: status === "pending"
                readonly property bool modified: status === "modified"
                readonly property bool deleted: status === "deleted"
                readonly property bool activeLayer: layerId === root.activeLayerId
                readonly property bool ghosted: !activeLayer
                    && root.layerViewMode === "ghost"
                readonly property bool selectionAuditionActive:
                    playback.selectionAudition !== null
                    && playback.selectionAudition.playing
                readonly property bool playbackActive: playback.playing
                    && !root.detachedAuditionActive
                    && playback.positionSeconds >= startTime
                    && playback.positionSeconds <= startTime + duration
                objectName: latestRecorded ? "latestRecordedNote" : "noteItem-" + noteId
                readonly property real rawNoteX: root.keyWidth + (startTime - root.viewStartSeconds) * root.pixelsPerSecond
                readonly property real rawNoteWidth: Math.max(18, duration * root.pixelsPerSecond)
                x: Math.max(root.keyWidth, rawNoteX)
                y: (root.highestPitch - pitch) * root.rowHeight + 2
                width: Math.max(0, Math.min(canvas.width, rawNoteX + rawNoteWidth) - x)
                height: Math.max(4, root.rowHeight - 4)
                visible: layerVisible
                    && root.layerVisibleInCurrentMode(layerId) && width > 0
                radius: root.theme.noteCornerRadius
                readonly property color presentationColor:
                    root.notePresentationColor(
                        pitch, visualRole,
                        body.containsMouse || resizeHandle.containsMouse,
                        layerCustomColor, velocity)
                readonly property bool interactive: layerVisible
                    && root.layerIsEditable(layerId, layerLocked) && !deleted
                color: deleted ? root.theme.muted
                    : root.characterSkin !== "none" ? "transparent" : presentationColor
                opacity: root.layerOpacityInCurrentMode(layerId, visualRole)
                    * root.editorNotesOpacity
                    * (selectionAuditionActive && !selected ? 0.18
                    : (root.proposalOnlyChanges && !root.proposalNoteIsChanged(noteId)
                        ? 0.14
                        : (deleted ? 0.55 : (body.pressed ? root.theme.noteDragAlpha
                            : (pending ? root.theme.notePreviewAlpha : 1.0)))))
                border.width: selected || playbackActive || latestRecorded
                    || body.containsMouse || resizeHandle.containsMouse ? 2
                        : deleted || (root.theme.studio && root.noteColorMode === "layer"
                            && visualRole === "melody") ? 1 : 0
                border.color: deleted ? root.theme.textMuted
                    : (playbackActive || latestRecorded ? root.theme.notePlaybackBorder
                        : (selected ? root.theme.noteSelectedBorder
                            : root.notePresentationBorder(
                                pitch, visualRole, layerCustomColor)))
                z: 10
                activeFocusOnTab: true
                scale: latestRecorded && keyboard.recording ? 1.04 : 1.0
                Behavior on scale { NumberAnimation { duration: 180 } }
                Accessible.name: noteName + " 音符"

                Loader {
                    anchors.fill: parent
                    anchors.margins: noteItem.border.width
                    active: root.characterSkin !== "none" && !noteItem.deleted && noteItem.visible
                    sourceComponent: CharacterNoteSurface {
                        noteRenderer: root.theme.studio ? root : null
                        fillColor: noteItem.presentationColor
                        styleKey: root.theme.noteStyle
                        character: root.characterSkin
                        startTime: noteItem.startTime
                        duration: noteItem.duration
                        velocity: noteItem.velocity
                        position: root.cursorSeconds
                        motionActive: root.characterMotionActive
                    }
                }

                Rectangle {
                    id: noteStateGlow
                    objectName: "noteStateGlow-" + noteItem.noteId
                    anchors.fill: parent
                    anchors.margins: -2
                    radius: 4
                    color: "transparent"
                    border.width: noteItem.selected || noteItem.playbackActive
                        || body.containsMouse || resizeHandle.containsMouse ? 2 : 0
                    border.color: noteItem.selected ? root.theme.noteSelectedGlow
                        : (noteItem.playbackActive ? root.theme.notePlaybackGlow
                            : root.theme.noteHoverGlow)
                    visible: border.width > 0
                    z: -1
                }

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    text: noteItem.noteName
                    color: !root.theme.studio && root.characterSkin !== "none" && !noteItem.deleted ? "#201d26"
                        : root.theme.textOnColor(noteItem.deleted ? root.theme.textMuted : noteItem.presentationColor)
                    font.pixelSize: 9
                    font.strikeout: noteItem.deleted
                    visible: noteItem.width >= 28
                }

                Repeater {
                    visible: false
                    model: 0
                    delegate: Rectangle {
                        required property int index
                        x: 3 + index * 8
                        y: 1
                        width: 4
                        height: 1
                        color: root.theme.warning
                    }
                }

                Rectangle {
                    id: lifecycleDecoration
                    objectName: "lifecycleDecoration-" + noteItem.noteId
                    visible: false
                    anchors.right: parent.right
                    anchors.rightMargin: 2
                    anchors.top: parent.top
                    anchors.topMargin: -9
                    width: badgeLabel.implicitWidth + 8
                    height: 14
                    radius: 7
                    color: root.theme.elevatedSurface
                    border.color: root.theme.warning
                    border.width: 1
                    z: 2
                    Text {
                        id: badgeLabel
                        anchors.centerIn: parent
                        text: noteItem.pending ? "未同步" : "已改"
                        color: root.theme.warning
                        font.pixelSize: 8
                        font.bold: true
                    }
                }

                Rectangle {
                    visible: noteItem.deleted
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    height: 1
                    color: root.theme.danger
                }

                MouseArea {
                    id: body
                    objectName: "noteBody-" + noteItem.noteId
                    anchors.fill: parent
                    anchors.rightMargin: 8
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    hoverEnabled: true
                    cursorShape: noteItem.interactive ? Qt.SizeAllCursor : Qt.ArrowCursor
                    property real previousX: 0
                    property real previousY: 0
                    property bool duplicating: false
                    property bool gestureStarted: false

                    ToolTip.visible: false
                    ToolTip.text: noteItem.pending ? "未同步音符"
                        : (noteItem.modified ? "已修改，等待同步" : "已删除，等待同步")

                    onPressed: mouse => {
                        gestureStarted = false
                        duplicating = false
                        root.claimEditorFocus()
                        if (noteItem.deleted)
                            return
                        if (!noteItem.activeLayer)
                            editor.setActiveLayer(noteItem.layerId)
                        if (noteItem.layerLocked)
                            return
                        if (mouse.button === Qt.RightButton) {
                            editor.selectNote(noteItem.noteId, false)
                            var menuPoint = body.mapToItem(root, mouse.x, mouse.y)
                            noteMenu.x = Math.min(menuPoint.x, root.width - noteMenu.width - 8)
                            noteMenu.y = Math.min(menuPoint.y, root.height - noteMenu.height - 8)
                            noteMenu.open()
                            return
                        }
                        // Keep drag deltas in scene coordinates. Selection
                        // actions replace fixed toolbar content and never move
                        // this canvas, while side-panel and resize changes can
                        // still update the item's current local geometry.
                        var point = body.mapToItem(null, mouse.x, mouse.y)
                        previousX = point.x
                        previousY = point.y
                        var appendToSelection = (mouse.modifiers & Qt.ControlModifier) !== 0
                            || (mouse.modifiers & Qt.ShiftModifier) !== 0
                        duplicating = (mouse.modifiers & Qt.AltModifier) !== 0
                        if (duplicating) {
                            if (!noteItem.selected)
                                editor.selectNote(noteItem.noteId, false)
                            editor.beginDuplicateGesture()
                        } else {
                            if (!noteItem.selected || appendToSelection)
                                editor.selectNote(noteItem.noteId, appendToSelection)
                            editor.beginGesture()
                        }
                        gestureStarted = true
                        root.gestureFeedbackVisible = true
                    }
                    onPositionChanged: mouse => {
                        if (!gestureStarted || !pressed
                                || (mouse.buttons & Qt.LeftButton) === 0)
                            return
                        var point = body.mapToItem(null, mouse.x, mouse.y)
                        if (duplicating)
                            editor.previewDuplicatePixels(point.x - previousX, point.y - previousY)
                        else
                            editor.previewMovePixels(point.x - previousX, point.y - previousY)
                        previousX = point.x
                        previousY = point.y
                    }
                    onReleased: mouse => {
                        if (mouse.button === Qt.LeftButton && gestureStarted) {
                            if (duplicating)
                                editor.commitDuplicateGesture()
                            else
                                editor.commitGesture()
                            duplicating = false
                            root.gestureFeedbackVisible = false
                        }
                        gestureStarted = false
                    }
                    onCanceled: {
                        if (gestureStarted) {
                            if (duplicating)
                                editor.cancelDuplicateGesture()
                            else
                                editor.cancelGesture()
                        }
                        duplicating = false
                        gestureStarted = false
                        root.gestureFeedbackVisible = false
                    }
                }

                MouseArea {
                    id: resizeHandle
                    width: 8
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.right: parent.right
                    hoverEnabled: true
                    cursorShape: noteItem.deleted ? Qt.ArrowCursor : Qt.SizeHorCursor
                    enabled: !noteItem.layerLocked && !noteItem.deleted
                    property real previousX: 0

                    onPressed: mouse => {
                        root.claimEditorFocus()
                        if (noteItem.deleted)
                            return
                        previousX = resizeHandle.mapToItem(canvas, mouse.x, mouse.y).x
                        editor.selectNote(noteItem.noteId, false)
                        editor.beginGesture()
                        root.gestureFeedbackVisible = true
                    }
                    onPositionChanged: mouse => {
                        if (!pressed)
                            return
                        var currentX = resizeHandle.mapToItem(canvas, mouse.x, mouse.y).x
                        editor.previewResizePixels(currentX - previousX)
                        previousX = currentX
                    }
                    onReleased: {
                        editor.commitGesture()
                        root.gestureFeedbackVisible = false
                    }
                    onCanceled: {
                        editor.cancelGesture()
                        root.gestureFeedbackVisible = false
                    }
                }

                Popup {
                    id: noteMenu
                    objectName: "noteContextMenu"
                    parent: Overlay.overlay
                    width: 148
                    height: 42
                    padding: 4
                    modal: false
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                    background: Rectangle {
                        radius: 8
                        color: root.theme.elevatedSurface
                        border.color: root.theme.borderStrong
                    }
                    contentItem: Rectangle {
                        radius: 5
                        color: deletePointer.containsMouse ? "#33FF6B7A" : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "删除音符"
                            color: root.theme.danger
                            font.bold: true
                            font.pixelSize: 12
                        }
                        MouseArea {
                            id: deletePointer
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                editor.deleteSelected()
                                noteMenu.close()
                            }
                        }
                    }
                }
            }
        }

        Canvas {
            id: smartCompositeCanvas
            opacity: root.editorNotesOpacity
            readonly property var paintPalette: rawNoteCanvas.paintPalette.concat([
                root.theme.surface, root.theme.borderStrong, root.theme.textPrimary])
            onPaintPaletteChanged: requestPaint()
            onVisibleChanged: if (visible) requestPaint()
            objectName: "smartCompositeCanvas"
            anchors.fill: parent
            visible: root.useCompositeCanvas
            enabled: visible
            z: 9

            onPaint: {
                if (root.displayMode !== "smart")
                    return
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var items = root.compositeCanvasItems
                var audition = root.playbackBridge.selectionAudition
                var auditioning = audition !== null && audition.playing
                var selectedIds = (root.editorBridge.compositeSelectionSummary || ({})
                    ).childNoteIds || []
                for (var index = 0; index < items.length; index++) {
                    var item = items[index]
                    if (!Boolean(item.layerVisible)
                            || !root.layerVisibleInCurrentMode(String(item.layerId || "")))
                        continue
                    var selected = Number(item.selectedChildCount || 0) > 0
                    var pitch = Number(item.representativePitch)
                    var start = Number(item.startTime || 0)
                    var end = Number(item.endTime || start)
                    var noteX = root.keyWidth
                        + (start - root.viewStartSeconds) * root.pixelsPerSecond
                    var noteWidth = Math.max(18, (end - start)
                        * root.pixelsPerSecond)
                    var noteY = (root.highestPitch - Number(item.pitchMax))
                        * root.rowHeight + 2
                    var noteHeight = Math.max(root.rowHeight - 4,
                        (Number(item.pitchMax) - Number(item.pitchMin) + 1)
                            * root.rowHeight - 4)
                    var layerAlpha = root.layerOpacityInCurrentMode(
                        String(item.layerId || ""), String(item.visualRole || item.layerRole || "other"))
                    var alpha = layerAlpha * (auditioning && !selected ? 0.18 : (root.theme.studio ? 1.0 : 0.86))
                    ctx.globalAlpha = root.proposalNotesOpacity(item.childNoteIds || [], alpha)
                    ctx.fillStyle = root.notePresentationColor(
                        pitch, String(item.visualRole || item.layerRole || "other"), false,
                        String(item.layerCustomColor || ""), Number(item.velocity || 96))
                    if (root.characterSkin !== "none")
                        root.paintCharacterNote(ctx, noteX, noteY, noteWidth, noteHeight,
                            Number(item.startTime), Number(item.endTime) - Number(item.startTime), 96)
                    else
                        root.paintNoteBody(ctx, noteX, noteY, noteWidth, noteHeight)
                    ctx.globalAlpha = 1.0
                    ctx.lineWidth = selected ? 2 : 1
                    ctx.strokeStyle = selected
                        ? root.theme.noteSelectedBorder
                        : root.notePresentationBorder(
                            pitch, String(item.visualRole || item.layerRole || "other"),
                            String(item.layerCustomColor || ""))
                    // Composite notes span several pitches. Keep a thin
                    // boundary around every Studio composite so mixed colors
                    // remain readable even on user-supplied photographs.
                    if (selected || root.theme.studio)
                        ctx.strokeRect(noteX + 0.5, noteY + 0.5,
                            Math.max(0, noteWidth - 1), Math.max(0, noteHeight - 1))

                    var details = item.childDetails || []
                    if (Boolean(item.expanded) && details.length > 1) {
                        for (var childIndex = 0; childIndex < details.length; childIndex++) {
                            var detail = details[childIndex] || ({})
                            var childId = String(detail.id || "")
                            var childPitch = Number(detail.pitch || 0)
                            var childStart = Number(detail.startTime || 0)
                            var childDuration = Math.max(0, Number(detail.duration || 0))
                            var childX = root.keyWidth
                                + (childStart - root.viewStartSeconds)
                                    * root.pixelsPerSecond
                            var childY = (root.highestPitch - childPitch)
                                * root.rowHeight + 2
                            var childWidth = Math.max(3,
                                childDuration * root.pixelsPerSecond)
                            var childHeight = Math.max(4, root.rowHeight - 4)
                            var childSelected = selectedIds.indexOf(childId) >= 0
                            ctx.globalAlpha = auditioning && !childSelected
                                ? 0.18 : 1.0
                            ctx.fillStyle = root.notePresentationColor(
                                childPitch, String(item.visualRole || item.layerRole || "other"), false,
                                String(item.layerCustomColor || ""), Number(detail.velocity || 96))
                            if (root.characterSkin !== "none")
                                root.paintCharacterNote(ctx, childX, childY, childWidth, childHeight,
                                    Number(detail.startTime), Number(detail.duration), Number(detail.velocity || 96))
                            else
                                root.paintNoteBody(ctx, childX, childY, childWidth, childHeight)
                            ctx.globalAlpha = 1.0
                            ctx.lineWidth = childSelected ? 2 : 1
                            ctx.strokeStyle = childSelected
                                ? root.theme.noteSelectedBorder
                                : root.notePresentationBorder(
                                    childPitch, String(item.visualRole || item.layerRole || "other"),
                                    String(item.layerCustomColor || ""))
                            if (childSelected)
                                ctx.strokeRect(childX + 0.5, childY + 0.5,
                                    Math.max(0, childWidth - 1),
                                    Math.max(0, childHeight - 1))
                            if (childWidth >= 28) {
                                ctx.fillStyle = root.theme.textOnColor(root.notePresentationColor(childPitch, String(item.visualRole || item.layerRole || "other"), false, String(item.layerCustomColor || ""), Number(detail.velocity || 96)))
                                ctx.font = "8px " + JSON.stringify(root.theme.fontUi)
                                ctx.fillText(String(detail.noteName
                                    || root.pianoKeyLabel(childPitch)),
                                    childX + 3, childY + childHeight * 0.68)
                            }
                        }
                    } else if (details.length > 1) {
                        for (var markerIndex = 0; markerIndex < details.length; markerIndex++) {
                            var marker = details[markerIndex] || ({})
                            var markerX = root.keyWidth
                                + (Number(marker.startTime || 0)
                                    - root.viewStartSeconds) * root.pixelsPerSecond
                            var markerSelected = selectedIds.indexOf(String(marker.id || "")) >= 0
                            ctx.globalAlpha = markerSelected ? 1.0 : 0.82
                            ctx.fillStyle = markerSelected
                                ? root.theme.noteSelectedBorder
                                : root.notePresentationBorder(
                                    Number(marker.pitch || pitch),
                                    String(item.visualRole || item.layerRole || "other"),
                                    String(item.layerCustomColor || ""))
                            ctx.fillRect(markerX, noteY, 2, noteHeight)
                        }
                        ctx.globalAlpha = 1.0
                    }

                    if (Boolean(item.pitchVariant)) {
                        ctx.fillStyle = root.theme.textOnColor(root.notePresentationColor(pitch, String(item.visualRole || item.layerRole || "other"), false, String(item.layerCustomColor || "")))
                        ctx.font = "bold 10px " + JSON.stringify(root.theme.fontUi)
                        ctx.fillText("~", noteX + 4, noteY + 11)
                    }
                    if (Number(item.triggerCount || 0) > 1 && noteWidth >= 24) {
                        var buttonX = Math.min(root.width, noteX + noteWidth) - 24
                        ctx.fillStyle = root.theme.surface
                        ctx.fillRect(buttonX, noteY + 2, 22, 22)
                        ctx.strokeStyle = root.theme.borderStrong
                        ctx.lineWidth = 1
                        ctx.strokeRect(buttonX + 0.5, noteY + 2.5, 21, 21)
                        ctx.fillStyle = root.theme.textPrimary
                        ctx.font = "14px " + JSON.stringify(root.theme.fontUi)
                        ctx.fillText(Boolean(item.expanded) ? "−" : "+",
                            buttonX + 7, noteY + 18)
                    }
                }
                ctx.globalAlpha = 1.0
            }

            MouseArea {
                id: smartCompositePointer
                objectName: "smartCompositePointer"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                hoverEnabled: true
                onPressed: mouse => {
                    var pressedHit = root.compositeAt(mouse.x, mouse.y)
                    if (!pressedHit)
                        mouse.accepted = false
                }
                onClicked: mouse => {
                    var hit = root.compositeAt(mouse.x, mouse.y)
                    if (!hit)
                        return
                    if (hit.expand) {
                        root.editorBridge.compositeModel.toggle_expanded(
                            String(hit.item.groupId || ""))
                        root.claimEditorFocus()
                        return
                    }
                    if (String(hit.item.layerId || "") !== root.activeLayerId) {
                        root.editorBridge.setActiveLayer(String(hit.item.layerId || ""))
                        root.claimEditorFocus()
                        return
                    }
                    if (Boolean(hit.item.layerLocked))
                        return
                    if (hit.kind === "child")
                        root.editorBridge.selectNotes([hit.noteId])
                    else
                        root.editorBridge.selectNotes(hit.item.childNoteIds || [])
                    root.claimEditorFocus()
                }
            }

            Connections {
                target: root.editorBridge.compositeModel
                function onChanged() { smartCompositeCanvas.requestPaint() }
            }
            Connections {
                target: root.playbackBridge.selectionAudition
                function onPlayingChanged() { smartCompositeCanvas.requestPaint() }
            }
            Connections {
                target: root
                function onViewStartSecondsChanged() { smartCompositeCanvas.requestPaint() }
                function onViewportTopChanged() { smartCompositeCanvas.requestPaint() }
                function onHighestPitchChanged() { smartCompositeCanvas.requestPaint() }
                function onPixelsPerSecondChanged() { smartCompositeCanvas.requestPaint() }
                function onVisibleSecondsChanged() { smartCompositeCanvas.requestPaint() }
                function onNoteColorModeChanged() { smartCompositeCanvas.requestPaint() }
                function onNoteEmphasisModeChanged() { smartCompositeCanvas.requestPaint() }
                function onCharacterSkinChanged() { smartCompositeCanvas.requestPaint() }
                function onSkinSettingsChanged() { smartCompositeCanvas.requestPaint() }
                function onCharacterMotionActiveChanged() { smartCompositeCanvas.requestPaint() }
                function onLayerViewModeChanged() { smartCompositeCanvas.requestPaint() }
                function onActiveLayerIdChanged() { smartCompositeCanvas.requestPaint() }
                function onMinimumPitchChanged() { smartCompositeCanvas.requestPaint() }
                function onMaximumPitchChanged() { smartCompositeCanvas.requestPaint() }
                function onWidthChanged() { smartCompositeCanvas.requestPaint() }
            }
            Connections {
                target: root.playbackBridge
                function onPlayingChanged() { smartCompositeCanvas.requestPaint() }
                function onPositionChanged() { smartCompositeCanvas.requestPaint() }
            }
        }

        Repeater {
            id: compositeNoteRepeater
            objectName: "compositeNoteRepeater"
            model: root.displayMode === "smart" && !root.denseProject
                ? editor.compositeModel : null
            delegate: CompositeNoteDelegate {
                theme: root.theme
                timeline: root
                editor: root.editorBridge
                playback: root.playbackBridge
            }
        }

        Repeater {
            id: proposalChangeOverlayRepeater
            objectName: "proposalChangeOverlay"
            model: root.proposalOverlayUseCanvas ? [] : root.proposalOverlayNotes
            delegate: Rectangle {
                required property var modelData
                readonly property string overlayKind: String(modelData.overlayKind || "updated")
                readonly property string noteId: String(modelData.noteId || "")
                readonly property int pitch: Number(modelData.pitch === undefined ? 60 : modelData.pitch)
                readonly property real startTime: Number(modelData.startTime || 0)
                readonly property real duration: Math.max(0, Number(modelData.duration || 0))
                readonly property real rawNoteX: root.keyWidth
                    + (startTime - root.viewStartSeconds) * root.pixelsPerSecond
                readonly property real rawNoteWidth: Math.max(18, duration * root.pixelsPerSecond)
                readonly property bool withinPitchViewport: pitch <= root.highestPitch
                    && pitch >= root.lowestPitch
                readonly property bool withinTimeViewport: rawNoteX + rawNoteWidth > root.keyWidth
                    && rawNoteX < canvas.width
                objectName: "proposalOverlayNote-" + overlayKind + "-" + noteId
                x: Math.max(root.keyWidth, rawNoteX)
                y: (root.highestPitch - pitch) * root.rowHeight + 1
                width: Math.max(0, Math.min(canvas.width, rawNoteX + rawNoteWidth) - x)
                height: Math.max(6, root.rowHeight - 2)
                visible: withinPitchViewport && withinTimeViewport && width > 0
                radius: 3
                color: overlayKind === "added"
                    ? Qt.rgba(root.theme.success.r, root.theme.success.g, root.theme.success.b, 0.24)
                    : (overlayKind === "deleted" || overlayKind === "ghost"
                        ? Qt.rgba(root.theme.danger.r, root.theme.danger.g, root.theme.danger.b, 0.18)
                        : "transparent")
                border.width: root.proposalFocusedNoteId === noteId ? 3 : 2
                border.color: overlayKind === "added" ? root.theme.success
                    : (overlayKind === "deleted" || overlayKind === "ghost"
                        ? root.theme.danger : root.theme.warning)
                opacity: overlayKind === "ghost" ? 0.75 : 1.0
                z: 13
                enabled: false

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.noteName || "音符"
                    color: overlayKind === "added" ? root.theme.success
                        : (overlayKind === "deleted" || overlayKind === "ghost"
                            ? root.theme.danger : root.theme.warning)
                    font.pixelSize: 9
                    font.bold: true
                    visible: parent.width >= 32
                }
            }
        }

        Canvas {
            id: proposalChangeOverlayCanvas
            objectName: "proposalChangeOverlayCanvas"
            anchors.fill: parent
            visible: root.proposalPending && root.proposalOverlayUseCanvas
            enabled: false
            z: 13

            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var overlays = root.proposalOverlayNotes
                for (var index = 0; index < overlays.length; index++) {
                    var item = overlays[index]
                    var pitch = Number(item.pitch)
                    if (!isFinite(pitch))
                        continue
                    var rawX = root.keyWidth
                        + (Number(item.startTime) - root.viewStartSeconds)
                            * root.pixelsPerSecond
                    var rawWidth = Math.max(18,
                        Number(item.duration) * root.pixelsPerSecond)
                    var x = Math.max(root.keyWidth, rawX)
                    var right = Math.min(width, rawX + rawWidth)
                    var y = (root.highestPitch - pitch) * root.rowHeight + 1
                    if (right <= root.keyWidth || x >= width
                            || y + root.rowHeight < 0 || y > height)
                        continue
                    var kind = String(item.overlayKind || "updated")
                    var isGhost = kind === "ghost"
                    var color = kind === "added" ? root.theme.success
                        : (kind === "deleted" || isGhost
                            ? root.theme.danger : root.theme.warning)
                    ctx.globalAlpha = isGhost ? 0.75 : 1.0
                    ctx.strokeStyle = color
                    ctx.lineWidth = root.proposalFocusedNoteId
                        === String(item.noteId || "") ? 3 : 2
                    if (kind === "added") {
                        ctx.globalAlpha = 0.24
                        ctx.fillStyle = color
                        ctx.fillRect(x, y, right - x, Math.max(6, root.rowHeight - 2))
                        ctx.globalAlpha = 1.0
                    } else if (kind === "deleted" || isGhost) {
                        ctx.globalAlpha = isGhost ? 0.13 : 0.18
                        ctx.fillStyle = color
                        ctx.fillRect(x, y, right - x, Math.max(6, root.rowHeight - 2))
                        ctx.globalAlpha = isGhost ? 0.75 : 1.0
                    }
                    ctx.strokeRect(x, y, right - x, Math.max(6, root.rowHeight - 2))
                }
                ctx.globalAlpha = 1.0
            }
        }

        Connections {
            target: root
            function repaintProposalOverlay() {
                proposalChangeOverlayCanvas.requestPaint()
            }
            function onProposalOverlayNotesChanged() { repaintProposalOverlay() }
            function onProposalPendingChanged() { repaintProposalOverlay() }
            function onProposalOnlyChangesChanged() {
                rawNoteCanvas.requestPaint()
                smartCompositeCanvas.requestPaint()
            }
            function onProposalOverlayUseCanvasChanged() { repaintProposalOverlay() }
            function onViewStartSecondsChanged() { repaintProposalOverlay() }
            function onPixelsPerSecondChanged() { repaintProposalOverlay() }
            function onHighestPitchChanged() { repaintProposalOverlay() }
            function onLowestPitchChanged() { repaintProposalOverlay() }
            function onProposalFocusedNoteIdChanged() { repaintProposalOverlay() }
            function onWidthChanged() { repaintProposalOverlay() }
            function onHeightChanged() { repaintProposalOverlay() }
        }

        Repeater {
            model: editor.duplicatePreview
            delegate: Rectangle {
                required property string sourceId
                required property int pitch
                required property string noteName
                required property int velocity
                required property real startTime
                required property real duration
                objectName: "duplicatePreviewNote-" + sourceId
                readonly property real rawNoteX: root.keyWidth + (startTime - root.viewStartSeconds) * root.pixelsPerSecond
                readonly property real rawNoteWidth: Math.max(18, duration * root.pixelsPerSecond)
                x: Math.max(root.keyWidth, rawNoteX)
                y: (root.highestPitch - pitch) * root.rowHeight + 2
                width: Math.max(0, Math.min(canvas.width, rawNoteX + rawNoteWidth) - x)
                height: Math.max(4, root.rowHeight - 4)
                visible: width > 0
                radius: 2
                color: root.theme.note
                opacity: 0.34
                border.width: 1
                border.color: root.theme.selectionBorder
                z: 11
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    text: noteName
                    color: root.theme.accentText
                    font.pixelSize: 9
                    visible: parent.width >= 28
                }
            }
        }

        Canvas {
            id: detachedPreviewCanvas
            objectName: "detachedPreviewCanvas"
            anchors.fill: parent
            z: 12
            readonly property var notes: playback.detachedPreviewNotes || []
            readonly property int activeNoteCount: {
                if (!playback.playing) return 0
                var count = 0
                for (var i = 0; i < notes.length; ++i)
                    if (playback.positionSeconds >= notes[i].startTime
                            && playback.positionSeconds < notes[i].startTime + notes[i].duration) ++count
                return count
            }
            visible: notes.length > 0
            onNotesChanged: requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                for (var i = 0; i < notes.length; ++i) {
                    var n = notes[i]
                    var sounding = playback.playing && playback.positionSeconds >= n.startTime
                        && playback.positionSeconds < n.startTime + n.duration
                    ctx.fillStyle = sounding ? root.theme.notePlaybackBorder : root.theme.note
                    var x = root.keyWidth + (n.startTime - root.viewStartSeconds) * root.pixelsPerSecond
                    var y = (root.highestPitch - n.pitch) * root.rowHeight + 2
                    var w = Math.max(4, n.duration * root.pixelsPerSecond)
                    if (x + w < root.keyWidth || x > width || y < 0 || y > height) continue
                    ctx.fillRect(Math.max(root.keyWidth, x), y,
                        Math.max(0, Math.min(width, x + w) - Math.max(root.keyWidth, x)), root.rowHeight - 4)
                }
            }
            Connections {
                target: root
                function onViewStartSecondsChanged() { detachedPreviewCanvas.requestPaint() }
                function onHighestPitchChanged() { detachedPreviewCanvas.requestPaint() }
                function onPixelsPerSecondChanged() { detachedPreviewCanvas.requestPaint() }
            }
            Connections {
                target: root.theme
                function onNoteChanged() { detachedPreviewCanvas.requestPaint() }
                function onNotePlaybackBorderChanged() { detachedPreviewCanvas.requestPaint() }
            }
            Connections {
                target: playback
                function onPositionChanged() { detachedPreviewCanvas.requestPaint() }
                function onPlayingChanged() { detachedPreviewCanvas.requestPaint() }
            }
        }

        Text {
            objectName: "detachedPreviewStatus"
            visible: detachedPreviewCanvas.visible
            text: playback.playing ? "正在试听草稿 · 原工程未播放"
                : "试听草稿已暂停 · 原工程未播放"
            x: root.keyWidth + 12; y: Math.max(8, parent.height - height - 8); z: 16
            color: root.theme.textPrimary
            font.pixelSize: 12
        }
        Rectangle {
            id: playheadGlow
            objectName: "playheadGlow"
            visible: root.playheadVisible
            x: root.keyWidth + root.playheadScreenSeconds * root.pixelsPerSecond - 4
            y: 0
            width: 10
            height: canvas.height
            color: root.theme.playheadGlow
            z: 14
        }

        Rectangle {
            id: playhead
            objectName: "playhead"
            visible: root.playheadVisible
            x: root.keyWidth + root.playheadScreenSeconds * root.pixelsPerSecond
            y: 0
            width: 2
            height: canvas.height
            color: root.theme.playheadLine
            z: 15
            MouseArea {
                id: playheadDrag
                objectName: "playheadDragArea"
                anchors.left: parent.left
                anchors.leftMargin: -6
                anchors.right: parent.right
                anchors.rightMargin: -6
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                cursorShape: Qt.SizeHorCursor
                onPressed: mouse => {
                    root.claimEditorFocus()
                    var point = mapToItem(root, mouse.x, mouse.y)
                    root.beginScrub(point.x)
                }
                onPositionChanged: mouse => {
                    if (!pressed)
                        return
                    var point = mapToItem(root, mouse.x, mouse.y)
                    root.updateScrub(point.x)
                }
                onReleased: root.finishScrub()
                onCanceled: root.finishScrub()
            }
        }

        Rectangle {
            id: returnButton
            objectName: "returnToPlaybackButton"
            visible: !root.followPlayback
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.topMargin: 10
            anchors.rightMargin: 12
            width: returnLabel.implicitWidth + 22
            height: 30
            radius: 8
            color: root.theme.elevatedSurface
            border.color: root.theme.playhead
            z: 30
            Text {
                id: returnLabel
                anchors.centerIn: parent
                text: "返回播放位置"
                color: root.theme.playhead
                font.pixelSize: 12
            }
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.returnToPlayback()
            }
        }

        Rectangle {
            id: marquee
            objectName: "marqueeSelection"
            visible: false
            color: root.theme.accentSelection
            opacity: 0.15
            border.width: 1
            border.color: root.theme.accentSelection
            z: 20
        }
    }

    Connections {
        target: playback
        function onPositionChanged() {
            if (root.followPlayback && playback.playing)
                root.requestPlaybackFollow()
        }
        function onPlayingChanged() {
            // Flush the final transport position before pausing. A queued follow
            // tick must not clamp the paused view back to the editing boundary.
            followPlaybackTimer.stop()
            if (root.followPlayback)
                root.revealPlaybackPosition(true)
        }
    }

    Connections {
        target: root.selectionAudition
        function onPositionChanged() {
            if (root.followPlayback && root.selectionAudition.playing)
                root.requestPlaybackFollow()
        }
        function onPlayingChanged() {
            if (root.followPlayback && root.selectionAudition.playing)
                root.revealPlaybackPosition(true)
        }
    }

    Connections {
        target: editor
        function onLoadSucceeded(path) {
            root.focusPitchRange(editor.notePitchRange)
        }
        function onSelectionChanged() {
            root.revealPitchRangeIfNeeded(editor.selectedPitchRange)
        }
    }

    Connections {
        target: keyboard
        function onKeyNoteOn(pitch) { root.setKeyboardPitchActive(pitch, true) }
        function onKeyNoteOff(pitch) { root.setKeyboardPitchActive(pitch, false) }
        function onRecordTimeChanged() {
            if (keyboard.recording)
                root.revealPlaybackPosition(false)
        }
    }

    Connections {
        target: experience
        function onChanged() { root.syncKeyboardPreviewDefaults() }
    }

    MiniMap {
        id: timelineMiniMap
        visible: effectiveDisplayMode !== "hidden"
            && (!root.hideEmptyOverview || editor.noteModel.totalCount > 0)
        parent: root
        theme: root.theme
        totalSeconds: root.miniMapTotalSeconds
        viewStartSeconds: root.viewStartSeconds
        visibleSeconds: root.visibleSeconds
        playheadSeconds: root.cursorSeconds
        playheadVisible: root.playheadVisible
        lowestPitch: root.lowestPitch
        highestPitch: root.highestPitch
        minimumPitch: root.minimumPitch
        maximumPitch: root.maximumPitch
        dockToBottomRight: root.dockMiniMapToBottomRight
        focusMode: root.focusMode
        onNavigateRequested: function(seconds, pitch) {
            root.navigateFromMiniMap(seconds, pitch)
        }
    }
}
