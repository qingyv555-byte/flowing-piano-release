import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Rectangle {
    id: topBar
    objectName: "commandBar"

    required property Theme theme
    property Item studioCommandHost: null
    property bool assistantVisible: false
    property bool compact: width < 1800 || focusMode
    readonly property bool ultraCompact: width < 1320
    property bool focusMode: false
    // Move secondary actions behind the overflow menu before a 2K/DPI-scaled
    // window can push the rightmost controls outside the visible toolbar.
    readonly property bool actionOverflow: width < 1800
    // Keep the status suffix at the smallest supported width so the control
    // still tells beginners which harmony state is active. At 1366px and
    // above the shorter label leaves room for the rest of the command group.
    property bool harmonyCompact: width <= 1280
    property bool harmonyAvailable: false
    property bool followPlayback: true
    property bool audioExporting: false
    property bool templateMode: false
    property string templateName: ""
    property string templateSubtitle: "正在改编内置乐曲"
    property string harmonyMode: "off"
    property string noteDisplayMode: "original"
    property bool arrangementVisible: false
    property bool inspectorVisible: false
    property bool autosaveEnabled: false
    // Selection actions live in the Timeline overlay. Keeping the command bar
    // independent makes its primary navigation stable while selection changes.
    property bool contextualActionsVisible: false
    readonly property real contextualActionsWidth: 0
    readonly property string harmonyModeLabel: harmonyMode === "preview" ? "预览"
        : harmonyMode === "auto" ? "自动" : "关"

    signal templateBackRequested(var focusReturnTarget)
    signal appearanceRequested(var focusReturnTarget)
    signal playingSettingsRequested()
    signal undoRequested()
    signal redoRequested()
    signal openRequested()
    signal newRequested(var opener)
    signal renameRequested(var opener)
    signal helpRequested(var opener)
    signal midiImportRequested()
    signal audioImportRequested()
    signal exportMidiRequested()
    signal exportAudioRequested()
    signal exportProjectRequested()
    signal saveRequested()
    signal saveCopyRequested()
    signal followPlaybackRequested(bool followPlayback)
    signal harmonyModeRequested(var opener)
    signal scoreLabRequested()
    signal assistantRequested()
    signal focusModeRequested(bool enabled)
    signal noteDisplayModeRequested(string mode)
    signal arrangementRequested(bool visible)
    signal inspectorRequested(bool visible)
    signal homeRequested(var focusReturnTarget)
    signal libraryRequested(var focusReturnTarget)
    signal autosaveRequested(bool enabled)

    implicitHeight: theme.topBarHeight
    color: theme.surface
    border.width: 0

    component ThemedComboBox: ComboBox {
        id: control
        implicitWidth: 70
        implicitHeight: 30
        Accessible.role: Accessible.ComboBox

        contentItem: Text {
            leftPadding: 10
            rightPadding: 24
            text: control.displayText
            color: control.enabled ? topBar.theme.textPrimary : topBar.theme.textMuted
            font.family: topBar.theme.fontUi
            font.pixelSize: topBar.theme.typeSmall
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: topBar.theme.radiusM
            color: control.hovered ? topBar.theme.surfaceHover : topBar.theme.elevatedSurface
            border.width: control.activeFocus ? 2 : 1
            border.color: control.activeFocus ? topBar.theme.focusRing : topBar.theme.borderStrong
        }
        indicator: Text {
            x: control.width - width - 9
            y: (control.height - height) / 2
            text: "⌄"
            color: topBar.theme.textSecondary
            font.pixelSize: 14
        }
        delegate: ItemDelegate {
            width: control.width
            contentItem: Text {
                text: modelData
                color: topBar.theme.textPrimary
                font.family: topBar.theme.fontUi
                font.pixelSize: topBar.theme.typeSmall
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: parent.highlighted ? topBar.theme.surfaceHover : topBar.theme.elevatedSurface
            }
        }
    }

    component ThemedSpinBox: Basic.SpinBox {
        id: control
        implicitWidth: 112
        implicitHeight: 30
        leftPadding: topBar.theme.space1
            + (control.mirrored ? control.up.indicator.width : control.down.indicator.width)
        rightPadding: topBar.theme.space1
            + (control.mirrored ? control.down.indicator.width : control.up.indicator.width)
        Accessible.role: Accessible.SpinBox

        signal editingValueCommitted(int value)

        contentItem: TextInput {
            z: 2
            text: control.displayText
            color: topBar.theme.textPrimary
            font.family: topBar.theme.numberFont
            font.pixelSize: topBar.theme.typeSmall
            horizontalAlignment: Qt.AlignHCenter
            verticalAlignment: Qt.AlignVCenter
            readOnly: !control.editable
            validator: control.validator
            inputMethodHints: Qt.ImhDigitsOnly
            selectByMouse: true
            leftPadding: topBar.theme.space2
            rightPadding: topBar.theme.space2
            onEditingFinished: {
                if (!acceptableInput) {
                    text = control.textFromValue(control.value, control.locale)
                    return
                }
                var typedValue = control.valueFromText(text, control.locale)
                control.value = typedValue
                control.editingValueCommitted(typedValue)
            }
        }
        up.indicator: Rectangle {
            objectName: "bpmIncreaseButton"
            x: control.mirrored ? 0 : control.width - width
            y: topBar.theme.space1 / 2
            width: 28
            height: control.height - topBar.theme.space1
            radius: topBar.theme.radiusS
            color: control.up.pressed ? topBar.theme.surfacePressed
                : (control.up.hovered ? topBar.theme.surfaceHover : "transparent")

            Text {
                anchors.centerIn: parent
                text: "+"
                color: control.enabled ? topBar.theme.textPrimary : topBar.theme.textMuted
                font.family: topBar.theme.numberFont
                font.pixelSize: topBar.theme.typeBody
                font.weight: topBar.theme.weightBold
            }
        }
        down.indicator: Rectangle {
            objectName: "bpmDecreaseButton"
            x: control.mirrored ? control.width - width : 0
            y: topBar.theme.space1 / 2
            width: 28
            height: control.height - topBar.theme.space1
            radius: topBar.theme.radiusS
            color: control.down.pressed ? topBar.theme.surfacePressed
                : (control.down.hovered ? topBar.theme.surfaceHover : "transparent")

            Text {
                anchors.centerIn: parent
                text: "−"
                color: control.enabled ? topBar.theme.textPrimary : topBar.theme.textMuted
                font.family: topBar.theme.numberFont
                font.pixelSize: topBar.theme.typeBody
                font.weight: topBar.theme.weightBold
            }
        }
        background: Rectangle {
            radius: topBar.theme.radiusM
            color: control.hovered ? topBar.theme.surfaceHover : topBar.theme.elevatedSurface
            border.width: control.activeFocus ? 2 : 1
            border.color: control.activeFocus ? topBar.theme.focusRing : topBar.theme.borderStrong
        }
    }

    component TopBarMenuItem: AppMenuItem {
        theme: topBar.theme
        readonly property color textColor: foregroundColor
        readonly property color hoverBackgroundColor: theme.surfaceHover
    }

    StudioCommandBar {
        parent: topBar.studioCommandHost !== null ? topBar.studioCommandHost : topBar
        anchors.fill: parent
        visible: topBar.theme.studio
        theme: topBar.theme
        bar: topBar
    }

    RowLayout {
        id: nocturneCommandContent
        objectName: "nocturneCommandContent"
        anchors.fill: parent
        anchors.leftMargin: topBar.theme.space3
        anchors.rightMargin: topBar.theme.space3
        spacing: topBar.theme.space2
        visible: topBar.theme.nocturne

        AppButton {
            id: nocturneHomeNav
            objectName: "nocturneHomeNav"
            theme: topBar.theme
            text: "首页"
            variant: "ghost"
            onClicked: topBar.homeRequested(nocturneHomeNav)
            Accessible.name: "返回夜庭首页"
        }
        AppButton {
            id: nocturneLibraryNav
            objectName: "nocturneLibraryNav"
            theme: topBar.theme
            text: "作品库"
            variant: "ghost"
            onClicked: topBar.libraryRequested(nocturneLibraryNav)
            Accessible.name: "返回作品库"
        }

        Label {
            text: editor.currentFileName
            color: topBar.theme.textMuted
            font.family: topBar.theme.fontUi
            font.pixelSize: topBar.theme.typeCaption
            elide: Text.ElideMiddle
            Layout.fillWidth: true
            Layout.maximumWidth: 220
        }

        Item { Layout.fillWidth: true }

        AppButton {
            objectName: "nocturneArrangementNav"
            theme: topBar.theme
            text: "编排"
            checkable: true
            checkedExternallyControlled: true
            checked: topBar.arrangementVisible
            onClicked: topBar.arrangementRequested(!topBar.arrangementVisible)
            Accessible.name: topBar.arrangementVisible ? "收起编排" : "展开编排"
        }
        AppButton {
            objectName: "nocturneAiNav"
            theme: topBar.theme
            text: "AI"
            onClicked: topBar.assistantRequested()
            Accessible.name: "打开 AI 助手"
        }
        AppButton {
            objectName: "nocturneInspectorNav"
            theme: topBar.theme
            text: "属性"
            checkable: true
            checkedExternallyControlled: true
            checked: topBar.inspectorVisible
            onClicked: topBar.inspectorRequested(!topBar.inspectorVisible)
            Accessible.name: topBar.inspectorVisible ? "收起属性" : "展开属性"
        }
        AppButton {
            objectName: "nocturneFocusNav"
            theme: topBar.theme
            text: topBar.focusMode ? "退出专注" : "专注"
            checkable: true
            checkedExternallyControlled: true
            checked: topBar.focusMode
            onClicked: topBar.focusModeRequested(!topBar.focusMode)
            Accessible.name: topBar.focusMode ? "退出专注模式" : "进入专注模式"
        }
        AppButton {
            id: nocturneImportButton
            objectName: "nocturneImportButton"
            theme: topBar.theme
            text: "导入"
            enabled: !editor.isSaving && !topBar.audioExporting
            onClicked: importMenu.open()
            Accessible.name: "导入"
        }
        AppButton {
            id: nocturneExportButton
            objectName: "nocturneExportButton"
            theme: topBar.theme
            text: "导出"
            enabled: !editor.isSaving && !topBar.audioExporting
            onClicked: exportMenu.open()
            Accessible.name: "导出"
        }
        AppButton {
            objectName: "nocturneSaveNav"
            theme: topBar.theme
            text: editor.isSaving ? "保存中..." : "保存"
            variant: "primary"
            loading: editor.isSaving
            enabled: !editor.isSaving
            onClicked: topBar.saveRequested()
            Accessible.name: "保存工程"
        }
        AppIconButton {
            id: nocturneEditorSettingsButton
            objectName: "nocturneEditorSettingsButton"
            theme: topBar.theme
            iconSource: "icons/settings-2.svg"
            tooltip: "编辑设置"
            accessibleName: "编辑设置"
            onClicked: nocturneEditorSettingsPopup.open()
        }
        AppIconButton {
            id: nocturneMoreMenuButton
            objectName: "nocturneMoreMenuButton"
            theme: topBar.theme
            iconSource: "icons/menu.svg"
            tooltip: "更多命令"
            accessibleName: "更多命令"
            onClicked: moreMenu.open()
        }
    }

    Popup {
        id: nocturneEditorSettingsPopup
        objectName: "nocturneEditorSettingsPopup"
        parent: topBar
        x: {
            var buttonRight = nocturneEditorSettingsButton.mapToItem(
                topBar, nocturneEditorSettingsButton.width, 0).x
            return Math.max(topBar.theme.space3,
                Math.min(topBar.width - width - topBar.theme.space3, buttonRight - width))
        }
        y: topBar.height
        width: 430
        padding: topBar.theme.space4
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        visible: opened && topBar.theme.nocturne
        onClosed: nocturneEditorSettingsButton.forceActiveFocus()
        background: Rectangle {
            radius: topBar.theme.radiusL
            color: topBar.theme.popupBackground
            border.color: topBar.theme.borderStrong
        }
        contentItem: ColumnLayout {
            spacing: topBar.theme.space3

            Label {
                text: "编辑设置"
                color: topBar.theme.textPrimary
                font.family: topBar.theme.fontUi
                font.pixelSize: topBar.theme.typePanelTitle
                font.weight: topBar.theme.weightSemibold
            }
            RowLayout {
                spacing: topBar.theme.space2
                Label {
                    text: "速度"
                    color: topBar.theme.textMuted
                    font.family: topBar.theme.fontUi
                    font.pixelSize: topBar.theme.typeCaption
                }
                ThemedSpinBox {
                    objectName: "nocturneBpmField"
                    from: 60
                    to: 300
                    value: editor.bpm
                    editable: true
                    onValueModified: editor.setBpm(value)
                    onEditingValueCommitted: editor.setBpm(value)
                    Accessible.name: "项目 BPM"
                }
                Label {
                    text: "拍号"
                    color: topBar.theme.textMuted
                    font.family: topBar.theme.fontUi
                    font.pixelSize: topBar.theme.typeCaption
                }
                ThemedComboBox {
                    objectName: "nocturneTimeSignatureBox"
                    model: editor.timeSignatureOptions
                    currentIndex: model.indexOf(editor.beatsPerBar + "/" + editor.beatUnit)
                    onActivated: {
                        var picked = model[currentIndex].split("/")
                        editor.setTimeSignature(picked[0], picked[1])
                    }
                    Accessible.name: "拍号"
                }
                Item { Layout.fillWidth: true }
            }
            RowLayout {
                spacing: topBar.theme.space2
                AppButton {
                    objectName: "nocturneSnapToggle"
                    theme: topBar.theme
                    text: "吸附"
                    checkable: true
                    checkedExternallyControlled: true
                    checked: editor.snapEnabled
                    onClicked: editor.setSnapEnabled(!editor.snapEnabled)
                    Accessible.name: "吸附开关"
                }
                ThemedComboBox {
                    objectName: "nocturneSnapDivisionBox"
                    model: ["1/4", "1/8", "1/16"]
                    currentIndex: editor.snapDivision === 4 ? 0
                        : (editor.snapDivision === 16 ? 2 : 1)
                    enabled: editor.snapEnabled
                    onActivated: editor.setSnapDivision([4, 8, 16][currentIndex])
                    Accessible.name: "吸附精度"
                }
                Item { Layout.fillWidth: true }
                Label {
                    objectName: "nocturneKeyboardHint"
                    text: "八度 C" + keyboard.octave + " · A–J 弹奏"
                    color: topBar.theme.textSecondary
                    font.family: topBar.theme.fontUi
                    font.pixelSize: topBar.theme.typeCaption
                }
                AppButton {
                    objectName: "nocturneStageHelpButton"
                    theme: topBar.theme
                    text: "使用帮助"
                    variant: "ghost"
                    onClicked: {
                        nocturneEditorSettingsPopup.close()
                        stageHelpPopup.open()
                    }
                }
            }
        }
    }

    RowLayout {
        id: primaryCommandContent
        objectName: "primaryCommandContent"
        anchors.fill: parent
        anchors.leftMargin: topBar.theme.space3
        anchors.rightMargin: topBar.theme.space3
        spacing: topBar.theme.space2
        visible: !topBar.theme.studio && !topBar.theme.nocturne

        Item {
            objectName: "projectTheoryGroup"
            width: 0
            height: 0
            visible: false
        }

        RowLayout {
            objectName: "projectIdentityGroup"
            // Project identity and save state now live in WindowChrome. Keep
            // the object name for existing UI probes without reserving space
            // for a duplicate title inside the command bar.
            visible: false
            Layout.minimumWidth: 0
            Layout.preferredWidth: 0
            Layout.maximumWidth: 0
            spacing: topBar.theme.space2

            Label {
                text: "AI Piano Studio"
                color: topBar.theme.textPrimary
                font.family: topBar.theme.fontUi
                font.weight: topBar.theme.weightBold
                font.pixelSize: topBar.theme.typeBody
                elide: Text.ElideRight
                Layout.maximumWidth: topBar.compact ? 100 : -1
            }
            Label {
                text: editor.isDirty ? "未保存" : "已保存"
                color: editor.isDirty ? topBar.theme.warning : topBar.theme.textMuted
                font.family: topBar.theme.fontUi
                font.pixelSize: topBar.theme.typeCaption
            }
        }

        RowLayout {
            id: templateEditorIdentity
            objectName: "templateEditorIdentity"
            visible: topBar.templateMode && !topBar.contextualActionsVisible
            Layout.maximumWidth: topBar.compact ? 250 : 320
            spacing: topBar.theme.space2

            AppIconButton {
                id: templateEditorBackButton
                objectName: "templateEditorBackButton"
                theme: topBar.theme
                iconSource: "icons/arrow-left.svg"
                tooltip: "返回上一层"
                accessibleName: "返回上一层"
                onClicked: topBar.templateBackRequested(templateEditorBackButton)
            }

            ColumnLayout {
                spacing: 1
                Layout.fillWidth: true

                Label {
                    text: topBar.templateName || "示例乐曲"
                    color: topBar.theme.textPrimary
                    font.family: topBar.theme.fontUi
                    font.weight: topBar.theme.weightBold
                    font.pixelSize: topBar.theme.typeBody
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Label {
                    text: topBar.templateSubtitle
                    color: topBar.theme.textSecondary
                    font.family: topBar.theme.fontUi
                    font.pixelSize: topBar.theme.typeCaption
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }

        RowLayout {
            objectName: "keyboardBar"
            visible: experience.mode === "advanced" && !topBar.compact
                && !topBar.contextualActionsVisible
            spacing: topBar.theme.space1

            Label {
                objectName: "octaveLabel"
                text: "八度 C" + keyboard.octave
                color: topBar.theme.textPrimary
                font.family: topBar.theme.fontUi
                font.pixelSize: topBar.theme.typeCaption
            }
            Label {
                objectName: "keyboardHint"
                text: "A–J 弹奏"
                color: topBar.theme.textSecondary
                font.family: topBar.theme.fontUi
                font.pixelSize: topBar.theme.typeCaption
                HoverHandler { id: keyboardHintHover }
                ToolTip.visible: keyboardHintHover.hovered
                ToolTip.text: "A / S / D / F / G / H / J 弹奏"
            }
        }

        AppButton {
            id: stageHelpButton
            objectName: "stageHelpButton"
            visible: !topBar.compact
            theme: topBar.theme
            text: "使用帮助"
            variant: "ghost"
            onClicked: stageHelpPopup.open()
        }

        Popup {
            id: stageHelpPopup
            objectName: "stageHelpPopup"
            parent: topBar
            x: topBar.theme.space3
            y: topBar.height
            width: Math.min(topBar.width - 24, 380)
            padding: 20
            focus: true
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            onClosed: topBar.forceActiveFocus()
            background: Rectangle {
                color: topBar.theme.elevatedSurface
                radius: topBar.theme.radiusL
                border.color: topBar.theme.accentPrimary
            }
            contentItem: ColumnLayout {
                spacing: 12
                Label {
                    text: "快速操作"
                    color: topBar.theme.textPrimary
                    font.family: topBar.theme.fontUi
                    font.pixelSize: topBar.theme.typeSection
                    font.bold: true
                }
                Repeater {
                    model: ["点击空白：放一个音符", "拖动音符：改变高低和位置",
                        "拖动音符右边：让声音更长", "按空格：播放或暂停；Ctrl+Z：撤销"]
                    delegate: Label {
                        required property string modelData
                        text: modelData
                        color: topBar.theme.textSecondary
                        font.family: topBar.theme.fontUi
                        font.pixelSize: topBar.theme.typeSmall
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }
                }
                AppButton {
                    theme: topBar.theme
                    text: "知道了"
                    variant: "primary"
                    Layout.fillWidth: true
                    onClicked: stageHelpPopup.close()
                }
            }
        }

        RowLayout {
            id: rhythmControls
            objectName: "rhythmGroup"
            visible: !topBar.templateMode
            Layout.leftMargin: topBar.theme.space2
            spacing: topBar.theme.space1

                    QtObject { objectName: "tempoBar" }
                    Label {
                        text: "速度"
                        color: topBar.theme.textMuted
                        font.family: topBar.theme.fontUi
                        font.pixelSize: topBar.theme.typeCaption
                    }
                    ThemedSpinBox {
                        objectName: "bpmField"
                        from: 60
                        to: 300
                        value: editor.bpm
                        editable: true
                        onValueModified: editor.setBpm(value)
                        onEditingValueCommitted: editor.setBpm(value)
                        ToolTip.visible: hovered
                        ToolTip.text: "项目速度（60-300）"
                        Accessible.name: "项目 BPM"
                    }
                    ThemedComboBox {
                        objectName: "timeSignatureBox"
                        model: editor.timeSignatureOptions
                        currentIndex: model.indexOf(editor.beatsPerBar + "/" + editor.beatUnit)
                        onActivated: {
                            var picked = model[currentIndex].split("/")
                            editor.setTimeSignature(picked[0], picked[1])
                        }
                        ToolTip.visible: hovered
                        ToolTip.text: "拍号"
                        Accessible.name: "拍号"
                    }
                    AppButton {
                        objectName: "snapToggle"
                        theme: topBar.theme
                        text: "吸附"
                        checkable: true
                        checkedExternallyControlled: true
                        checked: editor.snapEnabled
                        onToggled: checked => editor.setSnapEnabled(checked)
                        Accessible.name: "吸附开关"
                        ToolTip.visible: hovered
                        ToolTip.text: "让音符对齐网格；右侧选择网格精度"
                    }
                    AppButton {
                        objectName: "followPlaybackToggle"
                        theme: topBar.theme
                        text: topBar.followPlayback ? "跟随播放" : "自由浏览"
                        checkable: true
                        checkedExternallyControlled: true
                        checked: topBar.followPlayback
                        onToggled: checked => topBar.followPlaybackRequested(checked)
                        Accessible.name: "播放跟随"
                    }
                    ThemedComboBox {
                        objectName: "snapDivisionBox"
                        model: ["1/4", "1/8", "1/16"]
                        currentIndex: editor.snapDivision === 4 ? 0 : (editor.snapDivision === 16 ? 2 : 1)
                        enabled: editor.snapEnabled
                        onActivated: editor.setSnapDivision([4, 8, 16][currentIndex])
                        ToolTip.visible: hovered
                        ToolTip.text: "吸附精度"
                        Accessible.name: "吸附精度"
                    }
        }

        Rectangle {
            objectName: "projectTheoryGroupDivider"
            visible: false
            Layout.preferredWidth: 1
            Layout.preferredHeight: 24
            color: topBar.theme.borderSubtle
        }

        Item { Layout.fillWidth: true }

        RowLayout {
            objectName: "actionGroup"
            visible: true
            spacing: topBar.theme.space2

            AppButton {
                id: arrangementToggle
                objectName: "arrangementToggle"
                visible: topBar.width >= 1100
                theme: topBar.theme
                text: "编排"
                checkable: true
                checkedExternallyControlled: true
                checked: topBar.arrangementVisible
                onClicked: topBar.arrangementRequested(!topBar.arrangementVisible)
                Accessible.name: topBar.arrangementVisible
                    ? "收起编排视图" : "展开编排视图"
                ToolTip.visible: hovered
                ToolTip.text: "显示或隐藏多声部编排区"
            }

            AppButton {
                id: focusModeButton
                objectName: "editorFocusModeToggle"
                visible: topBar.width >= 1150
                theme: topBar.theme
                text: topBar.compact ? "专注" : "专注模式"
                checkable: true
                checkedExternallyControlled: true
                checked: topBar.focusMode
                onToggled: checked => topBar.focusModeRequested(checked)
                Accessible.name: topBar.focusMode ? "退出专注模式" : "进入专注模式"
                ToolTip.visible: hovered
                ToolTip.text: "最大化钢琴卷帘（Ctrl+Shift+F）"
            }

            AppButton {
                id: noteDisplayModeToggle
                objectName: "noteDisplayModeToggle"
                visible: topBar.width >= 1150
                theme: topBar.theme
                text: topBar.noteDisplayMode === "smart" ? "简洁音符" : "全部音符"
                onClicked: topBar.noteDisplayModeRequested(
                    topBar.noteDisplayMode === "smart" ? "original" : "smart"
                )
                Accessible.name: topBar.noteDisplayMode === "smart"
                    ? "切换到原始音符视图" : "切换到智能复合音符视图"
                ToolTip.visible: hovered
                ToolTip.text: topBar.noteDisplayMode === "smart"
                    ? "显示每个原始音符" : "合并密集触发，保留分段编辑"
            }

            AppButton {
                id: assistantButton
                objectName: "assistantButton"
                theme: topBar.theme
                text: "AI 助手"
                onClicked: topBar.assistantRequested()
                Accessible.name: "打开 AI 助手"
            }
        }

        Rectangle {
            objectName: "editFileGroupDivider"
            visible: !topBar.compact && !topBar.contextualActionsVisible
            Layout.preferredWidth: 1
            Layout.preferredHeight: 24
            color: topBar.theme.borderSubtle
        }

        RowLayout {
            id: fileGroup
            objectName: "fileGroup"
            spacing: topBar.theme.space2

            AppIconButton {
                objectName: "undoButton"
                theme: topBar.theme
                iconSource: "icons/undo-2.svg"
                tooltip: "撤销"
                accessibleName: "撤销"
                visible: !topBar.ultraCompact
                enabled: editor.canUndo
                onClicked: topBar.undoRequested()
            }
            AppIconButton {
                objectName: "redoButton"
                theme: topBar.theme
                iconSource: "icons/redo-2.svg"
                tooltip: "重做"
                accessibleName: "重做"
                visible: !topBar.ultraCompact
                enabled: editor.canRedo
                onClicked: topBar.redoRequested()
            }
            AppButton {
                objectName: "openButton"
                visible: !topBar.compact && !topBar.actionOverflow
                theme: topBar.theme
                text: "打开"
                iconSource: "icons/folder-open.svg"
                enabled: !editor.isSaving
                onClicked: topBar.openRequested()
                Accessible.name: "打开工程"
            }
            AppIconButton {
                id: moreMenuButton
                objectName: "moreMenuButton"
                visible: true
                theme: topBar.theme
                iconSource: "icons/menu.svg"
                tooltip: "更多命令"
                accessibleName: "更多命令"
                onClicked: moreMenu.open()
            }
            AppButton {
                id: importButton
                objectName: "importButton"
                theme: topBar.theme
                text: "导入"
                iconSource: "icons/folder-open.svg"
                enabled: !editor.isSaving && !topBar.audioExporting
                onClicked: importMenu.open()
                Accessible.name: "导入"
            }
            AppButton {
                id: exportButton
                objectName: "exportButton"
                theme: topBar.theme
                text: topBar.audioExporting ? "导出中..." : "导出"
                iconSource: "icons/download.svg"
                enabled: !editor.isSaving && !topBar.audioExporting
                loading: topBar.audioExporting
                onClicked: exportMenu.open()
                Accessible.name: "导出"
            }
            AppButton {
                objectName: "saveButton"
                theme: topBar.theme
                text: editor.isSaving ? "保存中..." : "保存"
                iconSource: "icons/save.svg"
                variant: "primary"
                loading: editor.isSaving
                enabled: !editor.isSaving
                onClicked: topBar.saveRequested()
                Accessible.name: "保存工程"
            }
        }
    }

    AppMenu {

        theme: topBar.theme
        id: moreMenu
        objectName: "moreMenu"
        implicitWidth: 192
        padding: topBar.theme.space1
        background: Rectangle {
            radius: topBar.theme.radiusM
            color: topBar.theme.elevatedSurface
            border.color: topBar.theme.borderStrong
        }
        x: {
            var anchorButton = topBar.theme.nocturne
                ? nocturneMoreMenuButton : moreMenuButton
            var buttonRight = anchorButton.mapToItem(
                topBar, anchorButton.width, 0).x
            return Math.max(0, Math.min(topBar.width - width, buttonRight - width))
        }
        y: topBar.height

        TopBarMenuItem {
            objectName: "moreFocusModeAction"
            text: topBar.focusMode ? "退出专注模式" : "专注模式"
            onTriggered: topBar.focusModeRequested(!topBar.focusMode)
        }
        TopBarMenuItem {
            objectName: "moreArrangementAction"
            text: topBar.arrangementVisible ? "收起编排视图" : "展开编排视图"
            onTriggered: topBar.arrangementRequested(!topBar.arrangementVisible)
        }
        TopBarMenuItem {
            objectName: "moreFollowPlaybackAction"
            text: topBar.followPlayback ? "关闭播放跟随" : "开启播放跟随"
            onTriggered: topBar.followPlaybackRequested(!topBar.followPlayback)
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreNoteDisplayAction"
            text: topBar.noteDisplayMode === "smart" ? "显示原始音符" : "合并密集音符显示"
            onTriggered: topBar.noteDisplayModeRequested(
                topBar.noteDisplayMode === "smart" ? "original" : "smart")
        }

        TopBarMenuItem {
            objectName: "moreHarmonyAction"
            text: "和弦模式 · " + topBar.harmonyModeLabel
            enabled: topBar.harmonyAvailable
            onTriggered: topBar.harmonyModeRequested(
                topBar.theme.nocturne ? nocturneMoreMenuButton : moreMenuButton)
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreUndoAction"
            text: "撤销"
            enabled: editor.canUndo
            onTriggered: topBar.undoRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreRedoAction"
            text: "重做"
            enabled: editor.canRedo
            onTriggered: topBar.redoRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreOpenAction"
            text: "打开工程"
            enabled: !editor.isSaving
            onTriggered: topBar.openRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreSaveCopyAction"
            text: "保存副本"
            enabled: !editor.isSaving
            onTriggered: topBar.saveCopyRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreBackgroundSettingsAction"
            text: "背景设置"
            onTriggered: topBar.appearanceRequested(
                topBar.theme.nocturne ? nocturneMoreMenuButton : moreMenuButton)
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "moreAutosaveAction"
            text: topBar.autosaveEnabled ? "自动保存 · 开" : "自动保存 · 关"
            onTriggered: topBar.autosaveRequested(!topBar.autosaveEnabled)
            Accessible.name: text
        }
    }

    AppMenu {

        theme: topBar.theme
        id: importMenu
        objectName: "importMenu"
        implicitWidth: 216
        padding: topBar.theme.space1
        background: Rectangle {
            radius: topBar.theme.radiusM
            color: topBar.theme.elevatedSurface
            border.color: topBar.theme.borderStrong
        }
        x: {
            var anchorButton = topBar.theme.nocturne ? nocturneImportButton : importButton
            var buttonX = anchorButton.mapToItem(topBar, 0, 0).x
            return Math.max(0, Math.min(topBar.width - width, buttonX))
        }
        y: topBar.height

        TopBarMenuItem {
            objectName: "importAudioAction"
            text: "导入音频"
            enabled: !editor.isSaving && !topBar.audioExporting
            onTriggered: topBar.audioImportRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "openExternalProjectAction"
            text: "导入工程文件"
            enabled: !editor.isSaving && !topBar.audioExporting
            onTriggered: topBar.openRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "importMidiAction"
            text: "导入 MIDI"
            enabled: !editor.isSaving && !topBar.audioExporting
            onTriggered: topBar.midiImportRequested()
            Accessible.name: text
        }
    }

    AppMenu {

        theme: topBar.theme
        id: exportMenu
        objectName: "exportMenu"
        implicitWidth: 216
        padding: topBar.theme.space1
        background: Rectangle {
            radius: topBar.theme.radiusM
            color: topBar.theme.elevatedSurface
            border.color: topBar.theme.borderStrong
        }
        x: {
            var anchorButton = topBar.theme.nocturne ? nocturneExportButton : exportButton
            var buttonRight = anchorButton.mapToItem(
                topBar, anchorButton.width, 0
            ).x
            return Math.max(0, Math.min(topBar.width - width, buttonRight - width))
        }
        y: topBar.height

        TopBarMenuItem {
            objectName: "exportProjectAction"
            text: "导出工程文件"
            enabled: !editor.isSaving && !topBar.audioExporting
            onTriggered: topBar.exportProjectRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "exportAudioAction"
            text: "导出音频"
            enabled: !editor.isSaving && !topBar.audioExporting
            onTriggered: topBar.exportAudioRequested()
            Accessible.name: text
        }
        TopBarMenuItem {
            objectName: "exportMidiAction"
            text: "导出 MIDI"
            enabled: !editor.isSaving && !topBar.audioExporting
            onTriggered: topBar.exportMidiRequested()
            Accessible.name: text
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: topBar.theme.borderSubtle
    }
}
