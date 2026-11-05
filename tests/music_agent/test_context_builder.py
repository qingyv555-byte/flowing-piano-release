from __future__ import annotations

import json
from types import SimpleNamespace

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.context_builder import (
    MAX_CONTEXT_HARMONY_RECORDS,
    MAX_CONTEXT_NOTE_RECORDS,
    MAX_CONTEXT_RANGES,
    MAX_CONTEXT_SELECTION_IDS,
    MAX_CONTEXT_MESSAGE_BYTES,
    MAX_CONTEXT_BAR_SPAN,
    MusicAgentContextBuilder,
    SYSTEM_PROMPT,
)
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentExecutionContext,
    MusicPermission,
)
from ai_piano_studio.music_agent.tool_gateway import (
    MAX_COMPACT_CONTEXT_BAR_SPAN,
    MAX_COMPACT_CONTEXT_NOTES,
    MAX_COMPACT_CONTEXT_RANGES,
    MAX_COMPACT_CONTEXT_SOURCE_NOTES,
    MusicAgentToolGateway,
)


def _gateway() -> MusicAgentToolGateway:
    return MusicAgentToolGateway(
        EditorController(
            ProjectState(
                notes=(
                    Note.create(id="selected", pitch=60, start_time=2.0, duration=0.5, velocity=80),
                    Note.create(id="outside", pitch=67, start_time=0.0, duration=0.5, velocity=90),
                )
            )
        )
    )


def test_context_is_compact_and_never_serializes_project_state() -> None:
    """Break caught: model context leaks the editor's complete ProjectState or project JSON."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset(
            {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
            }
        ),
        selected_note_ids=("selected",),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "Make this selected note gentler.", context, requested_bars=(BarRange(2, 2),)
    )

    rendered = "\n".join(str(message["content"]) for message in messages)
    assert "Make this selected note gentler." in rendered
    assert "selected" in rendered
    assert '"bpm":120' in rendered
    assert "outside" not in rendered
    assert "ProjectState" not in rendered
    assert "project_json" not in rendered
    assert "session:" not in rendered


def test_context_exposes_active_layer_summaries_and_selection_scope() -> None:
    melody = MusicLayer.create(id="melody", name="主旋律", role="melody", order=0)
    backing = MusicLayer.create(id="backing", name="伴奏", role="accompaniment", order=1)
    selected = Note.create(
        id="selected", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"
    )
    editor = EditorController(ProjectState(notes=(selected,), layers=(melody, backing)))
    editor.setActiveLayer("melody")
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_PROJECT_SUMMARY, MusicPermission.READ_NOTES}
        ),
        selected_note_ids=("selected",),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "调整当前音符", context, requested_bars=(BarRange(1, 1),)
    )
    facts = json.loads(str(messages[-1]["content"]).split("Authorized compact music context:\n", 1)[1])

    assert facts["activeLayerId"] == "melody"
    assert facts["selectionScope"] == "note"
    assert facts["musicLayers"] == [
        {"id": "melody", "name": "主旋律", "role": "melody", "noteCount": 1,
         "muted": False, "solo": False, "visible": True, "locked": False},
        {"id": "backing", "name": "伴奏", "role": "accompaniment", "noteCount": 0,
         "muted": False, "solo": False, "visible": True, "locked": False},
    ]


def test_context_denies_whole_project_notes_without_explicit_permission() -> None:
    """Break caught: a context request broadens note visibility without WHOLE_PROJECT_READ."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.READ_NOTES}),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "Describe the whole song.", context, requested_bars=(BarRange(1, 2),)
    )

    rendered = "\n".join(str(message["content"]) for message in messages)
    assert "outside" not in rendered
    assert '"nearby_notes":[]' in rendered


def test_context_reads_do_not_consume_the_tool_budget() -> None:
    """Break caught: constructing provider context spends the shared Gateway tool budget."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset(
            {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
            }
        ),
    )

    MusicAgentContextBuilder(gateway).build("Help me.", context)

    assert context.budget.steps == 0


def test_chat_context_does_not_read_or_disclose_project_music_facts() -> None:
    """Break caught: an ordinary question silently reads notes, harmony, and project context."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),), permissions=frozenset()
    )
    calls = 0
    original = gateway.compact_context

    def observed_compact_context(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    gateway.compact_context = observed_compact_context  # type: ignore[method-assign]

    messages = MusicAgentContextBuilder(gateway).build("什么是和弦？", context, intent="CHAT")

    assert calls == 0
    assert messages[0]["role"] == "system"
    assert "Available executable capabilities" in str(messages[0]["content"])
    assert messages[1:] == [
        {"role": "user", "content": "什么是和弦？\n\n输出语言要求：简体中文。"},
    ]


def test_chat_follow_up_can_receive_explicitly_scoped_music_context() -> None:
    """Break caught: a short follow-up loses the active project's bounded context."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset(
            {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
            }
        ),
        selected_note_ids=("selected",),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "好了吗？",
        context,
        intent="CHAT",
        conversation_history=(
            {"role": "user", "content": "写一段旋律"},
            {"role": "assistant", "content": "我正在处理当前工程。"},
        ),
    )

    rendered = str(messages[-1]["content"])
    assert "Current compact music-task context:" in rendered
    assert '"active_note_count":2' in rendered
    assert '"id":"selected"' in rendered
    assert "authorized_bars" in rendered


def test_system_instruction_names_the_real_audio_boundary() -> None:
    """Break caught: a provider promises an audio renderer that the app does not expose."""
    # Offline analysis now exists; it still grants no audio-file delivery.
    assert "no general audio-file delivery tool" in SYSTEM_PROMPT
    assert "playback" in SYSTEM_PROMPT
    assert "Music Tools" in SYSTEM_PROMPT


def test_system_instruction_includes_the_current_effective_capability_boundary() -> None:
    """Break caught: the provider prompt claims capabilities that the current turn cannot use."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES}),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "读取当前旋律",
        context,
        model_capabilities=ModelCapabilities(True, True, True, True, True),
    )
    system = str(messages[0]["content"])

    assert "Available executable capabilities" in system
    assert "notes_read" in system
    assert "notes_write" in system
    assert "audio_render" in system
    assert "Never claim" in system


def test_default_system_instruction_respects_current_permissions() -> None:
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES}),
    )

    messages = MusicAgentContextBuilder(gateway).build("读取当前旋律", context)
    system = str(messages[0]["content"])
    available = system.split("Unavailable capabilities:", 1)[0]

    assert "notes_read" in available
    assert "notes_write" not in available


def test_intent_guidance_separates_advice_from_execution() -> None:
    gateway = _gateway()
    read_context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES}),
    )
    write_context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}
        ),
    )

    advice_system = str(
        MusicAgentContextBuilder(gateway).build(
            "先给我建议，不要修改。", read_context, intent="READ_MUSIC"
        )[0]["content"]
    )
    edit_system = str(
        MusicAgentContextBuilder(gateway).build(
            "直接修改当前旋律。", write_context, intent="EDIT_MUSIC"
        )[0]["content"]
    )

    assert "advice/analysis only" in advice_system
    assert "Do not call write tools" in advice_system
    assert "must perform a write" in edit_system
    # Missing reference material must stop execution instead of forcing invented notes.
    assert "when the required source is available" in edit_system
    assert "If source data is missing, explicitly request it and stop without a write" in edit_system


def test_proposal_edit_prompt_requires_beginner_text_and_real_local_audition_changes() -> None:
    """Break caught: a model mistakes “no audio tool” for “the app cannot audition a Proposal.”"""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}
        ),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "让当前旋律更梦幻。",
        context,
        intent="EDIT_MUSIC",
        model_capabilities=ModelCapabilities(True, True, False, False, True),
    )
    system = str(messages[0]["content"])

    assert "brief beginner-friendly explanation" in system
    assert "stage real MIDI changes" in system
    assert "local Audition button" in system
    assert "do not claim that audition is unavailable" in system
    assert "never promise to create, attach, or send an audio file" in system


def test_chat_context_preserves_prior_user_decision_before_the_follow_up() -> None:
    """Break caught: visible Assistant history is discarded before the next provider request."""
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(), permissions=frozenset()
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "可以，直接开始。",
        context,
        intent="CHAT",
        conversation_history=(
            {"role": "user", "content": "创作 20 秒的十面埋伏高朝钢琴。"},
            {"role": "assistant", "content": "需要先确认是否可以扩展到第 1–10 小节。"},
        ),
    )

    assert messages[0]["role"] == "system"
    assert "Available executable capabilities" in str(messages[0]["content"])
    assert messages[1:] == [
        {"role": "user", "content": "创作 20 秒的十面埋伏高朝钢琴。"},
        {"role": "assistant", "content": "需要先确认是否可以扩展到第 1–10 小节。"},
        {"role": "user", "content": "可以，直接开始。\n\n输出语言要求：简体中文。"},
    ]


def test_history_pins_the_first_user_constraint_when_recent_turns_are_long() -> None:
    """Break caught: a long conversation silently drops the original musical constraint."""
    gateway = _gateway()
    context = gateway.create_execution_context(authorized_bars=(), permissions=frozenset())
    history = (
        {"role": "user", "content": "保持 C 小调、20 秒、先做安静主题"},
        {"role": "assistant", "content": "好的，我会保持 C 小调。"},
        *(
            {"role": "user" if index % 2 == 0 else "assistant", "content": "中间讨论 " + "x" * 1_700}
            for index in range(8)
        ),
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "继续处理", context, intent="CHAT", conversation_history=history
    )
    rendered = "\n".join(str(message["content"]) for message in messages)

    assert "保持 C 小调、20 秒、先做安静主题" in rendered
    assert "中间讨论 " in rendered


def test_intent_template_is_visible_as_structured_musical_guidance() -> None:
    gateway = _gateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.READ_PROJECT_SUMMARY}),
        intent_template="make_climax",
    )

    messages = MusicAgentContextBuilder(gateway).build(
        "后半段更有高潮感", context, intent="EDIT_MUSIC"
    )
    rendered = str(messages[-1]["content"])

    assert '"intent_template":"make_climax"' in rendered
    assert "保留可辨认的主题动机" in rendered


class _LargeContextGateway:
    def compact_context(self, _context: object, _ranges: tuple[BarRange, ...]) -> dict[str, object]:
        return {
            "summary": {
                "bpm": 120,
                "beats_per_bar": 4,
                "beat_unit": 4,
                "active_note_count": 9_999,
                "raw": "x" * 10_000,
            },
            "nearby_notes": [
                {"id": f"note-{index}", "pitch": 60, "unbounded": "x" * 1_000}
                for index in range(MAX_CONTEXT_NOTE_RECORDS + 10)
            ],
            "harmony": [
                {"measure_index": index, "candidates": [{"chord_symbol": "C"}] * 100}
                for index in range(MAX_CONTEXT_HARMONY_RECORDS + 10)
            ],
        }


def test_context_has_deterministic_scope_and_record_bounds() -> None:
    """Break caught: a large authorized scope forwards every raw note, harmony fact, or summary field."""
    context = MusicAgentExecutionContext(
        project_identity="test",
        baseline_revision=0,
        authorized_bars=tuple(BarRange(index, index) for index in range(1, MAX_CONTEXT_RANGES + 10)),
        permissions=frozenset(),
        selected_note_ids=tuple(
            f"selection-{index}-{'x' * 10_000}"
            for index in range(MAX_CONTEXT_SELECTION_IDS + 10)
        ),
    )

    messages = MusicAgentContextBuilder(_LargeContextGateway()).build("Summarize.", context)

    rendered = str(messages[-1]["content"])
    assert rendered.count('"start_bar"') == MAX_CONTEXT_RANGES * 2
    assert rendered.count('"id":"note-') <= MAX_CONTEXT_NOTE_RECORDS
    assert rendered.count('"measure_index"') <= MAX_CONTEXT_HARMONY_RECORDS
    assert rendered.count('"selection-') <= MAX_CONTEXT_SELECTION_IDS
    assert len(rendered.encode("utf-8")) <= MAX_CONTEXT_MESSAGE_BYTES
    assert "x" * 1_000 not in rendered
    assert '"raw"' not in rendered
    assert '"unbounded"' not in rendered


def test_oversized_context_reduces_dense_notes_progressively_instead_of_dropping_all() -> None:
    """Break caught: an 8 KB overflow jumps directly from 128 nearby notes to zero notes."""
    facts = {
        "selection": [f"selection-{index}-{'s' * 100}" for index in range(66)],
        "authorized_bars": [{"start_bar": 1, "end_bar": 5}],
        "requested_bars": [{"start_bar": 1, "end_bar": 5}],
        "summary": {"bpm": 120, "beats_per_bar": 4, "beat_unit": 4, "active_note_count": 66},
        "nearby_notes": [
            {
                "id": f"dense-{index:02d}-{'x' * 100}",
                "pitch": 48 + index % 24,
                "start_time": index * 0.125,
                "duration": 0.1,
                "velocity": 55 + index % 45,
            }
            for index in range(66)
        ],
        "harmony": [],
    }

    rendered = MusicAgentContextBuilder._serialize_facts(facts)
    payload = json.loads(rendered)

    assert len(rendered.encode("utf-8")) <= 8_000
    assert payload["context_truncated"] is True
    assert 16 <= len(payload["nearby_notes"]) < 66


def test_gateway_compact_context_stops_collecting_dense_authorized_notes_at_the_source() -> None:
    """Break caught: a whole-project context materializes every dense note before Builder caps it."""
    notes = tuple(
        Note.create(
            id=f"dense-{index}",
            pitch=60,
            start_time=index * 0.001,
            duration=0.0005,
            velocity=80,
        )
        for index in range(MAX_COMPACT_CONTEXT_NOTES + 100)
    )
    gateway = MusicAgentToolGateway(EditorController(ProjectState(notes=notes)))
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES, MusicPermission.WHOLE_PROJECT_READ}),
    )

    source = gateway.compact_context(context, (BarRange(1, 1),))

    assert len(source["nearby_notes"]) == MAX_COMPACT_CONTEXT_NOTES
    assert source["nearby_notes"][-1]["id"] == f"dense-{MAX_COMPACT_CONTEXT_NOTES - 1}"


def test_context_clamps_a_whole_project_range_before_harmony_collection() -> None:
    """Break caught: one authorized whole-project range reaches Harmony analysis without a bounded span."""
    context = MusicAgentExecutionContext(
        project_identity="test",
        baseline_revision=0,
        authorized_bars=(BarRange(1, 10_000),),
        permissions=frozenset(),
    )

    messages = MusicAgentContextBuilder(_LargeContextGateway()).build("Help.", context)

    assert '"requested_bars":[{"start_bar":1,"end_bar":%d}]' % MAX_CONTEXT_BAR_SPAN in str(
        messages[-1]["content"]
    )


class _RangeRecordingGateway(MusicAgentToolGateway):
    def __init__(self) -> None:
        super().__init__(EditorController(ProjectState()))
        self.requested_ends: list[int] = []

    def _compact_harmony(
        self, payload: object, context: object, *, remaining: int
    ) -> tuple[list[dict[str, object]], bool]:
        self.requested_ends.append(payload["end_bar"])
        return [], False


def test_gateway_clamps_direct_whole_project_harmony_collection() -> None:
    """Break caught: a caller of the read-only Gateway surface can bypass Builder's range cap."""
    gateway = _RangeRecordingGateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 10_000),),
        permissions=frozenset({MusicPermission.READ_HARMONY, MusicPermission.WHOLE_PROJECT_READ}),
    )

    gateway.compact_context(context, (BarRange(1, 10_000),))

    assert gateway.requested_ends == [MAX_COMPACT_CONTEXT_BAR_SPAN]


def test_gateway_caps_direct_requested_range_count_before_collection() -> None:
    """Break caught: direct callers can trigger unbounded context reads with many small ranges."""
    gateway = _RangeRecordingGateway()
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 10_000),),
        permissions=frozenset({MusicPermission.READ_HARMONY, MusicPermission.WHOLE_PROJECT_READ}),
    )

    gateway.compact_context(context, tuple(BarRange(index, index) for index in range(1, 20)))

    assert len(gateway.requested_ends) == MAX_COMPACT_CONTEXT_RANGES


class _HarmonyInputGateway(MusicAgentToolGateway):
    def __init__(self, notes: tuple[Note, ...]) -> None:
        super().__init__(EditorController(ProjectState(notes=notes)))
        self.harmony_input_notes = -1

    def _current_harmony_context(self, _requested: object, *, state: ProjectState, metadata: object) -> object:
        self.harmony_input_notes = len(state.notes)
        return SimpleNamespace(result=SimpleNamespace(suggestions=()))


def test_gateway_caps_dense_harmony_input_before_analysis() -> None:
    """Break caught: bounded Harmony output still analyzes every dense note in the requested bars."""
    notes = tuple(
        Note.create(
            id=f"harmony-{index}",
            pitch=60,
            start_time=index * 0.001,
            duration=0.0005,
            velocity=80,
        )
        for index in range(MAX_COMPACT_CONTEXT_NOTES + 100)
    )
    gateway = _HarmonyInputGateway(notes)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_HARMONY, MusicPermission.WHOLE_PROJECT_READ}),
    )

    gateway.compact_context(context, (BarRange(1, 1),))

    assert gateway.harmony_input_notes == MAX_COMPACT_CONTEXT_NOTES


class _CountingNotes(tuple[Note, ...]):
    def __new__(cls, notes: tuple[Note, ...]) -> _CountingNotes:
        value = super().__new__(cls, notes)
        value.examined = 0
        return value

    def __iter__(self):
        for note in super().__iter__():
            self.examined += 1
            yield note


def test_sparse_source_scans_are_bounded_and_marked_truncated() -> None:
    """Break caught: sparse/deleted ranges traverse every project note and look complete to the provider."""
    source_notes = _CountingNotes(
        tuple(
            Note.create(
                id=f"outside-{index}",
                pitch=60,
                start_time=10_000.0 + index,
                duration=0.5,
                velocity=80,
            )
            for index in range(MAX_COMPACT_CONTEXT_SOURCE_NOTES + 100)
        )
    )
    gateway = MusicAgentToolGateway(EditorController(ProjectState(notes=source_notes)))
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
                MusicPermission.WHOLE_PROJECT_READ,
            }
        ),
    )
    source_notes.examined = 0

    source = gateway.compact_context(context, (BarRange(1, 1),))

    assert source_notes.examined <= MAX_COMPACT_CONTEXT_SOURCE_NOTES * 2
    assert source["nearby_notes"] == []
    assert source["harmony"] == []
    assert source["context_truncated"] is True
    rendered = str(MusicAgentContextBuilder(gateway).build("Help.", context)[-1]["content"])
    assert '"context_truncated":true' in rendered


def test_chat_uses_partner_guidance_without_unrelated_execution_contract():
    gateway = _gateway()
    context = gateway.create_execution_context(authorized_bars=(), permissions=frozenset())
    messages = MusicAgentContextBuilder(gateway).build("我有点想法，但不知道从哪开始", context, intent="CHAT")
    system = str(messages[0]['content'])
    assert 'music-making partner' in system
    assert 'one focused question' in system
    assert 'get_notes returns' not in system
    assert 'For an exact existing-song request' not in system
    assert 'must perform a write' not in system
    assert 'Available executable capabilities' in system


def test_autonomous_prompt_matches_review_before_application():
    gateway = _gateway()
    context = gateway.create_execution_context(authorized_bars=(BarRange(1, 4),), permissions=frozenset(MusicPermission))
    system = str(MusicAgentContextBuilder(gateway).build('写20秒原创', context,
                 intent='CREATE_MUSIC', intent_template='autonomous_original')[0]['content'])
    assert 'explicitly accepted original alternative' in system
    assert 'user chooses audition and explicit application before saving' in system
    assert 'save and audition after successful' not in system
