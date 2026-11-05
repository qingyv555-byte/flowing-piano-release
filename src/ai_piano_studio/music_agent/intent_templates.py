"""Provider-neutral musical objectives used by curated AI quick actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MusicIntentTemplate:
    """A human-readable musical objective, separate from provider tool syntax."""

    action_id: str
    label: str
    guidance: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.action_id,
            "label": self.label,
            "guidance": list(self.guidance),
        }


QUICK_ACTION_TEMPLATES: tuple[MusicIntentTemplate, ...] = (
    MusicIntentTemplate(
        "continue",
        "\u7eed\u5199\u8fd9\u6bb5",
        (
            "\u5ef6\u7eed\u5f53\u524d\u4e3b\u9898\u548c\u8282\u594f\u8bed\u8a00",
            "\u53ea\u4fee\u6539\u7528\u6237\u6307\u5b9a\u6216\u6388\u6743\u7684\u8303\u56f4",
            "\u4fdd\u6301\u81ea\u7136\u7684\u6bb5\u843d\u8854\u63a5",
        ),
    ),
    MusicIntentTemplate(
        "improve_melody",
        "\u4f18\u5316\u65cb\u5f8b",
        (
            "\u4fdd\u7559\u53ef\u8fa8\u8ba4\u7684\u4e3b\u9898\u52a8\u673a",
            "\u6539\u5584\u65cb\u5f8b\u7684\u547c\u5438\u548c\u53e5\u6cd5",
            "\u907f\u514d\u65e0\u7406\u7531\u5730\u91cd\u5199\u6574\u6bb5",
        ),
    ),
    MusicIntentTemplate(
        "add_chords",
        "\u6dfb\u52a0\u548c\u5f26",
        (
            "\u4f7f\u7528\u5f53\u524d\u65cb\u5f8b\u548c\u5df2\u77e5\u548c\u58f0\u7ebf\u7d22",
            "\u4fdd\u6301\u58f0\u90e8\u8fdb\u884c\u81ea\u7136",
            "\u53ea\u4fee\u6539\u7528\u6237\u6307\u5b9a\u6216\u6388\u6743\u7684\u8303\u56f4",
        ),
    ),
    MusicIntentTemplate(
        "add_bass",
        "\u6dfb\u52a0\u4f4e\u97f3",
        (
            "\u4e3a\u65cb\u5f8b\u63d0\u4f9b\u7a33\u5b9a\u7684\u4f4e\u97f3\u652f\u6491",
            "\u907f\u514d\u4e0e\u5df2\u6709\u4f4e\u97f3\u53d1\u751f\u65e0\u610f\u4e49\u7684\u91cd\u53e0",
            "\u5c0a\u91cd\u5f53\u524d\u8c03\u6027",
        ),
    ),
    MusicIntentTemplate(
        "add_variation",
        "\u589e\u52a0\u53d8\u5316",
        (
            "\u4fdd\u7559\u6838\u5fc3\u52a8\u673a\u5e76\u52a0\u5165\u6e10\u8fdb\u53d8\u5316",
            "\u63a7\u5236\u97f3\u7b26\u5bc6\u5ea6\u548c\u8df3\u8fdb",
            "\u8ba9\u53d8\u5316\u670d\u52a1\u4e8e\u6bb5\u843d\u7ed3\u6784",
        ),
    ),
    MusicIntentTemplate(
        "make_climax",
        "\u5236\u9020\u9ad8\u6f6e",
        (
            "\u4fdd\u7559\u53ef\u8fa8\u8ba4\u7684\u4e3b\u9898\u52a8\u673a",
            "\u9010\u6b65\u589e\u52a0\u97f3\u533a\u548c\u80fd\u91cf",
            "\u907f\u514d\u968f\u673a\u5bc6\u5ea6\u7206\u70b8",
            "\u53ea\u4fee\u6539\u7528\u6237\u6307\u5b9a\u6216\u6388\u6743\u7684\u8303\u56f4",
        ),
    ),
    MusicIntentTemplate(
        "simplify",
        "\u7b80\u5316",
        (
            "\u4fdd\u7559\u65cb\u5f8b\u8f6e\u5ed3\u548c\u4e3b\u8981\u8282\u62cd",
            "\u51cf\u5c11\u4e0d\u5fc5\u8981\u7684\u5bc6\u5ea6\u6216\u88c5\u9970",
            "\u4e0d\u8981\u5220\u9664\u7528\u6237\u672a\u6388\u6743\u7684\u8303\u56f4",
        ),
    ),
    MusicIntentTemplate(
        "sadder",
        "\u66f4\u60b2\u4f24",
        (
            "\u4f7f\u7528\u66f4\u514b\u5236\u7684\u65cb\u5f8b\u8d70\u5411\u548c\u529b\u5ea6",
            "\u4fdd\u7559\u539f\u4e3b\u9898\u7684\u53ef\u8fa8\u8bc6\u5ea6",
            "\u907f\u514d\u53ea\u9760\u6781\u7aef\u4f4e\u97f3\u8868\u8fbe\u60c5\u7eea",
        ),
    ),
    MusicIntentTemplate(
        "warmer",
        "\u66f4\u6e29\u6696",
        (
            "\u4fdd\u7559\u65cb\u5f8b\u5e76\u6539\u5584\u8fde\u8d2f\u6027",
            "\u4f7f\u7528\u67d4\u548c\u7684\u97f3\u533a\u548c\u529b\u5ea6\u53d8\u5316",
            "\u907f\u514d\u65e0\u8c13\u5730\u589e\u52a0\u97f3\u7b26",
        ),
    ),
    MusicIntentTemplate(
        "dreamy",
        "\u66f4\u68a6\u5e7b",
        (
            "\u4fdd\u6301\u6e05\u6670\u7684\u4e3b\u9898\u548c\u8282\u62cd\u9aa8\u67b6",
            "\u589e\u52a0\u9002\u5ea6\u7684\u7a7a\u95f4\u611f\u4e0e\u548c\u58f0\u8272\u5f69",
            "\u63a7\u5236\u53e0\u97f3\u548c\u97f3\u7b26\u5bc6\u5ea6",
        ),
    ),
)

_TEMPLATES_BY_ID = {template.action_id: template for template in QUICK_ACTION_TEMPLATES}


def get_quick_action_template(action_id: str) -> MusicIntentTemplate | None:
    """Return a known intent only; unknown UI/provider values never gain write scope."""

    return _TEMPLATES_BY_ID.get(action_id) if isinstance(action_id, str) else None
