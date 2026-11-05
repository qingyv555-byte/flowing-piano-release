"""Recoverable failures, repeated effects, and unfinished work through the real controller."""
import json
import pytest
from urllib.error import HTTPError

from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from tests.music_agent.test_creator_assistant import creator
from tests.music_agent.test_creation_workflow import done
from tests.music_agent.test_source_confirmation_workflow import call


def reply(text='仍需处理。'):
    return (ProviderEvent(text_delta=text, finish_reason='stop'),)


def test_untrusted_result_metadata_does_not_crash_review():
    from ai_piano_studio.music_agent.creator_recovery import RecoveryReview
    from ai_piano_studio.music_agent.tool_contracts import MusicToolResult
    review = RecoveryReview()
    review.observe('report_progress', {'status': []}, MusicToolResult.success_result('report_progress', 'ok'))
    review.observe('run_command', {}, MusicToolResult.failure('run_command', 'execution_error', 'failed',
        data={'exit_code': [], 'status': {}}))
    assert review.feedback()
    assert review.feedback() is None


def test_image_delivery_failure_returns_to_model_without_terminating(tmp_path, qapp):
    from PySide6.QtGui import QImage
    from ai_piano_studio.music_agent.providers import ProviderError
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(0xffffff)
    assert image.save(str(tmp_path/'score.png'))
    assistant, provider, editor = creator(tmp_path, [
        call('read_image_file', dict(path='score.png')),
        call('write_file', dict(path='continued.txt', content='continued')), reply('已继续。')])
    def fail_image(*args):
        raise ProviderError('image transport failed')
    provider.set_tool_result_images = fail_image
    try:
        assert assistant.sendMessage('查看谱图并继续处理')
        done(qapp, assistant)
        result = json.loads(provider.requests[1][-1]['content'])
        assert result['error_code'] == 'execution_error'
        assert result['data']['reason'] == 'image_delivery_error'
        assert (tmp_path/'continued.txt').is_file()
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('contents', [b'', b'not a MIDI file'])
def test_invalid_preview_is_a_tool_error_not_a_playable_result(tmp_path, qapp, contents):
    (tmp_path/'bad.mid').write_bytes(contents)
    assistant, provider, editor = creator(tmp_path, [call('prepare_file_preview', dict(path='bad.mid')), reply()])
    assistant.setCreatorRecoveryReview(False)
    try:
        assert assistant.sendMessage('加载文件')
        done(qapp, assistant)
        assert not assistant.taskPreviewAvailable
        assert not json.loads(provider.requests[1][-1]['content'])['success']
    finally:
        assistant.shutdown()


def test_repeated_identical_call_reuses_receipt_without_writing_again(tmp_path, qapp):
    invocation = call('add_notes', dict(notes=[dict(pitch=60,start_time=0,duration=1,velocity=80)]))
    assistant, provider, editor = creator(tmp_path, [invocation, invocation, call('apply_music_changes', {}), reply()])
    try:
        assert assistant.sendMessage('添加一个音符')
        done(qapp, assistant)
        assert len(editor.state.active_notes) == 1
    finally:
        assistant.shutdown()


def test_provider_error_preserves_real_failure_status(tmp_path, qapp):
    from ai_piano_studio.music_agent.providers import ProviderError
    assistant, provider, editor = creator(tmp_path, [])
    def fail(*args, **kwargs):
        raise ProviderError('Connection lost', code='provider_unavailable')
        yield
    provider.stream_events = fail
    try:
        assert assistant.sendMessage('作曲')
        done(qapp, assistant)
        assert assistant.taskProgress['status'] == 'failed'
        assert assistant.taskProgress['safeErrorCode'] == 'provider_unavailable'
    finally:
        assistant.shutdown()


def test_native_recovery_restart_invalidates_images_and_scopes_call_ids(tmp_path, qapp, monkeypatch):
    from ai_piano_studio.music_agent.assistant_controller import _SerializedGateway
    invalidated = []
    original = _SerializedGateway.invalidate_score_image_delivery
    def invalidate(gateway):
        invalidated.append(True)
        return original(gateway)
    monkeypatch.setattr(_SerializedGateway, 'invalidate_score_image_delivery', invalidate)
    def invocation(name, payload):
        return (ProviderEvent(tool_calls=(ProviderToolCall('session-local-id', name, json.dumps(payload)),)),)
    assistant, provider, editor = creator(tmp_path, [
        invocation('read_file', dict(path='missing')), reply(),
        invocation('write_file', dict(path='recovered.txt', content='new session')), reply('完成。')])
    resets = []
    provider.reset_for_supplement = lambda: resets.append(True) or True
    try:
        assert assistant.sendMessage('失败后继续处理')
        done(qapp, assistant)
        assert resets and invalidated
        assert (tmp_path/'recovered.txt').read_text() == 'new session'
        assert provider.requests[2][1]['role'] == 'user'
        assert '失败后继续处理' in provider.requests[2][1]['content']
    finally:
        assistant.shutdown()


def test_source_403_gets_review_and_model_can_choose_code_instead(tmp_path, qapp, monkeypatch):
    def forbidden(url, *args, **kwargs):
        raise HTTPError(url, 403, 'Forbidden', None, None)
    monkeypatch.setattr('ai_piano_studio.music_agent.resource_tools.fetch_public_resource', forbidden)
    assistant, provider, editor = creator(tmp_path, [
        call('inspect_music_resource', dict(url='https://example.com/song.mid', source_url='https://example.com', title='音乐')),
        call('report_progress', dict(status='blocked', message='下载失败')),
        reply('请上传文件。'),
        call('write_file', dict(path='alternative.py', content='print(30+15)\n')),
        call('report_progress', dict(status='completed', message='已改用代码准备工具')),
        reply('工具文件已写好。'),
    ])
    try:
        assert assistant.sendMessage('自行处理这个音乐任务')
        done(qapp, assistant)
        assert (tmp_path/'alternative.py').is_file()
        assert len(provider.requests) == 6
        assert any(m.get('role') == 'developer' and '收尾复核' in m['content'] for m in provider.requests[3])
        assert assistant.lastTurnResult['providerCalls'] == 6
    finally:
        assistant.shutdown()


def test_same_failure_does_not_cause_infinite_review(tmp_path, qapp):
    scripts = [call('read_file', dict(path='missing')), call('report_progress', dict(status='blocked')), reply()]
    assistant, provider, editor = creator(tmp_path, scripts + [call('read_file', dict(path='missing')), call('report_progress', dict(status='blocked')), reply('没有新证据。')])
    try:
        assert assistant.sendMessage('读取文件')
        done(qapp, assistant)
        assert len(provider.requests) == 6
        assert assistant.taskProgress['status'] == 'responded'
    finally:
        assistant.shutdown()


def test_recovery_review_can_be_disabled(tmp_path, qapp):
    assistant, provider, editor = creator(tmp_path, [call('read_file',dict(path='missing')), reply()])
    assistant.setCreatorRecoveryReview(False)
    try:
        assert assistant.sendMessage('仅尝试一次')
        done(qapp, assistant)
        assert len(provider.requests) == 2
    finally:
        assistant.shutdown()


def test_inspecting_state_does_not_discard_staged_edits_and_receipt_is_true(tmp_path, qapp):
    assistant, provider, editor = creator(tmp_path, [
        call('add_notes', dict(notes=[dict(pitch=60,start_time=0,duration=1,velocity=80)])),
        call('inspect_app', dict(path='editor.state')),
        call('report_progress', dict(message='检查完了，现在应用')),
        call('apply_music_changes', {}), reply('已应用。'),
    ])
    try:
        assert assistant.sendMessage('写入后查看状态再应用')
        done(qapp, assistant)
        assert len(editor.state.active_notes) == 1
        assert assistant.lastTurnResult['committed'] is True
        editor.undo()
        assert not editor.state.active_notes
    finally:
        assistant.shutdown()


def test_duplicate_call_id_never_replays_a_different_write(tmp_path, qapp):
    def write(path, content):
        return (ProviderEvent(tool_calls=(ProviderToolCall('same-id','write_file',json.dumps(dict(path=path,content=content))),)),)
    assistant, provider, editor = creator(tmp_path, [write('first.txt','first'),write('second.txt','second'),reply()])
    assistant.setCreatorRecoveryReview(False)
    try:
        assert assistant.sendMessage('检查重复传输')
        done(qapp, assistant)
        assert (tmp_path/'first.txt').read_text() == 'first'
        assert not (tmp_path/'second.txt').exists()
        receipt = json.loads(provider.requests[-1][-1]['content'])
        assert not receipt['success'] and receipt['error_code'] == 'validation_error'
    finally:
        assistant.shutdown()


def test_running_command_is_not_silently_reported_as_finished(tmp_path, qapp):
    from tests.music_agent.test_creator_workspace import python_command
    script=tmp_path/'slow.py'; script.write_text('import time\ntime.sleep(60)\n')
    assistant, provider, editor = creator(tmp_path, [
        call('run_command',dict(command=python_command(script),wait_seconds=0)), reply('命令已启动。'), reply('稍后完成。'),
    ])
    try:
        assert assistant.sendMessage('执行命令并检查结果')
        done(qapp, assistant)
        assert assistant.taskProgress['safeErrorCode'] == 'unfinished_commands'
        assert assistant.taskProgress['status'] == 'failed'
        assert all(c.process.poll() is not None for c in assistant._creator_workspace.commands.values())
    finally:
        assistant.shutdown()
