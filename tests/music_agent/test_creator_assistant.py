"""The default Studio path lets the model choose tools and its visible workflow."""
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.creator_tools import CreatorWorkspace
from ai_piano_studio.music_agent.providers import ProviderEvent
from tests.music_agent.test_assistant_controller import _SerializedLoopProvider, _serialized_loop_assistant, _settings
from tests.music_agent.test_creation_workflow import Playback, done
from tests.music_agent.test_source_confirmation_workflow import call


def creator(tmp_path, scripts):
    editor = EditorController(ProjectState())
    provider = _SerializedLoopProvider(scripts)
    assistant = _serialized_loop_assistant(tmp_path, editor, provider, automatic_music=True,
        playback=Playback(), creator_enabled=True)
    assistant._window_settings = _settings(tmp_path)
    assistant._creator_workspace = CreatorWorkspace(assistant._creation_workflow.home, tmp_path)
    return assistant, provider, editor


def test_character_budget_counts_unicode_and_keeps_latest_history():
    from ai_piano_studio.music_agent.creator_loop import history_with_character_limit
    history = [dict(role='user', content='早期中文'), dict(role='assistant', content='新的回答😀')]
    assert history_with_character_limit(history, 0) == history
    assert history_with_character_limit(history, 5) == [history[1]]
    assert history_with_character_limit(history, 7) == [dict(role='user', content='中文'), history[1]]


def test_context_character_setting_is_persisted_and_used_by_creator(tmp_path, qapp):
    assistant, provider, editor = creator(tmp_path, [(ProviderEvent(text_delta='历史回复'),), (ProviderEvent(text_delta='完成'),)])
    try:
        assert assistant.contextCharacters == 0
        assert assistant.sendMessage('之前的要求')
        done(qapp, assistant)
        assistant.setContextCharacters(2)
        assert assistant._window_settings.value('assistant/contextCharacters', type=int) == 2
        assert assistant.sendMessage('本次输入保持完整')
        done(qapp, assistant)
        request = provider.requests[-1]
        assert request[1] == dict(role='assistant', content='回复')
        assert request[2]['content'].startswith('本次输入保持完整')
    finally:
        assistant.shutdown()


def test_model_authors_status_changes_plan_writes_code_and_reply_is_not_rewritten(tmp_path, qapp):
    answer = '还没生成可播放的 45 秒串烧；我先改进工具。' + '完整反馈。' * 1000
    assistant, provider, editor = creator(tmp_path, [
        call('report_progress', dict(title='两首歌共45秒', message='先创建串接工具',
            steps=[dict(label='编写工具', status='running'), dict(label='制作两段试听', status='pending')])),
        call('write_file', dict(path='new_tool.py', content='def total(values):\n    return sum(values)\n')),
        call('report_progress', dict(message='流程调整：先测试新工具', steps=[dict(label='工具已写好', status='completed'),
            dict(label='测试新工具', status='running')], status='waiting')),
        (ProviderEvent(text_delta=answer, finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('我要改代码来支持30秒加15秒，不要套固定的找歌流程')
        done(qapp, assistant)
        assert (tmp_path / 'new_tool.py').read_text() == 'def total(values):\n    return sum(values)\n'
        assert assistant.taskProgress['title'] == '两首歌共45秒'
        assert [row['label'] for row in assistant.taskProgress['modelSteps']] == ['工具已写好', '测试新工具']
        assert assistant.taskProgress['modelSteps'][-1]['status'] == 'running'
        assert any(row.text == answer for row in assistant.messages.entries)
        assert not editor.state.active_notes
        assert 'Do not' not in provider.requests[0][0]['content']
        assert 'run_command' in str(provider.tool_schemas[0])
        assert 'set_song_task' not in str(provider.tool_schemas[0])
    finally:
        assistant.shutdown()


def test_errors_do_not_force_stop_and_music_tools_keep_real_undoable_output(tmp_path, qapp):
    assistant, provider, editor = creator(tmp_path, [
        call('read_file', dict(path='missing.txt')),
        call('read_file', dict(path='missing.txt')),
        call('read_file', dict(path='missing.txt')),
        call('add_notes', dict(notes=[dict(pitch=60, start_time=50, duration=1, velocity=80)])),
        call('apply_music_changes', {}),
        (ProviderEvent(text_delta='音符已经写入。', finish_reason='stop'),),
    ])
    before = editor.projectRevision
    try:
        assert assistant.sendMessage('自行决定怎么做，直接写到工程')
        done(qapp, assistant)
        assert len(provider.requests) == 6
        assert editor.projectRevision > before
        assert len(editor.state.active_notes) == 1
        assert editor.state.active_notes[0].start_time == 50
        editor.undo()
        assert not editor.state.active_notes
    finally:
        assistant.shutdown()


def test_creator_preview_can_be_adopted_without_forced_audition(tmp_path, qapp):
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.persistence.midi import export_midi
    path = tmp_path / 'two-sections.mid'
    notes = (Note.create(pitch=60, start_time=0, duration=30, velocity=80),
             Note.create(pitch=72, start_time=30, duration=15, velocity=80))
    export_midi(path, notes)
    assistant, provider, editor = creator(tmp_path, [
        call('prepare_file_preview', dict(path=str(path), title='两段45秒验收')),
        (ProviderEvent(text_delta='文件已准备好，可直接采用或试听。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('读取我生成的两段文件')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        assert not assistant.taskPreviewPlayed
        pending = assistant._creation_workflow.pending_song
        assert len(pending.state.active_notes) == 2
        assert max(n.start_time + n.duration for n in pending.state.active_notes) == 45
        assert assistant.applyTaskProject()
        assert any(row.text == '文件已准备好，可直接采用或试听。' for row in assistant.messages.entries)
        assert assistant.taskMessageRow >= 0
    finally:
        assistant.shutdown()


def test_model_opens_own_qml_and_changes_live_property(tmp_path, qapp):
    qml = tmp_path / 'custom.qml'
    qml.write_text('import QtQuick\nimport QtQuick.Window\nWindow { visible: true; width: 200; height: 100; Item { objectName: "creatorOwnedItem"; property string message: "before" } }')
    assistant, provider, editor = creator(tmp_path, [
        call('open_app_view', dict(path=str(qml))),
        call('set_app_property', dict(path='ui:creatorOwnedItem.message', value='after')),
        call('inspect_app', dict(path='ui:creatorOwnedItem.message')),
        (ProviderEvent(text_delta='界面已显示。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('打开我写的界面并更新其文字')
        done(qapp, assistant)
        assert len(assistant._creator_views) == 1
        from ai_piano_studio.music_agent.creator_app import live_object
        assert live_object(assistant, 'ui:creatorOwnedItem.message') == 'after', str(provider.requests[-1])
        assert assistant.lastTurnResult['status'] == 'finished'
    finally:
        assistant.shutdown()


def test_creator_preview_audition_works_before_application(tmp_path, qapp):
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.persistence.midi import export_midi
    path = tmp_path / 'preview.mid'
    export_midi(path, [Note.create(pitch=60, start_time=0, duration=2, velocity=80)])
    assistant, provider, editor = creator(tmp_path, [
        call('prepare_file_preview', dict(path=str(path))),
        (ProviderEvent(text_delta='请试听。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('准备试听')
        done(qapp, assistant)
        assert assistant.auditionTask()
        assert assistant.taskPreviewPlayed
        assert assistant._analysis_playback.playing
        assert assistant.applyTaskProject()
    finally:
        assistant.shutdown()


def test_model_question_waits_for_user_then_continues_same_turn(tmp_path, qapp):
    from tests.music_agent.test_assistant_controller import _wait_until
    assistant, provider, editor = creator(tmp_path, [
        call('ask_user', dict(question='第二段想要明亮还是忧伤？')),
        (ProviderEvent(text_delta='收到，我继续写明亮的第二段。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('先问我第二段想要什么感觉')
        _wait_until(qapp, lambda: any(m.text == '第二段想要明亮还是忧伤？' for m in assistant.messages.entries))
        generation = assistant._active_generation
        assert assistant.isBusy
        assert assistant.sendMessage('明亮一点')
        done(qapp, assistant)
        assert assistant._generation == generation
        assert any('明亮一点' in str(m) for m in provider.requests[-1] if m.get('role') == 'tool')
        assert any(m.text == '收到，我继续写明亮的第二段。' for m in assistant.messages.entries)
    finally:
        assistant.shutdown()


def test_creator_unexpected_error_is_visible_with_actual_detail(tmp_path, qapp):
    assistant, provider, editor = creator(tmp_path, [])
    def fail(*args, **kwargs):
        raise RuntimeError('test executor unavailable: missing piano module')
    provider.stream_events = fail
    try:
        assert assistant.sendMessage('执行任务')
        done(qapp, assistant)
        assert assistant.taskProgress['status'] == 'failed'
        assert any('test executor unavailable: missing piano module' in m.text for m in assistant.messages.entries)
    finally:
        assistant.shutdown()


def test_creator_retains_provider_private_continuation_without_showing_it(tmp_path, qapp):
    from ai_piano_studio.music_agent.providers import ProviderToolCall
    assistant, provider, editor = creator(tmp_path, [
        (ProviderEvent(text_delta='我先看运行环境。', reasoning_content='opaque-continuation',
            tool_calls=(ProviderToolCall('environment', 'workspace_info', '{}'),)),),
        (ProviderEvent(text_delta='环境已确认。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('查看环境')
        done(qapp, assistant)
        transcript = next(m for m in provider.requests[-1] if m.get('tool_calls'))
        assert transcript['content'] == '我先看运行环境。'
        assert transcript['reasoning_content'] == 'opaque-continuation'
        assert all('opaque-continuation' not in m.text for m in assistant.messages.entries)
    finally:
        assistant.shutdown()


def test_creator_can_start_new_project_and_keep_working_in_same_conversation(tmp_path, qapp):
    assistant, provider, editor = creator(tmp_path, [
        call('call_app', dict(path='editor.newProject')),
        call('add_notes', dict(notes=[dict(pitch=64, start_time=0, duration=1, velocity=80)])),
        call('apply_music_changes', {}),
        (ProviderEvent(text_delta='新工程已写好。', finish_reason='stop'),),
        (ProviderEvent(text_delta='这是刚才的新工程。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('新建一个作品并写入音符')
        done(qapp, assistant)
        assert len(editor.state.active_notes) == 1
        assert assistant._last_project_session_id == editor.projectSessionId
        assert assistant.sendMessage('继续查看刚才的工程')
        done(qapp, assistant)
        assert any(m.get('content') == '新工程已写好。' for m in provider.requests[-1])
    finally:
        assistant.shutdown()


def test_creator_reply_without_result_is_not_automatically_task_completed(tmp_path, qapp):
    answer = '找到曲源了，但下载站返回 403，目前还没生成试听。请上传两首歌的 MIDI。'
    assistant, provider, editor = creator(tmp_path, [(ProviderEvent(text_delta=answer, finish_reason='stop'),)])
    try:
        assert assistant.sendMessage('烟花易冷30秒接晴天15秒，生成试听')
        done(qapp, assistant)
        assert assistant.taskProgress['status'] == 'responded'
        assert not assistant.taskPreviewAvailable
        assert any(m.text == answer for m in assistant.messages.entries)
    finally:
        assistant.shutdown()
