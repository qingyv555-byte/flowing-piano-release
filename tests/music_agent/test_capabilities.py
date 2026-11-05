import pytest

from ai_piano_studio.music_agent.capabilities import (
    ModelCapabilities,
    ReasoningOption,
)


def test_model_capabilities_serialize_real_provider_features() -> None:
    capabilities = ModelCapabilities(
        supports_tools=True,
        supports_structured_output=True,
        supports_audio=False,
        supports_files=False,
        supports_streaming=True,
        reasoning_options=(
            ReasoningOption("low", "较快", "以较低推理开销完成请求"),
        ),
    )

    assert capabilities.to_dict() == {
        "supportsTools": True,
        "supportsStructuredOutput": True,
        "supportsAudio": False,
        "supportsFiles": False,
        "supportsImages": None,  # Old configs remain unknown; files is not vision.
        "supportsStreaming": True,
        "reasoningOptions": [
            {
                "id": "low",
                "label": "较快",
                "description": "以较低推理开销完成请求",
            }
        ],
    }


def test_model_capabilities_reject_duplicate_reasoning_option_ids() -> None:
    with pytest.raises(ValueError, match="duplicate reasoning option"):
        ModelCapabilities(
            supports_tools=True,
            supports_structured_output=False,
            supports_audio=False,
            supports_files=False,
            supports_streaming=True,
            reasoning_options=(
                ReasoningOption("low", "较快", "减少推理开销"),
                ReasoningOption("low", "默认", "提供标准推理"),
            ),
        )
