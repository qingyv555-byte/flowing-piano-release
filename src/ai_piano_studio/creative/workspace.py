"""An ephemeral editor using the production gateway and proposal transaction."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSettings

from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.proposal_manager import MusicEditProposalManager
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from ai_piano_studio.persistence.project_document import ProjectDocument, project_document_to_dict, save_project_document
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.recent_files import RecentFiles


class CreativeWorkspace:
    """Never receives the live editor, its history or the user's recovery store.

    The caller owns GUI-thread lifetime and cancels its AI worker before close.
    Only an explicit save writes a musical document outside temporary storage.
    """

    def __init__(self, *, playback=None):
        self._temporary = TemporaryDirectory(prefix='piano-creative-')
        root = Path(self._temporary.name)
        self.editor = EditorController(
            recent_files=RecentFiles(QSettings(str(root / 'recent.ini'), QSettings.Format.IniFormat)),
            project_library=ProjectLibrary(root / 'projects'),
            autosave_enabled=False,
        )
        self.gateway = MusicAgentToolGateway(self.editor)
        self.proposals = MusicEditProposalManager(self.editor, self.gateway, playback=playback)
        self._epoch = 0
        self._active = False
        self._closed = False
        self.mark_saved()

    def _document_payload(self):
        return project_document_to_dict(ProjectDocument(self.editor.state, self.editor.harmony_metadata))

    @property
    def is_dirty(self) -> bool:
        return not self._closed and self._document_payload() != self._saved_document

    def mark_saved(self) -> None:
        self._saved_document = self._document_payload()

    def begin(self) -> int:
        if self._closed:
            raise RuntimeError('workspace is closed')
        self.cancel()
        self._active = True
        return self._epoch

    def is_current(self, epoch: int) -> bool:
        return self._active and not self._closed and epoch == self._epoch

    def cancel(self) -> None:
        self._epoch += 1
        self._active = False
        self.proposals.discard_proposal()

    def save(self, target: Path) -> None:
        if self._closed or not self.editor.state.active_notes:
            raise ValueError('no theme to save')
        self.editor.synchronizeNow()
        save_project_document(target, ProjectDocument(self.editor.state, self.editor.harmony_metadata))
        self.mark_saved()

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True
        self.editor.deleteLater()
        self._temporary.cleanup()
