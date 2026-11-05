"""Actual QML sliders retain independent tuning when changing instrument."""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_workflows import click, click_item, find_item, qml
from tools.studio_evidence import install_memory_output

@pytest.fixture(autouse=True)
def silent(monkeypatch):
    blocked=install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked

def reveal_sound(root, name):
    root.grabWindow()
    item=find_item(root,name)
    scroll=find_item(root,'studioSoundScroll')
    flick=qml(scroll,'contentItem')
    y=item.mapToItem(flick,QPointF()).y()
    flick.setProperty('contentY',max(0,min(flick.property('contentHeight')-flick.height(),flick.property('contentY')+y-30)))
    QTest.qWait(30)
    return item

@pytest.mark.parametrize('size',[(1000,640),(1440,1000)])
def test_sliders_remain_effective_on_both_sources_and_popup_fits(studio_session,qtbot,tmp_path,size):
    app,engine,root,editor,playback=studio_session
    root.resize(*size)
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda:root.property('pageState')=='editor')
    click(root,'studioSoundButton')
    qtbot.wait(80)
    popup=root.findChild(QObject,'studioSoundPopup')
    assert popup.property('opened') and popup.property('height') <= root.height()-24
    original=editor.state.content
    expected={}
    for sound_id in ('salamander','upright-sfz'):
        playback._commit_sound(sound_id)
        for name,key in [('studioToneSlider','brightness'),('studioSoundResponse','response'),('studioSoundReverb','reverb'),('studioSoundWidth','width')]:
            item=reveal_sound(root,name)
            assert item.isVisible() and item.isEnabled()
            before=getattr(playback.project_sound_parameters(),key)
            item.forceActiveFocus()
            delta = .05 if sound_id == 'salamander' or before == 0 else -.05
            QTest.keyClick(root, Qt.Key_Right if delta > 0 else Qt.Key_Left)
            value=getattr(playback.project_sound_parameters(),key)
            assert value == pytest.approx(max(0,min(1,before+delta)))
        assert value != before
        expected[sound_id]=playback.project_sound_parameters()
    playback._commit_sound('salamander')
    assert playback.project_sound_parameters()==expected['salamander']
    playback._commit_sound('upright-sfz')
    assert playback.project_sound_parameters()==expected['upright-sfz']
    assert editor.state.content==original
    qml(find_item(root,'studioSoundScroll'),'contentItem.contentY = 0; true')
    assert root.grabWindow().save(str(tmp_path/f'sound-controls-{size[0]}.png'))
