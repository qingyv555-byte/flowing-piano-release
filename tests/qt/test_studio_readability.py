"""Real Qt menu/toolbar/text colors, with device-free rendering and interactions."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from ai_piano_studio.appearance.image_palette import luminance
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, click_item, find_item, qml
from tests.music_agent.test_imported_song_extension import imported_source
from tools.studio_evidence import install_memory_output
from ai_piano_studio.__main__ import initialize_deferred_services


@pytest.fixture(autouse=True)
def silent_output(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def contrast(foreground, background):
    fg, bg = QColor(foreground), QColor(background)
    a = fg.alphaF()
    composed = QColor.fromRgbF(*(a * x + (1 - a) * y for x, y in zip(fg.getRgbF()[:3], bg.getRgbF()[:3])))
    first, second = luminance(composed), luminance(bg)
    return (max(first, second) + .05) / (min(first, second) + .05)


def editor_view(session, qtbot, tmp_path):
    app, engine, root, editor, playback = session
    root.resize(1366, 768)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    source = imported_source(tmp_path)
    assert editor.importMidiResult(source.midi)
    editor.synchronizeNow()
    editor.selectNote(editor.state.active_notes[0].id, False)
    qtbot.waitUntil(lambda: find_item(root, 'studioArrangement') is not None
                    and find_item(root, 'studioArrangement').isVisible())
    click(root, 'studioArrangement')
    qtbot.waitUntil(lambda: find_item(root, 'arrangementPanel').isVisible())
    return app, engine, root, editor, find_item(root, 'timeline')


def open_layer_menu(root, editor, qtbot):
    layer = editor.state.layers[0].id
    row = find_item(root, 'musicLayerRow-' + layer)
    click(root, 'musicLayerMenu-' + layer)
    items = [item for item in row.findChildren(QObject) if item.inherits('QQuickMenuItem')]
    lock = next(item for item in items if item.property('text') == '锁定音乐层')
    qtbot.waitUntil(lambda: lock.isVisible())
    point = lock.mapToScene(QPointF(lock.width() / 2, lock.height() / 2)).toPoint()
    QTest.mouseMove(root, point)
    qtbot.waitUntil(lambda: lock.property('highlighted'))
    return row, items, lock


@pytest.mark.parametrize('mode', ['paper', 'ink'])
def test_layer_menu_hover_text_has_sufficient_contrast(studio_session, qtbot, tmp_path, mode):
    app, engine, root, editor, timeline = editor_view(studio_session, qtbot, tmp_path)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', mode)
    row, items, lock = open_layer_menu(root, editor, qtbot)
    fg = lock.property('contentItem').property('color')
    bg = lock.property('background').property('color')
    evidence = Path('artifacts/theme-readability')
    evidence.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(evidence / f'{mode}-menu.png'))
    assert contrast(fg, bg) >= 4.5, (QColor(fg).name(), QColor(bg).name(), contrast(fg, bg))
    menu = row.findChild(QObject, 'musicLayerMenuPopup-' + editor.state.layers[0].id)
    before_navigation = editor.state.content
    QTest.keyClick(root, Qt.Key_Down)
    pin = next(item for item in items if item.objectName() == 'musicLayerPin-' + editor.state.layers[0].id)
    qtbot.waitUntil(lambda: pin.property('highlighted'))
    assert editor.state.content == before_navigation
    menu = row.findChild(QObject, 'musicLayerMenuPopup-' + editor.state.layers[0].id)
    qml(menu, 'close(), true')


def visible_item(root, name):
    pending = [root.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == name and item.isVisible():
            return item
        pending.extend(item.childItems())
    raise AssertionError(f'Visible item missing: {name}')


def assert_controls_fit_horizontal_clips(root):
    """Catch real controls protruding across a clipping parent, including nested panels."""
    pending = [root.contentItem()]
    checked = 0
    while pending:
        item = pending.pop()
        pending.extend(item.childItems())
        if not item.isVisible() or not item.inherits('QQuickControl') or item.width() <= 0:
            continue
        scene = item.mapToScene(QPointF(0, 0))
        if scene.y() + item.height() <= 0 or scene.y() >= root.height():
            continue
        ancestor = item.parentItem()
        while ancestor is not None:
            if ancestor.clip():
                left = item.mapToItem(ancestor, QPointF(0, 0)).x()
                assert left >= -.75, (item.objectName(), ancestor.objectName(), left)
                assert left + item.width() <= ancestor.width() + .75, (item.objectName(), ancestor.objectName(), left, item.width(), ancestor.width())
                checked += 1
            ancestor = ancestor.parentItem()
    assert checked >= 10


@pytest.mark.parametrize('mode', ['paper', 'ink'])
def test_all_color_schemes_keep_menu_states_and_custom_note_text_readable(studio_session, qtbot, tmp_path, mode):
    app, engine, root, editor, timeline = editor_view(studio_session, qtbot, tmp_path)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', mode)
    for scheme in ('none', 'luna', 'hack', 'bee', 'gale', 'traveler', '#000000', '#ffffff', '#e85840'):
        if scheme.startswith('#'):
            appearance.setValue('customThemeColor', scheme)
            appearance.setValue('themeSeedSource', 'custom')
            appearance.setValue('adaptiveTheme', True)
        else:
            appearance.setValue('themePalette', scheme)
            appearance.setValue('themeSeedSource', 'palette')
            appearance.setValue('adaptiveTheme', scheme != 'none')
        for fg in ('textPrimary', 'textSecondary', 'danger'):
            for bg in ('surface', 'elevatedSurface', 'surfaceHover', 'surfacePressed', 'surfaceSelected', 'popupBackground', 'inputBackground'):
                assert contrast(qml(timeline, 'theme.' + fg), qml(timeline, 'theme.' + bg)) >= 4.5, (mode, scheme, fg, bg)
        for fill in ('#000000', '#ffffff', '#777777', '#ffff00', '#0000ff', '#e85840', '#458ad5', '#52a072'):
            label = qml(timeline, f'theme.textOnColor("{fill}")')
            assert contrast(label, QColor(fill)) >= 4.5, (scheme, fill)
        row, items, lock = open_layer_menu(root, editor, qtbot)
        assert contrast(lock.property('contentItem').property('color'), lock.property('background').property('color')) >= 4.5
        menu = row.findChild(QObject, 'musicLayerMenuPopup-' + editor.state.layers[0].id)
        qml(menu, 'close(), true')


@pytest.mark.parametrize('mode', ['paper', 'ink'])
def test_layer_color_picker_pointer_keyboard_cancel_apply_and_undo(studio_session, qtbot, tmp_path, mode):
    app, engine, root, editor, timeline = editor_view(studio_session, qtbot, tmp_path)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', mode)
    before = editor.state.content
    before_notes = editor.state.notes
    row, items, lock = open_layer_menu(root, editor, qtbot)
    click_item(root, next(item for item in items if item.property('text') == '更改颜色'))
    popup = root.findChild(QObject, 'musicLayerColorPopup')
    qtbot.waitUntil(lambda: popup.property('opened'))
    spectrum = visible_item(root, 'colorSpectrum')
    click_item(root, spectrum, spectrum.width() * .8, spectrum.height() * .25)
    picked = popup.property('selectedColor')
    assert picked.hsvSaturationF() == pytest.approx(.8, abs=.02)
    assert picked.valueF() == pytest.approx(.75, abs=.02)
    QTest.keyClick(root, Qt.Key_Left)
    assert popup.property('selectedColor').hsvSaturationF() < picked.hsvSaturationF()
    start = spectrum.mapToScene(QPointF(spectrum.width() * .8, spectrum.height() * .25)).toPoint()
    end = spectrum.mapToScene(QPointF(spectrum.width() * .4, spectrum.height() * .5)).toPoint()
    QTest.mousePress(root, Qt.LeftButton, pos=start)
    QTest.mouseMove(root, end)
    QTest.mouseRelease(root, Qt.LeftButton, pos=end)
    assert popup.property('selectedColor').hsvSaturationF() == pytest.approx(.4, abs=.02)
    assert popup.property('selectedColor').valueF() == pytest.approx(.5, abs=.02)
    hue = visible_item(root, 'colorHueSlider')
    qml(hue, 'forceActiveFocus(), true')
    previous_hue = popup.property('selectedColor').hsvHueF()
    QTest.keyClick(root, Qt.Key_Right)
    assert popup.property('selectedColor').hsvHueF() > previous_hue
    assert editor.state.content == before
    assert popup.property('height') < root.height() and popup.property('width') < root.width()
    evidence = Path('artifacts/theme-readability')
    evidence.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(evidence / f'{mode}-picker.png'))
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not popup.property('visible'))
    assert editor.state.content == before
    row, items, lock = open_layer_menu(root, editor, qtbot)
    click_item(root, next(item for item in items if item.property('text') == '更改颜色'))
    qtbot.waitUntil(lambda: popup.property('opened'))
    click_item(root, visible_item(root, 'colorSwatch-6'))
    picked = popup.property('selectedColor')
    click_item(root, visible_item(root, 'colorApplyButton'))
    editor.synchronizeNow()
    assert editor.state.layers[0].custom_color.lower() == picked.name()
    assert editor.state.notes == before_notes
    editor.undo()
    assert editor.state.content == before


@pytest.mark.parametrize('mode', ['paper', 'ink'])
def test_note_pitch_labels_meet_text_contrast(studio_session, qtbot, tmp_path, mode):
    app, engine, root, editor, timeline = editor_view(studio_session, qtbot, tmp_path)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', mode)
    for pitch in (36, 48, 60, 72, 84):
        fill = qml(timeline, f'theme.notePitchColor({pitch}, 36, 84)')
        text = qml(timeline, f'theme.noteLabelColorForPitch({pitch}, 36, 84)')
        assert contrast(text, fill) >= 4.5, (mode, pitch, QColor(text).name(), QColor(fill).name(), contrast(text, fill))


@pytest.mark.parametrize('size', [(1366, 768), (1920, 1080)])
def test_inspector_horizontal_bounds_and_note_style_preview_apply_cancel(studio_session, qtbot, tmp_path, size):
    app, engine, root, editor, timeline = editor_view(studio_session, qtbot, tmp_path)
    root.resize(*size)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    before = editor.state.content
    click(root, 'studioInspector')
    qtbot.waitUntil(lambda: find_item(root, 'inspector').property('panelExpanded'))
    flick = find_item(root, 'inspectorBodyFlickable')
    column = find_item(root, 'inspectorContentColumn')
    qtbot.waitUntil(lambda: column.width() > 0)
    assert column.x() >= 3 and column.x() + column.width() < flick.width()
    # Focus the real first field and inspect its entire control against the clip edge.
    controls = [item for item in column.findChildren(QObject) if item.objectName() == 'fieldInput' and item.isVisible()]
    assert controls
    first = controls[0]
    qml(first, 'forceActiveFocus(), true')
    left = first.mapToItem(flick, QPointF(0, 0)).x()
    assert left >= 3 and left + first.width() <= flick.width() - 3
    assert_controls_fit_horizontal_clips(root)
    editor.clearSelection()
    editor.synchronizeNow()
    note = find_item(root, 'noteItem-' + editor.state.active_notes[0].id)
    assert note is not None
    assert qml(note, 'border.width') == 0
    assert qml(note, 'radius') == 1
    initialize_deferred_services(engine)
    click(root, 'studioSettingsButton')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAppearanceDialog') is not None)
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert_controls_fit_horizontal_clips(root)
    qml(dialog, 'page=1, true')
    from tests.qt.test_settings_interactions import press
    press(root, 'studioNoteStyle-rounded')
    assert appearance.values['noteStyle'] == 'rounded'
    assert qml(note, 'radius') == 5
    press(root, 'studioNoteStyle-velocity')
    assert qml(timeline, 'theme.noteBodyColor("#53a79b", 30)') != qml(timeline, 'theme.noteBodyColor("#53a79b", 120)')
    assert editor.state.content == before
    appearance.cancelPreview()
    assert appearance.values['noteStyle'] == 'flat'
    appearance.beginPreview()
    appearance.setValue('noteStyle', 'rounded')
    assert appearance.applyPreview()
    reopened = type(appearance)(appearance._settings, appearance._asset_home)
    assert reopened.values['noteStyle'] == 'rounded'
    evidence = Path('artifacts/theme-readability')
    evidence.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(evidence / f'{size[0]}-styles.png'))
    # Visit every settings pane and both ends of each scroll range: shared
    # controls must fit where they are used, not only on the notes pane.
    for page in range(7):
        qml(dialog, f'page={page}, true')
        root.grabWindow()
        scroll = find_item(root, 'studioAppearanceScroll')
        for edge in ('0', 'Math.max(0, contentItem.contentHeight-contentItem.height)'):
            qml(scroll, f'contentItem.contentY={edge}, true')
            root.grabWindow()
            assert_controls_fit_horizontal_clips(root)
    qml(dialog, 'close(), true')
