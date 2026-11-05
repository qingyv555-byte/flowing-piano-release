"""Bounded initial-image CLI transport; never uses dynamic tool images."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from ai_piano_studio.music_agent.codex_provider import _cli_env, find_codex
from ai_piano_studio.music_agent.codex_native import _WindowsProcessJob
from ai_piano_studio.music_agent.score_reference import _stop_renderer
from .contracts import LabFailure, TRANSCRIPTION_SCHEMA, validate_transcription


def owned_cli(path, arguments, *, text, cwd, timeout, cancelled, on_event, on_progress):
    """Drain bounded output, record only owned process identity and event types."""
    job = Path(cwd)
    with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as outgoing, tempfile.TemporaryFile() as errors:
        incoming.write(text.encode('utf-8'))
        incoming.seek(0)
        container = _WindowsProcessJob() if os.name == 'nt' else None
        process = None
        record = dict(owner='score_lab', startedAt=time.time(), purpose=('Initial-image readability' if any('readability-schema.json' in str(a) for a in arguments) else 'Initial-image transcription'), status='starting')
        try:
            process = subprocess.Popen([path, *arguments], stdin=incoming, stdout=outgoing, stderr=errors,
                cwd=cwd, env=_cli_env(), start_new_session=os.name != 'nt',
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == 'nt' else 0)
            if container is not None:
                container.attach_and_resume(process)
            record.update(pid=process.pid, status='running')
            (job/'process.json').write_text(json.dumps(record), encoding='utf-8')
            started, heartbeat = time.monotonic(), 0
            while True:
                if cancelled():
                    raise LabFailure('cancelled', '已取消找歌实验。')
                if os.fstat(outgoing.fileno()).st_size + os.fstat(errors.fileno()).st_size > 4_000_000:
                    raise LabFailure('output_limit', '识谱输出超过限制。')
                if any(p.stat().st_size > 1_000_000 for pattern in ('transcription-*.json','readability-result.json') for p in job.glob(pattern)):
                    raise LabFailure('output_limit', '识谱结构输出超过限制。')
                if process.poll() is not None:
                    break
                now = time.monotonic()
                if now-started >= timeout:
                    raise LabFailure('timeout', '识谱等待超时，请重试或更换谱源。')
                if now-heartbeat >= 1:
                    on_progress(None)
                    heartbeat = now
                time.sleep(.05)
            if process.returncode:
                raise LabFailure('cli_failed', '独立识谱进程失败；请检查 Codex 登录与连接后重试。')
            outgoing.seek(0)
            for line in outgoing.read(4_000_001).splitlines():
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(event, dict):
                    on_event(event)
        finally:
            _stop_renderer(process, container)
            record.update(status='exited', completedAt=time.time())
            (job/'process.json').write_text(json.dumps(record), encoding='utf-8')


def transcribe(images, job, model_id, seconds, *, runner=None, cancelled=lambda: False, progress=lambda event: None):
    job = Path(job).resolve()
    job.mkdir(parents=True, exist_ok=True)
    if (not 1 <= len(images) <= 4 or
                any(not Path(p).is_file() or Path(p).is_symlink() or Path(p).stat().st_size > 8_000_000
                    or not Path(p).read_bytes().startswith((b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff')) for p in images)
                or sum(Path(p).stat().st_size for p in images) > 8_000_000):
        raise LabFailure('images', '本次没有可用的有界 PNG/JPEG 谱面。')
    executable = find_codex() if runner is None else 'injected-codex'
    if not executable:
        raise LabFailure('cli_unavailable', '未找到已登录的 Codex CLI。')
    runner = runner or owned_cli
    schema = job/'transcription-schema.json'
    schema.write_text(json.dumps(TRANSCRIPTION_SCHEMA), encoding='utf-8')
    prompt = (
        '请用简明中文填写所有面向用户的 uncertainty 不确定性说明。 '
        'Read ONLY the actual attached score images. No tools, commands, files, web or prior song knowledge. '
        f'Transcribe both hands from the visible page BEGINNING for {seconds:g} seconds at the printed quarter-note BPM. '
        'Do not stop after one bar if further notation is visible. Follow score order; include chords and ties. '
        'Use quarter-note beats, start_beat=0 at the beginning, preserve rests, no tempo guessing or padded tail. '
        'Each raw performed note must last at least 1/64 quarter beat; do not use zero/near-zero note lengths. '
        'For unmeasured grace notes choose a plausible short performed duration before the target note '
        'and disclose the timing approximation in Chinese uncertainty. Do not label a zero-time ornament '
        'as a performed note or pad/extend other notes to satisfy the schema. '
        'For every note choose pitch=MIDI and degree=null (octave=0,accidental=0), OR pitch=null and degree=1..7. '
        'For numbered notation degree octave=0 means tonic in octave4 (C4=60; Db4=61), '
        'dots above/below are +1/-1 octaves; accidental is semitone offset from the tonic major scale degree. '
        'MIDI pitch is absolute. covered_beats states the actual contiguous notation read; both hands must cover '
        '[0,covered_beats] with notes or explicit rests, even if one hand is silent. '
        'For a partial final bar read enough notes to reach the requested seconds; the application crops. '
        'If image unreadable use can_read=false. Report public uncertainty and visible coverage limits, '
        'never fabricate missing notes or song identity. All JSON fields are required.')
    for attempt in range(2):
        if cancelled():
            raise LabFailure('cancelled', '已取消找歌实验。')
        final = job/f'transcription-{attempt}.json'
        final.unlink(missing_ok=True)
        args = ['exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--ephemeral',
                '--sandbox','read-only','--cd',str(job),'--model',model_id,
                '--output-schema',str(schema),'--output-last-message',str(final),'--json','--color','never',
                '-c','features.shell_tool=false','-c','features.apps=false','-c','web_search="disabled"',
                '-c','project_doc_max_bytes=0','-c','model_reasoning_effort="high"']
        for path in images:
            args.extend(['--image',str(Path(path).resolve())])
        args.append('-')
        completed = False
        failed = False
        def event(row):
            nonlocal completed, failed
            completed |= row.get('type') == 'turn.completed'
            failed |= row.get('type') in {'turn.failed','error'}
        try:
            progress(dict(stage='transcribing', message='正在读取本次实际谱面…'))
            runner(executable,args,text=prompt,cwd=str(job),timeout=240,cancelled=cancelled,
                   on_event=event,on_progress=lambda e: progress(dict(stage='heartbeat',message='独立识谱进程运行中…')))
        except LabFailure:
            raise
        except Exception:
            raise LabFailure('cli_failed', '独立识谱请求未完成，请检查 Codex 登录与连接后重试。') from None
        if cancelled():
            raise LabFailure('cancelled', '已取消找歌实验。')
        if not completed or failed:
            raise LabFailure('cli_terminal', '识谱未返回成功终态，未生成草稿。')
        try:
            if not final.is_file() or final.is_symlink() or final.stat().st_size > 1_000_000:
                raise ValueError('missing output')
            data = json.loads(final.read_text(encoding='utf-8'))
            return validate_transcription(data)
        except LabFailure as error:
            if error.code != 'format':
                raise
        except (ValueError, OSError, UnicodeError):
            pass
        if attempt:
            raise LabFailure('format', '识谱数据格式修正后仍无效；未生成草稿。')
        prompt += '\nOne format repair only: return strictly the required JSON schema from the SAME attached images; no commentary.'
