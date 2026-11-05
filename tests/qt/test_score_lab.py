from threading import Event
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl, Qt, QPointF
from PySide6.QtTest import QTest

from ai_piano_studio.score_lab.controller import ScoreLabController
from ai_piano_studio.score_lab.contracts import LabCandidate, LabDraft, LabFailure
from ai_piano_studio.music_agent.source_confirmation import SourceProposal, SourceEvidence
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.note import Note
from ai_piano_studio.persistence.project_document import load_project_document
from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.editor.controller import EditorController
from tests.qt.test_playback import FakePlayer
from tests.qt.test_studio_shell import studio_session, click
from tests.qt.test_nocturne import preserved_qsettings_state


@pytest.fixture(autouse=True)
def no_hardware_audio(monkeypatch):
    # Studio note insertion can audition automatically; keep this entire module silent.
    monkeypatch.setattr('ai_piano_studio.audio.player.AudioPlayer.audition', lambda *a, **kw: None)
    monkeypatch.setattr('ai_piano_studio.audio.mixer.PianoVoiceMixer.start_audio', lambda *a, **kw: None)


class Service:
    model_id = 'gpt-6.1-sol'
    def __init__(self, home):
        self.home, self.prepared, self.calls = home, [], []
        self.entered, self.release = Event(), Event()
        self.hold = False
        self.fail = False
    def discover(self, query, seconds, **kw):
        self.calls.append((query, seconds, kw['rejected_urls']))
        self.entered.set()
        if self.hold:
            self.release.wait(3)
        kw['progress']({'message': '实际检查来源中'})
        if self.fail:
            raise LabFailure('unreadable', '谱源不可读，请更换清晰谱源。')
        proposal = SourceProposal(query, '测试作者', '测试编配', '可见页开头', 'https://example.org/score.png', 'a'*64, 0,
            (SourceEvidence('https://example.org/page', 'b'*64, '<b>实际身份依据</b>'),), '实际检查的测试谱源', '编配与录音一致性未知')
        self.last = LabCandidate(str(len(self.calls)), query, proposal, seconds)
        return self.last
    def prepare(self, candidate, **kw):
        assert candidate is self.last
        self.prepared.append(candidate)
        return LabDraft(self.home/'private.json', ProjectState(notes=(Note.create(pitch=60,start_time=0,duration=2),)), candidate,
                        ('覆盖仅 2 秒，未补写。',), self.model_id)


def wait_idle(qtbot, c):
    qtbot.waitUntil(lambda: not c.busy, timeout=5000)


def test_idle_is_cheap(qapp, tmp_path):
    c = ScoreLabController(tmp_path, None)
    assert c.state == 'idle' and not c.busy and not c.candidate and not c.draft
    assert c._service is None
    c.shutdown()


def test_confirm_exact_object_detached_preview_export_and_history(qapp, qtbot, tmp_path):
    editor, player = EditorController(ProjectState()), FakePlayer()
    playback = PlaybackController(editor, player)
    service = Service(tmp_path)
    c = ScoreLabController(tmp_path, playback, service=service)
    before = editor.state
    try:
        c.findSong('线索', 15)
        wait_idle(qtbot, c)
        assert c.state == 'awaiting_confirmation' and not c.draft and not service.prepared
        assert c.candidate['selectedUrl'].endswith('score.png')
        c.confirmSource()
        wait_idle(qtbot, c)
        assert c.state == 'ready' and service.prepared == [service.last]
        c.confirmSource()
        assert len(service.prepared) == 1
        c.audition()
        assert c.playing and player.played[-1] == c._draft.state.playback_notes
        assert playback.detachedPreviewNotes and editor.state == before and not editor.canUndo
        assert c.exportProject(QUrl.fromLocalFile(str(tmp_path/'中文草稿.json')))
        assert load_project_document(tmp_path/'中文草稿.json').state == c._draft.state
        c.stopAudition()
        assert not c.playing and editor.state == before
    finally:
        c.shutdown()


def test_preview_request_is_not_playing_and_unrelated_preview_survives(qapp, qtbot, tmp_path):
    class DelayedPlayer(FakePlayer):
        def play_notes(self, notes):
            self.played.append(notes)
    editor, player = EditorController(ProjectState()), DelayedPlayer()
    playback = PlaybackController(editor, player)
    c = ScoreLabController(tmp_path, playback, service=Service(tmp_path))
    try:
        c.findSong('歌',15); wait_idle(qtbot,c); c.confirmSource(); wait_idle(qtbot,c)
        c.audition()
        assert c.previewStarting and not c.playing
        other = (Note.create(pitch=64,start_time=0,duration=1),)
        playback.play_detached_notes(other)
        stops = player.stop_calls
        c.cancel()
        assert player.stop_calls == stops and playback.detachedPreviewNotes[0]['pitch'] == 64
    finally:
        c.shutdown()


def test_cancel_requery_serializes_and_ignores_stale_result(qapp, qtbot, tmp_path):
    service = Service(tmp_path); service.hold = True
    c = ScoreLabController(tmp_path,None,service=service)
    try:
        c.findSong('旧',15)
        assert service.entered.wait(1)
        c.findSong('新',10)
        assert len(service.calls) == 1 and not c.candidate
        service.hold = False; service.release.set()
        wait_idle(qtbot,c)
        assert c.candidate['title'] == '新' and len(service.calls) == 2
        c.rejectSource('不同版本')
        wait_idle(qtbot,c)
        assert service.calls[-1][2] == ('https://example.org/score.png',)
        assert '不同版本' in service.calls[-1][0]
        c.cancel()
        c.confirmSource()
        assert not c.draft and not service.prepared
    finally:
        service.release.set(); c.shutdown()
    assert not c._thread or not c._thread.is_alive()


def test_failure_and_shutdown_busy(qapp, qtbot, tmp_path):
    service = Service(tmp_path); service.fail = True
    c = ScoreLabController(tmp_path,None,service=service)
    c.findSong('歌',15); wait_idle(qtbot,c)
    assert c.state == 'error' and '清晰' in c.error and not c.candidate
    service.hold = True; service.entered.clear()
    c.findSong('另一首',15)
    assert service.entered.wait(1)
    service.release.set()
    c.shutdown()
    assert not c._thread.is_alive()
    qapp.processEvents()
    assert not c.draft


@pytest.mark.parametrize('width,theme', [(1000,'paper'),(1440,'ink')])
def test_studio_actual_entry_flow_layout_and_input_isolation(studio_session, qtbot, tmp_path, width, theme):
    app, engine, root, editor, playback = studio_session
    c = engine.rootContext().contextProperty('scoreLab')
    c._service = Service(tmp_path)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.beginPreview(); appearance.setValue('themeId',theme); appearance.applyPreview()
    root.resize(width,800)
    editor.createNoteAt(0, 0)
    undo_before = editor.canUndo
    click(root,'studioScoreLabButton')
    lab = root.findChild(QObject,'scoreLabWindow')
    assert lab is not None
    qtbot.waitUntil(lambda: lab.isVisible())
    lab.resize(width,800)
    assert root.property('editorSurfaceOpen')
    keyboard = engine.rootContext().contextProperty('keyboard')
    assert 'score-lab-window' in keyboard._suspended
    before = editor.state
    query = lab.findChild(QObject,'scoreLabQuery')
    query.setProperty('text','测试线索')
    query.forceActiveFocus()
    QTest.keyClick(lab,Qt.Key_Space)
    QTest.keyClick(lab,Qt.Key_Z,Qt.ControlModifier)
    assert editor.state == before and editor.canUndo == undo_before and not playback.playing
    click(lab,'scoreLabFind')
    wait_idle(qtbot,c)
    assert c.state == 'awaiting_confirmation' and not c.draft
    assert c.candidate['evidence'][0]['quote'].startswith('<b>')
    assert lab.findChild(QObject,'scoreLabArtifact').property('text').find('https://example.org/score.png') >= 0
    assert lab.grabWindow().save(str(tmp_path/f'lab-source-{width}-{theme}.png'))
    click(lab,'scoreLabConfirm')
    wait_idle(qtbot,c)
    # Real detached controller backed by an in-memory player; no hardware audio.
    player = FakePlayer(); detached = PlaybackController(editor,player)
    c._playback = detached
    detached.playingChanged.connect(c._playing_changed)
    detached.detachedPreviewChanged.connect(c._detached_changed)
    click(lab,'scoreLabAudition')
    assert c.playing and player.played
    click(lab,'scoreLabStop')
    click(lab,'scoreLabExport')
    dialog = lab.findChild(QObject,'scoreLabSaveDialog')
    qtbot.waitUntil(lambda: dialog.property('visible'))
    dialog.setProperty('selectedFile',QUrl.fromLocalFile(str(tmp_path/'实验.json')))
    dialog.accepted.emit()
    dialog.close()
    assert load_project_document(tmp_path/'实验.json').state == c._draft.state
    assert lab.grabWindow().save(str(tmp_path/f'lab-{width}-{theme}.png'))
    query.textEdited.emit()
    assert editor.state == before and editor.canUndo == undo_before and not playback.playing
    assert c.state == 'idle'  # Editing the input invalidates the confirmed result.
    lab.close()
    qtbot.waitUntil(lambda: not root.property('editorSurfaceOpen'))
    assert 'score-lab-window' not in keyboard._suspended
    click(root,'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    qtbot.waitUntil(lambda: root.findChild(QObject,'studioFileMenuButton') is not None)
    click(root,'studioFileMenuButton')
    action = root.findChild(QObject,'studioScoreLabAction')
    assert action and action.property('enabled')
    action.trigger(None)
    qtbot.waitUntil(lambda: lab.isVisible())
    lab.close()



def test_cancel_during_prepare_drops_late_draft_and_returns_idle(qapp, qtbot, tmp_path):
    class HeldPrepare(Service):
        def prepare(self, candidate, **kw):
            self.entered.set()
            self.release.wait(3)
            return super().prepare(candidate, **kw)
    service = HeldPrepare(tmp_path)
    c = ScoreLabController(tmp_path,None,service=service)
    try:
        c.findSong('歌',15); wait_idle(qtbot,c)
        service.entered.clear()
        c.confirmSource()
        assert service.entered.wait(1)
        c.cancel()
        assert c.state == 'cancelling' and not c.draft and '停止' in c.status
        assert c._thread.daemon is False
        service.release.set(); wait_idle(qtbot,c)
        assert c.state == 'idle' and not c.draft and not c.candidate
    finally:
        service.release.set(); c.shutdown()


@pytest.mark.parametrize('terminal', ['natural', 'manual', 'sync_failure'])
def test_real_detached_signal_order_terminal_status(qapp, qtbot, tmp_path, terminal):
    class SignalOrderPlayer(FakePlayer):
        def play_notes(self, notes):
            if terminal == 'sync_failure':
                raise RuntimeError('in-memory start failure')
            super().play_notes(notes)
        def stop(self, mode, **kwargs):
            super().stop(mode, **kwargs)
            self.playingChanged.emit(False)
    editor, player = EditorController(ProjectState()), SignalOrderPlayer()
    playback = PlaybackController(editor, player)
    c = ScoreLabController(tmp_path, playback, service=Service(tmp_path))
    try:
        c.findSong('歌',15); wait_idle(qtbot,c); c.confirmSource(); wait_idle(qtbot,c)
        c.audition()
        if terminal == 'sync_failure':
            assert '试听未能启动' in c.error
            assert '准备试听' not in c.status
        else:
            assert c.playing
            if terminal == 'natural':
                # The REAL PlaybackController clears detached notes BEFORE
                # emitting playingChanged. Do not shortcut the Qt signal order.
                player.playingChanged.emit(False)
                assert '试听已结束' in c.status
            else:
                c.stopAudition()
                assert '试听已停止' in c.status
            assert not c.error
        assert not c.playing and not c.previewStarting
        assert not c._preview_timeout.isActive()
        assert editor.state == ProjectState() and not editor.canUndo
    finally:
        c.shutdown()


# Public candidate captured by parent actual default-service discovery; no network/replay call.
RECORDED_CANDIDATE = {'id': 'f98b28e1cb004c04966069fbf2e86e9a',
 'query': '胡彦斌的《月光》，就是歌词有“过情关，谁敢闯”的那首。想听M小冯哒编配的钢琴简谱开头，15秒。',
 'durationSeconds': 15.0,
 'sourceSha256': 'cb6417f8575b737820baa9f1b3abfcb771dc9717168cf3ce1640adc5eb8b9d50',
 'title': '月光',
 'artist': '胡彦斌',
 'edition': 'M小冯哒编配钢琴简谱',
 'section': '简谱首页可见开头，拟取15秒（视觉实验）',
 'selectedUrl': 'https://image.jianpujia.com/jianpudq/jianpu40/10c003cd74d94f0a2ec7dd7322d45671.png',
 'startSeconds': 0.0,
 'evidence': [{'url': 'https://www.jianpujia.com/jianpu/249167.html', 'quote': '月光钢琴简谱_胡彦斌演唱'},
              {'url': 'https://www.jianpujia.com/jianpu/249167.html', 'quote': '月光曲谱 由 EOP 大佬 M小冯哒 给大家编配'},
              {'url': 'https://www.jianpujia.com/jianpu/249167.html', 'quote': '过情关 谁敢闯'}],
 'summary': '页面歌手、编配者及歌词线索均吻合；与EXO、徐良和阿悄、王心凌的同名曲区分。已检查该页面链接的首张简谱图片，拟从可见页面开头取15秒。请确认此来源与版本。',
 'uncertainty': '依据转载页面身份说明，未独立核对原发布源；图片尚未转录或核验音符，不能保证与录音一致。当前无播放工具。 尚未取得两份独立身份依据；来源编配及段落仍需用户核对。'}


@pytest.mark.parametrize('width,theme', [(1000,'paper'),(1440,'ink')])
def test_actual_candidate_scopes_discovery_and_collapses_details(studio_session, qtbot, tmp_path, width, theme):
    class RecordedService(Service):
        def discover(self, query, seconds, **kw):
            row = RECORDED_CANDIDATE
            proposal = SourceProposal(row['title'], row['artist'], row['edition'], row['section'],
                row['selectedUrl'], row['sourceSha256'], row['startSeconds'],
                tuple(SourceEvidence(e['url'], '', e['quote']) for e in row['evidence']), row['summary'], row['uncertainty'])
            self.last = LabCandidate(row['id'], row['query'], proposal, row['durationSeconds'])
            return self.last
    app, engine, root, editor, playback = studio_session
    c = engine.rootContext().contextProperty('scoreLab')
    c._service = RecordedService(tmp_path)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.beginPreview(); appearance.setValue('themeId',theme); appearance.applyPreview()
    click(root,'studioScoreLabButton')
    lab = root.findChild(QObject,'scoreLabWindow'); lab.resize(width,800)
    c.findSong(RECORDED_CANDIDATE['query'],15); wait_idle(qtbot,c)
    assert c.state == 'awaiting_confirmation' and not c.draft
    explanation = lab.findChild(QObject,'scoreLabStageExplanation').property('text')
    assert '仅指查找阶段' in explanation and '确认后将转谱' in explanation and '独立试听' in explanation
    assert lab.findChild(QObject,'scoreLabUncertainty').property('text').endswith(RECORDED_CANDIDATE['uncertainty'])
    assert '当前无播放工具。' in c.candidate['uncertainty']
    details = lab.findChild(QObject,'scoreLabSourceDetails')
    assert not details.isVisible()
    artifact = lab.findChild(QObject,'scoreLabArtifact').property('text')
    assert RECORDED_CANDIDATE['selectedUrl'] in artifact and RECORDED_CANDIDATE['sourceSha256'] not in artifact
    assert lab.grabWindow().save(str(tmp_path/f'fix1-source-{width}-{theme}.png'))
    click(lab,'scoreLabDetailsToggle')
    assert details.isVisible()
    assert RECORDED_CANDIDATE['sourceSha256'] in lab.findChild(QObject,'scoreLabFingerprint').property('text')
    assert c.candidate['evidence'] == RECORDED_CANDIDATE['evidence']
    click(lab,'scoreLabDetailsToggle')
    confirm = lab.findChild(QObject,'scoreLabConfirm')
    qtbot.waitUntil(lambda: confirm.mapToScene(QPointF(0,confirm.height())).y() < lab.height())
    click(lab,'scoreLabConfirm'); wait_idle(qtbot,c)
    assert c.state == 'ready' and c.draft['noteCount'] == 1
    assert lab.grabWindow().save(str(tmp_path/f'fix1-ready-{width}-{theme}.png'))
    lab.close()


# Public source assessment + warnings from actual in-app3 run; no notes supplied.
RECORDED_READY_WARNINGS = {'candidate': {'id': 'c49b55e4ed4848c19cabe73b50945fac',
               'query': '胡彦斌的《月光》，就是歌词有“过情关，谁敢闯”的那首。想听M小冯哒编配的钢琴简谱开头，15秒。',
               'durationSeconds': 15.0,
               'sourceSha256': 'cb6417f8575b737820baa9f1b3abfcb771dc9717168cf3ce1640adc5eb8b9d50',
               'title': '月光',
               'artist': '胡彦斌',
               'edition': 'M小冯哒编配钢琴双手简谱',
               'section': '简谱第一页可见开头，拟取15秒',
               'selectedUrl': 'https://image.jianpujia.com/jianpudq/jianpu40/10c003cd74d94f0a2ec7dd7322d45671.png',
               'startSeconds': 0.0,
               'evidence': [{'url': 'https://www.jianpujia.com/jianpu/249167.html', 'quote': '月光钢琴简谱_胡彦斌演唱'},
                            {'url': 'https://www.jianpujia.com/jianpu/249167.html', 'quote': '由 EOP 大佬 M小冯哒 给大家编配'},
                            {'url': 'https://www.jianpujia.com/jianpu/249167.html', 'quote': '过情关 谁敢闯'}],
               'summary': '页面的歌手、编配者及歌词线索均吻合，排除EXO、徐良和王心凌的同名歌曲。已检查该页链接的首张谱图，拟从可见谱面开头取15秒，请确认来源与版本。',
               'uncertainty': '编配归属依据该转载页，尚无独立来源佐证；谱图尚未转录核验，不代表原录音或保证与原录音开头一致。当前无播放工具。 尚未取得两份独立身份依据；来源编配及段落仍需用户核对。'},
 'warnings': ['编配归属依据该转载页，尚无独立来源佐证；谱图尚未转录核验，不代表原录音或保证与原录音开头一致。当前无播放工具。 尚未取得两份独立身份依据；来源编配及段落仍需用户核对。',
              '第2、3小节叠置和弦的八度点较密，八度辨读存在不确定性。',
              '第4小节右手小音符2按倚音处理，占用目标音开头的1/16拍；实际演奏时值可略有不同。',
              '延音线连接的同音已合并；琶音和弦按同时起音记录，未细分扫奏时差。',
              '已读至第7小节末，共28拍，约15.27秒，供应用裁切至15秒。',
              '实验转谱结果，需核对实际谱面及试听。']}


def test_ready_card_scopes_only_exact_historical_source_warning(studio_session, qtbot, tmp_path):
    from dataclasses import replace
    class ReadyWarningsService(Service):
        def discover(self, query, seconds, **kw):
            candidate = super().discover(query, seconds, **kw)
            row = RECORDED_READY_WARNINGS['candidate']
            proposal = SourceProposal(row['title'],row['artist'],row['edition'],row['section'],
                row['selectedUrl'],row['sourceSha256'],row['startSeconds'],
                tuple(SourceEvidence(e['url'],'',e['quote']) for e in row['evidence']),row['summary'],row['uncertainty'])
            self.last = replace(candidate, proposal=proposal)
            return self.last
        def prepare(self, candidate, **kw):
            draft = super().prepare(candidate, **kw)
            warnings = RECORDED_READY_WARNINGS['warnings']
            # Source warning deliberately not first; a similar transcription
            # warning must NOT inherit the historical source-stage label.
            return replace(draft,warnings=tuple([*warnings[1:3],warnings[0],*warnings[3:],
                                                 '转谱补充：'+candidate.proposal.uncertainty]))
    app, engine, root, editor, playback = studio_session
    c = engine.rootContext().contextProperty('scoreLab'); c._service = ReadyWarningsService(tmp_path)
    click(root,'studioScoreLabButton')
    lab = root.findChild(QObject,'scoreLabWindow'); lab.resize(1000,900)
    c.findSong(RECORDED_READY_WARNINGS['candidate']['query'],15); wait_idle(qtbot,c)
    c.confirmSource(); wait_idle(qtbot,c)
    assert c.state == 'ready'
    pending, labels = [lab.contentItem()], []
    while pending:
        item = pending.pop()
        if item.property('text') is not None:
            labels.append(item)
        pending.extend(item.childItems())
    historical = [obj for obj in labels if '查找阶段的历史说明' in str(obj.property('text'))]
    assert len(historical) == 1
    text = historical[0].property('text')
    raw = RECORDED_READY_WARNINGS['candidate']['uncertainty']
    assert text.endswith(raw) and '现已转谱' in text and '可独立试听' in text
    assert '当前无播放工具。' in text
    for warning in RECORDED_READY_WARNINGS['warnings'][1:]:
        assert any(obj.property('text') == warning for obj in labels)
    assert any(obj.property('text') == '转谱补充：'+raw for obj in labels)
    before = editor.state
    player = FakePlayer(); detached = PlaybackController(editor,player)
    c._playback = detached
    detached.playingChanged.connect(c._playing_changed)
    detached.detachedPreviewChanged.connect(c._detached_changed)
    # Qt button remains connected to the real detached controller.
    action = lab.findChild(QObject,'scoreLabAudition')
    action.clicked.emit()
    assert c.playing and player.played[-1] == c._draft.state.playback_notes
    c.stopAudition()
    assert editor.state == before and not editor.canUndo and not playback.playing
    lab.close()


def test_recovery_budget_and_unrelated_assistant_busy(qapp, qtbot, tmp_path):
    class Failing(Service):
        def prepare(self, candidate, **kw):
            raise LabFailure('unreadable','分辨率过低',stage='transcribing', sourceUrl=candidate.proposal.selected_url,sourceSha256=candidate.proposal.selected_sha256)
    class Assistant:
        isBusy = False
    assistant = Assistant()
    service = Failing(tmp_path)
    c = ScoreLabController(tmp_path,None,service=service,assistant=assistant)
    try:
        c.findSong('用户20秒',20); wait_idle(qtbot,c)
        for round in (1,2):
            c.confirmSource(); wait_idle(qtbot,c)
            assert c.state == 'awaiting_confirmation' and c.recoveryRounds == round
            assert service.calls[-1][1] == 20
        c.confirmSource(); wait_idle(qtbot,c)
        assert c.state == 'error' and c.recoveryRounds == 2 and len(service.calls) == 3
        c.findSong('另一首',10); wait_idle(qtbot,c)
        assistant.isBusy = True
        c.confirmSource(); wait_idle(qtbot,c)
        assert c.state == 'error' and c.recoveryRounds == 0 and '其他任务' in c.status
        assert assistant.isBusy
    finally:
        c.shutdown()


def test_recovery_real_assistant_panel_history_links_and_topbar(studio_session, qtbot, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    app,engine,root,editor,playback = studio_session
    c = engine.rootContext().contextProperty('scoreLab')
    from ai_piano_studio.__main__ import initialize_deferred_services
    assistant = initialize_deferred_services(engine)
    class FailingOnce(Service):
        def prepare(self,candidate,**kw):
            raise LabFailure('unreadable','真实低清诊断',stage='transcribing',sourceUrl=candidate.proposal.selected_url,sourceSha256=candidate.proposal.selected_sha256)
        def discover(self,query,seconds,**kw):
            result = super().discover(query,seconds,**kw)
            kw['progress'](dict(stage='checking',title='实际检查的版本',url='https://example.org/new',message='正在核对'))
            return result
    c._service = FailingOnce(tmp_path)
    before, undo = editor.state, editor.canUndo
    root.setProperty('pageState','editor')
    qtbot.wait(50)
    click(root,'studioScoreLabTopBarButton')
    lab = root.findChild(QObject,'scoreLabWindow')
    assert lab.isVisible() and assistant.songRecoveryTask is c
    c.findSong('用户线索',20); wait_idle(qtbot,c)
    c.confirmSource(); wait_idle(qtbot,c)
    assert c.state == 'awaiting_confirmation' and not c.draft
    lab.close()
    # Reopen after close is intentionally a cancellation boundary.
    c.findSong('用户线索',20); wait_idle(qtbot,c)
    c.confirmSource(); wait_idle(qtbot,c)
    click(root,'studioAssistant')
    qtbot.waitUntil(lambda: bool(root.property('studioAssistantOpen')))
    panel = root.findChild(QObject,'songRecoveryFeedback')
    assert panel is not None and panel.isVisible()
    assert any('真实低清诊断' in m.text for m in assistant.messages.entries)
    assert editor.state == before and editor.canUndo == undo
    opened=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toString()) or True)
    assert c.openSource('https://example.org/new') and opened == ['https://example.org/new']
    assert not c.openSource('file:///tmp/private') and not c.openSource('javascript:alert(1)')
    assistant.clearConversation()
    assert not c.liveCandidates and c.state == 'idle'


def test_cancel_recovery_ignores_late_candidate_and_progress(qapp,qtbot,tmp_path):
    class HeldRecovery(Service):
        def prepare(self,candidate,**kw):
            self.hold = True
            raise LabFailure('unreadable','低清')
    service = HeldRecovery(tmp_path)
    c = ScoreLabController(tmp_path,None,service=service)
    try:
        c.findSong('歌曲',20); wait_idle(qtbot,c)
        service.entered.clear()
        c.confirmSource()
        qtbot.waitUntil(lambda:c.state=='recovering',timeout=2000)
        qtbot.waitUntil(lambda:len(service.calls)==2,timeout=2000)
        c.cancel()
        service.release.set()
        wait_idle(qtbot,c)
        assert c.state=='idle' and not c.candidate and not c.liveCandidates and not c.draft
    finally:
        service.release.set();c.shutdown()


def test_corrections_continue_request_without_forgetting_exclusions_or_budget(qapp,qtbot,tmp_path):
    class Correcting(Service):
        def __init__(self,home):
            super().__init__(home); self.contexts=[]
        def discover(self,query,seconds,**kw):
            self.contexts.append(kw)
            result=super().discover(query,seconds,**kw)
            if len(self.contexts)==1:
                for i in range(40):
                    kw['progress'](dict(message='检查失败',_inspection=dict(
                        requestedUrl=f'https://example.org/failed/{i}',sourceUrl=f'https://example.org/failed/{i}',
                        sourceSha256='',status='unavailable',outcome='rejected')))
            return result
        def prepare(self,candidate,**kw):
            raise LabFailure('unreadable','低清')
    service=Correcting(tmp_path)
    c=ScoreLabController(tmp_path,None,service=service)
    try:
        c.findSong('歌',20);wait_idle(qtbot,c)
        c.confirmSource();wait_idle(qtbot,c)
        assert c.recoveryRounds==1
        previous_failures=list(c._failures)
        c.rejectSource('另一编配');wait_idle(qtbot,c)
        assert c.recoveryRounds==1 and c._failures==previous_failures
        assert service.contexts[-1]['rejected_hashes']==('a'*64,)
        assert len(service.calls[-1][2])==41 and 'https://example.org/failed/0' in service.calls[-1][2]
        assert service.contexts[-1]['recovery_context']['failures']==previous_failures
        assert '另一编配' in c.query
        c.supplementClue('另一歌手');wait_idle(qtbot,c)
        assert c.recoveryRounds==1 and service.contexts[-1]['rejected_hashes']==('a'*64,)
        c.confirmSource();wait_idle(qtbot,c)
        assert c.recoveryRounds==2
        c.rejectSource('再核对');wait_idle(qtbot,c)
        c.confirmSource();wait_idle(qtbot,c)
        assert c.state=='error' and c.recoveryRounds==2
        c.findSong('全新歌曲',10);wait_idle(qtbot,c)
        assert c.recoveryRounds==0 and service.calls[-1][2]==()
        assert not c._rejected_hashes and not c._failures
    finally:
        c.shutdown()


def test_clear_active_recovery_mutes_old_notices_but_new_tasks_still_report(studio_session,qtbot,tmp_path):
    from ai_piano_studio.__main__ import initialize_deferred_services
    app,engine,root,editor,playback=studio_session
    assistant=initialize_deferred_services(engine)
    c=engine.rootContext().contextProperty('scoreLab')
    class HeldRecovery(Service):
        def prepare(self,candidate,**kw):
            self.hold=True
            raise LabFailure('unreadable','低清恢复诊断')
    service=HeldRecovery(tmp_path)
    c._service=service
    try:
        c.findSong('清除前的歌',20);wait_idle(qtbot,c)
        c.confirmSource()
        qtbot.waitUntil(lambda:c.state=='recovering' and len(service.calls)==2,timeout=2000)
        assert c.busy and any('清除前的歌' in m.text for m in assistant.messages.entries)
        assistant.clearConversation()
        assert not assistant.messages.entries and c.state=='cancelling'
        service.release.set()
        wait_idle(qtbot,c)
        qtbot.wait(100)  # Drain worker done/queued changed signals after the clear.
        assert not assistant.messages.entries
        assert c._thread is None and c._cancel.is_set()
        assert c.state=='idle' and not c.candidate and not c.draft and not c.liveCandidates
        service.hold=False
        c.findSong('清除后的新歌',20);wait_idle(qtbot,c)
        c.confirmSource();wait_idle(qtbot,c)
        assert c.state=='awaiting_confirmation'
        assert any('清除后的新歌' in m.text for m in assistant.messages.entries)
        assert not any('清除前的歌' in m.text for m in assistant.messages.entries)
        c.cancel()  # Ordinary cancellation is still useful and remains reportable.
        assert '已取消' in assistant.messages.entries[-1].text
    finally:
        service.release.set()
        c.cancel()
        if c.busy:
            wait_idle(qtbot,c)


@pytest.mark.parametrize('variant,automatically_prepares', [
    ('same_family', True),
    ('different_edition', False),
    ('different_section', False),
    ('unrelated_image', False),
    ('mismatched_proof_hash', False),
])
def test_approved_source_only_auto_continues_with_private_same_version_proof(
        qapp, qtbot, tmp_path, variant, automatically_prepares):
    from dataclasses import replace
    from ai_piano_studio.score_lab.source_inspection import SourceInspection

    class RecoverVariant(Service):
        def discover(self, query, seconds, **kw):
            original = super().discover(query, seconds, **kw)
            number = len(self.calls) - 1
            url = f'https://example.org/score-{number}.png'
            sha = ('a' if number == 0 else 'b') * 64
            shared = ('https://example.org/origin', 'f' * 64,
                      ('https://example.org/score-0.png', 'https://example.org/score-1.png'))
            families = (shared,) if number == 0 or variant != 'unrelated_image' else ()
            inspection_hash = 'c' * 64 if number == 1 and variant == 'mismatched_proof_hash' else sha
            inspection = SourceInspection(url, inspection_hash, 'readable', '已核对实际谱面',
                                          ((2500, 3400),), 1, (), (), families)
            proposal = replace(original.proposal, selected_url=url, selected_sha256=sha)
            if number == 1 and variant == 'different_edition':
                proposal = replace(proposal, edition='完全不同的编配')
            if number == 1 and variant == 'different_section':
                proposal = replace(proposal, section='副歌')
            self.last = replace(original, proposal=proposal, inspection=inspection)
            return self.last

        def prepare(self, candidate, **kw):
            self.prepared.append(candidate)
            if len(self.prepared) == 1:
                raise LabFailure('unreadable', '第一次谱面转录失败，需要另找高清谱')
            return LabDraft(tmp_path/'draft.json',
                            ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)),
                            candidate, (), self.model_id)

    service = RecoverVariant(tmp_path)
    controller = ScoreLabController(tmp_path, None, service=service)
    try:
        controller.findSong('同一首歌的开头', 20)
        wait_idle(qtbot, controller)
        assert controller.state == 'awaiting_confirmation'
        assert not service.prepared
        controller.confirmSource()
        qtbot.waitUntil(lambda: not controller.busy and controller.recoveryRounds == 1
                        and controller.state in {'ready', 'awaiting_confirmation'}, timeout=5000)
        assert len(service.calls) == 2
        assert controller.state == ('ready' if automatically_prepares else 'awaiting_confirmation')
        assert len(service.prepared) == (2 if automatically_prepares else 1)
        if not automatically_prepares:
            assert controller.candidate['edition'] == (
                '完全不同的编配' if variant == 'different_edition' else '测试编配')
    finally:
        controller.shutdown()


def test_cancel_releases_idle_managed_candidate_preview(qapp, qtbot, tmp_path):
    from dataclasses import replace
    from ai_piano_studio.score_lab.source_inspection import SourceInspection

    preview = tmp_path/'preview.png'

    class ManagedService(Service):
        def __init__(self, home):
            super().__init__(home)
            self.invalidations = 0
        def discover(self, query, seconds, **kw):
            candidate = super().discover(query, seconds, **kw)
            preview.write_bytes(b'isolated-preview')
            self.last = replace(candidate, inspection=SourceInspection(
                candidate.proposal.selected_url, candidate.proposal.selected_sha256,
                'readable', '清晰', ((1000, 1400),), 1, (preview,)))
            return self.last
        def invalidate_candidate(self):
            self.invalidations += 1
            preview.unlink(missing_ok=True)

    service = ManagedService(tmp_path)
    controller = ScoreLabController(tmp_path, None, service=service)
    try:
        controller.findSong('一首歌', 15)
        wait_idle(qtbot, controller)
        assert preview.exists() and controller.candidate['inspection']['previewUrls']
        controller.cancel()
        assert not preview.exists() and service.invalidations >= 1
        assert controller.state == 'idle' and not controller.candidate
    finally:
        controller.shutdown()


def test_real_score_preview_is_local_zoomable_and_does_not_edit_project(
        studio_session, qtbot, tmp_path):
    from dataclasses import replace
    from PySide6.QtGui import QImage
    from ai_piano_studio.score_lab.source_inspection import SourceInspection

    preview = tmp_path/'managed-score.png'
    picture = QImage(500, 700, QImage.Format_RGB32)
    picture.fill(Qt.white)
    assert picture.save(str(preview))

    class PreviewService(Service):
        def discover(self, query, seconds, **kw):
            candidate = super().discover(query, seconds, **kw)
            inspection = SourceInspection(candidate.proposal.selected_url,
                candidate.proposal.selected_sha256, 'readable', '音符和节奏标记可辨认',
                ((2500, 3400),), 1, (preview,))
            self.last = replace(candidate, inspection=inspection)
            return self.last

    app, engine, root, editor, playback = studio_session
    controller = engine.rootContext().contextProperty('scoreLab')
    controller._service = PreviewService(tmp_path)
    before, undo = editor.state, editor.canUndo
    click(root, 'studioScoreLabButton')
    window = root.findChild(QObject, 'scoreLabWindow')
    controller.findSong('预览检查', 15)
    wait_idle(qtbot, controller)
    image = window.findChild(QObject, 'scoreLabPreviewImage')
    zoom = window.findChild(QObject, 'scoreLabPreviewZoom')
    quality = window.findChild(QObject, 'scoreLabPreviewQuality')
    panel = next(obj for obj in window.findChildren(QObject, 'scoreLabPreviewPanel') if obj.isVisible())
    confirm = window.findChild(QObject, 'scoreLabConfirm')
    assert image is not None and zoom is not None
    assert quality is not None and '2500×3400' in quality.property('text')
    assert '可读' in quality.property('text')
    # Untrusted model-supplied readability reasons must never be Qt rich text
    # (including image tags with remote URLs).
    qml_text = (Path(__file__).resolve().parents[2] /
                'src/ai_piano_studio/qml/ScoreLabWindow.qml').read_text(encoding='utf-8')
    quality_source = qml_text.split('objectName: "scoreLabPreviewQuality"', 1)[1][:120]
    assert 'textFormat: Text.PlainText' in quality_source
    assert panel.property('y') < confirm.parent().property('y'), '谱面必须显示在确认按钮之前'
    qtbot.waitUntil(lambda: image.property('implicitWidth') > 0, timeout=5000)
    assert QUrl(image.property('source')).isLocalFile()
    assert 'managed-score.png' in QUrl(image.property('source')).toLocalFile()
    width_before = image.property('width')
    zoom.setProperty('value', 2.5)
    qtbot.waitUntil(lambda: image.property('width') > width_before, timeout=3000)
    assert editor.state == before and editor.canUndo == undo
    window.close()
