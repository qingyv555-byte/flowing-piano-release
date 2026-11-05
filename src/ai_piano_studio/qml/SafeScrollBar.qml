import QtQuick
import QtQuick.Controls

// Shared scrollbar treatment. Scroll surfaces also reserve theme.scrollbarGutter
// in their content width; this component keeps the thumb readable and away from
// the outer edge without creating a bright line over the content.
ScrollBar {
    id: root

    required property var theme

    policy: ScrollBar.AsNeeded
    width: root.theme.scrollbarWidth
    implicitWidth: root.theme.scrollbarWidth
    anchors.rightMargin: root.theme.scrollbarRightInset

    background: Rectangle {
        radius: width / 2
        color: root.theme.surface
        opacity: root.active ? 0.8 : 0.45
    }

    contentItem: Rectangle {
        implicitWidth: root.theme.scrollbarWidth
        radius: width / 2
        color: root.pressed
            ? root.theme.accentPrimaryPressed
            : (root.hovered ? root.theme.accentPrimaryHover : root.theme.accentPrimary)
        opacity: root.active ? 0.94 : 0.7
    }
}
