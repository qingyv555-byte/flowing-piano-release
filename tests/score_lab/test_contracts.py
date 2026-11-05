import pytest

from ai_piano_studio.score_lab.contracts import LabFailure, duration, transcription_project


def test_resource_schema_accepts_optional_fields_but_rejects_missing_and_unknown():
    from ai_piano_studio.score_lab.contracts import _schema
    from ai_piano_studio.music_agent.resource_tools import RESOURCE_DEFINITIONS
    schema = next(d.input_schema for d in RESOURCE_DEFINITIONS if d.name == 'inspect_music_resource')
    payload = dict(url='https://example.test/score', title='Score', source_url='https://example.test')
    _schema(payload, schema)
    _schema(dict(payload, page_number=1), schema)
    with pytest.raises(ValueError):
        _schema({k: v for k, v in payload.items() if k != 'url'}, schema)
    with pytest.raises(ValueError):
        _schema(dict(payload, unrecognized=True), schema)
    with pytest.raises(ValueError):
        _schema(dict(payload, page_number='first'), schema)


def output():
    return dict(can_read=True, bpm=120, beats_per_bar=4, beat_unit=4, tonic='Db',
                covered_beats=32, uncertainty=[], notes=[
                    dict(hand='right', start_beat=0, duration_beats=32, pitch=None, degree=3, octave=0, accidental=0),
                    dict(hand='left', start_beat=0, duration_beats=32, pitch=49, degree=None, octave=0, accidental=0)], rests=[])


def test_actual_degree_conversion_seconds_crop_and_hands():
    state, warnings = transcription_project(output(), 15)
    assert [n.pitch for n in state.notes] == [65, 49]
    assert all(n.duration == 15 for n in state.notes)
    assert len(state.layers) == 2
    assert state.tempo.bpm == 120


@pytest.mark.parametrize('value', [True, 0, 4, 31, float('nan'), float('inf'), '15'])
def test_duration_bounds(value):
    with pytest.raises(LabFailure):
        duration(value)


@pytest.mark.parametrize('field,value', [('bpm', 59), ('bpm', True), ('beat_unit', 7), ('covered_beats', float('inf'))])
def test_invalid_contract(field, value):
    data = output()
    data[field] = value
    with pytest.raises(LabFailure):
        transcription_project(data, 15)


@pytest.mark.parametrize('field,value', [('pitch', 128), ('start_beat', -1), ('duration_beats', 0), ('duration_beats', float('nan')), ('hand', 'third')])
def test_invalid_note(field, value):
    data = output()
    data['notes'][1][field] = value
    with pytest.raises(LabFailure):
        transcription_project(data, 15)


def test_missing_coverage_is_not_padded():
    data = output()
    data['covered_beats'] = 4
    for row in data['notes']:
        row['duration_beats'] = 4
    state, warnings = transcription_project(data, 15)
    assert state.estimated_duration == 2
    assert warnings


def test_unreadable_empty_and_unexplained_gaps_fail():
    for data in [dict(output(), can_read=False), dict(output(), notes=[]), dict(output(), rests=[], covered_beats=40)]:
        with pytest.raises(LabFailure):
            transcription_project(data, 15)


def test_full_image_response_crops_at_endpoint_and_survives_reload(tmp_path):
    from ai_piano_studio.persistence.project_json import save_project, load_project

    data = dict(output(), bpm=110, covered_beats=28, notes=[])
    for index in range(50):
        for template in output()['notes']:
            data['notes'].append(dict(
                template, start_beat=index / 2,
                duration_beats=3.25 if index == 49 else 0.5,
            ))
    data['notes'].append(dict(output()['notes'][0], start_beat=27.75, duration_beats=0.25))
    data['rests'] = [dict(hand='left', start_beat=27.75, duration_beats=0.25)]

    state, _ = transcription_project(data, 15)
    path = tmp_path / 'crop-15s.json'
    save_project(path, state)
    reloaded = load_project(path)
    assert len(reloaded.notes) == 100
    assert reloaded.estimated_duration == pytest.approx(15)
    assert all(n.duration > 1e-9 for n in reloaded.notes)
    assert all(n.start_time < 15 and n.start_time+n.duration <= 15+1e-12 for n in reloaded.notes)
