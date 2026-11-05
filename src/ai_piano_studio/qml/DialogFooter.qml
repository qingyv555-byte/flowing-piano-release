import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Control {
    id: root
    required property Theme theme
    default property alias actions: layout.data
    spacing: theme.space2
    padding: theme.space5
    topPadding: theme.space2
    implicitHeight: layout.implicitHeight + topPadding + bottomPadding
    contentItem: RowLayout { id: layout; spacing: root.spacing }
}
