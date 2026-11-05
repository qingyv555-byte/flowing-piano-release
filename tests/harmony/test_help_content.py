from __future__ import annotations

import logging
import re

from ai_piano_studio.harmony.help_content import HarmonyHelpContent


EXPECTED_IDS = [
    "harmony-basics",
    "chord-symbols",
    "major-minor",
    "chord-timing",
    "chord-selection",
    "bass-foundation",
    "chord-patterns",
    "inversions",
    "space-rhythm",
    "dynamics-register",
    "climax-design",
    "cadence-ending",
    "faq",
]

EXPECTED_BEGINNER_FAQ_HEADINGS = [
    "和弦到底什么时候出现？",
    "为什么打开旧工程没有马上铺满和弦？",
    "实时预览和“检测整首并生成”有什么区别？",
    "为什么这次没有添加和弦？",
    "为什么已有和弦/重叠音会被跳过？",
    "怎样更容易得到可靠建议？",
]


def test_guide_maps_exactly_thirteen_stable_sections() -> None:
    content = HarmonyHelpContent()
    sections = content.sections

    assert [section["id"] for section in sections] == EXPECTED_IDS
    assert [section["title"] for section in sections][4] == "怎样判断哪个和弦适合"
    assert [section["title"] for section in sections][10:12] == [
        "设计高潮",
        "结尾使用终止感",
    ]
    assert sections[0]["markdown"].startswith("## 和弦是什么")
    assert "自动添加后还能修改吗" in sections[-1]["markdown"]


def test_faq_contains_beginner_questions_for_live_and_whole_song_guidance() -> None:
    faq_markdown = HarmonyHelpContent().sections[-1]["markdown"]
    faq_headings = re.findall(r"^### (.+)$", faq_markdown, flags=re.MULTILINE)

    missing = [
        heading for heading in EXPECTED_BEGINNER_FAQ_HEADINGS
        if heading not in faq_headings
    ]

    assert missing == []


def test_open_section_changes_only_for_whitelisted_id(caplog) -> None:
    content = HarmonyHelpContent()
    content.openSection("climax-design")
    assert content.currentSectionId == "climax-design"

    with caplog.at_level(logging.WARNING):
        content.openSection("not-a-section")

    assert content.currentSectionId == "climax-design"
    assert "unknown harmony help section" in caplog.text.lower()


def test_activate_link_accepts_internal_ids_and_rejects_external_urls(caplog) -> None:
    content = HarmonyHelpContent()

    content.activateLink("#chord-selection")
    assert content.currentSectionId == "chord-selection"
    content.activateLink("cadence-ending")
    assert content.currentSectionId == "cadence-ending"

    for unsafe in ("https://example.com", "file:///tmp/guide", "#unknown"):
        with caplog.at_level(logging.WARNING):
            content.activateLink(unsafe)
        assert content.currentSectionId == "cadence-ending"
    assert caplog.text.lower().count("blocked harmony help link") == 3


def test_visible_section_updates_highlight_without_reloading_content() -> None:
    content = HarmonyHelpContent()
    sections = content.sections

    content.setVisibleSection("dynamics-register")

    assert content.currentSectionId == "dynamics-register"
    assert content.sections == sections
