from threading import Event, Thread
import json
import sys
import time

from PySide6.QtCore import QTimer
import pytest

from ai_piano_studio.music_agent.codex_provider import CodexProvider, run_codex
from ai_piano_studio.music_agent.progress import ProviderProgressEvent, TaskProgress


def test_elapsed_does_not_invent_progress_and_stale_updates_are_ignored(qapp):
    clock = [10.0]
    task = TaskProgress(clock=lambda: clock[0])
    task.start(1, "music", "codex", "test")
    task.record(1, ProviderProgressEvent("submitted", status="waiting", process_alive=True))
    clock[0] += 181
    task.tick()
    snapshot = task.snapshot()
    assert snapshot["elapsedText"] == "03:01"
    assert snapshot["stage"] == "submitted"
    assert snapshot["status"] == "waiting"
    assert snapshot["progressTotal"] is None
    assert "仍在运行" in snapshot["waitHint"] and "尚未收到" in snapshot["waitHint"]
    task.start(2, "new", "codex", "test")
    task.record(1, ProviderProgressEvent("writing"))
    task.finish(1, "completed")
    assert task.snapshot()["stage"] == "preparing"
    task.finish(2, "cancelled")
    task.record(2, ProviderProgressEvent("writing"))
    assert task.snapshot()["status"] == "cancelled"
    assert not task._timer.isActive()


def test_safe_failure_and_review_do_not_claim_a_completed_music_project(qapp):
    task = TaskProgress()
    task.start(3, "music", "api", "test")
    task.record(3, ProviderProgressEvent("writing", current=12, total=30))
    assert task.snapshot()["progressCurrent"] == 12
    task.await_review(3)
    assert task.snapshot()["stage"] == "review"
    assert task.snapshot()["status"] == "waiting"
    assert not task._timer.isActive()
    task.finish(3, "failed", error_code="secret raw provider error")
    snapshot = task.snapshot()
    assert snapshot["safeErrorCode"] == "execution_error"
    assert "secret" not in json.dumps(snapshot)


def test_local_receipt_omits_requests_raw_logs_and_hidden_reasoning(tmp_path, qapp):
    path = tmp_path / "task-diagnostics.jsonl"
    task = TaskProgress(diagnostic_path=path)
    task.start(1, "secret user request", "provider-private", "model-private")
    task.record(1, ProviderProgressEvent("responding", detail_code="private hidden reasoning"))
    task.finish(1, "failed", error_code="raw private error")
    raw = path.read_text("utf-8")
    assert "secret" not in raw and "private" not in raw and "reasoning" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[-1]["safeErrorCode"] == "execution_error"
    assert rows[-1]["status"] == "failed"


def test_native_jsonl_is_delivered_before_exit_and_handles_fragmented_utf8():
    events = []
    started = time.monotonic()
    code = "import sys,time; data='{" + '\"type\":\"thread.started\",\"text\":\"音符\"' + "}\\n'.encode(); sys.stdout.buffer.write(data[:-4]); sys.stdout.buffer.flush(); time.sleep(.08); sys.stdout.buffer.write(data[-4:]); sys.stdout.buffer.flush(); time.sleep(.3)"
    run_codex(sys.executable, ["-u", "-c", code], on_event=lambda e: events.append((e, time.monotonic() - started)))
    assert len(events) == 1
    assert events[0][0]["text"] == "音符"
    assert events[0][1] < time.monotonic() - started - .2


def test_codex_progress_contains_no_raw_items_or_reasoning():
    provider = CodexProvider("test")
    events = []
    provider.set_progress_callback(events.append)
    provider._cli_progress({"type": "item.updated", "item": {"type": "reasoning", "text": "private-reasoning-secret"}})
    provider._cli_progress({"type": "error", "message": "private-secret"})
    provider._cli_progress({"type": "unknown", "text": "private-secret"})
    assert len(events) == 1 and events[0].response_received
    assert "private" not in repr(events)
    assert CodexProvider("test").timeout_seconds > 240
    with pytest.raises(ValueError):
        CodexProvider("test", timeout_seconds=999999)


def test_callback_can_cancel_the_native_child_without_waiting_for_timeout():
    cancelled = Event()
    started = time.monotonic()
    with pytest.raises(Exception) as failure:
        run_codex(sys.executable, ["-u", "-c", "import time; print('{\"type\":\"thread.started\"}',flush=True); time.sleep(20)"],
                  on_event=lambda event: cancelled.set(), cancelled=cancelled.is_set, timeout=10)
    assert failure.value.code == "cancelled"
    assert time.monotonic() - started < 5
