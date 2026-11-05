import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "ToneDescription.js" as ToneDescription

ColumnLayout {
    id: pane
    required property Theme theme
    required property int page
    required property var draft
    property bool toneAvailable: false
    property bool recoveryAvailable: false
    signal edited(string key, var value)
    signal recoveryRequested()
    signal tonePreviewRequested()
    spacing: 14

    component Caption: Label {
        color: pane.theme.textPrimary
        font.family: pane.theme.fontUi
        font.pixelSize: 14
        Layout.fillWidth: true
    }
    component Choice: StyledComboBox {
        theme: pane.theme
        required property string field
        required property var values
        Layout.fillWidth: true
        currentIndex: values.indexOf(pane.draft[field])
        // Existing profiles may contain valid values between these presets.
        // Show their real value instead of silently displaying the first item.
        displayText: currentIndex >= 0 ? currentText : String(pane.draft[field])
            + (field === "rate" ? "×" : (field === "noteLength" || field === "previewLength" ? " 拍" : ""))
        onActivated: pane.edited(field, values[currentIndex])
        indicator: DropdownIndicator { theme: pane.theme }
    }
    Caption {
        text: ["播放与声音", "编辑偏好", "保存与恢复"][pane.page - 4] || ""
        font.pixelSize: 18; font.weight: Font.DemiBold
    }
    ColumnLayout {
        visible: pane.page === 4; Layout.fillWidth: true; spacing: 12
        Caption { text: "主音量 · " + Math.round(pane.draft.volume * 100) + "%" }
        StyledSlider {
            objectName: "preferencesVolume"; theme: pane.theme; Layout.fillWidth: true
            from: 0; to: 1; modelValue: pane.draft.volume; accessibleName: "主音量"
            onValueEdited: value => pane.edited("volume", value)
        }
        RowLayout {
            Layout.fillWidth: true
            Caption { objectName: "preferencesToneDescription"; text: "钢琴音色 · " + ToneDescription.describe(pane.draft.tone) }
            AppButton { objectName: "preferencesTonePreview"; theme: pane.theme; text: "试听"; enabled: pane.toneAvailable; onClicked: pane.tonePreviewRequested() }
        }
        StyledSlider {
            objectName: "preferencesTone"; theme: pane.theme; Layout.fillWidth: true
            from: 0; to: 1; modelValue: pane.draft.tone; enabled: pane.toneAvailable
            accessibleName: "钢琴音色"; onValueEdited: value => pane.edited("tone", value)
        }
        RowLayout {
            Layout.fillWidth: true
            Caption { text: "柔和"; color: pane.theme.textSecondary }
            Caption { text: "明亮"; horizontalAlignment: Text.AlignRight; color: pane.theme.textSecondary }
        }
        Caption { visible: !pane.toneAvailable; text: "钢琴音色暂不可用"; color: pane.theme.textSecondary }
        ColumnLayout {
            visible: !!pane.draft.soundParametersAvailable
            Layout.fillWidth: true; spacing: 12
            Caption { text: "当前音源 · " + (pane.draft.soundId === "upright-sfz" ? "立式钢琴" : "Salamander") }
            Caption { text: "音源音量 · " + Number(pane.draft.sourceTrim).toFixed(1) + " dB" }
            StyledSlider {
                objectName: "preferencesSourceTrim"; theme: pane.theme; Layout.fillWidth: true
                from: -9; to: 9; modelValue: pane.draft.sourceTrim || 0; accessibleName: "音源音量"
                onValueEdited: value => pane.edited("sourceTrim", value)
            }
            Repeater {
                model: [{key: "response", label: "力度响应"}, {key: "reverb", label: "回响"}, {key: "width", label: "空间"}]
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true; spacing: 6
                    Caption { text: modelData.label + " · " + Math.round((pane.draft[modelData.key] || 0) * 100) + "%" }
                    StyledSlider {
                        objectName: "preferencesSound_" + modelData.key; theme: pane.theme; Layout.fillWidth: true
                        from: 0; to: 1; modelValue: pane.draft[modelData.key] || 0; accessibleName: modelData.label
                        onValueEdited: value => pane.edited(modelData.key, value)
                    }
                }
            }
        }
        Caption { text: "播放速度" }
        Choice { objectName: "preferencesRate"; field: "rate"; values: [0.5, 0.75, 1, 1.25, 1.5, 2]; model: ["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"]; Accessible.name: "播放速度" }
    }
    ColumnLayout {
        visible: pane.page === 5; Layout.fillWidth: true; spacing: 12
        Caption { text: "新音符时长" }
        Choice { objectName: "preferencesNoteLength"; field: "noteLength"; values: [0.25, 0.5, 1, 2, 4]; model: ["1/4 拍", "1/2 拍", "1 拍", "2 拍", "4 拍"]; Accessible.name: "新音符时长" }
        Caption { text: "新音符力度" }
        SpinBox { objectName: "preferencesVelocity"; from: 1; to: 127; editable: true; value: pane.draft.velocity; Accessible.name: "新音符力度"; onValueModified: pane.edited("velocity", value) }
        Caption { text: "拖动试听" }
        Choice { objectName: "preferencesDragPreview"; field: "dragPreview"; values: ["off", "grid", "continuous"]; model: ["关闭", "按网格", "连续"]; Accessible.name: "拖动试听" }
        Caption { text: "琴键试听时长" }
        Choice { objectName: "preferencesPreviewLength"; field: "previewLength"; values: [0.25, 0.5, 1, 2, 4]; model: ["1/4 拍", "1/2 拍", "1 拍", "2 拍", "4 拍"]; Accessible.name: "琴键试听时长" }
    }
    ColumnLayout {
        visible: pane.page === 6; Layout.fillWidth: true; spacing: 12
        CheckBox { objectName: "preferencesAutosave"; text: "自动保存"; checked: pane.draft.autosave; onClicked: pane.edited("autosave", checked) }
        Caption { text: "停止操作后等待" }
        Choice { objectName: "preferencesSaveDelay"; field: "saveDelay"; values: [15, 30, 60, 120, 300]; model: ["15 秒", "30 秒", "1 分钟", "2 分钟", "5 分钟"]; enabled: pane.draft.autosave; Accessible.name: "自动保存等待时间" }
        Caption { text: "新作品需先手动保存一次。"; color: pane.theme.textSecondary; wrapMode: Text.Wrap }
        Rectangle { Layout.fillWidth: true; height: 1; color: pane.theme.borderSubtle }
        Caption { text: pane.recoveryAvailable ? "有可恢复的作品" : "暂无恢复副本"; color: pane.theme.textSecondary }
        AppButton { objectName: "preferencesRecovery"; theme: pane.theme; text: "查看恢复副本"; enabled: pane.recoveryAvailable; onClicked: pane.recoveryRequested() }
    }
}
