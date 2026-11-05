import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Dialog {
    id: dialog
    objectName: "studioAppearanceDialog"
    required property Theme theme
    required property var settings
    property var assistant: null
    property bool preferencesAvailable: false
    property var preferences: ({})
    property var preferencesDraft: ({})
    property bool toneAvailable: false
    property bool recoveryAvailable: false
    signal preferencesApplied(var values)
    signal recoveryRequested()
    // Music role colors are configured below.
    signal tonePreviewRequested(real value)
    readonly property var roleColorRows: [
        {key: "roleMelodyColor", title: "主旋律"},
        {key: "roleAccompanimentColor", title: "伴奏／副声部"},
        {key: "roleBassColor", title: "低音"},
        {key: "roleDrumsColor", title: "打击乐"},
        {key: "roleOtherColor", title: "其他／未分类"}
    ]
    function updateRoleColor(key, color) {
        if (!dialog.settings || !/^#[0-9a-fA-F]{6}$/.test(String(color)))
            return
        dialog.settings.setValue(key, color)
        dialog.settings.setValue("roleColorMode", "custom")
    }
    function editPreference(key, value) {
        var next = Object.assign({}, preferencesDraft)
        next[key] = value
        preferencesDraft = next
    }
    property int page: 0
    function requestWindowClose(action) { dialog.close(); action() }
    function requestPage(nextPage) {
        if (nextPage >= 0 && nextPage <= 6) page = nextPage
    }
    function requestClose() { close() }
    function applySettings() {
        if (dialog.settings.applyPreview()) {
            if (dialog.preferencesAvailable) dialog.preferencesApplied(dialog.preferencesDraft)
            dialog.close()
        }
    }
    property string backgroundTarget: "editor"
    readonly property bool hasBackgroundImage: /^(file:|skin:)/.test(String(values[backgroundTarget + "Source"]))
    property bool advancedFeedback: false
    property bool previewAnimation: false
    onPageChanged: { previewAnimation = false; advancedFeedback = false }
    readonly property var values: settings ? settings.values : ({})
    parent: Overlay.overlay
    width: Math.min(1120, parent ? parent.width - 32 : 1120)
    height: Math.min(820, parent ? parent.height - 32 : 820)
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    padding: 20
    modal: true
    focus: true
    closePolicy: Popup.NoAutoClose
    Shortcut {
        sequence: "Escape"
        enabled: dialog.opened
        onActivated: dialog.requestClose()
    }
    font.family: theme.fontUi
    font.pixelSize: 14
    palette.window: theme.popupBackground
    palette.base: theme.inputBackground
    palette.text: theme.textPrimary
    palette.windowText: theme.textPrimary
    palette.buttonText: theme.textPrimary
    palette.button: theme.surface
    palette.highlight: theme.accentPrimary
    palette.highlightedText: theme.textOnAccent
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.18) }
    onAboutToShow: {
        backgroundTarget = "editor"
        settings.beginPreview()
        preferencesDraft = Object.assign({}, preferences)
    }
    onClosed: { previewAnimation = false; settings.cancelPreview() }
    Timer { interval: 4000; running: dialog.previewAnimation; onTriggered: dialog.previewAnimation = false }

    background: Rectangle {
        objectName: "studioAppearanceBody"
        color: Qt.rgba(dialog.theme.popupBackground.r, dialog.theme.popupBackground.g,
                       dialog.theme.popupBackground.b, 1)
        radius: dialog.theme.radiusL
        border.color: dialog.theme.borderStrong
    }

    component Heading: Text {
        Layout.fillWidth: true
        color: dialog.theme.textPrimary
        font.family: dialog.theme.fontUi
        font.pixelSize: 20
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
    }
    component Hint: Text {
        Layout.fillWidth: true
        color: dialog.theme.textSecondary
        font.family: dialog.theme.fontUi
        font.pixelSize: 14
        wrapMode: Text.WordWrap
        lineHeight: 1.25
    }
    component ColorChoice: AbstractButton {
        id: choice
        property color swatchColor
        implicitWidth: 72
        implicitHeight: 32
        hoverEnabled: true
        activeFocusOnTab: true
        Accessible.role: Accessible.RadioButton
        Accessible.checked: checked
        background: Rectangle {
            radius: dialog.theme.radiusM
            color: choice.checked ? dialog.theme.surfaceSelected
                : choice.hovered || choice.down ? dialog.theme.surfaceHover : dialog.theme.surface
            border.width: choice.activeFocus ? 2 : 1
            border.color: choice.checked || choice.activeFocus
                ? dialog.theme.accentSelection : dialog.theme.borderStrong
        }
        contentItem: Item {
          Row {
            anchors.centerIn: parent
            spacing: 8
            Rectangle {
                width: 12; height: 12; radius: 6
                anchors.verticalCenter: parent.verticalCenter
                color: choice.swatchColor
                border.width: 1
                border.color: dialog.theme.borderStrong
            }
            Text {
                text: choice.text
                color: choice.checked ? dialog.theme.accentSelection : dialog.theme.textPrimary
                font.family: dialog.theme.fontUi
                font.pixelSize: 13
                font.weight: Font.Medium
            }
          }
        }
    }
    component ColorHexField: TextField {
        color: dialog.theme.textPrimary
        font.family: dialog.theme.numberFont
        font.pixelSize: 13
        implicitHeight: 34
        leftPadding: 10
        background: Rectangle {
            color: dialog.theme.inputBackground
            border.color: parent.activeFocus ? dialog.theme.focusRing : dialog.theme.borderStrong
            radius: dialog.theme.radiusM
        }
    }
    component SettingsSection: Frame {
        id: section
        default property alias entries: sectionBody.data
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        padding: 16
        background: Rectangle {
            color: dialog.theme.surface
            border.color: dialog.theme.borderSubtle
            radius: dialog.theme.radiusM
        }
        contentItem: ColumnLayout { id: sectionBody; spacing: section.spacing }
    }

    component ValueSlider: RowLayout {
        required property string settingKey
        required property string title
        property real minimum: 0
        property real maximum: 1
        spacing: 12
        Layout.fillWidth: true
        Text {
            text: parent.title
            Layout.preferredWidth: 100
            color: dialog.theme.textPrimary
            font.family: dialog.theme.fontUi
            font.pixelSize: 14
        }
        StyledSlider {
            theme: dialog.theme
            id: valueControl
            objectName: parent.settingKey + "Slider"
            Layout.fillWidth: true
            from: parent.minimum; to: parent.maximum
            modelValue: Number(dialog.values[parent.settingKey])
            onValueEdited: value => dialog.settings.setValue(parent.settingKey, value)
            accessibleName: parent.title
        }
        Text {
            objectName: parent.settingKey + "ValueLabel"
            Layout.preferredWidth: 44
            text: Math.round(valueControl.value * 100) + "%"
            horizontalAlignment: Text.AlignRight
            color: dialog.theme.textSecondary
            font.family: dialog.theme.fontUi
            font.pixelSize: 14
        }
    }
    component ValueCheck: CheckBox {
        required property string settingKey
        objectName: settingKey + "Check"
        checked: Boolean(dialog.values[settingKey])
        onClicked: dialog.settings.setValue(settingKey, checked)
        font.family: dialog.theme.fontUi
        font.pixelSize: 14
    }

    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Heading { text: dialog.preferencesAvailable ? "设置" : "外观与反馈"; font.pixelSize: 22 }

            }
            AppButton {
                theme: dialog.theme
                text: "关闭"
                objectName: "studioAppearanceClose"
                variant: "ghost"
                onClicked: dialog.requestClose()
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: 6
            Repeater {
                model: dialog.preferencesAvailable ? ["主题背景", "音乐反馈", "动态", "播放与声音", "编辑偏好", "保存与恢复"] : ["主题背景", "音乐反馈", "动态"]
                AppButton {
                    required property int index
                    required property string modelData
                    readonly property int targetPage: index === 0 ? 0 : index + 1
                    implicitHeight: 32
                    theme: dialog.theme
                    text: modelData
                    checkable: true
                    checkedExternallyControlled: true
                    checked: dialog.page === targetPage || (targetPage === 0 && dialog.page === 1)
                    onClicked: dialog.requestPage(targetPage)
                    objectName: "studioSettingsCategory" + targetPage
                }
            }
            Item { Layout.fillWidth: true }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                Layout.rightMargin: 20
                visible: dialog.page === 2
                spacing: 8
                Switch {
                    objectName: "studioFeedbackSwitch"
                    text: "音乐反馈"
                    checked: dialog.values.feedbackLevel !== "off"
                    onClicked: dialog.settings.setValue("feedbackLevel", checked ? "standard" : "off")
                    font.family: dialog.theme.fontUi
                    font.pixelSize: 14
                }
                RowLayout {
                    Layout.fillWidth: true
                    enabled: dialog.values.feedbackLevel !== "off"
                    Repeater {
                        model: [{key: "glass", name: "琉璃声场"}, {key: "weave", name: "织纹"}]
                        AbstractButton {
                            id: styleCard
                            required property var modelData
                            objectName: "studioFeedbackStyle-" + modelData.key
                            Layout.fillWidth: true; Layout.preferredWidth: 1; implicitHeight: 132
                            padding: 10
                            hoverEnabled: true; activeFocusOnTab: true
                            Accessible.name: modelData.name
                            Accessible.role: Accessible.RadioButton
                            Accessible.checked: dialog.values.feedbackStyle === modelData.key
                            onClicked: dialog.settings.setValue("feedbackStyle", modelData.key)
                            background: Rectangle {
                                radius: 8; color: dialog.theme.surface
                                border.width: dialog.values.feedbackStyle === styleCard.modelData.key || styleCard.activeFocus ? 2 : 1
                                border.color: dialog.values.feedbackStyle === styleCard.modelData.key || styleCard.activeFocus ? dialog.theme.accentSelection : dialog.theme.borderStrong
                            }
                            contentItem: ColumnLayout {
                                spacing: 10
                                Text {
                                    objectName: "studioFeedbackStyleTitle-" + styleCard.modelData.key
                                    text: styleCard.modelData.name
                                    font.pixelSize: 14; font.weight: Font.DemiBold
                                    color: dialog.theme.textPrimary; font.family: dialog.theme.fontUi
                                }
                                StudioStylePreview {
                                    Layout.fillWidth: true; Layout.fillHeight: true
                                    theme: dialog.theme; settings: dialog.settings; style: styleCard.modelData.key
                                    animating: dialog.previewAnimation && dialog.visible && dialog.page === 2
                                }
                            }
                        }
                    }
                }
                AppButton {
                    objectName: "studioFeedbackPreviewButton"
                    theme: dialog.theme; variant: "ghost"
                    text: dialog.previewAnimation ? "停止预览" : "预览动效"
                    enabled: dialog.values.feedbackLevel !== "off" && !dialog.values.reducedMotion
                    onClicked: dialog.previewAnimation = !dialog.previewAnimation
                }
            }
        ScrollView {
            id: scroll
            objectName: "studioAppearanceScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            rightPadding: 20
            contentWidth: availableWidth
            clip: true
            // The ScrollView-created Flickable rounds every animated position
            // to a whole logical pixel by default. Fine wheel input then sits
            // still for several frames and jumps, especially at scaled DPI.
            Binding { target: scroll.contentItem; property: "pixelAligned"; value: false }
            Binding { target: scroll.contentItem; property: "boundsBehavior"; value: Flickable.StopAtBounds }
            ScrollBar.vertical: SafeScrollBar {
                theme: dialog.theme
                objectName: "studioAppearanceScrollBar"
                parent: scroll
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.rightMargin: 0
                anchors.bottom: parent.bottom
                policy: ScrollBar.AsNeeded
                visible: size < 1
            }
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 16
                Loader {
                    Layout.fillWidth: true
                    active: dialog.preferencesAvailable && dialog.page >= 4 && dialog.page < 7
                    visible: active
                    sourceComponent: Component {
                        StudioPreferencesPane {
                            theme: dialog.theme; page: dialog.page; draft: dialog.preferencesDraft
                            toneAvailable: dialog.toneAvailable; recoveryAvailable: dialog.recoveryAvailable
                            onEdited: (key, value) => dialog.editPreference(key, value)
                            onTonePreviewRequested: dialog.tonePreviewRequested(dialog.preferencesDraft.tone)
                            onRecoveryRequested: { dialog.close(); dialog.recoveryRequested() }
                        }
                    }
                }
                GridLayout {
                    id: appearanceGrid
                    objectName: "studioAppearanceGroups"
                    Layout.fillWidth: true
                    visible: dialog.page === 0 || dialog.page === 1
                    columns: width >= 860 ? 2 : 1
                    columnSpacing: 16
                    rowSpacing: 16
                SettingsSection {
                    Layout.preferredWidth: 1
                    visible: dialog.page === 0 || dialog.page === 1
                    spacing: 14
                    Heading { text: "界面主题"; font.pixelSize: 16 }
                    Hint { text: "选择明暗外观和界面强调色。" }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Repeater {
                            model: [{key: "ink", name: "深海 · 深色", detail: "深青灰底色，雾青与暖白点缀"},
                                    {key: "paper", name: "纸谱 · 明亮", detail: "清楚、轻盈的明亮工作台"}]
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.maximumWidth: 280
                                AbstractButton {
                                    id: themeCard
                                    objectName: "studioTheme-" + parent.modelData.key
                                    property bool light: parent.modelData.key === "paper"
                                    property string themeKey: parent.modelData.key
                                    property string title: parent.modelData.name
                                    Layout.fillWidth: true; implicitHeight: 104
                                    hoverEnabled: true; activeFocusOnTab: true
                                    Accessible.name: title
                                    Accessible.role: Accessible.RadioButton
                                    readonly property bool selectedTheme: dialog.values.themeId === themeKey
                                    Accessible.checked: selectedTheme
                                    onClicked: dialog.settings.setValue("themeId", themeKey)
                                    background: Rectangle {
                                        radius: 8; color: themeCard.light ? "#F8FAF9" : "#111F28"
                                        border.width: themeCard.selectedTheme || themeCard.activeFocus ? 2 : 1
                                        border.color: themeCard.selectedTheme || themeCard.activeFocus ? dialog.theme.accentSelection : dialog.theme.borderStrong
                                    }
                                    contentItem: Item {
                                        Repeater {
                                            model: 6
                                            Rectangle {
                                                required property int index
                                                x: 14; y: 12 + index * 9
                                                width: parent.width - 28; height: 1
                                                color: themeCard.light ? "#D6E2E2" : "#2A4149"
                                            }
                                        }
                                        Repeater {
                                            model: [0, 2, 1, 4, 3]
                                            Rectangle {
                                                required property int index
                                                required property int modelData
                                                x: 24 + index * (parent.width - 64) / 5; y: 17 + modelData * 8
                                                width: 28; height: 7; radius: 2
                                                color: themeCard.light ? "#527F81" : "#AACBC7"
                                            }
                                        }
                                        Text {
                                            anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 14
                                            text: themeCard.title
                                            color: themeCard.light ? "#243E49" : "#E4EAE5"
                                            font.pixelSize: 14; font.family: dialog.theme.fontUi
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Hint { text: "强调色" }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        Repeater {
                            model: dialog.settings ? dialog.settings.characterThemes : []
                            ColorChoice {
                                required property var modelData
                                objectName: "studioPalette-" + modelData.key
                                text: modelData.name
                                swatchColor: modelData.color
                                checked: dialog.values.themePalette === modelData.key
                                    && dialog.values.themeId === "ink"
                                    && dialog.values.themeSeedSource === "palette"
                                Accessible.name: modelData.name + "主题配色"
                                onClicked: dialog.settings.applyCharacterTheme(modelData.key)
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        AppButton {
                            objectName: "studioCustomThemeColor"
                            theme: dialog.theme
                            text: "自定义…"
                            labelWeight: Font.Normal
                            checkable: true
                            checkedExternallyControlled: true
                            checked: dialog.values.themeSeedSource === "custom" && dialog.values.adaptiveTheme
                            onClicked: {
                                themeColorPicker.selectedColor = dialog.settings.themeSeed.a > 0
                                    ? dialog.settings.themeSeed : dialog.values.customThemeColor
                                themeColorPicker.open()
                            }
                        }
                        Rectangle {
                            objectName: "studioCustomThemeSwatch"
                            width: 28; height: 24; radius: 4
                            color: dialog.values.customThemeColor
                            border.color: dialog.theme.borderStrong
                        }
                        ColorHexField {
                            id: customColorHex
                            objectName: "studioCustomThemeHex"
                            Layout.preferredWidth: 116
                            text: dialog.values.customThemeColor
                            maximumLength: 7
                            selectByMouse: true
                            Accessible.name: "主题颜色值"
                            validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ }
                            onEditingFinished: if (acceptableInput) dialog.settings.applyCustomThemeColor(text)
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
                SettingsSection {
                    Layout.preferredWidth: 1
                    visible: dialog.page === 0 || dialog.page === 1
                    spacing: 12
                    Heading { text: "音符外观"; font.pixelSize: 16 }
                    Hint { text: "调整音符形状与声部颜色，不改变音乐内容。" }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        Repeater {
                            model: [
                                {key: "flat", name: "纯色 · 默认"},
                                {key: "rounded", name: "柔和圆角"},
                                {key: "velocity", name: "力度层次"}
                            ]
                            NoteStyleChoice {
                                required property var modelData
                                objectName: "studioNoteStyle-" + modelData.key
                                theme: dialog.theme
                                styleKey: modelData.key
                                text: modelData.name
                                checked: (dialog.values.noteStyle || "flat") === modelData.key
                                onClicked: dialog.settings.setValue("noteStyle", modelData.key)
                            }
                        }
                    }
                    Hint { text: "声部配色" }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        AppButton {
                            objectName: "studioRoleColorsUniform"
                            theme: dialog.theme; text: "统一颜色"; labelWeight: Font.Normal
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values.roleColorMode === "uniform"
                            onClicked: dialog.settings.setValue("roleColorMode", "uniform")
                        }
                        AppButton {
                            objectName: "studioRoleColorsAuto"
                            theme: dialog.theme; text: "按声部分色"; labelWeight: Font.Normal
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values.roleColorMode === "auto"
                            onClicked: {
                                dialog.settings.setValue("roleColorMode", "auto")
                                // An older single-color override must not
                                // quietly defeat an explicit auto-theme choice.
                                dialog.settings.setValue("noteColorSource", "palette")
                            }
                        }
                        AppButton {
                            objectName: "studioRoleColorsCustom"
                            theme: dialog.theme; text: "分别设置"; labelWeight: Font.Normal
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values.roleColorMode === "custom"
                            onClicked: dialog.settings.setValue("roleColorMode", "custom")
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        visible: dialog.values.roleColorMode === "custom"
                        Repeater {
                            model: dialog.roleColorRows
                            delegate: RowLayout {
                                id: roleRow
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 8
                                Text {
                                    text: roleRow.modelData.title
                                    color: dialog.theme.textPrimary
                                    font.pixelSize: 13
                                    Layout.preferredWidth: 92
                                }
                                Rectangle {
                                    width: 26; height: 24; radius: 4
                                    color: dialog.values[roleRow.modelData.key]
                                    border.color: dialog.theme.borderStrong
                                }
                                AppButton {
                                    objectName: "studioRolePick-" + roleRow.modelData.key
                                    theme: dialog.theme; text: "选择颜色…"
                                    onClicked: {
                                        roleNoteColorPicker.roleKey = roleRow.modelData.key
                                        roleNoteColorPicker.selectedColor = dialog.values[roleRow.modelData.key]
                                        roleNoteColorPicker.open()
                                    }
                                }
                                ColorHexField {
                                    objectName: "studioRoleHex-" + roleRow.modelData.key
                                    Layout.preferredWidth: 112
                                    text: dialog.values[roleRow.modelData.key]
                                    maximumLength: 7; selectByMouse: true
                                    Accessible.name: roleRow.modelData.title + "颜色值"
                                    validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ }
                                    onEditingFinished: if (acceptableInput)
                                        dialog.updateRoleColor(roleRow.modelData.key, text)
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        visible: dialog.values.roleColorMode !== "custom"
                        spacing: 8
                        Repeater {
                            model: dialog.settings ? [{key: "none", name: "默认", color: "#53a79b"}].concat(dialog.settings.characterThemes) : []
                            ColorChoice {
                                required property var modelData
                                objectName: "studioNotePalette-" + modelData.key
                                text: modelData.name
                                swatchColor: modelData.color
                                checked: dialog.values.noteColorSource === "palette"
                                    && dialog.values.characterSkin === modelData.key
                                Accessible.name: modelData.name + "音符配色"
                                onClicked: dialog.settings.applyNotePalette(modelData.key)
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: dialog.values.roleColorMode !== "custom"
                        spacing: 12
                        AppButton {
                            objectName: "studioCustomNoteColor"
                            theme: dialog.theme; text: "统一颜色…"; labelWeight: Font.Normal
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values.noteColorSource === "custom"
                            onClicked: {
                                noteColorPicker.selectedColor = dialog.values.customNoteColor
                                noteColorPicker.open()
                            }
                        }
                        Rectangle {
                            objectName: "studioCustomNoteSwatch"
                            width: 28; height: 24; radius: 4
                            color: dialog.values.customNoteColor
                            border.color: dialog.theme.borderStrong
                        }
                        ColorHexField {
                            objectName: "studioCustomNoteHex"
                            Layout.preferredWidth: 116
                            text: dialog.values.customNoteColor
                            maximumLength: 7; selectByMouse: true
                            Accessible.name: "音符颜色值"
                            validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ }
                            onEditingFinished: if (acceptableInput) dialog.settings.applyCustomNoteColor(text)
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
                SettingsSection {
                    Layout.preferredWidth: 1
                    visible: dialog.page === 0 || dialog.page === 1
                    spacing: 12
                    Layout.columnSpan: appearanceGrid.columns
                    Heading { text: "背景"; font.pixelSize: 16 }
                    Hint { text: "编辑器与首页分别设置。修改会实时预览，点击应用后保存。" }
                    RowLayout {
                        AppButton {
                            objectName: "studioBackgroundTarget-editor"
                            theme: dialog.theme; text: "编辑器背景"
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.backgroundTarget === "editor"
                            onClicked: dialog.backgroundTarget = "editor"
                        }
                        AppButton {
                            objectName: "studioBackgroundTarget-home"
                            theme: dialog.theme; text: "首页背景"
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.backgroundTarget === "home"
                            onClicked: dialog.backgroundTarget = "home"
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: appearanceGrid.columns
                        columnSpacing: 24
                        rowSpacing: 16
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
                            spacing: 10
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 180
                        color: dialog.theme.appBackground
                        radius: dialog.theme.radiusM
                        clip: true
                        StudioBackground {
                            objectName: "studioBackgroundPreview"
                            anchors.fill: parent
                            theme: dialog.theme
                            settings: dialog.settings
                            target: dialog.backgroundTarget
                            active: dialog.visible && (dialog.page === 0 || dialog.page === 1)
                            motionActive: false
                            protectText: false
                        }
                        Rectangle {
                            anchors.left: parent.left; anchors.bottom: parent.bottom
                            anchors.margins: 12
                            width: previewLabel.implicitWidth + 20
                            height: 30
                            radius: 6
                            color: dialog.theme.popupBackground
                            Text {
                                id: previewLabel
                                objectName: "studioBackgroundPreviewLabel"
                                anchors.centerIn: parent
                                text: dialog.backgroundTarget === "editor" ? "编辑器背景预览" : "首页背景预览"
                                font.family: dialog.theme.fontUi
                                font.pixelSize: 13
                                color: dialog.theme.textPrimary
                            }
                        }
                    }
                    RowLayout {
                        AppButton {
                            objectName: "studioBackgroundSource-none"
                            theme: dialog.theme; text: "纯色"
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values[dialog.backgroundTarget + "Source"] === "none"
                            onClicked: dialog.settings.setValue(dialog.backgroundTarget + "Source", "none")
                        }
                        AppButton {
                            objectName: "studioBackgroundSource-builtin"
                            theme: dialog.theme; text: dialog.backgroundTarget === "home" ? "流动声场" : "内置光带"
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values[dialog.backgroundTarget + "Source"] === "builtin"
                            onClicked: dialog.settings.setValue(dialog.backgroundTarget + "Source", "builtin")
                        }
                        AppButton {
                            objectName: "studioBackgroundSource-image"
                            theme: dialog.theme; text: "图片"; labelWeight: Font.Normal
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.hasBackgroundImage
                            onClicked: { if (!dialog.hasBackgroundImage) { imagePicker.target = dialog.backgroundTarget; imagePicker.open() } }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            objectName: "studioBackgroundChooseImage"
                            theme: dialog.theme; text: dialog.hasBackgroundImage ? "更换图片…" : "选择图片…"
                            enabled: !dialog.settings.importBusy
                            onClicked: { imagePicker.target = dialog.backgroundTarget; imagePicker.open() }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Hint {
                        text: dialog.settings.importNotice.length > 0
                            ? dialog.settings.importNotice
                            : String(dialog.values[dialog.backgroundTarget + "Source"]).indexOf("file:") === 0
                            ? "自定义图片"
                            : "支持 PNG、JPG、WebP、BMP 和 GIF，最大 24 MB；GIF 最大 1280 × 720。"
                    }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
                            spacing: 12
                    Text {
                        text: "导入画质 · 下次选择图片时生效"
                        color: dialog.theme.textPrimary
                        font.family: dialog.theme.fontUi
                        font.pixelSize: 14
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        enabled: !dialog.settings.importBusy
                        Repeater {
                            model: [{key: "1080", name: "流畅 · 1080p"},
                                    {key: "1440", name: "清晰 · 1440p"},
                                    {key: "2160", name: "超清 · 4K"}]
                            AppButton {
                                required property var modelData
                                objectName: "studioBackgroundImportSize-" + modelData.key
                                Layout.fillWidth: true
                                theme: dialog.theme
                                text: modelData.name
                                checkable: true
                                checkedExternallyControlled: true
                                checked: dialog.values.backgroundImportSize === modelData.key
                                onClicked: dialog.settings.setValue("backgroundImportSize", modelData.key)
                            }
                        }
                    }

                    RowLayout {
                        visible: dialog.hasBackgroundImage
                        Text { text: "显示方式"; color: dialog.theme.textPrimary; font.pixelSize: 14 }
                        AppButton {
                            objectName: "studioBackgroundFit-cover"
                            theme: dialog.theme; text: "填满"
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values[dialog.backgroundTarget + "Fit"] === "cover"
                            onClicked: dialog.settings.setValue(dialog.backgroundTarget + "Fit", "cover")
                        }
                        AppButton {
                            objectName: "studioBackgroundFit-contain"
                            theme: dialog.theme; text: "完整显示"
                            checkable: true; checkedExternallyControlled: true
                            checked: dialog.values[dialog.backgroundTarget + "Fit"] === "contain"
                            onClicked: dialog.settings.setValue(dialog.backgroundTarget + "Fit", "contain")
                        }
                    }
                    ValueSlider { visible: dialog.values[dialog.backgroundTarget + "Source"] !== "none"; settingKey: dialog.backgroundTarget + "Opacity"; title: "图片可见度" }
                    ValueSlider { visible: dialog.values[dialog.backgroundTarget + "Source"] !== "none"; settingKey: dialog.backgroundTarget + "Dim"; title: "融入底色" }
                    CheckBox {
                        objectName: "studioAdaptImageTheme"
                        text: "让界面配色跟随这张图片"
                        visible: String(dialog.values[dialog.backgroundTarget + "Source"]).indexOf("file:") === 0
                        checked: dialog.values.adaptiveTheme && dialog.values.themeImageTarget === dialog.backgroundTarget
                            && dialog.values.themeSeedSource === "image"
                        onClicked: {
                            dialog.settings.setValue("themeSeedSource", "image")
                            dialog.settings.setValue("themeImageTarget", dialog.backgroundTarget)
                            dialog.settings.setValue("adaptiveTheme", checked)
                        }
                    }
                    Hint {
                        visible: String(dialog.values[dialog.backgroundTarget + "Source"]).indexOf("file:") === 0
                        text: "从图片提取界面强调色；动图使用首帧。"
                    }
                    ValueSlider { visible: dialog.hasBackgroundImage && dialog.values[dialog.backgroundTarget + "Fit"] === "cover"; settingKey: dialog.backgroundTarget + "FocusX"; title: "水平位置" }
                    ValueSlider { visible: dialog.hasBackgroundImage && dialog.values[dialog.backgroundTarget + "Fit"] === "cover"; settingKey: dialog.backgroundTarget + "FocusY"; title: "垂直位置" }
                        }
                    }
                }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: dialog.page === 2
                    spacing: 12


                    Text { text: "反馈强度"; color: dialog.theme.textPrimary; font.pixelSize: 14 }
                    RowLayout {
                        Layout.fillWidth: true
                        enabled: dialog.values.feedbackLevel !== "off"
                        Repeater {
                            model: [{key: "gentle", name: "轻柔"}, {key: "balanced", name: "均衡"},
                                    {key: "vivid", name: "鲜明"}]
                            AppButton {
                                required property var modelData
                                objectName: "studioFeedbackStrength-" + modelData.key
                                Layout.fillWidth: true
                                theme: dialog.theme; text: modelData.name
                                checkable: true; checkedExternallyControlled: true
                                checked: dialog.values.feedbackStrength === modelData.key
                                onClicked: dialog.settings.setValue("feedbackStrength", modelData.key)
                            }
                        }
                    }
                    ColumnLayout {
                        enabled: dialog.values.feedbackLevel !== "off"
                        Layout.fillWidth: true
                        ValueSlider { settingKey: "glassWidth"; title: "宽度"; minimum: 0.25 }
                        ValueSlider { settingKey: "glassPositionX"; title: "水平位置"; enabled: Number(dialog.values.glassWidth) < 1 }

                    }
                    ColumnLayout {
                        visible: dialog.advancedFeedback && dialog.values.feedbackStyle === "glass"
                        enabled: dialog.values.feedbackLevel !== "off"
                        Layout.fillWidth: true
                        RowLayout {
                            Repeater {
                                model: [{name: "雾青", hue: 0.46}, {name: "琥珀", hue: 0.10}, {name: "灰蓝", hue: 0.58}]
                                AppButton {
                                    required property var modelData
                                    objectName: "studioFeedbackHue-" + modelData.name
                                    theme: dialog.theme; text: modelData.name
                                    onClicked: { dialog.settings.setValue("glassHue", modelData.hue); dialog.settings.setValue("glassSaturation", 0.28) }
                                }
                            }
                        }
                        ValueSlider { settingKey: "glassHue"; title: "色相" }
                        ValueSlider { settingKey: "glassSaturation"; title: "色彩浓度" }
                        ValueSlider { settingKey: "glassHeight"; title: "高度占比"; minimum: 0.08; maximum: 0.40 }
                        ValueSlider { settingKey: "glassPositionY"; title: "垂直位置" }

                    }
                    AppButton {
                        objectName: "studioFeedbackCustomize"
                        theme: dialog.theme; text: dialog.advancedFeedback ? "收起细节" : "更多细节"
                        variant: "ghost"
                        onClicked: dialog.advancedFeedback = !dialog.advancedFeedback
                    }
                    ColumnLayout {
                        visible: dialog.advancedFeedback
                        enabled: dialog.values.feedbackLevel !== "off"
                        ValueCheck { settingKey: "noteFeedback"; text: "音符演奏反馈" }
                        ValueCheck { settingKey: "keyFeedback"; text: "琴键响应" }
                        ValueCheck { settingKey: "spectrum"; text: "声音形态" }
                        ValueCheck { settingKey: "atmosphere"; text: "背景随音乐响应" }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: dialog.page === 3
                    spacing: 14
                    Heading { text: "动态"; font.pixelSize: 16 }
                    ValueCheck { settingKey: "homeAmbient"; text: "首页环境动态" }
                    ValueCheck { settingKey: "reducedMotion"; text: "减少动态" }

                    Text { text: "动态刷新上限"; color: dialog.theme.textPrimary; font.pixelSize: 14 }
                    RowLayout {
                        Repeater {
                            model: [{fps: 15, label: "15 · 节能"}, {fps: 30, label: "30 · 均衡"}, {fps: 60, label: "60 · 流畅"}]
                            AppButton {
                                required property var modelData
                                objectName: "studioMotionFps-" + modelData.fps
                                theme: dialog.theme; text: modelData.label
                                checkable: true; checkedExternallyControlled: true
                                checked: Number(dialog.values.motionFps) === modelData.fps
                                onClicked: dialog.settings.setValue("motionFps", modelData.fps)
                            }
                        }
                    }

                }
            }
        }
        }
        }
        Hint {
            visible: dialog.settings.importBusy || dialog.settings.importError.length > 0
            text: dialog.settings.importBusy ? "正在准备背景图片…" : dialog.settings.importError
            color: dialog.settings.importError.length > 0 ? dialog.theme.danger : dialog.theme.textSecondary
        }
        RowLayout {
            objectName: "studioSettingsFooter"
            Layout.fillWidth: true
            AppButton { objectName: "studioAppearanceReset"; theme: dialog.theme; text: "恢复外观默认"; visible: dialog.page < 4; variant: "ghost"; onClicked: dialog.settings.resetPreview() }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "studioAppearanceCancel"; theme: dialog.theme; text: "取消"; onClicked: dialog.requestClose() }
            AppButton {
                objectName: "studioAppearanceApply"
                theme: dialog.theme; text: "应用"; variant: "primary"
                enabled: !dialog.settings.importBusy
                onClicked: dialog.applySettings()
            }
        }
    }
    FileDialog {
        id: imagePicker
        objectName: "studioBackgroundImagePicker"
        property string target: "editor"
        title: "选择背景图片"
        fileMode: FileDialog.OpenFile
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"]
        onAccepted: dialog.settings.importBackground(target, selectedFile.toString())
    }
    AppColorDialog {
        id: noteColorPicker
        objectName: "studioNoteColorPicker"
        title: "自定义音符颜色"
        theme: dialog.theme
        onAccepted: dialog.settings.applyCustomNoteColor(selectedColor.toString())
    }
    AppColorDialog {
        id: roleNoteColorPicker
        objectName: "studioRoleNoteColorPicker"
        property string roleKey: ""
        title: "设置声部颜色"
        theme: dialog.theme
        onAccepted: dialog.updateRoleColor(roleKey, selectedColor.toString())
    }
    AppColorDialog {
        id: themeColorPicker
        objectName: "studioThemeColorPicker"
        title: "自定义主题颜色"
        theme: dialog.theme
        onAccepted: dialog.settings.applyCustomThemeColor(selectedColor.toString())
    }
}
