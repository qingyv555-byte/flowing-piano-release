import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AppButton {
    id: rhythm
    objectName: "studioMusicSettings"
    required property var bar
    text: editor.bpm + " BPM · " + editor.beatsPerBar + "/" + editor.beatUnit
    variant: "ghost"
    labelPixelSize: 12
    labelWeight: theme.weightRegular
    implicitWidth: normalContentWidth + 20
    onClicked: musicPopup.visible ? musicPopup.close() : musicPopup.open()
    Popup {
        id: musicPopup; objectName: "studioMusicPopup"
        x: 0; y: -height - 8; width: 340; padding: 20
        modal: false; focus: true; closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        onAboutToShow: keyboard.suspendInput("music-popup", true)
        onClosed: keyboard.suspendInput("music-popup", false)
        background: Rectangle { color: rhythm.theme.popupBackground; radius: 12; border.color: rhythm.theme.borderStrong }
        contentItem: ColumnLayout {
            spacing: 16
            Label { text: "节奏"; color: rhythm.theme.textPrimary; font.pixelSize: 16; font.weight: Font.Medium }
            RowLayout {
                Label { text: "速度"; color: rhythm.theme.textPrimary; font.pixelSize: 14; Layout.preferredWidth: 60 }
                SpinBox { objectName: "studioBpm"; from: 60; to: 300; value: editor.bpm; editable: true; onValueModified: editor.setBpm(value); Accessible.name: "速度 BPM" }
                Label { text: "BPM"; color: rhythm.theme.textSecondary; font.pixelSize: 12 }
            }
            RowLayout {
                Label { text: "拍号"; color: rhythm.theme.textPrimary; font.pixelSize: 14; Layout.preferredWidth: 60 }
                StyledComboBox {
                    theme: rhythm.theme
                    objectName: "studioTimeSignature"
                    Accessible.name: "拍号"
                    indicator: DropdownIndicator { theme: rhythm.theme }
                    model: editor.timeSignatureOptions
                    currentIndex: model.indexOf(editor.beatsPerBar + "/" + editor.beatUnit)
                    onActivated: {
                        var signature = model[currentIndex].split("/")
                        editor.setTimeSignature(signature[0], signature[1])
                    }
                }
            }
            AppButton { theme: rhythm.theme; text: "和声建议…"; variant: "ghost"; enabled: rhythm.bar.harmonyAvailable; onClicked: { musicPopup.close(); rhythm.bar.harmonyModeRequested(rhythm) } }
        }
    }
}
