"""Lifecycle-safe presentation and application boundary for staged music proposals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import floor
from types import MappingProxyType
from typing import Protocol

from ai_piano_studio.domain.note import Note, midi_to_name
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import PPQ
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
)
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentExecutionContext,
    MusicEditProposal,
    MusicEditProposalStatus,
    MusicToolResult,
)
from ai_piano_studio.music_agent.tool_gateway import (
    MusicAgentToolGateway,
    ProposalStageSnapshot,
)


def _frozen_fields(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class ProposalNoteChange:
    """Compact local note delta for future preview UI consumers."""

    note_id: str
    before: Mapping[str, object]
    after: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.note_id, str) or not self.note_id:
            raise ValueError("note_id must be a non-empty string")
        object.__setattr__(self, "before", _frozen_fields(self.before))
        object.__setattr__(self, "after", _frozen_fields(self.after))


@dataclass(frozen=True, slots=True)
class ProposalHarmonyChange:
    """Existing Harmony-group facts; no new chord/voicing logic lives here."""

    kind: str
    measure_index: int
    chord_symbol: str
    generated_note_count: int


@dataclass(frozen=True, slots=True)
class ProposalChangeSummary:
    """Compact real summary derived from baseline and staged local snapshots."""

    notes_added: int
    notes_updated: int
    notes_deleted: int
    bpm_before: int | None
    bpm_after: int | None
    harmony_changes: tuple[ProposalHarmonyChange, ...]
    affected_bars: BarRange | None


@dataclass(frozen=True, slots=True)
class ProposalChangeSet:
    """Detailed note/harmony changes for future UI inspection."""

    added: tuple[ProposalNoteChange, ...]
    updated: tuple[ProposalNoteChange, ...]
    deleted: tuple[ProposalNoteChange, ...]
    harmony: tuple[ProposalHarmonyChange, ...]


@dataclass(slots=True)
class _ManagedProposal:
    context: MusicAgentExecutionContext
    captured: ProposalStageSnapshot
    summary: ProposalChangeSummary
    changes: ProposalChangeSet
    inspector: dict[str, object]


class _ProposalPlayback(Protocol):
    def play_detached_notes(self, notes: tuple[Note, ...]) -> None: ...

    def stop_detached_preview(self) -> None: ...


class MusicEditProposalManager:
    """Own one staged proposal without giving it a path to mutate a live project."""

    def __init__(
        self,
        editor: EditorController,
        gateway: MusicAgentToolGateway,
        *,
        playback: _ProposalPlayback | None = None,
    ) -> None:
        if not isinstance(editor, EditorController):
            raise ValueError("editor must be EditorController")
        if not isinstance(gateway, MusicAgentToolGateway):
            raise ValueError("gateway must be MusicAgentToolGateway")
        if not gateway.is_bound_to(editor):
            raise ValueError("gateway must be bound to the same EditorController")
        if playback is not None and (
            not callable(getattr(playback, "play_detached_notes", None))
            or not callable(getattr(playback, "stop_detached_preview", None))
        ):
            raise ValueError("playback must support detached proposal playback")
        self._editor = editor
        self._gateway = gateway
        self._playback = playback
        self._active: _ManagedProposal | None = None
        self._terminal_proposal: MusicEditProposal | None = None
        self._terminal_summary: ProposalChangeSummary | None = None
        self._terminal_changes: ProposalChangeSet | None = None
        self._terminal_inspector: dict[str, object] | None = None
        self._applying = False
        self._editor.stateChanged.connect(self._on_editor_state)

    @property
    def proposal(self) -> MusicEditProposal | None:
        return self._active.captured.proposal if self._active is not None else self._terminal_proposal

    @property
    def staged_state(self) -> ProjectState | None:
        """Immutable pending content for host-specific size validation."""
        return self._active.captured.staged_state if self._active is not None else None

    @property
    def summary(self) -> ProposalChangeSummary | None:
        return self._active.summary if self._active is not None else self._terminal_summary

    @property
    def changes(self) -> ProposalChangeSet | None:
        return self._active.changes if self._active is not None else self._terminal_changes

    @property
    def inspector(self) -> dict[str, object]:
        """Return snapshot-derived, human-readable Proposal comparison data."""
        if self._active is not None:
            return self._active.inspector
        return self._terminal_inspector or {"available": False}

    def register(self, context: MusicAgentExecutionContext) -> MusicEditProposal:
        """Replace a pending suggestion with one current, Gateway-bound proposal."""
        self._discard_pending(MusicEditProposalStatus.DISCARDED)
        captured = self._gateway.seal_proposal(context)
        proposal = captured.proposal
        if not proposal.proposal_id:
            proposal = replace(proposal, proposal_id=context.turn_id)
            captured = replace(captured, proposal=proposal)
        changes = _build_change_set(captured)
        self._active = _ManagedProposal(
            context=context,
            captured=captured,
            summary=_build_summary(captured, changes),
            changes=changes,
            inspector=_build_inspector(captured, changes),
        )
        self._terminal_proposal = None
        self._terminal_summary = None
        self._terminal_changes = None
        self._terminal_inspector = None
        return proposal

    def discard_proposal(self) -> MusicToolResult:
        """Terminally discard the active suggestion without touching controller state."""
        if self._active is None:
            return MusicToolResult.failure("proposal", "conflict", "no pending proposal")
        self._terminalize(MusicEditProposalStatus.DISCARDED)
        return MusicToolResult.success_result("proposal", "proposal discarded")

    def apply_proposal(self) -> MusicToolResult:
        """Commit the whole pending proposal as one controller-owned History transaction."""
        active = self._active
        if active is None:
            return MusicToolResult.failure("proposal", "conflict", "no pending proposal")
        baseline = self._gateway.validate_proposal_baseline(active.captured.proposal)
        if not baseline.success:
            self._terminalize(MusicEditProposalStatus.STALE)
            return MusicToolResult.failure(
                "proposal", "conflict", "工程已发生变化，请重新生成建议。"
            )
        began_turn = False
        self._applying = True
        try:
            self._editor.begin_agent_turn()
            began_turn = True
            for operation, step in zip(
                active.captured.proposal.operations,
                self._gateway.iter_replay_proposal(active.context),
                strict=True,
            ):
                self._editor.preview_agent_state(
                    step.state,
                    step.metadata if operation.name == "apply_chord" else None,
                )
            self._editor.commit_agent_turn()
            began_turn = False
        except Exception:
            if began_turn:
                try:
                    self._editor.cancel_agent_turn()
                except Exception:
                    pass
            self._terminalize(MusicEditProposalStatus.FAILED)
            return MusicToolResult.failure("proposal", "execution_error", "proposal apply failed")
        finally:
            self._applying = False
        self._terminalize(MusicEditProposalStatus.APPLIED)
        return MusicToolResult.success_result("proposal", "proposal applied")

    def audition_proposal(self) -> MusicToolResult:
        """Send an affected-range staged snapshot to the existing playback controller."""
        active = self._active
        if active is None:
            return MusicToolResult.failure("proposal", "conflict", "no pending proposal")
        if self._playback is None:
            return MusicToolResult.failure(
                "proposal", "execution_error", "proposal playback is not available"
            )
        return self._audition_snapshot(
            active.captured.staged_state,
            active.summary.affected_bars,
            "proposal audition started",
        )

    def audition_original_proposal(self) -> MusicToolResult:
        """Play the frozen baseline snapshot for an A/B comparison."""
        active = self._active
        if active is None:
            return MusicToolResult.failure("proposal", "conflict", "no pending proposal")
        return self._audition_snapshot(
            active.captured.baseline_state,
            active.summary.affected_bars,
            "original proposal audition started",
        )

    def _audition_snapshot(
        self,
        state: ProjectState,
        affected_bars: BarRange | None,
        result_summary: str,
    ) -> MusicToolResult:
        if self._playback is None:
            return MusicToolResult.failure(
                "proposal", "execution_error", "proposal playback is not available"
            )
        notes = _audition_notes(state, affected_bars)
        if not notes:
            return MusicToolResult.failure("proposal", "conflict", "proposal has no audible notes")
        try:
            self._playback.play_detached_notes(notes)
        except Exception:
            return MusicToolResult.failure(
                "proposal", "execution_error", "proposal audition failed"
            )
        return MusicToolResult.success_result("proposal", result_summary)

    def _on_editor_state(self) -> None:
        if self._applying or self._active is None:
            return
        context = self._active.context
        if context.project_identity != f"session:{self._editor.projectSessionId}":
            self._terminalize(MusicEditProposalStatus.DISCARDED)
        elif context.baseline_revision != self._editor.projectRevision:
            self._terminalize(MusicEditProposalStatus.STALE)

    def _discard_pending(self, status: MusicEditProposalStatus) -> None:
        if self._active is not None:
            self._terminalize(status)

    def _terminalize(self, status: MusicEditProposalStatus) -> None:
        active = self._active
        if active is None:
            return
        if self._playback is not None:
            self._playback.stop_detached_preview()
        self._gateway.release_proposal(active.context)
        self._terminal_proposal = replace(
            active.captured.proposal,
            operations=(),
            status=status,
        )
        self._terminal_summary = active.summary
        self._terminal_changes = active.changes
        self._terminal_inspector = active.inspector
        self._active = None


def _build_change_set(captured: ProposalStageSnapshot) -> ProposalChangeSet:
    baseline_notes = {note.id: note for note in captured.baseline_state.active_notes}
    staged_notes = {note.id: note for note in captured.staged_state.active_notes}
    added = tuple(
        ProposalNoteChange(note_id, {}, _note_fields(note))
        for note_id, note in staged_notes.items()
        if note_id not in baseline_notes
    )
    deleted = tuple(
        ProposalNoteChange(note_id, _note_fields(note), {})
        for note_id, note in baseline_notes.items()
        if note_id not in staged_notes
    )
    updated = tuple(
        _updated_note_change(
            before,
            after,
            captured.baseline_state,
            captured.staged_state,
        )
        for note_id, before in baseline_notes.items()
        if (after := staged_notes.get(note_id)) is not None
    )
    return ProposalChangeSet(
        added=added,
        updated=tuple(change for change in updated if change is not None),
        deleted=deleted,
        harmony=_harmony_changes(captured.baseline_metadata, captured.staged_metadata),
    )


def _build_summary(
    captured: ProposalStageSnapshot, changes: ProposalChangeSet
) -> ProposalChangeSummary:
    before_bpm = captured.baseline_state.tempo.bpm
    after_bpm = captured.staged_state.tempo.bpm
    return ProposalChangeSummary(
        notes_added=len(changes.added),
        notes_updated=len(changes.updated),
        notes_deleted=len(changes.deleted),
        bpm_before=before_bpm if before_bpm != after_bpm else None,
        bpm_after=after_bpm if before_bpm != after_bpm else None,
        harmony_changes=changes.harmony,
        affected_bars=_affected_bars(captured, changes),
    )


def _updated_note_change(
    before: Note,
    after: Note,
    baseline: ProjectState,
    staged: ProjectState,
) -> ProposalNoteChange | None:
    before_fields: dict[str, object] = {}
    after_fields: dict[str, object] = {}
    if before.pitch != after.pitch:
        before_fields["pitch"] = before.pitch
        after_fields["pitch"] = after.pitch
    if not _same_musical_time(before.start_time, after.start_time, baseline, staged):
        before_fields["start"] = before.start_time
        after_fields["start"] = after.start_time
    if not _same_musical_time(before.duration, after.duration, baseline, staged):
        before_fields["duration"] = before.duration
        after_fields["duration"] = after.duration
    if before.velocity != after.velocity:
        before_fields["velocity"] = before.velocity
        after_fields["velocity"] = after.velocity
    if before.instrument != after.instrument:
        before_fields["instrument"] = before.instrument
        after_fields["instrument"] = after.instrument
    if not before_fields:
        return None
    return ProposalNoteChange(before.id, before_fields, after_fields)


def _same_musical_time(
    before_seconds: float,
    after_seconds: float,
    baseline: ProjectState,
    staged: ProjectState,
) -> bool:
    before_beats = before_seconds / baseline.tempo.seconds_per_beat
    after_beats = after_seconds / staged.tempo.seconds_per_beat
    return abs(before_beats - after_beats) < 1e-9


def _note_fields(note: Note) -> Mapping[str, object]:
    return {
        "pitch": note.pitch,
        "start": note.start_time,
        "duration": note.duration,
        "velocity": note.velocity,
        "instrument": note.instrument,
    }


def _inspector_note_snapshot(
    note: Note | None, state: ProjectState
) -> dict[str, object]:
    """Return a complete ID-free note snapshot in musical and raw coordinates."""
    if note is None:
        return {}
    snapshot = dict(_note_fields(note))
    snapshot.update(
        {
            "noteName": note.note_name,
            # The editor stores seconds, while a proposal may also change BPM.
            # Keep beat coordinates so every consumer can compare/render the
            # two snapshots on the same musical timeline.
            "startBeats": _seconds_to_beats(note.start_time, state),
            "durationBeats": _seconds_to_beats(note.duration, state),
        }
    )
    return snapshot


def _harmony_changes(
    baseline: HarmonyProjectMetadata, staged: HarmonyProjectMetadata
) -> tuple[ProposalHarmonyChange, ...]:
    baseline_groups = {group.group_id: group for group in baseline.groups}
    staged_groups = {group.group_id: group for group in staged.groups}
    additions = tuple(
        _harmony_change("ADDED", group)
        for group_id, group in staged_groups.items()
        if group_id not in baseline_groups
    )
    removals = tuple(
        _harmony_change("DELETED", group)
        for group_id, group in baseline_groups.items()
        if group_id not in staged_groups
    )
    updates = tuple(
        _harmony_change("UPDATED", group)
        for group_id, group in staged_groups.items()
        if group_id in baseline_groups and _harmony_group_changed(
            baseline_groups[group_id], group
        )
    )
    return additions + updates + removals


def _harmony_change(kind: str, group: HarmonyGroup) -> ProposalHarmonyChange:
    return ProposalHarmonyChange(
        kind=kind,
        measure_index=group.measure_index,
        chord_symbol=group.chord_symbol,
        generated_note_count=len(group.generated_note_ids),
    )


def _harmony_group_changed(before: HarmonyGroup, after: HarmonyGroup) -> bool:
    return (
        before.measure_index != after.measure_index
        or before.start_tick != after.start_tick
        or before.duration_ticks != after.duration_ticks
        or before.chord_symbol != after.chord_symbol
        or before.voicing_pitches != after.voicing_pitches
        or before.generated_note_ids != after.generated_note_ids
        or before.detached != after.detached
        or before.application_origin != after.application_origin
        or before.replace_policy != after.replace_policy
    )


def _harmony_snapshot(group: HarmonyGroup) -> dict[str, object]:
    """Return human-facing Harmony facts without generated note identifiers."""
    return {
        "chordSymbol": group.chord_symbol,
        "measureIndex": group.measure_index + 1,
        "startBeat": _number_or_int(group.start_tick / PPQ + 1),
        "durationBeats": _number_or_int(group.duration_ticks / PPQ),
        "voicing": [midi_to_name(pitch) for pitch in group.voicing_pitches],
        "noteCount": len(group.generated_note_ids),
    }


def _harmony_display_fields(
    before: Mapping[str, object], after: Mapping[str, object]
) -> list[dict[str, object]]:
    labels = {
        "chordSymbol": "和弦",
        "measureIndex": "小节",
        "startBeat": "起始",
        "durationBeats": "时值",
        "voicing": "和弦音",
        "noteCount": "音符数",
    }
    result: list[dict[str, object]] = []
    for key, label in labels.items():
        if key not in before and key not in after:
            continue
        before_value = before.get(key)
        after_value = after.get(key)
        if key == "measureIndex":
            before_text = f"第 {before_value} 小节" if before_value is not None else "—"
            after_text = f"第 {after_value} 小节" if after_value is not None else "—"
        elif key == "startBeat":
            before_text = f"第 {_format_number(before_value)} 拍" if before_value is not None else "—"
            after_text = f"第 {_format_number(after_value)} 拍" if after_value is not None else "—"
        elif key == "durationBeats":
            before_text = f"{_format_number(before_value)} 拍" if before_value is not None else "—"
            after_text = f"{_format_number(after_value)} 拍" if after_value is not None else "—"
        elif key == "voicing":
            before_text = "、".join(str(item) for item in before_value) if before_value else "—"
            after_text = "、".join(str(item) for item in after_value) if after_value else "—"
        elif key == "noteCount":
            before_text = f"{before_value} 个音符" if before_value is not None else "—"
            after_text = f"{after_value} 个音符" if after_value is not None else "—"
        else:
            before_text = str(before_value) if before_value is not None else "—"
            after_text = str(after_value) if after_value is not None else "—"
        result.append(
            {
                "key": key,
                "label": label,
                "before": before_value,
                "after": after_value,
                "display": f"{before_text} → {after_text}",
            }
        )
    return result


def _harmony_inspector_detail(
    kind: str,
    before_group: HarmonyGroup | None,
    after_group: HarmonyGroup | None,
) -> dict[str, object]:
    group = after_group or before_group
    assert group is not None
    before = _harmony_snapshot(before_group) if before_group is not None else {}
    after = _harmony_snapshot(after_group) if after_group is not None else {}
    location_source = after or before
    measure = location_source["measureIndex"]
    beat = location_source["startBeat"]
    return {
        "kind": kind,
        "kindLabel": {
            "added": "新增和弦",
            "updated": "修改和弦",
            "deleted": "删除和弦",
        }[kind],
        "location": f"第 {measure} 小节 · 第 {_format_number(beat)} 拍",
        "before": before,
        "after": after,
        "fields": _harmony_display_fields(before, after),
        "technical": {"groupId": group.group_id},
    }


def _harmony_inspector_details(
    baseline: HarmonyProjectMetadata, staged: HarmonyProjectMetadata
) -> tuple[dict[str, object], ...]:
    baseline_groups = {group.group_id: group for group in baseline.groups}
    staged_groups = {group.group_id: group for group in staged.groups}
    additions = tuple(
        _harmony_inspector_detail("added", None, group)
        for group_id, group in staged_groups.items()
        if group_id not in baseline_groups
    )
    updates = tuple(
        _harmony_inspector_detail("updated", baseline_groups[group_id], group)
        for group_id, group in staged_groups.items()
        if group_id in baseline_groups
        and _harmony_group_changed(baseline_groups[group_id], group)
    )
    deletions = tuple(
        _harmony_inspector_detail("deleted", group, None)
        for group_id, group in baseline_groups.items()
        if group_id not in staged_groups
    )
    return additions + updates + deletions


def _build_inspector(
    captured: ProposalStageSnapshot, changes: ProposalChangeSet
) -> dict[str, object]:
    """Build display facts from immutable before/after snapshots only.

    This is deliberately kept at the Proposal presentation boundary. It does
    not alter staged state, replay behavior, or the transaction that Apply uses.
    """
    baseline_notes = {note.id: note for note in captured.baseline_state.active_notes}
    staged_notes = {note.id: note for note in captured.staged_state.active_notes}
    updated = tuple(
        _inspector_detail(
            "updated",
            change,
            baseline_notes[change.note_id],
            staged_notes[change.note_id],
            captured.baseline_state,
            captured.staged_state,
        )
        for change in changes.updated
    )
    added = tuple(
        _inspector_detail(
            "added",
            change,
            None,
            staged_notes[change.note_id],
            captured.baseline_state,
            captured.staged_state,
        )
        for change in changes.added
    )
    deleted = tuple(
        _inspector_detail(
            "deleted",
            change,
            baseline_notes[change.note_id],
            None,
            captured.baseline_state,
            captured.staged_state,
        )
        for change in changes.deleted
    )
    all_details = (*added, *updated, *deleted)
    harmony_details = _harmony_inspector_details(
        captured.baseline_metadata, captured.staged_metadata
    )
    pitch_deltas = [
        int(detail["after"]["pitch"]) - int(detail["before"]["pitch"])
        for detail in updated
        if "pitch" in detail["before"] and "pitch" in detail["after"]
    ]
    position_deltas = [
        _seconds_to_beats(
            float(detail["after"]["start"]), captured.staged_state
        )
        - _seconds_to_beats(float(detail["before"]["start"]), captured.baseline_state)
        for detail in updated
        if "start" in detail["before"] and "start" in detail["after"]
    ]
    duration_deltas = [
        _seconds_to_beats(
            float(detail["after"]["duration"]), captured.staged_state
        )
        - _seconds_to_beats(
            float(detail["before"]["duration"]), captured.baseline_state
        )
        for detail in updated
        if "duration" in detail["before"] and "duration" in detail["after"]
    ]
    velocity_pairs = [
        (int(detail["before"]["velocity"]), int(detail["after"]["velocity"]))
        for detail in updated
        if "velocity" in detail["before"] and "velocity" in detail["after"]
    ]
    summary = {
        "notesAdded": len(added),
        "notesUpdated": len(updated),
        "notesDeleted": len(deleted),
        "pitchChanged": sum("pitch" in detail["before"] for detail in updated),
        "positionChanged": sum("start" in detail["before"] for detail in updated),
        "durationChanged": sum("duration" in detail["before"] for detail in updated),
        "velocityChanged": len(velocity_pairs),
        "instrumentChanged": sum(
            "instrument" in detail["before"] for detail in updated
        ),
        "bpmChanged": captured.baseline_state.tempo.bpm
        != captured.staged_state.tempo.bpm,
        "harmonyChanged": len(harmony_details),
    }
    return {
        "available": True,
        "headline": _inspector_headline(summary),
        "summary": summary,
        "scope": _inspector_scope(captured, changes),
        "bpm": {
            "before": captured.baseline_state.tempo.bpm,
            "after": captured.staged_state.tempo.bpm,
            "changed": captured.baseline_state.tempo.bpm
            != captured.staged_state.tempo.bpm,
        },
        "velocity": _velocity_summary(velocity_pairs),
        "pitch": _pitch_summary(pitch_deltas, updated),
        "timing": _timing_summary(position_deltas, duration_deltas),
        "bars": _bar_summary(all_details),
        "detailCount": len(all_details),
        "representativeDetails": list(all_details[:8]),
        "totalBeats": _inspector_total_beats(all_details),
        "details": {
            "added": list(added),
            "updated": list(updated),
            "deleted": list(deleted),
        },
        "harmony": list(harmony_details),
        "harmonyDetailCount": len(harmony_details),
        "technical": {
            "proposalId": captured.proposal.proposal_id,
            "authorizedBars": [
                {"startBar": item.start_bar, "endBar": item.end_bar}
                for item in captured.proposal.authorized_bars
            ],
        },
    }


def _inspector_headline(summary: Mapping[str, object]) -> str:
    pitch = int(summary["pitchChanged"])
    timing = int(summary["positionChanged"]) + int(summary["durationChanged"])
    velocity = int(summary["velocityChanged"])
    added = int(summary["notesAdded"])
    deleted = int(summary["notesDeleted"])
    if velocity > 0 and not pitch and not timing and not added and not deleted:
        return "本次只调整了力度，没有改变旋律和节奏结构。"
    if pitch and not timing and not velocity and not added and not deleted:
        return "本次调整了音高，没有改变节奏。"
    if timing and not pitch and not velocity and not added and not deleted:
        return "本次主要调整了节奏位置和时值。"
    if pitch and timing and velocity:
        return "本次同时调整了旋律、节奏和力度。"
    if added and not deleted and not pitch and not timing and not velocity:
        return "本次新增了音符。"
    if deleted and not added and not pitch and not timing and not velocity:
        return "本次删除了音符。"
    if not any(
        (
            pitch,
            timing,
            velocity,
            int(summary["instrumentChanged"]),
            int(summary["bpmChanged"]),
            int(summary["harmonyChanged"]),
            added,
            deleted,
        )
    ):
        return "没有检测到音符或音乐参数变化。"
    return "本次包含多种音乐变化。"


def _inspector_scope(
    captured: ProposalStageSnapshot, changes: ProposalChangeSet
) -> dict[str, object]:
    bars = _affected_bars(captured, changes)
    if bars is None:
        return {"startBar": 0, "endBar": 0, "label": "当前工程"}
    return {
        "startBar": bars.start_bar,
        "endBar": bars.end_bar,
        "label": (
            f"第 {bars.start_bar} 小节"
            if bars.start_bar == bars.end_bar
            else f"第 {bars.start_bar}–{bars.end_bar} 小节"
        ),
    }


def _inspector_detail(
    kind: str,
    change: ProposalNoteChange,
    before_note: Note | None,
    after_note: Note | None,
    baseline: ProjectState,
    staged: ProjectState,
) -> dict[str, object]:
    note = after_note or before_note
    assert note is not None
    state = staged if after_note is not None else baseline
    bar, beat = _note_location(note, state)
    before = dict(change.before)
    after = dict(change.after)
    return {
        "kind": kind,
        "kindLabel": {"added": "新增音符", "updated": "修改音符", "deleted": "删除音符"}[kind],
        "noteName": note.note_name,
        "bar": bar,
        "beat": beat,
        "location": f"第 {bar} 小节 · 第 {_format_number(beat)} 拍",
        "before": before,
        "after": after,
        # These are complete musical snapshots without IDs. They let the
        # Piano Roll draw both the proposed location and a deleted/moved ghost
        # without querying live editor state or leaking technical identifiers.
        "beforeNote": _inspector_note_snapshot(before_note, baseline),
        "afterNote": _inspector_note_snapshot(after_note, staged),
        "fields": _display_fields(before, after, baseline, staged),
        "technical": {
            "noteId": change.note_id,
            "before": before,
            "after": after,
        },
    }


def _display_fields(
    before: Mapping[str, object],
    after: Mapping[str, object],
    baseline: ProjectState,
    staged: ProjectState,
) -> list[dict[str, object]]:
    labels = {
        "pitch": "音高",
        "start": "位置",
        "duration": "长度",
        "velocity": "力度",
        "instrument": "乐器",
    }
    result: list[dict[str, object]] = []
    for key, label in labels.items():
        if key not in before and key not in after:
            continue
        before_value: object = before.get(key)
        after_value: object = after.get(key)
        if key == "pitch":
            before_text = midi_to_name(int(before_value)) if before_value is not None else "—"
            after_text = midi_to_name(int(after_value)) if after_value is not None else "—"
        elif key == "start":
            before_text = (
                f"第 {_format_number(_seconds_to_beats(float(before_value), baseline) + 1)} 拍"
                if before_value is not None
                else "—"
            )
            after_text = (
                f"第 {_format_number(_seconds_to_beats(float(after_value), staged) + 1)} 拍"
                if after_value is not None
                else "—"
            )
        elif key == "duration":
            before_text = (
                f"{_format_number(_seconds_to_beats(float(before_value), baseline))} 拍"
                if before_value is not None
                else "—"
            )
            after_text = (
                f"{_format_number(_seconds_to_beats(float(after_value), staged))} 拍"
                if after_value is not None
                else "—"
            )
        else:
            before_text = _format_number(before_value) if before_value is not None else "—"
            after_text = _format_number(after_value) if after_value is not None else "—"
        result.append(
            {
                "key": key,
                "label": label,
                "before": before_value,
                "after": after_value,
                "display": f"{before_text} → {after_text}",
            }
        )
    return result


def _velocity_summary(pairs: list[tuple[int, int]]) -> dict[str, object]:
    if not pairs:
        return {
            "changed": 0,
            "beforeAverage": None,
            "afterAverage": None,
            "beforeMin": None,
            "afterMin": None,
            "beforeMax": None,
            "afterMax": None,
            "increaseCount": 0,
            "decreaseCount": 0,
            "unchangedCount": 0,
            "changePercent": None,
        }
    before_values = [item[0] for item in pairs]
    after_values = [item[1] for item in pairs]
    before_average = sum(before_values) / len(before_values)
    after_average = sum(after_values) / len(after_values)
    return {
        "changed": len(pairs),
        "beforeAverage": _number_or_int(before_average),
        "afterAverage": _number_or_int(after_average),
        "beforeMin": min(before_values),
        "afterMin": min(after_values),
        "beforeMax": max(before_values),
        "afterMax": max(after_values),
        "increaseCount": sum(after > before for before, after in pairs),
        "decreaseCount": sum(after < before for before, after in pairs),
        "unchangedCount": sum(after == before for before, after in pairs),
        "changePercent": _number_or_int(
            ((after_average - before_average) / before_average * 100)
            if before_average
            else 0
        ),
    }


def _pitch_summary(
    deltas: list[int], updated: tuple[dict[str, object], ...]
) -> dict[str, object]:
    pitches = [
        int(detail["after"]["pitch"])
        for detail in updated
        if "pitch" in detail["after"]
    ]
    return {
        "changed": len(deltas),
        "upCount": sum(delta > 0 for delta in deltas),
        "downCount": sum(delta < 0 for delta in deltas),
        "maxSemitones": max((abs(delta) for delta in deltas), default=0),
        "rangeStart": midi_to_name(min(pitches)) if pitches else None,
        "rangeEnd": midi_to_name(max(pitches)) if pitches else None,
    }


def _timing_summary(
    position_deltas: list[float], duration_deltas: list[float]
) -> dict[str, object]:
    return {
        "positionChanged": len(position_deltas),
        "earlyCount": sum(delta < 0 for delta in position_deltas),
        "lateCount": sum(delta > 0 for delta in position_deltas),
        "maxPositionBeats": _number_or_int(
            max((abs(delta) for delta in position_deltas), default=0)
        ),
        "durationChanged": len(duration_deltas),
        "longerCount": sum(delta > 0 for delta in duration_deltas),
        "shorterCount": sum(delta < 0 for delta in duration_deltas),
        "maxDurationBeats": _number_or_int(
            max((abs(delta) for delta in duration_deltas), default=0)
        ),
    }


def _bar_summary(details: tuple[dict[str, object], ...]) -> list[dict[str, int]]:
    counts: dict[int, int] = {}
    for detail in details:
        bar = int(detail["bar"])
        counts[bar] = counts.get(bar, 0) + 1
    return [{"bar": bar, "count": counts[bar]} for bar in sorted(counts)]


def _inspector_total_beats(details: tuple[dict[str, object], ...]) -> float | int:
    """Find the farthest changed note in one BPM-independent coordinate space."""
    total = 0.0
    for detail in details:
        for key in ("beforeNote", "afterNote"):
            note = detail.get(key)
            if not isinstance(note, Mapping):
                continue
            try:
                end = float(note.get("startBeats", 0.0)) + float(
                    note.get("durationBeats", 0.0)
                )
            except (TypeError, ValueError):
                continue
            total = max(total, end)
    return _number_or_int(total)


def _note_location(note: Note, state: ProjectState) -> tuple[int, float]:
    beats = _seconds_to_beats(note.start_time, state)
    bar_length = state.tempo.beats_per_bar_length
    bar = int(floor(beats / bar_length)) + 1
    beat = beats - ((bar - 1) * bar_length)
    return bar, _number_or_int(beat + 1)


def _seconds_to_beats(seconds: float, state: ProjectState) -> float:
    return seconds / state.tempo.seconds_per_beat


def _number_or_int(value: float | int | object) -> float | int | object:
    if isinstance(value, float) and abs(value - round(value)) < 1e-9:
        return int(round(value))
    if isinstance(value, float):
        return round(value, 2)
    return value


def _format_number(value: object) -> str:
    normalized = _number_or_int(value)
    return str(normalized)


def _affected_bars(
    captured: ProposalStageSnapshot, changes: ProposalChangeSet
) -> BarRange | None:
    relevant: list[tuple[Note, ProjectState]] = []
    baseline = {note.id: note for note in captured.baseline_state.active_notes}
    staged = {note.id: note for note in captured.staged_state.active_notes}
    for change in changes.added:
        relevant.append((staged[change.note_id], captured.staged_state))
    for change in changes.updated:
        relevant.append((staged[change.note_id], captured.staged_state))
    for change in changes.deleted:
        relevant.append((baseline[change.note_id], captured.baseline_state))
    if relevant:
        bars = [
            int(floor(note.start_time / state.tempo.bar_duration_seconds)) + 1
            for note, state in relevant
        ]
        return BarRange(min(bars), max(bars))
    authorized = captured.proposal.authorized_bars
    if authorized:
        return BarRange(
            min(item.start_bar for item in authorized),
            max(item.end_bar for item in authorized),
        )
    return None


def _audition_notes(
    state: ProjectState, affected_bars: BarRange | None
) -> tuple[Note, ...]:
    notes = state.active_notes
    origin: float | None = None
    if affected_bars is not None:
        start_bar = max(1, affected_bars.start_bar - 1)
        end_bar = affected_bars.end_bar + 1
        start_seconds = (start_bar - 1) * state.tempo.bar_duration_seconds
        end_seconds = end_bar * state.tempo.bar_duration_seconds
        origin = start_seconds
        notes = tuple(
            note
            for note in notes
            if note.start_time < end_seconds
            and note.start_time + note.duration > start_seconds
        )
    if not notes:
        return ()
    if origin is None:
        origin = min(note.start_time for note in notes)
    normalized: list[Note] = []
    for note in notes:
        start_time = max(0.0, note.start_time - origin)
        end_time = note.start_time + note.duration
        duration = max(0.0, end_time - max(origin, note.start_time))
        if note.start_time < origin:
            duration = max(0.0, end_time - origin)
        normalized.append(note.with_changes(start_time=start_time, duration=duration))
    return tuple(normalized)
