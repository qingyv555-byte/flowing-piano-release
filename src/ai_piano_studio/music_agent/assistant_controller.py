"""QML-facing state boundary for the phased Music Assistant.

The controller intentionally has no model-agent loop. It owns presentation
state, secure provider configuration, and the existing music-proposal APIs so
Phase 8 can connect a real provider without replacing the UI architecture.
"""

from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, QRect, QSettings, QSortFilterProxyModel, Qt, QThread, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.capabilities import (
    AssistantCapabilitySet,
    EffectiveCapabilitySet,
    ModelCapabilities,
)
from ai_piano_studio.music_agent.credentials import KeyringCredentialStore
from ai_piano_studio.music_agent.conversation_history import ConversationHistory
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoop, MusicAgentLoopOutcome
from ai_piano_studio.music_agent.context_builder import (
    MAX_CONVERSATION_HISTORY_BYTES, MusicAgentContextBuilder,
)
from ai_piano_studio.music_agent.intent_templates import get_quick_action_template
from ai_piano_studio.music_agent.proposal_manager import (
    MusicEditProposalManager,
    ProposalChangeSet,
    ProposalChangeSummary,
)
from ai_piano_studio.music_agent.provider_settings import (
    ConnectionResult,
    ProviderSettingsService,
    ThirdPartyWarningRequired,
    map_connection_error,
)
from ai_piano_studio.music_agent.providers import ProviderError
from ai_piano_studio.music_agent.progress import ProviderProgressEvent, TaskProgress
from ai_piano_studio.music_agent.codex_provider import CodexProvider, detect_codex
from ai_piano_studio.music_agent.response_guard import (
    ResponseCapabilityGuard,
    ResponseGuardEvidence,
)
from ai_piano_studio.music_agent.turn_result import AssistantTurnResult
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentExecutionContext,
    MusicExecutionMode,
    MusicEditProposalStatus,
    MusicPermission,
    MusicToolResult,
)
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway

if TYPE_CHECKING:
    from ai_piano_studio.audio.controller import PlaybackController


logger = logging.getLogger(__name__)

_TOOL_FAILURE_MESSAGES = {
    "validation_error": "音乐参数校验未通过，本次修改未应用。",
    "permission_denied": "这次修改超出了当前允许的音乐范围。",
    "limit_exceeded": "本次音乐操作达到了已设置的操作上限，请调整操作设置后重试。",
    "conflict": "工程内容已经变化，请重新生成建议。",
    "execution_error": "音乐工具执行失败，请重试。",
    "context_error": "准备音乐数据失败，请求尚未发送给模型，工程未更改。",
    "incomplete_music": "实际修改未达到请求的目标时长，本次修改未应用，工程保留原状。",
}
_PROVIDER_FAILURE_MESSAGES = {
    "timeout": "等待已达到本次设置的上限，请重试或在 AI 服务设置中增加等待时限。",
    "provider_error": "服务商暂时无法完成请求，请检查连接后重试。",
    "provider_unavailable": "服务商暂时不可用，请检查网络或稍后重试。",
    "authentication": "服务商认证失败，请检查 API Key 和连接配置。",
    "rate_limit": "服务商当前限流，请稍后重试或切换模型。",
    "unsupported_capability": "当前服务商或模型不支持这项请求，请刷新模型或切换兼容模型。",
    "context_too_large": "发送给服务商的上下文过大，请缩小授权范围或分段处理。",
}
_EMPTY_PROJECT_INITIAL_BARS = 8
_ASSISTANT_WINDOW_SETTINGS_PREFIX = "assistantWindow/normal"
_ASSISTANT_WINDOW_DEFAULT_WIDTH = 1080
_ASSISTANT_WINDOW_DEFAULT_HEIGHT = 900
_ASSISTANT_WINDOW_MINIMUM_WIDTH = 860
_ASSISTANT_WINDOW_MINIMUM_HEIGHT = 640
_CREATE_INTENT_MARKERS = ("写", "生成", "续写", "创作", "来一段", "作曲")
_EDIT_INTENT_MARKERS = (
    "升高",
    "降低",
    "修改",
    "调整",
    "移动",
    "删除",
    "添加",
    "加入",
    "增加",
    "加上",
    "减",
    "转调",
    "优化",
    "增强",
    "改",
    "更梦幻",
    "更柔和",
    "更温暖",
    "更悲伤",
    "更有力",
    "更清晰",
    "更紧张",
    "更舒缓",
)
_FOLLOWUP_EDIT_MARKERS = (
    "再改",
    "再调整",
    "再柔和",
    "再明显",
    "继续改",
    "再修改",
    "换一种",
    "还是不好",
)
_READ_INTENT_MARKERS = ("这段", "感觉", "分析", "看看工程", "和声", "节奏", "调性")
_MUSIC_OBJECT_MARKERS = (
    "midi",
    "钢琴曲",
    "原曲",
    "曲子",
    "乐曲",
    "歌曲",
    "当前旋律",
    "当前音符",
    "选中的音符",
    "当前选择",
    "旋律",
    "音符",
    "高潮",
)
_MUSIC_READ_VERBS = ("看看", "查看", "读取", "分析", "检查", "评估")
_MUSIC_EDIT_OPERATIONS = (
    "做一个高潮",
    "提高八度",
    "降低八度",
    "升高八度",
    "降低半音",
    "提高半音",
    "升高半音",
    "力度调高",
    "力度调低",
    "力度增大",
    "力度减小",
)
_PREVIEW_OVERRIDE_MARKERS = (
    "先试听",
    "先预览",
    "让我试听后再应用",
    "先让我试听再决定",
    "先生成一个试听方案",
    "不要直接应用",
    "给我一个可以试听的方案",
)
_PREVIEW_OPTOUT_MARKERS = (
    "不要试听",
    "无需试听",
    "只分析",
    "仅分析",
    "只给文字",
    "只给建议",
    "不生成方案",
    "不要生成试听方案",
    "不要修改",
    "暂不修改",
)
_SCOPED_EDIT_TARGET_MARKERS = (
    "开头",
    "前奏",
    "节奏",
    "旋律",
    "音符",
    "和弦",
    "音高",
    "力度",
    "时值",
    "位置",
    "bpm",
    "速度",
    "调性",
    "主题",
)
_NON_EXECUTION_GUIDANCE_MARKERS = (
    "怎么",
    "如何",
    "方法",
    "建议",
    "分析",
    "告诉我",
    "说明",
    "思路",
    "方案",
)
_ADVICE_INTENT_MARKERS = (
    "先给我建议",
    "先给建议",
    "给我建议",
    "给建议",
    "给我一些修改建议",
    "给我修改建议",
    "先看看怎么改",
    "先看看如何改",
    "先看看怎么调整",
    "先看看怎么优化",
    "先分析",
    "分析一下怎么",
    "分析怎么",
    "告诉我怎么改",
    "告诉我如何改",
    "告诉我如何修改",
    "告诉我怎么修改",
    "只给建议",
    "先给方案",
    "先说方案",
    "先不要修改",
    "暂不修改",
    "不要修改",
    "不修改",
    "不想修改",
    "只分析",
)
_EXECUTION_INTENT_MARKERS = (
    "不要只给建议",
    "直接修改",
    "直接改",
    "现在执行",
    "立即执行",
    "按这个方案修改",
    "按方案修改",
    "按这个修改",
    "开始修改",
    "立即修改",
    "直接做",
)
_MUSIC_CONTEXT_MARKERS = (
    "当前",
    "选中",
    "范围",
    "旋律",
    "音符",
    "和弦",
    "节奏",
    "小节",
    "高潮",
    "主题",
    "调性",
    "钢琴",
    "工程",
    "梦幻",
    "柔和",
    "温暖",
    "悲伤",
    "有力",
    "清晰",
    "紧张",
    "舒缓",
)
_DIRECT_MUSIC_EDIT_RE = re.compile(
    r"(?:直接|请|帮我|现在|立即|把|将|让|使).{0,80}"
    r"(?:升高|降低|修改|调整|移动|删除|添加|加入|增加|加上|改(?:成|得|为)?|"
    r"优化|增强|变得|更(?:梦幻|柔和|温暖|悲伤|有力|清晰|紧张|舒缓)|高潮|和弦|旋律|节奏|主题)",
    re.IGNORECASE,
)
_CREATION_SCOPE_AUTOMATIC = "automatic"
_CREATION_SCOPE_MANUAL = "manual"
_MAX_ASSISTANT_MESSAGE_CHARS = 4_000
_MUSIC_CONTINUATION_STATUS_MARKERS = (
    "好了吗",
    "完成了吗",
    "做完了吗",
    "试听",
    "播放",
    "听一下",
    "听听",
    "查看结果",
    "看看结果",
    "预览一下",
    "看看改动",
    "查看改动",
    "看看效果",
    "现在怎么样",
)
_MUSIC_CONTINUATION_APPROVAL_MARKERS = (
    "允许",
    "同意",
    "可以",
    "开始",
    "继续",
    "执行",
    "确认",
    "按这个",
    "按方案",
    "直接做",
    "就这样",
)
_MUSIC_CONTINUATION_APPROVAL_PREFIXES = (
    "允许",
    "同意",
    "开始",
    "继续",
    "执行",
    "确认",
    "按这个",
    "按方案",
    "直接做",
    "就这样",
    "可以执行",
    "可以按这个",
    "可以按方案",
    "可以直接做",
    "可以开始",
    "可以直接开始",
)


class AssistantMessageKind(StrEnum):
    USER_MESSAGE = "USER_MESSAGE"
    ASSISTANT_MESSAGE = "ASSISTANT_MESSAGE"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    ACTIVITY = "ACTIVITY"
    PROPOSAL = "PROPOSAL"
    SYSTEM_NOTICE = "SYSTEM_NOTICE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    kind: AssistantMessageKind
    text: str
    title: str = ""
    detail: str = ""
    proposal_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "title": self.title,
            "detail": self.detail,
            "proposalId": self.proposal_id,
        }


class AssistantMessageModel(QAbstractListModel):
    persistenceError = Signal()
    KindRole = Qt.ItemDataRole.UserRole + 1
    TextRole = Qt.ItemDataRole.UserRole + 2
    TitleRole = Qt.ItemDataRole.UserRole + 3
    DetailRole = Qt.ItemDataRole.UserRole + 4
    ProposalIdRole = Qt.ItemDataRole.UserRole + 5

    def __init__(self, parent: QObject | None = None, *, history: ConversationHistory | None = None) -> None:
        super().__init__(parent)
        self._history = history
        self.history_error = ''
        self._history_readable = True
        self._entries: list[AssistantMessage] = []
        self.bind_history(history)

    def bind_history(self, history: ConversationHistory | None, fallback=()) -> None:
        self.beginResetModel()
        self._history = history
        self._history_readable = True
        self.history_error = ''
        self._entries = list(fallback)
        if history is not None:
            try:
                rows = history.load()
                entries = []
                for row in rows:
                    kind = AssistantMessageKind(row['kind'])
                    text = row['text']
                    if kind is AssistantMessageKind.PROPOSAL:
                        text = '\n'.join(filter(None, ('历史修改建议（只读）', row.get('title'), text, row.get('detail'))))
                        kind = AssistantMessageKind.SYSTEM_NOTICE
                    entries.append(AssistantMessage(kind, text, row.get('title', ''), row.get('detail', '')))
                self._entries = entries
            except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error):
                self._history_readable = False
                self._report_history_error()
        self.endResetModel()

    def _report_history_error(self) -> None:
        logger.exception('Workspace conversation persistence failed')
        self.history_error = '聊天记录暂时无法保存或读取，请保留当前窗口。原记录未清除。'
        self.persistenceError.emit()

    def _save(self, row: int, message: AssistantMessage) -> None:
        if self._history is not None and self._history_readable:
            try:
                self._history.save(row, message.to_dict())
            except (OSError, sqlite3.Error):
                self._history_readable = False
                self._report_history_error()

    @property
    def entries(self) -> tuple[AssistantMessage, ...]:
        return tuple(self._entries)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        message = self._entries[index.row()]
        values = {
            self.KindRole: message.kind.value,
            self.TextRole: message.text,
            self.TitleRole: message.title,
            self.DetailRole: message.detail,
            self.ProposalIdRole: message.proposal_id,
        }
        return values.get(role)

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.KindRole: b"kind",
            self.TextRole: b"text",
            self.TitleRole: b"title",
            self.DetailRole: b"detail",
            self.ProposalIdRole: b"proposalId",
        }

    def append(self, message: AssistantMessage) -> None:
        row = len(self._entries)
        self._save(row, message)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(message)
        self.endInsertRows()

    def clear(self) -> None:
        if self._history is not None:
            try:
                self._history.clear()
            except (OSError, sqlite3.Error):
                self._report_history_error()
                return
            self._history_readable = True
            self.history_error = ''
        self.beginResetModel()
        self._entries.clear()
        self.endResetModel()

    def replace(self, row: int, message: AssistantMessage) -> None:
        if row < 0 or row >= len(self._entries):
            raise IndexError("message row is out of range")
        self._save(row, message)
        self._entries[row] = message
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, list(self.roleNames()))


_REASONING_CHOICES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("off", "关闭", ()),
    ("low", "低", ("low",)),
    ("standard", "标准", ("medium", "standard")),
    ("high", "高", ("high",)),
    ("xhigh", "极高", ("xhigh", "extra_high", "max")),
)


class ConversationMessageView(QSortFilterProxyModel):
    """Show dialogue/errors/proposals while keeping technical events in history."""
    def filterAcceptsRow(self, row, parent):
        message = self.sourceModel().entries[row]
        return (message.kind not in {AssistantMessageKind.SYSTEM_NOTICE, AssistantMessageKind.ACTIVITY}
                or message.text.startswith(('历史修改建议（只读）', '执行回执：')))

    @Slot(int, result=int)
    def sourceRow(self, row):
        return self.mapToSource(self.index(row, 0)).row()


class _CatalogBoundProvider:
    """Keep a provider transport aligned with the selected catalog capabilities."""

    def __init__(self, provider: object, capabilities: object) -> None:
        self._provider = provider
        self._capabilities = capabilities

    def get_capabilities(self, _model_id: str) -> object:
        return self._capabilities

    def __getattr__(self, name: str) -> object:
        return getattr(self._provider, name)


class _GatewayDispatchCancelled(RuntimeError):
    """Raised in the provider worker before it can call a stale GUI gateway."""


@dataclass(slots=True)
class _GatewayCall:
    generation: int
    cancelled: Event
    method: str
    args: tuple[object, ...]
    kwargs: dict[str, object]
    done: Event = field(default_factory=Event)
    result: object = None
    error: BaseException | None = None


class _SerializedGateway(MusicAgentToolGateway):
    """Proxy Task 2 gateway operations through the controller's Qt thread."""

    def __init__(self, controller: MusicAssistantController, generation: int, cancelled: Event) -> None:
        self._controller = controller
        self._generation = generation
        self._cancelled = cancelled
        self._creator = controller._creator_enabled
        self._creator_context = None
        self._creator_owner = f'{controller.conversationId}/{generation}'
        self._resource_session = None
        if controller._creation_workflow is not None:
            from ai_piano_studio.music_agent.resource_tools import MusicResourceSession
            workflow = controller._creation_workflow
            inherited = getattr(workflow, 'handoff_resource_session', None) if workflow.current(generation) else None
            if inherited is not None:
                self._resource_session = inherited
                workflow.handoff_resource_session = None
                inherited.cancelled = cancelled.is_set
                inherited.progress = lambda event: controller._generationProgress.emit(generation, event)
                inherited.invalidate_score_image_delivery()
            else:
                self._resource_session = MusicResourceSession(controller._editor._projects.root.parent,
                    cancelled.is_set, lambda event: controller._generationProgress.emit(generation, event))
            self._resource_session.baseline = (controller._editor.projectSessionId, controller._editor.projectRevision)
            self._resource_session.instruction = controller._retry_instruction
            self._resource_session.song_task = getattr(workflow, 'song_task', None) if workflow.current(generation) else None
            self._resource_session.requested_duration = controller._task_memory.state.get('constraints', {}).get('targetSeconds', 15)
            flow = getattr(controller, '_song_source_flow', None)
            self._resource_session.require_source_confirmation = flow is not None and not self._creator
            self._resource_session.approved_source = flow.approved_for(generation) if flow and not self._creator else None
            self._resource_session.message_start_row = controller._task_message_start_row
            controller._resource_sessions[generation] = self._resource_session

    @property
    def registry(self):
        from ai_piano_studio.music_agent.memory_tool import with_task_history
        from ai_piano_studio.music_agent.audition_tools import with_audition_analysis
        registry = with_audition_analysis(with_task_history(self._controller.gateway.registry))
        if self._resource_session is not None:
            from ai_piano_studio.music_agent.resource_tools import with_music_resources
            registry = with_music_resources(registry)
        if self._controller._appearance_tools is not None:
            from ai_piano_studio.music_agent.style_tools import with_appearance_tools
            registry = with_appearance_tools(registry)
        if self._creator:
            from ai_piano_studio.music_agent.creator_tools import with_creator_tools
            registry = with_creator_tools(registry)
        return registry

    def execute(self, name: str, arguments: object, context: MusicAgentExecutionContext) -> MusicToolResult:
        if self._creator:
            from ai_piano_studio.music_agent.creator_tools import CREATOR_NAMES
            if name in CREATOR_NAMES:
                return self._execute_creator(name, arguments, context)
            context = self.creator_execution_context(context)
        workflow = getattr(self._controller, '_creation_workflow', None)
        isolated = bool(workflow and workflow.current(self._generation) and workflow.gateway is not None
                        and workflow.context is not None
                        and context.project_identity == workflow.context.project_identity)
        if self._resource_session is not None and self._resource_session.require_source_confirmation and not isolated:
            definition = self.registry.lookup(name)
            if definition is not None and not definition.read_only and name != 'prepare_song_preview':
                approved = self._resource_session.approved_source
                if (context.intent_template == 'source_discovery'
                        or (approved is None and (self._resource_session.song_task is not None
                            or (self._resource_session.inspected_sources
                                and self._controller._active_intent in {'CREATE_MUSIC', 'CHAT'})))
                        or (approved is not None and not self._resource_session.approved_source_ready)):
                    return MusicToolResult.failure(name, 'source_confirmation_required',
                        'Identify the actual source and obtain user confirmation before writing music. '
                        'After approval, inspect that exact source again before transcribing.')
        if name == "get_task_history":
            return self._invoke("read_task_history", arguments, context)
        if name == 'analyze_audition':
            from ai_piano_studio.music_agent.audition_tools import analyze_audition
            snapshot = self._invoke('audition_snapshot', arguments, context)
            return analyze_audition(snapshot, self._cancelled.is_set)
        if self._controller._appearance_tools is not None:
            from ai_piano_studio.music_agent.style_tools import STYLE_NAMES
            if name in STYLE_NAMES:
                return self._invoke('appearance_tool', name, arguments, context)
        if self._resource_session is not None:
            from ai_piano_studio.music_agent.resource_tools import RESOURCE_NAMES
            if name in RESOURCE_NAMES:
                if name == 'prepare_song_preview' and self._resource_session.require_source_confirmation:
                    from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds
                    try:
                        duration = requested_excerpt_seconds(self._resource_session.instruction,
                            default=(self._resource_session.song_task or {}).get('duration_seconds',
                                getattr(self._resource_session, 'requested_duration', 15)), unambiguous_only=True)
                    except ValueError as error:
                        return MusicToolResult.failure(name, 'validation_error', str(error))
                    if isinstance(arguments, Mapping):
                        arguments = dict(arguments, duration_seconds=duration)
                validation = self._invoke('validate_resource_request', name, arguments, context)
                if not validation.success:
                    return validation
                payload = validation.data['payload']
                from ai_piano_studio.music_agent.resource_tools import STUDIO_ASSIST_TEMPLATE
                if name == 'set_song_task':
                    result = self._invoke('accept_model_song_task', payload)
                    if result.success:
                        self._resource_session.song_task = result.data
                    return result
                if name == 'start_original_draft':
                    return MusicToolResult.success_result(name, 'Open the isolated original draft.',
                        data=dict(status='start_original_draft'))
                if name == 'start_song_draft':
                    if self._resource_session.song_task is None:
                        return MusicToolResult.failure(name, 'validation_error',
                            'Record the requested song using set_song_task first.')
                    return MusicToolResult.success_result(name, 'Open the isolated song draft.',
                        data=dict(self._resource_session.song_task, status='start_song_draft'))
                if (name == 'propose_song_source' and context.intent_template == STUDIO_ASSIST_TEMPLATE
                        and self._resource_session.song_task is None):
                    return MusicToolResult.failure(name, 'validation_error',
                        'Record the requested song with set_song_task before proposing its source.')
                result = self._resource_session.execute(name, payload)
                if name == 'propose_song_source' and self._resource_session.pending_source is not None:
                    candidate = self._resource_session.pending_source
                    flow = self._controller._song_source_flow
                    if flow and any(candidate.selected_url == old.selected_url
                                    or candidate.selected_sha256 == old.selected_sha256
                                    for old in flow.rejected_sources):
                        self._resource_session.pending_source = None
                        return MusicToolResult.failure(name, 'validation_error',
                            'The user rejected this source. Compare and inspect a different candidate or edition; do not reuse it.')
                return result
        return self._invoke("execute", name, arguments, context)

    def creator_execution_context(self, context):
        return self._creator_context or context

    def creator_facts(self, context):
        return self._invoke('creator_facts', context)

    @property
    def creator_has_preview(self):
        return self._resource_session is not None and self._resource_session.pending_song is not None

    def creator_finish_turn(self, _turn_id):
        self._controller._creator_workspace.finish_turn(self._creator_owner)

    def creator_pending_commands(self):
        return self._controller._creator_workspace.pending_commands(self._creator_owner)

    def creator_record_receipt(self, name, result):
        self._controller._creator_workspace.record_receipt(self._creator_owner, name, result)

    def _execute_creator(self, name, arguments, context):
        from ai_piano_studio.music_agent.creator_tools import APP_TOOL_NAMES
        workspace = self._controller._creator_workspace
        if name == 'ask_user':
            answer = self.creator_ask_user(arguments['question'])
            return MusicToolResult.success_result(name, 'User replied.', data=dict(answer=answer))
        if name in APP_TOOL_NAMES:
            result, self._creator_context = self._invoke('creator_app', name, arguments,
                                                        self.creator_execution_context(context))
            return result
        try:
            if name == 'read_image_file':
                from PySide6.QtCore import QBuffer, QIODevice
                from PySide6.QtGui import QImage
                path = workspace.resolve(arguments['path'])
                source = QImage(str(path))
                if source.isNull():
                    raise ValueError('The image could not be decoded.')
                output = QBuffer()
                output.open(QIODevice.OpenModeFlag.WriteOnly)
                if not source.save(output, 'PNG'):
                    raise ValueError('The image could not be encoded for delivery.')
                return MusicToolResult.success_result(name, 'Image read.',
                    data=dict(path=str(path), _provider_images=(bytes(output.data()),)))
            if name == 'prepare_file_preview':
                from ai_piano_studio.music_agent.song_workflow import prepare_project, prepare_song, discard_prepared_song
                from ai_piano_studio.music_agent.song_resolver import ResolvedMusic
                from ai_piano_studio.persistence.midi import load_midi
                from ai_piano_studio.persistence.project_document import load_project_document
                path = workspace.resolve(arguments['path'])
                title = arguments.get('title') or path.stem
                provenance = dict(kind='model_workspace', title=title, source_file=str(path), verifiedNotes=False)
                if path.suffix.lower() in {'.mid', '.midi'}:
                    song = prepare_song(ResolvedMusic(title, load_midi(path), provenance), '', workspace.home,
                                        cancelled=self._cancelled.is_set)
                else:
                    song = prepare_project(load_project_document(path).state, title, provenance, workspace.home,
                                           cancelled=self._cancelled.is_set)
                if self._resource_session.pending_song is not None:
                    discard_prepared_song(self._resource_session.pending_song, workspace.home)
                self._resource_session.pending_song = song
                return MusicToolResult.success_result(name, 'Preview prepared.', data=dict(
                    status='preview_ready', title=title, note_count=len(song.state.playback_notes),
                    duration_seconds=song.state.playback_duration, source_file=str(path)))
        except (OSError, ValueError, KeyError, TypeError) as error:
            return MusicToolResult.failure(name, 'execution_error', str(error))
        return workspace.execute(name, arguments, turn_id=self._creator_owner, cancelled=self._cancelled.is_set)

    def creator_ask_user(self, question):
        self._invoke('creator_question', str(question))
        while not self._cancelled.wait(.05):
            replies = self._controller._take_pending_messages(self._generation)
            if replies:
                self._invoke('creator_question_answered')
                return '\n\n'.join(replies)
        raise _GatewayDispatchCancelled()

    def creator_native_questions(self, payload):
        answers = {}
        for question in payload.get('questions', []):
            text = question.get('question', '')
            options = question.get('options', [])
            if options:
                text += '\n' + '\n'.join(str(option.get('label', '')) + '：' + str(option.get('description', '')) for option in options)
            answers[question['id']] = dict(answers=[self.creator_ask_user(text)])
        return dict(answers=answers)

    def acknowledge_score_images(self, source_sha256):
        if self._resource_session is not None:
            self._resource_session.acknowledge_score_images(source_sha256)

    def invalidate_score_image_delivery(self):
        if self._resource_session is not None:
            self._resource_session.invalidate_score_image_delivery()

    def compact_context(
        self, context: MusicAgentExecutionContext, requested_bars: tuple[BarRange, ...]
    ) -> object:
        return self._invoke("compact_context", context, requested_bars)

    def current_bar_duration_seconds(self, context: MusicAgentExecutionContext) -> float:
        return self._invoke("current_bar_duration_seconds", context)

    def current_proposal(self, context: MusicAgentExecutionContext) -> object:
        return self._invoke("current_proposal", context)

    def release_proposal(self, context: MusicAgentExecutionContext) -> None:
        self._invoke("release_proposal", context)

    def abort_turn(self, context: MusicAgentExecutionContext) -> MusicToolResult:
        return self._invoke("abort_turn", context)

    def finish_turn(self, context: MusicAgentExecutionContext) -> MusicToolResult:
        if self._cancelled.is_set():
            raise _GatewayDispatchCancelled()
        return MusicToolResult.success_result("agent_turn", "collaboration turn is ready")

    def _invoke(self, method: str, *args: object) -> object:
        return self._controller._dispatch_gateway_call(
            self._generation, self._cancelled, method, args
        )


class MusicAssistantController(QObject):
    """Owns QML-safe Assistant state without bypassing music-agent boundaries."""

    changed = Signal()
    windowRequested = Signal()
    windowCloseRequested = Signal()
    collaborationConfirmationRequested = Signal()
    audioImportRequested = Signal()
    proposalNoteLocateRequested = Signal(str, float, int, str)
    taskProgressChanged = Signal()
    conversationChanged = Signal()
    _generationProgress = Signal(int, object)
    _generationFinished = Signal(int, object, object)
    _generationTextDelta = Signal(int, str)
    _generationResponseStarted = Signal(int)
    _generationValidationRepair = Signal(int, int)
    _catalogRefreshFinished = Signal(int, str, object, object)
    _codexDetectionFinished = Signal(object)
    _musicToolVerificationFinished = Signal(str, str, object, object)
    _connectionProbeFinished = Signal(int, str, str, object, object)
    _gatewayCallRequested = Signal(object)
    _cancellationRequested = Signal()
    _pendingMessagesChanged = Signal(int)

    def __init__(
        self,
        editor: EditorController,
        *,
        playback: PlaybackController | None = None,
        gateway: MusicAgentToolGateway | None = None,
        proposal_manager: MusicEditProposalManager | None = None,
        provider_settings: ProviderSettingsService | None = None,
        agent_loop_factory: Callable[..., MusicAgentLoop] | None = None,
        history_path: Path | None = None,
        automatic_music: bool = False,
        require_song_confirmation: bool = False,
        model_driven_assistant: bool = False,
        creator_enabled: bool = False,
        appearance: QObject | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._appearance_tools = None
        self._creator_enabled = creator_enabled
        self._creator_views = []
        self._resource_sessions = {}
        self._require_song_confirmation = bool(require_song_confirmation)
        self._model_driven_assistant = model_driven_assistant and automatic_music and require_song_confirmation
        if appearance is not None:
            from ai_piano_studio.music_agent.style_tools import AppearanceTools
            self._appearance_tools = AppearanceTools(appearance)
        if not isinstance(editor, EditorController):
            raise ValueError("editor must be EditorController")
        self._editor = editor
        self._gateway = gateway or MusicAgentToolGateway(editor)
        if not self._gateway.is_bound_to(editor):
            raise ValueError("gateway must be bound to editor")
        self._proposal_manager = proposal_manager or MusicEditProposalManager(
            editor, self._gateway, playback=playback
        )
        self._app_capabilities = AssistantCapabilitySet.from_registry(self._gateway.registry)
        self._response_guard = ResponseCapabilityGuard()
        self._provider_settings = provider_settings or ProviderSettingsService(
            settings=self._default_settings(), credentials=KeyringCredentialStore()
        )
        self._window_settings = self._default_settings()
        from ai_piano_studio.music_agent.creator_tools import CreatorWorkspace
        self._creator_workspace = CreatorWorkspace(editor._projects.root.parent,
            Path(str(self._window_settings.value('assistant/creatorWorkspace',
                editor._projects.root.parent / 'assistant/workspace'))))
        self._agent_loop_factory = agent_loop_factory
        self._conversation_base = history_path
        self._conversation_key = self._project_conversation_key(editor.currentFilePath)
        self._conversation_path = editor.currentFilePath
        self._memory_conversations = {}
        self._conversation_drafts = {}
        # Presentation-only receipts shared by docked and detached workspaces.
        # No project content or conversation storage format is changed.
        self._task_display_receipts = {}
        history = ConversationHistory(history_path, project_key=self._conversation_key) if history_path else None
        self._messages = AssistantMessageModel(self, history=history)
        self._conversation_id = history.conversation_id if history and history.conversation_id else self._conversation_key
        self._load_task_memory()
        self._conversation_view = ConversationMessageView(self)
        self._conversation_view.setSourceModel(self._messages)
        self._task_message_start_row = len(self._messages.entries)
        self._messages.persistenceError.connect(self.changed)
        self._conversation_start_row = 0
        self._mode = "suggestion"
        self._assistant_status = "idle"
        self._validation_repair_count = 0
        self._provider_id = "openai"
        self._model_id = self._initial_model_id(self._provider_id)
        self._reasoning_id = "off"
        self._last_project_session_id = editor.projectSessionId
        self._generation = 0
        self._task_progress = TaskProgress(self, diagnostic_path=(
            history_path.parent / "task-diagnostics.jsonl" if history_path is not None else None))
        self._task_progress.changed.connect(self.taskProgressChanged)
        self._retry_instruction = ""
        self._retry_attachments = ()
        self._retry_allow_creative = False
        self._active_generation: int | None = None
        self._active_cancel: Event | None = None
        self._active_context: MusicAgentExecutionContext | None = None
        self._active_intent = ""
        self._active_extension_seconds: float | None = None
        self._pending_messages: dict[int, list[str]] = {}
        self._pending_messages_lock = Lock()
        self._suppressed_streams: set[int] = set()
        self._last_music_intent = ""
        self._pending_music_intent = ""
        self._pending_music_instruction = ""
        self._pending_music_intent_template = ""
        self._last_music_authorized_bars: tuple[BarRange, ...] = ()
        self._last_music_permissions: frozenset[MusicPermission] = frozenset()
        self._last_music_selected_note_ids: tuple[str, ...] = ()
        self._gateway_dispatch_in_progress = False
        self._streaming_message_rows: dict[int, int] = {}
        self._streaming_texts: dict[int, str] = {}
        self._catalog_refreshing = False
        self._catalog_error: tuple[str, str] | None = None
        self._codex_detecting = False
        self._codex_status: dict[str, object] = {"checked": False, "installed": False, "login": "", "version": "", "supported": False, "message": "正在等待检测…"}
        self._connection_testing = False
        self._connection_tested = False
        self._connection_probe_serial = 0
        self._music_tool_verifying = False
        self._music_tool_verification_provider_id = ""
        self._music_tool_verification_model_id = ""
        self._music_tool_verification_failure: tuple[str, str, ConnectionResult] | None = None
        self._creation_scope_mode = _CREATION_SCOPE_AUTOMATIC
        self._creation_scope_start_bar = 1
        self._creation_scope_end_bar = _EMPTY_PROJECT_INITIAL_BARS
        self._proposal_only_changes = False
        self._last_audio_import_summary = ""
        self._last_turn_result: dict[str, object] = {}
        self._workers: set[Thread] = set()
        self._worker_lock = Lock()
        self._shutting_down = False
        self._connection_result = ConnectionResult(
            code=self._connection_code_unknown(), message="尚未测试连接。"
        )
        self._editor.stateChanged.connect(self._on_editor_state)
        self._editor.currentFileChanged.connect(self._on_project_path_changed)
        self._editor.dirtyChanged.connect(self._on_editor_presentation_changed)
        self._editor.savingChanged.connect(self._on_editor_presentation_changed)
        self._generationProgress.connect(self._on_generation_progress, Qt.ConnectionType.QueuedConnection)
        self._generationFinished.connect(self._on_generation_finished)
        self._generationTextDelta.connect(self._on_generation_text_delta)
        self._generationResponseStarted.connect(self._on_generation_response_started)
        self._generationValidationRepair.connect(self._on_generation_validation_repair)
        self._catalogRefreshFinished.connect(self._on_catalog_refresh_finished)
        self._codexDetectionFinished.connect(self._on_codex_detection_finished)
        self._musicToolVerificationFinished.connect(self._on_music_tool_verification_finished)
        self._connectionProbeFinished.connect(self._on_connection_probe_finished)
        self._gatewayCallRequested.connect(
            self._on_gateway_call_requested, Qt.ConnectionType.QueuedConnection
        )
        self._cancellationRequested.connect(
            self._cancel_generation_on_controller_thread, Qt.ConnectionType.QueuedConnection
        )
        self._pendingMessagesChanged.connect(self._on_pending_messages_changed, Qt.ConnectionType.QueuedConnection)
        from ai_piano_studio.music_agent.creation_workflow import CreationWorkflow
        self._analysis_playback = playback
        self._creation_workflow = CreationWorkflow(self, playback) if automatic_music else None
        from ai_piano_studio.music_agent.source_confirmation_flow import SongSourceConfirmationFlow
        self._song_source_flow = (SongSourceConfirmationFlow(self)
                                  if automatic_music and require_song_confirmation else None)

    @Property('QVariantMap', notify=changed)
    def songSourceConfirmation(self):
        return self._song_source_flow.public() if self._song_source_flow else {}

    @Slot(result=bool)
    def confirmSongSource(self):
        return bool(self._song_source_flow and self._song_source_flow.confirm())

    @Slot(result=bool)
    def rejectSongSource(self):
        return bool(self._song_source_flow and self._song_source_flow.reject())

    @staticmethod
    def _default_settings() -> QSettings:
        return QSettings()

    @Slot()
    def _on_editor_presentation_changed(self):
        # A QObject receiver is disconnected when its C++ owner is deleted.
        # A bare Signal.emit Python callback can outlive that owner.
        self.changed.emit()

    @staticmethod
    def _connection_code_unknown():
        from ai_piano_studio.music_agent.provider_settings import ConnectionResultCode

        return ConnectionResultCode.UNKNOWN_ERROR

    @property
    def gateway(self) -> MusicAgentToolGateway:
        """Phase 8 dependency; never exposed to QML."""

        return self._gateway

    @property
    def proposal_manager(self) -> MusicEditProposalManager:
        """Phase 8 dependency; QML receives only projections and action slots."""

        return self._proposal_manager

    @Property(QObject, constant=True)
    def messages(self) -> AssistantMessageModel:
        return self._messages

    @Property(QObject, constant=True)
    def conversationMessages(self):
        return self._conversation_view

    @Property(str, notify=conversationChanged)
    def conversationId(self):
        return self._conversation_id

    @Property(str, notify=conversationChanged)
    def conversationDraft(self):
        return self._conversation_drafts.get(self._conversation_id, '')

    @Slot(str)
    def setConversationDraft(self, text):
        self._conversation_drafts[self._conversation_id] = text

    @Property('QVariantList', notify=changed)
    def taskNotes(self):
        return [m.text for m in self._messages.entries[self._task_message_start_row:]
                if m.kind in {AssistantMessageKind.SYSTEM_NOTICE, AssistantMessageKind.ACTIVITY}][-8:]

    @staticmethod
    def _project_conversation_key(path):
        return ('file:' + hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode('utf-8')).hexdigest()
                if path else 'draft:' + uuid4().hex)

    def _load_task_memory(self):
        from ai_piano_studio.music_agent.task_memory import TaskMemory
        self._task_memory_error = ""
        scopes = getattr(self, "_memory_task_scopes", {})
        self._memory_task_scopes = scopes
        try:
            self._task_memory = scopes.get(self._conversation_id) or TaskMemory(
                self._conversation_base, self._conversation_id)
        except (OSError, ValueError, sqlite3.Error):
            self._task_memory = TaskMemory(None, self._conversation_id)
            self._task_memory_error = "任务记忆无法读取或保存，本次仅保留在内存；原文件未删除。"
        scopes[self._conversation_id] = self._task_memory
        if not self._task_memory.state and not self._task_memory_error:
            for entry in self._messages.entries:
                if entry.kind is AssistantMessageKind.USER_MESSAGE:
                    intent = self._classify_intent(entry.text)
                    if intent in {"CREATE_MUSIC", "EDIT_MUSIC"}:
                        self._memory_accept(entry.text, intent, new_task=bool(intent == "CREATE_MUSIC"
                            and not any(w in entry.text for w in ("继续", "续写", "接着", "往下", "刚刚", "刚才"))))
            if self._task_memory.state:
                self._memory_record("needs_revalidation", {"reason": "legacy_chat_import_no_execution_authority"})

    def _memory_project_facts(self):
        state = self._editor.state
        notes = tuple(sorted(((n.id, n.pitch, n.start_time, n.duration, n.velocity,
            n.layer_id, n.instrument, n.release_mode, n.release_ms, n.track, n.channel)
            for n in state.active_notes), key=lambda item: item[0]))
        return {"revision": self._editor.projectRevision,
                "fingerprint": hashlib.sha256(repr((notes, state.tempo, state.layers, state.controls)).encode("utf-8")).hexdigest(),
                "durationSeconds": max((n.start_time + n.duration for n in state.active_notes), default=0),
                "playbackDurationSeconds": state.playback_duration,
                "noteCount": len(state.active_notes)}

    def _memory_accept(self, text, intent, *, new_task=False):
        try:
            self._task_memory.accept(text, intent, new_task=new_task)
        except (OSError, ValueError, sqlite3.Error):
            self._task_memory_error = "任务记忆保存失败，原记录保留；请检查本地存储。"

    def _accept_model_song_task(self, text, payload):
        from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds
        title, artist = payload['title'].strip(), payload['artist'].strip()
        if not title or any(c in title + artist for c in '\r\n'):
            return MusicToolResult.failure('set_song_task', 'validation_error', 'Provide a song title and optional artist on one line.')
        constraints = self._task_memory.state.get('constraints', {})
        continuing = payload['continuing'] and constraints.get('sourceMode') == 'existing_song'
        if continuing and not artist:
            artist = constraints.get('artist', '')
        try:
            seconds = requested_excerpt_seconds(text, default=payload.get('duration_seconds',
                constraints.get('targetSeconds', 15) if continuing else 15), unambiguous_only=True)
            changed = (not continuing or (title, artist, seconds) !=
                       (constraints.get('songTitle'), constraints.get('artist'), constraints.get('targetSeconds'))
                       or ('section' in payload and payload['section'] != constraints.get('section'))
                       or ('start_seconds' in payload and payload['start_seconds'] != constraints.get('sourceStartSeconds')))
            self._task_memory.accept_song(text, title, artist, seconds, continuing=continuing,
                                         section=payload.get('section'), start_seconds=payload.get('start_seconds'))
        except ValueError as error:
            return MusicToolResult.failure('set_song_task', 'validation_error', str(error))
        except (OSError, sqlite3.Error):
            return MusicToolResult.failure('set_song_task', 'execution_error', 'The application could not save the task.')
        if changed:
            if self._song_source_flow is not None:
                self._song_source_flow.clear()
                if not continuing:
                    self._song_source_flow.rejected_sources.clear()
            self.changed.emit()
        constraints = self._task_memory.state['constraints']
        data = dict(status='task_recorded', task_id=self._task_memory.state['taskId'],
                    version=self._task_memory.state['version'], title=title, artist=artist,
                    duration_seconds=seconds, section=constraints.get('section', ''))
        if 'sourceStartSeconds' in constraints:
            data['start_seconds'] = constraints['sourceStartSeconds']
        workflow = self._creation_workflow
        if workflow and workflow.current(self._active_generation) and workflow.gateway is not None:
            workflow.song_task = dict(data)
            workflow.reconstructing, workflow.original_draft, workflow.creative = True, False, False
        return MusicToolResult.success_result('set_song_task', 'Song task recorded.', data=data)

    def _memory_record(self, status, facts=None):
        try:
            current = self._memory_project_facts()
            target = self._task_memory.state.get("constraints", {}).get("targetSeconds")
            if status == "completed" and target and current["durationSeconds"] + .05 < target:
                status = "needs_revalidation"
            self._task_memory.record(status, {**current, **(facts or {})})
        except (OSError, ValueError, sqlite3.Error):
            self._task_memory_error = "任务记忆保存失败，原记录保留；请检查本地存储。"

    def _memory_reconcile(self):
        try:
            self._task_memory.reconcile(self._memory_project_facts())
        except (OSError, ValueError, sqlite3.Error):
            self._task_memory_error = "任务状态无法保存，请检查本地存储。"

    def _memory_context(self):
        self._memory_reconcile()
        return self._task_memory.context(self._memory_project_facts())

    @staticmethod
    def _is_task_resume(text):
        return bool(re.fullmatch(r"(?:请|那就|帮我)?(?:继续|接着|重试|继续完成|接着做)[吧。！!\s]*", text))

    def _resumed_creation_instruction(self):
        from ai_piano_studio.music_agent.song_resolver import DURATION_RE
        state = self._task_memory.state
        target = state.get("constraints", {}).get("targetSeconds")
        instruction = state.get("goal", "") + "\n" + "\n".join(state.get("instructions", [])[-8:])
        if target:
            instruction = DURATION_RE.sub(f"{target:g}秒", instruction)
        return instruction

    def _creation_model_history(self):
        history = self._conversation_history()
        workflow = self._creation_workflow
        if history and history[-1]["role"] == "user" and workflow and history[-1]["content"] == workflow.display_text:
            history = history[:-1]
        return history

    def _bind_project_conversation(self):
        with self._pending_messages_lock:
            self._pending_messages.clear()
        workflow = self._creation_workflow
        retaining_creation = bool(workflow and workflow.applying)
        if workflow and not retaining_creation:
            workflow.cancel()
        self._memory_conversations[self._conversation_key] = self._messages.entries
        key = self._project_conversation_key(self._editor.currentFilePath)
        history = ConversationHistory(self._conversation_base, project_key=key) if self._conversation_base else None
        self._messages.bind_history(history, self._memory_conversations.get(key, ()))
        self._conversation_key = key
        self._conversation_path = self._editor.currentFilePath
        self._conversation_id = history.conversation_id if history and history.conversation_id else key
        self._load_task_memory()
        self._conversation_start_row = 0
        self._task_message_start_row = len(self._messages.entries)
        self._streaming_message_rows.clear()
        self._streaming_texts.clear()
        self._retry_instruction = ''
        self._retry_attachments = ()
        if not retaining_creation:
            self._task_progress.clear()
        self.conversationChanged.emit()

    @Slot()
    def _on_project_path_changed(self):
        if self._creator_enabled and self._active_generation is not None:
            # A creator task can save/open several files while keeping its chat.
            # File-path presentation changes do not invalidate its music state.
            self.changed.emit()
            return
        if self._editor.projectSessionId != self._last_project_session_id:
            return  # A load/new operation is bound after its state refresh succeeds.
        path = self._editor.currentFilePath
        if path == self._conversation_path:
            return
        if not self._conversation_path and path:
            key = self._project_conversation_key(path)
            history = self._messages._history
            try:
                if history is not None and self._messages._history_readable:
                    history.promote(key)
                receipts = self._task_display_receipts.pop(self._conversation_key, None)
                if receipts is not None:
                    self._task_display_receipts[key] = receipts
                self._conversation_key = key
                self._conversation_path = path
            except (OSError, ValueError, sqlite3.Error):
                self._messages._report_history_error()
        else:
            self._cancel_if_active()
            self._last_music_intent = ''
            self._last_turn_result = {}
            self._bind_project_conversation()
        self.changed.emit()

    @Property(QObject, notify=changed)
    def songRecoveryTask(self):
        return getattr(self, '_song_recovery_task', None)

    def bind_song_recovery(self, task):
        self._song_recovery_task = task
        self._song_recovery_last = None
        self._song_recovery_muted_through = -1
        task.changed.connect(self._song_recovery_changed)
        self.changed.emit()

    def _song_recovery_changed(self):
        task = self._song_recovery_task
        # A cleared task can still emit a final done/idle while its owned
        # worker unwinds. Keep its announcements invalid until a newer task
        # generation begins; cancellation and later tasks still report normally.
        if task.taskGeneration <= self._song_recovery_muted_through:
            return
        marker = (task.taskGeneration, task.recoveryRounds, task.state)
        if marker == self._song_recovery_last:
            return
        self._song_recovery_last = marker
        if task.state == 'recovering':
            text = '找歌：' + task.query + '\n' + task.failure.get('message', '') + '\n' + task.status
        elif task.recoveryRounds and task.state in ('awaiting_confirmation', 'error', 'idle'):
            text = '找歌：' + task.query + '\n' + task.status
        else:
            return
        self._messages.append(AssistantMessage(AssistantMessageKind.SYSTEM_NOTICE, text, title='找歌协同'))
        self.changed.emit()

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Property(str, notify=changed)
    def assistantStatus(self) -> str:
        return self._assistant_status

    @Property(bool, notify=changed)
    def isBusy(self) -> bool:
        """Whether a provider/repair task is still running for the current turn."""

        return self._assistant_status in {"thinking", "tool_running", "repairing"}

    @Property("QVariantMap", notify=taskProgressChanged)
    def taskProgress(self) -> dict[str, object]:
        return self._task_progress.snapshot()

    @Property(int, notify=changed)
    def codexWaitSeconds(self) -> int:
        try:
            return max(120, min(1800, int(self._window_settings.value("assistant/codexWaitSeconds", 600))))
        except (TypeError, ValueError):
            return 600

    @Slot(int)
    def setCodexWaitSeconds(self, seconds: int) -> None:
        self._window_settings.setValue("assistant/codexWaitSeconds", max(120, min(1800, seconds)))
        self.changed.emit()

    @Property(int, notify=changed)
    def historyContextKB(self) -> int:
        default = MAX_CONVERSATION_HISTORY_BYTES // 1000
        try:
            value = int(self._window_settings.value("assistant/historyContextKB", default))
        except (TypeError, ValueError):
            return default
        return max(4, min(512, value))

    @Slot(int)
    def setHistoryContextKB(self, value: int) -> None:
        self._window_settings.setValue("assistant/historyContextKB", max(4, min(512, value)))
        self.changed.emit()

    @Property(bool, constant=True)
    def creatorEnabled(self):
        return self._creator_enabled

    @Property(int, notify=changed)
    def contextCharacters(self):
        try:
            return max(0, min(2147483647, int(self._window_settings.value('assistant/contextCharacters', 0))))
        except (TypeError, ValueError):
            return 0

    @Slot(int)
    def setContextCharacters(self, value):
        self._window_settings.setValue('assistant/contextCharacters', max(0, value))
        self.changed.emit()

    @Property(str, notify=changed)
    def creatorInstructions(self):
        from ai_piano_studio.music_agent.creator_tools import DEFAULT_CREATOR_INSTRUCTIONS
        return str(self._window_settings.value('assistant/creatorInstructions', DEFAULT_CREATOR_INSTRUCTIONS))

    @Property(bool, notify=changed)
    def creatorRecoveryReview(self):
        return self._window_settings.value('assistant/creatorRecoveryReview', True, type=bool)

    @Slot(bool)
    def setCreatorRecoveryReview(self, enabled):
        self._window_settings.setValue('assistant/creatorRecoveryReview', enabled)
        self.changed.emit()

    @Slot(str)
    def setCreatorInstructions(self, text):
        self._window_settings.setValue('assistant/creatorInstructions', text)
        self.changed.emit()

    @Property(str, notify=changed)
    def creatorRequestOptions(self):
        return str(self._window_settings.value('assistant/creatorRequestOptions', '{}'))

    @Slot(str)
    def setCreatorRequestOptions(self, text):
        try:
            options = json.loads(text)
            if not isinstance(options, dict):
                raise ValueError('请求参数必须是 JSON 对象。')
        except (ValueError, TypeError) as error:
            self._append_error(str(error))
            self.changed.emit()
            return
        self._window_settings.setValue('assistant/creatorRequestOptions', json.dumps(options, ensure_ascii=False))
        self.changed.emit()

    @Property(str, notify=changed)
    def creatorWorkspacePath(self):
        return str(self._creator_workspace.workspace)

    @Slot(str)
    def setCreatorWorkspacePath(self, path):
        workspace = Path(path).expanduser().resolve()
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._append_error(str(error))
            self.changed.emit()
            return
        self._creator_workspace.workspace = workspace
        self._window_settings.setValue('assistant/creatorWorkspace', str(self._creator_workspace.workspace))
        self.changed.emit()

    def _creator_facts(self, context):
        from ai_piano_studio.persistence.project_document import ProjectDocument, save_project_document
        self._creator_workspace.workspace.mkdir(parents=True, exist_ok=True)
        snapshot_id = hashlib.sha256(self._conversation_id.encode()).hexdigest()[:24]
        snapshot = self._creator_workspace.directory / f'project-{snapshot_id}.pianoai.json'
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        save_project_document(snapshot, ProjectDocument(self._editor.state))
        return dict(application='Flowing Piano', purpose='作曲、编曲与音乐制作',
            history_character_limit=self.contextCharacters,
            recovery_review=self.creatorRecoveryReview,
            instructions=self.creatorInstructions, workspace=self._creator_workspace.info(),
            current_project=dict(snapshot_file=str(snapshot), revision=self._editor.projectRevision,
                bpm=self._editor.state.tempo.bpm, duration_seconds=self._editor.state.playback_duration,
                note_count=len(self._editor.state.active_notes),
                selected_note_ids=[n.id for n in self._editor.state.selected_notes]),
            editing=dict(mode=context.mode.value, app_roots=['editor', 'assistant', 'playback', 'proposal', 'ui'],
                named_ui_object_syntax='ui:objectName.property',
                user_preferences=dict(edit_mode=self.mode, scope=self.creationScope)),
            preview=self.taskPreviewInfo if self.taskPreviewAvailable else None)

    def _send_creator_message(self, text, *, display_text='', attachments=(), record_user=True):
        from ai_piano_studio.music_agent.creator_app import fresh_context
        history = self._conversation_history()
        self._generation += 1
        generation = self._generation
        context = fresh_context(self)
        cancelled = Event()
        self._retry_instruction, self._retry_attachments = text, attachments
        self._active_extension_seconds = None
        self._task_message_start_row = len(self._messages.entries)
        self._active_generation, self._active_cancel, self._active_context = generation, cancelled, context
        self._active_intent = 'CHAT'
        self._task_progress.start(generation, display_text or text, self._provider_id, self._model_id)
        self._task_progress.set_model_driven(generation)
        if record_user:
            self._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, display_text or text))
        try:
            loop = self._create_agent_loop(generation, cancelled)
        except Exception as error:
            self._active_generation = self._active_cancel = self._active_context = None
            self._assistant_status = 'error'
            self._append_error(str(error))
            self._task_progress.finish(generation, 'failed', error_code='provider_unavailable')
            self.changed.emit()
            return False
        self._assistant_status = 'thinking'
        self.changed.emit()
        worker = Thread(target=self._run_generation,
            args=(generation, cancelled, loop, text, context, self.providerReasoningValue or None,
                  'CHAT', 'studio_creator', history, attachments), daemon=True, name='studio-creator-turn')
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        return True

    @Slot(result=bool)
    def retryTask(self) -> bool:
        if self.isBusy or not self._retry_instruction:
            return False
        if self._song_source_flow and self._song_source_flow.retry_confirmed("重试已确认的谱源"):
            return True
        return self._send_message(self._retry_instruction, intent_template="", attachments=self._retry_attachments,
                                  allow_creative=self._retry_allow_creative)

    def _creation_reply(self, text, *, error=False):
        if self._creator_enabled:
            self._messages.append(AssistantMessage(
                AssistantMessageKind.ERROR if error else AssistantMessageKind.SYSTEM_NOTICE, text))
            return
        row = self._creation_workflow.response_row
        if row is not None and any(m.kind is AssistantMessageKind.USER_MESSAGE
                for m in self._messages.entries[row + 1:]):
            row = None
        message = AssistantMessage(AssistantMessageKind.ERROR if error else AssistantMessageKind.ASSISTANT_MESSAGE, self._safe_text(text))
        if row is not None and 0 <= row < len(self._messages.entries):
            self._messages.replace(row, message)
        else:
            self._creation_workflow.response_row = len(self._messages.entries)
            self._messages.append(message)

    def _adopt_creation_conversation(self, path, start_row):
        entries = self._messages.entries[start_row:]
        key = self._project_conversation_key(str(path))
        self._memory_conversations[key] = entries
        task_id = self.taskProgress.get('taskId')
        receipts = {}
        for row, receipt in self._task_display_receipts.get(self._conversation_key, {}).items():
            if receipt.get('task', {}).get('taskId') == task_id and int(row) >= start_row:
                receipts[str(int(row) - start_row)] = dict(receipt, saved=True)
        self._task_display_receipts[key] = receipts
        if self._conversation_base:
            try:
                history = ConversationHistory(self._conversation_base, project_key=key)
                offset = len(history.load())
                for row, message in enumerate(entries, offset):
                    history.save(row, message.to_dict())
                target = self._task_memory.copy_to(history.conversation_id)
                self._memory_task_scopes[history.conversation_id] = target
            except (OSError, ValueError, sqlite3.Error):
                self._messages._report_history_error()
        else:
            self._memory_task_scopes[key] = self._task_memory.copy_to(key)

    @Property(bool, notify=changed)
    def taskPreviewAvailable(self):
        return bool(self._creation_workflow and self._creation_workflow.pending_song is not None)

    @Property(bool, notify=changed)
    def taskPreviewCurrent(self):
        return bool(self.taskPreviewAvailable and self._creation_workflow.result_generation == self.taskProgress.get('generationId'))

    @Property(bool, notify=changed)
    def taskPreviewPlayed(self):
        return bool(self.taskPreviewAvailable and self._creation_workflow.previewed)

    @Property(bool, notify=changed)
    def taskPreviewPlaying(self):
        return bool(self.taskPreviewAvailable and self._creation_workflow._preview_active)

    @Property(int, notify=changed)
    def taskMessageRow(self) -> int:
        """Source transcript row that owns the current task's result card."""
        workflow = self._creation_workflow
        if workflow and workflow.result_generation == self._generation and workflow.response_row is not None:
            row = workflow.response_row
            if workflow.result_path and self._editor.currentFilePath == workflow.result_path:
                row -= workflow.message_start_row
            if 0 <= row < len(self._messages.entries):
                return row
        for row in range(self._task_message_start_row, len(self._messages.entries)):
            if self._messages.entries[row].kind in {AssistantMessageKind.ASSISTANT_MESSAGE, AssistantMessageKind.ERROR}:
                return row
        return self._task_message_start_row if self._task_message_start_row < len(self._messages.entries) else -1

    @Property('QVariantMap', notify=conversationChanged)
    def taskDisplayReceipts(self):
        return dict(self._task_display_receipts.get(self._conversation_key, {}))

    @Slot('QVariantMap')
    def rememberTaskDisplayReceipts(self, receipts):
        self._task_display_receipts[self._conversation_key] = dict(receipts)

    @Property('QVariantMap', notify=changed)
    def taskPreviewInfo(self):
        if not self.taskPreviewAvailable:
            return {}
        workflow = self._creation_workflow
        song = workflow.pending_song
        return dict(title=song.title, noteCount=len(song.state.playback_notes), durationSeconds=song.state.playback_duration,
                    sourceKind=song.provenance.get('kind', ''), error=workflow.preview_error)

    @Property(bool, notify=changed)
    def taskCreativeAlternativeAvailable(self):
        return bool(self._creation_workflow and self._creation_workflow.alternative_instruction
                    and self.taskProgress.get('safeErrorCode') == 'source_unavailable')

    @Slot(result=bool)
    def auditionTask(self):
        return bool(self._creation_workflow and self._creation_workflow.audition())

    @Slot(result=bool)
    def applyTaskProject(self):
        return bool(self._creation_workflow and self._creation_workflow.apply())

    @Slot(result=bool)
    def discardTaskProject(self):
        return bool(self._creation_workflow and self._creation_workflow.discard())

    @Slot()
    def stopTaskAudition(self):
        if self._creation_workflow:
            self._creation_workflow.stop_audition()

    @Slot(result=bool)
    def generateOriginalAlternative(self):
        if not self.taskCreativeAlternativeAvailable or self.isBusy:
            return False
        instruction = self._creation_workflow.alternative_instruction
        return self._send_message(instruction, intent_template='', allow_creative=True,
                                  display_text='我选择生成原创替代：' + instruction)

    def _append_user_music_request(self, text: str) -> None:
        self._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, text))

    @Property(bool, notify=changed)
    def taskProjectAvailable(self) -> bool:
        return bool(self._creation_workflow and self._creation_workflow.result_path
                    and self._creation_workflow.result_generation == self.taskProgress.get("generationId"))

    @Slot()
    def openTaskProject(self) -> None:
        if self._creation_workflow:
            self._creation_workflow.open_result()

    @Slot(int, object)
    def _on_generation_progress(self, generation: int, event: object) -> None:
        if generation == self._active_generation and isinstance(event, ProviderProgressEvent):
            self._task_progress.record(generation, event)

    @Property(str, notify=changed)
    def assistantProgressText(self) -> str:
        if self._assistant_status == "repairing":
            return f"正在修正 {self._validation_repair_count} 项音乐参数…"
        if self._assistant_status == "tool_running":
            return "正在执行音乐操作…"
        if self._assistant_status == "thinking":
            return "正在理解创作要求…"
        return ""

    @Property(bool, notify=changed)
    def proposalOnlyChanges(self) -> bool:
        """Whether the editor should visually de-emphasize unchanged notes."""

        return self._proposal_only_changes

    @Slot(bool)
    def setProposalOnlyChanges(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._proposal_only_changes:
            return
        self._proposal_only_changes = enabled
        self.changed.emit()

    @Property(bool, notify=changed)
    def canGenerateAdvicePreview(self) -> bool:
        """Expose a local advice-to-Proposal action only when it can really write."""

        return bool(
            self._pending_music_intent in {"CREATE_MUSIC", "EDIT_MUSIC"}
            and self._pending_music_instruction
            and self._assistant_status == "idle"
            and self.currentModelStatus.get("canCreate") is True
        )

    @Property(int, notify=changed)
    def advicePreviewMessageRow(self) -> int:
        if not self.canGenerateAdvicePreview:
            return -1
        for index in range(len(self._messages.entries) - 1, -1, -1):
            if self._messages.entries[index].kind is AssistantMessageKind.ASSISTANT_MESSAGE:
                return index
        return -1

    @Property(str, notify=changed)
    def currentProviderId(self) -> str:
        return self._provider_id

    @Property(str, notify=changed)
    def currentModelId(self) -> str:
        return self._model_id

    @Property(str, notify=changed)
    def currentReasoningId(self) -> str:
        return self._reasoning_id

    @Property(str, notify=changed)
    def providerReasoningValue(self) -> str:
        if self._reasoning_id == "off":
            return ""
        supported = {option.id for option in self._capabilities().reasoning_options}
        for choice_id, _label, provider_values in _REASONING_CHOICES:
            if choice_id == self._reasoning_id:
                return next((value for value in provider_values if value in supported), "")
        return ""

    @Property("QVariantList", notify=changed)
    def providerCatalog(self) -> list[dict[str, object]]:
        return self._provider_settings.connection_profiles()

    @Property("QVariantList", notify=changed)
    def connectionProfiles(self) -> list[dict[str, object]]:
        """Expose safe connection choices; credential material remains in CredentialStore."""
        return self._provider_settings.connection_profiles()

    @Property(str, notify=changed)
    def catalogSortMode(self) -> str:
        return self._provider_settings.catalog_sort_mode

    @Slot(str, result=bool)
    def setCatalogSortMode(self, mode: str) -> bool:
        if not self._provider_settings.set_catalog_sort_mode(mode):
            return False
        self.changed.emit()
        return True

    @Property("QVariantList", notify=changed)
    def modelCatalog(self) -> list[dict[str, object]]:
        return self._provider_settings.model_catalog(self._provider_id)

    @Property("QVariantList", notify=changed)
    def reasoningCatalog(self) -> list[dict[str, object]]:
        supported = {option.id for option in self._capabilities().reasoning_options}
        catalog: list[dict[str, object]] = []
        for choice_id, label, provider_values in _REASONING_CHOICES:
            provider_value = next((value for value in provider_values if value in supported), "")
            catalog.append(
                {
                    "id": choice_id,
                    "label": label,
                    "enabled": choice_id == "off" or bool(provider_value),
                    "providerValue": provider_value,
                }
            )
        return catalog

    @Property(bool, notify=changed)
    def catalogRefreshing(self) -> bool:
        return self._catalog_refreshing

    @Property(str, notify=changed)
    def modelCatalogError(self) -> str:
        return self._catalog_error[1] if self._catalog_error and self._catalog_error[0] == self._provider_id else ""

    @Property(bool, notify=changed)
    def codexDetecting(self) -> bool:
        return self._codex_detecting

    @Property("QVariantMap", notify=changed)
    def codexStatus(self) -> dict[str, object]:
        return dict(self._codex_status)

    @Slot(result=bool)
    def detectCodexAsync(self) -> bool:
        if self._shutting_down or self._codex_detecting:
            return False
        self._codex_detecting = True
        worker = Thread(target=self._run_codex_detection, name="music-agent-codex-detection", daemon=True)
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        self.changed.emit()
        return True

    def _run_codex_detection(self) -> None:
        try:
            result = detect_codex(cancelled=lambda: self._shutting_down)
        except Exception:
            result = {"installed": False, "supported": False, "login": "", "version": "", "message": "Codex 检测失败，请重新检测。"}
        if not self._shutting_down:
            self._codexDetectionFinished.emit(result)
        with self._worker_lock:
            self._workers.discard(current_thread())

    @Slot(object)
    def _on_codex_detection_finished(self, result: object) -> None:
        self._codex_detecting = False
        if self._shutting_down:
            return
        previous = tuple(self._codex_status.get(key) for key in ("installed", "login", "supported"))
        self._codex_status = {**result, "checked": True}
        self._provider_settings.update_codex_status(self._codex_status)
        if self._provider_id == "codex" and previous != tuple(self._codex_status.get(key) for key in ("installed", "login", "supported")):
            self._connection_probe_serial += 1
            self._connection_tested = False
            self._connection_result = ConnectionResult(self._connection_code_unknown(), "Codex 状态已更新，尚未测试连接。")
        self.changed.emit()

    @Property(bool, notify=changed)
    def connectionTesting(self) -> bool:
        return self._connection_testing

    @Property(bool, notify=changed)
    def connectionTested(self) -> bool:
        return self._connection_tested

    @Property("QVariantMap", notify=changed)
    def currentCapabilities(self) -> dict[str, object]:
        model = self._capabilities()
        effective = EffectiveCapabilitySet.from_sources(
            self._app_capabilities,
            model,
            self._music_permissions(),
        )
        capabilities = model.to_dict()
        capabilities["app"] = self._app_capabilities.to_dict()
        capabilities["effective"] = effective.to_dict()
        capabilities["supportsChat"] = True
        capabilities["canUseMusicTools"] = bool(effective.music_read or effective.music_write)
        capabilities["canUseCompatibilityMode"] = bool(capabilities["supportsStructuredOutput"])
        return capabilities

    @Property("QVariantMap", notify=changed)
    def lastTurnResult(self) -> dict[str, object]:
        """Expose the last turn's real execution evidence, not provider prose."""

        return dict(self._last_turn_result)

    @Property(str, notify=changed)
    def executionSummary(self) -> str:
        """UI receipt based on gateway results and current save state."""
        turn = self._last_turn_result
        if not turn:
            return ""
        if turn.get("status") == "preview_ready":
            source = turn.get("provenance", {})
            return f"新作品已准备：{source.get('title', '新作品')} · {turn.get('noteCount', 0)} 个音符\n尚未应用或保存为新工程；请先试听，再决定是否采用。"
        if turn.get("savedPath"):
            source = turn.get("provenance", {})
            kind = source.get("kind", "")
            label = {"licensed_score_midi": "授权乐谱", "ai_creative_unverified": "AI 创意演绎／未验证原曲",
                     "ai_original": "AI 原创", "automatic_audio_transcription": "音频自动转谱（待核对）",
                     "local_digital_score": "用户数字乐谱", "local_user_reference": "用户本地 MIDI"}.get(kind, "音乐资料")
            lines = [f"已创建并保存：{source.get('title', '新作品')} · {turn.get('noteCount', 0)} 个音符",
                     f"来源：{label}" + (f" · {source['license']}" if source.get("license") else "")]
            if source.get("qualityWarning"):
                lines.append(str(source["qualityWarning"]))
            lines.append("已启动本地试听" if turn.get("playbackStarted") else "作品可打开后播放")
            return "\n".join(lines)
        facts = turn.get("executionEvidence", turn)
        label = {"CHAT": "对话", "READ_MUSIC": "读取音乐", "EDIT_MUSIC": "修改音乐",
                 "CREATE_MUSIC": "创作音乐"}.get(facts.get("intent"), "音乐操作")
        lines = [f"本轮：{label} · 工具调用 {facts.get('toolCallCount', 0)} 次 · "
                 f"成功读取 {facts.get('readCount', 0)} 次 · 成功修改 {facts.get('writeCount', 0)} 次"]
        reads = facts.get("noteReads", [])
        if len(reads) > 3:
            lines.append(f"共读取 {len(reads)} 个小节范围，最近 3 次：")
        for read in reads[-3:]:
            lines.append(f"第 {read['startBar']}–{read['endBar']} 小节：读取 {read['count']} 个相交音符")
        if facts.get("projectNoteCount") is not None:
            lines.append(f"读取时工程共有 {facts['projectNoteCount']} 个活动音符")
        applied = turn.get("committed") or turn.get("proposalStatus") == "applied"
        staged = turn.get("proposalStatus") == "pending"
        if facts.get("writeCount", 0):
            state = "已应用" if applied else "提案待应用" if staged else "未应用或已撤回"
            lines.append(f"{state}：新增 {facts.get('notesAdded', 0)} / "
                         f"更新 {facts.get('notesUpdated', 0)} / 删除 {facts.get('notesDeleted', 0)} 个音符（操作计数）")
        elif turn.get("status") == "needs_input":
            lines.append("等待补充素材或范围，本轮未修改工程")
        if turn.get("auditionStarted"):
            lines.append("已启动本地试听")
        if self._editor.isSaving:
            lines.append("工程正在保存…")
        elif self._editor.isDirty:
            lines.append("工程有未保存修改，请使用保存按钮")
        else:
            lines.append("工程已保存" if self._editor.currentFilePath else "工程尚未保存为文件")
        return "\n".join(lines)

    @Property("QVariantMap", notify=changed)
    def currentModelStatus(self) -> dict[str, object]:
        """Describe the selected model without treating an unknown result as chat-only."""

        if self._creator_enabled:
            return dict(state='ready' if self._model_id else 'unverified',
                title='执行接口已开放' if self._model_id else '请选择模型',
                detail='模型可使用音乐工具、代码和命令，自行安排流程。实际服务能力以请求结果为准。',
                actionLabel='可直接开始', canCreate=bool(self._model_id), canVerify=False)

        if self._provider_id == "codex":
            return {"state": "ready" if self._model_id else "unverified", "title": "音乐工具已接入" if self._model_id else "请选择模型",
                    "detail": "音乐修改通过工作室提案预览并确认。检测与同步不会发送创作请求。",
                    "actionLabel": "无需额外验证", "canCreate": bool(self._model_id), "canVerify": False}

        if (
            self._music_tool_verifying
            and self._music_tool_verification_provider_id == self._provider_id
            and self._music_tool_verification_model_id == self._model_id
        ):
            return {
                "state": "verifying",
                "title": "正在验证创作能力…",
                "detail": "正在检查该模型能否调用音乐工具，请稍候。",
                "actionLabel": "正在验证…",
                "canCreate": False,
                "canVerify": False,
            }
        if (
            self._music_tool_verification_failure is not None
            and self._music_tool_verification_failure[:2] == (self._provider_id, self._model_id)
        ):
            return {
                "state": "verification_failed",
                "title": "创作验证失败",
                "detail": self._music_tool_verification_failure[2].message,
                "actionLabel": "重新验证",
                "canCreate": False,
                "canVerify": True,
            }
        capability_state = self._provider_settings.capability_state(self._provider_id, self._model_id)
        if capability_state == "known_supported":
            return {
                "state": "ready",
                "title": "MIDI 编辑可用",
                "detail": "音乐工具已验证，可读取和编辑音符、BPM、和弦；不能生成或发送音频文件。",
                "actionLabel": "无需验证",
                "canCreate": True,
                "canVerify": False,
            }
        if capability_state == "known_unsupported":
            return {
                "state": "chat_only",
                "title": "仅聊天",
                "detail": "该模型已确认不支持音乐创作工具，仍可聊天。",
                "actionLabel": "不支持创作",
                "canCreate": False,
                "canVerify": False,
            }
        return {
            "state": "unverified",
            "title": "创作能力待验证",
            "detail": "尚未验证该模型的连接与音乐工具能力。",
            "actionLabel": "验证创作能力",
            "canCreate": False,
            "canVerify": True,
        }

    @Property("QVariantMap", notify=changed)
    def creationScope(self) -> dict[str, object]:
        """Expose the next creation authorization before a provider turn begins."""

        return self._creation_scope_map("")

    @Slot(str, result="QVariantMap")
    def previewCreationScope(self, instruction: str) -> dict[str, object]:
        """Preview the exact local authorization for the text currently being composed."""

        return self._creation_scope_map(instruction if isinstance(instruction, str) else "")

    @Slot("QVariant", "QVariant", result=bool)
    def setCreationScopeBars(self, start_bar: object, end_bar: object) -> bool:
        """Set an explicit local creation range without contacting a provider."""

        try:
            start, end = int(start_bar), int(end_bar)
        except (TypeError, ValueError, OverflowError):
            return False
        if start < 1 or end < start:
            return False
        self._creation_scope_mode = _CREATION_SCOPE_MANUAL
        self._creation_scope_start_bar = start
        self._creation_scope_end_bar = end
        self.changed.emit()
        return True

    @Slot()
    def setCreationScopeAutomatic(self) -> None:
        """Return creation authorization to the unrestricted default mode."""

        if self._creation_scope_mode != _CREATION_SCOPE_AUTOMATIC:
            self._creation_scope_mode = _CREATION_SCOPE_AUTOMATIC
            self.changed.emit()

    @Property("QVariantMap", notify=changed)
    def projectContext(self) -> dict[str, object]:
        """Expose only human-readable project facts needed by the Assistant header."""
        state = self._editor.state
        tempo = state.tempo
        selected = state.selected_notes
        if selected:
            starts = [note.start_time for note in selected]
            ends = [note.start_time + note.duration for note in selected]
            bar_seconds = tempo.bar_duration_seconds
            start_bar = max(1, int(min(starts) // bar_seconds) + 1)
            end_bar = max(1, int(max(ends) // bar_seconds) + 1)
            range_text = f"当前范围：第 {start_bar}–{end_bar} 小节"
        else:
            range_text = "当前范围：整首工程" if state.active_notes else "从第 1 小节开始"
        return {
            "title": self._editor.currentFileName,
            "bpm": tempo.bpm,
            "timeSignature": f"{tempo.beats_per_bar}/{tempo.beat_unit}",
            "rangeText": range_text,
            "hasSelection": bool(selected),
        }

    @Property("QVariantMap", notify=changed)
    def proposal(self) -> dict[str, object]:
        proposal = self._proposal_manager.proposal
        summary = self._proposal_manager.summary
        if proposal is None or summary is None:
            return {
                "available": False,
                "status": "",
                "statusText": "",
                "proposalId": "",
                "notesAdded": 0,
                "notesUpdated": 0,
                "notesDeleted": 0,
                "barStart": 0,
                "barEnd": 0,
                "bpmBefore": None,
                "bpmAfter": None,
                "harmonyChangeCount": 0,
                "intentTemplate": "",
                "intentLabel": "按当前请求",
            }
        bars = summary.affected_bars
        return {
            "available": True,
            "proposalId": proposal.proposal_id,
            "status": proposal.status.value,
            "statusText": self._proposal_status_text(proposal.status),
            "notesAdded": summary.notes_added,
            "notesUpdated": summary.notes_updated,
            "notesDeleted": summary.notes_deleted,
            "barStart": bars.start_bar if bars is not None else 0,
            "barEnd": bars.end_bar if bars is not None else 0,
            "bpmBefore": summary.bpm_before,
            "bpmAfter": summary.bpm_after,
            "harmonyChangeCount": len(summary.harmony_changes),
            "intentTemplate": proposal.intent_template,
            "intentLabel": self._intent_label(proposal.intent_template),
        }

    @Property("QVariantMap", notify=changed)
    def proposalChanges(self) -> dict[str, object]:
        summary = self._proposal_manager.summary
        changes = self._proposal_manager.changes
        if summary is None or changes is None:
            return {"available": False}
        return {
            "available": True,
            "summary": self._summary_dict(summary),
            "inspector": self._proposal_inspector_for_ui(),
            "added": [self._note_change_dict(change) for change in changes.added],
            "updated": [self._note_change_dict(change) for change in changes.updated],
            "deleted": [self._note_change_dict(change) for change in changes.deleted],
            "harmony": [
                {
                    "kind": change.kind,
                    "measureIndex": change.measure_index + 1,
                    "chordSymbol": change.chord_symbol,
                    "generatedNoteCount": change.generated_note_count,
                }
                for change in changes.harmony
            ],
        }

    def _proposal_inspector_for_ui(self) -> dict[str, object]:
        inspector = dict(self._proposal_manager.inspector)
        if not inspector.get("available"):
            return inspector
        proposal = self._proposal_manager.proposal
        intent_template = proposal.intent_template if proposal is not None else ""
        summary = dict(inspector.get("summary", {}))
        inspector["intent"] = {
            "template": intent_template,
            "label": self._intent_label(intent_template),
        }
        inspector["actual"] = {
            "headline": str(inspector.get("headline", "")),
            "summary": summary,
        }
        inspector["qualityWarning"] = self._proposal_quality_warning(
            intent_template, summary
        )
        return inspector

    @Slot(str)
    def locateProposalNote(self, note_id: str) -> None:
        """Ask the editor surface to reveal a changed note without mutating it."""

        if not isinstance(note_id, str) or not note_id:
            return
        inspector = self._proposal_manager.inspector
        details_by_kind = inspector.get("details", {})
        for kind in ("added", "updated", "deleted"):
            for detail in details_by_kind.get(kind, []):
                technical = detail.get("technical", {})
                if str(technical.get("noteId", "")) != note_id:
                    continue
                note = detail.get("afterNote") or detail.get("beforeNote")
                if not note:
                    return
                self.proposalNoteLocateRequested.emit(
                    note_id,
                    float(note.get("startBeats", note.get("start", 0.0))),
                    int(note.get("pitch", 60)),
                    kind,
                )
                return

    @Property("QVariantMap", notify=changed)
    def connectionResult(self) -> dict[str, str]:
        return self._connection_result.to_dict()

    @Slot()
    def openWindow(self) -> None:
        self.windowRequested.emit()

    @Slot()
    def closeWindow(self) -> None:
        self.windowCloseRequested.emit()

    @Slot()
    def requestAudioImport(self) -> None:
        """Open the local audio importer without contacting a Provider."""
        self.audioImportRequested.emit()

    @Slot(str)
    def recordAudioImportSummary(self, summary: str) -> None:
        """Show a compact local-analysis result and attach it to the next AI turn."""
        value = str(summary or "").strip()[:1_000]
        if not value:
            return
        self._last_audio_import_summary = value
        self._append_notice("歌曲已导入，可继续编辑。\n" + value)
        self.changed.emit()

    @Slot(result="QVariantMap")
    def restoreWindowGeometry(self) -> dict[str, int]:
        """Return the saved normal assistant geometry or a visible default."""
        try:
            saved = self._saved_window_geometry()
            if saved is not None:
                fitted = self._fit_geometry_to_a_screen(saved)
                if fitted is not None:
                    visible_geometry, available = fitted
                    return self._geometry_map(
                        visible_geometry,
                        minimum_width=min(_ASSISTANT_WINDOW_MINIMUM_WIDTH, available.width()),
                        minimum_height=min(_ASSISTANT_WINDOW_MINIMUM_HEIGHT, available.height()),
                    )
        except Exception:
            logger.exception("Unable to restore assistant window geometry")
        return self._default_window_geometry()

    @Slot("QVariant", "QVariant", "QVariant", "QVariant")
    def saveWindowGeometry(self, x: object, y: object, width: object, height: object) -> None:
        """Persist only a normal, QML-validated assistant window rectangle."""
        try:
            geometry = QRect(
                int(x),
                int(y),
                max(_ASSISTANT_WINDOW_MINIMUM_WIDTH, int(width)),
                max(_ASSISTANT_WINDOW_MINIMUM_HEIGHT, int(height)),
            )
            settings = self._window_settings
            settings.setValue(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}X", geometry.x())
            settings.setValue(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}Y", geometry.y())
            settings.setValue(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}Width", geometry.width())
            settings.setValue(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}Height", geometry.height())
        except (TypeError, ValueError, OverflowError):
            logger.warning("Ignoring invalid assistant window geometry")
        except Exception:
            logger.exception("Unable to save assistant window geometry")

    def _saved_window_geometry(self) -> QRect | None:
        settings = self._window_settings
        values = (
            settings.value(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}X"),
            settings.value(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}Y"),
            settings.value(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}Width"),
            settings.value(f"{_ASSISTANT_WINDOW_SETTINGS_PREFIX}Height"),
        )
        if any(value is None for value in values):
            return None
        try:
            x, y, width, height = (int(value) for value in values)
        except (TypeError, ValueError, OverflowError):
            logger.warning("Ignoring invalid saved assistant window geometry")
            return None
        if width <= 0 or height <= 0:
            logger.warning("Ignoring non-positive saved assistant window geometry")
            return None
        return QRect(
            x,
            y,
            max(_ASSISTANT_WINDOW_MINIMUM_WIDTH, width),
            max(_ASSISTANT_WINDOW_MINIMUM_HEIGHT, height),
        )

    @staticmethod
    def _geometry_map(
        geometry: QRect, *, minimum_width: int | None = None, minimum_height: int | None = None
    ) -> dict[str, int]:
        result = {
            "x": geometry.x(),
            "y": geometry.y(),
            "width": geometry.width(),
            "height": geometry.height(),
        }
        if minimum_width is not None:
            result["minimumWidth"] = minimum_width
        if minimum_height is not None:
            result["minimumHeight"] = minimum_height
        return result

    @staticmethod
    def _fit_geometry_to_a_screen(geometry: QRect) -> tuple[QRect, QRect] | None:
        for screen in QGuiApplication.screens():
            available = screen.availableGeometry()
            if not geometry.intersects(available):
                continue
            width = min(geometry.width(), available.width())
            height = min(geometry.height(), available.height())
            x = min(max(geometry.x(), available.x()), available.x() + available.width() - width)
            y = min(max(geometry.y(), available.y()), available.y() + available.height() - height)
            return QRect(x, y, width, height), available
        return None

    @staticmethod
    def _default_window_geometry() -> dict[str, int]:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return {
                "x": 0,
                "y": 0,
                "width": _ASSISTANT_WINDOW_DEFAULT_WIDTH,
                "height": _ASSISTANT_WINDOW_DEFAULT_HEIGHT,
            }
        available = screen.availableGeometry()
        width = min(_ASSISTANT_WINDOW_DEFAULT_WIDTH, available.width())
        height = min(_ASSISTANT_WINDOW_DEFAULT_HEIGHT, available.height())
        return {
            "x": available.x() + max(0, (available.width() - width) // 2),
            "y": available.y() + max(0, (available.height() - height) // 2),
            "width": width,
            "height": height,
            "minimumWidth": min(_ASSISTANT_WINDOW_MINIMUM_WIDTH, available.width()),
            "minimumHeight": min(_ASSISTANT_WINDOW_MINIMUM_HEIGHT, available.height()),
        }

    @Slot()
    def requestCollaborationMode(self) -> None:
        if self._mode == "suggestion":
            self.collaborationConfirmationRequested.emit()

    @Slot()
    def confirmCollaborationMode(self) -> None:
        if self._mode != "collaboration":
            self._mode = "collaboration"
            self._append_notice("协作模式已开启。本轮 AI 修改仍可用 Ctrl+Z 一次撤销。")
            self.changed.emit()

    @Slot()
    def returnToSuggestionMode(self) -> None:
        if self._mode != "suggestion":
            self._mode = "suggestion"
            self._append_notice("已切回建议模式。")
            self.changed.emit()

    @Slot(str, result=bool)
    def selectProvider(self, provider_id: str) -> bool:
        try:
            config = self._provider_settings.public_config(provider_id)
        except ValueError:
            return False
        self._connection_probe_serial += 1
        self._catalog_error = None
        self._connection_tested = False
        self._cancel_if_active()
        self._clear_music_tool_verification_feedback()
        configured_model_id = str(config["modelId"])
        self._provider_id = provider_id
        self._model_id = self._catalog_model_id(provider_id, configured_model_id)
        self._reasoning_id = "off"
        # A result from another endpoint must never label this connection.
        self._connection_result = ConnectionResult(
            code=self._connection_code_unknown(), message="尚未测试连接。"
        )
        self._set_model_unavailable_result(configured_model_id, self._model_id)
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def selectModel(self, model_id: str) -> bool:
        if not isinstance(model_id, str) or not model_id.strip():
            return False
        selected_model_id = model_id.strip()
        if selected_model_id not in {
            str(item["id"]) for item in self._provider_settings.model_catalog(self._provider_id)
        }:
            return False
        self._cancel_if_active()
        self._clear_music_tool_verification_feedback()
        if self._model_id != selected_model_id:
            self._connection_probe_serial += 1
            self._connection_tested = False
            self._connection_result = ConnectionResult(
                self._connection_code_unknown(), "已切换模型，尚未测试连接。"
            )
        self._model_id = selected_model_id
        if self._assistant_status == "error":
            self._assistant_status = "idle"
        if not self.providerReasoningValue:
            self._reasoning_id = "off"
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def selectReasoning(self, reasoning_id: str) -> bool:
        choice = next((item for item in self.reasoningCatalog if item["id"] == reasoning_id), None)
        if choice is None or not bool(choice["enabled"]):
            return False
        self._reasoning_id = reasoning_id
        self.changed.emit()
        return True

    @Slot(result="QVariantList")
    def refreshModelCatalog(self) -> list[dict[str, object]]:
        """Refresh only an explicit provider catalog; Phase 8 owns when to call it."""

        self._cancel_if_active()
        try:
            catalog = self._provider_settings.refresh_models(self._provider_id)
        except BaseException as error:
            self._connection_result = map_connection_error(error)
            self.changed.emit()
            return self.modelCatalog
        self._model_id = self._catalog_model_id(self._provider_id, self._model_id)
        self.changed.emit()
        return catalog

    @Slot(result="QVariantMap")
    def verifyMusicTools(self) -> dict[str, str]:
        """Run the existing bounded, harmless provider capability probe on user request."""
        self._connection_tested = True
        self._connection_result = self._provider_settings.verify_music_tools(
            self._provider_id, self._model_id
        )
        self._update_music_tool_verification_result(
            self._provider_id, self._model_id, self._connection_result
        )
        if self._connection_result.code.value == "SUCCESS" and self._assistant_status == "error":
            self._assistant_status = "idle"
        self.changed.emit()
        return self.connectionResult

    @Slot(result=bool)
    def verifyMusicToolsAsync(self) -> bool:
        """Verify only a user-selected model without freezing the Assistant window."""

        if self._shutting_down or self._music_tool_verifying:
            return False
        if self._provider_settings.capability_state(self._provider_id, self._model_id) == "known_unsupported":
            return False
        provider_id, model_id = self._provider_id, self._model_id
        self._connection_tested = True
        try:
            provider = self._provider_settings.create_provider(provider_id, model_id=model_id)
        except BaseException as error:
            self._connection_result = map_connection_error(error)
            self._update_music_tool_verification_result(provider_id, model_id, self._connection_result)
            self.changed.emit()
            return False
        self._music_tool_verifying = True
        self._music_tool_verification_provider_id = provider_id
        self._music_tool_verification_model_id = model_id
        self._music_tool_verification_failure = None
        worker = Thread(
            target=self._run_music_tool_verification,
            args=(provider_id, model_id, provider),
            name="music-agent-capability-verification",
            daemon=True,
        )
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        self.changed.emit()
        return True

    def _run_music_tool_verification(self, provider_id: str, model_id: str, provider: object) -> None:
        capabilities: object = None
        error: BaseException | None = None
        try:
            verifier = getattr(provider, "verify_tool_capability", None)
            if not callable(verifier):
                raise ProviderError("selected model does not support streaming tool calls")
            capabilities = verifier()
            if not isinstance(capabilities, ModelCapabilities):
                raise ProviderError("selected model does not support streaming tool calls")
        except BaseException as caught:
            logger.warning("Assistant music tool verification failed")
            error = caught
        self._musicToolVerificationFinished.emit(provider_id, model_id, capabilities, error)
        with self._worker_lock:
            self._workers.discard(current_thread())

    @Slot(str, str, object, object)
    def _on_music_tool_verification_finished(
        self, provider_id: str, model_id: str, capabilities: object, error: object
    ) -> None:
        is_current_verification = (
            self._music_tool_verifying
            and self._music_tool_verification_provider_id == provider_id
            and self._music_tool_verification_model_id == model_id
        )
        if is_current_verification:
            self._music_tool_verifying = False
            self._music_tool_verification_provider_id = ""
            self._music_tool_verification_model_id = ""
        if self._shutting_down or provider_id != self._provider_id or model_id != self._model_id:
            if is_current_verification:
                self.changed.emit()
            return
        if isinstance(error, BaseException):
            self._connection_result = map_connection_error(error)
        elif isinstance(capabilities, ModelCapabilities):
            try:
                self._provider_settings.record_capability_verification(provider_id, model_id, capabilities)
                from ai_piano_studio.music_agent.provider_settings import ConnectionResultCode

                self._connection_result = ConnectionResult(
                    ConnectionResultCode.SUCCESS,
                    "已验证该模型支持流式音乐工具调用。"
                    if capabilities.supports_tools
                    else "已确认该模型不支持音乐创作工具，仍可聊天。",
                )
            except BaseException as caught:
                self._connection_result = map_connection_error(caught)
        else:
            self._connection_result = map_connection_error(
                ProviderError("selected model does not support streaming tool calls")
            )
        self._update_music_tool_verification_result(provider_id, model_id, self._connection_result)
        if self._connection_result.code.value == "SUCCESS" and self._assistant_status == "error":
            self._assistant_status = "idle"
        self.changed.emit()

    @Slot(result=bool)
    def refreshModelCatalogAsync(self) -> bool:
        """Refresh the selected provider catalog without blocking the Assistant UI."""

        if self._shutting_down or self._catalog_refreshing:
            return False
        provider_id = self._provider_id
        serial = self._connection_probe_serial
        self._catalog_error = None
        try:
            provider = self._provider_settings.create_provider(provider_id)
            if isinstance(provider, CodexProvider):
                provider.is_cancelled = lambda: self._shutting_down
        except BaseException as error:
            logger.warning("Assistant model catalog refresh could not start")
            self._connection_result = map_connection_error(error)
            self._catalog_error = (provider_id, self._connection_result.message)
            self.changed.emit()
            return False
        self._catalog_refreshing = True
        worker = Thread(
            target=self._run_model_catalog_refresh,
            args=(serial, provider_id, provider),
            name="music-agent-model-catalog",
            daemon=True,
        )
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        self.changed.emit()
        return True

    def _run_model_catalog_refresh(self, serial: int, provider_id: str, provider: object) -> None:
        models: object = ()
        error: BaseException | None = None
        try:
            models = tuple(provider.list_models())
        except BaseException as caught:
            logger.warning("Assistant model catalog refresh failed")
            error = caught
        if not self._shutting_down:
            self._catalogRefreshFinished.emit(serial, provider_id, models, error)
        with self._worker_lock:
            self._workers.discard(current_thread())

    @Slot(int, str, object, object)
    def _on_catalog_refresh_finished(
        self, serial: int, provider_id: str, models: object, error: object
    ) -> None:
        self._catalog_refreshing = False
        if self._shutting_down or provider_id != self._provider_id or serial != self._connection_probe_serial:
            self.changed.emit()
            return
        if isinstance(error, BaseException):
            self._connection_result = map_connection_error(error)
            self._catalog_error = (provider_id, self._connection_result.message)
            self.changed.emit()
            return
        try:
            self._provider_settings.set_dynamic_models(provider_id, models)
        except BaseException as caught:
            logger.warning("Assistant model catalog result was invalid")
            self._connection_result = map_connection_error(caught)
            self._catalog_error = (provider_id, self._connection_result.message)
            self.changed.emit()
            return
        self._model_id = self._catalog_model_id(provider_id, self._model_id)
        self.changed.emit()

    @Slot(str, result=bool)
    def sendMessage(self, message: str) -> bool:
        if self.isBusy:
            return self._queue_supplement(message)
        return self._send_message(message, intent_template="")

    @Property(int, notify=changed)
    def pendingMessageCount(self) -> int:
        with self._pending_messages_lock:
            return len(self._pending_messages.get(self._active_generation, ()))

    def _queue_supplement(self, message: str) -> bool:
        text = message.strip() if isinstance(message, str) else ""
        generation = self._active_generation
        if not text or generation is None or self._shutting_down:
            return False
        if self._creation_workflow and self._creation_workflow.current(generation):
            try:
                self._creation_workflow.add_supplement(text)
            except ValueError as error:
                self._append_error(str(error))
                self.changed.emit()
                return False
            self._retry_instruction = self._creation_workflow.instruction
        else:
            self._retry_instruction += "\n\n用户补充：" + text
        resource_session = self._resource_sessions.get(generation)
        if resource_session is not None:
            resource_session.instruction = self._retry_instruction
        with self._pending_messages_lock:
            self._pending_messages.setdefault(generation, []).append(text)
        if self._active_context is None or self._active_context.intent_template != 'studio_model_assist':
            self._memory_accept(text, self._task_memory.state.get("intent", "EDIT_MUSIC"))
        self._suppressed_streams.add(generation)
        self._append_user_music_request(text)
        self.changed.emit()
        return True

    def _take_pending_messages(self, generation: int) -> tuple[str, ...]:
        with self._pending_messages_lock:
            messages = tuple(self._pending_messages.pop(generation, ()))
        if messages:
            self._pendingMessagesChanged.emit(generation)
        return messages

    @Slot(int)
    def _on_pending_messages_changed(self, generation: int) -> None:
        if generation == self._active_generation:
            self.changed.emit()

    def send_scoped_proposal(self, instruction: str, bars: BarRange, *, creating: bool, attachments: tuple = ()) -> bool:
        """Structured creative entry: material text cannot expand write authority."""
        context = self._gateway.create_execution_context(
            authorized_bars=(bars,), permissions=self._music_permissions(include_writes=True),
            mode=MusicExecutionMode.PROPOSAL,
        )
        sent = self._send_message(
            instruction, intent_template="", context_override=context,
            forced_intent="CREATE_MUSIC" if creating else "EDIT_MUSIC",
            attachments=attachments,
        )
        if not sent:
            self._gateway.release_proposal(context)
        return sent

    @Slot(str, str, result=bool)
    def sendQuickAction(self, action_id: str, message: str) -> bool:
        """Send a curated musical objective without exposing provider tool syntax."""

        template = get_quick_action_template(action_id)
        if template is None:
            return False
        return self._send_message(message, intent_template=template.action_id)

    @Slot(result=bool)
    def generateAdvicePreview(self) -> bool:
        """Turn the last actionable advice into one inherited Proposal-mode turn."""

        if not self.canGenerateAdvicePreview:
            return False
        intent = self._pending_music_intent
        instruction = self._pending_music_instruction
        intent_template = self._pending_music_intent_template
        context = self._gateway.create_execution_context(
            authorized_bars=self._last_music_authorized_bars,
            permissions=self._continuation_permissions(include_writes=True),
            mode=MusicExecutionMode.PROPOSAL,
            selected_note_ids=self._last_music_selected_note_ids,
            intent_template=intent_template,
        )
        return self._send_message(
            instruction
            + "\n\n请根据上一轮已经给出的建议生成一个可试听的修改方案；现在执行真实音乐工具，"
            "但不要直接应用到工程。",
            intent_template=intent_template,
            forced_intent=intent,
            context_override=context,
            display_text="按这个建议生成可试听方案",
        )

    def _send_message(
        self,
        message: str,
        *,
        intent_template: str,
        forced_intent: str = "",
        context_override: MusicAgentExecutionContext | None = None,
        display_text: str = "",
        attachments: tuple = (),
        allow_creative: bool = False,
    ) -> bool:
        if self._shutting_down or self._assistant_status not in {"idle", "proposal_ready", "error"}:
            return False
        text = message.strip() if isinstance(message, str) else ""
        if not text:
            return False
        if self._creator_enabled:
            return self._send_creator_message(text, display_text=display_text, attachments=attachments)
        from ai_piano_studio.music_agent.resource_tools import STUDIO_ASSIST_TEMPLATE
        model_driven = (self._model_driven_assistant and not (forced_intent or intent_template
            or attachments or context_override or allow_creative) and self.currentModelStatus["canCreate"]
            and not self._is_direct_apply_request(text) and self._mode != "collaboration")
        if model_driven:
            intent_template = STUDIO_ASSIST_TEMPLATE
        flow = self._song_source_flow
        if flow:
            handled = flow.handle_text(text, model_driven=model_driven)
            if handled is not None:
                return handled
            flow.revoke_approval()
            if not model_driven:
                flow.rejected_sources.clear()
        if self._creation_workflow and not (forced_intent or intent_template or attachments or context_override):
            followup = self._creation_workflow.expand_song_followup(text)
            if followup:
                display_text = display_text or text
                text = followup
        self._memory_reconcile()
        intent = "CHAT" if model_driven else forced_intent or self._resolve_turn_intent(text)
        named_new_song = False
        if flow and not attachments and context_override is None and not intent_template:
            from ai_piano_studio.music_agent.song_resolver import music_request, requests_named_song
            named_new_song = bool(music_request(text).named and (
                requests_named_song(text) or re.match(r'^(?:请|帮我)?(?:来一首|来一段|我想听|演奏|弹|播放|换成|换一首)', text)
                or re.search(r'换成[《“\"]|替换为[《“\"]', text)))
            named_new_song &= not bool(re.search(r'续写|往下接|往后接|继续写|接下去|介绍|推荐|歌词是什么', text))
            if named_new_song:
                intent = 'CREATE_MUSIC'
        if flow and intent == 'CREATE_MUSIC':
            if not flow.validate_duration(text):
                return False
        self._active_extension_seconds = self._duration_extension_seconds(text) if model_driven or intent == "EDIT_MUSIC" else None
        context_instruction = text
        if (intent == "EDIT_MUSIC" and self._active_extension_seconds is None
                and re.fullmatch(r"(?:请|那就|帮我)?(?:继续|接着|重试|继续完成|接着做)[吧。！!\s]*", text)):
            target = self._task_memory.state.get("constraints", {}).get("targetSeconds")
            if target:
                self._active_extension_seconds = target
                context_instruction += f"\n继续扩展到{target:g}秒。"
        if not model_driven and intent in {"CHAT", "READ_MUSIC"} and self._is_local_proposal_follow_up(text):
            return self._handle_local_proposal_follow_up(text)
        self._generation += 1
        generation = self._generation
        with self._pending_messages_lock:
            self._pending_messages.clear()
        self._suppressed_streams.clear()
        self._retry_instruction = text
        self._retry_attachments = attachments
        self._retry_allow_creative = allow_creative
        self._task_message_start_row = len(self._messages.entries)
        self._task_progress.start(generation, self._safe_text(display_text or text), self._provider_id, self._model_id)
        self._memory_accept(text, intent, new_task=bool(intent == "CREATE_MUSIC" and not self._is_task_resume(text)
            and not any(w in text for w in ("继续", "续写", "接着", "往下", "刚刚", "刚才"))))
        resumed_creation = intent == "CREATE_MUSIC" and self._is_task_resume(text) and bool(self._task_memory.state)
        if self._creation_workflow and context_override is None and (named_new_song or resumed_creation or self._creation_workflow.eligible(intent, text, attachments, intent_template)):
            self._creation_workflow.start(generation, self._resumed_creation_instruction() if resumed_creation else text,
                allow_creative=allow_creative, display_text=display_text or text)
            return True
        model_status = self.currentModelStatus
        if intent in {"CREATE_MUSIC", "EDIT_MUSIC"} and not model_status["canCreate"]:
            if model_status["canVerify"]:
                message = "创作前请点击“验证并开始创作”。这条提示没有发送给模型，也不会消耗调用额度。"
            elif model_status["state"] == "chat_only":
                message = "当前模型仅支持聊天，不能调用音乐工具。请切换到标有“MIDI 编辑可用”的模型；这条提示没有发送给模型，也不会消耗调用额度。"
            else:
                message = "当前模型暂不能创作。请检查模型状态后重试；这条提示没有发送给模型，也不会消耗调用额度。"
            self._append_error(message)
            self._task_progress.finish(generation, "failed", error_code="provider_unavailable")
            self.changed.emit()
            return False
        conversation_history = self._conversation_history()
        provider_instruction = text
        if self._active_extension_seconds is not None and not model_driven:
            provider_instruction += (f"\n\n本轮要求将当前作品的总时长扩展至 {self._active_extension_seconds:g} 秒，"
                f"目前约 {self._editor.state.playback_duration:g} 秒。保留现有开头和声部，在末尾继续，"
                "以本轮目标时长为准。这里的扩展默认指接着写后续乐句，不是复制开头、循环已有段落凑时长。"
                "只有用户明确要求重复/循环才重复；主旋律应向后发展，伴奏可合理复用。"
                "若为指定歌曲，优先查找后续原曲曲谱或 MIDI，按后续段落还原；不确定处诚实标明。"
                "使用实际音符工具完成，不要只改力度或描述建议。")
        pending_intent = (
            self._advice_task_intent(text)
            if intent == "READ_MUSIC" and not self._is_preview_opt_out(text)
            else ""
        )
        if self._last_audio_import_summary:
            provider_instruction = (
                provider_instruction
                + "\n\n最近一次本地音频分析摘要（不要把原始音频发送给任何服务）：\n"
                + self._last_audio_import_summary
            )
            self._last_audio_import_summary = ""
        self._messages.append(
            AssistantMessage(AssistantMessageKind.USER_MESSAGE, display_text or text)
        )
        try:
            context = context_override or self._create_execution_context(
                intent,
                context_instruction,
                intent_template=intent_template,
                pending_intent=pending_intent,
            )
            if self._creation_workflow is not None:
                context = replace(context, permissions=context.permissions | frozenset({MusicPermission.READ_PUBLIC_MUSIC_RESOURCES}))
            if self._appearance_tools is not None:
                context = replace(context, permissions=context.permissions | frozenset({MusicPermission.MANAGE_APP_APPEARANCE}))
        except BaseException as error:
            logger.warning("Music assistant execution context creation failed")
            self._connection_result = map_connection_error(error)
            self._assistant_status = "error"
            self._append_error(self._connection_result.message)
            self._task_progress.finish(generation, "failed", error_code="execution_error")
            self.changed.emit()
            return False
        cancelled = Event()
        try:
            loop = self._create_agent_loop(generation, cancelled)
        except BaseException as error:
            logger.warning("Music assistant loop creation failed")
            self._connection_result = map_connection_error(error)
            self._assistant_status = "error"
            self._append_error(self._connection_result.message)
            self._task_progress.finish(generation, "failed", error_code="execution_error")
            self.changed.emit()
            return False
        self._active_generation = generation
        self._active_cancel = cancelled
        self._active_context = context
        self._active_intent = intent
        if intent in {"CREATE_MUSIC", "EDIT_MUSIC", "READ_MUSIC"}:
            self._remember_music_context(
                context,
                intent,
                pending_intent=pending_intent,
                instruction=text,
                intent_template=intent_template,
            )
        self._assistant_status = "thinking"
        self.changed.emit()
        reasoning = self.providerReasoningValue or None
        worker = Thread(
            target=self._run_generation,
            args=(
                generation,
                cancelled,
                loop,
                provider_instruction,
                context,
                reasoning,
                intent,
                intent_template,
                conversation_history,
                attachments,
            ),
            daemon=True,
            name="music-assistant-turn",
        )
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        return True

    @staticmethod
    def _is_local_proposal_follow_up(text: str) -> bool:
        if MusicAssistantController._is_preview_opt_out(text):
            return False
        # Only an actual short status/preview query is a local UI shortcut.
        # A substantive request merely containing “试听” must reach the model.
        normalized = re.sub(r'[\s，。！？,.!?]+', '', text)
        normalized = re.sub(r'^(?:请|帮我|让我|我想|现在)', '', normalized)
        return any(normalized in {marker, marker+'一下', marker+'吧', marker+'呢'}
                   for marker in _MUSIC_CONTINUATION_STATUS_MARKERS)

    def _handle_local_proposal_follow_up(self, text: str) -> bool:
        self._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, text))
        proposal = self._proposal_manager.proposal
        status = proposal.status.value if proposal is not None else ""
        if status == MusicEditProposalStatus.PENDING.value:
            response = "修改建议已经准备好，可使用下方“试听”按钮在本地预览，确认后再应用。"
        elif status == MusicEditProposalStatus.APPLIED.value:
            response = "修改建议已应用到工程，请回到编辑器使用播放功能检查结果。"
        elif status == MusicEditProposalStatus.STALE.value:
            response = "这条修改建议已过期，工程发生变化，请重新生成建议。"
        elif status == MusicEditProposalStatus.DISCARDED.value:
            response = "这条修改建议已放弃，当前没有待试听的建议。"
        elif status == MusicEditProposalStatus.FAILED.value:
            response = "这条修改建议应用失败，当前没有可试听的建议。"
        else:
            response = "当前没有待试听的修改建议；如果要修改工程，请提出具体的音乐任务。"
        self._messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, response))
        effective = EffectiveCapabilitySet.from_sources(
            self._app_capabilities,
            self._capabilities(),
            self._music_permissions(include_writes=False),
        )
        self._last_turn_result = AssistantTurnResult(
            status="local_follow_up",
            intent="READ_MUSIC",
            mode=self._mode,
            tool_call_count=0,
            read_count=0,
            write_count=0,
            proposal_id=proposal.proposal_id if proposal is not None else "",
            proposal_status=status,
            revision_before=self._editor.projectRevision,
            revision_after=self._editor.projectRevision,
            committed=False,
            capabilities=effective.to_dict(),
            final_text=response,
        ).to_dict()

        self._assistant_status = (
            "proposal_ready"
            if status == MusicEditProposalStatus.PENDING.value
            else "idle"
        )
        self.changed.emit()
        return True

    @Slot()
    def cancelGeneration(self) -> None:
        """Invalidate and roll back the current worker turn without blocking QML."""

        if QThread.currentThread() != self.thread():
            self._cancellationRequested.emit()
            return
        self._cancel_generation_on_controller_thread()

    @Slot()
    def _cancel_generation_on_controller_thread(self) -> None:
        if self._active_generation is not None:
            self._memory_record("cancelled")
        self._invalidate_active_generation()

    def _create_execution_context(
        self,
        intent: str,
        instruction: str = "",
        *,
        intent_template: str = "",
        pending_intent: str = "",
    ) -> MusicAgentExecutionContext:
        turn_mode = self._execution_mode_for_instruction(instruction)
        if intent == "CHAT":
            if self._creation_workflow is not None:
                # The local intent classifier is only a hint in Studio. Give
                # the in-app model useful musical authority through reversible
                # proposals; an ordinary sentence need not contain a magic verb.
                explicit_ranges = self._explicit_bar_ranges(instruction)
                permissions = self._music_permissions(include_writes=True)
                if explicit_ranges:
                    bars = explicit_ranges
                elif self._creation_scope_mode == _CREATION_SCOPE_MANUAL:
                    bars = (self._resolved_creation_range(instruction),)
                elif intent_template == 'studio_model_assist' and self._editor.state.selected_notes:
                    bar_seconds = self._editor.state.tempo.bar_duration_seconds
                    bars = tuple(dict.fromkeys(BarRange(
                        int(note.start_time // bar_seconds) + 1,
                        int((note.start_time + note.duration) // bar_seconds) + 1,
                    ) for note in self._editor.state.selected_notes))
                else:
                    end = max(1, math.ceil(self._editor.state.playback_duration /
                                          self._editor.state.tempo.bar_duration_seconds))
                    bars = (BarRange(1,end),)
                    permissions = permissions | frozenset({MusicPermission.WHOLE_PROJECT_READ,MusicPermission.WHOLE_PROJECT_WRITE})
                append_after_seconds = None
                if intent_template == 'studio_model_assist' and self._is_append_continuation(instruction):
                    append_after_seconds = max((n.start_time + n.duration for n in self._editor.state.active_notes), default=0)
                return self._gateway.create_execution_context(authorized_bars=bars,permissions=permissions,
                    mode=MusicExecutionMode.PROPOSAL,selected_note_ids=() if explicit_ranges else tuple(n.id for n in self._editor.state.selected_notes),
                    intent_template=intent_template, append_after_seconds=append_after_seconds)
            if self._continuation_intent(instruction) is not None:
                return self._gateway.create_execution_context(
                    authorized_bars=self._continuation_authorized_bars(),
                    permissions=self._continuation_permissions(include_writes=False),
                    mode=MusicExecutionMode.PROPOSAL,
                    intent_template=intent_template,
                )
            return self._gateway.create_execution_context(
                authorized_bars=(),
                permissions=frozenset(),
                mode=MusicExecutionMode.PROPOSAL,
                intent_template=intent_template,
            )
        explicit_ranges = self._explicit_bar_ranges(instruction)
        continuation = self._continuation_intent(instruction)
        if (continuation == intent and not explicit_ranges and self._last_music_permissions
                and self._creation_scope_mode != _CREATION_SCOPE_MANUAL):
            return self._gateway.create_execution_context(
                authorized_bars=self._last_music_authorized_bars,
                permissions=self._continuation_permissions(
                    include_writes=intent in {"CREATE_MUSIC", "EDIT_MUSIC"}
                ),
                mode=turn_mode,
                intent_template=intent_template,
            )
        if (intent == "READ_MUSIC" and self._is_task_status_question(instruction)
                and not explicit_ranges and not self._editor.state.selected_notes
                and self._creation_scope_mode != _CREATION_SCOPE_MANUAL):
            duration = self._memory_project_facts()["durationSeconds"]
            end = max(1, math.ceil(duration / self._editor.state.tempo.bar_duration_seconds))
            return self._gateway.create_execution_context(
                authorized_bars=(BarRange(1, end),),
                permissions=self._music_permissions(include_writes=False),
                mode=MusicExecutionMode.PROPOSAL, intent_template=intent_template)
        if intent == "READ_MUSIC" and pending_intent == "CREATE_MUSIC":
            permissions = self._music_permissions(include_writes=False)
            if self._creation_scope_mode == _CREATION_SCOPE_AUTOMATIC and not explicit_ranges:
                permissions = permissions | frozenset({MusicPermission.WHOLE_PROJECT_READ})
            return self._gateway.create_execution_context(
                authorized_bars=(
                    explicit_ranges
                    if explicit_ranges
                    else (
                        (self._resolved_creation_range(instruction),)
                        if self._creation_scope_mode == _CREATION_SCOPE_MANUAL
                        else ()
                    )
                ),
                permissions=permissions,
                mode=turn_mode,
                intent_template=intent_template,
            )
        if intent == "CREATE_MUSIC":
            permissions = self._music_permissions()
            if self._creation_scope_mode == _CREATION_SCOPE_AUTOMATIC and not explicit_ranges:
                permissions = permissions | frozenset(
                    {MusicPermission.WHOLE_PROJECT_READ, MusicPermission.WHOLE_PROJECT_WRITE}
                )
            return self._gateway.create_execution_context(
                authorized_bars=(
                    explicit_ranges
                    if explicit_ranges
                    else (
                        (self._resolved_creation_range(instruction),)
                        if self._creation_scope_mode == _CREATION_SCOPE_MANUAL
                        else ()
                    )
                ),
                permissions=permissions,
                mode=turn_mode,
                intent_template=intent_template,
            )
        selected_notes = self._editor.state.selected_notes
        scoped_notes = (
            () if explicit_ranges else (selected_notes or self._editor.state.active_notes)
        )
        selected_note_ids = () if explicit_ranges else tuple(note.id for note in selected_notes)
        tempo = self._editor.state.tempo
        authorized_bars = tuple(
            dict.fromkeys(
                BarRange(
                    max(1, int(note.start_time // tempo.bar_duration_seconds) + 1),
                    max(1, int((note.start_time + note.duration) // tempo.bar_duration_seconds) + 1),
                )
                for note in scoped_notes
            )
        )
        if not authorized_bars and not explicit_ranges:
            authorized_bars = (self._initial_creation_range(instruction),)
        if explicit_ranges:
            authorized_bars = explicit_ranges
        permissions = self._music_permissions(include_writes=intent == "EDIT_MUSIC")
        if not explicit_ranges and self._creation_scope_mode == _CREATION_SCOPE_MANUAL:
            authorized_bars = (self._resolved_creation_range(instruction),)
        elif not explicit_ranges and not selected_notes and intent == "EDIT_MUSIC":
            target = self._duration_extension_seconds(instruction)
            if target is not None and target > self._editor.state.playback_duration:
                # The full-song request explicitly includes the new tail. Give
                # only its bounded bar range, not unrestricted project access.
                end = max(1, math.ceil(max(target, self._editor.state.playback_duration) / tempo.bar_duration_seconds))
                authorized_bars = (BarRange(1, end),)
        return self._gateway.create_execution_context(
            authorized_bars=authorized_bars,
            permissions=permissions,
            mode=turn_mode,
            selected_note_ids=selected_note_ids,
            intent_template=intent_template,
            append_after_seconds=(max((n.start_time + n.duration for n in self._editor.state.active_notes), default=0)
                if intent == "EDIT_MUSIC" and self._is_append_continuation(instruction) else None),
        )

    def _execution_mode_for_instruction(self, instruction: str) -> MusicExecutionMode:
        if self._is_preview_request(instruction):
            return MusicExecutionMode.PROPOSAL
        if self._is_direct_apply_request(instruction):
            return MusicExecutionMode.COLLABORATION
        return (
            MusicExecutionMode.COLLABORATION
            if self._mode == "collaboration"
            else MusicExecutionMode.PROPOSAL
        )

    @staticmethod
    def _is_append_continuation(instruction: str) -> bool:
        marker = re.search(r"续写|往下接|接下去|往后接|接着写|继续写|续到|续至", instruction)
        if marker:
            return not re.search(r"(?:不要|不用|别|暂不)\s*(?:再)?\s*$", instruction[:marker.start()])
        return MusicAssistantController._duration_extension_seconds(instruction) is not None

    @staticmethod
    def _duration_extension_seconds(instruction: str) -> float | None:
        from ai_piano_studio.music_agent.song_resolver import DURATION_RE
        marker = re.search(r"扩展|延长|续写|扩写|加长|往下接|接下去|往后接|接着写|继续写|续到|续至", instruction)
        if not marker or re.search(r"(?:不要|不用|别|暂不)\s*(?:再)?\s*$", instruction[:marker.start()]):
            return None
        requested = DURATION_RE.search(instruction, marker.end())
        if requested:
            seconds = float(requested.group(1))
            if math.isfinite(seconds) and seconds > 0:
                return seconds
        return None

    @staticmethod
    def _is_direct_apply_request(instruction: str) -> bool:
        if (re.search(r"[？?]|能不能|可以吗|是否|先聊|讨论", instruction)
                or MusicAssistantController._is_preview_opt_out(instruction)):
            return False
        positive = re.sub(r"(?:不要|别|不用|不想|暂不|禁止|不准|不接受)\s*直接(?:修改|改|写入|应用)(?:进去|到工程)?", "", instruction)
        return bool(re.search(r"直接(?:修改|改)(?:进去|到(?:当前)?工程|进工程)|直接(?:写入|应用)", positive))

    @staticmethod
    def _is_preview_request(instruction: str) -> bool:
        text = instruction if isinstance(instruction, str) else ""
        return any(marker in text for marker in _PREVIEW_OVERRIDE_MARKERS)

    @staticmethod
    def _is_preview_opt_out(instruction: str) -> bool:
        text = instruction if isinstance(instruction, str) else ""
        # “不要只给建议” explicitly asks to execute, so the nested
        # “只给建议” substring must not turn the whole request read-only.
        opt_out_text = text.replace("不要只给建议", "")
        for marker in _PREVIEW_OPTOUT_MARKERS:
            if marker not in opt_out_text:
                continue
            if marker not in {"不要修改", "暂不修改"}:
                return True

            # A no-change clause can constrain one musical dimension while a
            # later clause still requests another edit, e.g.
            # “不要修改节奏，只调整力度”.  Only treat it as a global opt-out
            # when no later clause contains a positive music action.
            marker_end = opt_out_text.find(marker) + len(marker)
            remainder = opt_out_text[marker_end:]
            clauses = re.split(r"[，。！？,!?；;：:、]", remainder)
            protected_scope = clauses[0].strip().lower()
            later_clauses = clauses[1:]
            has_named_scope = any(
                target in protected_scope for target in _SCOPED_EDIT_TARGET_MARKERS
            )
            has_unambiguous_later_execution = any(
                MusicAssistantController._classify_music_action(clause)
                in {"CREATE_MUSIC", "EDIT_MUSIC"}
                and not MusicAssistantController._is_advice_request(clause)
                and not any(
                    marker in clause for marker in _NON_EXECUTION_GUIDANCE_MARKERS
                )
                for clause in later_clauses
            )
            if has_named_scope and has_unambiguous_later_execution:
                continue
            return True
        return False

    def _music_permissions(self, *, include_writes: bool = True) -> frozenset[MusicPermission]:
        permissions = {
            MusicPermission.READ_PROJECT_SUMMARY,
            MusicPermission.READ_NOTES,
            MusicPermission.READ_HARMONY,
        }
        if include_writes and self._capabilities().supports_tools:
            permissions.update(
                {
                    MusicPermission.WRITE_NOTES,
                    MusicPermission.WRITE_BPM,
                    MusicPermission.WRITE_HARMONY,
                }
            )
        return frozenset(permissions)

    def _current_music_context_bars(self) -> tuple[BarRange, ...]:
        """Return bounded read ranges for a short follow-up to the active music task."""

        notes = self._editor.state.selected_notes or self._editor.state.active_notes
        if notes:
            tempo = self._editor.state.tempo
            ranges = tuple(
                dict.fromkeys(
                    BarRange(
                        max(1, int(note.start_time // tempo.bar_duration_seconds) + 1),
                        max(
                            1,
                            int(
                                (note.start_time + note.duration)
                                // tempo.bar_duration_seconds
                            )
                            + 1,
                        ),
                    )
                    for note in notes
                )
            )
            return ranges[:8]
        return (
            BarRange(
                max(1, self._creation_scope_start_bar),
                max(self._creation_scope_start_bar, self._creation_scope_end_bar),
            ),
        )

    def _continuation_authorized_bars(self) -> tuple[BarRange, ...]:
        if self._last_music_permissions:
            return self._last_music_authorized_bars
        return self._current_music_context_bars()

    def _continuation_permissions(self, *, include_writes: bool) -> frozenset[MusicPermission]:
        permissions = set(self._music_permissions(include_writes=include_writes))
        if MusicPermission.WHOLE_PROJECT_READ in self._last_music_permissions:
            permissions.add(MusicPermission.WHOLE_PROJECT_READ)
        if include_writes and (
            MusicPermission.WHOLE_PROJECT_WRITE in self._last_music_permissions
            or self._pending_music_intent == "CREATE_MUSIC"
            and MusicPermission.WHOLE_PROJECT_READ in self._last_music_permissions
        ):
            permissions.add(MusicPermission.WHOLE_PROJECT_WRITE)
        return frozenset(permissions)

    def _remember_music_context(
        self,
        context: MusicAgentExecutionContext,
        intent: str,
        *,
        pending_intent: str = "",
        instruction: str = "",
        intent_template: str = "",
    ) -> None:
        self._last_music_intent = intent
        self._last_music_authorized_bars = context.authorized_bars
        self._last_music_permissions = context.permissions
        self._last_music_selected_note_ids = context.selected_note_ids
        self._pending_music_intent = (
            pending_intent
            if intent == "READ_MUSIC" and pending_intent in {"EDIT_MUSIC", "CREATE_MUSIC"}
            else ""
        )
        self._pending_music_instruction = (
            instruction if self._pending_music_intent else ""
        )
        self._pending_music_intent_template = (
            intent_template if self._pending_music_intent else ""
        )

    def _resolved_creation_range(self, instruction: str) -> BarRange:
        if self._creation_scope_mode == _CREATION_SCOPE_MANUAL:
            return BarRange(self._creation_scope_start_bar, self._creation_scope_end_bar)
        return self._initial_creation_range(instruction)

    def _creation_scope_map(self, instruction: str) -> dict[str, object]:
        if self._creation_scope_mode == _CREATION_SCOPE_MANUAL:
            resolved = self._resolved_creation_range(instruction)
            label = f"第 {resolved.start_bar}–{resolved.end_bar} 小节"
            unlimited = False
        else:
            resolved = BarRange(self._creation_scope_start_bar, self._creation_scope_end_bar)
            label = "不限小节（可手动调整范围）"
            unlimited = True
        return {
            "mode": self._creation_scope_mode,
            "startBar": resolved.start_bar,
            "endBar": resolved.end_bar,
            "label": label,
            "unlimited": unlimited,
        }

    def _initial_creation_range(self, instruction: str) -> BarRange:
        """Resolve an empty-project duration against the existing TempoMap only."""
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s\b)", instruction.lower())
        if match is None:
            return BarRange(1, _EMPTY_PROJECT_INITIAL_BARS)
        seconds = float(match.group(1))
        if seconds <= 0:
            return BarRange(1, _EMPTY_PROJECT_INITIAL_BARS)
        tempo = self._editor.state.tempo
        beats = tempo.seconds_to_beats(seconds)
        bars = max(1, math.ceil(beats / tempo.beats_per_bar_length))
        return BarRange(1, bars)

    @staticmethod
    def _explicit_bar_ranges(instruction: str) -> tuple[BarRange, ...]:
        """Parse only explicit user bar ranges; selection never overrides them."""

        if not isinstance(instruction, str):
            return ()
        patterns = (
            r"第\s*(\d+)\s*(?:[-–—~到至]\s*(\d+)\s*)?小节",
            r"(?:当前\s*)?(\d+)\s*[-–—~到至]\s*(\d+)\s*小节",
            r"\bbar[s]?\s*(\d+)\s*(?:[-–—~到至]\s*(\d+)\s*)?\b",
        )
        ranges: list[BarRange] = []
        for pattern in patterns:
            for match in re.finditer(pattern, instruction, flags=re.IGNORECASE):
                start = int(match.group(1))
                end = int(match.group(2) or start)
                candidate = BarRange(start, end)
                if candidate not in ranges:
                    ranges.append(candidate)
        return tuple(ranges[:8])

    @staticmethod
    def _classify_intent(instruction: str) -> str:
        """Classify local user intent; it never grants permissions or ranges."""
        text = instruction.strip().lower()
        if not text:
            return "CHAT"
        # An unspecified action is an invitation to clarify, not authority to
        # choose an arbitrary edit or fabricate a piece.
        if re.fullmatch(r"(?:帮我|请|麻烦)?(?:弄一下|做一下|处理一下|改一下|优化一下|调整一下)[吧呀啊。！!？?\s]*", text):
            return "CHAT"
        # Discussing a possible arrangement is not a request to compose it.
        # Recognize this before action words such as 编配/生成 in the same sentence.
        if re.search(r"先聊|聊聊|先讨论|讨论一下|先帮我想|一起想想", text) and not (
            MusicAssistantController._is_explicit_execution_request(text)
        ):
            return "READ_MUSIC" if any(word in text for word in ("当前", "选中", "这段")) else "CHAT"
        # Informational questions and app configuration are not score-edit requests.
        if re.search(r"(?:想了解|介绍|是什么意思|怎么创作|如何作曲)", text) and not any(
            word in text for word in ("当前", "选中", "导入", "这段", "直接", "现在")
        ):
            return "CHAT"
        if any(word in text for word in ("软件设置", "连接设置", "模型设置", "api key")):
            return "CHAT"
        if any(word in text for word in ("原曲", "曲谱", "midi")) and any(
            word in text for word in ("在哪", "哪里", "查找", "搜索", "找一下", "下载链接")
        ):
            return "READ_MUSIC"
        if MusicAssistantController._is_explicit_execution_request(text):
            return MusicAssistantController._classify_music_action(text)
        if MusicAssistantController._is_advice_request(text):
            return "READ_MUSIC" if MusicAssistantController._is_music_related(text) else "CHAT"
        return MusicAssistantController._classify_music_action(text)

    @staticmethod
    def _classify_music_action(text: str) -> str:
        if MusicAssistantController._duration_extension_seconds(text) is not None:
            return "EDIT_MUSIC"
        if MusicAssistantController._is_composition_request(text):
            return "CREATE_MUSIC"
        if any(word in text for word in ("编配", "重新编曲")) and any(
            word in text for word in _MUSIC_OBJECT_MARKERS
        ):
            return "EDIT_MUSIC"
        if any(marker in text for marker in _FOLLOWUP_EDIT_MARKERS):
            return "EDIT_MUSIC"
        if any(marker in text for marker in _CREATE_INTENT_MARKERS):
            return "CREATE_MUSIC"
        if any(marker in text for marker in _EDIT_INTENT_MARKERS):
            return "EDIT_MUSIC"
        if MusicAssistantController._is_original_music_request(text):
            return "CREATE_MUSIC"
        has_music_object = any(marker in text for marker in _MUSIC_OBJECT_MARKERS)
        if has_music_object and (
            any(operation in text for operation in _MUSIC_EDIT_OPERATIONS)
            or "做一个" in text
            or re.search(r"变(?:梦幻|柔和|温暖|悲伤|有力|清晰|紧张|舒缓)", text)
        ):
            return "EDIT_MUSIC"
        if _DIRECT_MUSIC_EDIT_RE.search(text):
            return "EDIT_MUSIC"
        if has_music_object and any(verb in text for verb in _MUSIC_READ_VERBS):
            return "READ_MUSIC"
        if any(marker in text for marker in _READ_INTENT_MARKERS):
            return "READ_MUSIC"
        return "CHAT"

    @staticmethod
    def _is_composition_request(text: str) -> bool:
        """Recognize a request + musical object, not the word 'want' alone."""
        if any(word in text for word in ("介绍", "推荐", "歌词", "链接", "下载", "了解")):
            return False
        if re.search(r"(?:不要|别|不用|不想)(?:给我|来|做|弹|演奏|听|要)", text):
            return False
        if not any(word in text for word in ("当前", "这首", "选中", "前奏", "这段")):
            from ai_piano_studio.music_agent.song_resolver import requests_named_song
            if requests_named_song(text):
                return True
        return bool(
            re.search(r"(?:我(?:要|想要|想听)|给我|来|做|弹|演奏)\s*(?:一)?(?:段|首|曲).{0,40}"
                      r"(?:钢琴|伴奏|乐段|旋律|音乐|曲子|乐曲|歌曲|原曲|《[^》]+》)", text)
            or re.search(r"(?:我(?:要|想要|想听)|请|帮我|来|弹|演奏).{0,40}《[^》]+》", text)
            or re.search(r"(?:请|帮我)?(?:弹|演奏).{0,40}原曲", text)
        )

    @staticmethod
    def _is_explicit_execution_request(text: str) -> bool:
        if any(marker in text for marker in _EXECUTION_INTENT_MARKERS):
            return True
        return bool(
            re.search(
                r"(?:直接|现在|立即|开始).{0,80}"
                r"(?:写|生成|创作|改|调|优化|增强|添加|加入|增加|升高|降低|删除|移动)",
                text,
            )
        )

    @staticmethod
    def _is_advice_request(text: str) -> bool:
        return any(marker in text for marker in _ADVICE_INTENT_MARKERS)

    @staticmethod
    def _is_music_related(text: str) -> bool:
        return any(marker in text for marker in (*_MUSIC_CONTEXT_MARKERS, *_MUSIC_OBJECT_MARKERS)) or any(
            marker in text
            for marker in (*_CREATE_INTENT_MARKERS, *_EDIT_INTENT_MARKERS)
        )

    @staticmethod
    def _advice_task_intent(text: str) -> str:
        """Infer the task being discussed without granting this advice turn write access."""

        target = text
        for marker in sorted(_ADVICE_INTENT_MARKERS, key=len, reverse=True):
            target = target.replace(marker, " ")
        action = MusicAssistantController._classify_music_action(target)
        if action in {"CREATE_MUSIC", "EDIT_MUSIC"}:
            return action
        if any(
            marker in text
            for marker in ("怎么改", "如何改", "怎么调整", "怎么优化", "怎么变得", "如何变得")
        ):
            return "EDIT_MUSIC"
        return ""

    @staticmethod
    def _is_task_status_question(text: str) -> bool:
        question = re.search(r"进度|进行到哪|做到哪|写到哪|做完了吗|完成了吗|(?:怎么|为什么|为啥).*(?:没|未|不).*(?:动|改|写|完成|继续)", text)
        if not question:
            return False
        clauses = re.split(r"[，,。；;？?！!]\s*", text)
        return not any(re.match(r"^(?:请|然后|现在|接着|那就)?(?:继续(?:写|做|改)|执行|直接修改|重写|新增|删除|应用)", clause.strip()) for clause in clauses)

    def _resolve_turn_intent(self, instruction: str) -> str:
        """Keep terse confirmations and status questions attached to the active music task."""

        if self._is_task_status_question(instruction):
            return "READ_MUSIC"
        intent = self._classify_intent(instruction)
        if self._is_task_resume(instruction) and self._task_memory.state.get("status") == "completed":
            return "READ_MUSIC"
        if (
            self._mode == "suggestion"
            and self._is_preview_opt_out(instruction)
            and self._is_music_related(instruction)
        ):
            return "READ_MUSIC"
        if (
            self._mode == "suggestion"
            and intent == "READ_MUSIC"
            and self._is_advice_request(instruction)
            and not self._is_preview_opt_out(instruction)
        ):
            actionable = self._advice_task_intent(instruction)
            if actionable in {"CREATE_MUSIC", "EDIT_MUSIC"}:
                return actionable
        if self._is_original_music_request(instruction):
            continuation = self._continuation_intent(instruction)
            if continuation is not None:
                return continuation
        if intent != "CHAT":
            return intent
        continuation = self._continuation_intent(instruction)
        if self._task_memory.state.get("status") in {"pending", "failed", "proposal", "needs_revalidation"} and re.fullmatch(
                r"(?:请|那就|帮我)?(?:继续|接着|重试|继续完成|接着做)[吧。！!\s]*", instruction):
            return "EDIT_MUSIC" if self._editor.state.active_notes else "CREATE_MUSIC"
        return continuation or "CHAT"

    def _continuation_intent(self, instruction: str) -> str | None:
        """Return a continuation intent only for explicit short follow-up phrases."""

        if not self._last_music_intent:
            return None
        normalized = re.sub(r"[\s，。！？,.!?；;：:、]+", "", instruction.strip().lower())
        # A correction to the previous music task keeps its existing scope and
        # permissions; merely discussing an original song does not authorize edits.
        if (
            self._last_music_intent in {'CREATE_MUSIC', 'EDIT_MUSIC'}
            and self._is_original_music_request(instruction)
        ):
            return self._last_music_intent
        if any(marker in normalized for marker in _MUSIC_CONTINUATION_STATUS_MARKERS):
            return "READ_MUSIC"
        if len(normalized) <= 32 and (
            normalized in _MUSIC_CONTINUATION_APPROVAL_MARKERS
            or any(normalized.startswith(prefix) for prefix in _MUSIC_CONTINUATION_APPROVAL_PREFIXES)
        ):
            return self._pending_music_intent or self._last_music_intent
        return None

    @staticmethod
    def _is_original_music_request(instruction: str) -> bool:
        text = instruction.strip().lower()
        return bool(
            len(text) <= 80
            and re.match(r'^(?:我(?:要|想要)|要的是|换成|改成|用)', text)
            and ('原曲' in text or ('原版' in text and MusicAssistantController._is_music_related(text)))
            and not any(word in text for word in ('介绍', '意思', '评价', '推荐', '歌词', '链接', '吗', '？', '?'))
        )

    def _create_agent_loop(self, generation: int, cancelled: Event) -> MusicAgentLoop:
        serialized_gateway = _SerializedGateway(self, generation, cancelled)
        arguments = {
            "registry": serialized_gateway.registry,
            "gateway": serialized_gateway,
            "context_builder": MusicAgentContextBuilder(
                serialized_gateway, self._app_capabilities, task_context=self._memory_context(),
                history_budget_bytes=self.historyContextKB * 1000,
            ),
            "model_id": self._model_id,
            "is_cancelled": cancelled.is_set,
            "on_text_delta": lambda text: self._generationTextDelta.emit(generation, text),
            "on_response_started": lambda: self._generationResponseStarted.emit(generation),
            "on_progress": lambda event: self._generationProgress.emit(generation, event),
            "take_pending_messages": lambda: self._take_pending_messages(generation),
            "on_validation_repair": lambda count: self._generationValidationRepair.emit(
                generation, count
            ),
        }
        if self._agent_loop_factory is not None:
            return self._agent_loop_factory(**arguments)
        provider = self._provider_settings.create_provider(
            self._provider_id, model_id=self._model_id
        )
        if isinstance(provider, CodexProvider):
            provider.is_cancelled = cancelled.is_set
            provider.timeout_seconds = self.codexWaitSeconds
            # Discovery belongs to every music task, not just one keyword route.
            provider.allow_web_search = self._creation_workflow is not None
            provider.native_tools_enabled = self._creation_workflow is not None
            if self._creator_enabled:
                provider.creator_workspace = self._creator_workspace.workspace
                provider.request_user_input = serialized_gateway.creator_native_questions
            if (provider.native_tools_enabled and getattr(self._creation_workflow, 'reconstructing', False)
                    and serialized_gateway._resource_session is not None):
                serialized_gateway._resource_session.eager_score_reference = True
        if self._creator_enabled:
            provider.creator_enabled = True
            provider.creator_request_options = json.loads(self.creatorRequestOptions)
        return MusicAgentLoop(
            provider=_CatalogBoundProvider(provider, self._capabilities()), **arguments
        )

    def _run_generation(
        self,
        generation: int,
        cancelled: Event,
        loop: MusicAgentLoop,
        instruction: str,
        context: MusicAgentExecutionContext,
        reasoning: str | None,
        intent: str,
        intent_template: str,
        conversation_history: tuple[dict[str, str], ...],
        attachments: tuple = (),
    ) -> None:
        failure = None
        try:
            outcome = loop.run(
                instruction,
                context,
                reasoning=reasoning,
                intent=intent,
                intent_template=intent_template,
                conversation_history=conversation_history,
                attachments=attachments,
            )
        except BaseException as error:
            logger.warning("Music assistant turn failed")
            failure = error
            outcome = None
        if cancelled.is_set():
            outcome = None
        if cancelled.is_set() or self._shutting_down:
            resource_session = getattr(self, '_resource_sessions', {}).pop(generation, None)
            if resource_session is not None:
                resource_session.close()
        if not self._shutting_down:
            self._generationFinished.emit(generation, outcome, failure)
        with self._worker_lock:
            self._workers.discard(current_thread())

    @Slot(int, int)
    def _on_generation_validation_repair(self, generation: int, count: int) -> None:
        if generation != self._active_generation:
            return
        self._validation_repair_count = max(1, int(count))
        self._assistant_status = "repairing"
        self.changed.emit()

    def _dispatch_gateway_call(
        self,
        generation: int,
        cancelled: Event,
        method: str,
        args: tuple[object, ...],
    ) -> object:
        if cancelled.is_set() or self._shutting_down:
            raise _GatewayDispatchCancelled()
        call = _GatewayCall(generation, cancelled, method, args, {})
        self._gatewayCallRequested.emit(call)
        while not call.done.wait(0.01):
            if cancelled.is_set() or self._shutting_down:
                raise _GatewayDispatchCancelled()
        if call.error is not None:
            raise call.error
        return call.result

    @Slot(object)
    def _on_gateway_call_requested(self, call: object) -> None:
        if not isinstance(call, _GatewayCall):
            return
        try:
            if (
                self._shutting_down
                or call.cancelled.is_set()
                or call.generation != self._active_generation
            ):
                raise _GatewayDispatchCancelled()
            self._gateway_dispatch_in_progress = True
            self._begin_collaboration_turn_for_write(call)
            gateway = self._gateway_for_generation(call.generation)
            if call.method == 'creator_facts':
                call.result = self._creator_facts(*call.args)
            elif call.method == 'creator_question':
                self._task_progress.set_waiting_for_user(call.generation, True)
                self._streaming_message_rows.pop(call.generation, None)
                self._messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, call.args[0]))
                self._task_progress.report_model(call.generation, dict(message=call.args[0], status='等待你的回复'))
                self.changed.emit()
                call.result = True
            elif call.method == 'creator_question_answered':
                self._task_progress.set_waiting_for_user(call.generation, False)
                self._suppressed_streams.discard(call.generation)
                self._streaming_texts[call.generation] = ''
                self._task_progress.report_model(call.generation, dict(message='已收到回复，模型继续处理。', status='running'))
                self.changed.emit()
                call.result = True
            elif call.method == 'creator_app':
                from ai_piano_studio.music_agent.creator_app import app_operation
                call.result = app_operation(self, *call.args)
            elif call.method == "read_task_history":
                from ai_piano_studio.music_agent.memory_tool import read_task_history
                call.result = read_task_history(self._task_memory, gateway, *call.args)
            elif call.method == 'validate_resource_request':
                from ai_piano_studio.music_agent.resource_tools import validate_resource_request
                call.result = validate_resource_request(gateway, *call.args)
            elif call.method == 'accept_model_song_task':
                call.result = self._accept_model_song_task(self._retry_instruction, *call.args)
            elif call.method == 'appearance_tool':
                from ai_piano_studio.music_agent.style_tools import execute_appearance_tool
                call.result = execute_appearance_tool(self._appearance_tools, gateway, *call.args)
            elif call.method == 'audition_snapshot':
                from ai_piano_studio.music_agent.audition_tools import audition_snapshot
                call.result = audition_snapshot(gateway, *call.args, playback=self._analysis_playback)
            else:
                call.result = getattr(gateway, call.method)(*call.args, **call.kwargs)
        except BaseException as error:
            call.error = error
        finally:
            self._gateway_dispatch_in_progress = False
            call.done.set()

    def _begin_collaboration_turn_for_write(self, call: _GatewayCall) -> None:
        if call.method != "execute" or len(call.args) < 3:
            return
        tool_name, _arguments, context = call.args[:3]
        if not isinstance(context, MusicAgentExecutionContext):
            return
        if context.mode is not MusicExecutionMode.COLLABORATION:
            return
        try:
            definition = self._gateway_for_generation(call.generation).registry.lookup(tool_name)
        except Exception:
            logger.warning("Music assistant could not classify a gateway tool call")
            return
        if not definition.read_only:
            self._gateway_for_generation(call.generation).begin_turn(context)

    def _gateway_for_generation(self, generation: int):
        workflow = self._creation_workflow
        if workflow and workflow.current(generation) and workflow.gateway is not None:
            return workflow.gateway
        return self._gateway

    def _finish_creator(self, generation, outcome, error):
        resource = self._resource_sessions.pop(generation, None)
        if generation != self._active_generation:
            if resource is not None:
                resource.close()
            return
        row = self._streaming_message_rows.pop(generation, None)
        self._streaming_texts.pop(generation, None)
        if not isinstance(outcome, MusicAgentLoopOutcome):
            if resource is not None:
                resource.close()
            self._active_generation = self._active_cancel = self._active_context = None
            self._assistant_status = 'error'
            self._append_error(str(error) if error else '模型请求未返回结果。')
            self._task_progress.finish(generation, 'failed', error_code='execution_error')
            self.changed.emit()
            return
        if outcome.text:
            message = AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, outcome.text)
            if row is None:
                self._messages.append(message)
            else:
                self._messages.replace(row, message)
        self._active_generation = self._active_cancel = self._active_context = None
        self._active_intent = ''
        self._assistant_status = 'idle'
        preview = resource.pending_song if resource is not None else None
        proposal_id = proposal_status = ''
        if preview is not None:
            resource.pending_song = None
            workflow = self._creation_workflow
            workflow.cancel()
            workflow.pending_song = preview
            workflow.result_generation = generation
            workflow.baseline = (self._editor.projectSessionId, self._editor.projectRevision)
            workflow.instruction = self._retry_instruction
            workflow.previewed = False
            workflow.preview_error = ''
            workflow.result_path = ''
            # A tool-only Creator turn has no assistant text row. Retain its
            # user-message anchor when adoption switches to the saved project.
            workflow.response_row = row if row is not None else self.taskMessageRow
            workflow.message_start_row = self._task_message_start_row
            workflow.cancelled = Event()
            self._assistant_status = 'proposal_ready'
            self._task_progress.await_review(generation)
        elif outcome.status == 'proposal':
            proposal = self.presentProposal(outcome.context)
            proposal_id, proposal_status = str(proposal.get('proposalId', '')), str(proposal.get('status', ''))
            self._assistant_status = 'proposal_ready'
            self._task_progress.await_review(generation)
        elif outcome.status == 'finished':
            self._task_progress.finish(generation, 'responded')
        else:
            self._assistant_status = 'error' if outcome.status != 'cancelled' else 'idle'
            self._task_progress.finish(generation, 'cancelled' if outcome.status == 'cancelled' else 'failed',
                                       error_code=outcome.status if outcome.status != 'cancelled' else '')
        if resource is not None:
            resource.close()
        initial_revision = outcome.debug_snapshot.get('initial_revision', outcome.context.baseline_revision)
        initial_project = outcome.debug_snapshot.get('initial_project_identity', outcome.context.project_identity)
        project_changed = f'session:{self._editor.projectSessionId}' != initial_project
        self._set_turn_result(outcome, 'CHAT', outcome.text, proposal_id=proposal_id,
            proposal_status=proposal_status, committed=not project_changed and self._editor.projectRevision > initial_revision,
            revision_before=initial_revision, revision_after=self._editor.projectRevision)
        self._last_turn_result['projectChanged'] = project_changed
        self.changed.emit()
        pending = self._take_pending_messages(generation)
        if pending:
            self._send_creator_message('\n\n'.join(pending), record_user=False)

    @Slot(int, object, object)
    def _on_generation_finished(
        self, generation: int, outcome: object, _error: object
    ) -> None:
        if self._creator_enabled:
            self._finish_creator(generation, outcome, _error)
            return
        resource_session = getattr(self, '_resource_sessions', {}).pop(generation, None)
        if generation == self._active_generation and isinstance(outcome, MusicAgentLoopOutcome):
            if outcome.status in {'finished', 'proposal', 'needs_input', 'no_write', 'resource_preview',
                                  'source_confirmation', 'start_original_draft', 'start_song_draft'}:
                pending = self._take_pending_messages(generation)
                if pending:
                    if self._creation_workflow and self._creation_workflow.current(generation):
                        if resource_session is not None:
                            self._creation_workflow.handoff_resource_session = resource_session
                            self._creation_workflow.discovery_index = resource_session.discovery_context()
                        self._creation_workflow.restart_for_supplement()
                    else:
                        if resource_session is not None:
                            resource_session.close()
                        self._resume_supplemented_turn(generation, outcome)
                    return
        if (generation == self._active_generation and isinstance(outcome, MusicAgentLoopOutcome)
                and outcome.status in {'start_original_draft', 'start_song_draft'}):
            song_task = resource_session.song_task if resource_session is not None else None
            original = outcome.status == 'start_original_draft'
            if resource_session is not None and original:
                resource_session.close()
            old_gateway = self._gateway_for_generation(generation)
            if outcome.context.mode is MusicExecutionMode.PROPOSAL:
                old_gateway.release_proposal(outcome.context)
            else:
                old_gateway.abort_turn(outcome.context)
            if self._song_source_flow is not None:
                self._song_source_flow.clear()
            if original:
                self._memory_accept(self._retry_instruction, 'CREATE_MUSIC', new_task=True)
            row = self._streaming_message_rows.pop(generation, None)
            self._streaming_texts.pop(generation, None)
            self._creation_workflow.start(generation, self._retry_instruction, original_draft=original,
                                          song_task=None if original else song_task, response_row=row,
                                          resource_session=None if original else resource_session)
            return
        if (resource_session is not None and resource_session.pending_source is not None
                and self._song_source_flow is not None
                and generation == self._active_generation
                and isinstance(outcome, MusicAgentLoopOutcome) and outcome.status == 'source_confirmation'):
            proposal = resource_session.pending_source
            baseline, instruction = resource_session.baseline, resource_session.instruction
            song_task = resource_session.song_task
            if song_task is not None:
                instruction += (f'\n当前任务要求：制作《{proposal.title}》的试听，'
                                f'时长为 {song_task["duration_seconds"]:g} 秒。')
            resource_session.close()
            gateway = self._gateway_for_generation(generation)
            if outcome.context.mode is MusicExecutionMode.PROPOSAL:
                gateway.release_proposal(outcome.context)
            else:
                gateway.abort_turn(outcome.context)
            row = self._streaming_message_rows.pop(generation, None)
            self._streaming_texts.pop(generation, None)
            self._creation_workflow.response_row = row
            if not self._song_source_flow.offer(generation, proposal, instruction, baseline,
                                                model_driven=song_task is not None):
                self._creation_workflow.fail('conflict')
            return
        if (resource_session is not None and resource_session.pending_song is not None
                and generation == self._active_generation
                and isinstance(outcome, MusicAgentLoopOutcome) and outcome.status == 'resource_preview'):
            song = resource_session.pending_song
            resource_session.pending_song = None
            resource_session.close()
            row = self._streaming_message_rows.pop(generation, None)
            self._streaming_texts.pop(generation, None)
            gateway = self._gateway_for_generation(generation)
            if outcome.context.mode is MusicExecutionMode.PROPOSAL:
                gateway.release_proposal(outcome.context)
            else:
                gateway.abort_turn(outcome.context)
            self._creation_workflow.accept_resource_preview(generation, song,
                resource_session.baseline, resource_session.instruction, resource_session.message_start_row, row)
            return
        if resource_session is not None:
            if self._creation_workflow and self._creation_workflow.current(generation):
                delivered = {r.data.get('source_sha256') for r in getattr(outcome, 'tool_results', ())
                             if r.success and r.data.get('status') == 'visual_score' and r.data.get('images_delivered') is True}
                self._creation_workflow.score_references = tuple(dict(row) for row in resource_session.score_sources
                                                               if row['sha256'] in delivered)
            resource_session.close()
        if self._creation_workflow and self._creation_workflow.current(generation):
            row = self._streaming_message_rows.pop(generation, None)
            self._streaming_texts.pop(generation, None)
            self._creation_workflow.response_row = row
            self._creation_workflow.model_finished(generation, outcome)
            return
        if generation != self._active_generation:
            self._discard_stale_outcome(outcome)
            return
        if (isinstance(outcome, MusicAgentLoopOutcome) and self._active_extension_seconds is not None
                and (outcome.context.intent_template != 'studio_model_assist' or outcome.write_count)
                and outcome.status in {"finished", "proposal"}):
            state = (self._gateway.capture_proposal(outcome.context).staged_state
                     if outcome.context.mode is MusicExecutionMode.PROPOSAL else self._editor.state)
            note_end = max((n.start_time + n.duration for n in state.active_notes), default=0)
            if note_end + .05 < self._active_extension_seconds:
                if outcome.context.mode is MusicExecutionMode.COLLABORATION:
                    self._gateway.abort_turn(outcome.context)
                else:
                    self._gateway.release_proposal(outcome.context)
                outcome = replace(outcome, status="incomplete_music", text="", proposal=None)
        self._active_generation = None
        self._active_cancel = None
        self._active_context = None
        self._validation_repair_count = 0
        active_intent = self._active_intent
        model_driven = isinstance(outcome, MusicAgentLoopOutcome) and outcome.context.intent_template == 'studio_model_assist'
        if model_driven and outcome.status == 'proposal' and outcome.write_count:
            active_intent = 'EDIT_MUSIC'
            self._memory_accept(self._retry_instruction, active_intent, new_task=
                self._task_memory.state.get('constraints', {}).get('sourceMode') == 'existing_song')
            self._song_source_flow.clear()
        if (isinstance(outcome, MusicAgentLoopOutcome)
                and any(r.success and r.tool_name in {'apply_ui_style','restore_ui_style'}
                        for r in outcome.tool_results)
                and not any(r.success and self._is_write_tool(r.tool_name) for r in outcome.tool_results)):
            active_intent = 'CHAT'
        self._active_intent = ""
        streamed_row = self._streaming_message_rows.pop(generation, None)
        self._streaming_texts.pop(generation, None)
        if not isinstance(outcome, MusicAgentLoopOutcome):
            self._assistant_status = "error"
            self._append_error("助手暂时无法完成请求，请检查连接后重试。")
            self._task_progress.finish(generation, "failed", error_code="execution_error")
            self.changed.emit()
            return
        revision_before = outcome.context.baseline_revision
        revision_after = self._editor.projectRevision
        appearance_results = [r for r in outcome.tool_results if r.success
                              and r.tool_name in {'apply_ui_style','restore_ui_style'}]
        style_only = bool(appearance_results and not any(r.success and self._is_write_tool(r.tool_name)
                                                        for r in outcome.tool_results))
        if style_only and appearance_results[-1].data.get('status') != 'applied':
            outcome = replace(outcome, status='style_unavailable')
        committed = False
        if (
            outcome.status == "finished"
            and outcome.context.mode is MusicExecutionMode.COLLABORATION
            and active_intent in {"CREATE_MUSIC", "EDIT_MUSIC"}
        ):
            successful_writes = sum(
                1
                for result in outcome.tool_results
                if result.success and self._is_write_tool(result.tool_name)
            )
            if successful_writes == 0:
                self._gateway.abort_turn(outcome.context)
                self._assistant_status = "error"
                self._set_turn_result(
                    outcome,
                    active_intent,
                    "",
                    proposal_id="",
                    proposal_status="",
                    committed=False,
                    revision_before=revision_before,
                    revision_after=self._editor.projectRevision,
                )
                self._append_error(
                    "当前模型没有完成实际音乐修改。可以尝试重新生成，或切换一个更适合音乐工具的模型。"
                )
                self._task_progress.finish(generation, "failed", error_code="no_write")
                self.changed.emit()
                return
            revision_before = self._editor.projectRevision
            completion = self._gateway.finish_turn(outcome.context)
            if not completion.success or self._editor.projectRevision == revision_before:
                self._gateway.abort_turn(outcome.context)
                self._assistant_status = "error"
                self._set_turn_result(
                    outcome,
                    active_intent,
                    "",
                    proposal_id="",
                    proposal_status="",
                    committed=False,
                    revision_before=revision_before,
                    revision_after=self._editor.projectRevision,
                )
                self._append_error(
                    "这次修改没有写入工程。可以尝试重新生成，或检查当前音乐范围。"
                )
                self._task_progress.finish(generation, "failed", error_code="conflict")
                self.changed.emit()
                return
            committed = True
            revision_after = self._editor.projectRevision
        staged_proposal = None
        if outcome.status == "proposal":
            try:
                staged_proposal = self._gateway.current_proposal(outcome.context)
            except Exception:
                staged_proposal = None
        has_staged_proposal = bool(
            outcome.status == "proposal"
            and (
                self._write_count(outcome) > 0
                or outcome.proposal is not None
                or staged_proposal is not None
            )
        )
        proposal_status = "pending" if has_staged_proposal else ""
        proposal_id = (
            outcome.proposal.proposal_id
            if outcome.proposal is not None
            else staged_proposal.proposal_id if staged_proposal is not None else ""
        )
        evidence = ResponseGuardEvidence(
            intent=active_intent,
            mode=outcome.context.mode.value,
            status=outcome.status,
            tool_call_count=self._tool_call_count(outcome),
            read_count=self._read_count(outcome),
            write_count=self._write_count(outcome),
            proposal_id=proposal_id,
            proposal_status=proposal_status,
            committed=committed,
            revision_before=revision_before,
            revision_after=revision_after,
            capabilities=self._effective_capabilities_for_context(outcome.context),
        )
        guarded = self._response_guard.guard(self._safe_text(outcome.text), evidence)
        response = (guarded.text if outcome.text.strip() or outcome.status in {"finished", "proposal", "needs_input"} else "")
        if style_only and outcome.status == 'style_unavailable':
            response = ('当前外观设置还有未保存的草稿，已保留它；本轮样式未应用。'
                        if appearance_results[-1].data.get('status') == 'busy'
                        else '本轮样式未应用，请调整样式要求后重试。')
            if any(r.data.get('status') == 'applied' for r in appearance_results[:-1]):
                response = '已有样式更改生效，但最后一次外观操作未应用。'
        if response and outcome.status not in {"no_write"}:
            if streamed_row is None:
                self._messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, response))
            else:
                self._messages.replace(
                    streamed_row,
                    AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, response),
                )
        if committed and any(word in response for word in ("暂存", "点击应用", "尚未应用", "等待应用")):
            self._append_notice("执行回执：本轮修改已直接应用到工程，无需再次点击应用。")
        if appearance_results and appearance_results[-1].data.get('status') == 'applied':
            self._append_notice('执行回执：皮肤样式已应用并保存，可在聊天中要求恢复上一套。')
        has_tool_failure = any(not result.success for result in outcome.tool_results)
        activity = self._music_activity_message(
            outcome.tool_results,
            mode=outcome.context.mode,
            recovered=outcome.status in {"finished", "proposal"}
            and any(result.success for result in outcome.tool_results),
        )
        if outcome.status == "incomplete_music":
            activity = None  # Rolled-back writes must not be presented as applied edits.
        if activity is not None:
            self._messages.append(
                AssistantMessage(
                    AssistantMessageKind.ACTIVITY,
                    activity,
                    title="音乐处理",
                )
            )
        proposal_data: dict[str, object] = {}
        if outcome.status == "proposal":
            proposal_data = self.presentProposal(outcome.context)
            proposal_id = str(proposal_data.get("proposalId", proposal_id))
            proposal_status = str(proposal_data.get("status", proposal_status))
        if outcome.status == "tools_unavailable":
            self._assistant_status = "error"
            self._append_error("当前模型不支持音乐工具，请切换支持工具的模型。")
        elif outcome.status == "model_unavailable":
            self._assistant_status = "error"
            self._append_error("当前模型的工具能力未验证，请选择标为支持音乐工具的模型后重试。")
        elif outcome.status == 'source_unavailable':
            self._assistant_status = 'error'
            if model_driven and resource_session is not None and resource_session.song_task is not None:
                song_task = resource_session.song_task
                pending = self._song_source_flow.pending if self._song_source_flow.public() else None
                if pending is not None:
                    self._assistant_status = 'idle'
                    outcome = replace(outcome, status='source_confirmation')
                    self._memory_record('source_confirmation', {'source': self._song_source_flow._source_facts(
                        pending.proposal, 'awaiting_confirmation')})
                    self._append_notice('上一次找到的候选仍可核对。确认后可继续从这份乐谱生成试听。')
                else:
                    self._memory_record('failed', {'errorCode': 'source_unavailable', 'outcomeStatus': outcome.status})
                    self._append_error(f'尚未找到可确认的《{song_task["title"]}》谱源，'
                                       f'任务仍保留 {song_task["duration_seconds"]:g} 秒的要求。')
            else:
                self._append_error('本轮获取音乐资料的尝试尚未准备出可试听作品，原工程保留；可以让助手继续尝试其他来源或格式。')
        elif outcome.status == 'style_unavailable':
            self._assistant_status = 'error'
        elif outcome.status in _PROVIDER_FAILURE_MESSAGES:
            self._assistant_status = "error"
            self._append_error(_PROVIDER_FAILURE_MESSAGES[outcome.status])
        elif outcome.status == "no_write":
            self._assistant_status = "error"
            self._append_error(
                "当前模型没有完成实际音乐修改。可以尝试重新生成，或切换一个更适合音乐工具的模型。"
            )
        elif outcome.status in _TOOL_FAILURE_MESSAGES:
            self._assistant_status = "error"
            if not has_tool_failure:
                self._append_error(_TOOL_FAILURE_MESSAGES[outcome.status])
        elif outcome.status not in {"finished", "cancelled", "proposal", "needs_input"}:
            self._assistant_status = "error"
            self._append_error("助手暂时无法完成请求，请检查连接后重试。")
        else:
            self._assistant_status = "idle"
        self._set_turn_result(
            outcome,
            active_intent,
            response,
            proposal_id=proposal_id,
            proposal_status=proposal_status,
            committed=committed,
            revision_before=revision_before,
            revision_after=revision_after,
        )
        if outcome.status == "proposal":
            self._task_progress.await_review(generation)
        elif outcome.status == 'source_confirmation':
            self._task_progress.await_source_confirmation(generation)
        elif outcome.status == "finished" and self._assistant_status != "error":
            self._task_progress.finish(generation, "completed")
        else:
            self._task_progress.finish(generation, "cancelled" if outcome.status == "cancelled" else "failed", error_code="" if outcome.status == "cancelled" else outcome.status)
        self.changed.emit()

    def _resume_supplemented_turn(self, generation, outcome):
        """Cover input arriving after the worker's final checkpoint, before UI delivery."""
        context = outcome.context
        intent = self._active_intent
        if context.mode is MusicExecutionMode.COLLABORATION:
            context = self._gateway.create_execution_context(
                authorized_bars=context.authorized_bars, permissions=context.permissions,
                mode=context.mode, selected_note_ids=context.selected_note_ids,
                intent_template=context.intent_template, limits=context.budget.limits,
                append_after_seconds=context.append_after_seconds)
        if intent == "CREATE_MUSIC" and outcome.write_count:
            intent = "EDIT_MUSIC"
        self._active_context = context
        self._active_intent = intent
        self._assistant_status = "thinking"
        try:
            loop = self._create_agent_loop(generation, self._active_cancel)
        except Exception as error:
            self._invalidate_active_generation()
            self._task_progress.finish(generation, "failed", error_code=getattr(error, "code", "provider_unavailable"))
            self._assistant_status = "error"
            self._append_error("补充消息已保留，但模型连接未能继续，请重试。")
            self.changed.emit()
            return
        worker = Thread(target=self._run_generation, args=(generation, self._active_cancel, loop,
            self._retry_instruction, context, self.providerReasoningValue or None, intent,
            context.intent_template, self._conversation_history(), ()), daemon=True, name="music-supplement-turn")
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        self.changed.emit()

    @Slot(int)
    def _on_generation_response_started(self, generation: int) -> None:
        if generation == self._active_generation:
            self._suppressed_streams.discard(generation)
            self._streaming_texts[generation] = ''
            row = self._streaming_message_rows.get(generation)
            if row is not None and any(m.kind == AssistantMessageKind.USER_MESSAGE
                    for m in self._messages.entries[row + 1:]):
                self._streaming_message_rows.pop(generation, None)

    @Slot(int, str)
    def _on_generation_text_delta(self, generation: int, text: str) -> None:
        if generation != self._active_generation or not text or generation in self._suppressed_streams:
            return
        streamed_text = (
            self._streaming_texts.get(generation, "") + text
        )
        if not self._creator_enabled:
            streamed_text = streamed_text[:_MAX_ASSISTANT_MESSAGE_CHARS]
        self._streaming_texts[generation] = streamed_text
        context = self._active_context
        proposal = self._proposal_manager.proposal
        evidence = ResponseGuardEvidence(
            intent=self._active_intent,
            mode=context.mode.value if context is not None else self._mode,
            status="streaming",
            proposal_id=proposal.proposal_id if proposal is not None else "",
            proposal_status=(
                proposal.status.value
                if proposal is not None and proposal.status is MusicEditProposalStatus.PENDING
                else ""
            ),
            capabilities=self._effective_capabilities_for_context(context),
        )
        streamed_response = streamed_text if self._creator_enabled else self._response_guard.guard(
            self._safe_text(streamed_text), evidence
        ).text
        row = self._streaming_message_rows.get(generation)
        message = AssistantMessage(
            AssistantMessageKind.ASSISTANT_MESSAGE,
            streamed_response,
        )
        if row is None:
            row = len(self._messages.entries)
            self._messages.append(message)
            self._streaming_message_rows[generation] = row
        else:
            self._messages.replace(row, message)

    @staticmethod
    def _tool_result_message(result: MusicToolResult) -> str:
        if result.success:
            return "已处理音乐工具。"
        if result.error_code == "validation_error":
            invalid_items = result.data.get("invalid_items")
            if isinstance(invalid_items, list) and invalid_items:
                return (
                    f"有 {len(invalid_items)} 项音乐参数仍不符合当前工程范围，"
                    "本次修改未应用。"
                )
        return _TOOL_FAILURE_MESSAGES.get(result.error_code, "音乐工具执行失败，请重试。")

    @classmethod
    def _compact_tool_result_messages(
        cls, results: tuple[MusicToolResult, ...]
    ) -> tuple[tuple[str, int], ...]:
        compacted: list[tuple[str, int]] = []
        for result in results:
            text = cls._tool_result_message(result)
            if compacted and compacted[-1][0] == text:
                compacted[-1] = (text, compacted[-1][1] + 1)
            else:
                compacted.append((text, 1))
        return tuple(compacted)

    @classmethod
    def _music_activity_message(
        cls,
        results: tuple[MusicToolResult, ...],
        *,
        mode: MusicExecutionMode = MusicExecutionMode.COLLABORATION,
        recovered: bool = False,
    ) -> str | None:
        """Describe real mutations without making staged Proposal work look committed."""
        if not results:
            return None
        staged = mode is MusicExecutionMode.PROPOSAL or mode == MusicExecutionMode.PROPOSAL.value
        verb = "建议新增" if staged else "已新增"
        update_verb = "建议调整" if staged else "已修改"
        delete_verb = "建议删除" if staged else "已删除"
        successful = tuple(result for result in results if result.success)
        failures = cls._compact_tool_result_messages(
            tuple(result for result in results if not result.success)
        )
        lines: list[str] = []
        added_notes = sum(result.added_notes for result in successful)
        updated_notes = sum(result.updated_notes for result in successful)
        deleted_notes = sum(result.deleted_notes for result in successful)
        if added_notes:
            lines.append(f"{verb} {added_notes} 个音符。")
        if updated_notes:
            lines.append(f"{update_verb} {updated_notes} 个音符。")
        if deleted_notes:
            lines.append(f"{delete_verb} {deleted_notes} 个音符。")
        if any(result.tool_name == "set_bpm" for result in successful):
            lines.append("建议调整速度。" if staged else "已调整速度。")
        if any(
            result.tool_name == "apply_chord"
            and result.added_notes == result.updated_notes == result.deleted_notes == 0
            for result in successful
        ):
            lines.append("建议应用和弦。" if staged else "已应用和弦。")
        if recovered and failures:
            lines.append("已根据参数校验反馈完成修正。")
        else:
            lines.extend(
                text if count == 1 else f"{text.rstrip('。')}（{count} 项）。"
                for text, count in failures
            )
        return "\n".join(lines) if lines else None

    def _invalidate_active_generation(self) -> None:
        flow = getattr(self, '_song_source_flow', None)
        if flow:
            if flow.pending is not None:
                generation = self.taskProgress.get('generationId')
                if generation is not None:
                    self._task_progress.finish(generation, 'cancelled')
            flow.clear()
            flow.rejected_sources.clear()
        with self._pending_messages_lock:
            self._pending_messages.clear()
        self._suppressed_streams.clear()
        if self._active_generation is not None:
            self._task_progress.finish(self._active_generation, "cancelled")
        context = self._active_context
        cancelled = self._active_cancel
        if cancelled is not None:
            cancelled.set()
        for resource_session in tuple(getattr(self, '_resource_sessions', {}).values()):
            resource_session.close()
        self._resource_sessions.clear()
        if self._creation_workflow and (self._creation_workflow.generation is not None or self._creation_workflow.pending_song is not None):
            self._creation_workflow.cancel()
        self._generation += 1
        self._active_generation = None
        self._active_cancel = None
        self._active_context = None
        self._active_intent = ""
        self._validation_repair_count = 0
        if context is not None and context.mode is MusicExecutionMode.COLLABORATION:
            self._gateway.abort_turn(context)
        elif context is not None:
            self._gateway.release_proposal(context)
        proposal = self._proposal_manager.proposal
        self._assistant_status = (
            "proposal_ready"
            if proposal is not None and proposal.status is MusicEditProposalStatus.PENDING
            else "idle"
        )
        self.changed.emit()

    def _discard_stale_outcome(self, outcome: object) -> None:
        if not isinstance(outcome, MusicAgentLoopOutcome):
            return
        try:
            if outcome.context.mode is MusicExecutionMode.COLLABORATION:
                self._gateway.abort_turn(outcome.context)
            else:
                self._gateway.release_proposal(outcome.context)
        except Exception:
            logger.warning("Discarding stale music assistant turn failed")

    def _cancel_if_active(self) -> None:
        if self._active_generation is not None:
            self._invalidate_active_generation()

    def _conversation_history(self) -> tuple[dict[str, str], ...]:
        """Expose only prior natural-language turns to the provider context builder."""
        history: list[dict[str, str]] = []
        for entry in self._messages.entries[self._conversation_start_row:]:
            if entry.kind is AssistantMessageKind.USER_MESSAGE:
                history.append({"role": "user", "content": entry.text})
            elif entry.kind is AssistantMessageKind.ASSISTANT_MESSAGE:
                history.append({"role": "assistant", "content": entry.text})
        return tuple(history)

    @staticmethod
    def _safe_text(text: str) -> str:
        compact = text.strip()[:_MAX_ASSISTANT_MESSAGE_CHARS]
        visible = re.sub(
            r"<(?P<tag>think|analysis|reasoning)>.*?</(?P=tag)>",
            "",
            compact,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        if not visible:
            return "助手已完成回复。"
        machine_candidate = visible
        if machine_candidate.startswith("```"):
            machine_candidate = re.sub(
                r"^```(?:json|javascript|js|python)?\s*|\s*```$",
                "",
                machine_candidate,
                flags=re.IGNORECASE | re.DOTALL,
            ).strip()
        try:
            parsed = json.loads(machine_candidate)
        except (TypeError, ValueError):
            parsed = None
        lowered = machine_candidate.lower()
        if isinstance(parsed, (dict, list)) or (
            visible.startswith(("{", "[", "```"))
            and any(
                marker in lowered
                for marker in (
                    '"tool"',
                    '"tool_calls"',
                    '"arguments"',
                    '"function"',
                    '"call_id"',
                    '"tool_name"',
                    '"reasoning"',
                )
            )
        ):
            return "助手已完成回复。"
        return visible

    def _tool_call_count(self, outcome: MusicAgentLoopOutcome) -> int:
        return len(outcome.tool_results)

    def _write_count(self, outcome: MusicAgentLoopOutcome) -> int:
        return sum(
            1
            for result in outcome.tool_results
            if result.success and self._is_write_tool(result.tool_name)
        )

    def _read_count(self, outcome: MusicAgentLoopOutcome) -> int:
        successful = sum(1 for result in outcome.tool_results if result.success)
        return max(0, successful - self._write_count(outcome))

    def _effective_capabilities_for_context(
        self, context: MusicAgentExecutionContext | None
    ) -> EffectiveCapabilitySet:
        permissions = (
            context.permissions
            if isinstance(context, MusicAgentExecutionContext)
            else self._music_permissions()
        )
        return EffectiveCapabilitySet.from_sources(
            self._app_capabilities,
            self._capabilities(),
            permissions,
        )

    def _set_turn_result(
        self,
        outcome: MusicAgentLoopOutcome,
        intent: str,
        final_text: str,
        *,
        proposal_id: str,
        proposal_status: str,
        committed: bool,
        revision_before: int | None,
        revision_after: int | None,
    ) -> None:
        safe_final_text = final_text.strip() or (
            "当前模型没有完成实际音乐修改。"
            if intent in {"CREATE_MUSIC", "EDIT_MUSIC"}
            else "助手未返回可显示的文字。"
        )
        self._last_turn_result = AssistantTurnResult(
            status=outcome.status,
            intent=intent,
            mode=outcome.context.mode.value,
            tool_call_count=self._tool_call_count(outcome),
            read_count=self._read_count(outcome),
            write_count=self._write_count(outcome),
            proposal_id=proposal_id,
            proposal_status=proposal_status,
            revision_before=revision_before,
            revision_after=revision_after,
            committed=committed,
            capabilities=self._effective_capabilities_for_context(outcome.context).to_dict(),
            final_text=safe_final_text,
            provider_calls=int(outcome.debug_snapshot.get("provider_calls", 0)),
            validation_model_repairs=int(
                outcome.debug_snapshot.get("validation_model_repairs", 0)
            ),
            local_repairs=int(outcome.debug_snapshot.get("local_repairs", 0)),
        ).to_dict()

        successful = tuple(result for result in outcome.tool_results if result.success)
        self._last_turn_result["executionEvidence"] = {
            "intent": intent,
            "toolCallCount": self._tool_call_count(outcome),
            "readCount": self._read_count(outcome),
            "writeCount": self._write_count(outcome),
            "notesAdded": sum(result.added_notes for result in successful),
            "notesUpdated": sum(result.updated_notes for result in successful),
            "notesDeleted": sum(result.deleted_notes for result in successful),
            "noteReads": [
                {"startBar": result.data["start_bar"], "endBar": result.data["end_bar"],
                 "count": len(result.data.get("notes", []))}
                for result in successful if result.tool_name == "get_notes"
                and "start_bar" in result.data and "end_bar" in result.data
            ],
            "projectNoteCount": next((result.data.get("active_note_count") for result in reversed(successful)
                                      if result.tool_name == "get_project_summary"), None),
        }

        if intent in {"CREATE_MUSIC", "EDIT_MUSIC"}:
            status = "completed" if committed else "proposal" if proposal_id else "failed"
            self._memory_record(status, {"outcomeStatus": outcome.status,
                "committed": committed, "proposalId": proposal_id,
                "executionEvidence": self._last_turn_result.get("executionEvidence", {}),
                "contextCoverage": [step.get("contextCoverage", {}) for step in outcome.debug_snapshot.get("steps", ())]})

    def _is_write_tool(self, tool_name: str) -> bool:
        try:
            return not self._gateway.registry.lookup(tool_name).read_only
        except Exception:
            return False

    @Slot(object, result="QVariantMap")
    def presentProposal(self, context: object) -> dict[str, object]:
        if not isinstance(context, MusicAgentExecutionContext):
            raise ValueError("proposal context must be MusicAgentExecutionContext")
        proposal = self._proposal_manager.register(context)
        self._assistant_status = "proposal_ready"
        self._messages.append(
            AssistantMessage(
                AssistantMessageKind.PROPOSAL,
                "已生成修改建议。",
                title="修改建议",
                proposal_id=proposal.proposal_id,
            )
        )
        self.changed.emit()
        return self.proposal

    @Slot(result="QVariantMap")
    def auditionProposal(self) -> dict[str, object]:
        revision_before = self._editor.projectRevision
        return self._handle_proposal_result(
            self._proposal_manager.audition_proposal(), revision_before=revision_before
        )

    @Slot(result="QVariantMap")
    def auditionOriginalProposal(self) -> dict[str, object]:
        """Audition the frozen baseline side of the current Proposal A/B pair."""
        revision_before = self._editor.projectRevision
        return self._handle_proposal_result(
            self._proposal_manager.audition_original_proposal(),
            revision_before=revision_before,
        )

    @Slot(result="QVariantMap")
    def applyProposal(self) -> dict[str, object]:
        revision_before = self._editor.projectRevision
        return self._handle_proposal_result(
            self._proposal_manager.apply_proposal(), revision_before=revision_before
        )

    @Slot(result="QVariantMap")
    def discardProposal(self) -> dict[str, object]:
        revision_before = self._editor.projectRevision
        return self._handle_proposal_result(
            self._proposal_manager.discard_proposal(), revision_before=revision_before
        )

    @Slot(str, result="QVariantMap")
    def providerSetup(self, provider_id: str) -> dict[str, object]:
        try:
            config = self._provider_settings.public_config(provider_id)
        except ValueError:
            return {}
        definition = next(
            (item for item in self._provider_settings.provider_catalog() if item["id"] == config["providerType"]),
            None,
        )
        return {**(definition or {}), **config}

    @Slot()
    def clearSavedCredentials(self) -> None:
        self._connection_probe_serial += 1
        self._connection_tested = False
        self._cancel_if_active()
        self._provider_settings.clear_saved_credentials()
        self._connection_result = ConnectionResult(
            code=self._connection_code_unknown(), message="已删除保存的 API Key。"
        )
        self.changed.emit()

    @Slot(str, str, result=bool)
    def requiresThirdPartyWarning(self, provider_id: str, base_url: str) -> bool:
        try:
            return self._provider_settings.requires_third_party_warning(provider_id, base_url)
        except ValueError:
            return False

    @Slot(str, str, str, str, str, bool, result="QVariantMap")
    def saveProviderSettings(
        self,
        provider_id: str,
        display_name: str,
        base_url: str,
        api_key: str,
        model_id: str,
        third_party_acknowledged: bool,
    ) -> dict[str, object]:
        self._catalog_error = None
        previous_provider_id = self._provider_id
        previous_model_id = self._model_id
        previous_endpoint = self._provider_settings.public_config(self._provider_id)["baseUrl"]
        self._cancel_if_active()
        try:
            config = self._provider_settings.save(
                provider_id,
                display_name,
                base_url,
                api_key,
                model_id,
                third_party_acknowledged,
            )
        except ThirdPartyWarningRequired:
            return {"success": False, "needsAcknowledgement": True}
        except BaseException as error:
            self._connection_probe_serial += 1
            self._connection_tested = False
            self._connection_result = map_connection_error(error, api_key)
            self.changed.emit()
            return {"success": False, "needsAcknowledgement": False}
        self._provider_id = provider_id
        configured_model_id = str(config["modelId"])
        self._model_id = self._catalog_model_id(provider_id, configured_model_id)
        self._reasoning_id = "off"
        # A save is not a new connection: keep the result for the exact tested
        # transport. A supplied key is a credential replacement even if masked
        # UI fields otherwise look unchanged; never compare or expose secrets.
        transport_changed = (
            provider_id != previous_provider_id
            or self._model_id != previous_model_id
            or config["baseUrl"] != previous_endpoint
            or bool(api_key)
        )
        if transport_changed:
            self._connection_probe_serial += 1
            self._connection_tested = False
            self._connection_result = ConnectionResult(
                self._connection_code_unknown(), "配置已保存，尚未测试连接。"
            )
        self._set_model_unavailable_result(configured_model_id, self._model_id)
        self.changed.emit()
        return {"success": True, "needsAcknowledgement": False}

    @Slot(str, str, str, str, bool, result="QVariantMap")
    def createCustomConnection(
        self,
        display_name: str,
        base_url: str,
        api_key: str,
        model_id: str,
        third_party_acknowledged: bool,
    ) -> dict[str, object]:
        """Create a separate compatible profile without exposing its saved credential."""
        self._catalog_error = None
        self._connection_probe_serial += 1
        self._connection_tested = False
        self._cancel_if_active()
        try:
            config = self._provider_settings.create_custom_profile(
                display_name,
                base_url,
                model_id,
                api_key,
                third_party_acknowledged=third_party_acknowledged,
            )
        except ThirdPartyWarningRequired:
            return {"success": False, "needsAcknowledgement": True}
        except BaseException as error:
            self._connection_result = map_connection_error(error, api_key)
            self.changed.emit()
            return {"success": False, "needsAcknowledgement": False}
        self._provider_id = str(config["providerId"])
        self._model_id = self._catalog_model_id(self._provider_id, str(config["modelId"]))
        self._reasoning_id = "off"
        self._connection_result = ConnectionResult(self._connection_code_unknown(), "连接已保存，尚未测试。")
        self.changed.emit()
        return {"success": True, "needsAcknowledgement": False}

    @Slot(result=bool)
    def deleteCurrentCustomConnection(self) -> bool:
        """Delete only a user-created connection, returning the UI to compatible setup."""
        self._connection_probe_serial += 1
        self._connection_tested = False
        try:
            self._provider_settings.delete_profile(self._provider_id)
        except ValueError:
            return False
        self._cancel_if_active()
        self._provider_id = "compatible"
        self._model_id = self._initial_model_id(self._provider_id)
        self._reasoning_id = "off"
        self._connection_result = ConnectionResult(self._connection_code_unknown(), "已删除自定义连接。")
        self.changed.emit()
        return True

    @Slot(str, result="QVariantMap")
    def testConnection(self, provider_id: str) -> dict[str, str]:
        self._connection_probe_serial += 1
        self._connection_tested = True
        selected_model_id = self._model_id if provider_id == self._provider_id else None
        self._connection_result = self._provider_settings.test_connection(provider_id, selected_model_id)
        if provider_id == self._provider_id and self._connection_result.code.value == "SUCCESS":
            self._update_music_tool_verification_result(
                self._provider_id, self._model_id, self._connection_result
            )
        if (
            self._connection_result.code.value == "SUCCESS"
            and self._assistant_status == "error"
        ):
            self._assistant_status = "idle"
        self.changed.emit()
        return self.connectionResult

    @Slot(str, result=bool)
    def testConnectionAsync(self, provider_id: str) -> bool:
        """Probe a captured transport off-thread; apply results on the Qt thread."""
        if self._shutting_down or self._connection_testing or provider_id != self._provider_id:
            return False
        self._connection_tested = True
        model_id = self._model_id
        try:
            provider = self._provider_settings.create_provider(provider_id, model_id=model_id)
            if isinstance(provider, CodexProvider):
                provider.is_cancelled = lambda: self._shutting_down
            verify = self._provider_settings._requires_tool_capability_verification(provider_id, model_id)
        except BaseException as error:
            self._connection_result = map_connection_error(error)
            self.changed.emit()
            return False
        self._connection_probe_serial += 1
        serial = self._connection_probe_serial
        self._connection_testing = True
        worker = Thread(
            target=self._run_connection_probe,
            args=(serial, provider_id, model_id, provider, verify),
            name="music-agent-connection-test", daemon=True,
        )
        with self._worker_lock:
            self._workers.add(worker)
        worker.start()
        self.changed.emit()
        return True

    def _run_connection_probe(
        self, serial: int, provider_id: str, model_id: str, provider: object, verify: bool
    ) -> None:
        capabilities = None
        error = None
        try:
            provider.test_connection()
            if verify:
                verifier = getattr(provider, "verify_tool_capability", None)
                if not callable(verifier):
                    raise ProviderError("selected model does not support streaming tool calls")
                capabilities = verifier()
                if not isinstance(capabilities, ModelCapabilities):
                    raise ProviderError("selected model does not support streaming tool calls")
        except BaseException as caught:
            error = caught
        self._connectionProbeFinished.emit(serial, provider_id, model_id, capabilities, error)
        with self._worker_lock:
            self._workers.discard(current_thread())

    @Slot(int, str, str, object, object)
    def _on_connection_probe_finished(
        self, serial: int, provider_id: str, model_id: str, capabilities: object, error: object
    ) -> None:
        self._connection_testing = False
        if (self._shutting_down or serial != self._connection_probe_serial
                or provider_id != self._provider_id or model_id != self._model_id):
            self.changed.emit()
            return
        from ai_piano_studio.music_agent.provider_settings import ConnectionResultCode

        if isinstance(error, BaseException):
            self._connection_result = map_connection_error(error)
        else:
            if isinstance(capabilities, ModelCapabilities):
                try:
                    self._provider_settings.record_capability_verification(provider_id, model_id, capabilities)
                except BaseException as caught:
                    self._connection_result = map_connection_error(caught)
                    self.changed.emit()
                    return
            message = ("连接成功；这个模型可对话，但不能修改音乐。"
                       if isinstance(capabilities, ModelCapabilities) and not capabilities.supports_tools
                       else "连接成功，可以开始使用。")
            self._connection_result = ConnectionResult(ConnectionResultCode.SUCCESS, message)
        self._update_music_tool_verification_result(provider_id, model_id, self._connection_result)
        if self._connection_result.code.value == "SUCCESS" and self._assistant_status == "error":
            self._assistant_status = "idle"
        self.changed.emit()

    @Slot()
    def shutdown(self) -> None:
        self._shutting_down = True
        self._invalidate_active_generation()
        self._creator_workspace.close()
        for view in self._creator_views:
            view.deleteLater()
        self._creator_views.clear()
        with self._worker_lock:
            workers = tuple(self._workers)
        for worker in workers:
            if worker is not current_thread():
                worker.join(timeout=0.1)
        if self._proposal_manager.proposal is not None and self._proposal_manager.proposal.status is MusicEditProposalStatus.PENDING:
            self._proposal_manager.discard_proposal()
        self.windowCloseRequested.emit()

    def clear_temporary_conversation(self) -> None:
        """Cancel and forget local source context for ephemeral creative scopes."""
        self._invalidate_active_generation()
        self._proposal_manager.discard_proposal()
        if self._messages._history is None:
            self._messages.clear()
        self._conversation_start_row = len(self._messages.entries)
        self._streaming_message_rows.clear()
        self._streaming_texts.clear()
        self._last_music_intent = ""
        self._pending_music_intent = ""
        self._pending_music_instruction = ""
        self._pending_music_intent_template = ""
        self._last_music_authorized_bars = ()
        self._last_music_permissions = frozenset()
        self._last_music_selected_note_ids = ()
        self._last_audio_import_summary = ""
        self._last_turn_result = {}
        self._assistant_status = "idle"
        self.changed.emit()

    @Property(str, notify=changed)
    def conversationError(self) -> str:
        return self._messages.history_error or self._task_memory_error

    @Slot()
    def clearConversation(self) -> None:
        """Only the user's explicit clear action removes workspace history."""
        try:
            self._task_memory.clear()
        except (OSError, ValueError, sqlite3.Error):
            self._task_memory_error = "任务记忆清除失败，聊天记录保留；请检查本地存储后重试。"
            self.changed.emit()
            return
        recovery = getattr(self, '_song_recovery_task', None)
        if recovery is not None:
            recovery.cancel()
            self._song_recovery_muted_through = recovery.taskGeneration
        self.clear_temporary_conversation()
        self._messages.clear()
        if not self._messages.entries:
            self._conversation_start_row = 0
            self._task_display_receipts.pop(self._conversation_key, None)
            self._task_progress.clear()
            self.conversationChanged.emit()
        self.changed.emit()

    def _on_editor_state(self) -> None:
        if self._creator_enabled and self._gateway_dispatch_in_progress:
            self._last_project_session_id = self._editor.projectSessionId
            return
        flow = getattr(self, '_song_source_flow', None)
        if flow and flow.pending is not None and flow.pending.baseline != flow.baseline():
            flow.clear()
            flow.rejected_sources.clear()
            generation = self.taskProgress.get('generationId')
            if generation is not None:
                self._task_progress.finish(generation, 'cancelled')
        workflow = self._creation_workflow
        if (workflow and workflow.generation is not None and self._active_generation == workflow.generation
                and workflow.baseline != (self._editor.projectSessionId, self._editor.projectRevision)):
            self._invalidate_active_generation()
            self._append_notice("工程已发生变化，已取消本轮音乐创作；原工程保留。")
            self.changed.emit()
            if self._editor.projectSessionId == self._last_project_session_id:
                return
        if self._editor.projectSessionId != self._last_project_session_id:
            was_collaboration = self._mode == "collaboration"
            self._cancel_if_active()
            self._last_project_session_id = self._editor.projectSessionId
            self._bind_project_conversation()
            self._last_music_intent = ""
            self._last_turn_result = {}
            self._pending_music_intent = ""
            self._pending_music_instruction = ""
            self._pending_music_intent_template = ""
            self._last_music_authorized_bars = ()
            self._last_music_permissions = frozenset()
            self._last_music_selected_note_ids = ()
            self._mode = "suggestion"
        context = self._active_context
        if (
            self._active_generation is not None
            and not self._gateway_dispatch_in_progress
            and context is not None
            and context.baseline_revision != self._editor.projectRevision
        ):
            self._invalidate_active_generation()
            self._append_notice("工程已发生变化，已取消本轮 AI 操作。")
            self.changed.emit()
            return
        if self._active_generation is not None:
            return
        proposal = self._proposal_manager.proposal
        self._assistant_status = "proposal_ready" if proposal is not None and proposal.status is MusicEditProposalStatus.PENDING else "idle"
        self.changed.emit()

    def _capabilities(self):
        return self._provider_settings.capabilities_for(self._provider_id, self._model_id)

    def _clear_music_tool_verification_feedback(self) -> None:
        self._music_tool_verifying = False
        self._music_tool_verification_provider_id = ""
        self._music_tool_verification_model_id = ""
        self._music_tool_verification_failure = None

    def _update_music_tool_verification_result(
        self, provider_id: str, model_id: str, result: ConnectionResult
    ) -> None:
        if result.code.value == "SUCCESS":
            self._music_tool_verification_failure = None
            return
        self._music_tool_verification_failure = (provider_id, model_id, result)

    def _initial_model_id(self, provider_id: str) -> str:
        catalog = self._provider_settings.model_catalog(provider_id)
        return str(catalog[0]["id"]) if catalog else ""

    def _catalog_model_id(self, provider_id: str, preferred_model_id: str) -> str:
        catalog = self._provider_settings.model_catalog(provider_id)
        available_model_ids = {str(item["id"]) for item in catalog}
        if preferred_model_id in available_model_ids:
            return preferred_model_id
        return str(catalog[0]["id"]) if catalog else ""

    def _set_model_unavailable_result(self, configured_model_id: str, active_model_id: str) -> None:
        if configured_model_id and configured_model_id != active_model_id:
            self._connection_result = ConnectionResult(
                self._connection_result.code,
                "已保存配置，但所选模型当前不可用；请选择目录中的模型或刷新模型列表。",
            )

    def _handle_proposal_result(
        self, result: MusicToolResult, *, revision_before: int | None = None
    ) -> dict[str, object]:
        if revision_before is None:
            revision_before = self._editor.projectRevision
        if result.success:
            if result.summary == "proposal applied":
                self._task_progress.finish(self._generation, "completed")
                self._append_notice("已应用")
            elif result.summary == "proposal discarded":
                self._task_progress.finish(self._generation, "cancelled")
                self._append_notice("已放弃")
            elif result.summary == "proposal audition started":
                self._append_notice("正在试听建议。")
            elif result.summary == "original proposal audition started":
                self._append_notice("正在试听原版。")
        elif result.error_code == "conflict" and "变化" in result.summary:
            self._append_error("工程已发生变化，请重新生成建议。")
        else:
            self._append_error("无法完成该建议操作，请重试。")
        proposal = self._proposal_manager.proposal
        status = proposal.status.value if proposal is not None else ""
        revision_after = self._editor.projectRevision
        committed = bool(
            result.success
            and status == MusicEditProposalStatus.APPLIED.value
            and revision_after > revision_before
        )
        self._assistant_status = "proposal_ready" if proposal is not None and proposal.status is MusicEditProposalStatus.PENDING else "idle"
        message = self.proposal.get("statusText", "") if status else result.summary
        prior_evidence = (
            self._last_turn_result.get("executionEvidence")
            if proposal is not None and self._last_turn_result.get("proposalId") == proposal.proposal_id
            else None
        )
        self._last_turn_result = AssistantTurnResult(
            status="local_proposal_action" if result.success else "local_proposal_error",
            intent="READ_MUSIC",
            mode=self._mode,
            tool_call_count=0,
            read_count=0,
            write_count=0,
            proposal_id=proposal.proposal_id if proposal is not None else "",
            proposal_status=status,
            revision_before=revision_before,
            revision_after=revision_after,
            committed=committed,
            capabilities=self._effective_capabilities_for_context(None).to_dict(),
            final_text=message,
        ).to_dict()
        if prior_evidence is not None:
            self._last_turn_result["executionEvidence"] = prior_evidence
        if result.success and result.summary in {"proposal applied", "proposal discarded"}:
            self._memory_record("completed" if committed else "cancelled", {"proposalStatus": status, "committed": committed})
        self._last_turn_result["auditionStarted"] = bool(
            result.success and "audition started" in result.summary
        )
        self.changed.emit()
        return {
            "success": result.success,
            "status": status,
            "proposalId": proposal.proposal_id if proposal is not None else "",
            "message": message,
        }

    def _append_notice(self, text: str) -> None:
        self._messages.append(AssistantMessage(AssistantMessageKind.SYSTEM_NOTICE, text))

    def _append_error(self, text: str) -> None:
        self._messages.append(AssistantMessage(AssistantMessageKind.ERROR, text))

    @staticmethod
    def _proposal_status_text(status: MusicEditProposalStatus) -> str:
        return {
            MusicEditProposalStatus.PENDING: "等待你的确认。",
            MusicEditProposalStatus.APPLIED: "已应用",
            MusicEditProposalStatus.DISCARDED: "已放弃",
            MusicEditProposalStatus.STALE: "工程已发生变化，请重新生成建议。",
            MusicEditProposalStatus.FAILED: "应用失败，请重新生成建议。",
        }[status]

    @staticmethod
    def _intent_label(action_id: str) -> str:
        template = get_quick_action_template(action_id)
        return template.label if template is not None else "按当前请求"

    @staticmethod
    def _proposal_quality_warning(
        intent_template: str, summary: dict[str, object]
    ) -> str:
        """Return a conservative warning when actual facts do not match intent."""

        note_changes = sum(
            int(summary.get(key, 0))
            for key in ("notesAdded", "notesUpdated", "notesDeleted")
        )
        if intent_template == "add_chords" and not int(summary.get("harmonyChanged", 0)):
            if not note_changes:
                return "未检测到和弦或音符变化，请先查看变化或试听后再决定。"
        if intent_template == "add_bass" and not int(summary.get("notesAdded", 0)):
            if not int(summary.get("pitchChanged", 0)):
                return "未检测到新增低音相关变化，请先查看变化或试听。"
        if intent_template == "make_climax":
            if (
                int(summary.get("velocityChanged", 0))
                and not int(summary.get("pitchChanged", 0))
                and not int(summary.get("notesAdded", 0))
            ):
                return "当前实际变化主要是力度调整，未检测到音区或结构扩展。"
        return ""

    @staticmethod
    def _note_change_dict(change) -> dict[str, object]:
        return {
            "noteId": change.note_id,
            "before": dict(change.before),
            "after": dict(change.after),
        }

    @staticmethod
    def _summary_dict(summary: ProposalChangeSummary) -> dict[str, object]:
        bars = summary.affected_bars
        return {
            "notesAdded": summary.notes_added,
            "notesUpdated": summary.notes_updated,
            "notesDeleted": summary.notes_deleted,
            "barStart": bars.start_bar if bars is not None else 0,
            "barEnd": bars.end_bar if bars is not None else 0,
            "bpmBefore": summary.bpm_before,
            "bpmAfter": summary.bpm_after,
        }
