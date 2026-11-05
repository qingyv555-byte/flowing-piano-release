"""Lossless initial input and fresh-source safety; no model/network calls."""
import hashlib
import json
from pathlib import Path

import pytest

from ai_piano_studio.score_lab.contracts import LabFailure, transcription_project
from ai_piano_studio.score_lab.service import ScoreLabService
from test_cli_transcriber import fake_runner
from test_contracts import output
from test_discovery import PAGE, Provider, call, proposal
from test_source_inspection import png, cli
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

def jpeg():
    image=QImage(600,800,QImage.Format_RGB32); image.fill(0xffffffff)
    buf=QBuffer(); buf.open(QIODevice.WriteOnly); image.save(buf,"JPEG"); return bytes(buf.data())


@pytest.mark.parametrize('url,raw,mime,strategy,suffix', [
    ('https://music.example/extensionless', png(), 'image/png','original_source_image','.png'),
    ('https://music.example/wrong.png', jpeg(), 'image/jpeg','original_source_image','.jpg'),
    ('https://music.example/score.pdf', b'%PDF-1.4 fixture', 'application/pdf','rendered_pdf_pages','.png'),
])
def test_original_png_jpeg_initial_bytes_pdf_stays_rendered(tmp_path,monkeypatch,url,raw,mime,strategy,suffix):
    rendered = png(550,750)
    fetches = []
    def fetch(target,**kwargs):
        fetches.append(target)
        if target == PAGE:
            return f'<html>Fixture artist<a href="{url}">score</a></html>'.encode(),target,'text/html'
        return raw,target,mime
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    for function in ('render_score_image_reference','render_score_reference'):
        monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.'+function,
            lambda *a,**k:dict(images=(rendered,),text='',page_count=1,rendered_pages=[1]))
    p = Provider([[call('inspect_music_resource',dict(url=PAGE,title='Fixture',source_url=PAGE)),
                   call('inspect_music_resource',dict(url=url,title='Fixture',source_url=PAGE)),
                   call('propose_song_source',proposal(url))]])
    images = []
    def runner(path,args,**kwargs):
        image = Path(args[args.index('--image')+1])
        images.append(image)
        assert image.suffix == suffix
        assert image.read_bytes() == (rendered if strategy == 'rendered_pdf_pages' else raw)
        fake_runner(output())(path,args,**kwargs)
    svc = ScoreLabService(tmp_path,provider_factory=lambda _:p,cli_runner=runner)
    draft = svc.prepare(svc.discover('Fixture'))
    assert len(images) == 2 and images[-1].parent == draft.project_path.parent
    assert fetches.count(url) == (3 if strategy == 'rendered_pdf_pages' else 4)
    provenance = json.loads(draft.project_path.with_name('provenance.json').read_text(encoding='utf-8'))
    assert provenance['inputStrategy'] == strategy
    assert provenance['sourceImageSha256'] == (hashlib.sha256(raw).hexdigest() if strategy == 'original_source_image' else None)


@pytest.mark.parametrize('failure', ['hash','redirect','cancel','signature'])
def test_second_image_fetch_failure_never_becomes_draft(tmp_path,monkeypatch,failure):
    url = 'https://music.example/input'
    raw = png()
    count = [0]
    stopped = [False]
    def fetch(target,**kwargs):
        if target == PAGE:
            return f'<html>Fixture artist<a href="{url}">score</a></html>'.encode(),target,'text/html'
        count[0] += 1
        if count[0] == 4:
            if failure == 'cancel':
                stopped[0] = True
            if failure == 'hash':
                return raw+b'changed',target,'image/png'
            if failure == 'redirect':
                return raw,'https://music.example/other','image/png'
            if failure == 'signature':
                return b'not an image',target,'image/png'
        return raw,target,'image/png'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',
        lambda *a,**k:dict(images=(raw,),text='',page_count=1,rendered_pages=[1]))
    p = Provider([[call('inspect_music_resource',dict(url=PAGE,title='Fixture',source_url=PAGE)),
                   call('inspect_music_resource',dict(url=url,title='Fixture',source_url=PAGE)),
                   call('propose_song_source',proposal(url))]])
    svc = ScoreLabService(tmp_path,provider_factory=lambda _:p,
        cli_runner=lambda *a,**k:cli()(*a,**k) if 'readability-schema.json' in a[1][a[1].index('--output-schema')+1] else pytest.fail('Unsafe second fetch reached transcription CLI'))
    with pytest.raises(LabFailure) as error:
        svc.prepare(svc.discover('Fixture'),cancelled=lambda:stopped[0])
    assert error.value.code == ('cancelled' if failure == 'cancel' else 'source_changed')
    assert not list(tmp_path.rglob('project.json'))


@pytest.mark.parametrize('beats', [.0074, 1/64-1e-10])
def test_raw_visual_near_zero_performed_note_rejected_before_construction(monkeypatch,beats):
    data = output()
    # Keep full hand coverage; the extra invalid grace must still be rejected.
    data['notes'].append(dict(hand='right',start_beat=1,duration_beats=beats,
                             pitch=65,degree=None,octave=0,accidental=0))
    monkeypatch.setattr('ai_piano_studio.score_lab.contracts.Note.create',
                        lambda **k:pytest.fail('Invalid raw data reached domain construction'))
    with pytest.raises(LabFailure):
        transcription_project(data,15)


def test_visual_resolution_minimum_allows_real_clipped_tail_and_short_rests():
    data = output()
    data['notes'].append(dict(hand='right',start_beat=29.999,duration_beats=1/64,
                             pitch=65,degree=None,octave=0,accidental=0))
    data['rests'] = [dict(hand='right',start_beat=0,duration_beats=.001)]
    state, _ = transcription_project(data,15)
    assert 0 < state.notes[-1].duration < (1/64)*.5
