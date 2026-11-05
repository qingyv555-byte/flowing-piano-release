import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    required property var theme
    readonly property var summary: editor.selectionSummary
    readonly property var compositeSummary: editor.compositeSelectionSummary
    readonly property var editorBridge: editor
    readonly property var experienceBridge: experience
    readonly property bool hasCompositeSelection: compositeSummary !== null
        && compositeSummary !== undefined
        && compositeSummary.groupId !== undefined
        && Number(compositeSummary.count) > 1
    property string experienceMode: experience.mode
    readonly property bool isAdvanced: advancedShown
    property bool advancedShown: false
    property string panelMode: "expanded"
    // `collapsed` is retained as a compatibility property for existing
    // callers; the visible state is derived from the two panel modes.
    property bool collapsed: panelMode === "collapsed"
    property bool focusMode: false
    readonly property string effectivePanelMode: focusMode ? "collapsed" : panelMode
    readonly property bool panelExpanded: effectivePanelMode === "expanded"
    property string inspectorMode: "selection"
    // Music-layer selection is wired in Wave 6. Keeping the target kind
    // explicit here gives the Inspector one stable context contract now.
    property string selectionTargetKind: "note"
    readonly property string contextKind: root.inspectorMode !== "selection"
        ? root.inspectorMode
        : (root.selectionTargetKind === "layer"
            ? "layer"
            : (root.summary.count === 0
                ? "empty" : (root.summary.count === 1 ? "note" : "multi")))
    readonly property real panelWidth: effectivePanelMode === "expanded"
        ? theme.inspectorWidth : 0
    property bool sectionStateReady: false
    readonly property real secondsPerBeat: 60.0 / editor.bpm * (4.0 / editor.beatUnit)

    signal panelModeRequested(string panelMode)

    color: theme.panel
    width: panelWidth
    Layout.preferredWidth: panelWidth
    Layout.minimumWidth: panelWidth
    Layout.maximumWidth: panelWidth
    border.width: 0
    clip: true

    onPanelModeChanged: {
        if (panelMode === "narrow") {
            panelMode = "collapsed"
            return
        }
        var shouldCollapse = panelMode === "collapsed"
        if (collapsed !== shouldCollapse)
            collapsed = shouldCollapse
    }

    onCollapsedChanged: {
        if (collapsed && panelMode !== "collapsed") {
            panelMode = "collapsed"
            panelModeRequested("collapsed")
        } else if (!collapsed && panelMode === "collapsed") {
            panelMode = "expanded"
            panelModeRequested("expanded")
        }
    }

    function cyclePanelMode() {
        if (root.focusMode)
            return
        var nextMode = root.panelMode === "expanded" ? "collapsed" : "expanded"
        root.panelModeRequested(nextMode)
    }

    function showLayerContext() {
        root.inspectorMode = "selection"
        root.selectionTargetKind = "layer"
    }

    function showNoteContext() {
        root.inspectorMode = "selection"
        root.selectionTargetKind = "note"
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: root.theme.border
    }

    AppIconButton {
        id: inspectorCollapseButton
        objectName: "inspectorCollapseButton"
        theme: root.theme
        tooltip: root.effectivePanelMode === "collapsed"
            ? "展开音符属性" : "收起属性栏"
        accessibleName: tooltip
        text: root.effectivePanelMode === "collapsed" ? "›" : "‹"
        x: parent.width - width - 8
        y: 8
        onClicked: root.cyclePanelMode()
    }

    function valueText(name, decimals) {
        var value = summary[name]
        if (value === null || value === undefined)
            return ""
        return decimals === undefined ? String(value) : Number(value).toFixed(decimals)
    }

    function beatText(seconds, offset) {
        if (seconds === null || seconds === undefined)
            return ""
        return (Number(seconds) / secondsPerBeat + offset).toFixed(2)
    }

    function tickText(seconds) {
        if (seconds === null || seconds === undefined)
            return ""
        return String(Math.round(Number(seconds) / secondsPerBeat * 480))
    }

    function velocityLevel(velocity) {
        if (velocity === null || velocity === undefined)
            return ""
        if (velocity < 45)
            return "轻柔"
        if (velocity < 90)
            return "自然"
        return "有力"
    }

    function velocityDescription(velocity) {
        if (velocity === null || velocity === undefined)
            return "混合力度"
        return velocityLevel(velocity) + " · " + Math.round(velocity) + " / 127"
    }

    function applyBeginnerVelocity(value) {
        if (root.summary.count > 0)
            editor.setSelectedProperty("velocity", Math.round(value))
    }

    function scrollInspectorBy(delta) {
        var maximumContentY = Math.max(
            0, inspectorBodyFlickable.contentHeight - inspectorBodyFlickable.height)
        inspectorBodyFlickable.contentY = Math.max(
            0,
            Math.min(maximumContentY, inspectorBodyFlickable.contentY - delta))
    }

    function scrollByWheel(delta) {
        if (!root.panelExpanded || !Number.isFinite(Number(delta)))
            return
        scrollInspectorBy(Number(delta))
    }

    component InspectorComboBox: ComboBox {
        id: control
        implicitHeight: 36
        leftPadding: 10
        rightPadding: 30

        contentItem: Text {
            text: control.displayText
            color: control.enabled ? root.theme.textPrimary : root.theme.textMuted
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSmall
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: root.theme.radiusM
            color: control.hovered
                ? root.theme.surfaceHover : root.theme.inputBackground
            border.width: control.activeFocus ? 2 : 1
            border.color: control.activeFocus
                ? root.theme.focusRing : root.theme.borderStrong
        }
        indicator: Text {
            x: control.width - width - 10
            y: (control.height - height) / 2
            text: "⌄"
            color: root.theme.textSecondary
            font.pixelSize: 14
        }
        delegate: ItemDelegate {
            width: control.width
            contentItem: Text {
                text: modelData
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.pixelSize: root.theme.typeSmall
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: parent.highlighted
                    ? root.theme.surfaceHover : root.theme.popupBackground
            }
        }
        popup: Popup {
            y: control.height + 4
            width: control.width
            implicitHeight: Math.min(contentItem.implicitHeight + 2, 240)
            padding: 1
            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: control.popup.visible ? control.delegateModel : null
                currentIndex: control.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator {}
            }
            background: Rectangle {
                color: root.theme.popupBackground
                border.width: 1
                border.color: root.theme.borderStrong
                radius: root.theme.radiusM
            }
        }
    }

    Component.onCompleted: root.sectionStateReady = true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.space4
        spacing: root.theme.space3
        visible: root.effectivePanelMode !== "collapsed"

        Item {
            id: inspectorHeader
            objectName: "inspectorHeader"
            Layout.fillWidth: true
            implicitHeight: root.panelExpanded
                ? root.theme.controlHeight + root.theme.space2 + 32
                : root.theme.controlHeight

            Label {
                id: inspectorContextHeading
                objectName: "inspectorContextHeading"
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.topMargin: 2
                text: root.inspectorMode === "selection"
                    ? (root.contextKind === "note"
                        ? "单个音符"
                        : (root.contextKind === "multi"
                            ? "多个音符"
                            : (root.contextKind === "layer" ? "音乐层" : "属性")))
                    : (root.inspectorMode === "defaults" ? "新音符设置" : "工程概览")
                color: root.theme.textPrimary
                font.family: root.theme.fontUi
                font.weight: root.theme.weightSemibold
                font.pixelSize: root.theme.typePanelTitle
            }

            SegmentedControl {
                id: inspectorModeSwitch
                objectName: "inspectorModeSwitch"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 32
                theme: root.theme
                model: ["所选音符", "新音符", "工程"]
                currentIndex: root.inspectorMode === "selection" ? 0
                    : (root.inspectorMode === "defaults" ? 1 : 2)
                visible: root.panelExpanded
                onActivated: index => root.inspectorMode = [
                    "selection", "defaults", "project"
                ][index]
                Accessible.name: "属性查看模式"
            }
        }

        Flickable {
            id: inspectorBodyFlickable
            objectName: "inspectorBodyFlickable"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            clip: true
            visible: root.panelExpanded
            contentWidth: Math.max(0, width - root.theme.scrollbarGutter)
            contentHeight: inspectorContentColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            flickableDirection: Flickable.VerticalFlick

            function clampContentY() {
                var maximumContentY = Math.max(0, contentHeight - height)
                contentY = Math.max(0, Math.min(maximumContentY, contentY))
            }

            onContentHeightChanged: Qt.callLater(clampContentY)
            onHeightChanged: Qt.callLater(clampContentY)
            onContentYChanged: {
                var maximumContentY = Math.max(0, contentHeight - height)
                if (contentY < 0 || contentY > maximumContentY)
                    clampContentY()
            }

            ColumnLayout {
                id: inspectorContentColumn
                objectName: "inspectorContentColumn"
                x: 3
                width: Math.max(0, inspectorBodyFlickable.contentWidth - 6)
                spacing: root.theme.space3

                Label {
                    id: inspectorEmptyState
                    objectName: "inspectorEmptyState"
                    Layout.fillWidth: true
                    Layout.topMargin: root.theme.space2
                    visible: root.inspectorMode === "selection"
                        && root.contextKind === "empty"
                    text: "选择音符或音乐层以编辑参数。"
                    color: root.theme.textSecondary
                    font.family: root.theme.fontUi
                    font.pixelSize: root.theme.typeBody
                    wrapMode: Text.Wrap
                }

                PropertySection {
                    id: newNoteDefaultsSection
                    objectName: "newNoteDefaultsSection"
                    theme: root.theme
                    title: "快捷设置"
                    subtitle: ""
                    visible: root.inspectorMode === "defaults"
                    expanded: experience.inspectorSectionExpanded("new-note-defaults", true)
                    onExpandedChanged: {
                        if (root.sectionStateReady)
                            experience.setInspectorSectionExpanded("new-note-defaults", expanded)
                    }

                    ColumnLayout {
                        id: newNoteDefaults
                        objectName: "newNoteDefaults"
                        Layout.fillWidth: true
                        spacing: root.theme.space2

                        Label {
                            objectName: "newNoteDefaultsTitle"
                            visible: false
                            text: "新建音符默认值"
                            color: root.theme.textPrimary
                            font.family: root.theme.fontUi
                            font.weight: root.theme.weightSemibold
                            font.pixelSize: root.theme.typeBodyStrong
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: root.theme.space2

                            AppField {
                                id: defaultNoteLengthField
                                objectName: "defaultNoteLengthField"
                                theme: root.theme
                                label: "长度（拍）"
                                Layout.preferredWidth: 70
                                Layout.minimumWidth: 70
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: Number(experience.newNoteLengthBeats).toFixed(2)
                                validator: DoubleValidator { bottom: 0.25; top: 4.0; decimals: 2 }
                                onEditingFinished: {
                                    var parsed = Number(text)
                                    if (Number.isFinite(parsed))
                                        experience.setNewNoteLengthBeats(parsed)
                                }
                            }

                            Item { Layout.fillWidth: true }

                            Repeater {
                                model: [
                                    { label: "½", value: 0.5 },
                                    { label: "1", value: 1.0 },
                                    { label: "2", value: 2.0 }
                                ]
                                delegate: AppButton {
                                    required property var modelData
                                    theme: root.theme
                                    text: modelData.label
                                    accessibleName: modelData.value + " 拍"
                                    implicitWidth: 36
                                    implicitHeight: 36
                                    Layout.preferredWidth: 36
                                    Layout.preferredHeight: 36
                                    onClicked: experience.setNewNoteLengthBeats(modelData.value)
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: root.theme.space2

                            AppField {
                                id: pianoPreviewLengthField
                                objectName: "pianoPreviewLengthField"
                                theme: root.theme
                                label: "琴键试听（拍）"
                                Layout.preferredWidth: 70
                                Layout.minimumWidth: 70
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: Number(experience.pianoPreviewLengthBeats).toFixed(2)
                                validator: DoubleValidator { bottom: 0.25; top: 4.0; decimals: 2 }
                                onEditingFinished: {
                                    var parsed = Number(text)
                                    if (Number.isFinite(parsed))
                                        experience.setPianoPreviewLengthBeats(parsed)
                                }
                            }

                            Item { Layout.fillWidth: true }

                            Repeater {
                                model: [
                                    { label: "½", value: 0.5 },
                                    { label: "1", value: 1.0 },
                                    { label: "2", value: 2.0 }
                                ]
                                delegate: AppButton {
                                    required property var modelData
                                    theme: root.theme
                                    text: modelData.label
                                    accessibleName: modelData.value + " 拍试听"
                                    implicitWidth: 36
                                    implicitHeight: 36
                                    Layout.preferredWidth: 36
                                    Layout.preferredHeight: 36
                                    onClicked: experience.setPianoPreviewLengthBeats(modelData.value)
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: root.theme.space2

                            Label {
                                text: "轻柔"
                                color: root.theme.textMuted
                                font.pixelSize: root.theme.typeCaption
                            }
                            StyledSlider {
                                id: defaultNoteVelocitySlider
                                objectName: "defaultNoteVelocitySlider"
                                theme: root.theme
                                from: 1
                                to: 127
                                stepSize: 1
                                modelValue: experience.newNoteVelocity
                                accessibleName: "新建音符默认力度"
                                Layout.fillWidth: true
                                onValueCommitted: value => experience.setNewNoteVelocity(Math.round(value))
                            }
                            Label {
                                text: "有力"
                                color: root.theme.textMuted
                                font.pixelSize: root.theme.typeCaption
                            }
                        }

                        Label {
                            text: "默认力度：" + Math.round(experience.newNoteVelocity) + " / 127"
                            color: root.theme.textSecondary
                            font.pixelSize: root.theme.typeCaption
                            Layout.fillWidth: true
                        }
                    }
                }

                PropertySection {
                    id: selectionSection
                    objectName: "selectionSection"
                    theme: root.theme
                    title: "基础参数"
                    subtitle: root.summary.count > 0 ? "" : "未选择音符"
                    visible: root.inspectorMode === "selection"
                        && (root.contextKind === "note" || root.contextKind === "multi")
                        && (!root.hasCompositeSelection
                            || !compositeSelectionSection.overallMode)
                    badge: root.summary.count > 0 ? String(root.summary.count) : ""
                    expanded: experience.inspectorSectionExpanded(
                        "selection", true)
                    onExpandedChanged: {
                        if (root.sectionStateReady)
                            experience.setInspectorSectionExpanded("selection", expanded)
                    }

                    ColumnLayout {
                        id: selectedNotes
                        objectName: "selectedNotes"
                        Layout.fillWidth: true
                        spacing: root.theme.space2

                        Label {
                            objectName: "selectedNotesTitle"
                            visible: false
                            text: "当前选择"
                            color: root.theme.textPrimary
                            font.family: root.theme.fontUi
                            font.weight: root.theme.weightSemibold
                            font.pixelSize: root.theme.typeBodyStrong
                        }

                        Label {
                            visible: root.summary.count === 0
                            text: "选择音符以编辑属性。"
                            color: root.theme.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }

                        ColumnLayout {
                            visible: root.summary.count > 0
                            Layout.fillWidth: true
                            spacing: root.theme.space2

                            RowLayout {
                                visible: !root.isAdvanced
                                Layout.fillWidth: true
                                spacing: root.theme.space2
                                Label {
                                    text: "音符名称"
                                    color: root.theme.textMuted
                                    font.pixelSize: root.theme.typeSmall
                                }
                                Label {
                                    text: root.summary.noteNameMixed
                                        ? "混合" : root.valueText("noteName")
                                    color: root.theme.textPrimary
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignRight
                                }
                            }

                            AppField {
                                id: pitchField
                                objectName: "pitchField"
                                theme: root.theme
                                label: "音高 · 音符高低"
                                Layout.fillWidth: true
                                visible: root.isAdvanced
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: root.summary.pitchMixed ? "" : root.valueText("pitch")
                                placeholderText: root.summary.pitchMixed ? "混合" : ""
                                validator: IntValidator { bottom: 0; top: 127 }
                                onEditingFinished: {
                                    var parsed = Number(text)
                                    if (Number.isInteger(parsed))
                                        editor.setSelectedProperty("pitch", parsed)
                                }
                            }

                            AppField {
                                id: startTimeField
                                objectName: "startTimeField"
                                theme: root.theme
                                label: root.isAdvanced
                                    ? "位置 · 时间位置（Tick）"
                                    : "从第几拍开始"
                                Layout.fillWidth: true
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: root.summary.startTimeMixed
                                    ? "" : (root.isAdvanced
                                        ? root.tickText(root.summary.startTime)
                                        : root.beatText(root.summary.startTime, 1))
                                placeholderText: root.summary.startTimeMixed
                                    ? "多个值" : (root.isAdvanced ? "0" : "第 1 拍")
                                validator: DoubleValidator {
                                    bottom: root.isAdvanced ? 0 : 1
                                    decimals: 3
                                }
                                onEditingFinished: {
                                    var parsed = Number(text)
                                    if (Number.isFinite(parsed))
                                        editor.setSelectedProperty(
                                            "start_time",
                                            root.isAdvanced
                                                ? parsed / 480 * root.secondsPerBeat
                                                : (parsed - 1) * root.secondsPerBeat)
                                }
                            }

                            AppField {
                                id: durationField
                                objectName: "durationField"
                                theme: root.theme
                                label: root.isAdvanced
                                    ? "长度 · 持续时长（Duration）"
                                    : "持续几拍"
                                Layout.fillWidth: true
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: root.summary.durationMixed
                                    ? "" : (root.isAdvanced
                                        ? root.valueText("duration", 3)
                                        : root.beatText(root.summary.duration, 0))
                                placeholderText: root.summary.durationMixed
                                    ? "多个值" : (root.isAdvanced ? "秒" : "1 拍")
                                validator: DoubleValidator {
                                    bottom: 0.03125
                                    decimals: 3
                                }
                                onEditingFinished: {
                                    var parsed = Number(text)
                                    if (Number.isFinite(parsed))
                                        editor.setSelectedProperty(
                                            "duration",
                                            root.isAdvanced
                                                ? parsed
                                                : parsed * root.secondsPerBeat)
                                }
                            }

                            AppField {
                                id: velocityField
                                objectName: "velocityField"
                                theme: root.theme
                                label: "力度 · 音符强弱"
                                Layout.fillWidth: true
                                visible: root.isAdvanced
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: root.summary.velocityMixed
                                    ? "" : root.valueText("velocity")
                                placeholderText: root.summary.velocityMixed ? "混合" : "0 - 127"
                                validator: IntValidator { bottom: 0; top: 127 }
                                onEditingFinished: {
                                    var parsed = Number(text)
                                    if (Number.isInteger(parsed))
                                        editor.setSelectedProperty("velocity", parsed)
                                }
                            }

                            ColumnLayout {
                                visible: !root.isAdvanced
                                Layout.fillWidth: true
                                spacing: root.theme.space1

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: root.theme.space2
                                    Label {
                                        text: "轻柔"
                                        color: root.theme.textMuted
                                        font.pixelSize: root.theme.typeSmall
                                    }
                                    StyledSlider {
                                        id: beginnerVelocitySlider
                                        objectName: "beginnerVelocitySlider"
                                        theme: root.theme
                                        from: 1
                                        to: 127
                                        stepSize: 1
                                        modelValue: root.summary.velocity === null
                                            || root.summary.velocity === undefined
                                            ? 75 : root.summary.velocity
                                        accessibleName: "音符力度"
                                        Layout.fillWidth: true
                                        onValueCommitted: value => root.applyBeginnerVelocity(value)
                                    }
                                    Label {
                                        text: "有力"
                                        color: root.theme.textMuted
                                        font.pixelSize: root.theme.typeSmall
                                    }
                                }

                                Label {
                                    objectName: "velocityValueLabel"
                                    text: root.velocityDescription(beginnerVelocitySlider.value)
                                    color: root.theme.textSecondary
                                    font.pixelSize: root.theme.typeSmall
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Repeater {
                                        model: [
                                            { label: "轻", value: 40 },
                                            { label: "自然", value: 75 },
                                            { label: "强", value: 105 }
                                        ]
                                        delegate: AppButton {
                                            required property var modelData
                                            theme: root.theme
                                            text: modelData.label
                                            implicitWidth: 36
                                            implicitHeight: 36
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 36
                                            onClicked: {
                                                beginnerVelocitySlider.previewValue(modelData.value)
                                                root.applyBeginnerVelocity(modelData.value)
                                            }
                                        }
                                    }
                                }
                            }

                            AppField {
                                id: instrumentField
                                objectName: "instrumentField"
                                theme: root.theme
                                label: "乐器 · 播放音色"
                                visible: root.isAdvanced
                                Layout.fillWidth: true
                                wheelScrollEnabled: true
                                onWheelScrollRequested: delta => root.scrollInspectorBy(delta)
                                text: root.summary.instrumentMixed
                                    ? "" : root.valueText("instrument")
                                placeholderText: root.summary.instrumentMixed ? "混合" : "piano"
                                onEditingFinished: {
                                    if (text.trim().length > 0)
                                        editor.setSelectedProperty("instrument", text.trim())
                                }
                            }

                            ReleaseControl {
                                theme: root.theme
                                editorBridge: root.editorBridge
                                summary: root.summary
                                wheelScrollHandler: root.scrollInspectorBy
                            }

                            ColumnLayout {
                                visible: root.summary.count > 0
                                Layout.fillWidth: true
                                spacing: root.theme.space2

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: root.theme.border
                                }

                                AppButton {
                                    id: advancedToggle
                                    objectName: "advancedInspectorToggle"
                                    theme: root.theme
                                    Layout.fillWidth: true
                                    text: root.advancedShown
                                        ? "收起精细参数" : "精细调整…"
                                    onClicked: {
                                        // Commit any focused field in its current units first.
                                        advancedToggle.forceActiveFocus()
                                        root.advancedShown = !root.advancedShown
                                    }
                                }

                                GridLayout {
                                    visible: root.advancedShown
                                    columns: 2
                                    rowSpacing: root.theme.space1
                                    columnSpacing: root.theme.space2
                                    Layout.fillWidth: true
                                    Label {
                                        text: "准确秒数"
                                        color: root.theme.textMuted
                                        font.pixelSize: root.theme.typeSmall
                                    }
                                    Label {
                                        text: root.summary.startTimeMixed
                                            ? "混合" : root.valueText("startTime", 2) + " s"
                                        color: root.theme.textSecondary
                                        font.pixelSize: root.theme.typeSmall
                                    }
                                    Label {
                                        text: "音高 / 力度"
                                        color: root.theme.textMuted
                                        font.pixelSize: root.theme.typeSmall
                                    }
                                    Label {
                                        text: (root.summary.pitchMixed
                                            ? "混合" : root.valueText("pitch"))
                                            + " / "
                                            + (root.summary.velocityMixed
                                                ? "混合" : root.valueText("velocity"))
                                        color: root.theme.textSecondary
                                        font.pixelSize: root.theme.typeSmall
                                    }
                                }
                            }
                        }
                    }
                }

                CompositeSelectionSection {
                    id: compositeSelectionSection
                    objectName: "compositeSelectionSummary"
                    theme: root.theme
                    editorBridge: root.editorBridge
                    experienceBridge: root.experienceBridge
                    summary: root.compositeSummary
                    secondsPerBeat: root.secondsPerBeat
                    sectionStateReady: root.sectionStateReady
                    visible: root.inspectorMode === "selection"
                        && root.contextKind === "multi"
                        && root.hasCompositeSelection
                    Layout.fillWidth: true
                }

                PropertySection {
                    id: musicLayerInspectorSection
                    objectName: "musicLayerInspectorSection"
                    theme: root.theme
                    title: editor.activeLayer.name || "音乐层"
                    subtitle: (editor.activeLayer.noteCount || 0) + " 个音符"
                    visible: root.inspectorMode === "selection"
                        && root.contextKind === "layer"
                    expanded: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: root.theme.space2

                        AppField {
                            id: musicLayerNameField
                            objectName: "musicLayerNameField"
                            theme: root.theme
                            label: "名称"
                            Layout.fillWidth: true
                            text: editor.activeLayer.name || ""
                            onEditingFinished: {
                                if (text.trim().length > 0)
                                    editor.renameMusicLayer(editor.activeLayerId, text.trim())
                            }
                        }

                        Label {
                            text: "角色"
                            color: root.theme.textMuted
                            font.pixelSize: root.theme.typeSmall
                        }
                        InspectorComboBox {
                            objectName: "musicLayerRoleControl"
                            Layout.fillWidth: true
                            model: ["旋律", "伴奏", "低音", "鼓", "其他", "自定义"]
                            currentIndex: Math.max(0, [
                                "melody", "accompaniment", "bass", "drums", "other", "custom"
                            ].indexOf(editor.activeLayer.role || "other"))
                            onActivated: index => editor.setMusicLayerProperty(
                                editor.activeLayerId,
                                "role",
                                ["melody", "accompaniment", "bass", "drums", "other", "custom"][index]
                            )
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: root.theme.space2
                            AppColorField {
                                id: musicLayerColorField
                                objectName: "musicLayerColorField"
                                theme: root.theme
                                label: "音乐层颜色"
                                Layout.fillWidth: true
                                allowReset: true
                                text: editor.activeLayer.customColor || ""
                                onEditingFinished: editor.setMusicLayerProperty(
                                    editor.activeLayerId, "customColor", text.length > 0 ? text : null)
                            }
                            AppButton {
                                theme: root.theme
                                text: "重置"
                                variant: "ghost"
                                Layout.alignment: Qt.AlignBottom
                                onClicked: editor.setMusicLayerProperty(
                                    editor.activeLayerId, "customColor", null)
                            }
                        }

                        AppField {
                            objectName: "musicLayerInstrumentField"
                            theme: root.theme
                            label: "乐器"
                            Layout.fillWidth: true
                            text: editor.activeLayer.instrument || "piano"
                            onEditingFinished: editor.setMusicLayerProperty(
                                editor.activeLayerId, "instrument", text.trim())
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "音量"
                                color: root.theme.textSecondary
                                font.pixelSize: root.theme.typeSmall
                            }
                            StyledSlider {
                                objectName: "musicLayerVolumeSlider"
                                theme: root.theme
                                from: 0
                                to: 1
                                stepSize: 0.01
                                modelValue: Number(editor.activeLayer.volume || 0)
                                Layout.fillWidth: true
                                accessibleName: "音乐层音量"
                                onValueCommitted: value => editor.setMusicLayerProperty(
                                    editor.activeLayerId, "volume", value)
                            }
                            Label {
                                text: Math.round(Number(editor.activeLayer.volume || 0) * 100) + "%"
                                color: root.theme.textPrimary
                                font.pixelSize: root.theme.typeSmall
                            }
                        }

                        AppField {
                            objectName: "musicLayerVelocityOffsetField"
                            theme: root.theme
                            label: "相对力度（-127～127）"
                            Layout.fillWidth: true
                            text: String(editor.activeLayer.velocityOffset || 0)
                            validator: IntValidator { bottom: -127; top: 127 }
                            onEditingFinished: editor.setMusicLayerProperty(
                                editor.activeLayerId, "velocityOffset", Number(text))
                        }
                        AppField {
                            objectName: "musicLayerTransposeField"
                            theme: root.theme
                            label: "移调（半音）"
                            Layout.fillWidth: true
                            text: String(editor.activeLayer.transpose || 0)
                            validator: IntValidator { bottom: -48; top: 48 }
                            onEditingFinished: editor.setMusicLayerProperty(
                                editor.activeLayerId, "transpose", Number(text))
                        }
                        AppField {
                            objectName: "musicLayerTimeOffsetField"
                            theme: root.theme
                            label: "时间偏移（秒）"
                            Layout.fillWidth: true
                            text: Number(editor.activeLayer.timeOffset || 0).toFixed(3)
                            validator: DoubleValidator { decimals: 3 }
                            onEditingFinished: editor.setMusicLayerProperty(
                                editor.activeLayerId, "timeOffset", Number(text))
                        }

                        Label {
                            text: "新音符余音"
                            color: root.theme.textMuted
                            font.pixelSize: root.theme.typeSmall
                        }
                        SegmentedControl {
                            id: musicLayerDefaultReleaseControl
                            objectName: "musicLayerDefaultReleaseControl"
                            theme: root.theme
                            Layout.fillWidth: true
                            model: ["自然", "自定义", "立即截止"]
                            currentIndex: Math.max(0, [
                                "natural", "custom", "cut"
                            ].indexOf(editor.activeLayer.defaultReleaseMode || "natural"))
                            onActivated: index => editor.setMusicLayerProperty(
                                editor.activeLayerId,
                                "defaultReleaseMode",
                                ["natural", "custom", "cut"][index]
                            )
                        }
                        AppField {
                            id: musicLayerDefaultReleaseMsField
                            objectName: "musicLayerDefaultReleaseMsField"
                            theme: root.theme
                            label: "余音时长（毫秒）"
                            Layout.fillWidth: true
                            visible: (editor.activeLayer.defaultReleaseMode || "natural")
                                === "custom"
                            text: String(editor.activeLayer.defaultReleaseMs === null
                                || editor.activeLayer.defaultReleaseMs === undefined
                                ? 250 : Math.round(editor.activeLayer.defaultReleaseMs))
                            validator: DoubleValidator { bottom: 0; top: 5000; decimals: 1 }
                            onEditingFinished: editor.setMusicLayerProperty(
                                editor.activeLayerId, "defaultReleaseMs", Number(text))
                        }

                    }
                }

                PropertySection {
                    id: inspectorProjectSection
                    objectName: "inspectorProjectSection"
                    theme: root.theme
                    title: "工程概览"
                    subtitle: ""
                    visible: root.inspectorMode === "project"
                    expanded: experience.inspectorSectionExpanded("project", true)
                    onExpandedChanged: {
                        if (root.sectionStateReady)
                            experience.setInspectorSectionExpanded("project", expanded)
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: root.theme.space1
                        Label {
                            Layout.fillWidth: true
                            text: "BPM  " + editor.bpm + "   ·   拍号 "
                                + editor.beatsPerBar + "/" + editor.beatUnit
                            color: root.theme.textSecondary
                            font.family: root.theme.numberFont
                            font.pixelSize: root.theme.typeSmall
                            elide: Text.ElideRight
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "时长  " + Number(editor.playbackDurationSeconds).toFixed(1) + " 秒"
                            color: root.theme.textMuted
                            font.family: root.theme.numberFont
                            font.pixelSize: root.theme.typeSmall
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    implicitHeight: root.theme.space6
                }
            }

            ScrollBar.vertical: SafeScrollBar {
                id: inspectorBodyScrollBar
                objectName: "inspectorBodyScrollBar"
                theme: root.theme
            }

        }
    }

    // Own wheel input at the inspector boundary. When the content is scrolled
    // to an edge, a field can be visually outside the Flickable hit area; the
    // boundary handler keeps the inspector scrollable without editing fields.
    WheelHandler {
        id: inspectorWheelHandler
        objectName: "inspectorWheelHandler"
        enabled: root.panelExpanded
        grabPermissions: PointerHandler.CanTakeOverFromAnything

        onWheel: wheel => {
            var delta = wheel.angleDelta.y !== 0
                ? wheel.angleDelta.y : wheel.pixelDelta.y
            if (delta === 0)
                return
            var maximumContentY = Math.max(
                0, inspectorBodyFlickable.contentHeight
                    - inspectorBodyFlickable.height)
            inspectorBodyFlickable.contentY = Math.max(
                0,
                Math.min(
                    maximumContentY,
                    inspectorBodyFlickable.contentY - delta))
            wheel.accepted = true
        }
    }

    Connections {
        target: editor
        function onSelectionChanged() {
            if (editor.selectedCount > 0)
                root.showNoteContext()
        }
    }
}
