import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Dialog padding applies to its body, not to custom headers or footers.
Control {
    id: root
    required property Theme theme
    property string title: ""
    default property alias contents: layout.data
    spacing: theme.space1
    padding: theme.space5
    bottomPadding: theme.space2
    implicitHeight: layout.implicitHeight + topPadding + bottomPadding
    contentItem: ColumnLayout {
        id: layout
        spacing: root.spacing
        Label {
            visible: root.title.length > 0
            Layout.fillWidth: true
            text: root.title
            wrapMode: Text.WordWrap
            color: root.theme.textPrimary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSection
            font.weight: root.theme.weightBold
        }
    }
}
