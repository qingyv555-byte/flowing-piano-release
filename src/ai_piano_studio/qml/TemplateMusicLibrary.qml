import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: library
    objectName: "templateMusicLibrary"

    required property Theme theme
    property var service: null
    readonly property int contentMaxWidth: 1180
    readonly property int columnCount: width >= 1600 ? 3 : (width >= 1080 ? 2 : 1)
    focus: visible

    signal backRequested()

    function focusBack() { backButton.forceActiveFocus() }

    onVisibleChanged: {
        if (visible)
            Qt.callLater(focusBack)
    }

    Keys.onEscapePressed: event => {
        event.accepted = true
        library.backRequested()
    }

    Rectangle {
        anchors.fill: parent
        color: library.theme.canvas
    }

    Rectangle {
        id: topBand
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 76
        color: library.theme.elevatedSurface
        border.color: library.theme.borderSubtle

        RowLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 32, library.contentMaxWidth)
            spacing: 14

            AppButton {
                id: backButton
                objectName: "templateLibraryBackButton"
                text: "返回"
                variant: "ghost"
                theme: library.theme
                onClicked: library.backRequested()
                KeyNavigation.tab: templateGrid
                Accessible.name: "返回启动页"
            }

            ColumnLayout {
                spacing: 2
                Layout.fillWidth: true

                Label {
                    text: "示例乐曲"
                    color: library.theme.textPrimary
                    font.pixelSize: 22
                    font.bold: true
                }
                Label {
                    text: "试听示例，然后开始改编"
                    color: library.theme.textSecondary
                    font.pixelSize: 12
                }
            }

            Label {
                text: (library.service ? library.service.templates.length : 0) + " 首内置片段"
                color: library.theme.textMuted
                font.pixelSize: 12
            }
        }
    }

    GridView {
        id: templateGrid
        objectName: "templateGrid"
        anchors.top: topBand.bottom
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 32, library.contentMaxWidth)
        topMargin: 20
        bottomMargin: 24
        leftMargin: 0
        rightMargin: 0
        cellWidth: width / library.columnCount
        cellHeight: 318
        model: library.service ? library.service.templates : []
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        keyNavigationEnabled: true
        snapMode: GridView.SnapToRow
        delegate: Item {
            required property var modelData
            width: templateGrid.cellWidth
            height: templateGrid.cellHeight

            Rectangle {
                id: card
                objectName: "templateCard-" + modelData.id
                anchors.fill: parent
                anchors.margins: 8
                radius: library.theme.radiusL
                color: library.theme.elevatedSurface
                border.width: library.service && library.service.activeTemplateId === modelData.id
                    && library.service.previewing ? 2 : 1
                border.color: library.service && library.service.activeTemplateId === modelData.id
                    && library.service.previewing ? library.theme.accentPrimary : library.theme.borderSubtle

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8

                    Image {
                        objectName: "templateThumbnail-" + modelData.id
                        Layout.fillWidth: true
                        Layout.preferredHeight: 128
                        source: modelData.thumbnailSource
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: true
                        sourceSize.width: 640
                        sourceSize.height: 320
                    }

                    Label {
                        text: modelData.name
                        color: library.theme.textPrimary
                        font.pixelSize: 17
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Label {
                        text: modelData.composer + " · " + modelData.arrangement
                        color: library.theme.textSecondary
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Label {
                        text: modelData.tags.join(" · ")
                        color: library.theme.textMuted
                        font.pixelSize: 11
                        Layout.fillWidth: true
                    }

                    Label {
                        text: Math.round(modelData.durationSeconds) + " 秒  ·  " + modelData.noteCount + " 个音符"
                        color: library.theme.textMuted
                        font.pixelSize: 11
                    }

                    Item { Layout.fillHeight: true }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        AppButton {
                            objectName: "templatePreviewButton-" + modelData.id
                            Layout.fillWidth: true
                            text: library.service && library.service.activeTemplateId === modelData.id && library.service.previewing
                                ? "暂停" : "试听"
                            variant: "secondary"
                            theme: library.theme
                            onClicked: { if (library.service) library.service.preview(modelData.id) }
                            Accessible.name: (library.service && library.service.activeTemplateId === modelData.id && library.service.previewing
                                ? "暂停试听 " : "试听 ") + modelData.name
                        }

                        AppButton {
                            objectName: "templateAdaptButton-" + modelData.id
                            Layout.fillWidth: true
                            text: "开始改编"
                            variant: "primary"
                            theme: library.theme
                            onClicked: { if (library.service) library.service.adapt(modelData.id) }
                            Accessible.name: "基于 " + modelData.name + " 开始改编"
                        }
                    }

                    ProgressBar {
                        objectName: "templatePreviewProgress-" + modelData.id
                        Layout.fillWidth: true
                        visible: library.service && library.service.activeTemplateId === modelData.id
                        from: 0
                        to: Math.max(0.1, Number(modelData.durationSeconds))
                        value: Math.min(to, library.service ? library.service.previewPosition : 0)
                        Accessible.name: "试听进度 " + modelData.name
                    }
                }
            }
        }

        Label {
            anchors.centerIn: parent
            visible: !library.service || library.service.templates.length === 0
            text: "内置音乐暂时无法加载"
            color: library.theme.textMuted
            font.pixelSize: 14
        }
    }
}
