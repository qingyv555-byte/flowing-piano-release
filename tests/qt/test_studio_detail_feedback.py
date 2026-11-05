"""User-reported settings feedback and popup boundaries, with actual Qt controls."""
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import initialize_deferred_services
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, click_item, find_item, no_audio_device, qml
from tests.qt.test_studio_usability import capture
from tests.qt.test_studio_workflow_ux import open_ai_settings, reveal


def open_settings(session, qtbot, page, theme):
    _, engine, root, _, _ = session
    initialize_deferred_services(engine)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', theme)
    click(root, 'studioAppearanceButton')
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    dialog.setProperty('page', page)
    QTest.qWait(50)
    return root, dialog, appearance


def test_entering_editor_and_resizing_never_uses_a_negative_row_model(studio_session, qtbot, qtlog):
    root = studio_session[2]
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    for size in [(860, 640), (1200, 820)]:
        root.resize(*size)
        QTest.qWait(50)
    assert not [record.message for record in qtlog.records if 'Model size of' in record.message]


def preview_image(root, preview):
    QTest.qWait(80)
    point = preview.mapToScene(QPointF(0, 0)).toPoint()
    return root.grabWindow().copy(point.x(), point.y(), int(preview.width()), int(preview.height()))


@pytest.mark.parametrize('theme', ['paper', 'ink'])
@pytest.mark.parametrize('size', [(860, 640), (1200, 820)])
def test_feedback_cards_keep_titles_and_previews_inside_padding(studio_session, qtbot, theme, size):
    studio_session[2].resize(*size)
    QTest.qWait(50)
    root, dialog, _ = open_settings(studio_session, qtbot, 2, theme)
    for style in ['glass', 'weave']:
        card = find_item(root, 'studioFeedbackStyle-' + style)
        title = find_item(root, 'studioFeedbackStyleTitle-' + style)
        preview = find_item(root, 'studioStylePreview-' + style)
        card_top = card.mapToScene(QPointF(0, 0))
        title_top = title.mapToScene(QPointF(0, 0))
        preview_top = preview.mapToScene(QPointF(0, 0))
        assert title_top.y() >= card_top.y() + 9
        assert preview_top.y() >= title_top.y() + title.height() + 9
        assert preview_top.x() >= card_top.x() + 9
        assert preview_top.x() + preview.width() <= card_top.x() + card.width() - 9
        assert preview_top.y() + preview.height() <= card_top.y() + card.height() - 9
    capture(root, 'feedback-cards-' + theme + '-' + str(size[0]))
    qml(dialog, 'close(); true')


@pytest.mark.parametrize('theme', ['paper', 'ink'])
def test_feedback_parameter_edits_change_visible_preview_and_cancel(studio_session, qtbot, theme):
    root, dialog, appearance = open_settings(studio_session, qtbot, 2, theme)
    original = dict(appearance.values)
    preview = find_item(root, 'studioStylePreview-glass')
    appearance.setValue('feedbackLevel', 'standard')
    appearance.setValue('glassWidth', 1.0)
    before = preview_image(root, preview)
    # A real slider gesture must visibly update the preview, without starting animation/audio.
    slider = find_item(root, 'glassWidthSlider')
    click_item(root, slider, slider.width() * .35)
    assert appearance.values['glassWidth'] < .6
    assert preview_image(root, preview) != before
    for key, value in [('glassPositionX', 0.0), ('glassHue', .1),
                       ('glassHeight', .4), ('glassPositionY', .1), ('feedbackStrength', 'gentle')]:
        before = preview_image(root, preview)
        appearance.setValue(key, value)
        assert preview_image(root, preview) != before, key
    # Previews stay on-screen when reaching the lower customization controls.
    dialog.setProperty('advancedFeedback', True)
    scroll = root.findChild(QObject, 'studioAppearanceScroll')
    qtbot.waitUntil(lambda: qml(scroll, 'contentItem.contentHeight > contentItem.height'))
    QTest.qWait(50)
    qml(scroll, 'contentItem.contentY = Math.max(0, contentItem.contentHeight - contentItem.height); true')
    QTest.qWait(50)
    point = preview.mapToScene(QPointF(0, 0))
    assert point.y() > 0 and point.y() + preview.height() < root.height()
    capture(root, 'feedback-live-' + theme)
    assert not studio_session[4].playing
    assert studio_session[3].noteModel.totalCount == 0
    qml(dialog, 'close(); true')
    assert dict(appearance.values) == original


@pytest.mark.parametrize('theme', ['paper', 'ink'])
def test_preferences_popup_is_themed_and_keyboard_selection_works(studio_session, qtbot, theme):
    root, dialog, _ = open_settings(studio_session, qtbot, 5, theme)
    box = find_item(root, 'preferencesDragPreview')
    click_item(root, box)
    qtbot.waitUntil(lambda: bool(qml(box, 'popup.opened')))
    qtbot.waitUntil(lambda: bool(qml(box, 'popup.contentItem.itemAtIndex(currentIndex) !== null')))
    assert qml(box, 'popup.background.color === theme.popupBackground')
    # opened precedes delegate creation on some frames. Evaluate within the
    # stable combo scope; never pass a missing delegate to QQmlExpression.
    assert qml(box, 'popup.contentItem.itemAtIndex(currentIndex).contentItem.color === theme.textPrimary')
    capture(root, 'dropdown-' + theme)
    QTest.keyClick(root, Qt.Key_Down)
    QTest.keyClick(root, Qt.Key_Return)
    assert box.property('currentText') == '连续'
    assert qml(dialog, 'preferencesDraft.dragPreview') == 'continuous'
    qml(dialog, 'close(); true')


def test_qml_probe_rejects_null_scope_before_entering_qt():
    with pytest.raises(AssertionError, match='live scope'):
        qml(None, 'true')


@pytest.mark.parametrize('size', [(1080, 900), (860, 640)])
def test_connection_fields_fit_viewport_and_footer_has_insets(studio_session, qtbot, size):
    app, engine, root, _, _ = studio_session
    root.resize(*size)
    initialize_deferred_services(engine)
    engine.rootContext().contextProperty('studioAppearance').setValue('themeId', 'paper')
    pane = open_ai_settings(root, engine, qtbot)
    pane.setProperty('advanced', True)
    app.processEvents()
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    scroll = root.findChild(QObject, 'assistantAIServiceScroll')
    qtbot.waitUntil(lambda: scroll.property('availableWidth') > 100)
    for name in ['studioAIBaseUrl', 'studioAIApiKey', 'studioAIModelName']:
        field = root.findChild(QObject, name)
        assert field.width() > 200 and field.width() <= scroll.property('availableWidth') + 1
    # AI services moved to their own dialog; retain viewport/inset assertions.
    footer = qml(dialog, 'footer.contentItem')
    left = footer.mapToScene(QPointF()).x()
    assert left >= dialog.property('x') + 16
    assert left + footer.width() <= dialog.property('x') + dialog.property('width') - 16
    assert qml(scroll, 'contentItem.contentWidth <= availableWidth + 1')
    capture(root, 'connection-fit-' + str(size[0]))
    qml(scroll, 'contentItem.contentY = Math.max(0, contentItem.contentHeight - contentItem.height); true')
    capture(root, 'connection-fit-bottom-' + str(size[0]))
    click(root, 'assistantAIServiceClose')


def test_tone_label_and_audition_use_draft_without_saving(studio_session, qtbot, monkeypatch):
    root, dialog, _ = open_settings(studio_session, qtbot, 4, 'paper')
    _, engine, _, editor, playback = studio_session
    tone = engine.rootContext().contextProperty('pianoTone')
    qtbot.waitUntil(lambda: tone.available)
    # The settings draft now uses the same source-independent DSP as transport.
    original = playback.project_sound_parameters()
    calls = []
    def audition():
        calls.append(playback.piano_source.mixer._sound_effects.parameters)
    monkeypatch.setattr(playback, 'auditionCurrentSound', audition)
    slider = find_item(root, 'preferencesTone')
    click_item(root, slider, slider.width() * .9)
    label = find_item(root, 'preferencesToneDescription')
    assert '明亮' in label.property('text') and '%' in label.property('text')
    draft = qml(dialog, 'preferencesDraft.tone')
    click(root, 'preferencesTonePreview')
    assert calls[0].brightness == pytest.approx(draft)
    assert playback.project_sound_parameters() == original
    assert playback._sound_parameter_preview
    assert editor.noteModel.totalCount == 0 and not editor.isDirty
    capture(root, 'tone-feedback')
    qml(dialog, 'close(); true')
    assert playback.project_sound_parameters() == original
    assert not playback._sound_parameter_preview
    assert playback.piano_source.mixer._sound_effects.parameters == original


def assert_dialog_insets(root, dialog, qtbot):
    qtbot.waitUntil(lambda: bool(dialog.property('opened')))
    QTest.qWait(50)
    assert qml(dialog, 'x >= 0 && y >= 0 && x + width <= parent.width + 1 && y + height <= parent.height + 1')
    sections = ['footer']
    if qml(dialog, 'header !== null && header.implicitHeight > 0'):
        sections.append('header')
    else:
        assert qml(dialog, 'leftPadding >= 16 && rightPadding >= 16')
    for section in sections:
        assert qml(dialog, section + '.leftPadding >= 16 && ' + section + '.rightPadding >= 16')
        assert qml(dialog, section + '.availableHeight > 0')
        assert qml(dialog, section + '.contentItem.width <= width - 32')
    footer = qml(dialog, 'footer')
    bottom = footer.mapToScene(QPointF(0, footer.height())).y()
    assert bottom <= root.height()


@pytest.mark.parametrize('theme', ['paper', 'ink'])
def test_sounding_row_renders_under_notes_across_the_real_editor(studio_session, qtbot, theme):
    _, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', theme)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    editor.createNoteAt(0, 144)
    editor.synchronizeNow()
    editor.clearSelection()
    playback.stopAudition()
    mixer = playback._player._mixer
    mixer._read_output_frames(4096)
    QTest.qWait(50)
    playback.setVolume(.65)
    pitch = editor.state.notes[0].pitch
    playback.play()
    qtbot.waitUntil(lambda: playback._player._project_session is not None)
    mixer._read_output_frames(1024)
    feedback = engine.rootContext().contextProperty('studioFeedback')
    feedback.sample()
    effect = find_item(root, 'studioFeedback')
    assert feedback.voices, (feedback.enabled, playback.playing, mixer.active_voice_ids,
                             {key: effect.property(key) for key in ['eventsRequested', 'noteEnabled', 'playing', 'active']})
    row = find_item(root, 'studioPitchRow-' + str(pitch))
    assert row.property('targetOpacity') > 0, (pitch, [(v['pitch'],v['phase'],v['layerVisible'],v['positioned']) for v in feedback.voices],
        qml(effect, 'rowStrength(' + str(pitch) + ')'), effect.property('strength'), row.parentItem() == effect)
    qtbot.waitUntil(lambda: row.property('opacity') > .01)
    assert row.width() > root.width() * .6
    assert row.property('color') == find_item(root, 'studioFeedback').property('theme').property('feedbackPitch')
    capture(root, 'sounding-row-' + theme)
    playback.pause()
    qtbot.waitUntil(lambda: row.property('opacity') == 0)


@pytest.mark.parametrize('theme', ['paper', 'ink'])
@pytest.mark.parametrize('size', [(860, 640), (1200, 820)])
def test_related_dialogs_have_inset_headers_footers_and_scrollable_content(studio_session, qtbot, theme, size):
    _, engine, root, editor, _ = studio_session
    initialize_deferred_services(engine)
    engine.rootContext().contextProperty('studioAppearance').setValue('themeId', theme)
    root.resize(*size)
    QTest.qWait(50)
    click(root, 'studioHelpButton')
    support = root.findChild(QObject, 'supportDialog')
    assert_dialog_insets(root, support, qtbot)
    scroll = find_item(root, 'supportBodyScroll')
    assert qml(scroll, 'contentItem.contentWidth <= availableWidth + 1')
    qml(scroll, 'contentItem.contentY = contentItem.contentHeight - contentItem.height; true')
    QTest.qWait(50)
    restore = find_item(root, 'restoreDefaultsButton')
    assert restore.mapToScene(QPointF(0, restore.height())).y() < qml(support, 'footer').y() + support.property('y')
    capture(root, 'help-fit-' + theme + '-' + str(size[0]))
    for name in ['resetUiConfirm', 'restoreDefaultsConfirm']:
        confirmation = root.findChild(QObject, name)
        qml(confirmation, 'open(); true')
        assert_dialog_insets(root, confirmation, qtbot)
        qml(confirmation, 'reject(); true')
    qml(support, 'close(); true')
    qml(root, 'openTechnicalDetails("错误详情", "详细诊断\\n".repeat(80)); true')
    details = root.findChild(QObject, 'technicalDetailsDialog')
    assert_dialog_insets(root, details, qtbot)
    capture(root, 'details-fit-' + theme + '-' + str(size[0]))
    qml(details, 'close(); true')
    for request, name in [('audioImportDialogRequested', 'audioImportDialog'),
                          ('midiImportDialogRequested', 'midiImportDialog')]:
        root.setProperty(request, True)
        qtbot.waitUntil(lambda: root.findChild(QObject, name) is not None)
        dialog = root.findChild(QObject, name)
        qml(dialog, 'open(); true')
        assert_dialog_insets(root, dialog, qtbot)
        capture(root, name + '-fit-' + theme + '-' + str(size[0]))
        qml(dialog, 'close(); true')
    qml(root, 'openAssistantWindow(); true')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    root.resize(*size)
    QTest.qWait(50)
    for name in ['assistantCreationScopeDialog', 'assistantCollaborationConfirmDialog',
                 'assistantProposalChangesDialog']:
        dialog = root.findChild(QObject, name)
        qml(dialog, 'open(); true')
        assert_dialog_insets(root, dialog, qtbot)
        qml(dialog, 'close(); true')
    open_ai_settings(root, engine, qtbot)
    dialog = root.findChild(QObject, 'studioAIThirdPartyDialog')
    qml(dialog, 'open(); true')
    assert_dialog_insets(root, dialog, qtbot)
    qml(dialog, 'close(); true')
    click(root, 'assistantAIServiceClose')
    click(root, 'studioAssistantClose')
    assert editor.noteModel.totalCount == 0 and not editor.isDirty
