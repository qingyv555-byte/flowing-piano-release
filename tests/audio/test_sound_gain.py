"""Playback calibration boosts quiet notes without changing export calibration."""
import numpy as np
import pytest

from ai_piano_studio.audio.sound_gain import scale_pcm, source_gain


@pytest.mark.parametrize('sound,live_db,export_db', [
    ('salamander', -11.0, 0.0), ('upright-sfz', 5.0, 5.0),
])
def test_playback_gain_is_2_2_times_previous_calibration(sound, live_db, export_db):
    for trim in (-9, 0, 9):
        assert source_gain(sound, trim) == pytest.approx(2.2 * 10 ** ((live_db + trim) / 20))
        assert source_gain(sound, trim, export=True) == pytest.approx(10 ** ((export_db + trim) / 20))


def test_boost_keeps_quiet_dynamics_and_bounds_loud_peaks():
    pcm = np.array([-32767, -1000, 0, 1000, 32767], dtype='<i2')
    result = scale_pcm(pcm, 2.2)
    assert result[1:4].tolist() == [-2200, 0, 2200]
    assert np.abs(result.astype(np.int32)).max() <= 32766
    assert result[0] < 0 < result[-1]
    assert np.all(np.diff(result.astype(np.int32)) > 0)
