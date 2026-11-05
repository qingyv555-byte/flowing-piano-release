from PySide6.QtCore import QObject, Qt, QPointF, QPoint
from PySide6.QtTest import QTest
from pathlib import Path
import os
import pytest
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_workflows import qml, no_audio_device, click, click_item, find_item


def test_default_profile_row_audition_and_try_button_produce_pcm(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1366,768)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.findChild(QObject,'playingSettingsButton') is not None)
    playback.setVolume(.61)
    click(root,'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    keyboard=engine.rootContext().contextProperty('keyboard')
    assert keyboard.tryingKeys
    click(root,'playingProfile')
    QTest.keyClick(root,Qt.Key_A)
    assert not keyboard.heldKeys and 'playing-profile-menu' in keyboard._suspended
    QTest.keyClick(root,Qt.Key_Escape)
    qtbot.waitUntil(lambda:'playing-profile-menu' not in keyboard._suspended)
    mixer=playback.piano_source.mixer
    click_item(root, find_item(root,'mappingAudition-0'))
    assert any(mixer.mix_frames(1024)), (keyboard._owned_voice_ids, playback.volume)
    keyboard.releaseAll()
    for _ in range(30): mixer.mix_frames(1024)
    click(root,'tryPlayingKeys')
    assert not keyboard.tryingKeys
    click(root,'tryPlayingKeys')
    assert keyboard.tryingKeys
    QTest.keyPress(root,Qt.Key_A)
    assert keyboard.heldKeys==['C4'], (keyboard._suspended, keyboard._typing_focused())
    assert any(mixer.mix_frames(1024))
    QTest.keyRelease(root,Qt.Key_A)
    assert not editor.state.notes
    dialog.close()


def test_default_key_edits_create_draft_copy_and_resume_trying(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1366,768)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'playingSettingsButton') is not None)
    click(root,'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    keyboard=engine.rootContext().contextProperty('keyboard')
    original=keyboard.preferences.snapshot()
    button=find_item(root,'mappingKey-0')
    assert button.isEnabled()
    click_item(root,button)
    assert keyboard.capturingKey
    QTest.keyClick(root,Qt.Key_Q)
    assert not keyboard.capturingKey and keyboard.tryingKeys
    draft=keyboard.settingsDraft
    assert draft['activeProfile']!='default'
    assert draft['profiles'][0]==original['profiles'][0]
    assert draft['profiles'][-1]['mappings'][0]['key']==ord('Q')
    QTest.keyPress(root,Qt.Key_Q)
    assert keyboard.heldKeys==['C4']
    QTest.keyRelease(root,Qt.Key_Q)
    field=root.findChild(QObject,'playingProfileName')
    field.forceActiveFocus()
    QTest.keyPress(root,Qt.Key_Q)
    assert not keyboard.heldKeys
    QTest.keyRelease(root,Qt.Key_Q)
    dialog.close()
    assert keyboard.preferences.snapshot()==original
    assert not keyboard.tryingKeys and not editor.state.notes


def test_mapping_edit_preserves_scrolled_row_position(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(900,700)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'playingSettingsButton') is not None)
    click(root,'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    mappings=root.findChild(QObject,'playingMappings')
    qtbot.waitUntil(lambda:mappings.property('contentHeight') > mappings.height())
    mappings.setProperty('contentY', min(140, mappings.property('contentHeight')-mappings.height()))
    before=mappings.property('contentY')
    assert before > 40
    old_pitch=engine.rootContext().contextProperty('keyboard').settingsDraft['profiles'][0]['mappings'][8]['pitch']
    selector=find_item(root,'mappingPitch-8')
    assert selector is not None and selector.isVisible()
    click_item(root,selector)
    QTest.keyClick(root,Qt.Key_Down)
    QTest.keyClick(root,Qt.Key_Enter)
    QTest.qWait(200)
    assert abs(mappings.property('contentY')-before) < 1
    draft=engine.rootContext().contextProperty('keyboard').settingsDraft
    active=next(profile for profile in draft['profiles'] if profile['id']==draft['activeProfile'])
    assert active['mappings'][8]['pitch'] == old_pitch + 1
    qml(dialog,"editProfile((v,p)=>p.mappings[9].enabled=!p.mappings[9].enabled); true")
    QTest.qWait(200)
    assert abs(mappings.property('contentY')-before) < 1


def test_clicking_same_mapping_key_again_cancels_capture(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(900,700)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'playingSettingsButton') is not None)
    click(root,'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    keyboard=engine.rootContext().contextProperty('keyboard')
    button=find_item(root,'mappingKey-0')
    click_item(root,button)
    assert keyboard.capturingKey
    click_item(root,find_item(root,'mappingKey-0'))
    assert not keyboard.capturingKey
    assert keyboard.tryingKeys


@pytest.mark.parametrize('custom_profile', [False, True])
def test_scrolled_key_capture_keeps_rows_in_place(studio_session, qtbot, custom_profile):
    app, engine, root, editor, playback = studio_session
    root.resize(900,700)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'playingSettingsButton') is not None)
    click(root,'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    if custom_profile:
        qml(dialog,'editProfile((v,p)=>{}); true')
    keyboard=engine.rootContext().contextProperty('keyboard')
    mappings=root.findChild(QObject,'playingMappings')
    qtbot.waitUntil(lambda:mappings.property('contentHeight') > mappings.height())
    mappings.setProperty('contentY', mappings.property('contentHeight')-mappings.height())
    QTest.qWait(50)
    before=mappings.property('contentY')
    assert before > 100
    for row,key in ((8,Qt.Key_Comma),(9,Qt.Key_Period)):
        button=find_item(root,f'mappingKey-{row}')
        click_item(root,button)
        assert keyboard.capturingKey
        QTest.keyClick(root,key)
        QTest.qWait(250)
        assert abs(mappings.property('contentY')-before) < 1
        assert find_item(root,f'mappingKey-{row}') is button
        assert find_item(root,f'mappingKey-{row}').property('text') == chr(key)
        assert not keyboard.capturingKey and keyboard.tryingKeys
    # A rejected duplicate and cancelling capture must not rebuild the rows either.
    button=find_item(root,'mappingKey-9')
    click_item(root,button)
    QTest.keyClick(root,Qt.Key_Comma)
    assert keyboard.capturingKey
    assert abs(mappings.property('contentY')-before) < 1
    click_item(root,button)
    assert not keyboard.capturingKey and keyboard.tryingKeys
    assert button.property('text') == '.'
    assert not editor.state.notes


def test_pedal_drag_preview_escape_commit_undo_and_menu_input(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1366,768)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'studioRecordButton') is not None)
    keyboard=engine.rootContext().contextProperty('keyboard')
    keyboard.togglePedalLane();editor.addPedalEvent(.5,64)
    event=editor.state.controls[0]
    qtbot.waitUntil(lambda:find_item(root,'pedalPoint-'+event.id) is not None)
    point=find_item(root,'pedalPoint-'+event.id)
    lane=root.findChild(QObject,'pedalEventLane')
    start=point.mapToScene(QPointF(6,6)).toPoint()
    QTest.mousePress(root,Qt.LeftButton,pos=start)
    QTest.mouseMove(root,start+QPoint(40,-5))
    assert lane.property('draftTime') > event.time
    assert editor.state.controls==(event,)
    QTest.keyClick(root,Qt.Key_Escape)
    QTest.mouseRelease(root,Qt.LeftButton,pos=start+QPoint(40,-5))
    assert editor.state.controls==(event,)
    QTest.mousePress(root,Qt.LeftButton,pos=start)
    QTest.mouseMove(root,start+QPoint(40,-5))
    QTest.mouseRelease(root,Qt.LeftButton,pos=start+QPoint(40,-5))
    assert editor.state.controls[0].time > event.time
    editor.undo();assert editor.state.controls==(event,)
    click(root,'studioFileMenuButton')
    QTest.keyClick(root,Qt.Key_A)
    assert not keyboard.heldKeys
    QTest.keyClick(root,Qt.Key_Escape)
    click(root,'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    field=root.findChild(QObject,'playingProfileName')
    qml(dialog,"edit((v,p)=>{let c=JSON.parse(JSON.stringify(p));c.id='focus';c.name='Focus';v.profiles.push(c);v.activeProfile=c.id});true")
    keyboard.setTryKeys(True)
    field.forceActiveFocus()
    QTest.keyClick(root,Qt.Key_A)
    assert not keyboard.heldKeys
    dialog.close()


def test_settings_profile_edit_try_and_save_through_qml(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    width, height = map(int, os.environ.get('PLAYING_WINDOW_SIZE', '1366x768').split('x'))
    root.resize(width, height)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    qtbot.waitUntil(lambda: root.findChild(QObject,'playingSettingsButton') is not None)
    click(root, 'playingSettingsButton')
    dialog=root.findChild(QObject,'playingSettingsDialog')
    qtbot.waitUntil(lambda:dialog.property('opened'))
    keyboard=engine.rootContext().contextProperty('keyboard')
    assert not keyboard._input_enabled
    # Exercise the exact QVariantMap conversion used by all QML row edits.
    qml(dialog, "edit((v,p)=>{let c=JSON.parse(JSON.stringify(p));c.id='test';c.name='试弹方案';v.profiles.push(c);v.activeProfile=c.id}); true")
    assert keyboard.settingsDraft['activeProfile']=='test', keyboard.settingsError
    qml(dialog,"edit((v,p)=>{p.mappings[0].pitch=65;p.pedalKey=81}); true")
    assert keyboard.settingsDraft['profiles'][-1]['mappings'][0]['pitch']==65
    captures=os.environ.get('PLAYING_CAPTURE_DIR')
    if captures:
        Path(captures).mkdir(parents=True,exist_ok=True)
        app.processEvents()
        assert root.grabWindow().save(str(Path(captures)/'custom-keys.png'))
    keyboard.setTryKeys(True)
    dialog.forceActiveFocus(Qt.OtherFocusReason)
    QTest.keyPress(root, Qt.Key_A)
    assert keyboard.heldKeys==['F4']
    QTest.keyRelease(root, Qt.Key_A)
    assert not editor.state.notes
    keyboard.setTryKeys(False)
    click(root,'applyPlayingSettings')
    qtbot.waitUntil(lambda:not dialog.property('opened'))
    assert (tmp_path/'settings'/'playing.json').exists()
    assert keyboard.preferences.profile['id']=='test'
    timeline=root.findChild(QObject,'timeline')
    before=(timeline.x(),timeline.y(),timeline.width(),timeline.height())
    keyboard.togglePedalLane()
    editor.addPedalEvent(.5,127);editor.addPedalEvent(3,0)
    app.processEvents()
    assert (timeline.x(),timeline.y(),timeline.width(),timeline.height())==before
    if captures:
        assert root.grabWindow().save(str(Path(captures)/'pedal-lane.png'))


def test_real_qml_record_repeat_chord_pedal_and_undo(studio_session, qtbot):
    app, engine, root, editor, playback=studio_session
    root.resize(1366,768)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.findChild(QObject,'studioRecordButton') is not None)
    keyboard=engine.rootContext().contextProperty('keyboard')
    click(root,'studioRecordButton')
    qtbot.waitUntil(lambda:keyboard._stage=='recording')
    for _ in range(5):
        QTest.keyPress(root,Qt.Key_A);QTest.qWait(35);QTest.keyRelease(root,Qt.Key_A)
    QTest.keyPress(root,Qt.Key_S);QTest.keyPress(root,Qt.Key_D)
    keyboard.setPedal(True)
    QTest.qWait(50);QTest.keyRelease(root,Qt.Key_S);QTest.keyRelease(root,Qt.Key_D)
    assert not keyboard.heldKeys and keyboard.pedalDown
    click(root,'studioRecordButton')
    assert not keyboard.recording and not keyboard.pedalDown
    assert [n.pitch for n in editor.state.notes].count(60)==5
    assert len(editor.state.notes)==7 and len(editor.state.controls)==2
    assert all(n.duration>=.03125 for n in editor.state.notes)
    click(root,'studioUndo')
    assert not editor.state.notes and not editor.state.controls
    click(root,'studioRedo')
    assert len(editor.state.notes)==7 and len(editor.state.controls)==2
