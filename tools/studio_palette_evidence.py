"""Short, silent offscreen capture of the actual editor and AI surfaces."""
import json
from pathlib import Path
import tempfile
import time

from studio_evidence import ROOT, install_memory_output
from PySide6.QtCore import QObject, QMetaObject
from PySide6.QtGui import QFontDatabase, QColor, QImage, QPainter, QLinearGradient, QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.studio import build_studio_engine


def main():
    blocked = install_memory_output()
    output = ROOT / "docs/rebuild/home-field/evidence/palette"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/georgia.ttf")
    with tempfile.TemporaryDirectory(prefix="flowing-palette-") as temporary:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(temporary))
        root = engine.rootObjects()[0]
        root.resize(1920, 1080)
        initialize_deferred_services(engine)
        appearance = engine.rootContext().contextProperty("studioAppearance")

        def shot(window, name):
            QTest.qWait(100)
            assert window.grabWindow().save(str(output / (name + ".png")))

        shot(root, "home")
        editor.openProject(str(ROOT / "src/ai_piano_studio/resources/music-reactive-study.pianoai.json"))
        root.setProperty("pageState", "editor")
        shot(root, "editor")
        QMetaObject.invokeMethod(root, "openAssistantWindow")
        QTest.qWait(100)
        assistant = root.findChild(QWindow, "assistantWindow")
        assert assistant and assistant.isVisible()
        shot(assistant, "assistant")
        settings_dialog = assistant.findChild(QObject, "assistantSettingsDialog")
        QMetaObject.invokeMethod(settings_dialog, "open")
        shot(assistant, "assistant-settings")
        QMetaObject.invokeMethod(settings_dialog, "close")
        assistant.close()
        appearance.beginPreview()
        # A clearly identified color fixture, not a generated design or music.
        source = Path(temporary) / "warm-gradient.png"
        image = QImage(1280, 720, QImage.Format_RGB32)
        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, 1280, 720)
        gradient.setColorAt(0, QColor("#2f2024"))
        gradient.setColorAt(.55, QColor("#b65e47"))
        gradient.setColorAt(1, QColor("#e3bf8d"))
        painter.fillRect(image.rect(), gradient)
        painter.end()
        image.save(str(source))
        appearance.importBackground("editor", str(source))
        deadline = time.monotonic() + 5
        while appearance.importBusy and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not appearance.importError and not appearance.importBusy
        appearance.setValue("editorOpacity", 1)
        appearance.setValue("editorDim", 0)
        appearance.setValue("themeImageTarget", "editor")
        appearance.setValue("adaptiveTheme", True)
        while appearance.themeSeed.alpha() == 0 and time.monotonic() < deadline:
            QTest.qWait(10)
        assert appearance.themeSeed.alpha() > 0
        shot(root, "editor-image-adapted")
        QMetaObject.invokeMethod(root, "openAssistantWindow")
        shot(assistant, "assistant-image-adapted")
        assistant.close()
        appearance.cancelPreview()
        root.close()
        engine.deleteLater()
        app.processEvents()
    assert blocked == []
    (output / "capture.json").write_text(json.dumps({
        "kind": "Actual Qt offscreen software rendering",
        "hardwareAudioCalls": blocked, "musicPlayed": False,
        "customBackground": "Locally constructed warm gradient for palette verification",
        "nativeDesktopAcceptance": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
