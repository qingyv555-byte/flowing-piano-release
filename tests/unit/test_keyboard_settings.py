import json

import pytest

from ai_piano_studio.keyboard.settings import PlayingSettings, default_profile, validate_profile


def test_default_layout_and_custom_duplicate_pitch():
    profile = default_profile()
    assert [(m['key'], m['pitch']) for m in profile['mappings']] == [
        (65, 60), (87, 61), (83, 62), (69, 63), (68, 64), (70, 65),
        (84, 66), (71, 67), (89, 68), (72, 69), (85, 70), (74, 71)]
    profile['mappings'].append({'key': 81, 'pitch': 60, 'enabled': True})
    assert validate_profile(profile)['mappings'][-1]['pitch'] == 60
    profile['mappings'].append({'key': 65, 'pitch': 21, 'enabled': True})
    with pytest.raises(ValueError, match='占用'):
        validate_profile(profile)


@pytest.mark.parametrize('mapping', [
    {'key': 32, 'pitch': 60, 'enabled': True},
    {'key': 65, 'pitch': 109, 'enabled': True},
    {'key': 65, 'pitch': True, 'enabled': True},
])
def test_rejects_reserved_and_bad_ranges(mapping):
    with pytest.raises(ValueError):
        validate_profile({'id': 'custom', 'name': '我的', 'mappings': [mapping], 'pedalKey': None})


def test_apply_is_atomic_cancel_and_corrupt_fallback(tmp_path):
    path = tmp_path / 'playing.json'
    settings = PlayingSettings(path)
    draft = settings.snapshot()
    custom = default_profile()
    custom.update(id='mine', name='我的方案', mappings=[])
    draft['profiles'].append(custom)
    draft['activeProfile'] = 'mine'
    assert settings.profile['id'] == 'default'
    settings.apply(draft)
    assert PlayingSettings(path).profile['mappings'] == []
    before = path.read_bytes()
    draft['velocity'] = 0
    with pytest.raises(ValueError):
        settings.apply(draft)
    assert path.read_bytes() == before
    assert settings.snapshot()['velocity'] == 80
    path.write_text('{bad', encoding='utf-8')
    recovered = PlayingSettings(path)
    assert recovered.profile == default_profile()
    assert recovered.warning
    assert path.read_text(encoding='utf-8') == '{bad'


def test_default_is_immutable_and_pedal_cannot_share_note_key(tmp_path):
    settings = PlayingSettings(tmp_path / 'settings.json')
    draft = settings.snapshot()
    draft['profiles'][0]['name'] = 'changed'
    with pytest.raises(ValueError):
        settings.apply(draft)
    profile = default_profile()
    profile['pedalKey'] = 65
    with pytest.raises(ValueError, match='占用'):
        validate_profile(profile)
