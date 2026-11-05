from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from tests.qt.test_keyboard import make_keyboard
from ai_piano_studio.keyboard.settings import default_profile


def key_event(kind, key, *, repeat=False, scan=0, modifiers=Qt.NoModifier):
    return QKeyEvent(kind, key, modifiers, scan, 0, 0, '', repeat)


def test_repeat_press_and_repeat_release_keep_one_voice(qapp):
    _, playback, _, keyboard = make_keyboard()
    keyboard.setInputEnabled(True)
    for kind, repeat in [(QEvent.KeyPress, False), (QEvent.KeyPress, True), (QEvent.KeyRelease, True)]:
        assert keyboard.eventFilter(keyboard, key_event(kind, Qt.Key_A, repeat=repeat))
    assert len(playback.started_voices) == 1
    assert playback.released_voices == []
    assert keyboard.eventFilter(keyboard, key_event(QEvent.KeyRelease, Qt.Key_A, modifiers=Qt.ControlModifier))
    assert playback.released_voices == [(1, 'natural')]


def test_focus_release_does_not_stop_other_shared_audio(qapp):
    _, playback, _, keyboard = make_keyboard()
    keyboard.pressKey(Qt.Key_A)
    keyboard.releaseAll()
    assert playback.released_voices == [(1, 'safe')]
    assert playback.stop_modes == []


def test_two_keys_same_pitch_own_voices_and_key_up_uses_original_pitch(qapp):
    _, playback, _, keyboard = make_keyboard()
    draft = keyboard.preferences.snapshot()
    profile = default_profile()
    profile.update(id='mine', name='Mine')
    profile['mappings'].append({'key': ord('Q'), 'pitch': 60, 'enabled': True})
    draft['profiles'].append(profile)
    draft['activeProfile'] = 'mine'
    assert keyboard.updateSettingsDraft(draft)
    assert keyboard.applySettings()
    off = []
    keyboard.keyNoteOff.connect(off.append)
    keyboard.pressKey(ord('A'))
    keyboard.pressKey(ord('Q'))
    keyboard.octaveUp()
    keyboard.releaseKey(ord('A'))
    assert off == []
    assert playback.released_voices == [(1, 'natural')]
    keyboard.releaseKey(ord('Q'))
    assert off == [60]
    assert playback.released_voices[-1] == (2, 'natural')


def test_capture_consumes_key_and_escape_without_playing(qapp):
    _, playback, _, keyboard = make_keyboard()
    draft = keyboard.preferences.snapshot()
    profile = default_profile()
    profile.update(id='mine', name='Mine')
    draft['profiles'].append(profile)
    draft['activeProfile'] = 'mine'
    assert keyboard.updateSettingsDraft(draft)
    keyboard.captureKey(0)
    keyboard.eventFilter(keyboard, key_event(QEvent.KeyPress, ord('Q')))
    assert keyboard.settingsDraft['profiles'][1]['mappings'][0]['key'] == ord('Q')
    assert not keyboard.capturingKey
    assert playback.started_voices == []


def test_native_identity_survives_logical_key_change(qapp):
    _, playback, _, keyboard = make_keyboard()
    keyboard.setInputEnabled(True)
    keyboard.eventFilter(keyboard, key_event(QEvent.KeyPress, Qt.Key_A, scan=30))
    keyboard.eventFilter(keyboard, key_event(QEvent.KeyRelease, Qt.Key_Q, scan=30))
    assert not keyboard._held
    assert playback.released_voices == [(1, 'natural')]


def test_layout_change_cannot_release_another_held_physical_key(qapp):
    _, playback, _, keyboard = make_keyboard()
    keyboard.setInputEnabled(True)
    keyboard.eventFilter(keyboard, key_event(QEvent.KeyPress, Qt.Key_A, scan=30))
    keyboard.eventFilter(keyboard, key_event(QEvent.KeyPress, Qt.Key_S, scan=31))
    keyboard.eventFilter(keyboard, key_event(QEvent.KeyRelease, Qt.Key_S, scan=30))
    assert playback.released_voices == [(1,'natural')]
    assert keyboard.heldKeys == ['D4']
    keyboard.releaseAll()
