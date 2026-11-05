"""General local execution and extensible tools for the Studio composer.

The workspace is a working directory, not a path allowlist or a sandbox.
Subprocess ownership is about cancellation and cleanup, not command policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from threading import RLock
from uuid import uuid4

from ai_piano_studio.music_agent.tool_contracts import MusicPermission, MusicToolResult
from ai_piano_studio.music_agent.tool_registry import MusicToolDefinition, MusicToolRegistry


CREATOR_TEMPLATE = 'studio_creator'
DEFAULT_CREATOR_INSTRUCTIONS = (
    '你是 Flowing Piano 中的助手，主要与用户一起作曲、编曲和制作音乐。'
    '下面提供当前工程、运行环境、对话和工具，帮助你理解当前状况。\n'
    '默认交互框架：用 report_progress 展示你的计划、当前进展和结果，'
    '按需要使用工具并展示可试听的成果。这个框架可按用户需求和实际任务调整。'
    '\n默认尽量自主完成用户的目标：单个来源不可用时，可以换来源、读取谱图或自行写工具；'
    '只有确实需要用户提供的信息才交回用户。进度里区分已经完成、仍未完成和需要用户输入。'
    '\n默认效率建议（可按任务调整）：独立的检索尽量合并发起；同一来源已明确失败时优先换路径。'
    '找到足够清晰且包含目标段落的正确曲谱后，优先制作所需片段，不为比较更多来源反复搜索。'
    '读取同一谱的其他页或局部时复用已有地址；只有缺页、看不清或曲目不符时再补充查找。'
)


def obj(properties, required=()):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=False)


def tool(name, description, properties, required=(), *, read_only=False):
    return MusicToolDefinition(name, description, obj(properties, required),
                               MusicPermission.READ_PROJECT_SUMMARY, read_only)


TEXT = dict(type='string')
NUMBER = dict(type='number')
INTEGER = dict(type='integer')
BOOLEAN = dict(type='boolean')
ANY_OBJECT = dict(type='object', additionalProperties=True)
CREATOR_DEFINITIONS = (
    tool('workspace_info', 'Read the actual application source/workspace paths, Python, and installed custom tool manifests.', {}, read_only=True),
    tool('read_file', 'Read a local text file. Paths may be absolute or relative to the workspace. Offset and count are characters; omitted count reads the whole file.',
         dict(path=TEXT, offset=INTEGER, count=INTEGER, encoding=TEXT), ('path',), read_only=True),
    tool('write_file', 'Create or replace a local file, including application code, scripts or custom tools. Creates parent directories. Returns actual path and size.',
         dict(path=TEXT, content=TEXT, encoding=TEXT), ('path', 'content')),
    tool('run_command', 'Run a command with the local user account, network and installed environment. Can edit code, install dependencies, run scripts and start services. Returns a command_id if still running; use command_result to continue. No timeout unless supplied. Working directory is not a sandbox.',
         dict(command=TEXT, cwd=TEXT, wait_seconds=NUMBER, timeout_seconds=NUMBER, keep_alive=BOOLEAN), ('command',)),
    tool('command_result', 'Read a command result and incremental stdout/stderr. An offset permits reading the rest of large output. A nonzero exit_code is a real failed command.',
         dict(command_id=TEXT, offset=INTEGER, count=INTEGER, wait_seconds=NUMBER), ('command_id',), read_only=True),
    tool('stop_command', 'Stop this command and its owned children.', dict(command_id=TEXT), ('command_id',)),
    tool('register_workspace_tool', 'Register or update a persistent tool you have implemented. command reads JSON arguments on stdin and writes its result to stdout. Its source code can be edited with write_file or commands. Available immediately through call_workspace_tool and in later chats.',
         dict(name=TEXT, description=TEXT, input_schema=ANY_OBJECT, command=TEXT, cwd=TEXT),
         ('name', 'description', 'input_schema', 'command')),
    tool('call_workspace_tool', 'Run a registered custom tool with JSON arguments. Returns its command_id, stdout, stderr and actual exit_code. Poll command_result if still running.',
         dict(name=TEXT, arguments=ANY_OBJECT, wait_seconds=NUMBER), ('name', 'arguments')),
    tool('report_progress', 'Write or revise the visible task card in your own words. Steps and their order are yours to choose. Replace steps to change the workflow; there is no enforced sequence. Status is your reported task status; tool execution receipts are recorded separately.',
         dict(title=TEXT, message=TEXT, status=TEXT, steps=dict(type='array', items=obj(dict(id=TEXT, label=TEXT, status=TEXT), ('label', 'status'))))),
    tool('ask_user', 'Ask the user a question in the conversation and wait for their reply. Use when information is needed; the user replies in the normal chat box. Cancellation stops waiting.',
         dict(question=TEXT), ('question',)),
    tool('inspect_app', 'Inspect a live application object or property on the UI thread. Roots: editor, assistant, playback. Returns values and available attributes/method signatures. Use paths such as editor.state or editor.saveProject.',
         dict(path=TEXT), ('path',), read_only=True),
    tool('call_app', 'Call an application method on its UI thread. Discover methods with inspect_app or source code. Can control playback, save/import projects, apply suggestions and change application state. Returns the actual result.',
         dict(path=TEXT, args=dict(type='array', items={}), kwargs=ANY_OBJECT), ('path',)),
    tool('set_app_property', 'Set a live application property on its UI thread. Inspect the object or source for the property and value type.',
         dict(path=TEXT, value={}), ('path', 'value')),
    tool('apply_music_changes', 'Apply the currently staged music edits immediately, as an undoable editor operation. This can be chosen by the model when the user wants the change applied.', {}),
    tool('open_app_view', 'Load a QML interface you wrote into the application. Use a Window or ApplicationWindow root with visible: true. Context objects: editor, assistant, playback. The file can import its own local QML components.',
         dict(path=TEXT), ('path',)),
    tool('prepare_file_preview', 'Load a local MIDI or .pianoai.json project into a playable preview. Can be generated by your scripts or custom tools, including multi-song arrangements. No fixed song, duration or source-confirmation workflow.',
         dict(path=TEXT, title=TEXT), ('path',)),
    tool('read_image_file', 'Deliver a local image to the model, for example a generated score, screenshot or analysis plot.',
         dict(path=TEXT), ('path',), read_only=True),
)
CREATOR_NAMES = frozenset(row.name for row in CREATOR_DEFINITIONS)
APP_TOOL_NAMES = frozenset({'inspect_app', 'call_app', 'set_app_property', 'report_progress', 'apply_music_changes', 'open_app_view'})


def with_creator_tools(registry):
    return MusicToolRegistry([*(registry.lookup(n) for n in registry.names), *CREATOR_DEFINITIONS])


@dataclass
class Command:
    process: subprocess.Popen
    directory: Path
    job: object
    turn_id: str
    keep_alive: bool
    started: float
    timeout: float | None
    handles: tuple
    status: str = 'running'


class CreatorWorkspace:
    def __init__(self, home: Path, workspace: Path):
        self.home, self.workspace = Path(home), Path(workspace).resolve()
        self.directory = self.home / 'assistant/creator'
        self.commands: dict[str, Command] = {}
        self.manifest_path = self.directory / 'tools.json'
        self._lock = RLock()
        self._closed = False
        self.receipt_error = ''

    def pending_commands(self, turn_id):
        with self._lock:
            return tuple(key for key, command in self.commands.items()
                         if command.turn_id == turn_id and not command.keep_alive and command.process.poll() is None)

    def record_receipt(self, turn_id, name, result):
        # Metadata only: never save arguments, file contents, stdout or credentials.
        status = result.data.get('status')
        exit_code = result.data.get('exit_code')
        record = dict(at=datetime.now(timezone.utc).isoformat(), tool=name, success=result.success,
                      error_code=result.error_code,
                      status=status if isinstance(status, str) and status in {'running', 'completed', 'failed', 'cancelled', 'timeout',
                          'stopped', 'finished', 'resource_preview', 'proposal', 'unfinished_commands'} else None,
                      exit_code=exit_code if type(exit_code) is int else None)
        try:
            directory = self.directory / 'receipts'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (hashlib.sha256(turn_id.encode()).hexdigest()[:24] + '.jsonl')
            with self._lock, path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        except OSError as error:
            self.receipt_error = type(error).__name__

    def resolve(self, path):
        candidate = Path(path).expanduser()
        return (candidate if candidate.is_absolute() else self.workspace / candidate).resolve()

    def manifests(self):
        if not self.manifest_path.exists():
            return {}
        result = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            raise ValueError('Custom tool manifest must be an object')
        return result

    def info(self):
        return dict(workspace=str(self.workspace), application_source=str(Path(__file__).resolve().parents[3]),
                    python=sys.executable, platform=sys.platform, profile=str(self.home),
                    custom_tools=self.manifests(), tool_manifest=str(self.manifest_path),
                    receipt_directory=str(self.directory / 'receipts'), receipt_error=self.receipt_error,
                    running_commands=[key for key, job in self.commands.items() if job.process.poll() is None])

    def execute(self, name, payload, *, turn_id, cancelled=lambda: False):
        try:
            if name == 'workspace_info':
                data = self.info()
            elif name == 'read_file':
                path = self.resolve(payload['path'])
                content = path.read_text(encoding=payload.get('encoding', 'utf-8-sig'))
                offset = max(0, int(payload.get('offset', 0)))
                count = payload.get('count')
                excerpt = content[offset:] if count is None else content[offset:offset + max(0, int(count))]
                data = dict(path=str(path), content=excerpt, offset=offset, total_characters=len(content),
                            next_offset=offset + len(excerpt) if offset + len(excerpt) < len(content) else None)
            elif name == 'write_file':
                path = self.resolve(payload['path'])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload['content'], encoding=payload.get('encoding', 'utf-8'))
                data = dict(path=str(path), bytes=path.stat().st_size, written=True)
            elif name == 'run_command':
                command_id = self.start(payload, turn_id)
                data = self.result(command_id, wait_seconds=payload.get('wait_seconds', 1), cancelled=cancelled)
            elif name == 'command_result':
                data = self.result(**payload, cancelled=cancelled)
            elif name == 'stop_command':
                self.stop(payload['command_id'])
                data = self.result(payload['command_id'])
            elif name == 'register_workspace_tool':
                manifests = self.manifests()
                if not payload['name'].strip() or not payload['command'].strip():
                    raise ValueError('Tool name and command must not be empty')
                manifests[payload['name']] = dict(payload, cwd=str(self.resolve(payload.get('cwd', '.'))))
                self.directory.mkdir(parents=True, exist_ok=True)
                from ai_piano_studio.persistence.atomic import atomic_write_bytes
                atomic_write_bytes(self.manifest_path, json.dumps(manifests, ensure_ascii=False, indent=2).encode())
                data = dict(registered=True, tool=manifests[payload['name']])
            elif name == 'call_workspace_tool':
                manifest = self.manifests().get(payload['name'])
                if manifest is None:
                    raise ValueError('Custom tool not found; use workspace_info for installed tools')
                command_id = self.start(manifest, turn_id, stdin=json.dumps(payload['arguments'], ensure_ascii=False))
                data = self.result(command_id, wait_seconds=payload.get('wait_seconds', 1), cancelled=cancelled)
            else:
                raise ValueError('Unknown workspace operation')
            if data.get('status') in {'failed', 'timeout'} and 'exit_code' in data:
                return MusicToolResult.failure(name, 'execution_error',
                    f"Command {data['status']}; exit code {data['exit_code']}.", data=data)
            return MusicToolResult.success_result(name, 'Operation executed.', data=data)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return MusicToolResult.failure(name, 'execution_error', str(error), data=dict(exception=type(error).__name__))

    def start(self, payload, turn_id, *, stdin=''):
        with self._lock:
            if self._closed:
                raise RuntimeError('Workspace execution is closed')
            return self._start(payload, turn_id, stdin=stdin)

    def _start(self, payload, turn_id, *, stdin=''):
        timeout = payload.get('timeout_seconds')
        if timeout is not None:
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError('timeout_seconds must be a finite positive number when provided')
        command_id = uuid4().hex
        directory = self.directory / 'commands' / command_id
        directory.mkdir(parents=True)
        (directory / 'stdin.txt').write_text(stdin, encoding='utf-8')
        handles = ((directory / 'stdin.txt').open('rb'), (directory / 'stdout.txt').open('wb'), (directory / 'stderr.txt').open('wb'))
        job = None
        try:
            if os.name == 'nt':
                from ai_piano_studio.music_agent.codex_native import _WindowsProcessJob
                job = _WindowsProcessJob()
                shell = shutil.which('pwsh') or shutil.which('powershell')
                if not shell:
                    raise OSError('PowerShell is unavailable')
                args = [shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                        '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); ' + payload['command']]
            else:
                args = [os.environ.get('SHELL', '/bin/sh'), '-c', payload['command']]
            process = subprocess.Popen(args, cwd=self.resolve(payload.get('cwd', '.')),
                stdin=handles[0], stdout=handles[1], stderr=handles[2],
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
            if job is not None:
                job.attach_and_resume(process)
        except BaseException:
            if job is not None:
                job.close()
            for handle in handles:
                handle.close()
            raise
        self.commands[command_id] = Command(process, directory, job, turn_id,
            bool(payload.get('keep_alive', False)), time.monotonic(), float(timeout) if timeout is not None else None, handles)
        (directory / 'process.json').write_text(json.dumps(dict(command_id=command_id, owner='flowing-piano-creator',
            turn_id=turn_id, pid=process.pid, started_at=datetime.now(timezone.utc).isoformat(),
            cwd=str(self.resolve(payload.get('cwd', '.'))), command=payload['command'], keep_alive=bool(payload.get('keep_alive', False))),
            ensure_ascii=False, indent=2), encoding='utf-8')
        return command_id

    def result(self, command_id, offset=0, count=16000, wait_seconds=0, *, cancelled=lambda: False):
        command = self.commands[command_id]
        # Waiting yields periodically; it does not impose a command runtime limit.
        deadline = time.monotonic() + min(30, max(0, float(wait_seconds)))
        while command.process.poll() is None:
            if cancelled():
                self.stop(command_id, status='cancelled')
                break
            if command.timeout is not None and time.monotonic() - command.started >= command.timeout:
                self.stop(command_id, status='timeout')
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(.05)
        code = command.process.poll()
        if code is not None:
            if command.status == 'running':
                command.status = 'completed' if code == 0 else 'failed'
            self._release(command)
        data = dict(command_id=command_id, status=command.status, exit_code=code,
                    directory=str(command.directory), elapsed_seconds=round(time.monotonic() - command.started, 2))
        offset, count = max(0, int(offset)), max(1, int(count))
        for stream in ('stdout', 'stderr'):
            with (command.directory / f'{stream}.txt').open('rb') as handle:
                handle.seek(offset)
                raw = handle.read(count)
                remaining = bool(handle.read(1))
            data[stream] = raw.decode('utf-8', errors='replace')
            data[f'{stream}_next_offset'] = offset + len(raw)
            data[f'{stream}_has_more'] = remaining
        return data

    def _release(self, command):
        with self._lock:
            if command.job is not None and not command.keep_alive:
                command.job.close()
                command.job = None
            for handle in command.handles:
                handle.close()

    def stop(self, command_id, status='stopped'):
        with self._lock:
            self._stop(command_id, status)

    def _stop(self, command_id, status):
        command = self.commands[command_id]
        if command.process.poll() is None:
            if command.job is not None:
                command.job.close()
                command.job = None
            elif os.name != 'nt':
                os.killpg(command.process.pid, signal.SIGTERM)
            else:
                command.process.terminate()
            try:
                command.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                command.process.kill()
                command.process.wait(timeout=3)
            command.status = status
        command.keep_alive = False
        self._release(command)

    def finish_turn(self, turn_id):
        with self._lock:
            commands = tuple(self.commands.items())
        for command_id, command in commands:
            if command.turn_id == turn_id and not command.keep_alive:
                self.stop(command_id)

    def close(self):
        with self._lock:
            self._closed = True
            commands = tuple(self.commands)
        for command_id in commands:
            self.stop(command_id)
