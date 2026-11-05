"""Menu readability under a conflicting platform palette, without desktop input."""
from pathlib import Path
import base64
import wave

import pytest
from PySide6.QtCore import QObject, QUrl

from tests.qt.test_studio_shell import preserved_qsettings_state, studio_session
from tests.qt.test_studio_workflows import accept_file, click, click_item, qml
from tests.qt.test_studio_workflow_ux import memory_audio, open_ai_settings
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.project_json import save_project


def contrast(first, second):
    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                  for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    values = sorted((luminance(first), luminance(second)))
    return (values[1] + 0.05) / (values[0] + 0.05)


@pytest.mark.parametrize("theme_id", ["paper", "ink"])
@pytest.mark.parametrize("target", ["file", "connections"])
def test_studio_menus_remain_readable_with_conflicting_system_palette(
        studio_session, qtbot, theme_id, target):
    _app, engine, root, _editor, _playback = studio_session
    appearance = engine.rootContext().contextProperty("studioAppearance")
    appearance.setValue("themeId", theme_id)
    if target == "file":
        click(root, "studioCreateButton")
        qtbot.waitUntil(lambda: root.property("pageState") == "editor")
        click(root, "studioFileMenuButton")
        menu = root.findChild(QObject, "studioFileMenu")
    else:
        open_ai_settings(root, engine, qtbot)
        menu = root.findChild(QObject, "assistantConnectionTemplates")
        # The retained connection editor owns this menu; its parent must be visible.
        qml(menu, "aiServiceDialog.close(); settingsDialog.open(); true")
        qtbot.waitUntil(lambda: root.findChild(QObject, "assistantSettingsDialog").property("opened"))
        # Native platform popups cannot be observed with the offscreen driver.
        qml(menu, "popupType = 0; popup(); true")
    qtbot.waitUntil(lambda: menu.property("opened"))
    # Reproduce the unreadable dark highlight/dark text seen on Windows.
    # App menus must resolve their own semantic foreground/background colors.
    qml(menu, 'palette.highlight = "#191919"; palette.highlightedText = "#243e49"; true')
    item = qml(menu, "itemAt(0)")
    for state in ("normal", "highlighted", "down"):
        qml(item, f'highlighted = {str(state == "highlighted").lower()}; '
                  f'down = {str(state == "down").lower()}; true')
        foreground = qml(item, "contentItem.color")
        background = qml(item, "background.color")
        if background.alphaF() == 0:
            background = qml(menu, "background.color")
        assert contrast(foreground, background) >= 4.5, (
            target, theme_id, state, foreground.name(), background.name())
        assert qml(item, "font.pixelSize") >= 13
    qml(menu, "close(); true")


@pytest.mark.parametrize("entry", ["home", "file"])
@pytest.mark.parametrize("suffix", [".pianoai.json", ".json", ".JSON"])
def test_import_opens_json_projects_without_choosing_a_file_type(studio_session, qtbot, tmp_path, entry, suffix):
    _app, _engine, root, editor, _playback = studio_session
    source = tmp_path / ("用户检查" + suffix)
    save_project(source, ProjectState(notes=(Note.create(pitch=67, start_time=0, duration=0.75),)))
    qml(root, "midiImportFileDialog.options = FileDialog.DontUseNativeDialog")
    if entry == "home":
        click(root, "studioImportButton")
    else:
        click(root, "studioCreateButton")
        qtbot.waitUntil(lambda: root.property("pageState") == "editor")
        click(root, "studioFileMenuButton")
        menu = root.findChild(QObject, "studioFileMenu")
        qtbot.waitUntil(lambda: menu.property("opened"))
        item = qml(menu, '(function() { for (var i = 0; i < count; ++i) '
                   '{ var item = itemAt(i); if (item.action && item.action.objectName === "studioImportMidiAction") return item; } return null; })()')
        assert item.property("text") == "导入文件…"
        click_item(root, item)
    picker = qml(root, "midiImportFileDialog")
    qtbot.waitUntil(lambda: picker.property("visible"))
    filters = qml(picker, "nameFilters[0]")
    for extension in ("*.json", "*.pianoai.json", "*.mid", "*.midi", "*.wav", "*.mp3"):
        assert extension in filters
    accept_file(root, qtbot, "midiImportFileDialog", source)
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    assert [note.pitch for note in editor.state.playback_notes] == [67]
    assert Path(editor.currentFilePath) == source


def test_cancel_import_preserves_unsaved_music_and_history(studio_session, qtbot):
    _app, _engine, root, editor, _playback = studio_session
    click(root, "studioCreateButton")
    qtbot.waitUntil(lambda: root.property("pageState") == "editor")
    editor.createNoteAt(0, 0)
    before, depth, path = editor.state.content, editor.historyUndoDepth, editor.currentFilePath
    qml(root, "midiImportFileDialog.options = FileDialog.DontUseNativeDialog; midiImportFileDialog.open(); true")
    picker = qml(root, "midiImportFileDialog")
    qtbot.waitUntil(lambda: picker.property("visible"))
    assert root.property("editorSurfaceOpen")
    assert not root.property("editorInputEnabled")
    picker.rejected.emit()
    picker.close()
    qtbot.waitUntil(lambda: not picker.property("visible"))
    assert editor.state.content == before and editor.historyUndoDepth == depth
    assert editor.currentFilePath == path and editor.isDirty


def test_each_import_starts_with_all_supported_types_visible(studio_session, qtbot):
    _app, _engine, root, _editor, _playback = studio_session
    qml(root, "midiImportFileDialog.options = FileDialog.DontUseNativeDialog")
    click(root, "studioImportButton")
    picker = qml(root, "midiImportFileDialog")
    qtbot.waitUntil(lambda: picker.property("visible"))
    qml(picker, "selectedNameFilter.index = 2; true")
    assert qml(picker, "selectedNameFilter.index") == 2
    picker.close()
    qtbot.waitUntil(lambda: not picker.property("visible"))
    qml(picker, "open(); true")
    qtbot.waitUntil(lambda: picker.property("visible"))
    assert qml(picker, "selectedNameFilter.index") == 0
    picker.close()


@pytest.mark.parametrize("suffix", [".wav", ".MP3"])
def test_unified_import_routes_audio_to_preview_without_changing_music(
        studio_session, qtbot, tmp_path, suffix):
    _app, _engine, root, editor, _playback = studio_session
    source = tmp_path / ("待提取" + suffix)
    if suffix.lower() == ".mp3":
        from tests.audio.test_importer import _SHORT_SILENT_MP3
        source.write_bytes(base64.b64decode(_SHORT_SILENT_MP3))
    else:
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b"\0\0" * 4410)
    before, depth = editor.state.content, editor.historyUndoDepth
    qml(root, "midiImportFileDialog.options = FileDialog.DontUseNativeDialog")
    click(root, "studioImportButton")
    accept_file(root, qtbot, "midiImportFileDialog", source)
    qtbot.waitUntil(lambda: root.findChild(QObject, "audioImportDialog") is not None)
    dialog = root.findChild(QObject, "audioImportDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert dialog.property("sourceUrl") == QUrl.fromLocalFile(str(source)).toString()
    assert editor.state.content == before and editor.historyUndoDepth == depth
    dialog.close()
