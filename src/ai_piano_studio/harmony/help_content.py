from __future__ import annotations

import logging
from pathlib import Path
import re

from PySide6.QtCore import QObject, Property, Signal, Slot


logger = logging.getLogger(__name__)

SECTION_IDS = (
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
)
_HEADING = re.compile(r"^## (.+)$", re.MULTILINE)


def default_guide_path() -> Path:
    return Path(__file__).parents[1] / "resources" / "help" / "harmony_beginner_guide.md"


def _parse_sections(markdown: str) -> tuple[dict[str, str], ...]:
    matches = [match for match in _HEADING.finditer(markdown) if match.group(1) != "目录"]
    if len(matches) != len(SECTION_IDS):
        raise ValueError(f"expected 13 harmony help sections, found {len(matches)}")
    sections: list[dict[str, str]] = []
    for index, (section_id, match) in enumerate(zip(SECTION_IDS, matches)):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append(
            {
                "id": section_id,
                "title": match.group(1).strip(),
                "markdown": markdown[match.start() : end].strip(),
            }
        )
    return tuple(sections)


class HarmonyHelpContent(QObject):
    currentSectionChanged = Signal()

    def __init__(self, resource_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        path = resource_path or default_guide_path()
        self._sections = _parse_sections(path.read_text(encoding="utf-8"))
        self._by_id = {section["id"]: section for section in self._sections}
        self._current_section_id = SECTION_IDS[0]

    @Property("QVariantList", constant=True)
    def sections(self) -> list[dict[str, str]]:
        return [dict(section) for section in self._sections]

    @Property(str, notify=currentSectionChanged)
    def currentSectionId(self) -> str:
        return self._current_section_id

    @Property(str, notify=currentSectionChanged)
    def currentSectionMarkdown(self) -> str:
        return self._by_id[self._current_section_id]["markdown"]

    @Slot(str)
    def openSection(self, section_id: str) -> None:
        if section_id not in self._by_id:
            logger.warning("Unknown harmony help section: %s", section_id)
            return
        if section_id != self._current_section_id:
            self._current_section_id = section_id
            self.currentSectionChanged.emit()

    @Slot(str)
    def setVisibleSection(self, section_id: str) -> None:
        self.openSection(section_id)

    @Slot(str)
    def activateLink(self, target: str) -> None:
        section_id = target[1:] if target.startswith("#") else target
        if section_id not in self._by_id:
            logger.warning("Blocked harmony help link: %s", target)
            return
        self.openSection(section_id)
