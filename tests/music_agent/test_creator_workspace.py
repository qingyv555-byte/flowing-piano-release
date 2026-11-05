"""Real local execution, persistent extensions, and user-owned runtime state."""
from pathlib import Path
import sys

from ai_piano_studio.music_agent.creator_tools import CreatorWorkspace


def python_command(path):
    if sys.platform == 'win32':
        return "& '" + sys.executable.replace("'", "''") + "' '" + str(path).replace("'", "''") + "'"
    import shlex
    return shlex.join([sys.executable, str(path)])


def test_model_can_write_real_code_outside_working_directory_and_run_registered_tool(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    runtime = CreatorWorkspace(tmp_path / 'profile', workspace)
    source = tmp_path / 'another-project' / 'transform.py'
    try:
        written = runtime.execute('write_file', dict(path=str(source), content=(
            'import json, sys\nrequest = json.load(sys.stdin)\n'
            'print(json.dumps({"duration_seconds": sum(request["segments"])}))\n')), turn_id='test')
        assert written.success and source.is_file()
        result = runtime.execute('register_workspace_tool', dict(name='combine_durations',
            description='Sum arranged segment lengths', input_schema={'type': 'object'},
            command=python_command(source)), turn_id='test')
        assert result.success
        restored = CreatorWorkspace(tmp_path / 'profile', workspace)
        try:
            executed = restored.execute('call_workspace_tool', dict(name='combine_durations',
                arguments={'segments': [30, 15]}, wait_seconds=10), turn_id='test')
            assert executed.success, executed
            assert executed.data['exit_code'] == 0
            assert '45' in executed.data['stdout']
            assert restored.commands[executed.data['command_id']].job is None
        finally:
            restored.close()
    finally:
        runtime.close()


def test_command_receipt_is_real_and_cancellation_reaps_its_process(tmp_path):
    runtime = CreatorWorkspace(tmp_path / 'profile', tmp_path)
    source = tmp_path / 'long.py'
    source.write_text('import time\nprint("running", flush=True)\ntime.sleep(90)\n')
    try:
        command_id = runtime.start(dict(command=python_command(source)), 'test')
        result = runtime.result(command_id, wait_seconds=1)
        assert result['exit_code'] is None and result['status'] == 'running'
        result = runtime.result(command_id, wait_seconds=1, cancelled=lambda: True)
        assert result['status'] == 'cancelled'
        assert runtime.commands[command_id].process.poll() is not None
    finally:
        runtime.close()


def test_nonzero_command_exit_is_a_failure(tmp_path):
    runtime = CreatorWorkspace(tmp_path/'profile', tmp_path)
    source = tmp_path/'fail.py'
    source.write_text('raise SystemExit(7)\n')
    try:
        command = python_command(source)
        if sys.platform == 'win32':
            command += '; exit $LASTEXITCODE'
        result = runtime.execute('run_command', dict(command=command, wait_seconds=10), turn_id='test')
        assert not result.success and result.data['exit_code'] == 7
    finally:
        runtime.close()


def test_receipts_only_store_metadata_and_io_failure_is_observable(tmp_path, monkeypatch):
    from ai_piano_studio.music_agent.tool_contracts import MusicToolResult
    runtime = CreatorWorkspace(tmp_path/'profile', tmp_path)
    result = MusicToolResult.success_result('run_command', 'PRIVATE SUMMARY',
        data=dict(stdout='PRIVATE OUTPUT', status='PRIVATE STATUS', exit_code=0))
    try:
        runtime.record_receipt('test', 'run_command', result)
        text = next((runtime.directory/'receipts').glob('*.jsonl')).read_text()
        assert 'PRIVATE' not in text and 'run_command' in text
        original = Path.open
        def denied(path, *args, **kwargs):
            if path.suffix == '.jsonl':
                raise PermissionError('PRIVATE ERROR')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', denied)
        runtime.record_receipt('test', 'run_command', result)
        assert runtime.info()['receipt_error'] == 'PermissionError'
    finally:
        runtime.close()
