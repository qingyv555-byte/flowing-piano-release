"""Provider-neutral response validation against real assistant capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ai_piano_studio.music_agent.capabilities import EffectiveCapabilitySet


class CapabilityClaimType(str, Enum):
    AUDIO_RENDER = "audio_render"
    AUDIO_ATTACHMENT = "audio_attachment"
    AUDIO_PLAYBACK = "audio_playback"
    FILE_DELIVERY = "file_delivery"
    UNSUPPORTED_TOOL_ACTION = "unsupported_tool_action"
    FALSE_COMPLETION = "false_completion"
    FALSE_TOOL_DENIAL = "false_tool_denial"


@dataclass(frozen=True, slots=True)
class ResponseGuardEvidence:
    """Execution facts used to validate a provider's natural-language claims."""

    intent: str = "CHAT"
    mode: str = "suggestion"
    status: str = "finished"
    tool_call_count: int = 0
    read_count: int = 0
    write_count: int = 0
    proposal_id: str = ""
    proposal_status: str = ""
    committed: bool = False
    revision_before: int | None = None
    revision_after: int | None = None
    capabilities: EffectiveCapabilitySet | None = None

    @property
    def has_real_edit(self) -> bool:
        return bool(
            self.committed
            or self.write_count > 0
            or self.proposal_status in {"pending", "applied"}
        )


@dataclass(frozen=True, slots=True)
class ResponseGuardResult:
    text: str
    blocked_claims: tuple[CapabilityClaimType, ...] = ()


_NEGATIVE_MARKERS = (
    "不支持",
    "不能",
    "无法",
    "没有",
    "尚未",
    "未提供",
    "不会",
    "不可",
    "不具备",
    "not ",
    "no ",
    "cannot",
    "can't",
    "unsupported",
    "without",
)
_AUDIO_WORDS = r"(?:试听音频|音频文件|音频|audio|wav|mp3)"
_AUDIO_RENDER_RE = re.compile(
    rf"(?:生成|创建|渲染|制作|generate|create|render).{{0,24}}{_AUDIO_WORDS}"
    rf"|{_AUDIO_WORDS}.{{0,24}}(?:已生成|已经生成|正在生成|马上生成|created|generated)"
    rf"|(?:已|已经|正在|马上)?(?:渲染|render)(?:好了|完成|好)?",
    re.IGNORECASE,
)
_FILE_DELIVERY_RE = re.compile(
    rf"(?:发送|附加|上传|提供|给你|稍后发送|send|attach|upload|deliver).{{0,24}}"
    rf"(?:wav|mp3|音频|audio|文件|file)",
    re.IGNORECASE,
)
_PLAYBACK_RE = re.compile(
    r"(?:播放给你听|给你听|(?:我|助手|ai).{0,8}播放|"
    r"(?:已|已经|正在|马上)?播放(?:了|完成|中)?|"
    r"play.{0,16}(?:audio|listen|for you))",
    re.IGNORECASE,
)
_UNSUPPORTED_TOOL_RE = re.compile(
    r"(?:调用|使用|执行|调用了).{0,16}(?:音频|渲染|播放|wav|mp3).{0,16}(?:工具|tool|接口)",
    re.IGNORECASE,
)
_COMPLETION_RE = re.compile(
    r"(?:已经|已)?(?:完成(?:了)?(?:修改|写入|创作|处理)?|"
    r"修改好了|处理好了|创作好了|写入成功|生成成功|新增成功|删除成功|"
    r"done|finished)",
    re.IGNORECASE,
)
_EDIT_WORD_RE = re.compile(r"(?:修改|写入|创作|加入|删除|新增|生成).{0,12}(?:好了|完成|成功)")


class ResponseCapabilityGuard:
    """Rewrite only claims contradicted by execution evidence or capability state."""

    def guard(self, text: str, evidence: ResponseGuardEvidence) -> ResponseGuardResult:
        if not isinstance(text, str) or not text.strip():
            return ResponseGuardResult("")
        if not isinstance(evidence, ResponseGuardEvidence):
            raise ValueError("response guard evidence is invalid")
        blocked: list[CapabilityClaimType] = []
        parts: list[str] = []
        for segment in self._segments(text):
            stripped = segment.strip()
            if self._false_tool_denial(stripped, evidence):
                if CapabilityClaimType.FALSE_TOOL_DENIAL not in blocked:
                    blocked.append(CapabilityClaimType.FALSE_TOOL_DENIAL)
                parts.append("当前可通过提供的 MIDI 音符工具处理音乐；原曲素材是否齐全需要另行核对。")
                continue
            if not stripped or self._is_disclaimer(stripped):
                parts.append(segment)
                continue
            claims = self._claims_for(stripped, evidence)
            if not claims:
                parts.append(segment)
                continue
            for claim in claims:
                if claim not in blocked:
                    blocked.append(claim)
            parts.append(self._replacement(claims, evidence))
        return ResponseGuardResult("".join(parts).strip(), tuple(blocked))

    @staticmethod
    def _false_tool_denial(text: str, evidence: ResponseGuardEvidence) -> bool:
        capabilities = evidence.capabilities
        if capabilities is None or not capabilities.notes_write:
            return False
        return bool(re.search(
            r"(?:没有|不具备|未提供|无法使用|不能使用).{0,20}"
            r"(?:音乐编辑|音符编辑|音乐修改|音符写入|midi编辑|midi 编辑).{0,10}(?:工具|能力|接口)"
            r"|(?:我|助手|当前).{0,8}(?:不能|无法)(?:直接)?(?:编辑|修改|写入)(?:音乐|音符|midi)"
            r"|(?:cannot|can't|no|don't have).{0,20}(?:edit midi|edit notes|music.editing tools)",
            text, flags=re.IGNORECASE,
        ))

    @staticmethod
    def _segments(text: str) -> tuple[str, ...]:
        segments = tuple(
            part
            for part in re.split(
                r"(?<=[。！？!?；;，,\n])|(?<=\.)(?=\s)|(?=但是|但|可是|不过|however|but\b)",
                text,
                flags=re.IGNORECASE,
            )
            if part
        )
        return segments or (text,)

    @staticmethod
    def _is_disclaimer(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in _NEGATIVE_MARKERS)

    @staticmethod
    def _claims_for(
        text: str, evidence: ResponseGuardEvidence
    ) -> tuple[CapabilityClaimType, ...]:
        claims: list[CapabilityClaimType] = []
        if _AUDIO_RENDER_RE.search(text):
            claims.append(CapabilityClaimType.AUDIO_RENDER)
        if _FILE_DELIVERY_RE.search(text):
            claims.append(CapabilityClaimType.FILE_DELIVERY)
            claims.append(CapabilityClaimType.AUDIO_ATTACHMENT)
        if _UNSUPPORTED_TOOL_RE.search(text):
            claims.append(CapabilityClaimType.UNSUPPORTED_TOOL_ACTION)
        if _PLAYBACK_RE.search(text) and not any(
            marker in text
            for marker in (
                "在软件中",
                "在编辑器",
                "使用软件",
                "使用工程",
                "工程内",
                "播放功能",
                "导出功能",
                "你可以",
            )
        ):
            claims.append(CapabilityClaimType.AUDIO_PLAYBACK)
        if (
            not evidence.has_real_edit
            and evidence.intent in {"CREATE_MUSIC", "EDIT_MUSIC"}
            and evidence.status not in {"streaming"}
            and (_EDIT_WORD_RE.search(text) or _COMPLETION_RE.search(text))
        ):
            claims.append(CapabilityClaimType.FALSE_COMPLETION)
        return tuple(dict.fromkeys(claims))

    @staticmethod
    def _replacement(
        claims: tuple[CapabilityClaimType, ...], evidence: ResponseGuardEvidence
    ) -> str:
        if CapabilityClaimType.FALSE_COMPLETION in claims:
            if evidence.proposal_status == "pending":
                return "修改建议已生成，但尚未应用到工程；请先试听或应用。"
            return "AI 给出了建议，但尚未生成实际修改。"
        audio_claim = any(
            claim
            in {
                CapabilityClaimType.AUDIO_RENDER,
                CapabilityClaimType.AUDIO_ATTACHMENT,
                CapabilityClaimType.FILE_DELIVERY,
            }
            for claim in claims
        )
        if audio_claim:
            if evidence.proposal_status == "pending":
                return "修改建议已准备好；对话不能生成或发送试听音频，请使用下方“试听”按钮在本地检查。"
            return "当前软件不能在对话中生成或发送音频文件，请使用工程内的播放或导出功能。"
        if CapabilityClaimType.AUDIO_PLAYBACK in claims:
            return "对话不能直接播放音频，请使用编辑器的播放功能。"
        return "该操作不在当前助手的可用工具范围内。"
