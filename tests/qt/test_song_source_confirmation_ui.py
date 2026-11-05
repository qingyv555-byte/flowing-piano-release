"""Actual Studio startup enables source confirmation and renders its trusted actions."""
import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.source_confirmation import SourceProposal, SourceEvidence
from tests.qt.test_studio_shell import preserved_qsettings_state, studio_session
from tests.qt.test_studio_workflows import click, find_item, qml
from tests.qt.test_studio_workflow_ux import memory_audio


@pytest.mark.parametrize('width', [1000, 1440])
def test_production_studio_waits_for_source_and_card_is_plain_text(studio_session, qtbot, width):
    _app, engine, root, editor, _playback = studio_session
    root.resize(width, 800)
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    assert assistant._require_song_confirmation
    assert assistant._song_source_flow is not None
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioAssistant')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    # Controlled, inspected-source fixture: UI must render it as data, not rich text.
    proposal = SourceProposal('<b>同名歌曲</b>', '测试作者', '钢琴改编', '主歌',
        'https://scores.example.org/score.mid', 'a' * 64, 0,
        (SourceEvidence('https://scores.example.org/song', 'b' * 64, '来源页的歌名与作者'),),
        '比较候选后选择了这份可读乐谱。', '尚未对照原唱录音。')
    assistant._generation += 1
    generation = assistant._generation
    assistant._active_generation = generation
    assistant._task_progress.start(generation, '找一首歌', assistant.currentProviderId, assistant.currentModelId)
    assert assistant._song_source_flow.offer(generation, proposal, '来一首《同名歌曲》',
        (editor.projectSessionId, editor.projectRevision))
    card = root.findChild(QObject, 'songSourceConfirmationCard')
    qtbot.waitUntil(lambda: card is not None and card.isVisible())
    label = root.findChild(QObject, 'songSourceIdentity')
    assert '<b>' in label.property('text')
    assert qml(label, 'textFormat === Text.PlainText')
    selected = root.findChild(QObject, 'songSourceSelectedResource')
    assert selected is not None and selected.isVisible()
    assert proposal.selected_url in selected.property('text')
    assert qml(selected, 'textFormat === Text.PlainText')
    evidence = find_item(root, 'songSourceEvidence')
    assert evidence is not None and evidence.isVisible()
    assert proposal.evidence[0].url in evidence.property('text')
    assert qml(evidence, 'textFormat === Text.PlainText')
    score_copy = find_item(root, 'copySongScoreLink')
    page_copy = find_item(root, 'copySongPageLink')
    qtbot.waitUntil(lambda: page_copy.x() >= score_copy.x() + score_copy.width())
    click(root, 'copySongScoreLink')
    assert QGuiApplication.clipboard().text() == proposal.selected_url
    click(root, 'copySongPageLink')
    assert QGuiApplication.clipboard().text() == proposal.evidence[0].url
    selected.forceActiveFocus()
    selected.selectAll()
    QTest.keyClick(root, Qt.Key_C, Qt.ControlModifier)
    assert proposal.selected_url in QGuiApplication.clipboard().text()
    assert any('选定乐谱资源：' + proposal.selected_url in message.text for message in assistant.messages.entries)
    assert any(proposal.evidence[0].url in message.text for message in assistant.messages.entries)
    assert find_item(root, 'songSourceConfirmButton').isEnabled()
    assert find_item(root, 'songSourceRejectButton').isEnabled()
    assert not assistant.taskPreviewAvailable and not editor.state.playback_notes
    assert card.width() <= width and card.height() < 350
