from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QWindow

from ai_piano_studio.__main__ import build_engine


ROOT = Path(__file__).parents[2]
QML_DIR = ROOT / "src" / "ai_piano_studio" / "qml"


def test_both_top_level_windows_use_the_shared_frameless_chrome(qapp) -> None:
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    assistant_source = (QML_DIR / "AssistantWindow.qml").read_text(encoding="utf-8")
    chrome_source = (QML_DIR / "WindowChrome.qml").read_text(encoding="utf-8")

    assert "WindowChrome" in main_source
    assert "WindowChrome" in assistant_source
    assert "Qt.FramelessWindowHint" in main_source
    assert "Qt.FramelessWindowHint" in assistant_source
    assert "startSystemMove" in chrome_source
    assert "startSystemResize" in chrome_source
    for object_name in (
        'objectName: "windowChrome"',
        'objectName: "windowChromeMinimizeButton"',
        'objectName: "windowChromeMaximizeButton"',
        'objectName: "windowChromeCloseButton"',
    ):
        assert object_name in chrome_source

    _app, engine, _editor, _playback, _keyboard = build_engine()
    main_window = engine.rootObjects()[0]
    assert bool(main_window.flags() & Qt.FramelessWindowHint)
    main_chrome = main_window.findChild(QObject, "windowChrome")
    assert main_chrome is not None
    assert main_chrome.property("visible") is True

    assistant = engine.rootContext().contextProperty("assistant")
    assistant.openWindow()
    qapp.processEvents()
    assistant_window = main_window.findChild(QWindow, "assistantWindow")
    assert assistant_window is not None
    assert bool(assistant_window.flags() & Qt.FramelessWindowHint)
    assistant_chrome = assistant_window.findChild(QObject, "windowChrome")
    assert assistant_chrome is not None
    assert assistant_chrome.property("visible") is True
    main_window.close()


def test_window_chrome_preserves_identity_and_native_interaction_contract() -> None:
    source = (QML_DIR / "WindowChrome.qml").read_text(encoding="utf-8")

    assert "productName" in source
    assert "contextText" in source
    assert "statusText" in source
    assert "showMinimized" in source
    assert "showMaximized" in source
    assert "showNormal" in source
    assert "close()" in source
    assert "onDoubleClicked" in source
    assert "Qt.LeftEdge" in source
    assert "Qt.RightEdge" in source
    assert "Qt.TopEdge" in source
    assert "Qt.BottomEdge" in source


def test_theme_uses_the_approved_cool_dark_release_palette(qapp) -> None:
    _app, engine, _editor, _playback, _keyboard = build_engine()
    root = engine.rootObjects()[0]
    theme = root.property("appTheme")

    expected = {
        "appBackground": "#07111e",
        "surface": "#0b1a2a",
        "borderStrong": "#16324a",
        "textPrimary": "#dce8f5",
        "textSecondary": "#8fa8bf",
        # Raised from the original draft token so muted desktop text remains
        # readable against the shipped dark surfaces.
        "textMuted": "#8299ae",
        "accentPrimary": "#49d7ff",
        "accentPrimaryHover": "#7be7ff",
        "success": "#3fd0a8",
        "warning": "#f2b84b",
        "danger": "#f06a7e",
        "keyWhite": "#c8d5e2",
    }
    for name, value in expected.items():
        assert theme.property(name) == QColor(value)
    assert theme.property("windowChromeHeight") >= 32
    root.close()


def test_qt_native_surface_palette_is_dark(qapp) -> None:
    _app, _engine, _editor, _playback, _keyboard = build_engine()

    assert qapp.palette().window().color().name() == "#07111e"
    assert qapp.palette().base().color().name() == "#0b1a2a"
    assert qapp.palette().button().color().name() == "#0e2032"
    # The offscreen platform reports Unknown even after an explicit dark
    # request, so the production call is asserted separately and palette
    # colors remain the runtime contract for Qt-owned surfaces.
    main_source = (QML_DIR.parent / "__main__.py").read_text(encoding="utf-8")
    assert "setColorScheme(Qt.ColorScheme.Dark)" in main_source
