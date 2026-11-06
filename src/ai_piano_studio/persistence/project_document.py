from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import tempfile

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata

from .project_json import _project_state_from_dict, _project_state_to_dict


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    state: ProjectState
    harmony: HarmonyProjectMetadata = field(default_factory=HarmonyProjectMetadata.empty)


def project_document_from_dict(
    data: object, logger: logging.Logger | None = None
) -> ProjectDocument:
    state = _project_state_from_dict(data)
    harmony = (
        HarmonyProjectMetadata.from_dict(data["harmony"], state, logger)
        if isinstance(data, dict) and "harmony" in data
        else HarmonyProjectMetadata.empty()
    )
    return ProjectDocument(state, harmony)


def project_document_to_dict(document: ProjectDocument) -> dict[str, object]:
    payload = _project_state_to_dict(document.state)
    harmony = document.harmony.reconciled(document.state)
    if not harmony.is_empty:
        payload["harmony"] = harmony.to_dict()
    return payload


def load_project_document(path: str | Path) -> ProjectDocument:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid project JSON: {error}") from error
    return project_document_from_dict(data)


def save_project_document(path: str | Path, document: ProjectDocument, *, compact: bool = False) -> None:
    target = Path(path)
    temporary: Path | None = None
    try:
        text = json.dumps(
            project_document_to_dict(document),
            ensure_ascii=False,
            indent=None if compact else 2,
            allow_nan=False,
        ) + "\n"
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        _LOGGER.exception("Failed to save project document %s", target)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
