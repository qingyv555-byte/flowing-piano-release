"""Real Qt window drag events; no Explorer or compositor claim."""
import base64
import wave

import pytest
from PySide6.QtCore import QMimeData, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy, QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_json import save_project
from tests.qt.test_studio_shell import preserved_qsettings_state, studio_session
from tests.qt.test_studio_workflows import click, qml
from tests.qt.test_studio_workflow_ux import memory_audio


def drag(root, urls=None, text=None):
    mime = QMimeData()
    if urls is not None:
        mime.setUrls(urls)
    if text is not None:
        mime.setText(text)
    enter = QDragEnterEvent(QPoint(400, 300), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(root, enter)
    move = QDragMoveEvent(QPoint(400, 300), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(root, move)
    hint = root.findChild(QObject, "studioFileDropHint")
    hint_visible = hint is not None and hint.isVisible()
    drop = QDropEvent(QPointF(400, 300), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(root, drop)
    return enter.isAccepted(), move.isAccepted(), drop.isAccepted(), hint_visible


def snapshot(editor):
    return editor.state.content, editor.historyUndoDepth, editor.currentFilePath, editor.isDirty


@pytest.mark.parametrize("page", ["launch", "editor"])
@pytest.mark.parametrize("confirm", [False, True])
def test_json_requires_confirmation(studio_session, qtbot, tmp_path, page, confirm):
    _app, _engine, root, editor, _playback = studio_session
    if page == "editor":
        click(root, "studioCreateButton")
        editor.createNoteAt(0, 0)
    source = tmp_path / "music-source.json"
    expected = ProjectState(notes=(Note.create(pitch=67, start_time=0, duration=.5),))
    save_project(source, expected)
    before = snapshot(editor)
    assert drag(root, [QUrl.fromLocalFile(str(source))]) == (True, True, True, True)
    dialog = root.findChild(QObject, "studioFileDropConfirmation")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert snapshot(editor) == before
    assert not root.property("editorInputEnabled")
    assert root.activeFocusItem().objectName() == "studioFileDropCancel"
    if page == "editor":
        QTest.keyClick(root, Qt.Key_Z, Qt.ControlModifier)
        assert snapshot(editor) == before
    click(root, "studioFileDropConfirm" if confirm else "studioFileDropCancel")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    if confirm:
        if page == "editor":
            # The existing router saves a dirty draft before switching projects.
            naming = root.findChild(QObject, "projectNameDialog")
            qtbot.waitUntil(lambda: naming.property("opened"))
            assert snapshot(editor) == before
            click(root, "projectNameConfirmButton")
            qtbot.waitUntil(lambda: editor.currentFilePath == str(source))
        assert editor.state.content == expected.content
        assert editor.currentFilePath == str(source)
    else:
        assert snapshot(editor) == before
    assert root.findChild(QObject, "studioFileDrop").property("pendingSource").isEmpty()


@pytest.mark.parametrize("kind", ["text", "remote", "unsupported", "multiple", "unc"])
def test_rejects_invalid_ingress(studio_session, tmp_path, kind):
    _app, _engine, root, editor, _playback = studio_session
    source = QUrl.fromLocalFile(str(tmp_path / "song.json"))
    urls = {"text": None, "remote": [QUrl("https://example.com/song.json")],
            "unsupported": [QUrl.fromLocalFile(str(tmp_path / "song.exe"))],
            "multiple": [source, source], "unc": [QUrl("file://server/share/song.json")]}[kind]
    before = snapshot(editor)
    assert drag(root, urls, "song.json" if kind == "text" else None) == (False, False, False, False)
    assert snapshot(editor) == before


@pytest.mark.parametrize("body", ["{bad", '{"source": "a song"}'])
def test_invalid_json_retains_music(studio_session, qtbot, tmp_path, body):
    _app, _engine, root, editor, _playback = studio_session
    click(root, "studioCreateButton")
    editor.createNoteAt(0, 0)
    editor.saveProject(str(tmp_path / "prior.pianoai.json"))
    qtbot.waitUntil(lambda: not editor.isSaving)
    source = tmp_path / "music-source.json"
    source.write_text(body, encoding="utf-8")
    before = snapshot(editor)
    assert drag(root, [QUrl.fromLocalFile(str(source))])[2]
    qtbot.waitUntil(lambda: root.findChild(QObject, "studioFileDropConfirmation").property("opened"))
    click(root, "studioFileDropConfirm")
    assert snapshot(editor) == before


def test_escape_and_project_change_invalidate_pending(studio_session, qtbot, tmp_path):
    _app, _engine, root, editor, _playback = studio_session
    source = tmp_path / "song.json"
    save_project(source, ProjectState())
    dropper = root.findChild(QObject, "studioFileDrop")
    opener = root.findChild(QObject, "studioImportButton")
    opener.forceActiveFocus()
    assert drag(root, [QUrl.fromLocalFile(str(source))])[2]
    qtbot.waitUntil(lambda: dropper.property("confirmationOpen"))
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not dropper.property("confirmationOpen"))
    assert dropper.property("pendingSource").isEmpty()
    assert root.activeFocusItem() == opener
    assert drag(root, [QUrl.fromLocalFile(str(source))])[2]
    qtbot.waitUntil(lambda: dropper.property("confirmationOpen"))
    editor.newProject()
    qtbot.waitUntil(lambda: not dropper.property("confirmationOpen"))
    qml(dropper, "confirmPending(); true")
    assert editor.currentFilePath != str(source)


@pytest.mark.parametrize("suffix", [".mid", ".MIDI", ".wav", ".MP3"])
def test_music_drop_routes_once_to_existing_preview(studio_session, qtbot, tmp_path, suffix):
    _app, _engine, root, editor, _playback = studio_session
    source = tmp_path / ("拖入音乐" + suffix)
    midi = suffix.lower() in (".mid", ".midi")
    if midi:
        from ai_piano_studio.persistence.midi import export_midi
        export_midi(source, (Note.create(pitch=67, start_time=0, duration=.5),))
    elif suffix.lower() == ".mp3":
        from tests.audio.test_importer import _SHORT_SILENT_MP3
        source.write_bytes(base64.b64decode(_SHORT_SILENT_MP3))
    else:
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b"\0\0" * 4410)
    dropper = root.findChild(QObject, "studioFileDrop")
    confirmed = QSignalSpy(dropper.fileConfirmed)
    before = snapshot(editor)
    assert drag(root, [QUrl.fromLocalFile(str(source))]) == (True, True, True, True)
    qtbot.waitUntil(lambda: dropper.property("confirmationOpen"))
    assert snapshot(editor) == before and confirmed.count() == 0
    click(root, "studioFileDropConfirm")
    qml(dropper, "confirmPending(); true")
    assert confirmed.count() == 1
    name = "midiImportDialog" if midi else "audioImportDialog"
    qtbot.waitUntil(lambda: root.findChild(QObject, name) is not None)
    preview = root.findChild(QObject, name)
    qtbot.waitUntil(lambda: preview.property("opened"))
    assert snapshot(editor) == before


@pytest.mark.parametrize("block", ["saving", "recycling", "modal"])
def test_busy_and_modal_reject_and_invalidate(studio_session, qtbot, tmp_path, block):
    _app, _engine, root, editor, _playback = studio_session
    source = QUrl.fromLocalFile(str(tmp_path / "song.json"))
    dropper = root.findChild(QObject, "studioFileDrop")
    assert drag(root, [source])[2]
    qtbot.waitUntil(lambda: dropper.property("confirmationOpen"))
    if block == "saving":
        editor._is_saving = True
        editor.savingChanged.emit()
    elif block == "recycling":
        editor._recycle_job = object()
        editor.recycleProgressChanged.emit()
    else:
        qml(root, "newConfirmDialog.open(); true")
    try:
        qtbot.waitUntil(lambda: not dropper.property("confirmationOpen"))
        assert drag(root, [source]) == (False, False, False, False)
        qml(dropper, "confirmPending(); true")
        assert editor.currentFilePath != source.toLocalFile()
    finally:
        editor._is_saving = False
        editor._recycle_job = None
        editor.savingChanged.emit()
        editor.recycleProgressChanged.emit()
