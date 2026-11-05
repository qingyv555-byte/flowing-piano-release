import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    objectName: "harmonySuggestionLane"
    required property var theme
    required property var model
    required property real viewStartSeconds
    required property real pixelsPerSecond
    required property real keyWidth
    required property real playheadSeconds
    required property string statusText
    required property bool analysisPending
    property int selectedMeasureIndex: -1
    property int suppressedPlayheadMeasureIndex: -1
    readonly property real visibleSeconds: Math.max(0.1, (width - keyWidth) / pixelsPerSecond)

    signal candidateRequested(int measureIndex, int candidateIndex)
    signal addRequested(int measureIndex)
    signal dismissRequested(int measureIndex)
    signal whyRequested(int measureIndex)
    signal regenerateRequested(int measureIndex)

    height: 60
    color: theme.toolbar
    clip: true
    visible: typeof harmony !== "undefined" && harmony.mode === "preview"

    Rectangle {
        x: 0
        y: 0
        width: root.keyWidth
        height: parent.height
        color: root.theme.canvasBackground
        border.color: root.theme.borderSubtle
        z: 2
        Text {
            anchors.centerIn: parent
            text: "和弦建议"
            color: root.theme.textSecondary
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeCaption
        }
    }

    Text {
        objectName: "harmonySuggestionEmptyState"
        x: root.keyWidth + root.theme.space3
        width: Math.max(0, root.width - x - root.theme.space3)
        anchors.verticalCenter: parent.verticalCenter
        visible: root.model && root.model.count === 0
        text: root.statusText
        color: root.theme.textSecondary
        font.family: root.theme.fontUi
        font.pixelSize: root.theme.typeCaption
        elide: Text.ElideRight
        Accessible.name: text
    }

    Repeater {
        model: root.model
        delegate: Rectangle {
            id: tile
            required property int measureIndex
            required property real startSeconds
            required property real durationSeconds
            required property bool complete
            required property bool temporary
            required property int selectedCandidateIndex
            required property string chordSymbol
            required property string chineseName
            required property string notesText
            required property var candidates

            objectName: "harmonySuggestionTile-" + measureIndex
            readonly property bool playheadActive: root.playheadSeconds >= startSeconds
                && root.playheadSeconds < startSeconds + durationSeconds
            readonly property bool expanded: root.selectedMeasureIndex === measureIndex
                || (playheadActive && root.suppressedPlayheadMeasureIndex !== measureIndex)
            readonly property string accessibleSummary: "第 " + (measureIndex + 1)
                + " 小节，推荐 " + chordSymbol + " " + chineseName + "，" + notesText
                + (expanded ? "，已展开，按 Enter 收起候选" : "，按 Enter 展开候选")
            readonly property var alternativeIndices: {
                var indices = []
                for (var candidateIndex = 0;
                        candidateIndex < candidates.length && indices.length < 2;
                        ++candidateIndex) {
                    if (candidateIndex !== selectedCandidateIndex)
                        indices.push(candidateIndex)
                }
                return indices
            }
            readonly property int alternativeCount: expanded ? alternativeIndices.length : 0
            readonly property real timelineWidth: durationSeconds * root.pixelsPerSecond
            readonly property real directActionsRequiredWidth:
                primaryRow.anchors.leftMargin + primaryRow.implicitWidth
                + directActions.anchors.leftMargin + directActions.implicitWidth
                + directActions.anchors.rightMargin
            readonly property bool compactActions:
                timelineWidth < directActionsRequiredWidth
            readonly property real compactActionMinimumWidth: 58
            readonly property bool widenedForCompactAction: expanded && compactActions
                && timelineWidth < compactActionMinimumWidth
            readonly property bool consumesSpaceKey: true
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: accessibleSummary
            Accessible.onPressAction: toggleExpanded()
            x: widenedForCompactAction
                ? Math.max(root.keyWidth, Math.min(
                    root.keyWidth + (startSeconds - root.viewStartSeconds) * root.pixelsPerSecond,
                    root.width - width
                ))
                : root.keyWidth + (startSeconds - root.viewStartSeconds) * root.pixelsPerSecond
            width: widenedForCompactAction
                ? compactActionMinimumWidth : timelineWidth
            height: root.height - 8
            y: 4
            z: widenedForCompactAction ? 1 : 0
            visible: startSeconds + durationSeconds > root.viewStartSeconds
                && startSeconds < root.viewStartSeconds + root.visibleSeconds
            radius: root.theme.radiusS
            color: expanded ? root.theme.elevatedSurface : root.theme.canvasBackground
            border.color: activeFocus ? root.theme.focusRing : root.theme.borderSubtle
            border.width: activeFocus ? 2 : 1
            clip: true

            function toggleExpanded() {
                if (expanded) {
                    root.selectedMeasureIndex = -1
                    root.suppressedPlayheadMeasureIndex = playheadActive ? measureIndex : -1
                } else {
                    root.suppressedPlayheadMeasureIndex = -1
                    root.selectedMeasureIndex = measureIndex
                }
            }

            function collapseAndRestoreFocus() {
                compactActionsMenu.close()
                root.selectedMeasureIndex = -1
                root.suppressedPlayheadMeasureIndex = playheadActive ? measureIndex : -1
                Qt.callLater(function() { tile.forceActiveFocus(Qt.TabFocusReason) })
            }

            function openCompactActionsFromSummary() {
                compactActionsMenu.open()
                Qt.callLater(function() {
                    if (candidateMenuOne.visible)
                        candidateMenuOne.forceActiveFocus(Qt.TabFocusReason)
                    else
                        applyMenuItem.forceActiveFocus(Qt.TabFocusReason)
                })
            }

            onPlayheadActiveChanged: {
                if (!playheadActive && root.suppressedPlayheadMeasureIndex === measureIndex)
                    root.suppressedPlayheadMeasureIndex = -1
            }

            Keys.onPressed: event => {
                if (!tile.activeFocus || event.isAutoRepeat)
                    return
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                        || event.key === Qt.Key_Space) {
                    event.accepted = true
                    tile.toggleExpanded()
                }
            }
            Keys.onTabPressed: event => {
                if (tile.activeFocus && tile.expanded && tile.compactActions) {
                    event.accepted = true
                    tile.openCompactActionsFromSummary()
                } else
                    event.accepted = false
            }
            Keys.onEscapePressed: event => {
                if (tile.expanded) {
                    event.accepted = true
                    tile.collapseAndRestoreFocus()
                }
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    tile.forceActiveFocus(Qt.MouseFocusReason)
                    tile.toggleExpanded()
                }
            }

            Row {
                id: primaryRow
                anchors.left: parent.left
                anchors.leftMargin: 7
                anchors.verticalCenter: parent.verticalCenter
                spacing: 5
                Text {
                    text: tile.chordSymbol
                    color: root.theme.textPrimary
                    font.family: root.theme.numberFont
                    font.bold: true
                    font.pixelSize: root.theme.typeBody
                }
                Text {
                    text: tile.chineseName
                    color: root.theme.textSecondary
                    font.family: root.theme.fontUi
                    font.pixelSize: root.theme.typeCaption
                    visible: !tile.expanded
                }
                Text {
                    objectName: "harmonyTemporaryBadge-" + tile.measureIndex
                    text: tile.temporary ? "临时" : "完成"
                    color: root.theme.textSecondary
                    font.family: root.theme.fontUi
                    font.pixelSize: root.theme.typeCaption
                }
            }

            Row {
                id: directActions
                anchors.left: primaryRow.right
                anchors.leftMargin: 10
                anchors.right: parent.right
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                spacing: 5
                visible: tile.expanded && !tile.compactActions

                Repeater {
                    model: tile.alternativeIndices
                    delegate: Button {
                        required property int modelData
                        objectName: "harmonyCandidate-" + tile.measureIndex + "-" + modelData
                        text: tile.candidates[modelData].chordSymbol
                        onClicked: root.candidateRequested(tile.measureIndex, modelData)
                    }
                }
                Button {
                    objectName: "harmonyAdd-" + tile.measureIndex
                    text: tile.temporary ? "立即应用建议" : "添加这个和弦"
                    onClicked: root.addRequested(tile.measureIndex)
                }
                Button {
                    objectName: "harmonyRegenerate-" + tile.measureIndex
                    text: "换一个"
                    Accessible.name: "换一个和弦"
                    onClicked: root.regenerateRequested(tile.measureIndex)
                }
                Button {
                    objectName: "harmonyWhy-" + tile.measureIndex
                    text: "为什么推荐"
                    Accessible.name: "为什么推荐这个和弦"
                    onClicked: root.whyRequested(tile.measureIndex)
                }
                Button {
                    objectName: "harmonyDismiss-" + tile.measureIndex
                    text: "关闭本小节"
                    Accessible.name: "关闭本小节预览"
                    onClicked: root.dismissRequested(tile.measureIndex)
                }
            }

            Button {
                id: compactActions
                objectName: "harmonyActionsMenu-" + tile.measureIndex
                anchors.right: parent.right
                anchors.rightMargin: 5
                anchors.verticalCenter: parent.verticalCenter
                width: 48
                height: parent.height - 10
                visible: tile.expanded && tile.compactActions
                text: "操作"
                z: 2
                onClicked: compactActionsMenu.open()

                AppMenu {

                    theme: root.theme
                    id: compactActionsMenu
                    y: compactActions.height
                    AppMenuItem {
                        theme: root.theme
                        id: candidateMenuOne
                        readonly property int candidateIndex: tile.alternativeIndices.length > 0
                            ? tile.alternativeIndices[0] : -1
                        objectName: "harmonyCandidateMenu-" + tile.measureIndex + "-" + candidateIndex
                        visible: candidateIndex >= 0
                        text: visible ? "换成 " + tile.candidates[candidateIndex].chordSymbol : ""
                        Accessible.name: visible
                            ? "替代候选 " + tile.candidates[candidateIndex].chordSymbol : ""
                        onTriggered: root.candidateRequested(tile.measureIndex, candidateIndex)
                        Keys.onTabPressed: event => {
                            event.accepted = true
                            if (candidateMenuTwo.visible)
                                candidateMenuTwo.forceActiveFocus(Qt.TabFocusReason)
                            else
                                applyMenuItem.forceActiveFocus(Qt.TabFocusReason)
                        }
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            tile.collapseAndRestoreFocus()
                        }
                    }
                    AppMenuItem {
                        theme: root.theme
                        id: candidateMenuTwo
                        readonly property int candidateIndex: tile.alternativeIndices.length > 1
                            ? tile.alternativeIndices[1] : -1
                        objectName: "harmonyCandidateMenu-" + tile.measureIndex + "-" + candidateIndex
                        visible: candidateIndex >= 0
                        text: visible ? "换成 " + tile.candidates[candidateIndex].chordSymbol : ""
                        Accessible.name: visible
                            ? "替代候选 " + tile.candidates[candidateIndex].chordSymbol : ""
                        onTriggered: root.candidateRequested(tile.measureIndex, candidateIndex)
                        Keys.onTabPressed: event => {
                            event.accepted = true
                            applyMenuItem.forceActiveFocus(Qt.TabFocusReason)
                        }
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            tile.collapseAndRestoreFocus()
                        }
                    }
                    AppMenuItem {
                        theme: root.theme
                        id: applyMenuItem
                        objectName: "harmonyApplyMenu-" + tile.measureIndex
                        text: tile.temporary ? "立即应用建议" : "添加这个和弦"
                        Accessible.name: text
                        onTriggered: root.addRequested(tile.measureIndex)
                        Keys.onTabPressed: event => {
                            event.accepted = true
                            regenerateMenuItem.forceActiveFocus(Qt.TabFocusReason)
                        }
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            tile.collapseAndRestoreFocus()
                        }
                    }
                    AppMenuItem {
                        theme: root.theme
                        id: regenerateMenuItem
                        objectName: "harmonyRegenerateMenu-" + tile.measureIndex
                        text: "换一个和弦"
                        Accessible.name: text
                        onTriggered: root.regenerateRequested(tile.measureIndex)
                        Keys.onTabPressed: event => {
                            event.accepted = true
                            whyMenuItem.forceActiveFocus(Qt.TabFocusReason)
                        }
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            tile.collapseAndRestoreFocus()
                        }
                    }
                    AppMenuItem {
                        theme: root.theme
                        id: whyMenuItem
                        objectName: "harmonyWhyMenu-" + tile.measureIndex
                        text: "为什么推荐这个和弦"
                        Accessible.name: text
                        onTriggered: root.whyRequested(tile.measureIndex)
                        Keys.onTabPressed: event => {
                            event.accepted = true
                            dismissMenuItem.forceActiveFocus(Qt.TabFocusReason)
                        }
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            tile.collapseAndRestoreFocus()
                        }
                    }
                    AppMenuItem {
                        theme: root.theme
                        id: dismissMenuItem
                        objectName: "harmonyDismissMenu-" + tile.measureIndex
                        text: "关闭本小节预览"
                        Accessible.name: text
                        onTriggered: root.dismissRequested(tile.measureIndex)
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            tile.collapseAndRestoreFocus()
                        }
                    }
                }
            }
        }
    }
}
