"""Editable recommendation order, not benchmark scores or capability grants.

Family defaults express a general reasoning/creation preference. Within a
family, full/reasoning editions precede speed editions, then newer versions.
Unknown model names keep their provider's order rather than gaining a made-up
score. Actual music-tool access remains governed by capability verification.
"""
from __future__ import annotations

import re


_FAMILY_ORDER = {
    "openai": 0, "anthropic": 10, "gemini": 20, "deepseek": 30,
    "kimi": 40, "glm": 50, "qwen": 60, "doubao": 70, "minimax": 80,
}
_PROVIDER_FAMILIES = {
    "codex": "openai", "kimi-code": "kimi", "glm-coding": "glm",
    "qwen-token": "qwen", "minimax-token": "minimax", "volcengine-plan": "doubao",
}


def provider_priority(provider_id: str) -> int:
    if provider_id == "openrouter":
        return 900
    return _FAMILY_ORDER.get(_PROVIDER_FAMILIES.get(provider_id, provider_id), 1000)


def model_preference(model_id: str) -> int:
    name = model_id.lower().split('/')[-1]
    family = next((family for family, pattern in (
        ('openai', r'^(gpt-|chatgpt|o[1-9](?:-|$))'), ('anthropic', r'claude'),
        ('gemini', r'gemini|gemma'), ('deepseek', r'deepseek'), ('kimi', r'kimi|moonshot'),
        ('glm', r'glm-'), ('qwen', r'qwen|qwq'), ('doubao', r'doubao|seed-|ark-code'),
        ('minimax', r'minimax'),
    ) if re.search(pattern, name)), None)
    if family is None:
        return 1_000_000_000
    # These are edition preferences, not an assertion of measured superiority.
    if re.search(r'mini|nano|lite|haiku|luna', name):
        edition = 4
    elif re.search(r'flash|turbo|fast', name):
        edition = 3
    elif re.search(r'sonnet|plus|sol|terra|chat', name):
        edition = 1
    else:
        edition = 0
    version = re.search(r'(\d{1,2})(?:[.\-](\d{1,2}))?', name)
    major, minor = (int(version.group(1)), int(version.group(2) or 0)) if version else (0, 0)
    if family == "openai" and re.match(r'^o[34](?:-|$)', name):
        major = 5  # The o-series is not a GPT generation-number sequence.
    return _FAMILY_ORDER[family] * 1_000_000 + (99 - major) * 10_000 + edition * 1000 + 99 - minor
