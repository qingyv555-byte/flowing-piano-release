from __future__ import annotations

from dataclasses import replace

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
    NoteSignature,
)
from ai_piano_studio.music_agent.proposal_manager import (
    MusicEditProposalManager,
    _harmony_inspector_details,
)
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicExecutionMode,
    MusicPermission,
)
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway


class RecordingProposalPlayback:
    def __init__(self) -> None:
        self.played: list[tuple[Note, ...]] = []
        self.stopped = 0

    def play_detached_notes(self, notes: tuple[Note, ...]) -> None:
        self.played.append(notes)

    def stop_detached_preview(self) -> None:
        self.stopped += 1


def _editor_with_harmony_candidates() -> EditorController:
    melody = (
        (72, 0.0, 0.5),
        (83, 0.5, 0.25),
        (76, 0.75, 0.25),
        (79, 1.0, 0.5),
        (77, 1.5, 0.25),
        (81, 1.75, 0.125),
        (72, 1.875, 0.125),
    )
    return EditorController(
        ProjectState(
            notes=tuple(
                Note.create(
                    id=f"melody-{index}",
                    pitch=pitch,
                    start_time=start_time,
                    duration=duration,
                    velocity=80,
                )
                for index, (pitch, start_time, duration) in enumerate(melody)
            )
        )
    )


def test_harmony_proposal_auditions_and_applies_existing_group_as_one_history_step() -> None:
    """Break caught: proposal preview omits existing Harmony output or Apply loses its metadata history."""
    editor = _editor_with_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_HARMONY, MusicPermission.WRITE_HARMONY}
        ),
    )
    available = gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, context
    )
    assert available.success
    measure = available.data["measures"][0]
    assert gateway.execute(
        "apply_chord",
        {
            "measure_index": measure["measure_index"],
            "candidate_index": measure["candidates"][0]["candidate_index"],
            "harmony_context_token": available.data["harmony_context_token"],
        },
        context,
    ).success
    playback = RecordingProposalPlayback()
    manager = MusicEditProposalManager(editor, gateway, playback=playback)
    before = (editor.state, editor.harmony_metadata)

    manager.register(context)
    auditioned = manager.audition_proposal()
    applied = manager.apply_proposal()

    assert auditioned.success and applied.success
    assert manager.summary.harmony_changes[0].measure_index == 0
    assert manager.summary.harmony_changes[0].generated_note_count == 3
    group = editor.harmony_metadata.groups[0]
    assert set(group.voicing_pitches).issubset({note.pitch for note in playback.played[-1]})
    assert editor._history.undo_depth == 1
    editor.undo()
    assert (editor.state, editor.harmony_metadata) == before
    editor.redo()
    assert len(editor.harmony_metadata.groups) == 1


def test_note_proposal_apply_reconciles_existing_harmony_conflicts() -> None:
    """Break caught: ordinary proposal notes bypass existing Harmony detachment rules."""
    editor = _editor_with_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    harmony_context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_HARMONY, MusicPermission.WRITE_HARMONY}
        ),
        mode=MusicExecutionMode.COLLABORATION,
    )
    candidates = gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, harmony_context
    )
    measure = candidates.data["measures"][0]
    gateway.begin_turn(harmony_context)
    assert gateway.execute(
        "apply_chord",
        {
            "measure_index": measure["measure_index"],
            "candidate_index": measure["candidates"][0]["candidate_index"],
            "harmony_context_token": candidates.data["harmony_context_token"],
        },
        harmony_context,
    ).success
    assert gateway.finish_turn(harmony_context).success
    conflict_pitch = editor.harmony_metadata.groups[0].voicing_pitches[0] + 12
    proposal_context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert gateway.execute(
        "add_notes",
        {
            "notes": [
                {
                    "pitch": conflict_pitch,
                    "start_time": 0.0,
                    "duration": 0.5,
                    "velocity": 96,
                }
            ]
        },
        proposal_context,
    ).success
    manager = MusicEditProposalManager(editor, gateway)

    manager.register(proposal_context)
    assert manager.apply_proposal().success

    assert editor.harmony_metadata.groups[0].detached is True
    editor.undo()
    assert editor.harmony_metadata.groups[0].detached is False
    editor.redo()
    assert editor.harmony_metadata.groups[0].detached is True


def test_harmony_inspector_reports_same_group_symbol_timing_and_voicing_changes() -> None:
    """Break caught: a changed chord group is reduced to an unchanged count."""
    signature = NoteSignature(60, 0.0, 0.5, 80, "acoustic-grand-piano")
    baseline_group = HarmonyGroup(
        group_id="harmony-1",
        measure_index=0,
        start_tick=0,
        duration_ticks=1920,
        chord_symbol="C",
        voicing_pitches=(60, 64, 67),
        generated_note_ids=("generated-1",),
        original_signatures={"generated-1": signature},
        detached=False,
        application_origin="auto",
        replace_policy="automatic",
    )
    staged_group = replace(
        baseline_group,
        measure_index=1,
        start_tick=480,
        duration_ticks=1440,
        chord_symbol="Am",
        voicing_pitches=(57, 60, 64),
    )

    details = _harmony_inspector_details(
        HarmonyProjectMetadata((baseline_group,)),
        HarmonyProjectMetadata((staged_group,)),
    )

    assert len(details) == 1
    detail = details[0]
    assert detail["kind"] == "updated"
    assert detail["location"] == "第 2 小节 · 第 2 拍"
    assert detail["before"]["chordSymbol"] == "C"
    assert detail["after"]["chordSymbol"] == "Am"
    assert detail["before"]["durationBeats"] == 4
    assert detail["after"]["durationBeats"] == 3
    assert detail["before"]["voicing"] == ["C4", "E4", "G4"]
    assert detail["after"]["voicing"] == ["A3", "C4", "E4"]
    assert "generatedNoteIds" not in detail["before"]
    assert detail["technical"]["groupId"] == "harmony-1"
