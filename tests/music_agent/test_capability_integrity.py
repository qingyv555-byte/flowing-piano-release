from __future__ import annotations

from dataclasses import replace

import pytest

from ai_piano_studio.music_agent.capabilities import (
    AssistantCapabilitySet,
    EffectiveCapabilitySet,
    ModelCapabilities,
    build_capability_prompt,
)
from ai_piano_studio.music_agent.response_guard import (
    CapabilityClaimType,
    ResponseCapabilityGuard,
    ResponseGuardEvidence,
)
from ai_piano_studio.music_agent.turn_result import AssistantTurnResult
from ai_piano_studio.music_agent.tool_contracts import MusicPermission
from ai_piano_studio.music_agent.tool_registry import MusicToolRegistry


def _effective(*permissions: MusicPermission) -> EffectiveCapabilitySet:
    return EffectiveCapabilitySet.from_sources(
        AssistantCapabilitySet.from_registry(MusicToolRegistry.default()),
        ModelCapabilities(
            supports_tools=True,
            supports_structured_output=True,
            supports_audio=True,
            supports_files=True,
            supports_streaming=True,
        ),
        frozenset(permissions),
    )


def test_app_capabilities_are_derived_from_tools_and_audio_is_explicitly_unavailable() -> None:
    capabilities = AssistantCapabilitySet.from_registry(MusicToolRegistry.default())

    assert capabilities.music_read is True
    assert capabilities.notes_read is True
    assert capabilities.notes_write is True
    assert capabilities.bpm_write is True
    assert capabilities.harmony_read is True
    assert capabilities.harmony_write is True
    assert capabilities.audio_render is False
    assert capabilities.audio_generate is False
    assert capabilities.audio_playback_control is False
    assert capabilities.audio_attachment is False
    assert capabilities.wav_generation is False
    assert capabilities.mp3_generation is False
    assert capabilities.file_attachment is False


def test_effective_capabilities_intersect_model_and_current_permissions() -> None:
    effective = _effective(MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES)

    assert effective.music_read is True
    assert effective.notes_read is True
    assert effective.notes_write is True
    assert effective.bpm_write is False
    assert effective.harmony_write is False
    assert effective.audio_render is False
    assert effective.audio_attachment is False


def test_audio_capabilities_also_require_an_audio_permission() -> None:
    app = replace(
        AssistantCapabilitySet.from_registry(MusicToolRegistry.default()),
        audio_render=True,
        audio_generate=True,
        audio_playback_control=True,
        audio_attachment=True,
        wav_generation=True,
        mp3_generation=True,
        file_attachment=True,
    )
    effective = EffectiveCapabilitySet.from_sources(
        app,
        ModelCapabilities(True, True, True, True, True),
        frozenset({MusicPermission.READ_NOTES}),
    )

    assert effective.audio_render is False
    assert effective.audio_generate is False
    assert effective.audio_playback_control is False
    assert effective.audio_attachment is False
    assert effective.wav_generation is False
    assert effective.mp3_generation is False
    assert effective.file_attachment is False


def test_capability_prompt_is_generated_from_the_effective_source() -> None:
    prompt = build_capability_prompt(
        AssistantCapabilitySet.from_registry(MusicToolRegistry.default()),
        ModelCapabilities(True, True, True, True, True),
        frozenset({MusicPermission.READ_NOTES}),
    )

    assert "Available executable capabilities" in prompt
    assert "notes_read" in prompt
    assert "notes_write" not in prompt.split("Unavailable capabilities:", 1)[0]
    assert "audio_render" in prompt
    assert "wav_generation" in prompt
    assert "Never claim" in prompt


def test_response_guard_rewrites_audio_delivery_claim_without_deleting_valid_context() -> None:
    evidence = ResponseGuardEvidence(
        intent="CREATE_MUSIC",
        mode="proposal",
        tool_call_count=2,
        read_count=1,
        write_count=1,
        proposal_id="proposal-1",
        proposal_status="pending",
        committed=False,
        revision_before=4,
        revision_after=4,
        capabilities=_effective(MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES),
    )

    result = ResponseCapabilityGuard().guard(
        "我读取了当前工程。修改建议已经生成试听音频，稍后发送 WAV 给你。",
        evidence,
    )

    assert result.text.startswith("我读取了当前工程。")
    assert "修改建议" in result.text
    assert "试听音频已经生成" not in result.text
    assert "发送 WAV" not in result.text
    assert CapabilityClaimType.AUDIO_RENDER in result.blocked_claims
    assert CapabilityClaimType.FILE_DELIVERY in result.blocked_claims


def test_response_guard_preserves_truthful_audio_disclaimer() -> None:
    evidence = ResponseGuardEvidence(
        intent="CHAT",
        mode="proposal",
        capabilities=_effective(),
    )
    text = "当前软件不支持音频生成或文件附件，请使用工程内的播放和导出功能。"

    result = ResponseCapabilityGuard().guard(text, evidence)

    assert result.text == text
    assert result.blocked_claims == ()


def test_response_guard_keeps_a_negative_audio_clause_but_blocks_mixed_false_completion() -> None:
    evidence = ResponseGuardEvidence(
        intent="EDIT_MUSIC",
        mode="collaboration",
        capabilities=_effective(MusicPermission.READ_NOTES),
    )

    result = ResponseCapabilityGuard().guard(
        "我已经修改好了，但没有生成音频。",
        evidence,
    )

    assert "尚未生成实际修改" in result.text
    assert "但没有生成音频" in result.text
    assert CapabilityClaimType.FALSE_COMPLETION in result.blocked_claims


def test_response_guard_blocks_completion_without_real_write_or_proposal() -> None:
    evidence = ResponseGuardEvidence(
        intent="EDIT_MUSIC",
        mode="collaboration",
        capabilities=_effective(MusicPermission.READ_NOTES),
    )

    result = ResponseCapabilityGuard().guard("好的，我已经修改好了。", evidence)

    assert "已经修改好了" not in result.text
    assert "尚未生成实际修改" in result.text
    assert CapabilityClaimType.FALSE_COMPLETION in result.blocked_claims


def test_response_guard_waits_until_turn_end_before_checking_edit_completion() -> None:
    evidence = ResponseGuardEvidence(
        intent="EDIT_MUSIC",
        mode="collaboration",
        status="streaming",
        capabilities=_effective(MusicPermission.READ_NOTES),
    )

    result = ResponseCapabilityGuard().guard("已完成修改。", evidence)

    assert result.text == "已完成修改。"
    assert CapabilityClaimType.FALSE_COMPLETION not in result.blocked_claims


def test_response_guard_allows_completion_after_committed_revision_change() -> None:
    evidence = ResponseGuardEvidence(
        intent="EDIT_MUSIC",
        mode="collaboration",
        tool_call_count=1,
        write_count=1,
        committed=True,
        revision_before=4,
        revision_after=5,
        capabilities=_effective(MusicPermission.WRITE_NOTES),
    )

    result = ResponseCapabilityGuard().guard("已完成修改。", evidence)

    assert result.text == "已完成修改。"
    assert result.blocked_claims == ()


def test_response_guard_catches_render_and_direct_listen_claims_without_file_keywords() -> None:
    evidence = ResponseGuardEvidence(
        intent="CREATE_MUSIC",
        mode="proposal",
        capabilities=_effective(),
    )

    result = ResponseCapabilityGuard().guard("已经渲染好了，马上播放给你听。", evidence)

    assert CapabilityClaimType.AUDIO_RENDER in result.blocked_claims
    assert CapabilityClaimType.AUDIO_PLAYBACK in result.blocked_claims


def test_response_guard_catches_playback_claim_without_an_explicit_subject() -> None:
    evidence = ResponseGuardEvidence(
        intent="CREATE_MUSIC",
        mode="proposal",
        capabilities=_effective(),
    )

    result = ResponseCapabilityGuard().guard("音频已经播放了。", evidence)

    assert "不能直接播放音频" in result.text
    assert CapabilityClaimType.AUDIO_PLAYBACK in result.blocked_claims


def test_assistant_turn_result_rejects_inconsistent_commit_evidence() -> None:
    with pytest.raises(ValueError, match="revision_after"):
        AssistantTurnResult(
            status="finished",
            intent="EDIT_MUSIC",
            mode="collaboration",
            tool_call_count=1,
            read_count=0,
            write_count=1,
            proposal_id="",
            proposal_status="",
            revision_before=4,
            revision_after=4,
            committed=True,
            capabilities={},
            final_text="已完成修改。",
        )

    with pytest.raises(ValueError, match="non-negative"):
        AssistantTurnResult(
            status="finished",
            intent="CHAT",
            mode="suggestion",
            tool_call_count=-1,
            read_count=0,
            write_count=0,
            proposal_id="",
            proposal_status="",
            revision_before=None,
            revision_after=None,
            committed=False,
            capabilities={},
            final_text="好的。",
        )
