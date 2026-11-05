import QtQuick

FocusScope {
    id: control

    required property var theme
    property var model: []
    property int currentIndex: 0
    readonly property bool validModel: isValidModel(model)
    readonly property int segmentCount: validModel ? model.length : 0
    signal activated(int index)

    implicitWidth: 240
    implicitHeight: theme.controlHeight
    width: implicitWidth
    height: implicitHeight
    activeFocusOnTab: true
    Accessible.role: Accessible.Grouping
    Accessible.name: "选项"

    function isValidModel(candidate) {
        if (!candidate || candidate.length < 2 || candidate.length > 4)
            return false
        for (var index = 0; index < candidate.length; index += 1) {
            if (typeof candidate[index] !== "string")
                return false
        }
        return true
    }

    function normalizedIndex(index) {
        return Math.max(0, Math.min(segmentCount - 1, index))
    }

    function select(index, activate) {
        if (!enabled || !validModel)
            return
        currentIndex = normalizedIndex(index)
        if (activate)
            activated(currentIndex)
    }

    onModelChanged: {
        if (validModel && currentIndex !== normalizedIndex(currentIndex))
            currentIndex = normalizedIndex(currentIndex)
    }
    onCurrentIndexChanged: {
        if (validModel && currentIndex !== normalizedIndex(currentIndex))
            currentIndex = normalizedIndex(currentIndex)
    }

    Keys.onPressed: event => {
        if (!enabled || !validModel)
            return
        if (event.key === Qt.Key_Left) {
            event.accepted = true
            select(currentIndex - 1, false)
        } else if (event.key === Qt.Key_Right) {
            event.accepted = true
            select(currentIndex + 1, false)
        } else if (!event.isAutoRepeat && (event.key === Qt.Key_Return
                || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)) {
            event.accepted = true
            select(currentIndex, true)
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: control.theme.radiusM
        color: control.theme.surface
        border.width: 1
        border.color: control.theme.borderStrong

        Rectangle {
            x: 3 + control.currentIndex * (parent.width - 6) / Math.max(1, control.segmentCount)
            y: 3
            width: (parent.width - 6) / Math.max(1, control.segmentCount)
            height: parent.height - 6
            radius: Math.max(2, control.theme.radiusM - 3)
            color: control.theme.surfaceSelected
            visible: control.validModel
        }

        Row {
            anchors.fill: parent
            Repeater {
                model: control.validModel ? control.model : []
                delegate: Item {
                    id: segment
                    objectName: "segment-" + index
                    required property int index
                    required property string modelData
                    width: parent.width / control.segmentCount
                    height: parent.height
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData
                    Accessible.checked: control.currentIndex === index

                    Rectangle {
                        anchors.fill: parent
                        color: "transparent"
                    }

                    Text {
                        anchors.centerIn: parent
                        text: segment.modelData
                        color: control.currentIndex === segment.index
                            ? control.theme.accentPrimary : control.theme.textSecondary
                        font.family: control.theme.fontUi
                        font.pixelSize: 13
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: control.enabled
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            control.forceActiveFocus(Qt.MouseFocusReason)
                            control.select(segment.index, true)
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 1
        color: "transparent"
        radius: control.theme.radiusM - 1
        border.width: control.activeFocus ? 2 : 0
        border.color: control.theme.focusRing
    }
}
