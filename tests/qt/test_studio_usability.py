"""Offscreen product review: real controls, temporary profiles, no audio device."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from ai_piano_studio.__main__ import initialize_deferred_services

from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, click_item, find_item, no_audio_device, view_menu
from tests.qt.test_studio_workflows import qml
from tests.qt.test_studio_workflow_ux import open_ai_settings, reveal


def test_effect_preview_stops_on_navigation_and_never_starts_audio(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, 'studioAppearanceButton')
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    dialog.setProperty('page', 2)
    preview = find_item(root, 'studioStylePreview-glass')
    assert preview is not None
    before = preview.property('phase')
    dialog.setProperty('previewAnimation', True)
    qtbot.waitUntil(lambda: preview.property('phase') != before)
    dialog.setProperty('page', 0)
    assert not dialog.property('previewAnimation')
    stopped = preview.property('phase')
    QTest.qWait(150)
    assert preview.property('phase') == stopped
    assert not playback.playing
    assert editor.noteModel.totalCount == 0
    QTest.keyClick(root, Qt.Key_Escape)
    assert not dialog.property('previewAnimation')


def test_connection_preset_clears_draft_and_fits_small_window(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1000, 640)
    pane = open_ai_settings(root, engine, qtbot)
    key = root.findChild(QObject, 'studioAIApiKey')
    model = root.findChild(QObject, 'studioAIModelName')
    key.setProperty('text', 'test-only-unsaved-draft')
    model.setProperty('text', 'old-model')
    click(root, 'studioAIAddConnection')
    guard = root.findChild(QObject, 'studioAIUnsavedDialog')
    qtbot.waitUntil(lambda: guard.property('opened'))
    assert key.property('text') == 'test-only-unsaved-draft'
    click(root, 'studioAIDiscardChanges')
    assert pane.property('creating')
    assert key.property('text') == '' and model.property('text') == ''
    address = root.findChild(QObject, 'studioAIBaseUrl')
    assert address.property('text') == 'https://'
    address.setProperty('text', 'https://example.test/v1')
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    assert dialog.property('height') <= root.height() - 32
    assert dialog.property('width') <= root.width() - 32
    capture(root, 'assistant-connection-small')
    click(root, 'assistantAIServiceClose')
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIDiscardChanges')
    qtbot.waitUntil(lambda: not dialog.property('opened'))
    pane = open_ai_settings(root, engine, qtbot)
    assert not pane.property('creating')
    assert root.findChild(QObject, 'studioAIApiKey').property('text') == ''
    assert root.findChild(QObject, 'studioAIBaseUrl').property('text') != 'https://example.test/v1'
    click(root, 'assistantAIServiceClose')


def capture(root, name):
    destination = os.environ.get('STUDIO_AUDIT_CAPTURE_DIR')
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        QTest.qWait(100)
        assert root.grabWindow().save(str(path / (name + '.png')))


def test_settings_drafts_cancel_and_apply_existing_controllers(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    experience = engine.rootContext().contextProperty('experience')
    saves = engine.rootContext().contextProperty('savePreferences')
    original = (playback.volume, experience.newNoteVelocity, saves.autosaveEnabled)
    click(root, 'studioAppearanceButton')
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    for page in range(4, 7):
        click(root, 'studioSettingsCategory' + str(page))
        assert dialog.property('page') == page
        capture(root, 'settings-page-' + str(page))
    qml(dialog, 'editPreference("volume", 0.25); editPreference("velocity", 95); editPreference("autosave", true); true')
    assert (playback.volume, experience.newNoteVelocity, saves.autosaveEnabled) == original
    QTest.keyClick(root, Qt.Key_Escape)
    assert (playback.volume, experience.newNoteVelocity, saves.autosaveEnabled) == original
    click(root, 'studioAppearanceButton')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert qml(dialog, 'preferencesDraft.volume') == original[0]
    qml(dialog, 'editPreference("volume", 0.25); editPreference("velocity", 95); editPreference("autosave", true); editPreference("saveDelay", 30); true')
    click(root, 'studioAppearanceApply')
    qtbot.waitUntil(lambda: not dialog.property('opened'))
    assert playback.volume == .25
    assert experience.newNoteVelocity == 95
    assert saves.autosaveEnabled and saves.autosaveDelaySeconds == 30
    from ai_piano_studio.persistence.save_preferences import SavePreferences
    from ai_piano_studio.experience.settings import ExperienceSettings
    assert SavePreferences(settings=saves._settings).autosaveDelaySeconds == 30
    assert ExperienceSettings(settings=experience._settings).newNoteVelocity == 95


def test_assistant_ai_entry_opens_real_connection_dialog(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioAssistant')
    click(root, 'studioAssistantConnectionSettings')
    qtbot.waitUntil(lambda: find_item(root, 'studioAIServicePane') is not None)
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    assert dialog.property('opened')
    general = root.findChild(QObject, 'studioAppearanceDialog')
    assert general is None or not general.property('opened')
    assert find_item(root, 'studioAIApiKey').isVisible()
    click(root, 'assistantAIServiceClose')



def test_settings_show_valid_values_between_presets_without_changing_them(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    experience = engine.rootContext().contextProperty('experience')
    experience.setNewNoteLengthBeats(3)
    playback.setPlaybackRate(1.1)
    click(root, 'studioAppearanceButton')
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    click(root, 'studioSettingsCategory4')
    qtbot.waitUntil(lambda: find_item(root, 'preferencesRate') is not None)
    assert find_item(root, 'preferencesRate').property('displayText') == '1.1×'
    click(root, 'studioSettingsCategory5')
    assert find_item(root, 'preferencesNoteLength').property('displayText') == '3 拍'
    click(root, 'studioAppearanceApply')
    assert playback.playbackRate == 1.1
    assert experience.newNoteLengthBeats == 3
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioSoundButton')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioSoundPopup').property('opened'))
    assert find_item(root, 'studioPlaybackRate').property('displayText') == '1.1×'


def test_applied_settings_survive_engine_restart_but_cancelled_drafts_do_not(studio_session, qtbot, tmp_path):
    from configparser import ConfigParser
    from ai_piano_studio.studio import build_studio_engine
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, 'studioAppearanceButton')
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.setValue('themeId', 'paper')
    qml(dialog, 'editPreference("volume", 0.25); editPreference("rate", 1.5); editPreference("velocity", 95); editPreference("saveDelay", 30); true')
    click(root, 'studioAppearanceApply')
    qtbot.waitUntil(lambda: not dialog.property('opened'))
    click(root, 'studioAppearanceButton')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    appearance.setValue('themeId', 'deep')
    qml(dialog, 'editPreference("volume", 0.75); editPreference("rate", 0.5); editPreference("velocity", 42); true')
    QTest.keyClick(root, Qt.Key_Escape)
    root.close()
    # Read the actual file independently of QSettings' shared in-process cache.
    stored = ConfigParser()
    stored.read(tmp_path / 'settings' / 'playback.ini', encoding='utf-8')
    assert stored.getfloat('General', 'volume') == .25
    assert stored.getfloat('General', 'rate') == 1.5
    restarted = build_studio_engine(tmp_path)
    _, next_engine, _, next_playback, _ = restarted
    next_root = next_engine.rootObjects()[0]
    try:
        initialize_deferred_services(next_engine)
        app.processEvents()
        assert next_playback.volume == .25
        assert next_playback.playbackRate == 1.5
        assert next_engine.rootContext().contextProperty('experience').newNoteVelocity == 95
        assert next_engine.rootContext().contextProperty('savePreferences').autosaveDelaySeconds == 30
        assert next_engine.rootContext().contextProperty('studioAppearance').values['themeId'] == 'paper'
        click(next_root, 'studioAppearanceButton')
        qtbot.waitUntil(lambda: next_root.findChild(QObject, 'studioAppearanceDialog') is not None)
        next_dialog = next_root.findChild(QObject, 'studioAppearanceDialog')
        qtbot.waitUntil(lambda: next_dialog.property('opened'))
        assert qml(next_dialog, 'preferencesDraft.volume') == .25
        assert qml(next_dialog, 'preferencesDraft.rate') == 1.5
    finally:
        next_playback.stop()
        next_root.close()
        next_engine.deleteLater()
        app.processEvents()


def test_assistant_scope_stays_accessible_after_project_details_dialog(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    qml(root, 'openAssistantWindow(); true')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    root.resize(1000, 640)
    app.processEvents()
    # Compact Studio moved the context into a dialog and scope into the composer.
    scope = find_item(root, 'assistantEditCreationScopeButton')
    assert '未连接' in find_item(root, 'studioAssistantConnectionStatus').property('text')
    assert scope.isVisible() and scope.isEnabled()
    click(root, 'assistantMoreButton')
    click(root, 'studioAssistantContext')
    context = root.findChild(QObject, 'assistantProjectInfoDialog')
    qtbot.waitUntil(lambda: context.property('opened'))
    assert scope.isVisible()
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not context.property('opened'))
    app.processEvents()
    click(root, 'assistantEditCreationScopeButton')
    click(root, 'assistantScopeManual')
    dialog = root.findChild(QObject, 'assistantCreationScopeDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property('opened'))
    assert not context.property('opened') and scope.isVisible() and scope.isEnabled()
    capture(root, 'assistant-scope-summary-small')
    click(root, 'studioAssistantClose')


def test_dense_minimap_repaints_note_colors_when_theme_changes(studio_session, qtbot):
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    app, engine, root, editor, playback = studio_session
    state = ProjectState(tuple(Note.create(id=str(i), pitch=60, start_time=i*.04,
                                          duration=.12) for i in range(1000)))
    editor._replace_project(state, saved_content=state.content, path=None)
    root.setProperty('pageState', 'editor')
    appearance = engine.rootContext().contextProperty('studioAppearance')
    minimap = find_item(root, 'miniMap')
    canvas = find_item(root, 'miniMapDenseNoteCanvas')
    qtbot.waitUntil(canvas.isVisible)
    appearance.beginPreview()
    for theme_id in ('paper', 'ink'):
        appearance.setValue('themeId', theme_id)
        QTest.qWait(100)
        y = (minimap.property('maximumPitch')-60)/minimap.property('pitchSpan')*canvas.height()+1
        point = canvas.mapToScene(QPointF(canvas.width()*.8, y))
        # Default layer is 'other', all saved notes are fully opaque. Choose a
        # point beyond the viewport overlay to observe the actual note paint.
        expected = minimap.property('theme').property('layerOther')
        def correct_pixel():
            image = root.grabWindow()
            scale = image.width() / root.width()
            px, py = round(point.x()*scale), round(point.y()*scale)
            # A fractional DPR can place the center on an antialiased edge.
            return any(image.pixelColor(px, py+dy) == expected for dy in (-1, 0, 1))
        qtbot.waitUntil(correct_pixel)
        capture(root, 'dense-minimap-' + theme_id)
    appearance.cancelPreview()


@pytest.mark.parametrize('display_mode', ['original', 'smart'])
def test_dense_note_colors_follow_theme_mode_and_same_pitch_layers(studio_session, qtbot, display_mode):
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.domain.project import ProjectState
    from ai_piano_studio.domain.music_layer import MusicLayer
    app, engine, root, editor, playback = studio_session
    layers = (MusicLayer.default(), MusicLayer.create(id='second', name='Second', custom_color='#B65973', order=1))
    probes = (Note.create(id='a', pitch=72, start_time=.5, duration=.8),
              Note.create(id='b', pitch=72, start_time=2, duration=.8, layer_id='second'))
    state = ProjectState(probes + tuple(Note.create(id=f'far-{i}', pitch=60,
                         start_time=50+i, duration=.1) for i in range(601)), layers=layers)
    editor._replace_project(state, saved_content=state.content, path=None)
    root.setProperty('pageState', 'editor')
    experience = engine.rootContext().contextProperty('experience')
    experience.setLayerViewMode('all')
    appearance = engine.rootContext().contextProperty('studioAppearance')
    timeline = find_item(root, 'timeline')
    timeline.setProperty('displayMode', display_mode)
    canvas = find_item(root, 'rawNoteCanvas' if display_mode == 'original' else 'smartCompositeCanvas')
    qtbot.waitUntil(canvas.isVisible)
    appearance.beginPreview()
    for theme_id in ('paper', 'ink'):
        appearance.setValue('themeId', theme_id)
        for mode in ('layer', 'mono', 'pitch'):
            experience.setNoteColorMode(mode)
            for note, layer in zip(probes, layers):
                # Both dense paths use opaque fills in Studio. Check an
                # interior pixel, not a border pixel (which can be anti-
                # aliased when the background/theme changes).
                expression = f'notePresentationColor(72, "other", false, "{layer.custom_color or ""}")'
                expected = qml(timeline, expression)
                expected = expected.name() if hasattr(expected, 'name') else str(expected).lower()
                x = timeline.property('keyWidth') + (note.start_time+.6)*timeline.property('pixelsPerSecond')
                y = (timeline.property('highestPitch')-72)*timeline.property('rowHeight')+8
                point = canvas.mapToScene(QPointF(x, y))
                def correct_pixel():
                    image = root.grabWindow()
                    scale = image.width()/root.width()
                    actual = image.pixelColor(round(point.x()*scale), round(point.y()*scale)).name()
                    assert actual == expected, (theme_id, mode, note.id, point, actual, expected)
                qtbot.waitUntil(correct_pixel)
    appearance.cancelPreview()


def test_connection_list_discards_key_draft_and_test_never_saves(studio_session, qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
    from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    assistant._provider_settings = ProviderSettingsService(settings=QSettings(str(tmp_path / 'providers.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())
    assert assistant.saveProviderSettings('openai', 'OpenAI', 'https://api.openai.com/v1', 'test-key', 'gpt-4o-mini', True)['success']
    pane = open_ai_settings(root, engine, qtbot)
    field = root.findChild(QObject, 'studioAIApiKey')
    button = root.findChild(QObject, 'studioAITestConnection')
    field.setProperty('text', 'unsaved-test-key')
    assert pane.property('dirty') and not button.isEnabled()
    click(root, 'studioAIConnection-openai')
    assert field.property('text') == 'unsaved-test-key'
    click(root, 'studioAIConnection-deepseek')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIUnsavedDialog').property('opened'))
    click(root, 'studioAIDiscardChanges')
    click(root, 'studioAIConnection-openai')
    assert field.property('text') == '' and not pane.property('dirty')
    assert button.isEnabled()
    calls = []
    monkeypatch.setattr(assistant, 'testConnectionAsync', lambda provider: calls.append(provider) or True)
    click_item(root, reveal(root, 'studioAITestConnection'))
    assert calls == ['openai']
    assert assistant._provider_settings.public_config('openai')['modelId'] == 'gpt-4o-mini'
    root.resize(1000, 640)
    app.processEvents()
    capture(root, 'connection-list-small')
    click(root, 'assistantAIServiceClose')


def test_connection_list_keyboard_changes_connection_and_clears_key_draft(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    root.resize(1000, 640)
    pane = open_ai_settings(root, engine, qtbot)
    assistant = engine.rootContext().contextProperty('assistant')
    click(root, 'studioAIConnection-openai')
    item = find_item(root, 'studioAIConnection-openai')
    item.forceActiveFocus()
    field = root.findChild(QObject, 'studioAIApiKey')
    field.setProperty('text', 'not-saved-test-key')
    QTest.keyClick(root, Qt.Key_Down)
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIUnsavedDialog').property('opened'))
    assert assistant.currentProviderId == 'openai'
    click(root, 'studioAIDiscardChanges')
    assert assistant.currentProviderId == 'anthropic' and field.property('text') == ''
    qtbot.waitUntil(lambda: find_item(root, 'studioAIConnection-anthropic').hasActiveFocus())
    QTest.keyClick(root, Qt.Key_Up)
    qtbot.waitUntil(lambda: assistant.currentProviderId == 'openai')
    qtbot.waitUntil(lambda: find_item(root, 'studioAIConnection-openai').hasActiveFocus())
    # Navigation follows the visible API/subscription/local/custom grouping.
    profiles = qml(pane, 'profiles.map(function(p) {return p.id}).join("|")').split('|')
    assert set(profiles) == {profile['id'] for profile in assistant.connectionProfiles}
    for provider_id in profiles[1:]:
        QTest.keyClick(root, Qt.Key_Down)
        qtbot.waitUntil(lambda: assistant.currentProviderId == provider_id)
        qtbot.waitUntil(lambda: find_item(root, 'studioAIConnection-' + provider_id).hasActiveFocus())
    listing = root.findChild(QObject, 'studioAIProviderScroll')
    last = find_item(root, 'studioAIConnection-' + profiles[-1])
    y = last.mapToItem(qml(listing, 'contentItem'), QPointF()).y()
    assert y >= -1 and y + last.height() <= qml(listing, 'contentItem.height') + 1
    click(root, 'assistantAIServiceClose')


def test_paper_playhead_and_pedal_view_remain_usable_at_minimum_width(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    root.resize(1000, 640)
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.beginPreview()
    appearance.setValue('themeId', 'paper')
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    editor.createNoteAt(200, 180)
    editor.synchronizeNow()
    playback.seek(1.0)
    playhead = find_item(root, 'playhead')
    qtbot.waitUntil(playhead.isVisible)
    assert playhead.property('color').name() == '#315f64'
    keyboard = engine.rootContext().contextProperty('keyboard')
    previous = keyboard.showPedalLane
    view_menu(root, qtbot, 5)
    assert keyboard.showPedalLane != previous
    capture(root, 'paper-playhead-pedal')
    view_menu(root, qtbot, 5)
    assert keyboard.showPedalLane == previous
    click(root, 'playingSettingsButton')
    dialog = root.findChild(QObject, 'playingSettingsDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    capture(root, 'paper-playing-settings')
    QTest.keyClick(root, Qt.Key_Escape)
    appearance.cancelPreview()


def test_home_prioritizes_continuing_without_replacing_current_notes(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    editor.createNoteAt(200, 180)
    editor.synchronizeNow()
    before = editor.state.content
    continue_button = root.findChild(QObject, 'studioContinueButton')
    assert continue_button.isVisible()
    assert continue_button.property('variant') == 'primary'
    assert root.findChild(QObject, 'studioCreateButton').property('variant') == 'secondary'
    capture(root, 'home-returning')
    click(root, 'studioContinueButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    assert editor.state.content == before


@pytest.mark.parametrize('size', [(1000, 640), (1366, 768)])
def test_workspace_review_surfaces(studio_session, qtbot, size):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    root.resize(*size)
    tag = str(size[0])
    capture(root, tag + '-home')
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    capture(root, tag + '-editor')
    status = root.findChild(QObject, 'studioStatusSummary')
    assert '已同步' in status.property('fullText')
    assert '同步' not in status.property('text')
    assert '长度' in status.property('fullText')
    click(root, 'studioMusicSettings')
    popup = root.findChild(QObject, 'studioMusicPopup')
    qtbot.waitUntil(lambda: popup.property('opened'))
    capture(root, tag + '-rhythm')
    QTest.keyClick(root, Qt.Key_Escape)
    click(root, 'studioSoundButton')
    popup = root.findChild(QObject, 'studioSoundPopup')
    qtbot.waitUntil(lambda: popup.property('opened'))
    capture(root, tag + '-sound')
    QTest.keyClick(root, Qt.Key_Escape)
    editor.createNoteAt(0, 0)
    editor.createNoteAt(160, 36)
    click(root, 'studioInspector')
    click(root, 'studioArrangement')
    capture(root, tag + '-panels')
    view_menu(root, qtbot, 0)
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAppearanceDialog') is not None)
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    capture(root, tag + '-appearance')
    for page in (1, 2, 3):
        dialog.setProperty('page', page)
        capture(root, tag + '-appearance-' + str(page))
    QTest.keyClick(root, Qt.Key_Escape)
    assert editor.noteModel.totalCount == 2
    assert editor.isDirty


def test_empty_overview_does_not_cover_canvas_or_change_saved_preference(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    overview = root.findChild(QObject, 'miniMap')
    experience = engine.rootContext().contextProperty('experience')
    experience.setMiniMapDisplayMode('visible')
    assert not overview.isVisible()
    editor.createNoteAt(0, 0)
    qtbot.waitUntil(overview.isVisible)
    editor.undo()
    qtbot.waitUntil(lambda: not overview.isVisible())
    assert experience.miniMapDisplayMode == 'visible'
    experience.setMiniMapDisplayMode('hidden')
    editor.redo()
    assert not overview.isVisible()


@pytest.mark.parametrize('theme', ['deep', 'paper'])
def test_selection_and_drag_stay_aligned_after_panel_and_window_changes(studio_session, qtbot, theme):
    app, engine, root, editor, playback = studio_session
    appearance = engine.rootContext().contextProperty('studioAppearance')
    appearance.beginPreview()
    appearance.setValue('themeId', theme)
    root.resize(1000, 640)
    app.processEvents()
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'drawNoteArea') is not None)
    canvas = root.findChild(QObject, 'drawNoteArea')
    qtbot.waitUntil(lambda: canvas.width() > 800)
    click_item(root, canvas, 300, 180)
    original = editor.state.notes[0]
    timeline = root.findChild(QObject, 'timeline')
    for panel in ('studioInspector', 'studioArrangement', 'studioInspector', 'studioArrangement'):
        click(root, panel)
        app.processEvents()
        item = find_item(root, 'noteItem-' + original.id)
        before = (timeline.mapToScene(QPointF()), timeline.width(), timeline.height(),
                  item.mapToScene(QPointF()))
        editor.clearSelection()
        click_item(root, item, 10, item.height() / 2)
        app.processEvents()
        assert editor.state.selected_ids == frozenset({original.id})
        assert before == (timeline.mapToScene(QPointF()), timeline.width(), timeline.height(),
                          item.mapToScene(QPointF()))
    root.resize(1200, 740)
    app.processEvents()
    item = find_item(root, 'noteItem-' + original.id)
    start = item.mapToScene(QPointF(10, item.height() / 2)).toPoint()
    end = start + QPoint(40, -18)
    depth = editor.historyUndoDepth
    QTest.mousePress(root, Qt.LeftButton, pos=start)
    QTest.mouseMove(root, end, delay=30)
    QTest.mouseRelease(root, Qt.LeftButton, pos=end)
    moved = editor.state.selected_notes[0]
    assert moved.start_time == pytest.approx(original.start_time + .5)
    assert moved.pitch == original.pitch + 1
    assert editor.historyUndoDepth == depth + 1
    click(root, 'studioUndo')
    assert editor.state.notes[0] == original
    click(root, 'studioMusicSettings')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioMusicPopup').property('opened'))
    capture(root, theme + '-rhythm-and-editing')
