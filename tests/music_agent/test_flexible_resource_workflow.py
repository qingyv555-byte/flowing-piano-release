"""Resources preserve source identity and expose alternative notation routes."""
import json
from ai_piano_studio.music_agent import resource_tools
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback


def test_html_mml_data_becomes_real_multitrack_notes_without_executing_javascript(tmp_path, monkeypatch):
    html = '<h1>Actual source song</h1><script>throw new Error("must not execute"); const score={mml:"MML@t120o4c4d4,o3c2;"};</script>'
    monkeypatch.setattr(resource_tools, 'fetch_public_resource', lambda url, **kw:(html.encode(),url,'text/html'))
    session = resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    result = session.execute('inspect_music_resource',dict(url='https://scores.example/edition',source_url='https://scores.example/edition',title='Requested label'))
    assert result.data['status']=='parsed'
    assert result.data['note_count']==3 and len(result.data['tracks'])>=2
    assert 'Actual source song' in result.data['untrusted_text']
    assert session.resources[result.data['resource_id']].provenance['sourceFormat']=='mml'


def test_page_returns_image_candidates_and_their_labels(tmp_path,monkeypatch):
    html='<main><h1>Requested song</h1><a href="/full.png"><img src="/small.jpg" alt="Requested song staff preview"></a></main>'
    monkeypatch.setattr(resource_tools,'fetch_public_resource',lambda url,**kw:(html.encode(),url,'text/html'))
    result=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None).execute('inspect_music_resource',
        dict(url='https://scores.example/edition',source_url='https://scores.example/edition',title='Requested song'))
    feedback=json.loads(serialize_provider_feedback(result))['data']
    assert 'https://scores.example/small.jpg' in feedback['image_links']
    assert 'https://scores.example/full.png' in feedback['links']


def test_auto_follow_retains_origin_evidence_when_candidate_is_another_song(tmp_path,monkeypatch):
    from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
    def fetch(url,**kw):
        if url.endswith('.mid'):return (CATALOG_HOME/'moonlight1.mid').read_bytes(),url,'audio/midi'
        return b'<h1>Requested Song</h1><a href="/moonlight.mid">Beethoven Moonlight sample</a><a href="/requested.pdf">Requested Song score</a>',url,'text/html'
    monkeypatch.setattr(resource_tools,'fetch_public_resource',fetch)
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None);session.eager_score_reference=True
    result=session.execute('inspect_music_resource',dict(url='https://scores.example/edition',source_url='https://scores.example/edition',title='Requested Song'))
    feedback=json.loads(serialize_provider_feedback(result))['data']
    assert 'Requested Song' in feedback['untrusted_text']
    assert 'Beethoven Moonlight sample' in feedback['untrusted_text']
    assert 'not proof' in feedback['warning'].lower()


def test_standalone_image_is_sent_as_real_score_media_not_as_parsed_notes(tmp_path,monkeypatch):
    from ai_piano_studio.music_agent import score_reference
    from tests.music_agent.test_visual_score_tools import png
    monkeypatch.setattr(resource_tools,'fetch_public_resource',lambda url,**kw:(png(),url,'image/png'))
    monkeypatch.setattr(score_reference,'render_score_image_reference',lambda *a,**kw:
        dict(page_count=1,text='',rendered_pages=[1,1],image_regions=[dict(page=1,region='overview'),dict(page=1,region='upper_systems')],images=(png(),png())))
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    result=session.execute('inspect_music_resource',dict(url='https://scores.example/full.png',source_url='https://scores.example/edition',title='Song'))
    assert result.data['status']=='visual_score' and len(result.data['_provider_images'])==2
    assert not session.resources
    assert session.score_sources[0]['sourceFormat']=='raster_score_images'


def test_invalid_mml_template_does_not_hide_later_real_score_or_page_links(tmp_path,monkeypatch):
    html='<h1>Source</h1><script>const empty="MML@";const score="MML@t120o4c4d4;";</script><a href="/score.mid">MIDI</a>'
    monkeypatch.setattr(resource_tools,'fetch_public_resource',lambda url,**kw:(html.encode(),url,'text/html'))
    session=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None)
    args=dict(url='https://scores.example/page',source_url='https://scores.example/page',title='Song')
    assert session.execute('inspect_music_resource',args).data['note_count']==2
    html='<script>const empty="MML@";</script><a href="/score.mid">MIDI</a>'
    result=session.execute('inspect_music_resource',args)
    assert result.data['status']=='page' and result.data['download_links']


def test_image_source_dimensions_remain_visible_without_blocking_thumbnail(tmp_path,monkeypatch):
    from ai_piano_studio.music_agent import score_reference
    from tests.music_agent.test_visual_score_tools import png
    monkeypatch.setattr(resource_tools,'fetch_public_resource',lambda url,**kw:(png(),url,'image/png'))
    monkeypatch.setattr(score_reference,'render_score_image_reference',lambda *a,**kw:
        dict(page_count=1,text='',rendered_pages=[1,1],image_regions=[dict(page=1,region='overview'),dict(page=1,region='upper_systems')],images=(png(),png()),source_dimensions=[200,260]))
    result=resource_tools.MusicResourceSession(tmp_path,lambda:False,lambda e:None).execute('inspect_music_resource',
        dict(url='https://scores.example/preview.png',source_url='https://scores.example/edition',title='Song'))
    data=json.loads(serialize_provider_feedback(result))['data']
    assert data['status']=='visual_score' and data['source_dimensions']==[200,260]
    assert 'preview' in data['warning'].lower()
