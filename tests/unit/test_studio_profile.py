from pathlib import Path

import pytest

from ai_piano_studio.studio import resolve_profile_home


def test_local_profile_setting_and_explicit_cli_precedence(tmp_path):
    configuration = tmp_path/'config'
    configuration.mkdir()
    migrated = tmp_path/'migrated-profile'
    (configuration/'.studio-profile-home').write_text(str(migrated), encoding='utf8')
    assert resolve_profile_home(configuration_root=configuration) == migrated
    isolated = tmp_path/'isolated-test'
    assert resolve_profile_home(isolated, configuration_root=configuration) == isolated


def test_absent_local_setting_keeps_existing_profile_default(tmp_path, monkeypatch):
    from ai_piano_studio import studio
    monkeypatch.setattr(studio.QStandardPaths, 'writableLocation', lambda _: str(tmp_path))
    assert resolve_profile_home(configuration_root=tmp_path) == tmp_path/'AI Piano Studio Rebuild'


@pytest.mark.parametrize('value', ['', 'relative-folder', 'X'*33000], ids=['empty', 'relative', 'oversize'])
def test_invalid_local_setting_does_not_silently_create_a_different_profile(tmp_path, value):
    (tmp_path/'.studio-profile-home').write_text(value, encoding='utf8')
    with pytest.raises(ValueError):
        resolve_profile_home(configuration_root=tmp_path)
