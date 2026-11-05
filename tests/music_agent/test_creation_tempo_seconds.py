"""Creation tools author seconds; later tempo metadata must not stretch them."""
import json
from pathlib import Path

import pytest

from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.tool_contracts import MusicExecutionMode, MusicPermission
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from ai_piano_studio.persistence.project_document import load_project_document
from tests.music_agent.test_creation_workflow import assistant_for, done, accept_prepared


@pytest.mark.parametrize("bpm,bars", [(77, 4), (110, 6)])
@pytest.mark.parametrize("tempo_first", [False, True])
def test_isolated_creation_call_order_preserves_seconds_through_adoption_and_reload(
    tmp_path, qapp, bpm, bars, tempo_first
):
    """Break caught: late set_bpm stretches a valid 10–15 second reconstruction."""
    step = 30 / bpm
    payload = [dict(pitch=60 + i % 7, start_time=i * step,
                    duration=step, velocity=70) for i in range(bars * 8)]
    calls = [ProviderToolCall("notes", "add_notes", json.dumps({"notes": payload})),
             ProviderToolCall("tempo", "set_bpm", json.dumps({"bpm": bpm}))]
    if tempo_first:
        calls.reverse()
    old = Note.create(pitch=48, start_time=2, duration=1, velocity=80)
    editor = EditorController(ProjectState(notes=(old,)))
    before = editor.state
    assistant, playback, _ = assistant_for(
        tmp_path, editor, [(ProviderEvent(tool_calls=tuple(calls)),)]
    )
    assert assistant.sendMessage("我要周杰伦的青花瓷，用钢琴弹")
    done(qapp, assistant)
    assert assistant.taskPreviewAvailable, assistant.taskProgress
    assert editor.state == before and not playback.notes
    draft = assistant._creation_workflow.pending_song.state
    assert draft.playback_duration == pytest.approx(bars * 240 / bpm)
    assert 10 <= draft.playback_duration <= 15
    assert [(n.start_time, n.duration) for n in draft.active_notes] == [
        (n["start_time"], n["duration"]) for n in payload
    ]
    for i, note in enumerate(draft.active_notes):
        assert note.start_time * bpm / 60 == pytest.approx(i / 2)
        assert note.duration * bpm / 60 == pytest.approx(.5)
    assert draft.tempo.bpm == draft.playback_tempo.bpm == bpm
    accept_prepared(assistant)
    saved = load_project_document(Path(assistant.lastTurnResult["savedPath"])).state
    assert saved.tempo.bpm == saved.playback_tempo.bpm == bpm
    assert saved.active_notes == draft.active_notes
    assert saved.playback_notes == playback.notes == draft.playback_notes
    # The ordinary gateway remains beat preserving after the draft is adopted.
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(), permissions=frozenset({MusicPermission.WRITE_BPM}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    assert gateway.execute("set_bpm", {"bpm": 120}, context).success
    assert gateway.finish_turn(context).success
    assert editor.state.active_notes[-1].duration == pytest.approx(.25)
    editor.undo()
    editor.synchronizeNow()
    assert editor.state.active_notes == saved.active_notes


def test_seconds_tempo_change_preserves_events_and_playback_until_sync():
    """Break caught: the creation tempo path edits event seconds or live playback."""
    note = Note.create(pitch=60, start_time=2, duration=.5, velocity=70)
    control = ControlEvent.create(time=3, value=127)
    before = ProjectState(notes=(note,), controls=(control,))
    changed = before.with_tempo(TempoMap(77), preserve_note_seconds=True)
    assert changed.notes == before.notes
    assert changed.controls == before.controls
    assert changed.playback_notes == before.playback_notes
    assert changed.playback_controls == before.playback_controls
    assert changed.playback_tempo == before.playback_tempo
    synced = changed.synchronize()
    assert synced.playback_tempo.bpm == 77
    assert synced.playback_controls == before.controls
    assert synced.playback_notes == before.playback_notes
    normal = before.with_tempo(TempoMap(77))
    assert normal.active_notes[0].start_time == pytest.approx(240 / 77)
    assert normal.controls[0].time == pytest.approx(360 / 77)


@pytest.mark.parametrize("event_kind", ["note", "control"])
def test_seconds_tempo_change_keeps_locked_layer_guard(event_kind):
    """Break caught: metadata-only tempo changes bypass existing layer locks."""
    layer = MusicLayer.default().with_changes(locked=True)
    state = ProjectState(
        notes=(Note.create(pitch=60, start_time=0, duration=1, velocity=70),)
        if event_kind == "note" else (),
        controls=(ControlEvent.create(time=1, value=0),) if event_kind == "control" else (),
        layers=(layer,),
    )
    with pytest.raises(ValueError, match="locked"):
        state.with_tempo(TempoMap(77), preserve_note_seconds=True)
