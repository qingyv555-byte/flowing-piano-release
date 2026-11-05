"""An SFZ audition is audio-only: source project data never changes."""
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from ai_piano_studio.audio.sfz_preview import _preview_notes, SfzPreviewController
from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState


def test_preview_clips_sustained_notes_within_8_seconds():
    source = ProjectState(
        notes=(
            Note.create(pitch=60, start_time=1, duration=2, layer_id="lead"),
            Note.create(pitch=72, start_time=7, duration=4, layer_id="lead"),
            Note.create(pitch=84, start_time=20, duration=1, layer_id="lead"),
        ),
        layers=(MusicLayer.create(id="lead", name="lead"),),
        controls=(ControlEvent.create(time=2, value=127, layer_id="lead"),
                  ControlEvent.create(time=5, value=0, layer_id="lead")),
    )
    original = source.content
    preview = _preview_notes(source, 2.5)
    assert len(preview) == 2
    assert [(n.pitch, n.start_time, n.duration) for n in preview] == [
        (60, 0.0, 2.5), (72, 4.5, 3.5)]
    assert source.content == original


def test_preview_returns_empty_for_silent_window():
    state = ProjectState(notes=(Note.create(pitch=72, start_time=100, duration=1),))
    assert _preview_notes(state, 0) == ()


def test_inflight_cancel_and_shutdown_wait_for_all_workers(qapp, tmp_path):
    # Model the state after two queued generations, without creating any
    # native audio threads or starting the hardware QMediaPlayer.
    controller = SfzPreviewController(
        SimpleNamespace(), SimpleNamespace(), tmp_path)
    temp_home = Path(controller._temp.name)
    stale_file = temp_home / "stale.wav"
    stale_file.write_bytes(b"stale")
    first = SimpleNamespace(cancelled=Event())
    second = SimpleNamespace(cancelled=Event())
    controller._jobs = {1: first, 2: second}
    controller._job = second
    controller._generation = 2
    controller._busy = True

    controller.stop()
    assert first.cancelled.is_set() and second.cancelled.is_set()
    controller.shutdown()
    assert temp_home.is_dir(), "must not delete active native worker scratch"
    controller._ready(1, str(stale_file))
    assert not stale_file.exists()
    assert temp_home.is_dir(), "last worker still owns scratch"
    controller._failed(2, "cancelled")
    assert not temp_home.exists(), "all native workers stopped: scratch may retire"
    controller.shutdown()
