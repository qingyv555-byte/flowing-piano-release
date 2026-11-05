import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.ComboBox {
    id: control
    required property Theme theme
    implicitWidth: 160
    implicitHeight: theme.controlHeight
    hoverEnabled: true
    font.family: theme.fontUi
    font.pixelSize: 14
    leftPadding: 12
    rightPadding: 34
    opacity: enabled ? 1 : 0.45
    Accessible.role: Accessible.ComboBox
    contentItem: Text {
        text: control.displayText
        color: control.theme.textPrimary
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: DropdownIndicator { theme: control.theme }
    background: Rectangle {
        radius: control.theme.radiusM
        color: control.down ? control.theme.surfacePressed
            : control.hovered ? control.theme.surfaceHover : control.theme.inputBackground
        border.color: control.activeFocus ? control.theme.focusRing : control.theme.borderStrong
        border.width: control.activeFocus ? 2 : 1
    }
    delegate: Basic.ItemDelegate {
        id: option
        required property int index
        width: control.width - 8
        height: 38
        highlighted: control.highlightedIndex === index
        hoverEnabled: true
        contentItem: Text {
            text: control.textAt(option.index)
            font.family: control.font.family
            font.pixelSize: control.font.pixelSize
            font.weight: option.index === control.currentIndex ? Font.DemiBold : Font.Normal
            color: control.theme.textPrimary
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: control.theme.radiusS
            color: option.highlighted || option.hovered ? control.theme.surfaceHover
                : option.index === control.currentIndex ? control.theme.surfaceSelected : "transparent"
            border.width: option.index === control.currentIndex ? 1 : 0
            border.color: control.theme.accentSelection
        }
    }
    popup: Basic.Popup {
        y: control.height + 4
        width: control.width
        padding: 4
        margins: 8
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 280)
        background: Rectangle {
            radius: control.theme.radiusM
            color: control.theme.popupBackground
            border.color: control.theme.borderStrong
        }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 0
            Basic.ScrollBar.vertical: SafeScrollBar { theme: control.theme; visible: size < 1 }
        }
    }
}
