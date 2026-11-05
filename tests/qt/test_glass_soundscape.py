"""Geometry and settings checks; never start playback or a hardware sink."""
from PySide6.QtCore import QObject, QMetaObject, Q_ARG, Qt
from PySide6.QtQml import QQmlExpression, qmlContext
from tests.qt.test_studio_shell import studio_session, click
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_appearance import appearance, appearance_window, visual_child
from ai_piano_studio.appearance.studio_settings import StudioAppearance


def test_glass_geometry_preserves_editor_and_cancel_restores(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    settings = engine.rootContext().contextProperty('studioAppearance')
    settings.beginPreview()
    settings.setValue('feedbackStyle', 'glass')
    glass = root.findChild(QObject, 'studioGlassSoundscape')
    timeline = root.findChild(QObject, 'timeline')
    qtbot.waitUntil(lambda: glass.width() > 500)
    original = (timeline.width(), timeline.height(), editor.state.content, editor.historyUndoDepth)
    full_width = glass.width()
    settings.setValue('glassWidth', .5)
    settings.setValue('glassHeight', .35)
    settings.setValue('glassPositionX', 1)
    settings.setValue('glassPositionY', 0)
    root.grabWindow()
    assert abs(glass.width() - full_width / 2) < 1
    assert glass.y() == 0 and glass.x() > full_width / 2
    pcm = root.findChild(QObject, 'studioPcmFeedback')
    feedback = root.findChild(QObject, 'studioFeedback')
    settings.setValue('feedbackStyle', 'weave')
    qtbot.waitUntil(pcm.isVisible)
    assert abs(pcm.width() - glass.width()) < 1
    assert abs(pcm.x() - glass.x()) < 1
    playback.setVolume(.65)
    levels = []
    for band in (.05, .3, .8):
        query = QQmlExpression(qmlContext(feedback), feedback, f'weaveLevel({band}, 0.8)')
        level, undefined = query.evaluate()
        assert not query.hasError() and not undefined
        levels.append(level)
    assert 0 < levels[0] < levels[1] < levels[2]
    playback.setVolume(0)
    assert original == (timeline.width(), timeline.height(), editor.state.content, editor.historyUndoDepth)
    settings.setValue('feedbackLevel', 'off')
    assert not glass.isVisible()
    settings.cancelPreview()
    assert settings.values['glassWidth'] == 1


def test_glass_controls_apply_and_reopen(appearance_window, qtbot):
    root, settings = appearance_window
    dialog = root.findChild(QObject, 'studioAppearanceDialog')
    dialog.open()
    dialog.setProperty('page', 2)
    qtbot.waitUntil(lambda: dialog.property('opened'))
    root.grabWindow()
    button = visual_child(root.contentItem(), 'studioFeedbackStyle-glass')
    assert button is not None
    button.clicked.emit()
    assert settings.values['feedbackStyle'] == 'glass'
    for key, value in [('glassHue', .12), ('glassSaturation', .6), ('glassHeight', .3),
                       ('glassWidth', .6), ('glassPositionX', .2), ('glassPositionY', .8)]:
        slider = visual_child(root.contentItem(), key + 'Slider')
        assert slider is not None
        # StyledSlider uses the same preview path for pointer and keyboard input.
        assert QMetaObject.invokeMethod(slider, 'previewValue',
                                       Qt.DirectConnection, Q_ARG('QVariant', value))
        assert abs(settings.values[key] - value) < .001
    assert settings.applyPreview()
    reopened = StudioAppearance(settings._settings, settings._asset_home)
    assert reopened.values == settings.values
    settings.setValue('glassHeight', 2)
    assert settings.values['glassHeight'] == .4
    settings.setValue('glassWidth', -1)
    assert settings.values['glassWidth'] == .25
