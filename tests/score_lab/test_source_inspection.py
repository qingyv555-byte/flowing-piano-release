import hashlib
import json
from pathlib import Path
import io
import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from ai_piano_studio.score_lab.source_inspection import SourceInspector
from ai_piano_studio.score_lab.visual_preflight import assess_readability
from ai_piano_studio.score_lab.contracts import LabFailure
from ai_piano_studio.music_agent.source_confirmation import InspectedSource


def png(w=600,h=800):
    image=QImage(w,h,QImage.Format_RGB32); image.fill(0xffffffff)
    out=QBuffer(); out.open(QIODevice.WriteOnly); image.save(out,'PNG')
    return bytes(out.data())


def cli(data=None, terminal='turn.completed', seen=None):
    def run(executable,args,**kw):
        if seen is not None: seen.append((args,kw))
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(data or {'readable':True,'reason':'印刷符号清楚；仍需逐音核对。'}),encoding='utf-8')
        kw['on_event']({'type':terminal})
    return run


def test_small_actual_bytes_rejected_before_vision(tmp_path,monkeypatch):
    raw=png(200,260); url='https://fixture.example/a.png'
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda *a,**k:(raw,url,'image/png'))
    inspector=SourceInspector(tmp_path,'model',runner=lambda *a,**k:pytest.fail('thumbnail reached vision'))
    result=inspector.inspect(InspectedSource(url,hashlib.sha256(raw).hexdigest(),'',artifact_kind='score_image'))
    assert result.status=='unreadable' and result.dimensions==((200,260),)
    assert result.source_url==url and result.source_sha256==hashlib.sha256(raw).hexdigest()
    inspector.close()


@pytest.mark.parametrize('data,terminal,code',[(dict(readable=False,reason='符号模糊'),'turn.completed',None),(None,'turn.failed','readability_terminal')])
def test_cli_honest_readability_and_terminal(tmp_path,data,terminal,code):
    image=tmp_path/'original.png'; image.write_bytes(png())
    if code:
        with pytest.raises(LabFailure) as err: assess_readability([image],tmp_path/'probe','model',timeout=17,runner=cli(data,terminal))
        assert err.value.code==code
    else:
        seen=[]
        result=assess_readability([image],tmp_path/'probe','model',timeout=17,runner=cli(data,terminal,seen))
        assert result['readable'] is False
        args,kw=seen[0]
        assert args[args.index('--image')+1]==str(image.resolve()) and kw['timeout']==17
        assert '--sandbox' in args and 'features.shell_tool=false' in args
        assert 'Do not transcribe' in kw['text']


def test_cancel_before_cli(tmp_path):
    with pytest.raises(LabFailure) as err:
        assess_readability([],tmp_path,'model',timeout=10,runner=cli(),cancelled=lambda:True)
    assert err.value.code=='cancelled'


def test_bound_original_preview_dedup_budget_and_invalidation(tmp_path,monkeypatch):
    from dataclasses import replace
    raw=png(); url='https://fixture.example/a.png'; seen=[]
    current=[raw,url]
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda *a,**k:(current[0],current[1],'image/png'))
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',lambda *a,**k:dict(images=(png(500,700),),page_count=1))
    inspector=SourceInspector(tmp_path,'model',runner=cli(seen=seen))
    source=InspectedSource(url,hashlib.sha256(raw).hexdigest(),'',artifact_kind='score_image')
    first=inspector.inspect(source)
    assert first.status=='readable' and len(seen)==1
    initial=Path(seen[0][0][seen[0][0].index('--image')+1])
    assert initial.read_bytes()==raw
    assert first.preview_paths[0].read_bytes()!=raw
    assert inspector.inspect(source).status=='readable' and len(seen)==1
    # Same hash at an actually fetched alias reuses only the visual verdict.
    current[1]='https://fixture.example/alias.png'
    alias=inspector.inspect(replace(source,url=current[1]))
    assert alias.source_url==current[1] and len(seen)==1
    for i in (1,2):
        current[:]=[png(600+i,800),f'https://fixture.example/{i}.png']
        checked=inspector.inspect(replace(source,url=current[1],sha256=hashlib.sha256(current[0]).hexdigest()))
        assert checked.status==('readable' if i==1 else 'unavailable')
    assert len(seen)==2 and inspector.probes==2
    preview=first.preview_paths[0]
    inspector.close()
    assert not preview.exists() and not first.public()['previewUrls']


def test_changed_binding_and_bomb_dimensions_do_not_probe(tmp_path,monkeypatch):
    raw=png(); url='https://fixture.example/a.png'
    inspector=SourceInspector(tmp_path,'model',runner=lambda *a,**k:pytest.fail('Unsafe data reached CLI'))
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda *a,**k:(raw,url+'other','image/png'))
    source=InspectedSource(url,hashlib.sha256(raw).hexdigest(),'',artifact_kind='score_image')
    assert inspector.inspect(source).status=='unavailable'
    inspector.close()
    # Genuine PNG envelope with a hostile IHDR must be rejected on metadata,
    # before any pixel decode/renderer/vision.
    import struct, zlib
    body=struct.pack('>IIBBBBB',40001,40001,8,2,0,0,0)
    hostile=raw[:16]+body+struct.pack('>I',zlib.crc32(b'IHDR'+body))+raw[33:]
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda *a,**k:(hostile,url,'image/png'))
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',lambda *a,**k:pytest.fail('Hostile dimensions reached decode'))
    inspector=SourceInspector(tmp_path,'model',runner=cli())
    assert inspector.inspect(InspectedSource(url,hashlib.sha256(hostile).hexdigest(),'',artifact_kind='score_image')).status=='unavailable'
    inspector.close()


def test_actual_img_family_relationships_not_same_site_or_labels(tmp_path,monkeypatch):
    from dataclasses import replace
    page='https://fixture.example/page'; a='https://fixture.example/thumb.png'; b='https://fixture.example/full.png'; other='https://fixture.example/other.png'
    html=f'<html><a href="{b}"><img src="{a}" srcset="{b} 2x"></a><img src="{other}"></html>'.encode()
    resources={page:html,a:png(600,800),b:png(900,1200),other:png(700,900)}
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda url,**k:(resources[url],url,'text/html' if url==page else 'image/png'))
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',lambda *a,**k:dict(images=(png(),),page_count=1))
    origin=InspectedSource(page,hashlib.sha256(html).hexdigest(),'song',(a,b,other))
    def inspect(url):
        return InspectedSource(url,hashlib.sha256(resources[url]).hexdigest(),'',artifact_kind='score_image')
    i=SourceInspector(tmp_path,'model',runner=cli())
    first=i.inspect(inspect(a),(origin,)); second=i.inspect(inspect(b),(origin,))
    assert first.same_version_evidence(second)
    # Unrelated image on exactly the same origin/page is not an image family.
    assert not first.same_version_evidence(replace(second,source_sha256='f'*64,image_families=()))
    assert first.image_families[0][:2]==(page,origin.sha256)
    i.close()


def test_discovery_actual_low_image_then_high_image_feedback(tmp_path,monkeypatch):
    from test_discovery import Provider, call, proposal, PAGE, IMAGE
    from ai_piano_studio.score_lab.service import ScoreLabService
    high='https://music.example/full.png'; raw=png(200,260); original=png(900,1200)
    page=f'<html>Fixture artist<a href="{IMAGE}">small</a><a href="{high}">full</a></html>'.encode()
    def fetch(url,**kw): return (page if url==PAGE else original if url==high else raw),url,('text/html' if url==PAGE else 'image/png')
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',fetch)
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',lambda *a,**k:dict(images=(png(),),text='',page_count=1,rendered_pages=[1]))
    def inspect(url): return call('inspect_music_resource',dict(url=url,title='Fixture',source_url=PAGE))
    p=Provider([[inspect(PAGE),inspect(IMAGE)],[call('propose_song_source',proposal()),inspect(high)],[call('propose_song_source',proposal(high))]])
    service=ScoreLabService(tmp_path,provider_factory=lambda _:p,cli_runner=cli())
    candidate=service.discover('Fixture')
    assert candidate.proposal.selected_url==high and candidate.inspection.status=='readable'
    feedback=json.loads(next(m['content'] for m in p.requests[1][0] if m.get('tool_call_id')=='inspect_music_resource' and '200' in m.get('content','')))
    assert feedback['data']['visual_inspection']['dimensions']==[[200,260]] and not feedback['success']
    paths=candidate.inspection.preview_paths
    with pytest.raises(LabFailure): service.discover('')
    assert all(not p.exists() for p in paths)


def test_elapsed_probe_limit_and_remaining_timeout(tmp_path,monkeypatch):
    from dataclasses import replace
    clock=[100.0]; raw=png(); url='https://fixture.example/a.png'
    monkeypatch.setattr('ai_piano_studio.score_lab.source_inspection.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource',lambda *a,**k:(raw,url,'image/png'))
    monkeypatch.setattr('ai_piano_studio.music_agent.score_reference.render_score_image_reference',lambda *a,**k:dict(images=(raw,),page_count=1))
    seen=[]
    def run(*args,**kwargs):
        seen.append(kwargs['timeout']); cli()(*args,**kwargs); clock[0]=161.0
    inspector=SourceInspector(tmp_path,'model',runner=run,deadline=340)
    source=InspectedSource(url,hashlib.sha256(raw).hexdigest(),'',artifact_kind='score_image')
    result=inspector.inspect(source)
    assert seen==[60] and result.status=='unavailable' and '60' in result.reason
    inspector.close()
    clock[0]=100
    inspector=SourceInspector(tmp_path,'model',runner=run,deadline=117)
    with pytest.raises(LabFailure) as error: inspector.inspect(source)
    assert seen[-1]==17 and error.value.code=='timeout'
    inspector.close()


def test_cancellation_after_initial_image_call_is_not_accepted(tmp_path):
    stopped=[False]; image=tmp_path/'actual.png'; image.write_bytes(png())
    def run(*a,**k): cli()(*a,**k); stopped[0]=True
    with pytest.raises(LabFailure) as error:
        assess_readability([image],tmp_path/'probe','model',timeout=10,runner=run,cancelled=lambda:stopped[0])
    assert error.value.code=='cancelled'


@pytest.mark.parametrize('data', [
    dict(readable=True,reason='clear',extra='not allowed'),
    dict(readable='true',reason='clear'),
    dict(readable=True,reason='x'*401),
])
def test_readability_rejects_invalid_schema_output(tmp_path,data):
    image=tmp_path/'actual.png'; image.write_bytes(png())
    with pytest.raises(LabFailure) as error:
        assess_readability([image],tmp_path/'probe','model',timeout=10,runner=cli(data))
    assert error.value.code=='readability_failed'
