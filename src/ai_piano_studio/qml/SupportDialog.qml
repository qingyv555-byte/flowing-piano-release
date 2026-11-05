import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "supportDialog"
    required property Theme theme
    required property var support
    required property var assistant
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent ? parent.width - 32 : 620)
    height: Math.min(650, parent ? parent.height - 32 : 650)
    modal: true
    focus: true
    padding: theme.space5
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    property string feedbackText: ""
    property bool diagnosticsExpanded: false
    readonly property bool supportReady: root.support !== null

    function report(text) {
        feedbackText = text
        feedbackTimer.restart()
    }

    Timer { id: feedbackTimer; interval: 5000; onTriggered: root.feedbackText = "" }

    onOpened: {
        diagnosticsExpanded = false
        bodyScroll.contentItem.contentY = 0
        aboutButton.forceActiveFocus(Qt.PopupFocusReason)
    }
    background: Rectangle {
        radius: theme.radiusL
        color: theme.elevatedSurface
        border.color: theme.borderStrong
        border.width: 1
    }

    header: DialogHeader {
        theme: root.theme
        spacing: theme.space1
        Label {
            text: "帮助与诊断"
            color: theme.textPrimary
            font.family: theme.fontUi
            font.pixelSize: theme.typeSection
            font.weight: theme.weightBold
        }

    }

    contentItem: ScrollView {
        id: bodyScroll
        objectName: "supportBodyScroll"
        clip: true
        rightPadding: root.theme.scrollbarGutter
        contentWidth: availableWidth
        contentHeight: supportContent.implicitHeight
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical: SafeScrollBar {
            objectName: "supportBodyScrollBar"
            theme: root.theme
            parent: bodyScroll
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            visible: size < 1
        }
        ColumnLayout {
            id: supportContent
            width: bodyScroll.availableWidth
            spacing: theme.space2

            Label {
                text: "关于 AI Piano Studio"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
                Layout.topMargin: theme.space2
            }
            Label {
                objectName: "aboutVersionLabel"
                text: "AI Piano Studio\nVersion "
                    + (root.supportReady ? support.version : "unknown")
                    + " " + (root.supportReady && support.beta ? "Beta" : "")
                color: theme.textSecondary
                font.family: theme.fontUi
                font.pixelSize: theme.typeBody
                lineHeight: 1.25
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                objectName: "aboutBuildLabel"
                text: "构建标识：" + (root.supportReady ? support.buildCommit : "unknown")
                color: theme.textMuted
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderSubtle }

            Label {
                text: "诊断与反馈"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: theme.space2
                AppButton {
                    id: aboutButton
                    objectName: "exportDiagnosticsButton"
                    text: "导出诊断信息"
                    theme: root.theme
                    enabled: root.supportReady
                    onClicked: {
                        var path = root.support.exportDiagnostics()
                        root.report(path ? "诊断包已生成到下载文件夹。" : "无法导出诊断信息")
                    }
                    Accessible.name: "导出诊断信息"
                }
                AppButton {
                    objectName: "copyDiagnosticsPreviewButton"
                    text: "复制摘要"
                    theme: root.theme
                    enabled: root.supportReady
                    onClicked: root.report(root.support.copyDiagnosticsPreview()
                        ? "诊断摘要已复制，可先检查后发送。"
                        : "无法复制诊断摘要")
                    Accessible.name: "复制诊断摘要"
                }
                AppButton {
                    objectName: "openLogDirectoryButton"
                    text: "打开日志文件夹"
                    theme: root.theme
                    enabled: root.supportReady
                    onClicked: {
                        Qt.openUrlExternally(root.support.openLogDirectory())
                        root.report("已打开日志文件夹。")
                    }
                    Accessible.name: "打开日志文件夹"
                }
            }
            Label {
                text: "发送前预览（不含 API Key、聊天正文、工程音符或用户文件路径）"
                color: theme.textMuted
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            AppButton {
                objectName: "toggleDiagnosticsPreview"
                theme: root.theme
                text: root.diagnosticsExpanded ? "收起诊断摘要" : "查看诊断摘要"
                onClicked: root.diagnosticsExpanded = !root.diagnosticsExpanded
            }
            ScrollView {
                id: diagnosticsScroll
                visible: root.diagnosticsExpanded
                objectName: "diagnosticsPreviewScroll"
                Layout.fillWidth: true
                Layout.preferredHeight: 132
                clip: true
                rightPadding: root.theme.scrollbarGutter
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical: SafeScrollBar {
                    theme: root.theme
                    parent: diagnosticsScroll
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.right: parent.right
                    visible: size < 1
                }
                TextArea {
                    objectName: "diagnosticsPreview"
                    width: diagnosticsScroll.availableWidth
                    text: root.supportReady ? support.diagnosticsPreview : "暂不可用"
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextArea.Wrap
                    color: theme.textSecondary
                    font.family: theme.numberFont
                    font.pixelSize: theme.typeSmall
                    Accessible.name: "诊断摘要预览"
                    background: Rectangle {
                        radius: theme.radiusM
                        color: theme.surface
                        border.color: theme.borderSubtle
                    }
                }
            }
            AppButton {
                objectName: "feedbackButton"
                text: "反馈问题（生成诊断包）"
                theme: root.theme
                Layout.fillWidth: true
                enabled: root.supportReady
                onClicked: {
                    var path = root.support.exportDiagnostics()
                    root.report(path ? "诊断包已生成，请将它附在反馈中。" : "无法生成诊断包")
                }
                Accessible.name: "反馈问题"
                Accessible.description: "第一版通过诊断包提供反馈，不会上传项目或聊天正文。"
            }
            Label {
                visible: root.feedbackText.length > 0
                text: root.feedbackText
                color: root.feedbackText.indexOf("无法") === 0 ? theme.danger : theme.accentPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderSubtle }

            Label {
                text: "界面设置"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            AppButton {
                objectName: "resetUiSettingsButton"
                text: "重置界面设置"
                theme: root.theme
                Layout.fillWidth: true
                enabled: root.supportReady
                onClicked: resetUiConfirm.open()
                Accessible.name: "重置界面设置"
                Accessible.description: "只重置窗口、面板、折叠和界面偏好，不删除工程或 API Key。"
            }
            Label {
                text: "重置后重新启动软件即可完全应用新的界面布局。"
                color: theme.textMuted
                font.family: theme.fontUi
                font.pixelSize: theme.typeSmall
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderSubtle }

            Label {
                text: "高级设置"
                color: theme.textPrimary
                font.family: theme.fontUi
                font.pixelSize: theme.typePanelTitle
                font.weight: theme.weightSemibold
            }
            AppButton {
                objectName: "restoreDefaultsButton"
                text: "恢复默认设置"
                theme: root.theme
                Layout.fillWidth: true
                enabled: root.supportReady
                onClicked: restoreDefaultsConfirm.open()
                Accessible.name: "恢复默认设置"
            }
        }
    }

    footer: DialogFooter {
        theme: root.theme
        spacing: theme.space2
        Label {
            objectName: "supportScrollHint"
            Layout.fillWidth: true
            text: "向下滚动查看更多"
            visible: bodyScroll.ScrollBar.vertical.position + bodyScroll.ScrollBar.vertical.size < 0.995
            color: root.theme.textMuted
            font.pixelSize: root.theme.typeSmall
        }
        Item { Layout.fillWidth: true }
        AppButton {
            objectName: "supportDialogCloseButton"
            text: "关闭"
            theme: root.theme
            variant: "secondary"
            onClicked: root.close()
        }
    }

    Dialog {
        id: resetUiConfirm
        objectName: "resetUiConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(440, root.width - 24)
        padding: root.theme.space5
        modal: true
        focus: true
        title: "重置界面设置？"
        header: DialogHeader { theme: root.theme; title: resetUiConfirm.title }
        background: Rectangle { color: root.theme.popupBackground; radius: root.theme.radiusL; border.color: root.theme.borderStrong }
        footer: DialogFooter {
            theme: root.theme
            Item { Layout.fillWidth: true }
            AppButton { theme: root.theme; text: "取消"; onClicked: resetUiConfirm.reject() }
            AppButton { theme: root.theme; text: "重置"; onClicked: resetUiConfirm.accept() }
        }
        onAccepted: {
            root.support.resetUiSettings()
            root.report("界面设置已重置，重启后生效。")
        }
        contentItem: Label {
            text: "窗口位置、面板尺寸和折叠状态会恢复默认。工程、音乐和 API Key 不会删除。"
            color: root.theme.textSecondary
            wrapMode: Text.WordWrap
        }
    }

    Dialog {
        id: restoreDefaultsConfirm
        objectName: "restoreDefaultsConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, root.width - 24)
        padding: root.theme.space5
        modal: true
        focus: true
        title: "恢复默认设置？"
        header: DialogHeader { theme: root.theme; title: restoreDefaultsConfirm.title }
        background: Rectangle { color: root.theme.popupBackground; radius: root.theme.radiusL; border.color: root.theme.borderStrong }
        footer: DialogFooter {
            theme: root.theme
            Item { Layout.fillWidth: true }
            AppButton { theme: root.theme; text: "取消"; onClicked: restoreDefaultsConfirm.reject() }
            AppButton { theme: root.theme; text: "恢复"; onClicked: restoreDefaultsConfirm.accept() }
        }
        property bool removeCredentials: false
        onOpened: removeCredentials = false
        onAccepted: {
            root.support.restoreDefaults(removeCredentials)
            root.report(removeCredentials
                ? "默认设置已恢复，保存的 API Key 已删除。"
                : "默认设置已恢复，API Key 保留。")
        }
        contentItem: ColumnLayout {
            spacing: root.theme.space2
            Label {
                text: "这不会删除用户工程。API Key 是否删除由下面的独立选项决定。"
                color: root.theme.textSecondary
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            CheckBox {
                objectName: "removeCredentialsCheckBox"
                text: "同时删除已保存的 API Key"
                checked: restoreDefaultsConfirm.removeCredentials
                onToggled: restoreDefaultsConfirm.removeCredentials = checked
                Accessible.name: "同时删除已保存的 API Key"
            }
        }
    }
}
