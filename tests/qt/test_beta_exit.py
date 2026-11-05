"""Normal window close must protect both Studio and accepted creative work."""
import pytest
from PySide6.QtCore import QObject

from ai_piano_studio.persistence.project_document import load_project_document
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_workflows import no_audio_device
from tests.qt.test_project_save import block_project_document_writes


@pytest.fixture
def exit_session(studio_session):
    app, engine, root, editor, playback = studio_session
    creative = engine.rootContext().contextProperty('creative')
    root.setProperty('pageState', 'editor')
    try:
        yield app, engine, root, editor, creative
    finally:
        # Teardown is not a user exit; dispose the isolated test session even
        # when the test deliberately leaves an unsaved document/dialog open.
        creative.shutdown()
        editor.discardUnsavedChanges()


def test_window_close_keeps_dirty_project_and_services_until_user_decides(exit_session, qtbot):
    app, engine, root, editor, creative = exit_session
    editor.createNoteAt(0, 0)
    before = editor.state
    assert editor.isDirty
    assert root.close() is False
    assert root.isVisible()
    dialog = root.findChild(QObject, 'unsavedExitDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert not creative._closed
    assert not engine.rootContext().contextProperty('audioVisualizer')._shutdown
    root.findChild(QObject, 'unsavedExitCancelAction').clicked.emit()
    assert root.isVisible() and editor.state == before and editor.isDirty
    creative.open()
    assert creative.assistant is not None


def test_discard_then_close_only_exits_after_explicit_choice(exit_session, qtbot):
    _, _, root, editor, creative = exit_session
    editor.createNoteAt(0, 0)
    assert not root.close()
    qtbot.waitUntil(lambda: root.findChild(QObject, 'unsavedExitDialog').property('opened'))
    root.findChild(QObject, 'unsavedExitDiscardAction').clicked.emit()
    qtbot.waitUntil(lambda: not root.isVisible())
    assert creative._closed


def test_save_then_close_waits_for_disk_and_preserves_music(exit_session, qtbot, tmp_path, monkeypatch):
    _, _, root, editor, _ = exit_session
    target = editor._projects.root / 'beta-exit' / 'project.pianoai.json'
    target.parent.mkdir(parents=True)
    assert editor.saveProject(str(target))
    qtbot.waitUntil(lambda: not editor.isSaving)
    editor.createNoteAt(0, 0)
    entered, release = block_project_document_writes(monkeypatch)
    try:
        assert not root.close()
        qtbot.waitUntil(lambda: root.findChild(QObject, 'unsavedExitDialog').property('opened'))
        root.findChild(QObject, 'unsavedExitSaveAction').clicked.emit()
        qtbot.waitUntil(entered.is_set)
        assert root.isVisible() and editor.isSaving
        assert not root.close()  # A second close cannot bypass the pending write.
    finally:
        release.set()
    qtbot.waitUntil(lambda: not root.isVisible())
    assert len(load_project_document(target).state.notes) == 1


def test_failed_save_during_exit_leaves_draft_editable(exit_session, qtbot, tmp_path, monkeypatch):
    from ai_piano_studio.editor import controller as module

    _, _, root, editor, creative = exit_session
    target = editor._projects.root / 'beta-exit' / 'project.pianoai.json'
    target.parent.mkdir(parents=True)
    assert editor.saveProject(str(target))
    qtbot.waitUntil(lambda: not editor.isSaving)
    original = target.read_bytes()
    editor.createNoteAt(0, 0)
    def fail(*args):
        raise OSError('simulated full disk')
    monkeypatch.setattr(module, 'save_project_document', fail)
    assert not root.close()
    qtbot.waitUntil(lambda: root.findChild(QObject, 'unsavedExitDialog').property('opened'))
    root.findChild(QObject, 'unsavedExitSaveAction').clicked.emit()
    qtbot.waitUntil(lambda: not editor.isSaving)
    assert root.isVisible() and editor.isDirty and not creative._closed
    assert target.read_bytes() == original


def test_unsaved_creative_theme_blocks_exit_even_with_clean_main_project(exit_session, qtbot):
    _, _, root, editor, creative = exit_session
    creative._ensure()
    creative._workspace.editor.createNoteAt(0, 0)
    assert not editor.isDirty
    assert not root.close()
    dialog = root.findChild(QObject, 'creativeExitDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert not creative._closed and creative.noteCount == 1
    root.findChild(QObject, 'creativeExitCancelAction').clicked.emit()
    assert root.isVisible() and creative.noteCount == 1


def test_discarding_main_project_still_protects_independent_theme(exit_session, qtbot):
    _, _, root, editor, creative = exit_session
    editor.createNoteAt(0, 0)
    creative._ensure()
    creative._workspace.editor.createNoteAt(0, 0)
    assert not root.close()
    qtbot.waitUntil(lambda: root.findChild(QObject, 'unsavedExitDialog').property('opened'))
    root.findChild(QObject, 'unsavedExitDiscardAction').clicked.emit()
    qtbot.waitUntil(lambda: root.findChild(QObject, 'creativeExitDialog').property('opened'))
    assert root.isVisible() and not creative._closed
    root.findChild(QObject, 'creativeExitDiscardAction').clicked.emit()
    qtbot.waitUntil(lambda: not root.isVisible())
    assert creative._closed


def test_creative_saved_identity_tracks_undo_and_failed_atomic_write(exit_session, tmp_path, monkeypatch):
    import ai_piano_studio.persistence.project_document as module

    _, _, _, _, creative = exit_session
    creative._ensure()
    draft = creative._workspace.editor
    draft.createNoteAt(0, 0)
    assert creative.hasUnsavedTheme
    target = tmp_path / 'theme.pianoai.json'
    assert creative.saveTheme(str(target))
    assert not creative.hasUnsavedTheme
    original = target.read_bytes()
    draft.setBpm(90)
    assert creative.hasUnsavedTheme
    def fail(*args):
        raise OSError('simulated replacement failure')
    monkeypatch.setattr(module.os, 'replace', fail)
    assert not creative.saveTheme(str(target))
    assert creative.hasUnsavedTheme and target.read_bytes() == original
    creative.undoTheme()
    assert not creative.hasUnsavedTheme
