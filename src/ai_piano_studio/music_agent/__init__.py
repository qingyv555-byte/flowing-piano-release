"""Security-first building blocks for the AI Music Agent Assistant."""

from ai_piano_studio.music_agent.capabilities import (
    AssistantCapabilitySet,
    EffectiveCapabilitySet,
    ModelCapabilities,
    ReasoningOption,
    build_capability_prompt,
)
from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.credentials import (
    CredentialStoreError,
    CredentialNotFoundError,
    CredentialReference,
    KeyringCredentialStore,
    MemoryCredentialStore,
)
from ai_piano_studio.music_agent.security import ProviderEndpoint
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentExecutionContext,
    MusicAgentLimits,
    MusicAgentTurnBudget,
    MusicEditProposal,
    MusicEditProposalStatus,
    MusicExecutionMode,
    MusicPermission,
    MusicToolLimitError,
    MusicToolResult,
    StagedMusicOperation,
)
from ai_piano_studio.music_agent.proposal_manager import (
    MusicEditProposalManager,
    ProposalChangeSet,
    ProposalChangeSummary,
)
from ai_piano_studio.music_agent.providers import (
    DeepSeekProvider,
    ModelProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderError,
    ProviderEvent,
    ProviderToolCall,
)
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoop, MusicAgentLoopOutcome
from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
from ai_piano_studio.music_agent.response_guard import (
    CapabilityClaimType,
    ResponseCapabilityGuard,
    ResponseGuardEvidence,
    ResponseGuardResult,
)
from ai_piano_studio.music_agent.turn_result import AssistantTurnResult
from ai_piano_studio.music_agent.registry import ModelProviderRegistry
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from ai_piano_studio.music_agent.tool_registry import (
    MusicToolDefinition,
    MusicToolRegistry,
    UnknownMusicToolError,
)

__all__ = [
    "CredentialNotFoundError",
    "CredentialReference",
    "CredentialStoreError",
    "KeyringCredentialStore",
    "MemoryCredentialStore",
    "ModelProvider",
    "ModelProviderRegistry",
    "ModelCapabilities",
    "AssistantCapabilitySet",
    "EffectiveCapabilitySet",
    "build_capability_prompt",
    "ProviderConfig",
    "ProviderEndpoint",
    "ProviderError",
    "ProviderEvent",
    "ProviderToolCall",
    "ReasoningOption",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "DeepSeekProvider",
    "BarRange",
    "MusicAgentExecutionContext",
    "MusicAgentToolGateway",
    "MusicAgentContextBuilder",
    "MusicAgentLoop",
    "MusicAgentLoopOutcome",
    "AssistantTurnResult",
    "CapabilityClaimType",
    "ResponseCapabilityGuard",
    "ResponseGuardEvidence",
    "ResponseGuardResult",
    "MusicAgentLimits",
    "MusicAgentTurnBudget",
    "MusicEditProposal",
    "MusicEditProposalManager",
    "MusicEditProposalStatus",
    "MusicExecutionMode",
    "MusicPermission",
    "MusicToolLimitError",
    "MusicToolDefinition",
    "MusicToolRegistry",
    "MusicToolResult",
    "ProposalChangeSet",
    "ProposalChangeSummary",
    "StagedMusicOperation",
    "UnknownMusicToolError",
]
