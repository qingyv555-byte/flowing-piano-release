"""A source page must expose music download evidence beyond its navigation."""
from threading import Event
import json

from ai_piano_studio.music_agent import resource_tools
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
from ai_piano_studio.music_agent.tool_contracts import MusicToolResult


def inspect_html(tmp_path, monkeypatch, html):
    monkeypatch.setattr(resource_tools, "fetch_public_resource", lambda url, **kw:
        (html.encode("utf8"), url, "text/html; charset=utf-8"))
    session = resource_tools.MusicResourceSession(tmp_path, Event().is_set, lambda e: None)
    return session.execute("inspect_music_resource", dict(url="https://scores.example.org/edition",
        title="未列入目录的曲目", source_url="https://scores.example.org/edition"))


def test_download_link_survives_more_than_forty_navigation_links(tmp_path, monkeypatch):
    nav = "".join(f'<a href="/category/{i}">Navigation category {i}</a>' for i in range(100))
    result = inspect_html(tmp_path, monkeypatch, nav + '<main><h1>钢琴谱</h1>'
        '<a href="/files/score-847.mid">Download MIDI</a></main>')
    assert result.data["links"][0] == "https://scores.example.org/files/score-847.mid"
    assert len(result.data["links"]) <= 40


def test_download_label_prioritizes_an_opaque_public_endpoint(tmp_path, monkeypatch):
    nav = "".join(f'<a href="/category/{i}">Menu {i}</a>' for i in range(80))
    result = inspect_html(tmp_path, monkeypatch, nav + '<h1>钢琴谱</h1>'
        '<a href="/fetch.php?edition=847">下载 MIDI 文件</a>')
    assert result.data["download_links"][0] == "https://scores.example.org/fetch.php?edition=847"
    feedback = json.loads(serialize_provider_feedback(result))
    assert feedback["data"]["download_links"] == result.data["download_links"]


def test_music_text_survives_long_navigation_and_hidden_payment_ui(tmp_path, monkeypatch):
    nav = '<nav>' + ('<span>分类导航文字不代表曲谱资料</span>' * 500) + '</nav>'
    result = inspect_html(tmp_path, monkeypatch, nav + '<main><h1>真实曲目标题</h1>'
        '<p>此钢琴编配提供 MIDI 下载和 MusicXML。</p></main>'
        '<div style="display: none"><br>Payment Successful! Download unlocked</div>')
    assert "此钢琴编配提供 MIDI" in result.data["untrusted_text"]
    assert "Payment Successful" not in result.data["untrusted_text"]


def test_optional_li_end_tags_and_bad_unrelated_href_do_not_hide_midi(tmp_path, monkeypatch):
    html = '<ul>' + ''.join(f'<li><a href="/menu/{i}">menu</a>' for i in range(600)) + '</ul>'
    html += '<a href="https://[bad">broken link</a><a href="/song.mid">MIDI download</a>'
    result = inspect_html(tmp_path, monkeypatch, html)
    assert result.data["status"] == "page"
    assert result.data["download_links"][0] == "https://scores.example.org/song.mid"


def test_feedback_keeps_the_only_download_before_long_page_prose():
    source = "https://scores.example.org/page?edition=" + "a" * 1950
    download = "https://scores.example.org/file.mid?edition=" + "b" * 1950
    result = MusicToolResult.success_result("inspect_music_resource", "page", data=dict(
        status="page", url=source, source_url=source, links=[download], download_links=[download],
        untrusted_text="网页曲谱正文" * 800))
    feedback = serialize_provider_feedback(result, max_bytes=16384)
    assert len(feedback.encode("utf8")) <= 16384
    assert json.loads(feedback)["data"]["download_links"] == [download]


def test_embedded_midi_and_public_reference_scripts_are_exposed(tmp_path, monkeypatch):
    result = inspect_html(tmp_path, monkeypatch, '<script src="/player/data.js"></script>'
        '<bgsound src="/music/847.mid"><object data="/music/847.musicxml"></object>')
    assert "https://scores.example.org/music/847.mid" in result.data["download_links"]
    assert "https://scores.example.org/music/847.musicxml" in result.data["download_links"]
    assert result.data["reference_links"] == ["https://scores.example.org/player/data.js"]
    assert json.loads(serialize_provider_feedback(result))["data"]["reference_links"] == result.data["reference_links"]


def test_public_player_metadata_is_read_as_data_without_executing_it(tmp_path, monkeypatch):
    content = b'const song = {midiUrl: "/assets/847.mid"}; throw new Error("Never execute this source");'
    monkeypatch.setattr(resource_tools, "fetch_public_resource", lambda url, **kw:
        (content, url, "text/javascript"))
    session = resource_tools.MusicResourceSession(tmp_path, Event().is_set, lambda e: None)
    result = session.execute("inspect_music_resource", dict(url="https://scores.example.org/player/data.js",
        title="曲谱页面引用的公开播放数据", source_url="https://scores.example.org/edition"))
    assert result.data["status"] == "reference_text"
    assert result.data["download_links"] == ["https://scores.example.org/assets/847.mid"]
    assert "Never execute" in result.data["untrusted_text"]
    assert not session.resources


def test_public_reference_text_cursor_reads_beyond_cached_evidence(tmp_path, monkeypatch):
    content = ('谱' * 12_100 + '\nconst song = {midiUrl: "/assets/847.mid"};').encode()
    monkeypatch.setattr(resource_tools, "fetch_public_resource", lambda url, **kw:
        (content, url, "text/javascript"))
    url = "https://scores.example.org/player/data.js"
    args = dict(url=url, title="曲谱", source_url=url)
    session = resource_tools.MusicResourceSession(tmp_path, Event().is_set, lambda e: None)
    first = json.loads(serialize_provider_feedback(session.execute("inspect_music_resource", args)))["data"]
    assert first["next_text_offset"] == 12_000
    later = session.execute("inspect_music_resource", dict(args, text_offset=first["next_text_offset"]))
    assert later.data["status"] == "reference_text"
    assert "/assets/847.mid" in later.data["untrusted_text"]
    assert later.data["next_text_offset"] is None


def test_accessible_pdf_is_prioritized_over_midi_shopping_and_navigation(tmp_path, monkeypatch):
    html = '<a href="/midi-shop">MIDI downloads</a><a href="/source/847.pdf">Download piano PDF</a>'
    result = inspect_html(tmp_path, monkeypatch, html)
    assert result.data['download_links'][0] == 'https://scores.example.org/source/847.pdf'
