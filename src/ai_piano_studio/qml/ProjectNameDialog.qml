import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "projectNameDialog"

    required property var theme
    required property var editor
    property string dialogTitle: "保存工程"
    property string operationLabel: "保存"
    property string excludePath: ""
    property string fieldObjectName: "projectNameField"
    property string cancelButtonObjectName: "projectNameCancelButton"
    property string confirmButtonObjectName: "projectNameConfirmButton"
    property string suggestion: ""
    property string validationError: ""
    property bool validationValid: false

    signal acceptedName(string name)

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 440
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: theme.space5

    function openFor(title, initialName, operation, excludedPath) {
        dialogTitle = title || "保存工程"
        operationLabel = operation || "保存"
        excludePath = excludedPath || ""
        nameField.text = initialName || "未命名工程"
        refreshValidation()
        open()
    }

    function refreshValidation() {
        if (!editor || typeof editor.validateProjectName !== "function")
            return
        var result = editor.validateProjectName(nameField.text, excludePath)
        validationValid = Boolean(result.valid)
        validationError = result.valid ? "" : String(result.error || "工程名称无效")
        suggestion = String(result.suggestion || "")
    }

    function submit() {
        refreshValidation()
        if (validationValid)
            acceptedName(nameField.text)
    }

    onOpened: Qt.callLater(function() {
        nameField.focusInput(true)
    })
    onClosed: {
        suggestion = ""
        validationError = ""
        validationValid = false
    }

    background: Rectangle {
        radius: root.theme.radiusL
        color: root.theme.elevatedSurface
        border.color: root.theme.borderStrong
        border.width: 1
    }

    header: Label {
        leftPadding: root.theme.space5
        rightPadding: root.theme.space5
        topPadding: root.theme.space5
        text: root.dialogTitle
        color: root.theme.textPrimary
        font.family: root.theme.fontUi
        font.pixelSize: root.theme.typeSection
        font.weight: root.theme.weightBold
    }

    contentItem: ColumnLayout {
        spacing: root.theme.space3

        AppField {
            id: nameField
            objectName: root.fieldObjectName
            Layout.fillWidth: true
            theme: root.theme
            label: "工程名称"
            errorText: root.validationError
            onTextChanged: root.refreshValidation()
            onAccepted: root.submit()
        }

        Label {
            Layout.fillWidth: true
            visible: root.suggestion.length > 0
            text: "建议名称：" + root.suggestion
            color: root.theme.warning
            font.family: root.theme.fontUi
            font.pixelSize: root.theme.typeSmall
            elide: Text.ElideRight
        }
    }

    footer: Item {
        implicitHeight: root.theme.controlHeight + root.theme.space6

        RowLayout {
            anchors.fill: parent
            anchors.margins: root.theme.space3
            spacing: root.theme.space2

            Item { Layout.fillWidth: true }

            AppButton {
                objectName: root.cancelButtonObjectName
                theme: root.theme
                text: "取消"
                onClicked: root.close()
            }
            AppButton {
                objectName: root.confirmButtonObjectName
                theme: root.theme
                text: root.operationLabel
                variant: "primary"
                enabled: root.validationValid
                onClicked: root.submit()
            }
        }
    }
}
