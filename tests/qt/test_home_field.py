from pathlib import Path
from PySide6.QtCore import QObject
from PySide6.QtGui import QFontDatabase
from tests.qt.test_studio_shell import studio_session, click
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_appearance import png, gif


def test_home_sizes_and_ambient_shutdown(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/georgia.ttf')
    settings = engine.rootContext().contextProperty('studioAppearance')
    home = root.findChild(QObject, 'studioHome')
    timer = home.findChild(QObject, 'studioHomeAmbientTimer')
    field = home.findChild(QObject, 'studioHomeField')
    output = Path(__file__).parents[2] / 'docs/rebuild/home-field/evidence'
    output.mkdir(parents=True, exist_ok=True)
    for width, height in ((1920, 1080), (1366, 768)):
        root.resize(width, height)
        qtbot.waitUntil(lambda: home.width() == width)
        qtbot.wait(120)  # Settle layout and the first Canvas paint before evidence capture.
        qtbot.waitUntil(lambda: field.width() == home.width())
        for name in ('studioCreateButton', 'studioTemplatesButton', 'studioLibraryButton', 'studioImportButton'):
            button = home.findChild(QObject, name)
            assert button.isVisible() and button.isEnabled()
            pos = button.mapToScene(button.boundingRect().center())
            assert 0 <= pos.x() < width and 0 <= pos.y() < height
        assert home.height() <= height
        assert root.grabWindow().save(str(output / f'home-{width}x{height}.png'))
    assert timer.property('running')
    settings.setValue('homeAmbient', False)
    assert not timer.property('running')
    settings.setValue('homeAmbient', True)
    settings.setValue('reducedMotion', True)
    assert not timer.property('running')
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    assert root.findChild(QObject, 'studioHome') is None


def test_home_image_and_gif_replace_field_with_preview_rollback(studio_session, tmp_path, qtbot):
    app, engine, root, editor, playback = studio_session
    settings = engine.rootContext().contextProperty('studioAppearance')
    home = root.findChild(QObject, 'studioHome')
    field = home.findChild(QObject, 'studioHomeField')
    settings.beginPreview()
    settings.importBackground('home', str(png(tmp_path / 'home.png')))
    qtbot.waitUntil(lambda: not settings.importBusy)
    assert not settings.importError and not field.isVisible()
    qtbot.waitUntil(lambda: home.findChild(QObject, 'studioBackgroundImage') is not None)
    settings.cancelPreview()
    assert field.isVisible()
    settings.beginPreview()
    settings.importBackground('home', str(gif(tmp_path / 'home.gif')))
    qtbot.waitUntil(lambda: not settings.importBusy)
    qtbot.waitUntil(lambda: home.findChild(QObject, 'studioBackgroundAnimation') is not None)
    animation = home.findChild(QObject, 'studioBackgroundAnimation')
    qtbot.waitUntil(lambda: animation.property('frameCount') == 2)
    first = animation.property('currentFrame')
    qtbot.waitUntil(lambda: animation.property('currentFrame') != first)
    settings.setValue('homeAmbient', False)
    qtbot.waitUntil(lambda: home.findChild(QObject, 'studioBackgroundStill') is not None)
    assert not field.isVisible()
    assert settings.applyPreview()
    assert settings.values['homeSource'].endswith('.gif')
