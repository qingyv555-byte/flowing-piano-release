import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "playingSettingsDialog"
    required property Theme theme
    parent: Overlay.overlay
    width: Math.min(880, parent ? parent.width - 48 : 880)
    height: Math.min(680, parent ? parent.height - 48 : 680)
    anchors.centerIn: parent
    modal: true; focus: true; padding: 24
    closePolicy: Popup.CloseOnEscape
    title: "演奏与录制"
    header: DialogHeader { theme: dialog.theme; title: dialog.title; padding: dialog.padding; bottomPadding: dialog.theme.space2 }
    palette.window: theme.popupBackground
    palette.windowText: theme.textPrimary
    palette.base: theme.inputBackground
    palette.text: theme.textPrimary
    palette.button: theme.surface
    palette.buttonText: theme.textPrimary
    palette.highlight: theme.accentPrimary
    palette.highlightedText: theme.textOnAccent
    component SectionTab: TabButton {
        id: tab
        implicitHeight: 38
        contentItem: Text {
            text: tab.text; color: dialog.theme.textPrimary
            font.family: dialog.theme.fontUi; font.pixelSize: 14
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: tab.checked ? dialog.theme.selectedBackground : dialog.theme.surface
            border.color: tab.checked || tab.activeFocus ? dialog.theme.accentPrimary : dialog.theme.borderStrong
        }
    }
    property var draft: keyboard.settingsDraft
    property var profile: draft.profiles.find(p => p.id === draft.activeProfile)
    property bool editable: profile && profile.id !== "default"
    // Keep delegates alive when the Python draft snapshot changes after key capture.
    ListModel { id: mappingRows }
    function syncMappings() {
        if (!mappingRows) return
        const rows = profile ? profile.mappings : []
        if (mappingRows.count > rows.length)
            mappingRows.remove(rows.length, mappingRows.count - rows.length)
        for (let i = 0; i < rows.length; ++i) {
            const values = { mappingKey: rows[i].key, mappingPitch: rows[i].pitch,
                             mappingEnabled: rows[i].enabled }
            if (i === mappingRows.count) mappingRows.append(values)
            else for (const role of Object.keys(values)) {
                if (mappingRows.get(i)[role] !== values[role])
                    mappingRows.setProperty(i, role, values[role])
            }
        }
    }
    onProfileChanged: syncMappings()
    Component.onCompleted: syncMappings()
    function edit(change, copyDefault = false) {
        let value = JSON.parse(JSON.stringify(draft))
        let selected = value.profiles.find(p => p.id === value.activeProfile)
        if (copyDefault && selected.id === "default") {
            selected = JSON.parse(JSON.stringify(selected))
            selected.id = "keys-"+Date.now()
            selected.name = "我的按键方案"
            value.profiles.push(selected)
            value.activeProfile = selected.id
        }
        change(value, selected)
        return keyboard.updateSettingsDraft(value)
    }
    function editProfile(change) { return edit(change, true) }
    function capture(row) {
        if (!editable && !editProfile((v,p) => {})) return
        keyboard.captureKey(row)
        forceActiveFocus()
    }
    onAboutToShow: {
        keyboard.beginSettings()
        tabs.currentIndex = 0
        keyboard.setTryKeys(true)
    }
    onClosed: keyboard.cancelSettings()
    background: Rectangle { color: dialog.theme.popupBackground; radius: 16; border.color: dialog.theme.borderStrong }
    contentItem: ColumnLayout {
        spacing: 14
        TabBar {
            id: tabs; Layout.fillWidth: true
            spacing: 8
            background: Item {}
            onCurrentIndexChanged: if (dialog.opened) keyboard.setTryKeys(currentIndex === 0)
            SectionTab { text: "自定义按键" }
            SectionTab { text: "录制" }
            SectionTab { text: "踏板事件" }
        }
        StackLayout {
            currentIndex: tabs.currentIndex; Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                RowLayout {
                    StyledComboBox {
                        theme: dialog.theme
                        id: profileSelector
                        objectName: "playingProfile"; Layout.fillWidth: true
                        Connections {
                            target: profileSelector.popup
                            function onOpened() { keyboard.suspendInput("playing-profile-menu", true) }
                            function onClosed() { keyboard.suspendInput("playing-profile-menu", false) }
                        }
                        model: dialog.draft.profiles; textRole: "name"
                        currentIndex: dialog.draft.profiles.findIndex(p => p.id === dialog.draft.activeProfile)
                        onActivated: dialog.edit(v => v.activeProfile = model[currentIndex].id)
                    }
                    AppButton {
                        theme: dialog.theme; text: "复制为新方案"
                        onClicked: dialog.edit((v,p) => { let copy=JSON.parse(JSON.stringify(p)); copy.id="keys-"+Date.now(); copy.name=p.name+" 副本"; v.profiles.push(copy); v.activeProfile=copy.id })
                    }
                    AppButton {
                        theme: dialog.theme; text: "删除方案"; enabled: dialog.editable
                        onClicked: dialog.edit(v => { v.profiles=v.profiles.filter(p => p.id!==v.activeProfile); v.activeProfile="default" })
                    }
                }
                RowLayout {
                    Label { text: "方案名称"; color: dialog.theme.textSecondary }
                    TextField {
                        objectName:"playingProfileName"
                        Layout.fillWidth: true; text: dialog.profile ? dialog.profile.name : ""
                        maximumLength: 64; onEditingFinished: if (dialog.profile && text !== dialog.profile.name) dialog.editProfile((v,p) => p.name=text)
                    }
                    AppButton { theme: dialog.theme; text: "恢复默认"; onClicked: dialog.edit(v => v.activeProfile="default") }
                }
                Label {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; color: dialog.theme.textSecondary
                    text: "按键试弹 · 点击键位修改"
                }
                ListView {
                    id: keyMappings
                    objectName: "playingMappings"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
                    model: mappingRows
                    ScrollBar.vertical: ScrollBar {}
                    delegate: RowLayout {
                        id: mappingRow
                        required property int mappingKey
                        required property int mappingPitch
                        required property bool mappingEnabled
                        required property int index
                        width: ListView.view.width - 14; height: 40; spacing: 12
                        CheckBox {
                            checked: mappingRow.mappingEnabled
                            Accessible.name: "启用映射"
                            onClicked: dialog.editProfile((v,p) => p.mappings[mappingRow.index].enabled=checked)
                        }
                        AppButton {
                            objectName: "mappingKey-"+mappingRow.index
                            theme: dialog.theme; text: String.fromCharCode(mappingRow.mappingKey)
                            Layout.preferredWidth: 70; onClicked: dialog.capture(mappingRow.index)
                        }
                        Label { text: "→"; color: dialog.theme.textSecondary }
                        StyledComboBox {
                            theme: dialog.theme
                            id: pitchSelector
                            objectName: "mappingPitch-"+mappingRow.index
                            Connections {
                                target: pitchSelector.popup
                                function onOpened() { keyboard.suspendInput("playing-pitch-menu", true) }
                                function onClosed() { keyboard.suspendInput("playing-pitch-menu", false) }
                            }
                            Layout.preferredWidth: 150
                            model: Array.from({length:88}, (_,i) => ["C","C♯","D","D♯","E","F","F♯","G","G♯","A","A♯","B"][(i+21)%12]+(Math.floor((i+21)/12)-1))
                            currentIndex: mappingRow.mappingPitch-21
                            onActivated: dialog.editProfile((v,p) => p.mappings[mappingRow.index].pitch=currentIndex+21)
                        }
                        Label {
                            Layout.fillWidth: true; color: dialog.theme.textPrimary
                            text: "MIDI "+mappingRow.mappingPitch
                        }
                        AppButton { objectName:"mappingAudition-"+mappingRow.index; theme: dialog.theme; text: "试听"; onClicked: keyboard.tapPitch(mappingRow.mappingPitch) }
                        AppButton {
                            theme: dialog.theme; text: "移除"
                            onClicked: dialog.editProfile((v,p) => p.mappings.splice(mappingRow.index,1))
                        }
                    }
                }
                RowLayout {
                    AppButton {
                        theme: dialog.theme; text: "添加映射"
                        onClicked: dialog.editProfile((v,p) => { let keys="QBCIKLMNOPRVXZ1234567890"; for (let key of keys) { let code=key.charCodeAt(0); if (!p.mappings.some(r=>r.key===code) && p.pedalKey!==code) {p.mappings.push({key:code,pitch:60,enabled:true}); break;} } })
                    }
                    AppButton {
                        theme: dialog.theme; text: "踏板键："+(dialog.profile && dialog.profile.pedalKey ? String.fromCharCode(dialog.profile.pedalKey) : "未设置")
                        onClicked: dialog.capture(-1)
                    }
                    AppButton { theme: dialog.theme; text: "清除踏板键"; enabled: dialog.profile && dialog.profile.pedalKey !== null; onClicked: dialog.editProfile((v,p)=>p.pedalKey=null) }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        objectName: "tryPlayingKeys"; theme: dialog.theme; text: keyboard.tryingKeys ? "暂停试弹" : "恢复试弹"
                        variant: keyboard.tryingKeys ? "primary" : "ghost"
                        onClicked: { keyboard.setTryKeys(!keyboard.tryingKeys); forceActiveFocus() }
                    }
                }
                Label {
                    Layout.fillWidth: true; color: dialog.theme.textPrimary
                    text: keyboard.capturingKey ? "请按一个键 · Esc 取消" : keyboard.tryingKeys ? "试弹中 · "+keyboard.heldKeys.join(" · ") : "试弹已暂停"
                }
            }
            ColumnLayout {
                Label { text: "录制设置"; color: dialog.theme.textPrimary; font.pixelSize: 20 }
                Label { Layout.fillWidth: true; text: "从播放头录入当前音乐层，保留已有音符。"; wrapMode: Text.Wrap; color: dialog.theme.textSecondary }
                GridLayout {
                    columns: 2; columnSpacing: 24; rowSpacing: 18; Layout.fillWidth: true
                    Label { text: "预备拍"; color: dialog.theme.textPrimary }
                    StyledComboBox {
                        theme: dialog.theme; model: ["关闭", "1 小节", "2 小节"]; currentIndex: dialog.draft.countInBars; onActivated: dialog.edit(v=>v.countInBars=currentIndex) }
                    Label { text: "节拍器"; color: dialog.theme.textPrimary }
                    StyledComboBox {
                        theme: dialog.theme; model: ["关闭", "录制时", "播放和录制时"]; currentIndex: ["off","record","always"].indexOf(dialog.draft.metronome); onActivated: dialog.edit(v=>v.metronome=["off","record","always"][currentIndex]) }
                    Label { text: "节拍器音量"; color: dialog.theme.textPrimary }
                    Slider { from:0; to:1; value:dialog.draft.metronomeVolume; onMoved:dialog.edit(v=>v.metronomeVolume=value) }
                    Label { text: "录制 / 试弹力度（1–127）"; color: dialog.theme.textPrimary }
                    SpinBox { from:1; to:127; value:dialog.draft.velocity; onValueModified:dialog.edit(v=>v.velocity=value) }
                    Label { text: "按键提示"; color: dialog.theme.textPrimary }
                    CheckBox { text:"在演奏区显示"; checked:dialog.draft.showHints; onClicked:dialog.edit(v=>v.showHints=checked) }
                }
                Item { Layout.fillHeight: true }
                Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: dialog.theme.textSecondary; text: "节拍器仅供监听，不写入作品。" }
            }
            ColumnLayout {
                Label { text: "踏板 · 当前音乐层"; font.pixelSize: 20; color: dialog.theme.textPrimary }
                Label { text: "0–63 抬起，64–127 踩下"; color: dialog.theme.textSecondary }
                RowLayout {
                    TextField { id: pedalTime; placeholderText:"时间（秒）"; text:playback.positionSeconds.toFixed(3); validator:DoubleValidator {bottom:0; decimals:6} }
                    SpinBox { id:pedalValue; from:0; to:127; value:127; editable:true }
                    AppButton { theme:dialog.theme; text:"添加事件"; enabled:!keyboard.recording && pedalTime.acceptableInput; onClicked:editor.addPedalEvent(Number(pedalTime.text),pedalValue.value) }
                }
                ListView {
                    Layout.fillWidth:true; Layout.fillHeight:true; clip:true; model:editor.pedalEvents
                    ScrollBar.vertical:ScrollBar {}
                    delegate:RowLayout {
                        required property var modelData
                        width:ListView.view.width-16; height:42
                        TextField {
                            Layout.preferredWidth:140; text:modelData.time.toFixed(3)
                            validator:DoubleValidator {bottom:0;decimals:6}
                            onEditingFinished:if(acceptableInput)editor.updatePedalEvent(modelData.id,Number(text),modelData.value)
                        }
                        SpinBox { from:0;to:127;value:modelData.value;editable:true;onValueModified:editor.updatePedalEvent(modelData.id,modelData.time,value) }
                        Label { Layout.fillWidth:true; text:(modelData.value>=64?"踩下":"抬起")+(modelData.channel!==null?" · Ch "+(modelData.channel+1):""); color:dialog.theme.textSecondary }
                        AppButton { theme:dialog.theme; text:"删除"; onClicked:editor.deletePedalEvent(modelData.id) }
                    }
                }
            }
        }
        Label { Layout.fillWidth:true; visible:text.length>0; text:keyboard.settingsError; color:dialog.theme.textPrimary; wrapMode:Text.Wrap }
        RowLayout {
            AppButton { theme:dialog.theme; text:"停止所有声音"; onClicked:keyboard.stopAllSounds() }
            Item { Layout.fillWidth:true }
            AppButton { theme:dialog.theme; text:"取消"; onClicked:dialog.close() }
            AppButton { objectName:"applyPlayingSettings"; theme:dialog.theme; text:"保存方案与设置"; variant:"primary"; onClicked:if(keyboard.applySettings()) dialog.close() }
        }
    }
}
