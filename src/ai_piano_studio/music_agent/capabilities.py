from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from ai_piano_studio.music_agent.tool_contracts import MusicPermission


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ReasoningOption:
    id: str
    label: str
    description: str

    def __post_init__(self) -> None:
        _required_text(self.id, "reasoning option id")
        _required_text(self.label, "reasoning option label")
        _required_text(self.description, "reasoning option description")

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    supports_tools: bool
    supports_structured_output: bool
    supports_audio: bool
    supports_files: bool
    supports_streaming: bool
    reasoning_options: tuple[ReasoningOption, ...] = ()
    supports_images: bool | None = None

    def __post_init__(self) -> None:
        booleans = (
            self.supports_tools,
            self.supports_structured_output,
            self.supports_audio,
            self.supports_files,
            self.supports_streaming,
        )
        if any(not isinstance(value, bool) for value in booleans):
            raise ValueError("capability flags must be booleans")
        if self.supports_images is not None and not isinstance(self.supports_images, bool):
            raise ValueError('image capability must be true, false or unknown')
        if not isinstance(self.reasoning_options, tuple) or any(
            not isinstance(option, ReasoningOption) for option in self.reasoning_options
        ):
            raise ValueError("reasoning options must be a tuple of ReasoningOption")
        option_ids = [option.id for option in self.reasoning_options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("duplicate reasoning option id")

    def to_dict(self) -> dict[str, object]:
        return {
            "supportsTools": self.supports_tools,
            "supportsStructuredOutput": self.supports_structured_output,
            "supportsAudio": self.supports_audio,
            "supportsFiles": self.supports_files,
            "supportsImages": self.supports_images,
            "supportsStreaming": self.supports_streaming,
            "reasoningOptions": [option.to_dict() for option in self.reasoning_options],
        }


_APP_CAPABILITY_NAMES = (
    "music_read",
    "music_write",
    "notes_read",
    "notes_write",
    "bpm_write",
    "harmony_read",
    "harmony_write",
    "audio_render",
    "audio_generate",
    "audio_playback_control",
    "audio_attachment",
    "wav_generation",
    "mp3_generation",
    "file_attachment",
)
_AUDIO_CAPABILITY_NAMES = frozenset(
    {
        "audio_render",
        "audio_generate",
        "audio_playback_control",
        "audio_attachment",
        "wav_generation",
        "mp3_generation",
        "file_attachment",
    }
)


def _validate_capability_flags(values: Collection[object]) -> None:
    if any(not isinstance(value, bool) for value in values):
        raise ValueError("capability flags must be booleans")


@dataclass(frozen=True, slots=True)
class AssistantCapabilitySet:
    """Capabilities the app actually exposes through its Music Tool registry."""

    music_read: bool
    music_write: bool
    notes_read: bool
    notes_write: bool
    bpm_write: bool
    harmony_read: bool
    harmony_write: bool
    audio_render: bool = False
    audio_generate: bool = False
    audio_playback_control: bool = False
    audio_attachment: bool = False
    wav_generation: bool = False
    mp3_generation: bool = False
    file_attachment: bool = False

    def __post_init__(self) -> None:
        _validate_capability_flags(
            (
                self.music_read,
                self.music_write,
                self.notes_read,
                self.notes_write,
                self.bpm_write,
                self.harmony_read,
                self.harmony_write,
                self.audio_render,
                self.audio_generate,
                self.audio_playback_control,
                self.audio_attachment,
                self.wav_generation,
                self.mp3_generation,
                self.file_attachment,
            )
        )

    @classmethod
    def from_registry(cls, registry: object) -> "AssistantCapabilitySet":
        """Derive the app source from the real registry, never from a model name."""

        names = getattr(registry, "names", None)
        lookup = getattr(registry, "lookup", None)
        if not isinstance(names, tuple) or not callable(lookup):
            raise ValueError("registry must expose names and lookup")
        definitions = tuple(lookup(name) for name in names)
        read_permissions = {
            definition.permission for definition in definitions if definition.read_only
        }
        write_permissions = {
            definition.permission for definition in definitions if not definition.read_only
        }
        return cls(
            music_read=bool(
                read_permissions
                & {
                    MusicPermission.READ_PROJECT_SUMMARY,
                    MusicPermission.READ_NOTES,
                    MusicPermission.READ_HARMONY,
                }
            ),
            music_write=bool(
                write_permissions
                & {
                    MusicPermission.WRITE_NOTES,
                    MusicPermission.WRITE_BPM,
                    MusicPermission.WRITE_HARMONY,
                }
            ),
            notes_read=MusicPermission.READ_NOTES in read_permissions,
            notes_write=MusicPermission.WRITE_NOTES in write_permissions,
            bpm_write=MusicPermission.WRITE_BPM in write_permissions,
            harmony_read=MusicPermission.READ_HARMONY in read_permissions,
            harmony_write=MusicPermission.WRITE_HARMONY in write_permissions,
        )

    @property
    def available_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in _APP_CAPABILITY_NAMES if bool(getattr(self, name))
        )

    @property
    def unavailable_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in _APP_CAPABILITY_NAMES if not bool(getattr(self, name))
        )

    def to_dict(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in _APP_CAPABILITY_NAMES}


@dataclass(frozen=True, slots=True)
class EffectiveCapabilitySet:
    """App ∩ verified model ∩ current turn permissions."""

    music_read: bool
    music_write: bool
    notes_read: bool
    notes_write: bool
    bpm_write: bool
    harmony_read: bool
    harmony_write: bool
    audio_render: bool
    audio_generate: bool
    audio_playback_control: bool
    audio_attachment: bool
    wav_generation: bool
    mp3_generation: bool
    file_attachment: bool
    model_supports_tools: bool
    model_supports_structured_output: bool
    model_supports_audio: bool
    model_supports_files: bool
    model_supports_streaming: bool

    @classmethod
    def from_sources(
        cls,
        app: AssistantCapabilitySet,
        model: ModelCapabilities,
        permissions: Collection[MusicPermission],
    ) -> "EffectiveCapabilitySet":
        if not isinstance(app, AssistantCapabilitySet):
            raise ValueError("app capabilities are invalid")
        if not isinstance(model, ModelCapabilities):
            raise ValueError("model capabilities are invalid")
        if not isinstance(permissions, Collection) or any(
            not isinstance(permission, MusicPermission) for permission in permissions
        ):
            raise ValueError("permissions are invalid")
        current = set(permissions)
        audio_permission_granted = any(
            str(getattr(permission, "value", permission)).startswith("audio_")
            for permission in current
        )
        file_permission_granted = any(
            str(getattr(permission, "value", permission)).startswith("file_")
            for permission in current
        )
        model_tools = model.supports_tools
        can_read = bool(
            current
            & {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
                MusicPermission.WHOLE_PROJECT_READ,
            }
        )
        can_write = MusicPermission.WHOLE_PROJECT_WRITE in current
        notes_read = MusicPermission.READ_NOTES in current or MusicPermission.WHOLE_PROJECT_READ in current
        notes_write = MusicPermission.WRITE_NOTES in current or can_write
        bpm_write = MusicPermission.WRITE_BPM in current or can_write
        harmony_read = MusicPermission.READ_HARMONY in current or MusicPermission.WHOLE_PROJECT_READ in current
        harmony_write = MusicPermission.WRITE_HARMONY in current or can_write
        return cls(
            music_read=app.music_read and model_tools and can_read,
            music_write=app.music_write and model_tools and bool(
                current
                & {
                    MusicPermission.WRITE_NOTES,
                    MusicPermission.WRITE_BPM,
                    MusicPermission.WRITE_HARMONY,
                    MusicPermission.WHOLE_PROJECT_WRITE,
                }
            ),
            notes_read=app.notes_read and model_tools and notes_read,
            notes_write=app.notes_write and model_tools and notes_write,
            bpm_write=app.bpm_write and model_tools and bpm_write,
            harmony_read=app.harmony_read and model_tools and harmony_read,
            harmony_write=app.harmony_write and model_tools and harmony_write,
            audio_render=app.audio_render and model.supports_audio and audio_permission_granted,
            audio_generate=app.audio_generate and model.supports_audio and audio_permission_granted,
            audio_playback_control=(
                app.audio_playback_control and model.supports_audio and audio_permission_granted
            ),
            audio_attachment=(
                app.audio_attachment
                and model.supports_files
                and audio_permission_granted
                and file_permission_granted
            ),
            wav_generation=app.wav_generation and model.supports_audio and audio_permission_granted,
            mp3_generation=app.mp3_generation and model.supports_audio and audio_permission_granted,
            file_attachment=app.file_attachment and model.supports_files and file_permission_granted,
            model_supports_tools=model.supports_tools,
            model_supports_structured_output=model.supports_structured_output,
            model_supports_audio=model.supports_audio,
            model_supports_files=model.supports_files,
            model_supports_streaming=model.supports_streaming,
        )

    @property
    def available_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in _APP_CAPABILITY_NAMES if bool(getattr(self, name))
        )

    @property
    def unavailable_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in _APP_CAPABILITY_NAMES if not bool(getattr(self, name))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **{
                name: bool(getattr(self, name))
                for name in _APP_CAPABILITY_NAMES
            },
            "modelSupportsTools": self.model_supports_tools,
            "modelSupportsStructuredOutput": self.model_supports_structured_output,
            "modelSupportsAudio": self.model_supports_audio,
            "modelSupportsFiles": self.model_supports_files,
            "modelSupportsStreaming": self.model_supports_streaming,
        }


def build_capability_prompt(
    app: AssistantCapabilitySet,
    model: ModelCapabilities,
    permissions: Collection[MusicPermission],
) -> str:
    """Render the same effective boundary used by runtime/UI validation."""

    effective = EffectiveCapabilitySet.from_sources(app, model, permissions)
    available = ", ".join(effective.available_names) or "none"
    unavailable = ", ".join(effective.unavailable_names) or "none"
    return (
        "Capability boundary for this turn:\n"
        f"Available executable capabilities: {available}.\n"
        f"Unavailable capabilities: {unavailable}.\n"
        "The app can only use the available Music Tools. Never claim an unavailable "
        "audio, playback, attachment, or file-delivery action was performed. "
        "A model's audio/file metadata does not grant an app capability."
    )
