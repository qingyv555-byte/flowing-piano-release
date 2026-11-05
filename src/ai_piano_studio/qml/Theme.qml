import QtQuick

QtObject {
    property bool nocturne: false
    property bool studio: false
    readonly property color imageSeed: studio && typeof studioAppearance !== "undefined"
        && studioAppearance !== null ? studioAppearance.themeSeed : "transparent"
    function studioColor(value) {
        return imageSeed.a > 0 ? studioAppearance.tintColor(value) : value
    }
    readonly property var noteSettings: studio && typeof studioAppearance !== "undefined"
        && studioAppearance !== null ? studioAppearance.values : ({})
    readonly property bool customNoteColorActive: studio && noteSettings.noteColorSource === "custom"
    readonly property color customNoteColor: noteSettings.customNoteColor || "#53a79b"
    readonly property string noteStyle: studio ? (noteSettings.noteStyle || "flat") : "flat"
    readonly property real noteCornerRadius: noteStyle === "rounded" ? 5 : 1
    function noteBodyColor(base, velocity) {
        base = Qt.tint(base, "transparent")
        return noteStyle === "velocity" ? mixColor(base, pianoRollBackground,
            0.42 * (1 - Math.max(1, Math.min(127, velocity)) / 127)) : base
    }
    function studioNoteColor(fallback) {
        if (customNoteColorActive)
            return customNoteColor
        var palettes = {luna: "#b49362", hack: "#9785af", bee: "#789b80", gale: "#c77d62", traveler: "#668ca9"}
        return studio && palettes[noteSettings.characterSkin] ? palettes[noteSettings.characterSkin] : fallback
    }
    function studioRoleColor(role, fallback) {
        // The user can switch between theme-following and independent exact
        // colors for every part. Manual values survive skin changes.
        if (!studio)
            return fallback
        if (!noteSettings.roleColorMode || noteSettings.roleColorMode === "uniform")
            return studioNoteColor(paper ? "#36775C" : "#89C1A6")
        var keys = {
            melody: "roleMelodyColor", accompaniment: "roleAccompanimentColor",
            bass: "roleBassColor", drums: "roleDrumsColor", other: "roleOtherColor"
        }
        if (noteSettings.roleColorMode === "custom")
            return noteSettings[keys[role] || "roleOtherColor"] || fallback
        var themeSeedAvailable = imageSeed.a > 0
        var paletteSeedAvailable = customNoteColorActive ||
            (noteSettings.characterSkin && noteSettings.characterSkin !== "none")
        if (!themeSeedAvailable && !paletteSeedAvailable)
            return fallback
        var seed = Qt.tint(customNoteColorActive ? customNoteColor :
            (themeSeedAvailable ? imageSeed : studioNoteColor(fallback)), "transparent")
        if (role === "melody" && customNoteColorActive)
            return seed
        var rotation = role === "melody" ? 0
            : role === "accompaniment" ? 0.32
            : role === "bass" ? 0.59 : role === "drums" ? 0.16 : 0.78
        var hue = seed.hslHue < 0 ? 0.53 : seed.hslHue
        var saturation = Math.max(0.52, Math.min(0.78, seed.hslSaturation))
        return Qt.hsla((hue + rotation) % 1, saturation, paper ? 0.40 : 0.70, 1)
    }
    readonly property bool paper: studio && typeof studioAppearance !== "undefined"
        && studioAppearance !== null && studioAppearance.values.themeId === "paper"
    readonly property bool reducedMotion: studio && studioAppearance !== null
        && studioAppearance.values.reducedMotion
    readonly property url gardenArtwork: Qt.resolvedUrl("assets/nocturne-garden.png")
    readonly property color sceneTint: studio ? appBackground : nocturne ? "#030604" : Qt.rgba(0.01, 0.04, 0.09, 1)
    readonly property color sceneGlow: studio ? accentSelection : nocturne ? "#78866B" : Qt.rgba(0.44, 0.28, 0.72, 1)
    function backgroundSourceFor(scene) {
        return nocturne && scene.backgroundPreset === "stage"
            && scene.customBackgroundPath.length === 0 ? gardenArtwork : scene.backgroundSource
    }
    // Stage and surfaces
    readonly property color appBackground: studio ? studioColor((paper ? "#E8EDF0" : "#111F28")) : nocturne ? "#030604" : "#07111E"
    readonly property color canvasBackground: studio ? studioColor((paper ? "#F4F7F7" : "#13232C")) : nocturne ? "#090F0C" : "#091827"
    readonly property color surface: studio ? studioColor((paper ? "#F8FAF9" : "#1A2B34")) : nocturne ? "#D40E1412" : "#0B1A2A"
    readonly property color elevatedSurface: studio ? studioColor((paper ? "#FFFFFF" : "#21333D")) : nocturne ? "#E8151D18" : "#0E2032"
    readonly property color surfaceHover: studio ? studioColor((paper ? "#DBE6E6" : "#2A4149")) : nocturne ? "#29382F" : "#122D43"
    readonly property color surfacePressed: studio ? studioColor((paper ? "#CBDADA" : "#15262F")) : nocturne ? "#0B110D" : "#081520"
    readonly property color surfaceSelected: studio ? studioColor((paper ? "#D4E3DF" : "#304B50")) : nocturne ? "#304034" : "#103744"
    readonly property color inputBackground: studio ? studioColor((paper ? "#F0F5F4" : "#15262F")) : nocturne ? "#111A15" : "#091827"
    readonly property color popupBackground: studio ? studioColor((paper ? "#F8FAF9" : "#21333D")) : nocturne ? "#151D18" : "#0E2032"
    readonly property color selectedBackground: studio ? studioColor((paper ? "#C3DAD4" : "#3A555B")) : nocturne ? "#334638" : "#12404D"
    readonly property color borderSubtle: studio ? studioColor((paper ? "#CAD7D8" : "#2C414A")) : nocturne ? "#404F5A54" : Qt.rgba(22 / 255, 50 / 255, 74 / 255, 0.78)
    readonly property color borderStrong: studio ? studioColor((paper ? "#9BAFB2" : "#506970")) : nocturne ? "#46564B" : "#16324A"
    readonly property color scrim: "#A6000000"

    // Shared custom Windows chrome. Move and resize loops stay native.
    readonly property color chromeBackground: appBackground
    readonly property color chromeBorder: borderStrong
    readonly property color chromeHover: surfaceHover

    // Text and state
    readonly property color textPrimary: studio ? studioColor((paper ? "#243E49" : "#E4EAE5")) : nocturne ? "#EEEEE8" : "#DCE8F5"
    readonly property color textSecondary: studio ? studioColor((paper ? "#465E68" : "#ADBFBD")) : nocturne ? "#B4BDB5" : "#8FA8BF"
    readonly property color textMuted: studio ? studioColor((paper ? "#465E68" : "#ADBFBD")) : nocturne ? "#A2AAA5" : "#8299AE"
    readonly property color textDisabled: studio ? studioColor((paper ? "#7C8B91" : "#788F95")) : nocturne ? "#68776C" : "#52697D"
    readonly property color textOnAccent: studio ? studioColor((paper ? "#FFFFFF" : "#182B32")) : nocturne ? "#142019" : "#061824"
    readonly property color textOnGold: "#261A05"
    readonly property color textOnDanger: "#2B0A11"
    readonly property color accentPrimary: studio ? studioColor((paper ? "#315F64" : "#D9E6DF")) : nocturne ? "#DCE5CF" : "#49D7FF"
    readonly property color accentPrimaryHover: studio ? studioColor((paper ? "#3B7075" : "#E5F0E8")) : nocturne ? "#EDF3E5" : "#7BE7FF"
    readonly property color accentPrimaryPressed: studio ? studioColor((paper ? "#244C52" : "#BACFC6")) : nocturne ? "#B9C8A9" : "#24B4D7"
    readonly property color accentMusic: studio ? (paper ? "#815F29" : "#D6BC8F") : "#F2B84B"
    readonly property color accentSelection: studio ? studioColor((paper ? "#315F64" : "#B9DAD0")) : nocturne ? "#E5F1D6" : "#7BE7FF"
    readonly property color info: studio ? (paper ? "#356778" : "#A9CAD1") : "#66D9F4"
    readonly property color success: studio ? (paper ? "#386D59" : "#A7CCB5") : "#3FD0A8"
    readonly property color warning: studio ? (paper ? "#815F29" : "#D6BC8F") : "#F2B84B"
    readonly property color danger: studio ? (paper ? "#98344A" : "#ECA9AD") : "#F06A7E"
    readonly property color record: studio ? (paper ? "#B44058" : "#EB93A3") : "#FF5F83"
    readonly property color focusRing: accentSelection
    readonly property color playingNote: studio ? (paper ? "#B67725" : "#F1C778") : "#9DEBFF"
    // A bright core in ink, a dark engraving in paper; independent of style.
    readonly property color feedbackCore: studio && paper ? "#8B571C" : "#FFE0A0"
    readonly property color noteColor1: notePitchHighMid
    readonly property color noteColor2: accentMusic
    readonly property color noteColor3: notePitchMid
    readonly property color noteColor4: notePitchHigh
    readonly property color noteColor5: notePitchLow

    // Music Layer roles are semantic. Skins may replace these values without
    // changing layer identity, editor behavior, or project data.
    readonly property color layerMelody: studio ? studioRoleColor("melody", paper ? "#8A5626" : "#F1BB78") : nocturne ? "#D0DEC0" : "#39C8FF"
    readonly property color layerAccompaniment: studio ? studioRoleColor("accompaniment", paper ? "#456D91" : "#8CB9D7") : nocturne ? "#BEB79B" : "#45D6A8"
    readonly property color layerBass: studio ? studioRoleColor("bass", paper ? "#36775C" : "#89C1A6") : nocturne ? "#89ACA4" : "#8B72FF"
    readonly property color layerDrums: studio ? studioRoleColor("drums", paper ? "#865D75" : "#CA9CAD") : nocturne ? "#B98B65" : "#F2A65A"
    readonly property color layerOther: studio ? studioRoleColor("other", paper ? "#687584" : "#AABAC3") : nocturne ? "#98AA91" : "#7F9BB4"
    readonly property color noteMono: studio ? studioNoteColor("#53a79b") : nocturne ? "#A8BE9B" : "#45D7DD"

    // Piano keys
    readonly property color keyWhite: studio ? studioColor((paper ? "#F4F6F2" : "#D9E3DE")) : nocturne ? "#C9C8B9" : "#C8D5E2"
    readonly property color keyWhiteEdge: studio ? studioColor((paper ? "#95A8A5" : "#8B9F9C")) : nocturne ? "#777C6F" : "#8FA8BF"
    readonly property color keyWhiteHighlight: studio ? studioColor((paper ? "#FFFFFF" : "#EEF4EE")) : nocturne ? "#E5E4D7" : "#DCE8F5"
    readonly property color keyBlack: studio ? studioColor((paper ? "#24363E" : "#1A2B34")) : nocturne ? "#111713" : "#152230"
    readonly property color keyBlackEdge: studio ? studioColor((paper ? "#536F73" : "#506970")) : nocturne ? "#48534A" : "#30465A"
    readonly property color keyBlackHighlight: studio ? studioColor((paper ? "#72908E" : "#829F9A")) : nocturne ? "#657267" : "#415A70"

    // Piano Roll visual language: one cool, continuous pitch scale.
    readonly property color pianoRollBackground: canvasBackground
    readonly property color gridLineMinor: studio ? Qt.rgba(0.48, 0.65, 0.64, paper ? 0.14 : 0.08) : nocturne ? Qt.rgba(151 / 255, 169 / 255, 148 / 255, 0.07) : Qt.rgba(140 / 255, 170 / 255, 210 / 255, 0.08)
    readonly property color gridLineMajor: studio ? Qt.rgba(0.48, 0.65, 0.64, paper ? 0.25 : 0.18) : nocturne ? Qt.rgba(184 / 255, 196 / 255, 174 / 255, 0.14) : Qt.rgba(160 / 255, 195 / 255, 235 / 255, 0.16)
    readonly property color barLine: studio ? Qt.rgba(0.55, 0.72, 0.68, 0.32) : nocturne ? Qt.rgba(220 / 255, 229 / 255, 207 / 255, 0.18) : Qt.rgba(120 / 255, 220 / 255, 255 / 255, 0.18)
    readonly property color playheadLine: studio ? (paper ? "#315F64" : "#F1C778") : "#F2B84B"
    readonly property color playheadGlow: studio ? Qt.alpha(playheadLine, paper ? 0.07 : 0.12) : Qt.rgba(242 / 255, 184 / 255, 75 / 255, 0.24)
    readonly property color middleCHighlight: studio ? Qt.alpha(accentSelection, 0.16) : nocturne ? Qt.rgba(176 / 255, 193 / 255, 166 / 255, 0.16) : Qt.rgba(120 / 255, 180 / 255, 255 / 255, 0.20)

    readonly property color notePitchLow: studio ? studioNoteColor("#598ba8") : nocturne ? "#738B82" : "#7B5CFF"
    readonly property color notePitchLowMid: studio ? studioNoteColor("#4c99a5") : nocturne ? "#879D8C" : "#4B86FF"
    readonly property color notePitchMid: studio ? studioNoteColor("#53a79b") : nocturne ? "#A8BE9B" : "#34CFFF"
    readonly property color notePitchHighMid: studio ? studioNoteColor("#8aab70") : nocturne ? "#C0C7A9" : "#42E7C9"
    readonly property color notePitchHigh: studio ? studioNoteColor("#b39a58") : nocturne ? "#D8D6B9" : "#7AF5D0"
    readonly property color noteLabelLight: studio ? "#F4F8F3" : Qt.rgba(220 / 255, 232 / 255, 245 / 255, 0.92)
    readonly property color noteLabelDark: studio ? "#182B32" : Qt.rgba(12 / 255, 25 / 255, 36 / 255, 0.85)
    readonly property color noteHoverOverlay: Qt.rgba(255 / 255, 255 / 255, 255 / 255, 0.06)
    readonly property color noteHoverGlow: studio ? Qt.alpha(accentSelection, 0.18) : nocturne ? Qt.rgba(220 / 255, 229 / 255, 207 / 255, 0.16) : Qt.rgba(120 / 255, 235 / 255, 255 / 255, 0.18)
    readonly property color noteSelectedBorder: studio ? studioColor((paper ? "#244F54" : "#F0F5EF")) : nocturne ? "#F0F5E8" : "#A8F1FF"
    readonly property color noteSelectedGlow: studio ? Qt.alpha(accentSelection, 0.30) : nocturne ? Qt.rgba(220 / 255, 229 / 255, 207 / 255, 0.30) : Qt.rgba(132 / 255, 241 / 255, 255 / 255, 0.34)
    readonly property color notePlaybackGlow: studio ? Qt.alpha(playheadLine, 0.30) : Qt.rgba(87 / 255, 230 / 255, 255 / 255, 0.35)
    readonly property color notePlaybackBorder: studio ? (paper ? "#315F64" : "#F1C778") : "#9DEBFF"
    readonly property real notePreviewAlpha: 0.72
    readonly property real noteDragAlpha: 0.85

    // The same pitch mapping is reused by the Mini-map so the overview remains legible.
    readonly property color minimapBackground: studio ? studioColor((paper ? "#E3ECEA" : "#1A2B34")) : nocturne ? Qt.rgba(9 / 255, 15 / 255, 12 / 255, 0.76) : Qt.rgba(10 / 255, 20 / 255, 32 / 255, 0.72)
    readonly property color minimapInnerBackground: studio ? studioColor((paper ? "#D8E5E1" : "#20353D")) : nocturne ? Qt.rgba(17 / 255, 26 / 255, 21 / 255, 0.62) : Qt.rgba(14 / 255, 26 / 255, 40 / 255, 0.58)
    readonly property color minimapBorder: studio ? studioColor((paper ? "#9AB3AE" : "#4E6A6D")) : nocturne ? Qt.rgba(180 / 255, 197 / 255, 173 / 255, 0.16) : Qt.rgba(145 / 255, 210 / 255, 255 / 255, 0.16)
    readonly property color minimapBorderStrong: studio ? Qt.alpha(accentSelection, 0.28) : nocturne ? Qt.rgba(220 / 255, 229 / 255, 207 / 255, 0.26) : Qt.rgba(145 / 255, 210 / 255, 255 / 255, 0.28)
    readonly property color minimapShadow: Qt.rgba(0, 0, 0, 0.35)
    readonly property color minimapViewportBorder: studio ? accentSelection : nocturne ? "#DCE5CF" : Qt.rgba(210 / 255, 248 / 255, 255 / 255, 0.92)
    readonly property color minimapViewportFill: studio ? Qt.alpha(accentSelection, 0.06) : nocturne ? Qt.rgba(220 / 255, 229 / 255, 207 / 255, 0.06) : Qt.rgba(135 / 255, 235 / 255, 255 / 255, 0.06)
    readonly property color minimapTitleText: studio ? studioColor((paper ? "#243E49" : "#E4EAE5")) : nocturne ? Qt.rgba(238 / 255, 238 / 255, 232 / 255, 0.88) : Qt.rgba(220 / 255, 232 / 255, 245 / 255, 0.88)
    readonly property color minimapMetaText: studio ? studioColor((paper ? "#465E68" : "#ADBFBD")) : nocturne ? Qt.rgba(180 / 255, 189 / 255, 181 / 255, 0.72) : Qt.rgba(170 / 255, 190 / 255, 210 / 255, 0.72)

    function mixColor(first, second, amount, alpha) {
        var t = Math.max(0, Math.min(1, Number(amount)))
        return Qt.rgba(
            first.r + (second.r - first.r) * t,
            first.g + (second.g - first.g) * t,
            first.b + (second.b - first.b) * t,
            alpha === undefined ? 1 : alpha
        )
    }

    function layerRoleColor(role) {
        switch (String(role || "").toLowerCase()) {
        case "melody":
            return layerMelody
        case "accompaniment":
            return layerAccompaniment
        case "bass":
            return layerBass
        case "drums":
            return layerDrums
        default:
            return layerOther
        }
    }

    function layerRoleFill(role, alpha) {
        var base = layerRoleColor(role)
        var opacity = alpha === undefined ? 0.18 : Math.max(0, Math.min(1, Number(alpha)))
        return Qt.rgba(base.r, base.g, base.b, opacity)
    }

    function layerRoleMuted(role) {
        return mixColor(layerRoleColor(role), textDisabled, 0.58)
    }

    function notePitchColor(pitch, minimumPitch, maximumPitch) {
        var low = Number(minimumPitch)
        var high = Number(maximumPitch)
        var span = Math.max(1, high - low)
        var position = Math.max(0, Math.min(1, (Number(pitch) - low) / span))
        if (position <= 0.25)
            return mixColor(notePitchLow, notePitchLowMid, position / 0.25)
        if (position <= 0.5)
            return mixColor(notePitchLowMid, notePitchMid, (position - 0.25) / 0.25)
        if (position <= 0.75)
            return mixColor(notePitchMid, notePitchHighMid, (position - 0.5) / 0.25)
        return mixColor(notePitchHighMid, notePitchHigh, (position - 0.75) / 0.25)
    }

    function notePitchHoverColor(pitch, minimumPitch, maximumPitch, hovered) {
        var base = notePitchColor(pitch, minimumPitch, maximumPitch)
        return hovered ? mixColor(base, noteLabelLight, 0.10) : base
    }

    function notePitchBorderColor(pitch, minimumPitch, maximumPitch) {
        return mixColor(notePitchColor(pitch, minimumPitch, maximumPitch), noteLabelLight, 0.28)
    }

    function noteLabelColorForPitch(pitch, minimumPitch, maximumPitch) {
        return textOnColor(notePitchColor(pitch, minimumPitch, maximumPitch))
    }

    function colorLuminance(value) {
        value = Qt.tint(value, "transparent")
        function linear(channel) {
            return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(value.r) + 0.7152 * linear(value.g) + 0.0722 * linear(value.b)
    }

    function colorContrast(first, second) {
        var a = colorLuminance(first), b = colorLuminance(second)
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
    }

    function textOnColor(fill) {
        var dark = colorContrast(noteLabelDark, fill), light = colorContrast(noteLabelLight, fill)
        if (Math.max(dark, light) >= 4.5)
            return dark >= light ? noteLabelDark : noteLabelLight
        return colorLuminance(fill) > 0.179 ? Qt.rgba(0, 0, 0, 1) : Qt.rgba(1, 1, 1, 1)
    }

    function minimapNoteColor(pitch, minimumPitch, maximumPitch) {
        if (studio)
            return notePitchColor(pitch, minimumPitch, maximumPitch)
        return mixColor(
            notePitchColor(pitch, minimumPitch, maximumPitch),
            pianoRollBackground,
            0.08,
            0.92
        )
    }

    // Typography
    readonly property string fontUi: "Microsoft YaHei"
    readonly property string fontFallback: "Microsoft YaHei"
    readonly property string fontSymbol: "Segoe UI Symbol"
    readonly property string numberFont: "Consolas"
    readonly property int typeDisplay: 40
    readonly property int typePageTitle: 24
    readonly property int typeSection: 18
    readonly property int typePanelTitle: 15
    readonly property int typeBody: 14
    readonly property int typeBodyStrong: 14
    readonly property int typeSmall: 12
    readonly property int typeCaption: studio ? 12 : 11
    readonly property int lineDisplay: 52
    readonly property int linePageTitle: 34
    readonly property int lineSection: 27
    readonly property int linePanelTitle: 24
    readonly property int lineBody: 22
    readonly property int lineBodyStrong: 22
    readonly property int lineSmall: 18
    readonly property int lineCaption: 16
    readonly property int weightRegular: Font.Normal
    readonly property int weightSemibold: Font.DemiBold
    readonly property int weightBold: Font.Bold

    // Stable dimensions
    readonly property int controlHeight: 36
    readonly property int primaryControlHeight: 48
    readonly property int iconButtonSize: 36
    readonly property int iconSmall: 16
    readonly property int iconMedium: 20
    readonly property int iconLarge: 24
    readonly property int topBarHeight: studio ? 64 : 52
    readonly property int windowChromeHeight: 40
    readonly property int transportHeight: 64
    readonly property int statusBarHeight: 28
    readonly property int rulerHeight: 30
    readonly property int keyWidth: 96
    readonly property int inspectorWidth: 304
    readonly property int inspectorCollapsedWidth: 44
    readonly property int projectRailWidth: 224
    readonly property int projectRailCollapsedWidth: 52
    readonly property int arrangementDefaultHeight: 240
    readonly property int arrangementCompactHeight: 132
    readonly property int musicLayerRowHeight: 34
    readonly property int minimumPianoRollWidth: 640
    readonly property int panelEdgeAffordanceWidth: 36
    readonly property int contentMaxWidth: 1120
    readonly property int miniMapHeight: 26
    // Scroll content must end before the thumb starts; the thumb itself is
    // inset from the outer edge so it never sits on top of labels or artwork.
    readonly property int scrollbarWidth: 8
    readonly property int scrollbarGutter: 20
    readonly property int scrollbarRightInset: 2

    // Spacing and radius
    readonly property int space1: 4
    readonly property int space2: 8
    readonly property int space3: 12
    readonly property int space4: 16
    readonly property int space5: 20
    readonly property int space6: 24
    readonly property int space7: 32
    readonly property int space8: 40
    readonly property int space9: 48
    readonly property int space10: 64
    readonly property int radiusS: 4
    readonly property int radiusM: studio ? 10 : 6
    readonly property int radiusL: 8

    // Motion (ms)
    readonly property int motionPress: reducedMotion ? 0 : 90
    readonly property int motionHover: reducedMotion ? 0 : 140
    readonly property int motionState: reducedMotion ? 0 : 180
    readonly property int motionPanel: reducedMotion ? 0 : 220
    readonly property int motionModal: reducedMotion ? 0 : 260
    readonly property int motionToastIn: reducedMotion ? 0 : 200
    readonly property int motionToastOut: reducedMotion ? 0 : 160
    readonly property int motionAmbient: 12000

    // Existing scene, grid, feedback, and key-hover tokens.
    readonly property color sceneProtection: studio ? studioColor((paper ? "#E8EDF0" : "#111F28")) : "#8F0A0F1C"
    readonly property color sceneDream: studio ? "#A9CAD1" : "#A9D9FF"
    readonly property color sceneCosmic: studio ? "#759D9A" : "#2497C7"
    readonly property color sceneSunset: studio ? "#D6BC8F" : "#D6875D"
    readonly property color gridMinor: gridLineMinor
    readonly property color gridBeat: gridLineMajor
    readonly property color gridBar: barLine
    readonly property color feedbackSurface: studio ? studioColor((paper ? "#D5E5DF" : "#2A4347")) : "#F21B2748"
    readonly property color feedbackBorder: studio ? studioColor((paper ? "#315F64" : "#D9E6DF")) : "#5CE1A1"
    readonly property color feedbackPitch: studio ? studioColor((paper ? "#315F64" : "#D9E6DF")) : "#82F5D1"
    readonly property color keyWhiteHover: studio ? studioColor((paper ? "#DEEBE4" : "#ECF5EE")) : nocturne ? "#E5E4D7" : "#DCE8F5"
    readonly property color keyBlackHover: studio ? studioColor((paper ? "#4E7774" : "#759A92")) : nocturne ? "#657267" : "#486278"

    // Compatibility mappings
    readonly property color accentSecondary: accentSelection
    readonly property color accentDeep: surfaceSelected
    readonly property color playhead: playheadLine
    readonly property color error: danger
    readonly property int titleBarHeight: topBarHeight
    readonly property int buttonSize: iconButtonSize
    readonly property int playButtonSize: primaryControlHeight
    readonly property int durHover: motionHover
    readonly property int durPanel: motionPanel
    readonly property int durPulse: motionState
    readonly property int durKeyRelease: motionState
    readonly property int durFeedback: motionState
    readonly property int durToastIn: motionToastIn
    readonly property int durToastOut: motionToastOut
    readonly property int durAmbient: motionAmbient
    readonly property int commandHeight: topBarHeight

    readonly property color canvas: appBackground
    readonly property color toolbar: surface
    readonly property color panel: elevatedSurface
    readonly property color status: surface
    readonly property color field: elevatedSurface
    readonly property color control: surfaceHover
    readonly property color border: borderSubtle
    readonly property color gridStrong: gridBar
    readonly property color gridSoft: gridBeat
    readonly property color keyLight: keyWhite
    readonly property color keyDark: keyBlack
    readonly property color text: textPrimary
    readonly property color muted: textMuted
    readonly property color accent: accentPrimary
    readonly property color accentText: textOnAccent
    readonly property color note: notePitchMid
    readonly property color noteBorder: notePitchBorderColor(60, 36, 84)
    readonly property color selectionBorder: noteSelectedBorder
}
