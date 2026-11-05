import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

StyledComboBox {
    id: control
    property string providerId: ""
    contentItem: RowLayout {
        spacing: 8
        AIBrandIcon { theme: control.theme; providerId: control.providerId; modelId: control.currentIndex >= 0 && control.model[control.currentIndex] ? control.model[control.currentIndex].id : ""; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
        Text { text: control.displayText; color: control.theme.textPrimary; font: control.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; Layout.fillWidth: true }
    }
    delegate: Basic.ItemDelegate {
        required property int index
        width: control.width - 8
        height: 38
        highlighted: control.highlightedIndex === index
        hoverEnabled: true
        contentItem: RowLayout {
            spacing: 8
            AIBrandIcon { theme: control.theme; providerId: control.providerId; modelId: control.model[index] ? control.model[index].id : ""; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
            Text { text: control.textAt(index); color: control.theme.textPrimary; font: control.font; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: index === control.currentIndex ? "✓" : ""; color: control.theme.textPrimary }
        }
        background: Rectangle { radius: control.theme.radiusS; color: parent.highlighted || parent.hovered ? control.theme.surfaceHover : parent.index === control.currentIndex ? control.theme.surfaceSelected : "transparent" }
    }
}
