from pathlib import Path
import subprocess
import sys
from threading import Event, Timer
import os

import pytest

from ai_piano_studio.music_agent import transcription
from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime, MusicRuntimeUnavailable, MusicRuntimeFailed
from ai_piano_studio.music_agent.song_resolver import SourceCancelled


@pytest.mark.skipif(os.name != 'nt', reason='Windows interpreter launchers can leave a child holding redirected logs')
def test_owned_runtime_stops_descendants_before_removing_logs(tmp_path, monkeypatch):
    source=tmp_path/'source.wav'
    source.write_bytes(b'test transport fixture')
    pid_file=tmp_path/'owned-child.pid'
    original=subprocess.Popen
    code=("import subprocess,sys,time,pathlib; "
          "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
          f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(60)")
    def controlled(_command,**kwargs):
        return original([sys.executable,'-c',code],**kwargs)
    monkeypatch.setattr(transcription.subprocess,'Popen',controlled)
    runtime=IsolatedMusicRuntime(tmp_path,python=Path(sys.executable),timeout=5)
    try:
        with pytest.raises(SourceCancelled):
            runtime.convert(source,'audio',cancelled=pid_file.exists)
        assert not list((tmp_path/'assistant/runtime-jobs').iterdir())
    finally:
        # If the red implementation leaves this test's own grandchild behind,
        # clean only its recorded PID; never touch another task's process.
        if pid_file.exists():
            killer=original([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),
                '/PID',pid_file.read_text().strip(),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            killer.wait(timeout=5)


def test_missing_optional_runtime_is_precise_and_creates_no_jobs(tmp_path):
    source = tmp_path / "test.wav"
    source.write_bytes(b"bounded source")
    runtime = IsolatedMusicRuntime(tmp_path, python=tmp_path / "missing.exe")
    with pytest.raises(MusicRuntimeUnavailable):
        runtime.convert(source, "audio")
    assert not (tmp_path / "assistant/runtime-jobs").exists()


@pytest.mark.parametrize("cancel", [True, False])
def test_owned_native_runtime_is_reaped_on_cancel_or_timeout(tmp_path, monkeypatch, cancel):
    source = tmp_path / "test.wav"
    source.write_bytes(b"test transport fixture, not real audio")
    original = subprocess.Popen
    processes = []
    def controlled(_command, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(transcription.subprocess, "Popen", controlled)
    runtime = IsolatedMusicRuntime(tmp_path, python=Path(sys.executable), timeout=1)
    stopped = Event()
    timer = Timer(.2, stopped.set)
    if cancel:
        timer.start()
    events = []
    try:
        with pytest.raises(SourceCancelled if cancel else MusicRuntimeFailed):
            runtime.convert(source, "audio", cancelled=stopped.is_set, progress=events.append)
    finally:
        if cancel:
            timer.cancel()
            timer.join()
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not list((tmp_path / "assistant/runtime-jobs").iterdir())
    assert any(event.stage == "transcribing" for event in events)
