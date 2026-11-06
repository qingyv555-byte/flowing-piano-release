from threading import Event
import pytest

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtTest import QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_document import ProjectDocument


def document(name, pitch=60):
    return ProjectDocument(ProjectState((Note.create(
        id=name, pitch=pitch, start_time=0, duration=1,
        created_at="2026-11-06T00:00:00+00:00", updated_at="2026-11-06T00:00:00+00:00"),)))


def test_slow_recovery_keeps_gui_alive_and_clear_prevents_resurrection(qapp, tmp_path, monkeypatch):
    from ai_piano_studio.persistence import recovery_writer
    entered, release = Event(), Event()
    original = recovery_writer.save_project_document

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_writer, "save_project_document", slow)
    store = RecoveryStore(tmp_path / "recovery")
    beats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    try:
        store.queue_document(document("old"), None)
        assert entered.wait(1)
        QTest.qWait(60)
        assert len(beats) >= 3
        store.clear()
    finally:
        release.set()
        timer.stop()
        store.flush()
    assert not store.available
    assert not list((tmp_path / "recovery").glob(".recovery-*"))


def test_latest_snapshot_wins_and_synchronous_write_invalidates_inflight(qapp, tmp_path, monkeypatch):
    from ai_piano_studio.persistence import recovery_writer
    entered, release = Event(), Event()
    original = recovery_writer.save_project_document

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_writer, "save_project_document", slow)
    store = RecoveryStore(tmp_path / "recovery")
    try:
        store.queue_document(document("old"), None)
        assert entered.wait(1)
        store.queue_document(document("middle"), None)
        store.queue_document(document("latest"), "latest.pianoai.json")
    finally:
        release.set()
        store.flush()
    recovered, path = store.read_document()
    assert recovered == document("latest") and path == "latest.pianoai.json"
    entered.clear()
    release.clear()
    try:
        store.queue_document(document("stale"), None)
        assert entered.wait(1)
        store.write_document(document("explicit"), None)
    finally:
        release.set()
        store.flush()
    assert store.read_document()[0] == document("explicit")


def test_worker_failure_preserves_previous_snapshot_and_allows_retry(qapp, tmp_path, monkeypatch):
    from ai_piano_studio.persistence import recovery_writer
    store = RecoveryStore(tmp_path / "recovery")
    store.write_document(document("previous"), None)
    original = recovery_writer.save_project_document

    def fail(*args, **kwargs):
        raise OSError("injected disk error")

    monkeypatch.setattr(recovery_writer, "save_project_document", fail)
    store.queue_document(document("failed"), None)
    store.flush()
    assert store.read_document()[0] == document("previous")
    monkeypatch.setattr(recovery_writer, "save_project_document", original)
    store.queue_document(document("retry"), None)
    store.flush()
    assert store.read_document()[0] == document("retry")
    assert QThreadPool.globalInstance().waitForDone(3000)


@pytest.mark.parametrize("clean", [True, False])
def test_controller_timer_and_project_switch_cancel_unpublished_backup(qapp, tmp_path, monkeypatch, clean):
    from ai_piano_studio.editor.controller import EditorController
    from ai_piano_studio.persistence import recovery_writer
    entered, release = Event(), Event()
    original = recovery_writer.save_project_document

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_writer, "save_project_document", slow)
    store = RecoveryStore(tmp_path / "recovery")
    controller = EditorController(document("saved").state, recovery_store=store)
    controller._recovery_timer.setInterval(1)
    try:
        controller._execute(document("edit", 64).state)
        QTest.qWait(30)
        assert entered.wait(1) and store.pending and not store.available
        empty = ProjectDocument(ProjectState())
        controller._replace_project(empty.state, saved_content=empty.state.content, path=None,
                                    harmony_metadata=empty.harmony,
                                    saved_harmony_metadata=empty.harmony if clean else None)
        assert controller.isDirty == (not clean)
    finally:
        controller._recovery_timer.stop()
        release.set()
        store.flush()
    assert not store.available


def test_normal_application_exit_waits_for_recovery_publication(tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    code = '''
import sys,time
sys.path.insert(0,sys.argv[2])
from PySide6.QtCore import QCoreApplication,QTimer
from ai_piano_studio.persistence import recovery_writer
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_document import ProjectDocument
from ai_piano_studio.domain.project import ProjectState
app=QCoreApplication([])
original=recovery_writer.save_project_document
def slow(*args,**kwargs):
 time.sleep(.15)
 return original(*args,**kwargs)
recovery_writer.save_project_document=slow
store=RecoveryStore(sys.argv[1])
store.queue_document(ProjectDocument(ProjectState()),None)
QTimer.singleShot(10,app.quit)
app.exec()
assert store.read_document() is not None
assert not store.pending
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path),
                             str(Path(__file__).resolve().parents[2] / "src")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
