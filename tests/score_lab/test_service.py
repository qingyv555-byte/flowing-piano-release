from dataclasses import replace
import json

import pytest

from ai_piano_studio.persistence.project_json import load_project
from ai_piano_studio.score_lab.contracts import LabFailure
from ai_piano_studio.score_lab.service import ScoreLabService
from test_contracts import output
from test_cli_transcriber import fake_runner
from test_discovery import PAGE, IMAGE, PNG, Provider, call, inspected_turns, proposal, resources


def service(tmp_path,resources,monkeypatch,data=None):
    factory,_ = resources
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',
        lambda *a,**k:dict(images=(PNG,),text='',page_count=1,rendered_pages=[1]))
    p = Provider([*inspected_turns(),[call('propose_song_source',proposal())]])
    return ScoreLabService(tmp_path,provider_factory=lambda _:p,resource_factory=factory,
                           cli_runner=fake_runner(output() if data is None else data))


def test_prepare_fresh_source_actual_notes_json_reload_and_provenance(tmp_path,resources,monkeypatch):
    svc = service(tmp_path,resources,monkeypatch)
    candidate = svc.discover('Fixture')
    assert not list(tmp_path.rglob('project.json'))
    stages = []
    draft = svc.prepare(candidate,progress=lambda e:stages.append(e['stage']))
    assert [n.pitch for n in draft.state.notes] == [65,49]
    assert draft.state == load_project(draft.project_path)
    assert len(draft.state.layers) == 2
    assert draft.public()['modelId'] == 'gpt-6.1-sol'
    assert set(stages) >= {'checking','transcribing','validating'}
    provenance = json.loads(draft.project_path.with_name('provenance.json').read_text(encoding='utf-8'))
    assert provenance['candidate']['sourceSha256'] == candidate.proposal.selected_sha256
    assert (draft.project_path.parent/'score-original.png').read_bytes() == PNG
    assert all(not session.resources for session in resources[1])


def test_forged_stale_and_consumed_candidates_fail(tmp_path,resources,monkeypatch):
    svc = service(tmp_path,resources,monkeypatch)
    candidate = svc.discover('Fixture')
    for forged in [replace(candidate),replace(candidate,duration_seconds=30),replace(candidate,proposal=replace(candidate.proposal,selected_url=PAGE))]:
        with pytest.raises(LabFailure) as error:
            svc.prepare(forged)
        assert error.value.code == 'stale_candidate'
    svc.prepare(candidate)
    with pytest.raises(LabFailure):
        svc.prepare(candidate)


def test_new_search_even_failed_invalidates_previous_confirmation(tmp_path,resources,monkeypatch):
    svc = service(tmp_path,resources,monkeypatch)
    candidate = svc.discover('Fixture')
    with pytest.raises(LabFailure):
        svc.discover('')
    with pytest.raises(LabFailure) as error:
        svc.prepare(candidate)
    assert error.value.code == 'stale_candidate'


@pytest.mark.parametrize('fresh_url,fresh_raw',[(IMAGE,PNG+b'changed'),(PAGE,PNG)])
def test_fresh_hash_or_final_url_change_fails(tmp_path,resources,monkeypatch,fresh_url,fresh_raw):
    svc = service(tmp_path,resources,monkeypatch)
    candidate = svc.discover('Fixture')
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda *a,**k:(fresh_raw,fresh_url,'image/png'))
    with pytest.raises(LabFailure) as error:
        svc.prepare(candidate)
    assert error.value.code == 'source_changed'
    assert not list(tmp_path.rglob('project.json'))


@pytest.mark.parametrize('data', [dict(output(),notes=[]),dict(output(),can_read=False),dict(output(),covered_beats=100)])
def test_invalid_output_never_becomes_draft(tmp_path,resources,monkeypatch,data):
    svc = service(tmp_path,resources,monkeypatch,data)
    with pytest.raises(LabFailure):
        svc.prepare(svc.discover('Fixture'))
    assert not list(tmp_path.rglob('project.json'))


def test_cancelled_prepare_never_draft(tmp_path,resources,monkeypatch):
    svc = service(tmp_path,resources,monkeypatch)
    candidate = svc.discover('Fixture')
    with pytest.raises(LabFailure) as error:
        svc.prepare(candidate,cancelled=lambda:True)
    assert error.value.code == 'cancelled'
    assert not list(tmp_path.rglob('project.json'))


def test_actual_parsed_mml_resource_path_without_cli(tmp_path,monkeypatch):
    url = 'https://music.example/score.mml'
    def fetch(target,**kwargs):
        if target == PAGE:
            return f'<html>Fixture artist<a href="{url}">MML score</a></html>'.encode(),target,'text/html'
        return b'MML@t120o4c4d4,t120o3c2;',target,'text/plain'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    turns = [[call('inspect_music_resource',dict(url=PAGE,title='Fixture',source_url=PAGE)),
              call('inspect_music_resource',dict(url=url,title='Fixture',source_url=PAGE))],
             [call('propose_song_source',proposal(url))]]
    svc = ScoreLabService(tmp_path,provider_factory=lambda _:Provider(turns),
                          cli_runner=lambda *a,**k:pytest.fail('Parsed sources must not use vision CLI'))
    draft = svc.prepare(svc.discover('Fixture'))
    assert [n.pitch for n in draft.state.notes] == [48,60,62]
    assert len(draft.state.layers) == 2
    assert draft.warnings


def test_parsed_tempo_signature_handling_and_native_timing():
    from ai_piano_studio.persistence.midi import MidiImportResult, MidiNoteEvent
    midi = MidiImportResult(notes=(MidiNoteEvent(0,960,60,75,0,0),),ppq=480,bpm=60,
                           beats_per_bar=3,beat_unit=4,track_count=1,note_track_count=1,
                           tempo_event_count=1,warnings=())
    state = ScoreLabService._parsed_project(midi,1,5)
    assert state.notes[0].duration == 1 and state.notes[0].start_time == 0
    assert state.tempo.beats_per_bar == 3
    with pytest.raises(LabFailure) as error:
        ScoreLabService._parsed_project(replace(midi,warnings=('multi_tempo',)),0,5)
    assert error.value.code == 'tempo'


def test_parsed_endpoint_excludes_cutoff_but_keeps_crossing_note():
    from ai_piano_studio.persistence.midi import MidiImportResult, MidiNoteEvent
    midi = MidiImportResult(notes=(
        MidiNoteEvent(0,480,60,75,0,0),
        MidiNoteEvent(13199,13201,62,75,0,0),
        MidiNoteEvent(13200,13680,64,75,0,0)),ppq=480,bpm=110,
        beats_per_bar=4,beat_unit=4,track_count=1,note_track_count=1,tempo_event_count=1,warnings=())
    state = ScoreLabService._parsed_project(midi,0,15)
    assert [n.pitch for n in state.notes] == [60,62]
    assert state.notes[-1].duration == pytest.approx(60/(110*480))


@pytest.mark.parametrize('failure', ['cancelled','session_construction','session_configuration'])
def test_early_prepare_failure_releases_discovered_managed_preview(tmp_path,resources,monkeypatch,failure):
    svc = service(tmp_path,resources,monkeypatch)
    candidate = svc.discover('Fixture')
    previews = candidate.inspection.preview_paths
    assert svc._inspector is not None and previews and all(p.is_file() for p in previews)
    closed = []
    if failure == 'session_construction':
        def factory(*args):
            raise OSError('injected session construction failure')
        svc.resource_factory = factory
    elif failure == 'session_configuration':
        class Session:
            @property
            def require_source_confirmation(self):
                return True
            @require_source_confirmation.setter
            def require_source_confirmation(self,value):
                raise OSError('injected session configuration failure')
            def close(self):
                closed.append(True)
        svc.resource_factory = lambda *args: Session()
    with pytest.raises(LabFailure) as error:
        svc.prepare(candidate,cancelled=lambda:failure == 'cancelled')
    assert error.value.code == ('cancelled' if failure == 'cancelled' else 'prepare_failed')
    assert svc._candidate is None and svc._inspector is None
    assert all(not p.exists() for p in previews)
    assert closed == ([True] if failure == 'session_configuration' else [])
    assert not list(tmp_path.rglob('project.json'))


def test_stale_prepare_does_not_invalidate_other_current_candidate_preview(tmp_path,resources,monkeypatch):
    svc = service(tmp_path,resources,monkeypatch)
    previous = svc.discover('Fixture')
    svc.provider_factory = lambda _: Provider([*inspected_turns(),[call('propose_song_source',proposal())]])
    current = svc.discover('Fixture revised request')
    inspector = svc._inspector
    previews = current.inspection.preview_paths
    try:
        with pytest.raises(LabFailure) as error:
            svc.prepare(previous,cancelled=lambda:True)
        assert error.value.code == 'stale_candidate'
        assert svc._candidate is current and svc._inspector is inspector
        assert previews and all(p.is_file() for p in previews)
    finally:
        svc.invalidate_candidate()
