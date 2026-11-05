import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Window {
    id: root
    objectName: "creativeOverlay"
    required property var creative
    width: 440; height: 330
    visible: false
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    color: theme.popupBackground
    Theme { id: theme; studio: true }
    Connections {
        target: root.creative
        function onOverlayRequested() { root.show() }
        function onClearInputRequested() { root.hide() }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 10
        Label { text: "灵感速记"; font.pixelSize: 19; color: theme.textPrimary }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: creative.overlayStatus; color: theme.textPrimary }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: creative.captureSource; color: theme.textSecondary }
        Image { source: creative.imagePreview; cache: false; fillMode: Image.PreserveAspectFit; Layout.fillWidth: true; Layout.preferredHeight: source.toString().length ? 70 : 0 }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true
            Label { width: parent.width; text: creative.capturedText; wrapMode: Text.Wrap; color: theme.textPrimary }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "发送至：" + creative.destination; color: theme.textSecondary; font.pixelSize: 11 }
        CheckBox { id: remember; text: "此电脑上允许该应用和连接以后直接发送"; checked: false }
        RowLayout {
            Button { text: "确认发送"; enabled: creative.captureReady; onClicked: creative.sendCaptured(remember.checked) }
            Button { text: "编辑 / 查看结果"; onClicked: { root.hide(); creative.editCaptured() } }
            Button { text: "取消"; onClicked: { creative.cancel(); root.hide() } }
        }
    }
}
