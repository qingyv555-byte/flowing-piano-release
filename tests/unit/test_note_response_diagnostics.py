"""Keep diagnostic mode selection and missing timing evidence honest."""
import os

import pytest

from tools import measure_note_response as diagnostic


def test_native_mode_overrides_inherited_offscreen_settings(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QSG_RENDER_LOOP', 'basic')
    diagnostic.configure_rendering('windows')
    assert os.environ['QT_QPA_PLATFORM'] == 'windows'
    assert 'QSG_RENDER_LOOP' not in os.environ


def test_offscreen_default_retains_historical_basic_render_loop(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'windows')
    monkeypatch.setenv('QSG_RENDER_LOOP', 'threaded')
    diagnostic.configure_rendering('offscreen')
    assert os.environ['QT_QPA_PLATFORM'] == 'offscreen'
    assert os.environ['QSG_RENDER_LOOP'] == 'basic'


def test_native_comparison_can_explicitly_select_basic_loop(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QSG_RENDER_LOOP', 'threaded')
    diagnostic.configure_rendering('windows', 'basic')
    assert os.environ['QT_QPA_PLATFORM'] == 'windows'
    assert os.environ['QSG_RENDER_LOOP'] == 'basic'


def test_missing_frame_signal_is_not_replaced_with_a_measured_latency():
    rows = [{'frame': 1000.0, 'pcm': 1000.0},
            {'frame': None, 'pcm': 10.0},
            {'frame': 30.0, 'pcm': 20.0}]
    result = diagnostic.summarize_samples(rows, ['frame', 'pcm', 'absent'])
    assert result['frame'] == {'count': 1, 'p50': 30.0, 'p95': 30.0, 'max': 30.0}
    assert result['pcm'] == {'count': 2, 'p50': 15.0, 'p95': 20.0, 'max': 20.0}
    assert result['absent'] == {'count': 0, 'p50': None, 'p95': None, 'max': None}


@pytest.mark.parametrize('arguments', [
    ['--samples', '1'], ['--notes', '-1'], ['--playing', '--verify-pixels'],
])
def test_invalid_measurement_rejected_before_creating_a_window(arguments):
    with pytest.raises(SystemExit) as result:
        diagnostic.parse_arguments(['unused.json', *arguments])
    assert result.value.code == 2
