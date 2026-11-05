"""Optional isolated music runtime. No model downloads or heavy imports in Qt."""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
from typing import Protocol

from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.song_resolver import SourceCancelled, validate_midi


_NATIVE_POPEN = subprocess.Popen


def _stop_owned_runtime(process):
    """Reap only this live Popen's tree before deleting redirected log files."""
    if process.poll() is None:
        if os.name == 'nt':
            # Windows virtualenv launchers can own a separate interpreter which
            # inherits output.log. Killing the launcher alone leaves that file
            # locked. The live Popen handle pins this owned PID against reuse.
            killer = _NATIVE_POPEN([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),
                '/PID',str(process.pid),'/T','/F'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                killer.wait(timeout=5)
            finally:
                if killer.poll() is None:
                    killer.kill()
                    killer.wait(timeout=5)
            if process.poll() is None:
                process.kill()
        else:
            process.kill()
    process.wait(timeout=5)


class MusicRuntimeUnavailable(RuntimeError):
    pass


class MusicRuntimeFailed(RuntimeError):
    pass


class TranscriptionService(Protocol):
    def convert(self, path: Path, kind: str, *, cancelled, progress): ...


class IsolatedMusicRuntime:
    def __init__(self, home: Path, *, python: Path | None = None, timeout: float = 300):
        self.home = Path(home)
        self.python = python or self.discover(self.home)
        self.timeout = min(600, max(1, timeout))

    @staticmethod
    def discover(home: Path) -> Path | None:
        for root in (home / "music-runtime", Path(__file__).resolve().parents[3] / ".music-runtime"):
            for suffix in ("Scripts/python.exe", "bin/python"):
                candidate = root / suffix
                if candidate.is_file():
                    return candidate
        return None

    def convert(self, path: Path, kind: str, *, cancelled=lambda: False, progress=lambda e: None):
        if self.python is None or not self.python.is_file():
            raise MusicRuntimeUnavailable("optional music runtime is unavailable")
        if kind not in {"score", "audio"} or not path.is_file():
            raise ValueError("unsupported music source")
        if path.stat().st_size > (32_000_000 if kind == "audio" else 2_000_000):
            raise ValueError("source exceeds music runtime limit")
        if cancelled():
            raise SourceCancelled()
        progress(ProviderProgressEvent("transcribing" if kind == "audio" else "parsing"))
        jobs = self.home / "assistant/runtime-jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="music-", dir=jobs) as temporary:
            job = Path(temporary)
            env = os.environ.copy()
            env.update(OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", NUMBA_NUM_THREADS="2",
                       PYTHONUTF8="1", NUMBA_CACHE_DIR=str(job / "numba-cache"))
            command = [str(self.python), "-I", str(Path(__file__).with_name("music_runtime_worker.py")),
                       kind, str(path.resolve()), str(job)]
            with (job / "output.log").open("wb") as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, stdin=subprocess.DEVNULL,
                    env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                # Owned job receipt supports precise cleanup/debugging; source
                # path and raw third-party logs are never kept after the job.
                (job / "process.json").write_text(json.dumps(dict(pid=process.pid,
                    startedAt=time.time(), purpose="optional music conversion", kind=kind)), encoding="utf-8")
                started, heartbeat = time.monotonic(), 0.0
                try:
                    while process.poll() is None:
                        if cancelled():
                            raise SourceCancelled()
                        now = time.monotonic()
                        if now - started >= self.timeout:
                            raise MusicRuntimeFailed("music conversion timed out")
                        if (job / "output.log").stat().st_size > 1_000_000:
                            raise MusicRuntimeFailed("music runtime output exceeded limit")
                        if now - heartbeat >= 1:
                            progress(ProviderProgressEvent("heartbeat", process_alive=True))
                            heartbeat = now
                        time.sleep(.05)
                    if cancelled():
                        raise SourceCancelled()
                    if process.returncode:
                        raise MusicRuntimeFailed("music conversion failed")
                    midi_path = job / "result.mid"
                    if not midi_path.is_file() or midi_path.stat().st_size > 2_000_000:
                        raise MusicRuntimeFailed("music runtime returned no bounded MIDI")
                    parsed = validate_midi(midi_path.read_bytes())
                    # Validate observable quality, never infer exact-song identity.
                    duration = max(note.end_tick for note in parsed.notes) / parsed.ppq * 60 / parsed.bpm
                    if (kind == "audio" and duration > 190) or len(parsed.notes) / max(duration, 1) > 100:
                        raise MusicRuntimeFailed("music output failed quality bounds")
                    progress(ProviderProgressEvent("transcribing" if kind == "audio" else "parsing",
                        current=len(parsed.notes), total=len(parsed.notes), process_alive=False))
                    return parsed
                finally:
                    _stop_owned_runtime(process)
