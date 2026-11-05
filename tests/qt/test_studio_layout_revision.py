"""Single-row Studio chrome exercises real controls without audio devices."""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tests.qt.test_studio_shell import studio_session, click, find_visual_item
from tests.qt.test_studio_workflows import qml, no_audio_device, click_item
from tests.qt.test_nocturne import preserved_qsettings_state
from ai_piano_studio.__main__ import initialize_deferred_services


@pytest.mark.parametrize('width', [1000, 1280, 1440])
def test_integrated_toolbar_retains_controls_and_canvas(studio_session, qtbot, width, tmp_path):
    app, engine, root, editor, playback = studio_session
    root.resize(width, 800)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    app.processEvents()
    chrome = root.findChild(QObject, 'windowChrome')
    assert chrome.property('integratedToolbar')
    toolbar = root.findChild(QObject, 'studioCommandBar')
    assert toolbar.parentItem().objectName() == 'windowChromeToolbarHost'
    previous_right = 0
    for name in ('studioHomeNav', 'studioFileMenuButton', 'studioUndo', 'studioRedo',
                 'studioSnap', 'studioArrangement', 'studioInspector', 'studioViewMenuButton',
                 'studioAssistant', 'studioSave', 'studioSettingsButton',
                 'windowChromeMinimizeButton', 'windowChromeMaximizeButton', 'windowChromeCloseButton'):
        item = root.findChild(QObject, name)
        assert item is not None and item.isVisible(), name
        point = item.mapToScene(QPointF(0, 0))
        assert point.x() >= previous_right - 1, name
        assert 0 <= point.y() < chrome.property('titleBarHeight'), name
        previous_right = point.x() + item.width()
        assert previous_right <= width, name
    assert root.findChild(QObject, 'studioAssistant').property('variant') == 'primary'
    assert root.findChild(QObject, 'studioSave').property('variant') == 'primary'
    timeline = root.findChild(QObject, 'timeline')
    assert timeline.mapToScene(QPointF()).y() < chrome.property('titleBarHeight') + 45
    for name in ('studioRecordButton', 'playingSettingsButton', 'sustainPedalButton',
                 'studioSoundButton', 'studioMusicSettings', 'studioVolumeSlider'):
        item = root.findChild(QObject, name)
        assert item is not None and item.isVisible(), name
        point = item.mapToScene(QPointF())
        assert 0 <= point.x() and point.x() + item.width() <= width, name
    # The formal timeline still owns its keyboard pitch strip.
    assert timeline.property('keyWidth') > 0
    assert root.grabWindow().save(str(tmp_path / f'studio-{width}.png'))
    initialize_deferred_services(engine)
    click(root, 'studioSettingsButton')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAppearanceDialog') is not None)
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAppearanceDialog').property('opened'))


def test_view_and_sound_have_distinct_responsibilities(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    menu = root.findChild(QObject, 'studioViewMenu')
    texts = '|'.join(qml(menu, f'itemAt({i}).text') for i in range(menu.property('count')))
    assert '跟随播放' in texts and '缩略图' in texts
    assert not any(word in texts for word in ('专注', '智能', '全曲', '设置', '声部', '属性', '陪伴'))
    assert root.findChild(QObject, 'studioFollowPlayback') is None
    click(root, 'studioMusicSettings')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioMusicPopup').property('opened'))
    bpm = root.findChild(QObject, 'studioBpm')
    bpm.setProperty('value', 132)
    qml(bpm, 'valueModified(); true')
    assert editor.bpm == 132


def test_view_menu_labels_stay_fixed_and_checks_track_real_state(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    root.resize(1000, 640)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    experience = engine.rootContext().contextProperty('experience')
    keyboard = engine.rootContext().contextProperty('keyboard')
    timeline = root.findChild(QObject, 'timeline')
    menu = root.findChild(QObject, 'studioViewMenu')
    states = (
        ('跟随播放', lambda: timeline.property('followPlayback')),
        ('简化音符', lambda: experience.compositeDisplayMode == 'smart'),
        ('缩略图', lambda: experience.miniMapDisplayMode != 'hidden'),
        ('踏板轨迹', lambda: keyboard.showPedalLane),
    )
    for index, (label, current_state) in enumerate(states):
        initial = current_state()
        if not menu.property('opened'):
            click(root, 'studioViewMenuButton')
            qtbot.waitUntil(lambda: menu.property('opened'))
        for expected in (initial, not initial):
            item = qml(menu, f'itemAt({index})')
            assert item.property('text') == label
            assert item.property('checkable') is True
            assert item.property('checked') is expected
            assert qml(item, 'indicator.visible') is expected
            if index == 0 and expected == initial:
                assert root.grabWindow().save(str(tmp_path / 'studio-view-checks.png'))
            click_item(root, item)
            assert menu.property('opened'), 'Switches must allow continuous adjustment'
            assert current_state() is not expected
        assert current_state() is initial
    experience.setMiniMapDisplayMode('hidden')
    assert qml(menu, 'itemAt(2).checked') is False
    experience.setMiniMapDisplayMode('visible')
    assert qml(menu, 'itemAt(2).checked') is True
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not menu.property('opened'))
    click(root, 'studioViewMenuButton')
    qtbot.waitUntil(lambda: menu.property('opened'))
    click(root, 'studioViewMenuButton')
    qtbot.waitUntil(lambda: not menu.property('opened'))


def test_settings_keyboard_shortcut_opens_formal_dialog(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    root.requestActivate()
    qtbot.waitUntil(lambda: root.isActive())
    QTest.keyClick(root, Qt.Key_Comma, Qt.ControlModifier)
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAppearanceDialog') is not None)
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert not root.findChild(QObject, 'studioSettingsShortcut').property('enabled')


def test_recording_controls_fit_small_window(studio_session, qtbot, tmp_path, no_audio_device):
    app, engine, root, editor, playback = studio_session
    root.resize(1000, 640)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioRecordButton')
    keyboard = engine.rootContext().contextProperty('keyboard')
    assert keyboard.recording
    app.processEvents()
    previous_right = 0
    for name in ('studioPlayPauseButton', 'studioStopButton', 'studioRecordButton',
                 'studioPosition', 'studioMusicSettings', 'playingSettingsButton',
                 'sustainPedalButton', 'studioCancelRecording', 'studioSoundButton',
                 'studioVolumeLabel', 'studioVolumeSlider', 'studioVolumeValue'):
        item = root.findChild(QObject, name)
        assert item is not None and item.isVisible(), name
        point = item.mapToScene(QPointF())
        assert point.x() >= previous_right - 1, name
        previous_right = point.x() + item.width()
        assert previous_right <= 1000, name
    assert root.grabWindow().save(str(tmp_path / 'studio-recording-1000.png'))
    keyboard.cancelRecording()
    assert not keyboard.recording
