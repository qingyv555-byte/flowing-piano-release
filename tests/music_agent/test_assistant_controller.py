from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread, get_ident

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import (
    AssistantMessageKind,
    MusicAssistantController,
)
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoopOutcome
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.providers import (
    ProviderError,
    ProviderEvent,
    ProviderToolCall,
)
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicPermission,
    MusicToolResult,
)
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "assistant.ini"), QSettings.Format.IniFormat)


def _editor() -> EditorController:
    note = Note.create(id="source", pitch=60, start_time=0.5, duration=0.5)
    return EditorController(ProjectState((note,), frozenset({note.id})))


def _assistant(tmp_path: Path, editor: EditorController | None = None) -> MusicAssistantController:
    editor = editor or _editor()
    return MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
    )


def test_switching_provider_does_not_reuse_another_connection_result(tmp_path):
    from ai_piano_studio.music_agent.provider_settings import ConnectionResult, ConnectionResultCode
    assistant = _assistant(tmp_path)
    assistant._connection_result = ConnectionResult(ConnectionResultCode.SUCCESS, "other endpoint succeeded")
    assert assistant.selectProvider("ollama")
    assert assistant.connectionResult["code"] == "UNKNOWN_ERROR"
    assert "other endpoint" not in assistant.connectionResult["message"]


def test_editing_connection_invalidates_previous_success(tmp_path):
    from ai_piano_studio.music_agent.provider_settings import ConnectionResult, ConnectionResultCode
    assistant = _assistant(tmp_path)
    assistant._connection_result = ConnectionResult(ConnectionResultCode.SUCCESS, "old configuration")
    result = assistant.saveProviderSettings("openai", "OpenAI", "https://api.openai.com/v1",
                                            "test-only-key", "gpt-4o-mini", True)
    assert result["success"]
    assert assistant.connectionResult["code"] == "UNKNOWN_ERROR"
    assert "尚未测试" in assistant.connectionResult["message"]


@dataclass
class _ScriptedLoop:
    gateway: object
    action: Callable[[object, object], MusicAgentLoopOutcome]

    def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
        return self.action(self.gateway, context)


def _loop_assistant(
    tmp_path: Path,
    editor: EditorController,
    action: Callable[[object, object], MusicAgentLoopOutcome],
) -> MusicAssistantController:
    gateway = MusicAgentToolGateway(editor)
    return MusicAssistantController(
        editor,
        gateway=gateway,
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: _ScriptedLoop(kwargs["gateway"], action),
    )


def _staged_proposal(assistant: MusicAssistantController):
    gateway = assistant.gateway
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    return context


def _wait_until(qapp, predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    assert predicate()


def test_project_context_exposes_title_tempo_signature_and_selected_bar_range(tmp_path: Path) -> None:
    """Break caught: the Assistant has no user-visible indication of its active project."""
    editor = _editor()
    assistant = _assistant(tmp_path, editor)

    context = assistant.projectContext

    assert context == {
        "title": "未命名工程",
        "bpm": 120,
        "timeSignature": "4/4",
        "rangeText": "当前范围：第 1–1 小节",
        "hasSelection": True,
    }


def test_project_context_updates_after_project_switch_without_importing_old_chat(tmp_path: Path) -> None:
    """Break caught: Assistant chat continues to visually describe the old project after a switch."""
    editor = _editor()
    assistant = _assistant(tmp_path, editor)

    editor.newProject()

    assert assistant.projectContext["title"] == "未命名工程"
    assert assistant.messages.entries == (), "New projects have independent chats"


def test_automatic_creation_scope_does_not_limit_the_model_by_requested_duration(tmp_path: Path) -> None:
    """Break caught: a duration phrase silently caps a model's note output to estimated bars."""
    assistant = _assistant(tmp_path)

    context = assistant._create_execution_context("CREATE_MUSIC", "来一段十面埋伏的高朝钢琴，20s")

    assert context.authorized_bars == ()
    assert MusicPermission.WHOLE_PROJECT_WRITE in context.permissions


def test_manual_creation_scope_is_applied_without_starting_a_provider_turn(tmp_path: Path) -> None:
    """Break caught: users cannot inspect or change the authorization that limits new music."""
    assistant = _assistant(tmp_path)

    assert assistant.setCreationScopeBars(3, 14) is True
    context = assistant._create_execution_context("CREATE_MUSIC", "创作钢琴旋律")

    assert assistant.creationScope == {
        "mode": "manual",
        "startBar": 3,
        "endBar": 14,
        "label": "第 3–14 小节",
        "unlimited": False,
    }
    assert context.authorized_bars == (BarRange(3, 14),)


def test_explicit_user_range_overrides_selection_for_music_context(tmp_path: Path) -> None:
    """Break caught: a visible selection silently wins over an explicit bar range in the request."""
    assistant = _assistant(tmp_path)

    context = assistant._create_execution_context("EDIT_MUSIC", "只修改第 9–12 小节")

    assert context.authorized_bars == (BarRange(9, 12),)
    assert context.selected_note_ids == ()
    assert MusicPermission.WHOLE_PROJECT_WRITE not in context.permissions


def test_quick_action_passes_a_named_intent_template_to_the_agent(tmp_path: Path, qapp) -> None:
    received_templates: list[str] = []

    class IntentRecordingLoop:
        def run(self, _instruction: str, context: object, **kwargs: object) -> MusicAgentLoopOutcome:
            received_templates.append(kwargs["intent_template"])
            return MusicAgentLoopOutcome("finished", "已按目标处理。", context)

    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **_kwargs: IntentRecordingLoop(),
    )

    assert assistant.sendQuickAction("make_climax", "后半段更有高潮感") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert received_templates == ["make_climax"]


def test_unverified_creation_does_not_start_a_provider_turn(tmp_path: Path) -> None:
    """Break caught: an unverified music request spends a provider call before showing its required setup step."""
    editor = _editor()
    started_loops: list[dict[str, object]] = []
    settings = ProviderSettingsService(
        settings=_settings(tmp_path), credentials=MemoryCredentialStore()
    )
    settings.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4-pro", False)
    settings.set_dynamic_models("deepseek", ["deepseek-v4-pro"])
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=settings,
        agent_loop_factory=lambda **kwargs: started_loops.append(kwargs),
    )
    assert assistant.selectProvider("deepseek") is True
    assert assistant.currentModelStatus["state"] == "unverified"

    assert assistant.sendMessage("创作 20 秒钢琴旋律") is False

    assert started_loops == []
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ERROR
    assert "验证并开始创作" in assistant.messages.entries[-1].text


def test_follow_up_turn_passes_prior_user_and_assistant_messages_to_the_provider(
    tmp_path: Path, qapp
) -> None:
    """Break caught: a follow-up such as “可以扩展授权范围” arrives without its prior decision."""
    received_histories: list[tuple[dict[str, str], ...]] = []

    class HistoryRecordingLoop:
        def run(self, _instruction: str, context: object, **kwargs: object) -> MusicAgentLoopOutcome:
            received_histories.append(kwargs["conversation_history"])
            return MusicAgentLoopOutcome("finished", "已记住前文。", context)

    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **_kwargs: HistoryRecordingLoop(),
    )

    assert assistant.sendMessage("先创作一段 20 秒钢琴旋律") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.sendMessage("可以扩展授权范围") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert received_histories[-1] == (
        {"role": "user", "content": "先创作一段 20 秒钢琴旋律"},
        {"role": "assistant", "content": "已记住前文。"},
    )


def test_assistant_text_is_not_silently_truncated_at_one_thousand_characters(tmp_path: Path) -> None:
    """Break caught: a long assistant reply loses its earlier decision before a user can follow up."""
    assistant = _assistant(tmp_path)
    reply = "a" * 2_500

    assert assistant._safe_text(reply) == reply


def test_safe_text_hides_machine_payloads_and_hidden_reasoning(tmp_path: Path) -> None:
    assistant = _assistant(tmp_path)

    assert assistant._safe_text('{"notes":[{"pitch":60}]}') == "助手已完成回复。"
    assert assistant._safe_text("<think>内部推理不应显示</think>已准备好建议。") == "已准备好建议。"


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("你好", "CHAT"),
        ("什么是和弦？", "CHAT"),
        ("这段感觉怎么样", "READ_MUSIC"),
        ("看看当前旋律", "READ_MUSIC"),
        ("看看当前音符", "READ_MUSIC"),
        ("看看选中的音符", "READ_MUSIC"),
        ("分析当前旋律", "READ_MUSIC"),
        ("让当前选中的部分更梦幻，但保持清晰的主题和节拍，先给我建议。", "READ_MUSIC"),
        ("在当前目标范围加入一个有层次的高潮，先给我建议。", "READ_MUSIC"),
        ("分析一下当前旋律，先不要修改。", "READ_MUSIC"),
        ("先看看怎么改当前旋律", "READ_MUSIC"),
        ("分析一下怎么修改", "READ_MUSIC"),
        ("给我建议，不修改当前旋律", "READ_MUSIC"),
        ("告诉我如何修改当前旋律", "READ_MUSIC"),
        ("把选中的音升高一个八度", "EDIT_MUSIC"),
        ("把当前选择提高八度", "EDIT_MUSIC"),
        ("把当前选择降低半音", "EDIT_MUSIC"),
        ("把当前选择的力度调高", "EDIT_MUSIC"),
        ("帮我修改当前旋律", "EDIT_MUSIC"),
        ("直接把当前选中的部分改得更梦幻，保持主题和节拍。", "EDIT_MUSIC"),
        ("按这个方案修改", "EDIT_MUSIC"),
        ("加入高潮", "EDIT_MUSIC"),
        ("添加和弦", "EDIT_MUSIC"),
        ("更梦幻", "EDIT_MUSIC"),
        ("优化旋律", "EDIT_MUSIC"),
        ("写一段10秒忧伤旋律", "CREATE_MUSIC"),
        ("做一个高潮，但先让我试听再决定", "EDIT_MUSIC"),
        ("再柔和一点", "EDIT_MUSIC"),
    ],
)
def test_classify_intent_uses_local_music_action_boundaries(
    tmp_path: Path, instruction: str, expected: str
) -> None:
    """Break caught: the model alone chooses whether an ordinary chat becomes a write-capable turn."""
    assert _assistant(tmp_path)._classify_intent(instruction) == expected


def test_ambiguous_music_follow_ups_keep_the_active_task_or_read_its_result(tmp_path: Path) -> None:
    """Break caught: short confirmations/status questions fall into context-free chat."""
    assistant = _assistant(tmp_path)
    assistant._last_music_intent = "CREATE_MUSIC"

    assert assistant._resolve_turn_intent("允许") == "CREATE_MUSIC"
    assert assistant._resolve_turn_intent("给我方案，我要试听") == "READ_MUSIC"
    assert assistant._resolve_turn_intent("好了吗？") == "READ_MUSIC"
    assert assistant._resolve_turn_intent("预览一下") == "READ_MUSIC"
    assert assistant._resolve_turn_intent("看看结果") == "READ_MUSIC"
    assert assistant._resolve_turn_intent("可以告诉我和弦是什么") == "CHAT"
    assert assistant._resolve_turn_intent("你好") == "CHAT"


@pytest.mark.parametrize(
    "instruction",
    [
        "第 1–5 小节",
        "第1到5小节",
        "1–5 小节",
        "1-5小节",
        "当前 1–5 小节",
        "当前1到5小节",
        "bars 1-5",
        "bar 1-5",
        "直接把当前 1–5 小节变得更梦幻",
    ],
)
def test_explicit_bar_range_parser_accepts_natural_chinese_and_english_forms(
    instruction: str,
) -> None:
    """Break caught: a natural 1–5 bar phrase is split into selected-note bar ranges."""
    assert MusicAssistantController._explicit_bar_ranges(instruction) == (BarRange(1, 5),)


@pytest.mark.parametrize(
    "instruction",
    ["力度从 60 调到 90", "第 1 个音符移动到第 5 个位置", "速度 120-140 BPM"],
)
def test_explicit_bar_range_parser_ignores_unlabelled_number_ranges(instruction: str) -> None:
    """Break caught: ordinary numeric parameters are accidentally treated as bar authorization."""
    assert MusicAssistantController._explicit_bar_ranges(instruction) == ()


@pytest.mark.parametrize(
    "instruction",
    [
        "做一个高潮，但先试听",
        "让当前旋律更梦幻，先预览",
        "调整当前旋律，让我试听后再应用",
        "先生成一个试听方案，把当前旋律变柔和",
        "把当前旋律升高八度，不要直接应用",
        "给我一个可以试听的方案，把当前旋律变温暖",
    ],
)
def test_preview_request_overrides_only_the_current_collaboration_turn(
    tmp_path: Path, instruction: str
) -> None:
    """Break caught: preview wording writes directly or permanently changes global mode."""
    assistant = _assistant(tmp_path)
    assistant.confirmCollaborationMode()
    intent = assistant._classify_intent(instruction)

    context = assistant._create_execution_context(intent, instruction)

    assert intent in {"EDIT_MUSIC", "CREATE_MUSIC"}
    assert context.mode.value == "proposal"
    assert assistant.mode == "collaboration"


def test_direct_edit_without_preview_keeps_collaboration_mode(tmp_path: Path) -> None:
    """Break caught: Preview Override accidentally converts every collaboration edit to Proposal."""
    assistant = _assistant(tmp_path)
    assistant.confirmCollaborationMode()

    context = assistant._create_execution_context(
        "EDIT_MUSIC", "帮我修改当前旋律，现在执行"
    )

    assert context.mode.value == "collaboration"


def test_suggestion_mode_actionable_advice_defaults_to_a_proposal_intent(
    tmp_path: Path,
) -> None:
    """Break caught: “先给建议” consumes one read-only call and requires a second call for audition."""
    assistant = _assistant(tmp_path)

    intent = assistant._resolve_turn_intent(
        "让当前旋律更梦幻，保留主题，先给建议"
    )
    context = assistant._create_execution_context(
        intent,
        "让当前旋律更梦幻，保留主题，先给建议",
    )

    assert assistant.mode == "suggestion"
    assert intent == "EDIT_MUSIC"
    assert context.mode.value == "proposal"
    assert MusicPermission.WRITE_NOTES in context.permissions


def test_suggestion_actionable_advice_returns_text_and_proposal_in_one_call(
    tmp_path: Path, qapp
) -> None:
    """Break caught: beginner advice needs a second paid model turn before it can be auditioned."""
    calls: list[tuple[str, object, str]] = []

    class OneCallProposalLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(
            self, instruction: str, context: object, **kwargs: object
        ) -> MusicAgentLoopOutcome:
            calls.append((instruction, context, str(kwargs["intent"])))
            result = self._gateway.execute(
                "transpose_notes",
                {"note_ids": ["source"], "semitones": 1},
                context,
            )
            assert result.success
            return MusicAgentLoopOutcome(
                "proposal",
                "我会保留主题，并轻微抬高旋律，让变化更梦幻。",
                context,
                proposal=self._gateway.current_proposal(context),
                tool_results=(result,),
            )

    editor = _editor()
    original_revision = editor.projectRevision
    gateway = MusicAgentToolGateway(editor)
    assistant = MusicAssistantController(
        editor,
        gateway=gateway,
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: OneCallProposalLoop(kwargs["gateway"]),
    )

    assert assistant.sendMessage(
        "让当前旋律更梦幻，保留主题，先给建议"
    ) is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)

    assert len(calls) == 1
    assert calls[0][2] == "EDIT_MUSIC"
    assert calls[0][1].mode.value == "proposal"
    message_kinds = [entry.kind for entry in assistant.messages.entries]
    assert message_kinds.index(AssistantMessageKind.ASSISTANT_MESSAGE) < message_kinds.index(
        AssistantMessageKind.PROPOSAL
    )
    assert "保留主题" in next(
        entry.text
        for entry in assistant.messages.entries
        if entry.kind is AssistantMessageKind.ASSISTANT_MESSAGE
    )
    assert editor.state.active_notes[0].pitch == 60
    assert editor.projectRevision == original_revision
    assert editor._history.undo_depth == 0


@pytest.mark.parametrize(
    "instruction",
    (
        "让当前旋律更梦幻，但不要试听，只给文字",
        "分析当前旋律，只给建议，不要修改",
        "只分析当前音高与节奏结构",
        "不要修改，先分析怎么调整力度",
        "不要修改，告诉我调整力度的方法",
    ),
)
def test_explicit_preview_opt_out_remains_read_only(
    tmp_path: Path, instruction: str
) -> None:
    """Break caught: an explicit no-audition request still receives write-capable tools."""
    assistant = _assistant(tmp_path)

    intent = assistant._resolve_turn_intent(instruction)
    context = assistant._create_execution_context(intent, instruction)

    assert intent == "READ_MUSIC"
    assert MusicPermission.WRITE_NOTES not in context.permissions
    assert MusicPermission.WRITE_BPM not in context.permissions
    assert MusicPermission.WRITE_HARMONY not in context.permissions


@pytest.mark.parametrize(
    "instruction",
    (
        "不要只给建议，直接修改当前旋律",
        "不要修改节奏，只调整力度",
        "不要修改节奏，只调整力度，并说明为什么",
        "先给建议，把当前旋律变得更梦幻",
    ),
)
def test_suggestion_mode_does_not_confuse_scoped_negation_with_preview_opt_out(
    tmp_path: Path, instruction: str
) -> None:
    """A negated advice phrase or scoped edit constraint must still produce a Proposal."""
    assistant = _assistant(tmp_path)

    intent = assistant._resolve_turn_intent(instruction)
    context = assistant._create_execution_context(intent, instruction)

    assert intent == "EDIT_MUSIC"
    assert context.mode.value == "proposal"
    assert MusicPermission.WRITE_NOTES in context.permissions


def test_explicit_no_audition_never_offers_a_second_model_call(
    tmp_path: Path, qapp
) -> None:
    """Break caught: “不要试听” still advertises a paid advice-to-Proposal follow-up."""
    read_calls = 0

    def read_advice(gateway: object, context: object) -> MusicAgentLoopOutcome:
        nonlocal read_calls
        read_calls += 1
        result = gateway.execute(
            "get_notes", {"start_bar": 1, "end_bar": 1}, context
        )
        assert result.success
        return MusicAgentLoopOutcome(
            "finished",
            "当前旋律以级进为主，节奏稳定。",
            context,
            tool_results=(result,),
        )

    assistant = _loop_assistant(tmp_path, _editor(), read_advice)

    assert assistant.sendMessage(
        "让当前旋律更梦幻，但只给文字，不要试听"
    ) is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert read_calls == 1
    assert assistant.lastTurnResult["intent"] == "READ_MUSIC"
    assert assistant.lastTurnResult["writeCount"] == 0
    assert assistant._pending_music_intent == ""
    assert assistant.canGenerateAdvicePreview is False
    assert any(
        entry.kind is AssistantMessageKind.ASSISTANT_MESSAGE
        and "级进为主" in entry.text
        for entry in assistant.messages.entries
    )


def test_advice_can_be_turned_into_an_inherited_preview_without_retyping(
    tmp_path: Path, qapp
) -> None:
    """Break caught: advice has no local action that reuses its target, range, and constraints."""
    calls: list[tuple[str, object, str, str]] = []

    class AdviceThenProposalLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(self, instruction: str, context: object, **kwargs: object) -> MusicAgentLoopOutcome:
            intent = str(kwargs["intent"])
            calls.append((instruction, context, intent, str(kwargs["intent_template"])))
            if intent == "READ_MUSIC":
                result = self._gateway.execute(
                    "get_notes", {"start_bar": 1, "end_bar": 5}, context
                )
                assert result.success
                return MusicAgentLoopOutcome(
                    "finished", "建议逐步抬高音区并增加力度层次。", context, tool_results=(result,)
                )
            result = self._gateway.execute(
                "transpose_notes", {"note_ids": ["source"], "semitones": 1}, context
            )
            assert result.success
            return MusicAgentLoopOutcome(
                "proposal",
                "已生成可试听方案。",
                context,
                proposal=self._gateway.current_proposal(context),
                tool_results=(result,),
            )

    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    assistant = MusicAssistantController(
        editor,
        gateway=gateway,
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: AdviceThenProposalLoop(kwargs["gateway"]),
    )
    assistant.confirmCollaborationMode()

    assert assistant.sendQuickAction(
        "make_climax", "让第 1–5 小节形成高潮，但保留主题，先给建议"
    ) is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.canGenerateAdvicePreview is True
    expected_advice_row = max(
        index
        for index, entry in enumerate(assistant.messages.entries)
        if entry.kind is AssistantMessageKind.ASSISTANT_MESSAGE
    )
    assert assistant.advicePreviewMessageRow == expected_advice_row
    assert assistant.proposal["available"] is False

    assert assistant.generateAdvicePreview() is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)

    assert [item[2] for item in calls] == ["READ_MUSIC", "EDIT_MUSIC"]
    assert calls[0][1].authorized_bars == calls[1][1].authorized_bars == (BarRange(1, 5),)
    assert calls[1][1].mode.value == "proposal"
    assert MusicPermission.WRITE_NOTES in calls[1][1].permissions
    assert calls[0][3] == calls[1][3] == "make_climax"
    assert "保留主题" in calls[1][0]
    assert assistant.mode == "collaboration"
    assert assistant.proposal["available"] is True


def test_advice_then_short_approval_upgrades_the_pending_music_task_to_edit(
    tmp_path: Path, qapp
) -> None:
    """Break caught: a read-only advice turn makes the following “可以” read-only forever."""
    calls: list[tuple[str, object, str]] = []

    class IntentRecordingLoop:
        def run(self, instruction: str, context: object, **kwargs: object) -> MusicAgentLoopOutcome:
            calls.append((instruction, context, str(kwargs["intent"])))
            return MusicAgentLoopOutcome("finished", "已给出建议。", context)

    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **_kwargs: IntentRecordingLoop(),
    )
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("分析一下当前范围怎么变得更梦幻，先给我建议") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.sendMessage("可以") is True
    _wait_until(qapp, lambda: assistant.assistantStatus in {"idle", "error"})

    assert [intent for _instruction, _context, intent in calls] == [
        "READ_MUSIC",
        "EDIT_MUSIC",
    ]
    first_context = calls[0][1]
    second_context = calls[1][1]
    assert first_context.authorized_bars == second_context.authorized_bars
    assert MusicPermission.WRITE_NOTES not in first_context.permissions
    assert MusicPermission.WRITE_NOTES in second_context.permissions


def test_advice_then_allow_keeps_the_explicit_bar_range(tmp_path: Path, qapp) -> None:
    """Break caught: confirming a read-only plan drops its explicit 1–5 bar scope."""
    calls: list[tuple[str, object]] = []

    class IntentRecordingLoop:
        def run(self, _instruction: str, context: object, **kwargs: object) -> MusicAgentLoopOutcome:
            calls.append((str(kwargs["intent"]), context))
            return MusicAgentLoopOutcome("finished", "已处理。", context)

    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **_kwargs: IntentRecordingLoop(),
    )
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("第 1–5 小节加入高潮，先给我建议") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.sendMessage("允许") is True
    _wait_until(qapp, lambda: assistant.assistantStatus in {"idle", "error"})

    assert [intent for intent, _context in calls] == ["READ_MUSIC", "EDIT_MUSIC"]
    assert calls[0][1].authorized_bars == (BarRange(1, 5),)
    assert calls[1][1].authorized_bars == (BarRange(1, 5),)
    assert MusicPermission.WHOLE_PROJECT_WRITE not in calls[1][1].permissions


def test_advice_phrase_with_write_words_never_grants_write_tools(tmp_path: Path, qapp) -> None:
    """Break caught: “加入高潮，先给我建议” is classified as an edit before its advice qualifier."""
    contexts: list[object] = []

    class ContextRecordingLoop:
        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            contexts.append(context)
            return MusicAgentLoopOutcome("finished", "已给出建议。", context)

    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **_kwargs: ContextRecordingLoop(),
    )
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("在当前目标范围加入一个有层次的高潮，先给我建议") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert len(contexts) == 1
    assert MusicPermission.WRITE_NOTES not in contexts[0].permissions


def test_read_advice_with_successful_reads_is_not_reported_as_a_failed_edit(
    tmp_path: Path, qapp
) -> None:
    """Break caught: Completion Guard treats a legitimate READ_MUSIC answer as a write failure."""

    def read_advice(gateway: object, context: object) -> MusicAgentLoopOutcome:
        result = gateway.execute(
            "get_notes", {"start_bar": 1, "end_bar": 1}, context
        )
        assert result.success
        return MusicAgentLoopOutcome(
            "finished", "已读取旋律并给出建议。", context, tool_results=(result,)
        )

    assistant = _loop_assistant(tmp_path, _editor(), read_advice)

    assert assistant.sendMessage("分析一下当前旋律，先不要修改") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert assistant.lastTurnResult["intent"] == "READ_MUSIC"
    assert assistant.lastTurnResult["writeCount"] == 0
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ASSISTANT_MESSAGE
    assert all(
        entry.kind is not AssistantMessageKind.ERROR
        for entry in assistant.messages.entries
    )


def test_collaboration_finished_without_a_write_returns_a_structured_error(
    tmp_path: Path, qapp
) -> None:
    """Break caught: the zero-write completion path passes empty final text to AssistantTurnResult."""

    def analysis_only(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome("finished", "我分析了当前旋律。", context)

    editor = _editor()
    assistant = _loop_assistant(tmp_path, editor, analysis_only)
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("直接把当前选中的部分改得更梦幻，现在执行") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ERROR
    assert "没有完成实际" in assistant.messages.entries[-1].text
    assert assistant.lastTurnResult["status"] == "finished"
    assert assistant.lastTurnResult["finalText"]
    assert assistant.lastTurnResult["writeCount"] == 0


def test_advice_then_execute_runs_a_real_collaboration_write_in_the_same_scope(
    tmp_path: Path, qapp
) -> None:
    """Break caught: approval inherits the prose but never reaches the real editor write path."""
    calls: list[str] = []

    class ReadThenWriteLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(self, _instruction: str, context: object, **kwargs: object) -> MusicAgentLoopOutcome:
            intent = str(kwargs["intent"])
            calls.append(intent)
            if intent == "READ_MUSIC":
                result = self._gateway.execute(
                    "get_notes", {"start_bar": 1, "end_bar": 1}, context
                )
                assert result.success
                return MusicAgentLoopOutcome(
                    "finished", "已读取当前旋律并给出建议。", context, tool_results=(result,)
                )
            result = self._gateway.execute(
                "transpose_notes",
                {"note_ids": ["source"], "semitones": 1},
                context,
            )
            assert result.success
            return MusicAgentLoopOutcome(
                "finished", "已按方案修改。", context, tool_results=(result,)
            )

    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    assistant = MusicAssistantController(
        editor,
        gateway=gateway,
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: ReadThenWriteLoop(kwargs["gateway"]),
    )
    assistant.confirmCollaborationMode()
    before_revision = editor.projectRevision

    assert assistant.sendMessage("让当前范围更梦幻，先给我建议") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.sendMessage("可以，执行") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert calls == ["READ_MUSIC", "EDIT_MUSIC"]
    assert editor.state.active_notes[0].pitch == 61
    assert editor.projectRevision > before_revision
    assert editor._history.undo_depth == 1
    assert assistant.lastTurnResult["writeCount"] == 1
    editor.undo()
    assert editor.state.active_notes[0].pitch == 60


def test_chat_follow_up_context_is_read_only_and_bounded(tmp_path: Path) -> None:
    """Break caught: retaining task context accidentally grants a short chat write access."""
    assistant = _assistant(tmp_path)
    assistant._last_music_intent = "CREATE_MUSIC"

    context = assistant._create_execution_context("CHAT", "好了吗？")

    assert context.authorized_bars
    assert MusicPermission.READ_PROJECT_SUMMARY in context.permissions
    assert MusicPermission.READ_NOTES in context.permissions
    assert MusicPermission.READ_HARMONY in context.permissions
    assert MusicPermission.WRITE_NOTES not in context.permissions
    assert MusicPermission.WRITE_BPM not in context.permissions
    assert MusicPermission.WRITE_HARMONY not in context.permissions

    ordinary_chat = assistant._create_execution_context("CHAT", "可以告诉我和弦是什么")
    assert ordinary_chat.authorized_bars == ()
    assert ordinary_chat.permissions == frozenset()


def test_music_follow_up_reuses_the_previous_explicit_authorization_scope(tmp_path: Path) -> None:
    """Break caught: a short approval silently expands an explicit bar range to the whole project."""
    assistant = _assistant(tmp_path)
    first_context = assistant._create_execution_context(
        "CREATE_MUSIC", "在第 20–24 小节创作高潮"
    )
    assistant._remember_music_context(first_context, "CREATE_MUSIC")

    follow_up = assistant._create_execution_context("CREATE_MUSIC", "允许")

    assert first_context.authorized_bars == (BarRange(20, 24),)
    assert follow_up.authorized_bars == (BarRange(20, 24),)
    assert MusicPermission.WHOLE_PROJECT_READ not in follow_up.permissions
    assert MusicPermission.WHOLE_PROJECT_WRITE not in follow_up.permissions


def test_music_follow_up_reuses_previous_automatic_whole_project_authorization(tmp_path: Path) -> None:
    """Break caught: an automatic task loses its explicit whole-project decision on approval."""
    assistant = _assistant(tmp_path)
    first_context = assistant._create_execution_context("CREATE_MUSIC", "写一段旋律")
    assistant._remember_music_context(first_context, "CREATE_MUSIC")

    follow_up = assistant._create_execution_context("CREATE_MUSIC", "允许")

    assert first_context.authorized_bars == ()
    assert MusicPermission.WHOLE_PROJECT_READ in first_context.permissions
    assert MusicPermission.WHOLE_PROJECT_WRITE in first_context.permissions
    assert follow_up.authorized_bars == ()
    assert MusicPermission.WHOLE_PROJECT_READ in follow_up.permissions
    assert MusicPermission.WHOLE_PROJECT_WRITE in follow_up.permissions


def test_send_message_preserves_previous_scope_across_a_real_short_follow_up(
    tmp_path: Path, qapp
) -> None:
    """Break caught: the public send path forgets the previous explicit scope on approval."""
    contexts: list[object] = []

    def record_context(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        contexts.append(context)
        return MusicAgentLoopOutcome("finished", "已处理当前任务。", context)

    assistant = _loop_assistant(tmp_path, _editor(), record_context)

    assert assistant.sendMessage("在第 20–24 小节创作高潮") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.sendMessage("允许") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert len(contexts) == 2
    first, second = contexts
    assert first.authorized_bars == (BarRange(20, 24),)
    assert second.authorized_bars == (BarRange(20, 24),)
    assert MusicPermission.WHOLE_PROJECT_WRITE not in second.permissions


def test_cancelled_blocking_turn_discards_its_stale_proposal_and_returns_idle(tmp_path: Path, qapp) -> None:
    """Break caught: a cancelled worker completion can publish a proposal after the UI is idle."""
    editor = _editor()
    started, release, finished = Event(), Event(), Event()

    def delayed(gateway: object, context: object) -> MusicAgentLoopOutcome:
        started.set()
        assert release.wait(1)
        try:
            assert gateway.execute(
                "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
            ).success
            return MusicAgentLoopOutcome("proposal", "已准备好。", context)
        finally:
            finished.set()

    assistant = _loop_assistant(tmp_path, editor, delayed)
    before = editor.state

    assert assistant.sendMessage("升高选中音符") is True
    assert started.wait(1)
    assistant.cancelGeneration()
    release.set()
    assert finished.wait(1)
    _wait_until(qapp, lambda: not assistant._workers)
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert assistant.proposal["available"] is False
    assert editor.state == before
    assert [entry.kind for entry in assistant.messages.entries] == [AssistantMessageKind.USER_MESSAGE]


def test_user_undo_during_a_collaboration_turn_cancels_the_stale_generation(tmp_path: Path, qapp) -> None:
    """Break caught: a user edit or Undo lets a stale collaboration turn continue into an error."""
    editor = _editor()
    started, release, finished = Event(), Event(), Event()

    class DelayedCollaborationLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            started.set()
            assert release.wait(1)
            try:
                self._gateway.execute(
                    "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
                )
                return MusicAgentLoopOutcome("finished", "过期结果", context)
            finally:
                finished.set()

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: DelayedCollaborationLoop(kwargs["gateway"]),
    )
    assistant.confirmCollaborationMode()
    initial_state = editor.state

    assert assistant.sendMessage("将选中音符升高") is True
    assert started.wait(1)

    editor.setBpm(110)
    editor.undo()
    qapp.processEvents()

    assert assistant.assistantStatus == "idle"
    assert assistant.messages.entries[-1].text == "工程已发生变化，已取消本轮 AI 操作。"

    release.set()
    assert finished.wait(1)
    _wait_until(qapp, lambda: not assistant._workers)

    assert editor.state == initial_state
    assert assistant.assistantStatus == "idle"


def test_collaboration_waiting_for_a_read_only_turn_does_not_lock_user_history(
    tmp_path: Path, qapp
) -> None:
    """Break caught: merely waiting for a collaboration response must not open an editor gesture."""
    editor = _editor()
    started, release, finished = Event(), Event(), Event()

    class DelayedReadLoop:
        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            started.set()
            assert release.wait(1)
            finished.set()
            return MusicAgentLoopOutcome("finished", "已读取工程。", context)

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **_kwargs: DelayedReadLoop(),
    )
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("当前有几个音符？") is True
    assert started.wait(1)

    editor.setBpm(110)
    editor.undo()
    qapp.processEvents()

    assert editor.state.tempo.bpm == 120
    assert assistant.assistantStatus == "idle"
    assert assistant.messages.entries[-1].text == "工程已发生变化，已取消本轮 AI 操作。"

    release.set()
    assert finished.wait(1)
    _wait_until(qapp, lambda: not assistant._workers)


def test_one_turn_groups_tool_results_into_one_music_activity(
    tmp_path: Path, qapp
) -> None:
    """Break caught: one agent turn floods the transcript with repeated technical tool cards."""
    editor = _editor()

    def repeated_results(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome(
            "execution_error",
            "",
            context,
            tool_results=(
                MusicToolResult.success_result("read_project_summary", "summary"),
                MusicToolResult.success_result("read_notes", "notes"),
                MusicToolResult.success_result("read_harmony", "harmony"),
                MusicToolResult.failure("add_notes", "conflict", "stale"),
                MusicToolResult.failure("add_notes", "conflict", "stale"),
            ),
        )

    assistant = _loop_assistant(tmp_path, editor, repeated_results)

    assert assistant.sendMessage("整理工程") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    activities = [entry for entry in assistant.messages.entries if entry.kind is AssistantMessageKind.ACTIVITY]
    assert len(activities) == 1
    assert activities[0].title == "音乐处理"
    assert "已完成" not in activities[0].text
    assert "工程内容已经变化，请重新生成建议（2 项）" in activities[0].text


def test_suggestion_turn_presents_a_real_proposal_that_existing_undo_restores(tmp_path: Path, qapp) -> None:
    """Break caught: provider writes bypass the existing proposal Apply and one-step undo boundary."""
    editor = _editor()

    def transpose(gateway: object, context: object) -> MusicAgentLoopOutcome:
        assert gateway.execute(
            "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
        ).success
        return MusicAgentLoopOutcome("proposal", "我准备了一项升高建议。", context)

    assistant = _loop_assistant(tmp_path, editor, transpose)

    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)
    assert assistant.applyProposal()["success"] is True
    assert editor.state.active_notes[0].pitch == 62

    editor.undo()

    assert editor.state.active_notes[0].pitch == 60


def test_controller_exposes_structured_proposal_result_and_guards_provider_claims(
    tmp_path: Path, qapp
) -> None:
    """Break caught: a staged write is reported from model prose instead of real Proposal state."""
    editor = _editor()

    def stage_with_claim(gateway: object, context: object) -> MusicAgentLoopOutcome:
        result = gateway.execute(
            "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
        )
        assert result.success
        return MusicAgentLoopOutcome(
            "proposal",
            "我已经修改好了，也生成了试听音频并马上发送给你。",
            context,
            tool_results=(result,),
        )

    assistant = _loop_assistant(tmp_path, editor, stage_with_claim)

    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)

    turn = assistant.lastTurnResult
    assert turn["status"] == "proposal"
    assert turn["writeCount"] == 1
    assert turn["proposalStatus"] == "pending"
    assert turn["committed"] is False
    assert turn["proposalId"] == assistant.proposal["proposalId"]
    assert "试听音频并马上发送" not in turn["finalText"]
    assert any(
        entry.kind is AssistantMessageKind.ASSISTANT_MESSAGE
        and "试听音频并马上发送" not in entry.text
        for entry in assistant.messages.entries
    )


def test_proposal_follow_up_uses_local_state_without_a_second_provider_turn(
    tmp_path: Path, qapp
) -> None:
    """Break caught: “好了吗/试听” spends another model call despite a local Proposal already existing."""
    editor = _editor()
    calls = 0

    def stage(gateway: object, context: object) -> MusicAgentLoopOutcome:
        nonlocal calls
        calls += 1
        result = gateway.execute(
            "transpose_notes", {"note_ids": ["source"], "semitones": 1}, context
        )
        assert result.success
        return MusicAgentLoopOutcome("proposal", "建议已准备好。", context, tool_results=(result,))

    assistant = _loop_assistant(tmp_path, editor, stage)
    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)

    assert assistant.sendMessage("好了吗") is True
    assert assistant.sendMessage("试听") is True
    assert calls == 1
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ASSISTANT_MESSAGE
    assert "试听" in assistant.messages.entries[-1].text


def test_new_creation_request_with_audition_is_not_treated_as_local_follow_up(
    tmp_path: Path, qapp
) -> None:
    """A creation task may mention audition without becoming a local status query."""
    editor = _editor()
    calls = 0

    def create(gateway: object, context: object) -> MusicAgentLoopOutcome:
        nonlocal calls
        calls += 1
        result = gateway.execute(
            "add_notes",
            {"notes": [{"pitch": 72, "start_time": 1.0, "duration": 0.5, "velocity": 80}]},
            context,
        )
        assert result.success
        return MusicAgentLoopOutcome("proposal", "建议已准备好。", context, tool_results=(result,))

    assistant = _loop_assistant(tmp_path, editor, create)
    assert assistant.sendMessage("写一段旋律，完成后试听") is True

    _wait_until(qapp, lambda: assistant.proposal["available"] is True)
    assert calls == 1


def test_local_proposal_follow_up_reports_absent_applied_and_stale_states(tmp_path: Path) -> None:
    """Break caught: every short status question is answered as if an audio job existed."""
    editor = _editor()
    assistant = _assistant(tmp_path, editor)

    assert assistant.sendMessage("好了吗") is True
    assert "没有待试听" in assistant.messages.entries[-1].text

    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert assistant.gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 74, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assistant.presentProposal(context)
    assert assistant.applyProposal()["success"] is True
    applied_turn = assistant.lastTurnResult
    assert applied_turn["proposalStatus"] == "applied"
    assert applied_turn["committed"] is True
    assert applied_turn["revisionAfter"] > applied_turn["revisionBefore"]
    assert assistant.sendMessage("播放") is True
    assert "已应用到工程" in assistant.messages.entries[-1].text

    assistant.presentProposal(_staged_proposal(assistant))
    editor.setBpm(111)
    assert assistant.sendMessage("试听") is True
    assert "已过期" in assistant.messages.entries[-1].text


def test_empty_project_allows_a_bounded_initial_phrase_proposal_and_one_step_undo(
    tmp_path: Path, qapp
) -> None:
    """Break caught: an empty project authorizes only two seconds, rejecting a requested short composition."""
    editor = EditorController(ProjectState())
    observed_bars: list[tuple[BarRange, ...]] = []

    def create_phrase(gateway: object, context: object) -> MusicAgentLoopOutcome:
        observed_bars.append(context.authorized_bars)
        result = gateway.execute(
            "add_notes",
            {
                "notes": [
                    {"pitch": 60, "start_time": 0.0, "duration": 0.5, "velocity": 72},
                    {"pitch": 64, "start_time": 2.0, "duration": 0.5, "velocity": 76},
                    {"pitch": 67, "start_time": 4.0, "duration": 0.5, "velocity": 80},
                    {"pitch": 72, "start_time": 9.5, "duration": 0.5, "velocity": 84},
                ]
            },
            context,
        )
        assert result.success
        return MusicAgentLoopOutcome("proposal", "已准备好短旋律。", context, tool_results=(result,))

    assistant = _loop_assistant(tmp_path, editor, create_phrase)

    assert assistant.sendMessage("写一段10s的月光") is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)
    assert observed_bars == [()]
    assert editor.state.active_notes == ()

    assert assistant.applyProposal()["success"] is True
    assert len(editor.state.active_notes) == 4
    editor.undo()
    assert editor.state.active_notes == ()


def test_automatic_creation_scope_is_unlimited_at_every_project_tempo(tmp_path: Path) -> None:
    """Break caught: tempo or signature changes reinstate a hidden automatic creation cap."""
    editor = EditorController(ProjectState().with_tempo(TempoMap(90, 3, 4)))
    assistant = _assistant(tmp_path, editor)

    context = assistant._create_execution_context("CREATE_MUSIC", "写一段 10 秒的旋律")

    assert context.authorized_bars == ()
    assert MusicPermission.WHOLE_PROJECT_WRITE in context.permissions


def test_music_activity_reports_added_notes_not_read_call_count() -> None:
    """Break caught: read-only inspection is displayed as completed music work."""
    activity = MusicAssistantController._music_activity_message(
        (
            MusicToolResult.success_result("get_project_summary", "project summary retrieved"),
            MusicToolResult.success_result("get_notes", "retrieved 0 notes"),
            MusicToolResult.success_result("add_notes", "staged 32 added notes", added_notes=32),
        )
    )

    assert activity == "已新增 32 个音符。"


def test_nonempty_scope_uses_the_project_bar_duration_not_a_two_second_constant(tmp_path: Path) -> None:
    """Break caught: selected notes at 90 BPM map to the wrong authorized bar."""
    note = Note.create(id="slow", pitch=60, start_time=2.2, duration=0.5)
    editor = EditorController(
        ProjectState((note,), frozenset({note.id})).with_tempo(TempoMap(90, 3, 4))
    )
    assistant = _assistant(tmp_path, editor)

    context = assistant._create_execution_context("EDIT_MUSIC", "升高选中的音")

    assert context.authorized_bars == (BarRange(2, 2),)


def test_collaboration_turn_commits_one_undo_and_cancelled_turn_changes_nothing(tmp_path: Path, qapp) -> None:
    """Break caught: collaboration either creates a proposal or leaks edits from a cancelled turn."""
    editor = _editor()

    def collaboration_write(gateway: object, context: object) -> MusicAgentLoopOutcome:
        result = gateway.execute(
            "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
        )
        assert result.success
        assert gateway.finish_turn(context).success
        return MusicAgentLoopOutcome("finished", "已完成修改。", context, tool_results=(result,))

    assistant = _loop_assistant(tmp_path, editor, collaboration_write)
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")
    assert assistant.proposal["available"] is False
    assert editor.state.active_notes[0].pitch == 62
    editor.undo()
    assert editor.state.active_notes[0].pitch == 60


def test_text_only_model_chats_without_writes_or_tool_schemas(tmp_path: Path, qapp) -> None:
    """Break caught: selected catalog metadata permits a text-only model to receive write tools."""
    editor = _editor()
    observed_permissions: list[frozenset[MusicPermission]] = []

    def chat(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        observed_permissions.append(context.permissions)
        return MusicAgentLoopOutcome("finished", "试试 C 大调。", context)

    assistant = _loop_assistant(tmp_path, editor, chat)
    assistant.selectProvider("compatible")
    assistant.selectModel("text-only")
    before = editor.state

    assert assistant.sendMessage("这段感觉怎么样？") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert editor.state == before
    assert assistant.proposal["available"] is False
    assert observed_permissions == [
        frozenset(
            {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
            }
        )
    ]
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ASSISTANT_MESSAGE


def test_plain_chat_turn_has_no_music_permissions(tmp_path: Path, qapp) -> None:
    """Break caught: asking a theory question grants read access to the open project."""
    editor = _editor()
    observed_permissions: list[frozenset[MusicPermission]] = []

    def chat(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        observed_permissions.append(context.permissions)
        return MusicAgentLoopOutcome("finished", "和弦由多个音按一定关系构成。", context)

    assistant = _loop_assistant(tmp_path, editor, chat)

    assert assistant.sendMessage("什么是和弦？") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert observed_permissions == [frozenset()]


def test_controller_updates_one_assistant_message_while_text_is_streamed(tmp_path: Path, qapp) -> None:
    """Break caught: streaming UI either waits for the final turn or duplicates its final reply."""
    editor = _editor()

    class StreamingLoop:
        def __init__(self, on_text_delta: Callable[[str], None], **_kwargs: object) -> None:
            self._on_text_delta = on_text_delta

        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            self._on_text_delta("正在读取工程。")
            self._on_text_delta("已准备好。")
            return MusicAgentLoopOutcome("finished", "正在读取工程。已准备好。", context)

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: StreamingLoop(**kwargs),
    )

    assert assistant.sendMessage("给我一条进度") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    replies = [entry for entry in assistant.messages.entries if entry.kind is AssistantMessageKind.ASSISTANT_MESSAGE]
    assert len(replies) == 1
    assert replies[0].text == "正在读取工程。已准备好。"


def test_text_only_tool_request_is_blocked_before_a_provider_call(tmp_path: Path) -> None:
    """Break caught: a known chat-only model spends a call instead of explaining that creation is unavailable."""
    editor = _editor()

    def unavailable(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome("tools_unavailable", "", context)

    assistant = _loop_assistant(tmp_path, editor, unavailable)
    assistant.selectProvider("ollama")
    assistant.selectModel("llama3.2")
    before = editor.state

    assert assistant.sendMessage("请修改选中音符") is False

    assert editor.state == before
    assert assistant.proposal["available"] is False
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ERROR
    assert "仅支持聊天" in assistant.messages.entries[-1].text
    assert "MIDI 编辑可用" in assistant.messages.entries[-1].text


def test_collaboration_no_write_outcome_is_a_real_failure(tmp_path: Path, qapp) -> None:
    """Collaboration must not claim success when the provider made no write call."""
    editor = _editor()
    before = editor.state

    def no_write(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome("no_write", "好的，我已经修改好了。", context)

    assistant = _loop_assistant(tmp_path, editor, no_write)
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("再柔和一点") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    assert editor.state == before
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ERROR
    assert "没有完成实际" in assistant.messages.entries[-1].text


def test_unverified_model_capability_does_not_claim_a_connection_problem(tmp_path: Path, qapp) -> None:
    """Break caught: a catalog capability gap is presented as an API/network failure."""
    editor = _editor()

    def unavailable(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome("model_unavailable", "", context)

    assistant = _loop_assistant(tmp_path, editor, unavailable)

    assert assistant.sendMessage("写一段10秒的月光") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    error = assistant.messages.entries[-1]
    assert error.kind is AssistantMessageKind.ERROR
    assert "工具能力未验证" in error.text
    assert "检查连接" not in error.text


def test_successful_capability_verification_recovers_the_send_state(tmp_path: Path) -> None:
    """Break caught: a verified model remains unusable because a prior capability error locks Send."""

    class CapabilityProvider:
        def test_connection(self) -> bool:
            return True

        def verify_tool_capability(self):
            from ai_piano_studio.music_agent.capabilities import ModelCapabilities

            return ModelCapabilities(True, False, False, False, True)

    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: CapabilityProvider(),
    )
    settings.save(
        "deepseek",
        "",
        "https://api.deepseek.com/v1",
        "connection-key",
        "deepseek-v4-flash",
        False,
    )
    settings.set_dynamic_models("deepseek", ["deepseek-v4-flash"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True
    assistant._assistant_status = "error"

    result = assistant.testConnection("deepseek")

    assert result["code"] == "SUCCESS"
    assert assistant.currentCapabilities["canUseMusicTools"] is True
    assert assistant.assistantStatus == "idle"


def test_explicit_music_tool_verification_uses_the_same_safe_capability_probe(tmp_path: Path) -> None:
    """Break caught: the visible verification action does not update the selected model state."""

    class CapabilityProvider:
        def test_connection(self) -> bool:
            return True

        def verify_tool_capability(self):
            from ai_piano_studio.music_agent.capabilities import ModelCapabilities

            return ModelCapabilities(True, False, False, False, True)

    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: CapabilityProvider(),
    )
    settings.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4", False)
    settings.set_dynamic_models("deepseek", ["deepseek-v4"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True

    result = assistant.verifyMusicTools()

    assert result["code"] == "SUCCESS"
    assert assistant.currentCapabilities["canUseMusicTools"] is True


def test_current_model_status_distinguishes_unverified_ready_and_chat_only(tmp_path: Path) -> None:
    """Break caught: unknown and unsupported models are both shown to people as chat-only."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    settings.set_dynamic_models("deepseek", ["deepseek-v4-pro"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)

    assert assistant.selectProvider("deepseek") is True
    assert assistant.selectModel("deepseek-v4-pro") is True
    assert assistant.currentModelStatus == {
        "state": "unverified",
        "title": "创作能力待验证",
        "detail": "尚未验证该模型的连接与音乐工具能力。",
        "actionLabel": "验证创作能力",
        "canCreate": False,
        "canVerify": True,
    }

    settings.record_capability_verification(
        "deepseek", "deepseek-v4-pro", ModelCapabilities(True, False, False, False, True)
    )

    assert assistant.currentModelStatus["state"] == "ready"
    assert assistant.currentModelStatus["title"] == "MIDI 编辑可用"
    assert "不能生成或发送音频文件" in assistant.currentModelStatus["detail"]
    assert assistant.currentModelStatus["canCreate"] is True
    assert assistant.currentModelStatus["canVerify"] is False

    assert assistant.selectProvider("ollama") is True
    assert assistant.currentModelStatus == {
        "state": "chat_only",
        "title": "仅聊天",
        "detail": "该模型已确认不支持音乐创作工具，仍可聊天。",
        "actionLabel": "不支持创作",
        "canCreate": False,
        "canVerify": False,
    }


def test_explicit_verification_names_a_confirmed_chat_only_model(tmp_path: Path) -> None:
    """Break caught: an unsupported result is persisted correctly but reported as supported."""

    class ChatOnlyCapabilityProvider:
        def verify_tool_capability(self) -> ModelCapabilities:
            return ModelCapabilities(False, False, False, False, True)

    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: ChatOnlyCapabilityProvider(),
    )
    settings.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4-pro", False)
    settings.set_dynamic_models("deepseek", ["deepseek-v4-pro"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True

    result = assistant.verifyMusicTools()

    assert result["code"] == "SUCCESS"
    assert result["message"] == "已确认该模型不支持音乐创作工具，仍可聊天。"
    assert assistant.currentModelStatus["state"] == "chat_only"


def test_explicit_async_capability_verification_reports_progress_and_rejects_duplicate_clicks(
    tmp_path: Path, qapp
) -> None:
    """Break caught: checking model capability blocks the UI or gives no visible in-progress state."""

    class BlockingCapabilityProvider:
        def __init__(self) -> None:
            self.started = Event()
            self.release = Event()
            self.probe_calls = 0

        def verify_tool_capability(self) -> ModelCapabilities:
            self.probe_calls += 1
            self.started.set()
            assert self.release.wait(1)
            return ModelCapabilities(True, False, False, False, True)

    provider = BlockingCapabilityProvider()
    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: provider,
    )
    settings.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4-pro", False)
    settings.set_dynamic_models("deepseek", ["deepseek-v4-pro"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True

    assert assistant.verifyMusicToolsAsync() is True
    assert provider.started.wait(1)
    assert assistant.currentModelStatus["state"] == "verifying"
    assert assistant.currentModelStatus["actionLabel"] == "正在验证…"
    assert assistant.verifyMusicToolsAsync() is False

    provider.release.set()
    _wait_until(qapp, lambda: assistant.currentModelStatus["state"] == "ready")

    assert provider.probe_calls == 1
    assert assistant.currentModelStatus["canCreate"] is True


def test_async_capability_verification_failure_remains_retryable_chat(tmp_path: Path, qapp) -> None:
    """Break caught: a network failure is falsely presented as proof that the model is chat-only."""

    class FailingCapabilityProvider:
        def verify_tool_capability(self) -> ModelCapabilities:
            raise ProviderError("network request failed")

    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: FailingCapabilityProvider(),
    )
    settings.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4-flash", False)
    settings.set_dynamic_models("deepseek", ["deepseek-v4-flash"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True

    assert assistant.verifyMusicToolsAsync() is True
    _wait_until(qapp, lambda: assistant.currentModelStatus["state"] == "verification_failed")

    assert assistant.currentModelStatus == {
        "state": "verification_failed",
        "title": "创作验证失败",
        "detail": "无法连接到服务，请检查网络、Base URL 或本机服务。",
        "actionLabel": "重新验证",
        "canCreate": False,
        "canVerify": True,
    }


def test_successful_connection_test_clears_stale_capability_failure(tmp_path: Path, qapp) -> None:
    """Break caught: a later successful verification still leaves the selected model marked as failed."""

    class FlakyCapabilityProvider:
        def __init__(self) -> None:
            self.attempts = 0

        def test_connection(self) -> bool:
            return True

        def verify_tool_capability(self) -> ModelCapabilities:
            self.attempts += 1
            if self.attempts == 1:
                raise ProviderError("network request failed")
            return ModelCapabilities(True, False, False, False, True)

    provider = FlakyCapabilityProvider()
    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: provider,
    )
    settings.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4-pro", False)
    settings.set_dynamic_models("deepseek", ["deepseek-v4-pro"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True
    assert assistant.verifyMusicToolsAsync() is True
    _wait_until(qapp, lambda: assistant.currentModelStatus["state"] == "verification_failed")

    assistant.testConnection("deepseek")

    assert assistant.currentModelStatus["state"] == "ready"


def test_music_tool_verification_probes_the_currently_selected_catalog_model(
    tmp_path: Path, monkeypatch
) -> None:
    """Break caught: validating Flash/Pro silently probes the saved default model instead."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    settings.save(
        "deepseek",
        "",
        "https://api.deepseek.com/v1",
        "connection-key",
        "deepseek-chat",
        False,
    )
    settings.set_dynamic_models("deepseek", ["deepseek-v4-flash"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True
    assert assistant.selectModel("deepseek-v4-flash") is True
    calls: list[tuple[str, str | None]] = []

    def record_music_tool_verification(provider_id: str, model_id: str):
        calls.append((provider_id, model_id))
        from ai_piano_studio.music_agent.provider_settings import ConnectionResult, ConnectionResultCode

        return ConnectionResult(ConnectionResultCode.SUCCESS, "verified")

    monkeypatch.setattr(
        settings,
        "verify_music_tools",
        record_music_tool_verification,
        raising=False,
    )

    assistant.verifyMusicTools()

    assert calls == [("deepseek", "deepseek-v4-flash")]


def test_music_generation_binds_provider_transport_to_the_selected_catalog_model(
    tmp_path: Path, monkeypatch
) -> None:
    """Break caught: creation uses the saved default after the user selects Flash/Pro."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    settings.save(
        "deepseek",
        "",
        "https://api.deepseek.com/v1",
        "connection-key",
        "deepseek-chat",
        False,
    )
    settings.set_dynamic_models("deepseek", ["deepseek-v4-pro"])
    assistant = MusicAssistantController(_editor(), provider_settings=settings)
    assert assistant.selectProvider("deepseek") is True
    assert assistant.selectModel("deepseek-v4-pro") is True
    calls: list[tuple[str, str | None]] = []

    def create_recording_provider(provider_id: str, *, model_id: str | None = None):
        calls.append((provider_id, model_id))
        return object()

    monkeypatch.setattr(settings, "create_provider", create_recording_provider)

    assistant._create_agent_loop(1, Event())

    assert calls == [("deepseek", "deepseek-v4-pro")]


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("validation_error", "音乐参数校验未通过，本次修改未应用。"),
        ("permission_denied", "这次修改超出了当前允许的音乐范围。"),
        ("limit_exceeded", "本次音乐操作达到了已设置的操作上限，请调整操作设置后重试。"),
        ("conflict", "工程内容已经变化，请重新生成建议。"),
        ("execution_error", "音乐工具执行失败，请重试。"),
    ],
)
def test_mixed_tool_and_provider_failures_show_both_error_categories(
    tmp_path: Path, qapp, error_code: str, expected: str
) -> None:
    """Break caught: a later provider failure hides an earlier categorized Tool failure."""
    editor = _editor()

    def tool_failure(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome(
            "provider_error",
            "",
            context,
            tool_results=(MusicToolResult.failure("add_notes", error_code, "safe failure"),),
        )

    assistant = _loop_assistant(tmp_path, editor, tool_failure)

    assert assistant.sendMessage("写一段音乐") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    messages = assistant.messages.entries
    assert messages[-2].kind is AssistantMessageKind.ACTIVITY
    assert messages[-2].title == "音乐处理"
    assert messages[-2].text == expected
    assert messages[-1].kind is AssistantMessageKind.ERROR
    assert "检查连接" in messages[-1].text


def test_terminal_music_range_failure_does_not_claim_a_connection_problem(
    tmp_path: Path, qapp
) -> None:
    """Break caught: an out-of-range tool call is followed by a misleading connection error."""
    editor = _editor()

    def out_of_range(_gateway: object, context: object) -> MusicAgentLoopOutcome:
        return MusicAgentLoopOutcome(
            "permission_denied",
            "",
            context,
            tool_results=(
                MusicToolResult.failure("add_notes", "permission_denied", "outside authorized bars"),
            ),
        )

    assistant = _loop_assistant(tmp_path, editor, out_of_range)

    assert assistant.sendMessage("写一段音乐") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ACTIVITY
    assert assistant.messages.entries[-1].text == "这次修改超出了当前允许的音乐范围。"
    assert all("检查连接" not in message.text for message in assistant.messages.entries)


def test_worker_dispatches_registered_gateway_writes_to_the_controller_thread(tmp_path: Path, qapp) -> None:
    """Break caught: a provider worker directly invokes Gateway and Editor-bound write code."""
    editor = _editor()
    main_thread = get_ident()
    execution_threads: list[int] = []

    class GatewayWriteLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            execution_threads.append(get_ident())
            assert self._gateway.execute(
                "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
            ).success
            return MusicAgentLoopOutcome("proposal", "已准备好。", context)

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: GatewayWriteLoop(kwargs["gateway"]),
    )
    gateway_threads: list[int] = []
    original_execute = assistant.gateway.execute

    def record_execute(*args: object, **kwargs: object):
        gateway_threads.append(get_ident())
        return original_execute(*args, **kwargs)

    assistant.gateway.execute = record_execute  # type: ignore[method-assign]

    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: assistant.proposal["available"] is True)

    assert execution_threads != [main_thread]
    assert gateway_threads == [main_thread]


def test_shutdown_rejects_gateway_work_after_a_provider_ignores_cancellation(
    tmp_path: Path, qapp
) -> None:
    """Break caught: a blocked provider can call Gateway after controller shutdown."""
    editor = _editor()
    started, release, finished = Event(), Event(), Event()
    gateway_calls: list[int] = []

    class BlockingLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            started.set()
            assert release.wait(1)
            try:
                self._gateway.execute(
                    "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
                )
                return MusicAgentLoopOutcome("proposal", "过期结果", context)
            finally:
                finished.set()

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: BlockingLoop(kwargs["gateway"]),
    )
    original_execute = assistant.gateway.execute

    def record_execute(*args: object, **kwargs: object):
        gateway_calls.append(get_ident())
        return original_execute(*args, **kwargs)

    assistant.gateway.execute = record_execute  # type: ignore[method-assign]

    assert assistant.sendMessage("升高选中音符") is True
    assert started.wait(1)
    assistant.shutdown()
    release.set()
    assert finished.wait(1)
    _wait_until(qapp, lambda: not assistant._workers)
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert gateway_calls == []
    assert editor.state.active_notes[0].pitch == 60


def test_cancellation_during_a_registered_collaboration_write_rolls_back_before_commit(
    tmp_path: Path, qapp
) -> None:
    """Break caught: cancel racing a registered collaboration preview can leave or commit its edit."""
    editor = _editor()
    write_entered, release_write = Event(), Event()
    preview_threads: list[int] = []
    main_thread = get_ident()
    original_preview = editor.preview_agent_state

    def pause_after_preview(*args: object, **kwargs: object) -> None:
        original_preview(*args, **kwargs)
        preview_threads.append(get_ident())
        write_entered.set()
        assert release_write.wait(1)

    editor.preview_agent_state = pause_after_preview  # type: ignore[method-assign]

    class CollaborationLoop:
        def __init__(self, gateway: object) -> None:
            self._gateway = gateway

        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            assert self._gateway.execute(
                "transpose_notes", {"note_ids": ["source"], "semitones": 2}, context
            ).success
            assert self._gateway.finish_turn(context).success
            return MusicAgentLoopOutcome("finished", "已完成修改。", context)

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: CollaborationLoop(kwargs["gateway"]),
    )
    assistant.confirmCollaborationMode()

    def cancel_after_write_starts() -> None:
        assert write_entered.wait(1)
        assistant.cancelGeneration()
        release_write.set()

    cancellation_thread = Thread(target=cancel_after_write_starts)
    cancellation_thread.start()
    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: not cancellation_thread.is_alive())
    cancellation_thread.join()
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert preview_threads == [main_thread]
    assert editor.state.active_notes[0].pitch == 60
    assert assistant.proposal["available"] is False


def test_select_model_rejects_an_id_not_in_the_current_provider_catalog(tmp_path: Path) -> None:
    """Break caught: an arbitrary or removed model id can become the active provider model."""
    assistant = _assistant(tmp_path)
    initial = assistant.currentModelId

    assert assistant.selectModel("not-in-catalog") is False
    assert assistant.currentModelId == initial


def test_select_provider_keeps_a_configured_manual_model_missing_from_the_catalog(tmp_path: Path) -> None:
    """Break caught: a working manual model ID disappears merely because /models omits it."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    settings.save(
        "compatible",
        "Bridge",
        "https://bridge.example.test/v1",
        "bridge-key",
        "stale-model",
        True,
    )
    settings.set_dynamic_models("compatible", ["bridge-live"])
    editor = _editor()
    assistant = MusicAssistantController(editor, gateway=MusicAgentToolGateway(editor), provider_settings=settings)

    assert assistant.selectProvider("compatible") is True

    assert assistant.currentModelId == "stale-model"
    assert [item["id"] for item in assistant.modelCatalog] == ["bridge-live", "stale-model"]


def test_successful_save_keeps_an_undiscovered_manual_model_selectable(tmp_path: Path) -> None:
    """Break caught: saving a custom endpoint silently replaces the manual model ID."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    settings.set_dynamic_models("compatible", ["bridge-live"])
    editor = _editor()
    assistant = MusicAssistantController(editor, gateway=MusicAgentToolGateway(editor), provider_settings=settings)

    result = assistant.saveProviderSettings(
        "compatible",
        "Bridge",
        "https://bridge.example.test/v1",
        "bridge-key",
        "stale-model",
        True,
    )

    assert result["success"] is True
    assert assistant.currentModelId == "stale-model"
    # A new endpoint/key invalidates the previous endpoint's synced catalog;
    # the explicitly typed model must still remain selectable.
    assert [item["id"] for item in assistant.modelCatalog] == ["stale-model"]


def test_manual_model_without_catalog_entry_remains_selectable(tmp_path: Path) -> None:
    """Break caught: custom endpoints require /models even though they accept a manual model ID."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    editor = _editor()
    assistant = MusicAssistantController(editor, gateway=MusicAgentToolGateway(editor), provider_settings=settings)

    result = assistant.saveProviderSettings(
        "compatible",
        "Bridge",
        "https://bridge.example.test/v1",
        "bridge-key",
        "stale-model",
        True,
    )

    assert result["success"] is True
    assert assistant.currentModelId == "stale-model"
    assert [item["id"] for item in assistant.modelCatalog] == ["stale-model"]


def test_dynamic_model_catalog_keeps_verified_tool_models_visible(tmp_path: Path) -> None:
    """Break caught: refreshing an ID-only catalog hides the known Tool-capable model."""
    settings = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    settings.set_dynamic_models("deepseek", ["deepseek-v4-flash"])

    catalog = settings.model_catalog("deepseek")
    by_id = {item["id"]: item for item in catalog}

    assert {item["id"] for item in catalog} == {
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-v4-flash",
    }
    assert by_id["deepseek-chat"]["capabilities"]["supportsTools"] is True
    assert by_id["deepseek-v4-flash"]["capabilities"]["supportsTools"] is False
    assert by_id["deepseek-v4-flash"]["label"] == "deepseek-v4-flash"


def test_controller_starts_in_suggestion_and_requires_opt_in_for_collaboration(tmp_path: Path) -> None:
    editor = _editor()
    assistant = _assistant(tmp_path, editor)

    assert assistant.mode == "suggestion"
    assistant.requestCollaborationMode()
    assert assistant.mode == "suggestion"
    assistant.confirmCollaborationMode()

    assert assistant.mode == "collaboration"


def test_project_replacement_resets_collaboration_to_suggestion(tmp_path: Path) -> None:
    editor = _editor()
    assistant = _assistant(tmp_path, editor)
    assistant.confirmCollaborationMode()

    editor.newProject()

    assert assistant.mode == "suggestion"
    assert assistant.messages.entries == (), "Switch notices no longer occupy a new project chat"


def test_message_model_maps_missing_provider_configuration_to_a_friendly_error(tmp_path: Path) -> None:
    """Break caught: an unconfigured provider starts a turn or exposes its internal validation text."""
    assistant = _assistant(tmp_path)

    accepted = assistant.sendMessage("帮我把结尾更柔和")
    entries = assistant.messages.entries

    assert accepted is False
    assert [entry.kind for entry in entries] == [
        AssistantMessageKind.USER_MESSAGE,
        AssistantMessageKind.ERROR,
    ]
    assert "API Key" not in entries[-1].text
    assert assistant.assistantStatus == "error"


def test_controller_projects_a_real_phase_four_proposal_and_delegates_discard(tmp_path: Path) -> None:
    assistant = _assistant(tmp_path)
    context = _staged_proposal(assistant)

    proposal = assistant.presentProposal(context)
    result = assistant.discardProposal()

    assert proposal["status"] == "pending"
    assert proposal["notesAdded"] == 1
    assert result["success"] is True
    assert assistant.proposal["status"] == "discarded"
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.SYSTEM_NOTICE


def test_controller_exposes_snapshot_derived_human_readable_proposal_inspector(
    tmp_path: Path,
) -> None:
    """Break caught: QML has to reconstruct musical meaning from raw UUID fields."""
    assistant = _assistant(tmp_path)

    assistant.presentProposal(_staged_proposal(assistant))

    changes = assistant.proposalChanges
    inspector = changes["inspector"]
    assert inspector["available"] is True
    assert inspector["summary"]["notesAdded"] == 1
    assert inspector["summary"]["pitchChanged"] == 0
    assert inspector["summary"]["velocityChanged"] == 0
    assert inspector["scope"]["label"] == "第 2 小节"
    assert inspector["intent"]["label"] == "按当前请求"
    assert inspector["qualityWarning"] == ""
    assert inspector["details"]["added"][0]["noteName"] == "C5"
    assert "noteId" not in inspector["details"]["added"][0]
    assert inspector["details"]["added"][0]["technical"]["noteId"]


def test_controller_can_audition_the_frozen_original_proposal_snapshot(
    tmp_path: Path,
) -> None:
    """Break caught: Proposal Card offers only the staged version for comparison."""
    assistant = _assistant(tmp_path)
    assistant.presentProposal(_staged_proposal(assistant))

    result = assistant.auditionOriginalProposal()

    assert result["success"] is False
    assert result["message"]


def test_controller_exposes_proposal_overlay_filter_and_location_boundary(
    tmp_path: Path,
) -> None:
    """Break caught: the editor cannot show or locate the pending frozen change set."""
    assistant = _assistant(tmp_path)
    assistant.presentProposal(_staged_proposal(assistant))
    detail = assistant.proposalChanges["inspector"]["details"]["added"][0]
    note_id = detail["technical"]["noteId"]
    located: list[dict[str, object]] = []

    def on_located(note_id: str, start_beats: float, pitch: int, kind: str) -> None:
        located.append(
            {"noteId": note_id, "kind": kind, "startBeats": start_beats, "pitch": pitch}
        )

    assistant.proposalNoteLocateRequested.connect(on_located)

    assert assistant.proposalOnlyChanges is False
    assistant.setProposalOnlyChanges(True)
    assert assistant.proposalOnlyChanges is True

    assistant.locateProposalNote(note_id)

    assert located == [
        {
            "noteId": note_id,
            "kind": "added",
            "startBeats": detail["afterNote"]["startBeats"],
            "pitch": detail["afterNote"]["pitch"],
        }
    ]


def test_controller_marks_real_proposal_stale_after_editor_change(tmp_path: Path) -> None:
    editor = _editor()
    assistant = _assistant(tmp_path, editor)
    assistant.presentProposal(_staged_proposal(assistant))

    editor.setBpm(110)

    assert assistant.proposal["status"] == "stale"
    assert assistant.proposal["statusText"] == "工程已发生变化，请重新生成建议。"


def test_model_capabilities_and_reasoning_are_catalog_driven(tmp_path: Path) -> None:
    assistant = _assistant(tmp_path)

    assistant.selectProvider("openai")
    assistant.selectModel("o3")
    assistant.selectReasoning("standard")

    assert {item["id"] for item in assistant.providerCatalog} == {
        "openai", "deepseek", "anthropic", "gemini", "qwen", "kimi", "compatible", "ollama", "codex",
        "glm", "doubao", "minimax", "openrouter", "kimi-code", "glm-coding", "qwen-token", "minimax-token", "volcengine-plan"
    }
    assert any(item["id"] == "o3" for item in assistant.modelCatalog)
    assert assistant.currentCapabilities["supportsTools"] is True
    assert assistant.providerReasoningValue == "medium"
    assert any(item["id"] == "standard" and item["enabled"] for item in assistant.reasoningCatalog)


class _CatalogProvider:
    def list_models(self) -> list[str]:
        return ["dynamic-model"]

    def get_capabilities(self, model_id: str):
        raise AssertionError("not needed by this test double")

    def stream_chat(self, messages, tools, reasoning, attachments):
        raise AssertionError("not available before Phase 8")

    def test_connection(self) -> bool:
        return True

    def normalize_error(self, error):
        raise AssertionError("not needed by this test double")


def test_controller_can_refresh_the_selected_provider_model_catalog(tmp_path: Path) -> None:
    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path),
            credentials=MemoryCredentialStore(),
            provider_factory=lambda _provider_id: _CatalogProvider(),
        ),
    )

    catalog = assistant.refreshModelCatalog()

    assert [item["id"] for item in catalog] == ["dynamic-model", "gpt-4o-mini", "gpt-4o", "o3"]


def test_model_catalog_refresh_runs_off_the_controller_thread(qapp, qtbot, tmp_path: Path) -> None:
    """Break caught: refreshing a remote model catalog blocks the Qt event loop."""

    class BlockingCatalogProvider(_CatalogProvider):
        def __init__(self) -> None:
            self.started = Event()
            self.release = Event()

        def list_models(self) -> list[str]:
            self.started.set()
            assert self.release.wait(timeout=1)
            return ["dynamic-model"]

    provider = BlockingCatalogProvider()
    editor = _editor()
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path),
            credentials=MemoryCredentialStore(),
            provider_factory=lambda _provider_id: provider,
        ),
    )

    assert assistant.refreshModelCatalogAsync() is True
    assert provider.started.wait(timeout=0.2)
    assert assistant.catalogRefreshing is True

    provider.release.set()
    qtbot.waitUntil(lambda: assistant.catalogRefreshing is False, timeout=1000)

    assert [item["id"] for item in assistant.modelCatalog] == [
        "dynamic-model",
        "gpt-4o-mini",
        "gpt-4o",
        "o3",
    ]


class _SerializedLoopProvider:
    """Local scripted provider that drives the real MusicAgentLoop worker path."""

    TOOLS = ModelCapabilities(True, False, False, False, True)

    def __init__(self, scripts: list[tuple[ProviderEvent, ...]]) -> None:
        self._scripts = list(scripts)
        self.requests: list[list[dict[str, object]]] = []
        self.tool_schemas: list[list[dict[str, object]]] = []

    def stream_events(
        self,
        messages,
        tools,
        _reasoning,
        _attachments,
    ):
        self.requests.append(list(messages))
        self.tool_schemas.append(list(tools))
        if not self._scripts:
            yield ProviderEvent(text_delta="", finish_reason="stop")
            return
        yield from self._scripts.pop(0)

    def normalize_error(self, error: BaseException) -> ProviderError:
        return ProviderError("provider failed")

    def list_models(self) -> list[str]:
        return ["scripted-model"]

    def get_capabilities(self, _model_id: str) -> ModelCapabilities:
        return self.TOOLS


def _serialized_loop_assistant(
    tmp_path: Path,
    editor: EditorController,
    provider: _SerializedLoopProvider,
    **assistant_options,
) -> MusicAssistantController:
    settings = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: provider,
    )
    settings.save(
        "compatible",
        "Scripted",
        "https://scripted.example.test/v1",
        "scripted-key",
        "scripted-model",
        True,
    )
    settings.record_capability_verification("compatible", "scripted-model", provider.TOOLS)
    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=settings,
        **assistant_options,
    )
    assert assistant.selectProvider("compatible") is True
    assert assistant.selectModel("scripted-model") is True
    return assistant


def test_serialized_controller_collaboration_commits_one_real_write(tmp_path: Path, qapp) -> None:
    """The worker MusicAgentLoop -> serialized gateway path commits once with a revision change."""
    editor = _editor()
    provider = _SerializedLoopProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "transpose-1",
                            "transpose_notes",
                            '{"note_ids":["source"],"semitones":2}',
                        ),
                    )
                ),
            )
        ]
    )
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assistant.confirmCollaborationMode()
    before = editor.state
    before_revision = editor.projectRevision

    assert assistant.sendMessage("升高选中音符") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert assistant.proposal["available"] is False
    assert editor.state.active_notes[0].pitch == 62
    assert editor.state.active_notes[0].pitch != before.active_notes[0].pitch
    assert editor._history.undo_depth == 1
    assert editor.projectRevision != before_revision
    assert provider.requests
    assert all(
        entry.kind is not AssistantMessageKind.ERROR for entry in assistant.messages.entries
    )


def test_serialized_controller_collaboration_reads_then_writes_without_losing_schemas(
    tmp_path: Path, qapp
) -> None:
    """Break caught: the real controller path drops WRITE schemas after a read continuation."""
    editor = _editor()
    provider = _SerializedLoopProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "read-1",
                            "get_notes",
                            '{"start_bar":1,"end_bar":1}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "write-1",
                            "update_notes",
                            '{"updates":[{"id":"source","pitch":62}]}',
                        ),
                    )
                ),
            ),
        ]
    )
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assistant.confirmCollaborationMode()
    before_revision = editor.projectRevision

    assert assistant.sendMessage("直接把当前选中的部分改得更梦幻，现在执行") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    from ai_piano_studio.music_agent.memory_tool import with_task_history
    from ai_piano_studio.music_agent.audition_tools import with_audition_analysis
    expected = with_audition_analysis(with_task_history(MusicAgentToolGateway(editor).registry)).openai_schemas()
    assert provider.tool_schemas == [expected, expected, expected]
    assert editor.state.active_notes[0].pitch == 62
    assert editor.projectRevision > before_revision
    assert editor._history.undo_depth == 1
    assert assistant.lastTurnResult["intent"] == "EDIT_MUSIC"
    assert assistant.lastTurnResult["readCount"] == 1
    assert assistant.lastTurnResult["writeCount"] == 1


def test_serialized_controller_exposes_validation_recovery_metrics(
    tmp_path: Path, qapp
) -> None:
    """The UI-facing turn result records the real bounded recovery path."""
    editor = _editor()
    provider = _SerializedLoopProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid-write",
                            "update_notes",
                            '{"updates":[{"id":"source","pitch":130}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "corrected-write",
                            "update_notes",
                            '{"updates":[{"id":"source","pitch":62}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成。", finish_reason="stop"),),
        ]
    )
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assistant.confirmCollaborationMode()

    assert assistant.sendMessage("把当前音符改高一点，现在执行") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "idle")

    assert editor.state.active_notes[0].pitch == 62
    assert editor._history.undo_depth == 1
    turn = assistant.lastTurnResult
    assert turn["providerCalls"] == 3
    assert turn["validationModelRepairs"] == 1
    assert turn["localRepairs"] == 0


def test_serialized_controller_collaboration_no_write_is_an_error(tmp_path: Path, qapp) -> None:
    """A text-only collaboration edit through the real loop is reported as a real failure."""
    editor = _editor()
    provider = _SerializedLoopProvider(
        [
            (ProviderEvent(text_delta="好的，我已经修改好了。", finish_reason="stop"),),
            (ProviderEvent(text_delta="我再调整一下。", finish_reason="stop"),),
            (ProviderEvent(text_delta="完成了。", finish_reason="stop"),),
        ]
    )
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assistant.confirmCollaborationMode()
    before = editor.state
    before_revision = editor.projectRevision

    assert assistant.sendMessage("再柔和一点") is True
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")

    assert editor.state == before
    assert editor.projectRevision == before_revision
    assert editor._history.undo_depth == 0
    assert assistant.messages.entries[-1].kind is AssistantMessageKind.ERROR
    assert "没有完成实际" in assistant.messages.entries[-1].text
    # A no-write response is still a failure, without two forced model retries.
    assert len(provider.requests) == 1


def test_validation_repair_status_is_busy_and_clears_after_terminal_failure(
    tmp_path: Path, qapp
) -> None:
    """A real bounded repair must be visible while the worker is still alive."""
    editor = _editor()
    repair_started = Event()
    release = Event()

    class RepairingLoop:
        def __init__(self, **kwargs: object) -> None:
            self._on_validation_repair = kwargs["on_validation_repair"]

        def run(self, _instruction: str, context: object, **_kwargs: object) -> MusicAgentLoopOutcome:
            callback = self._on_validation_repair
            assert callable(callback)
            callback(3)
            repair_started.set()
            assert release.wait(1)
            return MusicAgentLoopOutcome(
                "validation_error",
                "",
                context,
                tool_results=(
                    MusicToolResult.failure(
                        "add_notes",
                        "validation_error",
                        "three invalid notes",
                        data={
                            "invalid_items": [
                                {
                                    "item_index": index,
                                    "field": "duration",
                                    "reason": "invalid_duration",
                                }
                                for index in range(3)
                            ]
                        },
                    ),
                ),
                debug_snapshot={
                    "provider_calls": 2,
                    "validation_model_repairs": 1,
                    "local_repairs": 0,
                },
            )

    assistant = MusicAssistantController(
        editor,
        gateway=MusicAgentToolGateway(editor),
        provider_settings=ProviderSettingsService(
            settings=_settings(tmp_path), credentials=MemoryCredentialStore()
        ),
        agent_loop_factory=lambda **kwargs: RepairingLoop(**kwargs),
    )
    assert assistant.sendMessage("整体改得更有层次") is True
    assert repair_started.wait(1)
    _wait_until(qapp, lambda: assistant.assistantStatus == "repairing")
    assert assistant.isBusy is True
    assert assistant.assistantProgressText == "正在修正 3 项音乐参数…"
    release.set()
    _wait_until(qapp, lambda: assistant.assistantStatus == "error")
    assert assistant.isBusy is False
    assert assistant.assistantProgressText == ""
    assert any(
        entry.kind is AssistantMessageKind.ACTIVITY and "3 项音乐参数" in entry.text
        for entry in assistant.messages.entries
    )


@pytest.mark.parametrize('instruction', ['帮我弄一下', '帮我优化一下吧', '请调整一下'])
def test_unspecified_action_is_a_conversation_before_edit_permission(instruction):
    assert MusicAssistantController._classify_intent(instruction) == 'CHAT'


def test_partner_clarifies_vague_request_then_follows_explicit_answer(tmp_path, qapp):
    question = "可以一起调整。你更想先改善旋律，还是伴奏？"
    provider = _SerializedLoopProvider([
        (ProviderEvent(text_delta=question, finish_reason="stop"),),
        (ProviderEvent(tool_calls=(ProviderToolCall("gentle", "update_notes",
            '{"updates":[{"id":"source","velocity":55}]}'),)),),
        (ProviderEvent(text_delta="我把这段旋律的力度放轻了，先试听看看。", finish_reason="stop"),),
    ])
    editor = _editor()
    before = editor.state.content
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assert assistant.sendMessage("帮我弄一下")
    _wait_until(qapp, lambda: not assistant.isBusy)
    assert not provider.tool_schemas[0]
    assert assistant.messages.entries[-1].text == question
    assert editor.state.content == before and not assistant.proposal['available']
    assert assistant.sendMessage("把当前旋律力度调为55，先试听再决定")
    _wait_until(qapp, lambda: not assistant.isBusy)
    assert any(m.get('content') == question for m in provider.requests[1])
    assert assistant.proposal['available'], assistant.lastTurnResult
    assert editor.state.content == before  # Only a proposal, never silently applied.


@pytest.mark.parametrize(('instruction', 'expected'), [
    ('先聊聊它的编配思路，不用生成', 'CHAT'),
    ('先讨论一下旋律怎么写', 'CHAT'),
    ('先聊聊当前旋律，不要修改', 'READ_MUSIC'),
    ('讨论完了，现在把当前旋律力度调为55', 'EDIT_MUSIC'),
])
def test_discussion_wording_does_not_grant_composition_authority(instruction, expected):
    assert MusicAssistantController._classify_intent(instruction) == expected
