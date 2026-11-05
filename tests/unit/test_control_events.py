import json

import pytest

from ai_piano_studio.domain.control_event import ControlEvent, release_times
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_json import save_project, load_project
from ai_piano_studio.persistence.midi import encode_midi, parse_midi, materialize_midi_import, materialize_midi_controls


def note(**kwargs):
    return Note.create(pitch=60, start_time=0, duration=.5, **kwargs)


@pytest.mark.parametrize('value,expected', [(0,.5),(63,.5),(64,2),(127,2)])
def test_binary_pedal_preserves_raw_value_and_key_duration(value, expected):
    n = note()
    events = (ControlEvent.create(time=.1,value=value), ControlEvent.create(time=2,value=0))
    assert release_times((n,),events)[n.id] == expected
    assert n.duration == .5
    assert events[0].value == value


def test_shared_channel_across_tracks_and_distinct_channels():
    a,b = note(channel=2,track=0),note(channel=3,track=0)
    events = (ControlEvent.create(time=.1,value=127,channel=2,track=1),ControlEvent.create(time=2,value=0,channel=2,track=1))
    assert release_times((a,b),events) == {a.id:2,b.id:.5}


def test_same_tick_order_survives_midi_and_json(tmp_path):
    n = note(channel=0,track=0,on_order=1,off_order=3)
    events = (ControlEvent.create(time=.5,value=90,channel=0,track=0,order=2),ControlEvent.create(time=2,value=0,channel=0,track=0,order=4))
    parsed = parse_midi(encode_midi((n,),controls=events))
    notes,layers = materialize_midi_import(parsed,status='saved')
    controls = materialize_midi_controls(parsed)
    assert release_times(notes,controls)[notes[0].id] == 2
    assert [e.value for e in parsed.controls] == [90,0]
    state = ProjectState(notes,layers=layers,controls=controls)
    save_project(tmp_path/'a.json',state)
    restored = load_project(tmp_path/'a.json')
    assert restored.controls == controls
    assert restored.playback_controls == controls
    assert restored.playback_duration == 2


def test_control_only_layer_is_not_discarded_on_import_or_delete():
    e = ControlEvent.create(time=1,value=0,channel=1,track=2)
    parsed = parse_midi(encode_midi((note(channel=0,track=0),),controls=(e,)))
    notes,layers = materialize_midi_import(parsed)
    controls = materialize_midi_controls(parsed)
    assert len(layers) == 2
    state = ProjectState(notes,layers=layers,controls=controls)
    with pytest.raises(ValueError):
        state.delete_layer(controls[0].layer_id)
    removed = state.delete_layer(controls[0].layer_id,note_policy='delete_notes')
    assert not removed.controls and not removed.playback_controls


def test_v4_load_defaults_to_empty_controls(tmp_path):
    save_project(tmp_path/'a.json',ProjectState((note(),)))
    data=json.loads((tmp_path/'a.json').read_text(encoding='utf-8'))
    data['schemaVersion']=4
    del data['controlEvents'],data['playbackControlEvents']
    (tmp_path/'a.json').write_text(json.dumps(data))
    assert load_project(tmp_path/'a.json').controls == ()


def test_midi_track_names_follow_event_routes_after_layer_reorder():
    from ai_piano_studio.domain.music_layer import MusicLayer
    a = MusicLayer.create(id='a', name='Melody')
    b = MusicLayer.create(id='b', name='Bass')
    notes = (note(layer_id='a', channel=0, track=0), note(layer_id='b', channel=1, track=1))
    controls = (ControlEvent.create(time=.1, value=127, layer_id='a', channel=0, track=0),)
    result = parse_midi(encode_midi(notes, layers=(b, a), controls=controls))
    assert result.track_names == ('Melody', 'Bass')


@pytest.mark.parametrize('changes',[{'time':float('nan')},{'value':128},{'value':True},{'channel':16},{'controller':66}])
def test_invalid_control_rejected(changes):
    with pytest.raises(ValueError):
        ControlEvent.create(**({'time':0,'value':0}|changes))
