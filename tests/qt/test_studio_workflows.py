"""New Studio actions reach real data and files, using offscreen/device-free IO.

QML menu delegates and controls receive QTest pointer/key events. File dialogs
receive a selected local URL and accepted signal at the picker boundary; their
real QML handlers must produce data/files. Platform picking remains unverified.
AI proposals use the real local gateway.
"""
from pathlib import Path
from dataclasses import replace
import wave

import pytest
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
from ai_piano_studio.persistence.midi import export_midi, load_midi
from ai_piano_studio.persistence.project_document import (
    ProjectDocument, load_project_document, save_project_document,
)
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session


@pytest.fixture(autouse=True)
def no_audio_device(monkeypatch):
    """Keep the real mixer/scheduler; substitute only physical output startup."""
    def ready(mixer, **_kwargs):
        mixer._audio_ready = True
    monkeypatch.setattr(PianoVoiceMixer, "start_audio", ready)
    monkeypatch.setattr(PianoVoiceMixer, "resume_audio", ready)
    monkeypatch.setattr(PianoVoiceMixer, "processed_audio_seconds", lambda self: 0.0)
    monkeypatch.setattr(PianoVoiceMixer, "output_latency_seconds", lambda self: 0.0)


def qml(obj, source):
    assert obj is not None, 'QML expression requires a live scope object'
    expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source)
    value, undefined = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    assert not undefined, source
    return value


def click_item(root, item, x=None, y=None):
    assert item is not None and item.isVisible() and item.isEnabled()
    point = item.mapToScene(QPointF(item.width() / 2 if x is None else x,
                                   item.height() / 2 if y is None else y)).toPoint()
    assert 0 <= point.x() < root.width() and 0 <= point.y() < root.height()
    QTest.mouseClick(root, Qt.LeftButton, pos=point)


def find_item(root, name):
    pending = [root.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    return root.findChild(QObject, name)


def click(root, name):
    click_item(root, find_item(root, name))


@pytest.mark.parametrize("signature,index", [((3, 8), 3), ((2, 2), 4)])
def test_studio_original_score_meter_selection_and_undo(studio_session, qtbot, signature, index):
    _app, _engine, root, editor, _playback = studio_session
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    click(root, "studioMusicSettings")
    box = root.findChild(QObject, "studioTimeSignature")
    qtbot.waitUntil(lambda: box.isVisible())
    click_item(root, box)
    qtbot.waitUntil(lambda: bool(qml(box, "popup.opened")))
    qtbot.waitUntil(lambda: qml(box, f"popup.contentItem.itemAtIndex({index})") is not None)
    click_item(root, qml(box, f"popup.contentItem.itemAtIndex({index})"))
    assert (editor.beatsPerBar, editor.beatUnit) == signature
    assert box.property("currentText") == f"{signature[0]}/{signature[1]}"
    editor.undo()
    assert box.property("currentText") == "4/4"
    editor.redo()
    assert box.property("currentText") == f"{signature[0]}/{signature[1]}"


def file_menu(root, qtbot, index):
    for dialog_id in ("openDialog", "midiDialog", "audioDialog", "exportProjectDialog", "midiImportFileDialog"):
        qml(root, f"{dialog_id}.options = FileDialog.DontUseNativeDialog")
    if index == 11:
        # Auto-save moved to the settings transaction; retain real policy checks.
        initialize_deferred_services(QQmlEngine.contextForObject(root).engine())
        click(root, "studioSettingsButton")
        dialog = root.findChild(QObject, "studioAppearanceDialog")
        qtbot.waitUntil(lambda: dialog is not None and dialog.property("opened"))
        dialog.setProperty("page", 6)
        qtbot.waitUntil(lambda: find_item(root, "preferencesAutosave") is not None)
        click(root, "preferencesAutosave")
        click(root, "studioAppearanceApply")
        qtbot.waitUntil(lambda: not dialog.property("opened"))
        return
    click(root, "studioFileMenuButton")
    menu = root.findChild(QObject, "studioFileMenu")
    qtbot.waitUntil(lambda: menu.property("opened"))
    item = qml(menu, f"itemAt({index + 1 if index < 11 else index})")
    click_item(root, item)
    qtbot.waitUntil(lambda: not menu.property("opened"))


def view_menu(root, qtbot, index):
    if index == 0:
        initialize_deferred_services(QQmlEngine.contextForObject(root).engine())
        click(root, "studioSettingsButton")
        return
    click(root, "studioViewMenuButton")
    menu = root.findChild(QObject, "studioViewMenu")
    qtbot.waitUntil(lambda: menu.property("opened"))
    click_item(root, qml(menu, f"itemAt({index - 2})"))
    assert menu.property("opened")
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not menu.property("opened"))


def accept_file(root, qtbot, dialog_id, path):
    dialog = qml(root, dialog_id)
    qtbot.waitUntil(lambda: dialog.property("visible"))
    assert dialog.setProperty("selectedFile", QUrl.fromLocalFile(str(path)))
    assert dialog.property("selectedFile") == QUrl.fromLocalFile(str(path))
    # accept() asks the offscreen platform helper for its empty selection,
    # overwriting selectedFile. Inject only this OS-picker completion boundary.
    dialog.accepted.emit()
    dialog.close()
    qtbot.waitUntil(lambda: not dialog.property("visible"))


def create_notes(root, editor, qtbot, count=2):
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    canvas = root.findChild(QObject, "drawNoteArea")
    qtbot.waitUntil(lambda: canvas.width() > 800)
    for index in range(count):
        click_item(root, canvas, 180 + 80 * index, 180)
        assert editor.noteModel.totalCount == index + 1


def submit_name(root, editor, qtbot, name):
    dialog = root.findChild(QObject, "projectNameDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    field = root.findChild(QObject, "projectNameField")
    field.setProperty("text", name)
    with qtbot.waitSignal(editor.saveSucceeded, timeout=5000):
        click(root, "projectNameConfirmButton")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert not editor.isDirty
    return Path(editor.currentFilePath)


def test_save_rename_copy_home_continue_recent_open_actual_documents(studio_session, qtbot):
    _app, _engine, root, editor, _playback = studio_session
    create_notes(root, editor, qtbot)
    click(root, "studioSave")
    first = submit_name(root, editor, qtbot, "动作矩阵 A")
    content = editor.state.content
    assert first.is_file()
    assert load_project_document(first).state.notes == editor.state.notes
    file_menu(root, qtbot, 12)
    rename = root.findChild(QObject, "renameProjectDialog")
    qtbot.waitUntil(lambda: rename.property("opened"))
    root.findChild(QObject, "renameProjectField").setProperty("text", "动作矩阵 A 改名")
    click(root, "renameProjectConfirmAction")
    qtbot.waitUntil(lambda: editor.currentFileName == "动作矩阵 A 改名")
    assert next(entry for entry in editor.libraryEntries if Path(entry["path"]) == first)["name"] == "动作矩阵 A 改名"
    assert editor.state.content == content
    file_menu(root, qtbot, 6)
    second = submit_name(root, editor, qtbot, "动作矩阵 B 副本")
    assert second != first and first.is_file()
    assert load_project_document(second).state.notes == load_project_document(first).state.notes
    click(root, "studioHomeNav")
    qtbot.waitUntil(lambda: root.property("pageState") == "launch")
    qtbot.waitUntil(lambda: find_item(root, "studioRecent-0") is not None)
    assert Path(qml(find_item(root, "studioRecent-0"), "modelData.path")) == second
    click(root, "studioContinueButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert Path(editor.currentFilePath) == second
    # Open A through the new file menu, then require the actual most-recent order.
    file_menu(root, qtbot, 0)
    accept_file(root, qtbot, "openDialog", first)
    qtbot.waitUntil(lambda: Path(editor.currentFilePath) == first)
    click(root, "studioHomeNav")
    qtbot.waitUntil(lambda: root.property("pageState") == "launch")
    qtbot.waitUntil(lambda: find_item(root, "studioRecent-0") is not None)
    recent = find_item(root, "studioRecent-0")
    assert Path(qml(recent, "modelData.path")) == first
    assert qml(recent, "modelData.name") == "动作矩阵 A 改名"
    click(root, "studioRecent-0")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert Path(editor.currentFilePath) == first
    assert editor.state.notes == load_project_document(first).state.notes


def test_new_file_menu_exports_real_midi_wav_and_project(studio_session, qtbot, tmp_path):
    _app, _engine, root, editor, playback = studio_session
    create_notes(root, editor, qtbot)
    click(root, "studioSave")
    saved = submit_name(root, editor, qtbot, "导出源工程")
    before = editor.state.content
    midi = tmp_path / "export.mid"
    file_menu(root, qtbot, 7)
    accept_file(root, qtbot, "midiDialog", midi)
    qtbot.waitUntil(midi.is_file)
    assert midi.read_bytes()[:4] == b"MThd"
    events = load_midi(midi).notes
    assert sorted(event.pitch for event in events) == sorted(note.pitch for note in editor.state.notes)
    project = tmp_path / "export.pianoai.json"
    file_menu(root, qtbot, 9)
    # File creation can precede the worker releasing its Windows file handle.
    with qtbot.waitSignal(editor.projectExported, timeout=5000):
        accept_file(root, qtbot, "exportProjectDialog", project)
    assert project.is_file()
    assert load_project_document(project).state.notes == editor.state.notes
    wav = tmp_path / "export.wav"
    file_menu(root, qtbot, 8)
    accept_file(root, qtbot, "audioDialog", wav)
    qtbot.waitUntil(lambda: wav.is_file() and not playback.audioExporting, timeout=20000)
    assert wav.read_bytes()[:4] == b"RIFF"
    with wave.open(str(wav)) as audio:
        assert audio.getnframes() > 0 and audio.getframerate() > 0
        assert audio.getnchannels() >= 1
        assert any(audio.readframes(audio.getnframes())), "exported PCM must contain music"
    assert editor.state.content == before
    assert Path(editor.currentFilePath) == saved and not editor.isDirty


def test_save_io_failure_keeps_dirty_music_and_file_and_shows_error(studio_session, qtbot, monkeypatch):
    from ai_piano_studio.editor import controller as controller_module

    _app, _engine, root, editor, _playback = studio_session
    create_notes(root, editor, qtbot, 1)
    click(root, "studioSave")
    saved = submit_name(root, editor, qtbot, "写入失败恢复")
    saved_bytes = saved.read_bytes()
    canvas = root.findChild(QObject, "drawNoteArea")
    click_item(root, canvas, 340, 180)
    assert editor.isDirty and len(editor.state.active_notes) == 2
    music = tuple(replace(note, status="saved", updated_at="test") for note in editor.state.active_notes)

    def fail_write(_path, _document):
        raise OSError("controlled test write failure")

    monkeypatch.setattr(controller_module, "save_project_document", fail_write)
    with qtbot.waitSignal(editor.saveFailed, timeout=5000):
        click(root, "studioSave")
    assert editor.isDirty and not editor.isSaving
    assert tuple(replace(note, status="saved", updated_at="test") for note in editor.state.active_notes) == music
    assert saved.read_bytes() == saved_bytes
    feedback = root.findChild(QObject, "feedbackHost")
    qtbot.waitUntil(lambda: feedback.property("visible"))
    assert "保存" in feedback.property("message")
    assert feedback.property("kind") == "danger"


def test_midi_import_cancel_then_confirm_is_one_undoable_change(studio_session, qtbot, tmp_path):
    _app, engine, root, editor, _playback = studio_session
    initialize_deferred_services(engine)
    create_notes(root, editor, qtbot, 1)
    # Freeze the pending->saved debounce before comparing complete note metadata.
    editor.synchronizeNow()
    source = tmp_path / "导入.mid"
    export_midi(source, (Note.create(pitch=67, start_time=0, duration=0.5, velocity=90),))
    before, depth = editor.state.content, editor.historyUndoDepth
    for confirm in (False, True):
        file_menu(root, qtbot, 3)
        accept_file(root, qtbot, "midiImportFileDialog", source)
        dialog = root.findChild(QObject, "midiImportDialog")
        qtbot.waitUntil(lambda: dialog.property("opened"))
        assert engine.rootContext().contextProperty("midiImport").noteCount == 1
        assert editor.state.content == before
        if confirm:
            click(root, "midiReplaceButton")
            click(root, "midiImportConfirmButton")
            assert [note.pitch for note in editor.state.notes] == [67]
            assert editor.historyUndoDepth == depth + 1
            click(root, "studioUndo")
        else:
            click(root, "midiImportCancelButton")
        assert editor.state.content == before
        assert editor.historyUndoDepth == depth


def test_selection_edit_delete_and_layer_controls_change_data_and_history(studio_session, qtbot):
    app, _engine, root, editor, _playback = studio_session
    root.resize(1600, 1000)
    create_notes(root, editor, qtbot)
    canvas = root.findChild(QObject, "drawNoteArea")
    # Ctrl-click both actual note delegates; Ctrl+A is not a declared command.
    first_note = find_item(root, "noteItem-" + editor.state.notes[0].id)
    click_item(root, first_note, 5, first_note.height() / 2)
    second_note = find_item(root, "noteItem-" + editor.state.notes[1].id)
    point = second_note.mapToScene(QPointF(5, second_note.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.ControlModifier, point)
    assert len(editor.state.selected_ids) == 2
    before, depth = editor.state.content, editor.historyUndoDepth
    click(root, "auditionSelectionButton")
    qtbot.waitUntil(lambda: _playback.selectionAudition.playing)
    assert _playback._player._temporary_session is not None
    click(root, "auditionSelectionButton")
    assert not _playback.selectionAudition.playing
    assert editor.state.content == before and editor.historyUndoDepth == depth
    pitches = sorted(note.pitch for note in editor.state.notes)
    canvas.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Up)
    assert sorted(note.pitch for note in editor.state.notes) == [pitch + 1 for pitch in pitches]
    assert editor.historyUndoDepth == depth + 1
    click(root, "studioUndo")
    assert editor.state.content == before
    canvas.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Delete)
    assert not editor.state.active_notes, (editor.state.selected_ids, editor.historyUndoDepth, root.property("editorInputEnabled"), editor.state.notes)
    click(root, "studioUndo")
    assert editor.state.content == before
    click(root, "studioArrangement")
    qtbot.waitUntil(lambda: root.findChild(QObject, "arrangementAddLayer").isVisible())
    click(root, "arrangementAddLayer")
    layer_id = editor.activeLayerId
    assert len(editor.state.layers) == 2
    qtbot.waitUntil(lambda: find_item(root, f"musicLayerMenu-{layer_id}") is not None)
    click(root, f"musicLayerMenu-{layer_id}")
    click(root, f"musicLayerMoveSelectionHere-{layer_id}")
    assert all(note.layer_id == layer_id for note in editor.state.active_notes)
    moved = editor.state.content
    for suffix, field in (("Mute", "muted"), ("Solo", "solo"), ("Visible", "visible"), ("Lock", "locked")):
        qtbot.waitUntil(lambda: find_item(root, f"musicLayer{suffix}-{layer_id}") is not None)
        item = find_item(root, f"musicLayer{suffix}-{layer_id}")
        previous = getattr(editor.state.layer(layer_id), field)
        depth = editor.historyUndoDepth
        click_item(root, item)
        assert getattr(editor.state.layer(layer_id), field) is not previous
        assert editor.historyUndoDepth == depth + 1
        click(root, "studioUndo")
        assert getattr(editor.state.layer(layer_id), field) is previous
    click(root, f"musicLayerMenu-{layer_id}")
    click(root, f"musicLayerPin-{layer_id}")
    assert editor.state.layers[0].id == layer_id and editor.state.layers[0].pinned
    click(root, "studioUndo")
    assert editor.state.content == moved
    depth = editor.historyUndoDepth
    click(root, f"musicLayerMenu-{layer_id}")
    click(root, f"musicLayerDelete-{layer_id}")
    dialog = root.findChild(QObject, "deleteMusicLayerDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    # The safe visible default retains every note in the unclassified layer.
    click(root, "moveNotesThenDeleteLayerButton")
    assert layer_id not in {layer.id for layer in editor.state.layers}
    assert len(editor.state.active_notes) == 2
    assert all(note.layer_id == "layer-unclassified" for note in editor.state.active_notes)
    assert editor.historyUndoDepth == depth + 1
    click(root, "studioUndo")
    assert editor.state.content == moved
    app.processEvents()


@pytest.mark.parametrize('size', [(1000, 640), (1440, 900)])
def test_assistant_new_entry_stages_applies_and_undoes_local_proposal(studio_session, qtbot, size, tmp_path):
    app, engine, root, editor, _playback = studio_session
    initialize_deferred_services(engine)
    root.resize(*size)
    create_notes(root, editor, qtbot, 1)
    editor.synchronizeNow()
    click(root, "studioAssistant")
    window = root
    qtbot.waitUntil(lambda: root.property("studioAssistantOpen"))
    assistant = engine.rootContext().contextProperty("assistant")
    assert assistant.gateway.is_bound_to(editor)
    assert window.color().alphaF() == 1
    before, depth = editor.state.content, editor.historyUndoDepth
    context = assistant.gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    result = assistant.gateway.execute("add_notes", {"notes": [
        {"pitch": 64, "start_time": 0.0, "duration": 0.5, "velocity": 80}
    ]}, context)
    assert result.success
    assistant.presentProposal(context)
    assert editor.state.content == before
    assert assistant.proposal_manager.summary.notes_added == 1
    app.processEvents()
    qtbot.waitUntil(lambda: find_item(window, "assistantProposalApplyButton") is not None)
    listing = root.findChild(QObject, "assistantMessageList")
    qml(listing, 'positionViewAtEnd(); true')
    app.processEvents()
    host = root.findChild(QObject, "studioAssistantHost")
    # The compact card keeps primary actions visible; details and secondary
    # actions are now inside its overflow menu, per the 2026-11-02 brief.
    for name in ("assistantProposalCard", "assistantProposalAuditionButton", "assistantProposalMore", "assistantProposalApplyButton"):
        control = find_item(root, name)
        x = control.mapToScene(QPointF()).x()
        assert x >= host.mapToScene(QPointF()).x()
        assert x + control.width() <= root.width() - 12, (name, x, control.width(), control.parentItem().width(), control.parentItem().parentItem().width())
    click(root, "assistantProposalMore")
    qtbot.waitUntil(lambda: find_item(root, "assistantProposalChangesButton").isVisible())
    click(root, "assistantProposalChangesButton")
    dialog = root.findChild(QObject, "assistantProposalChangesDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    qml(dialog, "close(); true")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert root.grabWindow().save(str(tmp_path / 'assistant-proposal.png'))
    button = find_item(window, "assistantProposalApplyButton")
    click_item(window, button)
    assert len(editor.state.notes) == 2
    assert any(note.pitch == 64 for note in editor.state.notes)
    assert editor.historyUndoDepth == depth + 1
    click(root, "studioAssistantClose")
    click(root, "studioUndo")
    assert editor.state.content == before


def test_transport_seek_music_and_sound_controls_change_real_state(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1600, 1000)
    create_notes(root, editor, qtbot)
    click(root, "studioPlayPauseButton")
    qtbot.waitUntil(lambda: root.findChild(QObject, "unsyncedPlaybackDialog").property("opened"))
    click(root, "syncAndPlayAction")
    qtbot.waitUntil(lambda: playback.playing)
    click(root, "studioPlayPauseButton")
    assert not playback.playing
    ruler = root.findChild(QObject, "timelineSeekArea")
    timeline = root.findChild(QObject, "timeline")
    click_item(root, ruler, float(timeline.property("keyWidth")) + 80, 8)
    assert playback.positionSeconds > 0
    click(root, "studioStopButton")
    assert not playback.playing and playback.positionSeconds == 0
    click(root, "studioSnap")
    snap_menu = root.findChild(QObject, "studioSnapMenu")
    qtbot.waitUntil(lambda: snap_menu.property("opened"))
    click_item(root, qml(snap_menu, "itemAt(0)"))
    assert not editor.snapEnabled
    assert snap_menu.property("opened")
    click_item(root, qml(snap_menu, "itemAt(2)"))
    assert editor.snapEnabled
    QTest.keyClick(root, Qt.Key_Escape)
    click(root, "studioMusicSettings")
    bpm = root.findChild(QObject, "studioBpm")
    qtbot.waitUntil(lambda: bpm.isVisible())
    click_item(root, bpm)
    bpm.forceActiveFocus()
    previous = editor.bpm
    QTest.keyClick(root, Qt.Key_Up)
    assert editor.bpm == previous + 1
    QTest.keyClick(root, Qt.Key_Escape)
    click(root, "studioSoundButton")
    sound_popup = root.findChild(QObject, "studioSoundPopup")
    qtbot.waitUntil(lambda: sound_popup.property("opened"))
    click(root, "studioSoundButton")
    qtbot.waitUntil(lambda: not sound_popup.property("visible"))
    click(root, "studioSoundButton")
    rate = root.findChild(QObject, "studioPlaybackRate")
    qtbot.waitUntil(lambda: rate.isVisible())
    click_item(root, rate)
    qtbot.waitUntil(lambda: bool(qml(rate, "popup.opened")))
    qtbot.waitUntil(lambda: qml(rate, "popup.contentItem.itemAtIndex(3)") is not None)
    choice = qml(rate, "popup.contentItem.itemAtIndex(3)")
    click_item(root, choice)
    assert playback.playbackRate == 1.25
    tone = engine.rootContext().contextProperty("pianoTone")
    slider = root.findChild(QObject, "studioToneSlider")
    if playback.soundParametersAvailable:
        # Nov 4: Studio now tunes both installed sources through the shared DSP.
        previous = playback.projectSoundBrightness
        slider.forceActiveFocus()
        QTest.keyClick(root, Qt.Key_Right)
        assert playback.projectSoundBrightness == pytest.approx(previous + 0.05)
    elif tone.available:
        previous = tone.value
        slider.forceActiveFocus()
        QTest.keyClick(root, Qt.Key_Right)
        assert tone.value == pytest.approx(previous + 0.05)
    else:
        assert not slider.isEnabled() and tone.statusMessage
    QTest.keyClick(root, Qt.Key_Escape)
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    assert playback.volume == 0
    assert visualizer.enabled is False and visualizer.rms == 0
    app.processEvents()


def test_home_library_return_and_import_reach_real_work(studio_session, qtbot, tmp_path):
    _app, engine, root, editor, _playback = studio_session
    click(root, "studioLibraryButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "library")
    click(root, "libraryBackButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "launch")
    imported = tmp_path / "首页导入.mid"
    export_midi(imported, (Note.create(pitch=65, start_time=0, duration=0.5),))
    qml(root, "midiImportFileDialog.options = FileDialog.DontUseNativeDialog")
    click(root, "studioImportButton")
    accept_file(root, qtbot, "midiImportFileDialog", imported)
    dialog = root.findChild(QObject, "midiImportDialog")
    qtbot.waitUntil(lambda: dialog is not None and dialog.property("opened"))
    click(root, "midiReplaceButton")
    click(root, "midiImportConfirmButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert [note.pitch for note in editor.state.active_notes] == [65]


def test_studio_shared_fft_demand_follows_play_mute_hidden_disabled_and_home(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    create_notes(root, editor, qtbot, 1)
    click(root, "studioSave")
    submit_name(root, editor, qtbot, "反馈生命周期")
    settings = engine.rootContext().contextProperty("studioAppearance")
    visualizer = engine.rootContext().contextProperty("audioVisualizer")
    effect = root.findChild(QObject, "studioFeedback")
    assert visualizer._tap is playback.piano_source.mixer._visualizer_tap
    volume = root.findChild(QObject, "studioVolumeSlider")
    click_item(root, volume, volume.width() * 0.65)
    assert playback.volume > 0
    click(root, "studioPlayPauseButton")
    qtbot.waitUntil(lambda: playback.playing and effect.property("analysisRequested"))
    assert visualizer.enabled
    # Mixer hardware startup is replaced by no_audio_device. This is lifecycle
    # evidence, while recorded nonzero PCM/pixel evidence lives in feedback tests.
    root.hide()
    app.processEvents()
    assert not effect.property("analysisRequested") and not visualizer.enabled
    assert not effect.property("animationRunning")
    root.show()
    qtbot.waitUntil(lambda: bool(effect.property("analysisRequested")))
    settings.setValue("feedbackLevel", "off")
    assert not effect.property("analysisRequested") and not visualizer.enabled
    settings.setValue("feedbackLevel", "standard")
    assert effect.property("analysisRequested") and visualizer.enabled
    click_item(root, volume, 0)
    assert playback.volume == 0
    assert not effect.property("analysisRequested") and not visualizer.enabled
    assert effect.property("displayRms") == 0
    click(root, "studioStopButton")
    click(root, "studioHomeNav")
    qtbot.waitUntil(lambda: root.property("pageState") == "launch")
    assert root.findChild(QObject, "studioFeedback") is None
    assert not visualizer.enabled
    assert visualizer.rms == 0


def test_home_examples_and_help_show_real_content_and_return(studio_session, qtbot):
    _app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    before = editor.state.content
    templates = engine.rootContext().contextProperty("templates")
    click(root, "studioTemplatesButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "templates")
    grid = root.findChild(QObject, "templateGrid")
    qtbot.waitUntil(lambda: grid.isVisible() and grid.property("count") > 0)
    assert grid.property("count") == len(templates.templates)
    assert all(entry["noteCount"] > 0 for entry in templates.templates)
    assert templates._preview_player is None and not templates.previewing
    click(root, "templateLibraryBackButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "launch")
    click(root, "studioHelpButton")
    qtbot.waitUntil(lambda: root.findChild(QObject, "supportDialog") is not None)
    dialog = root.findChild(QObject, "supportDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    support = engine.rootContext().contextProperty("support")
    version = root.findChild(QObject, "aboutVersionLabel")
    assert support.version and support.version in version.property("text")
    assert "unknown" not in version.property("text")
    click(root, "supportDialogCloseButton")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert editor.state.content == before and not editor.isDirty
    assert not playback.playing and root.property("pageState") == "launch"


def test_inspector_duration_and_record_buttons_edit_real_notes(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1600, 1000)
    create_notes(root, editor, qtbot, 1)
    before, depth = editor.state.content, editor.historyUndoDepth
    click(root, "studioInspector")
    inspector = root.findChild(QObject, "inspector")
    qtbot.waitUntil(lambda: inspector.property("panelExpanded"))
    field = root.findChild(QObject, "durationField")
    input_item = field.findChild(QObject, "fieldInput")
    click_item(root, input_item)
    QTest.keyClick(root, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(root, Qt.Key_2)
    QTest.keyClick(root, Qt.Key_Return)
    expected = 2.0 if inspector.property("isAdvanced") else 120.0 / editor.bpm
    assert editor.state.selected_notes[0].duration == pytest.approx(expected)
    assert editor.historyUndoDepth == depth + 1
    click(root, "studioUndo")
    assert editor.state.content == before
    click(root, "studioInspector")
    qtbot.waitUntil(lambda: not inspector.property("panelExpanded"))
    assert root.findChild(QObject, "inspectorEdgeAffordance") is None
    click(root, "studioInspector")
    qtbot.waitUntil(lambda: inspector.property("panelExpanded"))
    click(root, "studioInspector")
    qtbot.waitUntil(lambda: not inspector.property("panelExpanded"))

    keyboard = engine.rootContext().contextProperty("keyboard")
    # Integrated recording is additive at the playhead, with a 33ms visual
    # projection, rather than immediate insertion at the end of the score.
    editor.synchronizeNow()
    before_recording = editor.state.content
    playback.seek(0.5)
    assert playback.positionSeconds == pytest.approx(0.5)
    click(root, "studioRecordButton")
    assert keyboard.recording and keyboard._ticker.isActive()
    qtbot.waitUntil(lambda: keyboard._stage == "recording")
    piano_key = find_item(root, "pianoKey-60")
    click_item(root, piano_key)
    qtbot.waitUntil(lambda: len(editor.state.active_notes) == 2)
    recorded_id = keyboard.lastRecordedNoteId
    assert next(note for note in editor.state.notes if note.id == recorded_id).pitch == 60
    click(root, "studioRecordButton")
    assert not keyboard.recording and not keyboard._ticker.isActive()
    assert editor.historyUndoDepth == depth + 1
    recorded = next(note for note in editor.state.notes if note.id == recorded_id)
    assert recorded.duration > 0 and 0.5 <= recorded.start_time < 1.25
    assert editor.state.selected_ids == frozenset({recorded_id})
    click(root, "studioUndo")
    assert editor.state.content == before_recording
    assert playback.volume == 0
    app.processEvents()


def test_more_menu_changes_follow_display_and_real_autosave_policy(studio_session, qtbot):
    _app, engine, root, editor, _playback = studio_session
    create_notes(root, editor, qtbot, 1)
    click(root, "studioSave")
    submit_name(root, editor, qtbot, "工作台设置")
    click_item(root, root.findChild(QObject, "drawNoteArea"), 340, 180)
    editor.synchronizeNow()
    before = editor.state.content
    timeline = root.findChild(QObject, "timeline")
    experience = engine.rootContext().contextProperty("experience")
    preferences = engine.rootContext().contextProperty("savePreferences")
    follow = timeline.property("followPlayback")
    view_menu(root, qtbot, 2)
    assert timeline.property("followPlayback") is not follow
    view_menu(root, qtbot, 2)
    assert timeline.property("followPlayback") is follow

    display = experience.compositeDisplayMode
    view_menu(root, qtbot, 3)
    assert experience.compositeDisplayMode != display
    assert timeline.property("displayMode") == experience.compositeDisplayMode
    view_menu(root, qtbot, 3)
    assert experience.compositeDisplayMode == display

    autosave = preferences.autosaveEnabled
    file_menu(root, qtbot, 11)
    assert preferences.autosaveEnabled is not autosave
    assert editor._autosave_enabled is not autosave
    assert editor._autosave_timer.isActive() is not autosave
    # The toggle reaches the saved preference, not only the menu text.
    assert preferences._settings.value("save/autosaveEnabled", type=bool) is not autosave
    file_menu(root, qtbot, 11)
    assert preferences.autosaveEnabled is autosave
    assert editor._autosave_enabled is autosave
    assert editor.state.content == before
