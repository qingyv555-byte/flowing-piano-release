"""Regression checkpoints transcribed from the cited scores, not the generator."""
import pytest

from ai_piano_studio.domain.tempo import TempoMap
from tools.generate_builtin_templates import TEMPLATE_SPECS


def spec(name):
    return next(item for item in TEMPLATE_SPECS if item['id'] == name)


@pytest.mark.parametrize('numerator,denominator,quarters', [(3, 8, 1.5), (2, 2, 4)])
def test_original_template_meters_have_correct_bar_lengths(numerator, denominator, quarters):
    tempo = TempoMap(bpm=120, beats_per_bar=numerator, beat_unit=denominator)
    assert tempo.beats_per_bar_length == quarters
    assert tempo.bar_duration_seconds == quarters / 2


def test_elise_pickup_cadence_and_e4_match_public_domain_score():
    item = spec('fur-elise')
    assert item['meter'] == (3, 8)
    melody = sorted((e for e in item['events'] if e.voice == 'melody'), key=lambda e: e.start_beat)
    # The first two sixteenths are a pickup in the last eighth of the first bar.
    assert [(e.pitch, e.start_beat) for e in melody[:3]] == [(76, 1), (75, 1.25), (76, 1.5)]
    # The return to A uses E4-C5-B4, not D4-C5-B4 (Mutopia 931).
    assert any(e.pitch == 64 and e.start_beat == 11.25 for e in melody)
    assert melody[-1].pitch == 69
    assert melody[-1].duration_beats == 1


def test_minuet_unornamented_cadence_holds_a_for_entire_eighth_bar():
    melody = [e for e in spec('minuet-in-g')['events'] if e.voice == 'melody' and 21 <= e.start_beat < 24]
    assert [(e.pitch, e.start_beat, e.duration_beats) for e in melody] == [(69, 21, 3)]


def test_moonlight_uses_cut_time_without_rescaling_quarter_note_triplets():
    item = spec('moonlight-theme')
    assert item['meter'] == (2, 2)
    triplets = [e for e in item['events'] if e.voice == 'triplet']
    assert [e.start_beat for e in triplets[:4]] == pytest.approx([0, 1/3, 2/3, 1])
