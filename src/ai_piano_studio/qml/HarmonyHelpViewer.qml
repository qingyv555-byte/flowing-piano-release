import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: viewer
    objectName: "harmonyHelpViewer"
    required property var theme
    required property var helpContent
    signal closeRequested()
    property bool articleScrollUpdatesSection: false

    color: theme.canvasBackground

    function articleIndexForSection(sectionId) {
        var sections = helpContent ? helpContent.sections : []
        for (var index = 0; index < sections.length; ++index) {
            if (sections[index].id === sectionId)
                return index
        }
        return -1
    }

    function scrollArticleToSection(sectionId) {
        var index = articleIndexForSection(sectionId)
        if (index < 0)
            return
        article.positioning = true
        article.positionViewAtIndex(index, ListView.Beginning)
        Qt.callLater(function() {
            article.positioning = false
            article.updateVisibleSection()
        })
    }

    function selectArticleSection(sectionId) {
        if (!helpContent)
            return
        helpContent.openSection(sectionId)
        scrollArticleToSection(sectionId)
    }

    function updateVisibleArticleSection(sectionId) {
        if (!helpContent || helpContent.currentSectionId === sectionId)
            return
        articleScrollUpdatesSection = true
        helpContent.setVisibleSection(sectionId)
        articleScrollUpdatesSection = false
    }

    onVisibleChanged: {
        if (visible) {
            Qt.callLater(function() {
                viewer.scrollArticleToSection(helpContent.currentSectionId)
                closeButton.forceActiveFocus(Qt.TabFocusReason)
            })
        }
    }

    Connections {
        target: viewer.helpContent
        function onCurrentSectionChanged() {
            if (viewer.visible && !viewer.articleScrollUpdatesSection)
                viewer.scrollArticleToSection(viewer.helpContent.currentSectionId)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            color: viewer.theme.surface
            border.color: viewer.theme.borderSubtle

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: viewer.theme.space5
                anchors.rightMargin: viewer.theme.space5
                Label {
                    text: "和弦入门指南"
                    color: viewer.theme.textPrimary
                    font.family: viewer.theme.fontUi
                    font.pixelSize: viewer.theme.typePageTitle
                    font.weight: viewer.theme.weightBold
                    Layout.fillWidth: true
                }
                AppButton {
                    id: closeButton
                    objectName: "harmonyHelpCloseButton"
                    text: "返回编辑器"
                    theme: viewer.theme
                    onClicked: viewer.closeRequested()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            ListView {
                id: sectionList
                objectName: "harmonyHelpSectionList"
                Layout.preferredWidth: Math.min(280, viewer.width * 0.28)
                Layout.fillHeight: true
                model: viewer.helpContent ? viewer.helpContent.sections : []
                clip: true
                rightMargin: viewer.theme.scrollbarGutter
                ScrollBar.vertical: SafeScrollBar { theme: viewer.theme }
                currentIndex: {
                    var sections = viewer.helpContent ? viewer.helpContent.sections : []
                    for (var i = 0; i < sections.length; ++i)
                        if (sections[i].id === viewer.helpContent.currentSectionId)
                            return i
                    return sections.length > 0 ? 0 : -1
                }
                delegate: ItemDelegate {
                    required property var modelData
                    objectName: "harmonyHelpToc-" + modelData.id
                    width: Math.max(0, ListView.view.width - viewer.theme.scrollbarGutter)
                    text: modelData.title
                    highlighted: modelData.id === viewer.helpContent.currentSectionId
                    activeFocusOnTab: true
                    Accessible.name: text
                    contentItem: Text {
                        objectName: "harmonyHelpTocText-" + modelData.id
                        text: parent.text
                        color: theme.textPrimary
                        font.family: theme.fontUi
                        font.pixelSize: theme.typeBody
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                    background: Rectangle {
                        objectName: "harmonyHelpTocBackground-" + modelData.id
                        property color borderColor: parent.activeFocus ? theme.focusRing : "transparent"
                        radius: theme.radiusM
                        color: parent.highlighted
                            ? theme.surfaceSelected
                            : (parent.hovered ? theme.surfaceHover : "transparent")
                        border.color: borderColor
                        border.width: parent.activeFocus ? 2 : 0
                    }
                    onClicked: viewer.selectArticleSection(modelData.id)
                }
            }

            Rectangle {
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                color: viewer.theme.borderSubtle
            }

            ListView {
                id: article
                objectName: "harmonyHelpArticle"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: viewer.helpContent ? viewer.helpContent.sections : []
                property bool positioning: false
                rightMargin: viewer.theme.scrollbarGutter

                function updateVisibleSection() {
                    if (positioning || !viewer.helpContent)
                        return
                    var index = indexAt(1, contentY + 1)
                    var sections = viewer.helpContent.sections
                    if (index >= 0 && index < sections.length)
                        viewer.updateVisibleArticleSection(sections[index].id)
                }

                onContentYChanged: updateVisibleSection()
                ScrollBar.vertical: SafeScrollBar { theme: viewer.theme }

                delegate: Item {
                    id: articleSection
                    required property var modelData
                    objectName: "harmonyHelpArticleSection-" + modelData.id
                    width: Math.max(0, ListView.view.width - viewer.theme.scrollbarGutter)
                    height: articleText.height + viewer.theme.space6 * 2

                    TextEdit {
                        id: articleText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: viewer.theme.space6
                        height: contentHeight
                        text: modelData.markdown
                        textFormat: TextEdit.MarkdownText
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        color: viewer.theme.textPrimary
                        font.family: viewer.theme.fontUi
                        font.pixelSize: viewer.theme.typeBody
                        onLinkActivated: link => {
                            if (viewer.helpContent)
                                viewer.helpContent.activateLink(link)
                        }
                    }
                }
            }
        }
    }
}
