from __future__ import annotations

from time import perf_counter

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.midi import export_midi
from ai_piano_studio.persistence.project_json import load_project, save_project
from ai_piano_studio.music_agent.proposal_manager import MusicEditProposalManager
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicEditProposalStatus,
    MusicPermission,
)
from ai_piano_studio.music_agent.tool_gateway import (
    MusicAgentToolGateway,
    MusicToolConflictError,
    MusicToolNotFoundError,
)


def _editor() -> EditorController:
    return EditorController(
        ProjectState(
            notes=(
                Note.create(id="bar-1", pitch=60, start_time=0.0, duration=0.5, velocity=70),
                Note.create(id="bar-2", pitch=64, start_time=2.0, duration=0.5, velocity=80),
                Note.create(id="bar-3", pitch=67, start_time=4.0, duration=0.5, velocity=90),
            )
        )
    )


def _context(gateway: MusicAgentToolGateway, permissions: set[MusicPermission]):
    return gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset(permissions),
    )


def _stage_note(gateway: MusicAgentToolGateway, pitch: int = 72):
    context = _context(gateway, {MusicPermission.WRITE_NOTES})
    result = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": pitch, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    )
    assert result.success
    return context


def _stage_note_update_and_bpm(gateway: MusicAgentToolGateway):
    context = _context(
        gateway, {MusicPermission.WRITE_NOTES, MusicPermission.WRITE_BPM}
    )
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assert gateway.execute(
        "set_note_velocity", {"note_ids": ["bar-2"], "velocity": 105}, context
    ).success
    assert gateway.execute("set_bpm", {"bpm": 100}, context).success
    return context


class RecordingProposalPlayback:
    """Boundary fake that records a detached preview without replacing the real Gateway or Editor."""

    def __init__(self) -> None:
        self.played: list[tuple[Note, ...]] = []
        self.stopped = 0

    def play_detached_notes(self, notes: tuple[Note, ...]) -> None:
        self.played.append(notes)

    def stop_detached_preview(self) -> None:
        self.stopped += 1


def test_register_derives_compact_note_diff_from_the_staged_snapshot() -> None:
    """Break caught: preview presentation trusts tool counts instead of the actual staged result."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _stage_note(gateway)
    assert gateway.execute(
        "update_notes", {"updates": [{"id": "bar-2", "pitch": 66}]}, context
    ).success
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision)
    manager = MusicEditProposalManager(editor, gateway)

    proposal = manager.register(context)

    assert proposal.status is MusicEditProposalStatus.PENDING
    assert proposal.proposal_id == context.turn_id
    assert manager.summary.notes_added == 1
    assert manager.summary.notes_updated == 1
    assert manager.summary.notes_deleted == 0
    assert manager.summary.bpm_before is None
    assert manager.summary.bpm_after is None
    assert manager.changes.added[0].note_id != "bar-2"
    assert manager.changes.updated[0].note_id == "bar-2"
    assert manager.changes.updated[0].before == {"pitch": 64}
    assert manager.changes.updated[0].after == {"pitch": 66}
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) == before


def test_manager_rejects_a_gateway_bound_to_another_editor() -> None:
    """Break caught: a proposal from project B can be committed into project A."""
    left_editor = _editor()
    right_editor = _editor()

    with pytest.raises(ValueError, match="same EditorController"):
        MusicEditProposalManager(left_editor, MusicAgentToolGateway(right_editor))


def test_register_seals_a_proposal_against_later_gateway_writes() -> None:
    """Break caught: the user previews one proposal but Apply replays later hidden writes."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _stage_note(gateway, pitch=72)
    manager = MusicEditProposalManager(editor, gateway)

    manager.register(context)
    appended = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 74, "start_time": 3.25, "duration": 0.5, "velocity": 96}]},
        context,
    )

    assert appended.error_code == "conflict"
    assert manager.apply_proposal().success
    assert {note.pitch for note in editor.state.active_notes} == {60, 64, 67, 72}


def test_bpm_only_proposal_reports_tempo_without_false_note_updates() -> None:
    """Break caught: tempo rescaling is presented as unrelated note edits."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _context(gateway, {MusicPermission.WRITE_BPM})
    assert gateway.execute("set_bpm", {"bpm": 100}, context).success
    manager = MusicEditProposalManager(editor, gateway)

    manager.register(context)

    assert manager.summary.notes_added == 0
    assert manager.summary.notes_updated == 0
    assert manager.summary.notes_deleted == 0
    assert (manager.summary.bpm_before, manager.summary.bpm_after) == (120, 100)
    assert manager.inspector["detailCount"] == 0
    assert manager.inspector["harmonyDetailCount"] == 0


def test_discard_releases_pending_workspace_without_touching_project_or_history() -> None:
    """Break caught: discarding a suggestion leaves executable staged changes or edits the project."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _stage_note(gateway)
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(context)
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision)

    result = manager.discard_proposal()

    assert result.success
    assert manager.proposal.status is MusicEditProposalStatus.DISCARDED
    assert manager.summary.notes_added == 1
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) == before
    with pytest.raises(MusicToolNotFoundError):
        gateway.capture_proposal(context)
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 74, "start_time": 3.25, "duration": 0.5, "velocity": 96}]},
        context,
    ).error_code == "conflict"


def test_live_revision_change_marks_pending_proposal_stale_and_releases_it() -> None:
    """Break caught: a user edit leaves an old suggestion silently applicable."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _stage_note(gateway)
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(context)

    editor._execute(editor.state.with_tempo(TempoMap(bpm=110)))

    assert manager.proposal.status is MusicEditProposalStatus.STALE
    assert manager.summary.notes_added == 1
    with pytest.raises(MusicToolConflictError, match="project revision changed"):
        gateway.capture_proposal(context)


def test_apply_commits_all_staged_changes_as_one_undo_and_redo() -> None:
    """Break caught: Apply creates partial/multiple history records or cannot restore the whole suggestion."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(_stage_note_update_and_bpm(gateway))
    before = (editor.state, editor.harmony_metadata)

    applied = manager.apply_proposal()

    assert applied.success
    assert manager.proposal.status is MusicEditProposalStatus.APPLIED
    assert editor.state.tempo.bpm == 100
    assert {note.pitch for note in editor.state.active_notes} == {60, 64, 67, 72}
    assert next(note.velocity for note in editor.state.active_notes if note.id == "bar-2") == 105
    assert editor._history.undo_depth == 1
    editor.undo()
    assert (editor.state, editor.harmony_metadata) == before
    editor.redo()
    assert editor.state.tempo.bpm == 100
    assert manager.apply_proposal().error_code == "conflict"


def test_apply_rolls_back_every_replay_step_when_one_staged_operation_fails(monkeypatch) -> None:
    """Break caught: an Apply error after an earlier preview leaves a partial project/history edit."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(_stage_note_update_and_bpm(gateway))
    before = (
        editor.state,
        editor.harmony_metadata,
        editor._history.undo_depth,
        editor.projectRevision,
    )
    original = gateway._simulate_operation
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected replay failure")
        return original(*args)

    monkeypatch.setattr(gateway, "_simulate_operation", fail_second)

    failed = manager.apply_proposal()

    assert failed.error_code == "execution_error"
    assert manager.proposal.status is MusicEditProposalStatus.FAILED
    assert (
        editor.state,
        editor.harmony_metadata,
        editor._history.undo_depth,
        editor.projectRevision,
    ) == before


def test_audition_uses_detached_staged_bpm_notes_and_discard_cleans_it_up() -> None:
    """Break caught: proposal audition mutates live state, ignores staged tempo, or survives discard."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    playback = RecordingProposalPlayback()
    manager = MusicEditProposalManager(editor, gateway, playback=playback)
    manager.register(_stage_note_update_and_bpm(gateway))
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision)

    auditioned = manager.audition_proposal()
    manager.discard_proposal()

    assert auditioned.success
    assert {note.pitch for note in playback.played[-1]} == {60, 64, 67, 72}
    assert next(note.start_time for note in playback.played[-1] if note.pitch == 72) == pytest.approx(3.3)
    assert playback.stopped == 1
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) == before


def test_pending_proposal_is_excluded_from_project_json_and_midi_export(tmp_path) -> None:
    """Break caught: staged suggestion notes/BPM leak into durable project or MIDI output before Apply."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(_stage_note_update_and_bpm(gateway))
    project_path = tmp_path / "pending.pianoai.json"
    midi_path = tmp_path / "pending.mid"

    save_project(project_path, editor.state)
    export_midi(midi_path, editor.state.active_notes, editor.state.tempo)

    loaded = load_project(project_path)
    assert loaded.tempo.bpm == 120
    assert {note.pitch for note in loaded.active_notes} == {60, 64, 67}
    assert bytes((0x90, 72)) not in midi_path.read_bytes()


def test_project_replacement_discards_pending_proposal_and_cannot_apply_to_new_session() -> None:
    """Break caught: a project-A proposal carries its write capability into project B."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(_stage_note(gateway))
    replacement = ProjectState(
        (Note.create(id="new", pitch=55, start_time=0.0, duration=0.5),)
    )

    editor._replace_project(
        replacement,
        saved_content=replacement.content,
        path=None,
    )

    assert manager.proposal.status is MusicEditProposalStatus.DISCARDED
    assert manager.apply_proposal().error_code == "conflict"
    assert {note.pitch for note in editor.state.active_notes} == {55}


def test_replacing_a_pending_proposal_releases_the_old_workspace_and_keeps_one_active() -> None:
    """Break caught: several pending agent turns remain independently executable in memory."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    manager = MusicEditProposalManager(editor, gateway)
    first_context = _stage_note(gateway, pitch=72)
    first = manager.register(first_context)
    second_context = _stage_note(gateway, pitch=74)

    second = manager.register(second_context)

    assert manager.proposal is second
    assert manager.proposal.proposal_id != first.proposal_id
    with pytest.raises(MusicToolNotFoundError):
        gateway.capture_proposal(first_context)
    assert manager.discard_proposal().success
    assert manager.apply_proposal().error_code == "conflict"


def test_pending_proposal_leaves_live_editor_clean_without_scheduling_autosave() -> None:
    """Break caught: a suggestion alone marks the live project dirty and enters autosave/recovery."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    manager = MusicEditProposalManager(editor, gateway)

    manager.register(_stage_note(gateway))

    assert editor.isDirty is False
    assert editor._autosave_timer.isActive() is False
    assert editor._recovery_timer.isActive() is False


def test_music_agent_package_exports_only_phase_four_presentation_contracts() -> None:
    """Break caught: Phase 4 consumers must import private Gateway snapshot internals."""
    from ai_piano_studio.music_agent import (
        MusicEditProposalManager as ExportedManager,
        MusicEditProposalStatus as ExportedStatus,
        ProposalChangeSet,
        ProposalChangeSummary,
    )

    assert ExportedManager is MusicEditProposalManager
    assert ExportedStatus is MusicEditProposalStatus
    assert ProposalChangeSet is not None
    assert ProposalChangeSummary is not None


def test_inspector_summarizes_velocity_only_changes_without_exposing_ids() -> None:
    """Break caught: a 66-note velocity proposal is rendered as a UUID dump."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _context(gateway, {MusicPermission.WRITE_NOTES})
    assert gateway.execute(
        "set_note_velocity", {"note_ids": ["bar-2"], "velocity": 100}, context
    ).success
    manager = MusicEditProposalManager(editor, gateway)

    manager.register(context)

    inspector = manager.inspector
    summary = inspector["summary"]
    velocity = inspector["velocity"]
    assert inspector["headline"] == "本次只调整了力度，没有改变旋律和节奏结构。"
    assert summary == {
        "notesAdded": 0,
        "notesUpdated": 1,
        "notesDeleted": 0,
        "pitchChanged": 0,
        "positionChanged": 0,
        "durationChanged": 0,
        "velocityChanged": 1,
        "instrumentChanged": 0,
        "bpmChanged": False,
        "harmonyChanged": 0,
    }
    assert velocity["beforeAverage"] == 80
    assert velocity["afterAverage"] == 100
    assert velocity["increaseCount"] == 1
    assert velocity["decreaseCount"] == 0
    assert velocity["unchangedCount"] == 0
    assert inspector["bars"] == [{"bar": 2, "count": 1}]
    detail = inspector["details"]["updated"][0]
    assert detail["noteName"] == "E4"
    assert detail["location"] == "第 2 小节 · 第 1 拍"
    assert "noteId" not in detail
    assert detail["technical"]["noteId"] == "bar-2"
    assert detail["beforeNote"]["pitch"] == 64
    assert detail["beforeNote"]["velocity"] == 80
    assert detail["afterNote"]["pitch"] == 64
    assert detail["afterNote"]["velocity"] == 100


def test_inspector_classifies_mixed_note_changes_and_groups_by_bar() -> None:
    """Break caught: mixed edits cannot be distinguished from one updated count."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 3),),
        permissions=frozenset({MusicPermission.WRITE_NOTES, MusicPermission.WRITE_BPM}),
    )
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assert gateway.execute(
        "update_notes",
        {
            "updates": [
                {
                    "id": "bar-2",
                    "pitch": 66,
                    "start_time": 2.25,
                    "duration": 0.75,
                    "velocity": 90,
                    "instrument": "electric-piano",
                }
            ]
        },
        context,
    ).success
    assert gateway.execute("delete_notes", {"note_ids": ["bar-3"]}, context).success
    assert gateway.execute("set_bpm", {"bpm": 100}, context).success
    manager = MusicEditProposalManager(editor, gateway)

    manager.register(context)

    inspector = manager.inspector
    assert inspector["headline"] == "本次同时调整了旋律、节奏和力度。"
    assert inspector["summary"]["notesAdded"] == 1
    assert inspector["summary"]["notesUpdated"] == 1
    assert inspector["summary"]["notesDeleted"] == 1
    assert inspector["summary"]["pitchChanged"] == 1
    assert inspector["summary"]["positionChanged"] == 1
    assert inspector["summary"]["durationChanged"] == 1
    assert inspector["summary"]["velocityChanged"] == 1
    assert inspector["summary"]["instrumentChanged"] == 1
    assert inspector["summary"]["bpmChanged"] is True
    assert inspector["pitch"]["upCount"] == 1
    assert inspector["pitch"]["downCount"] == 0
    assert inspector["timing"]["earlyCount"] == 0
    assert inspector["timing"]["lateCount"] == 1
    assert inspector["timing"]["longerCount"] == 1
    assert inspector["bars"] == [{"bar": 2, "count": 2}, {"bar": 3, "count": 1}]
    assert inspector["detailCount"] == 3
    assert len(inspector["representativeDetails"]) <= 8
    assert inspector["details"]["added"][0]["noteName"] == "C5"
    assert inspector["details"]["deleted"][0]["noteName"] == "G4"
    assert inspector["details"]["updated"][0]["beforeNote"]["pitch"] == 64
    assert inspector["details"]["updated"][0]["afterNote"]["pitch"] == 66
    assert inspector["details"]["updated"][0]["beforeNote"]["startBeats"] == 4
    assert inspector["details"]["updated"][0]["afterNote"]["startBeats"] == 4.5


def test_original_and_modified_proposal_auditions_use_frozen_snapshots() -> None:
    """Break caught: both A/B buttons audition the same live editor state."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _stage_note(gateway)
    playback = RecordingProposalPlayback()
    manager = MusicEditProposalManager(editor, gateway, playback=playback)

    manager.register(context)
    assert manager.audition_original_proposal().success
    assert manager.audition_proposal().success

    assert len(playback.played) == 2
    assert len(playback.played[0]) == 3
    assert len(playback.played[1]) == 4
    assert {note.pitch for note in playback.played[0]} == {60, 64, 67}
    assert {note.pitch for note in playback.played[1]} == {60, 64, 67, 72}
    assert editor.projectRevision == 0


def test_audition_a_b_sides_share_the_same_affected_range_anchor() -> None:
    """Break caught: adding an earlier note shifts only the modified audition."""
    editor = EditorController(
        ProjectState(
            notes=(Note.create(id="baseline", pitch=60, start_time=2.0, duration=0.5),)
        )
    )
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 1.0, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    playback = RecordingProposalPlayback()
    manager = MusicEditProposalManager(editor, gateway, playback=playback)
    manager.register(context)

    assert manager.audition_original_proposal().success
    assert manager.audition_proposal().success

    original_baseline = next(note for note in playback.played[0] if note.id == "baseline")
    modified_baseline = next(note for note in playback.played[1] if note.id == "baseline")
    assert original_baseline.start_time == pytest.approx(modified_baseline.start_time)


def test_inspector_keeps_a_1000_note_diff_bounded_and_fast() -> None:
    """Break caught: opening a large Proposal blocks the UI or expands every row eagerly."""
    notes = tuple(
        Note.create(
            id=f"large-{index}",
            pitch=36 + index % 48,
            start_time=index * 0.125,
            duration=0.25,
            velocity=60 + index % 30,
        )
        for index in range(1000)
    )
    editor = EditorController(ProjectState(notes=notes))
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset(
            {MusicPermission.WHOLE_PROJECT_WRITE, MusicPermission.WRITE_NOTES}
        ),
    )
    assert gateway.execute(
        "set_note_velocity",
        {"note_ids": [note.id for note in notes], "velocity": 96},
        context,
    ).success
    manager = MusicEditProposalManager(editor, gateway)

    started = perf_counter()
    manager.register(context)
    elapsed = perf_counter() - started

    assert elapsed < 2.0
    assert manager.inspector["detailCount"] == 1000
    assert len(manager.inspector["representativeDetails"]) == 8
    assert all("noteId" not in detail for detail in manager.inspector["representativeDetails"])
