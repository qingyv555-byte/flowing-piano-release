import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var theme
    property string title: ""
    property string subtitle: ""
    property string badge: ""
    property bool expanded: true
    default property alias contentData: body.data

    signal toggled(bool expanded)

    implicitWidth: 240
    implicitHeight: header.implicitHeight
        + (root.expanded ? root.theme.space2 + body.implicitHeight : 0)
    // Keep the window-level Space-to-play shortcut from stealing the card's
    // keyboard toggle when this accessible section owns focus.
    readonly property bool consumesSpaceKey: true

    Accessible.role: Accessible.Button
    Accessible.name: root.title + (root.badge.length > 0 ? "，" + root.badge : "")
    Accessible.description: root.subtitle
    Accessible.onPressAction: root.toggleSection()
    activeFocusOnTab: root.enabled

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Space || event.key === Qt.Key_Return
                || event.key === Qt.Key_Enter) {
            root.toggleSection()
            event.accepted = true
        }
    }

    function toggleSection() {
        if (!root.enabled)
            return
        root.expanded = !root.expanded
        root.toggled(root.expanded)
    }

    Rectangle {
        id: header
        objectName: "propertySectionHeader-" + root.title
        width: parent.width
        implicitHeight: Math.max(root.theme.controlHeight, headerContent.implicitHeight + root.theme.space3)
        height: implicitHeight
        radius: root.theme.radiusM
        color: headerMouse.containsMouse ? root.theme.surfaceHover : root.theme.surface
        border.width: root.activeFocus ? 2 : 0
        border.color: root.activeFocus ? root.theme.focusRing : root.theme.borderSubtle

        RowLayout {
            id: headerContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: root.theme.space3
            anchors.rightMargin: root.theme.space3
            spacing: root.theme.space2

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Label {
                    text: root.title
                    color: root.theme.textPrimary
                    font.family: root.theme.fontUi
                    font.pixelSize: root.theme.typeBodyStrong
                    font.weight: root.theme.weightSemibold
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Label {
                    text: root.subtitle
                    visible: text.length > 0
                    color: root.theme.textSecondary
                    font.family: root.theme.fontUi
                    font.pixelSize: root.theme.typeCaption
                    wrapMode: Text.NoWrap
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }

            Rectangle {
                visible: root.badge.length > 0
                implicitWidth: badgeLabel.implicitWidth + root.theme.space3
                implicitHeight: root.theme.lineSmall + root.theme.space1
                radius: height / 2
                color: root.theme.surfaceSelected
                border.width: 1
                border.color: root.theme.borderStrong

                Label {
                    id: badgeLabel
                    anchors.centerIn: parent
                    text: root.badge
                    color: root.theme.accentSelection
                    font.family: root.theme.numberFont
                    font.pixelSize: root.theme.typeSmall
                }
            }

            Label {
                text: root.expanded ? "⌃" : "⌄"
                color: root.theme.textSecondary
                font.family: root.theme.fontSymbol
                font.pixelSize: root.theme.typeSection
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                Layout.preferredWidth: root.theme.iconMedium
            }
        }

        MouseArea {
            id: headerMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.toggleSection()
        }
    }

    ColumnLayout {
        id: body
        objectName: "propertySectionBody-" + root.title
        width: parent.width
        y: header.height + root.theme.space2
        height: implicitHeight
        visible: root.expanded
        spacing: root.theme.space2
    }
}
