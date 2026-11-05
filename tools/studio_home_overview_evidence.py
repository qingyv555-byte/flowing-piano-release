"""Capture actual Qt home/overview layouts with a temporary profile and no audio."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.studio import build_studio_engine


def main():
    def forbid_audio(*args, **kwargs):
        raise AssertionError("Layout evidence must not open an audio device")

    PianoVoiceMixer.start_audio = forbid_audio
    PianoVoiceMixer.resume_audio = forbid_audio
    output = ROOT / "docs/rebuild/music-reactive/evidence"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    log = []
    with tempfile.TemporaryDirectory(prefix="studio-home-overview-") as temp:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(temp))
        root = engine.rootObjects()[0]
        playback.setVolume(0)

        def find(name):
            pending = [root.contentItem()]
            while pending:
                item = pending.pop()
                if item.objectName() == name:
                    return item
                pending.extend(item.childItems())
            raise AssertionError(name)

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled()
            QTest.mouseClick(root, Qt.LeftButton,
                            pos=item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())
            QTest.qWait(80)

        def shot(name, size):
            root.resize(*size)
            QTest.qWait(100)
            deadline = time.monotonic() + 6
            while find("feedbackHost").isVisible() and time.monotonic() < deadline:
                QTest.qWait(30)
            assert not find("feedbackHost").isVisible()
            path = output / (name + ".png")
            image = root.grabWindow()
            assert image.save(str(path))
            record = {"file": path.name, "logicalSize": list(size),
                      "captureSize": [image.width(), image.height()],
                      "dpr": root.devicePixelRatio(), "page": root.property("pageState"),
                      "overviewMode": engine.rootContext().contextProperty("experience").miniMapDisplayMode,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            if record["page"] == "launch":
                heading = find("studioProductHeading")
                position = heading.mapToScene(QPointF(0, 0))
                record["headingOrigin"] = [position.x(), position.y()]
            log.append(record)

        try:
            shot("home-empty-wide", (1440, 900))
            notes = tuple(Note.create(pitch=60 + index % 8, start_time=index * .5, duration=.3)
                          for index in range(32))
            for title in ("午后的练习", "雨滴与和弦", "写给明天", "窗边", "春日小节"):
                state = ProjectState(notes)
                editor._replace_project(state, saved_content=state.content, path=None)
                assert editor.saveNamedProject(title)
                deadline = time.monotonic() + 5
                while editor.isSaving and time.monotonic() < deadline:
                    QTest.qWait(10)
                assert not editor.isSaving
            editor.newProject()
            shot("home-recent-wide", (2200, 900))
            shot("home-recent-narrow", (1000, 640))
            appearance = engine.rootContext().contextProperty("studioAppearance")
            appearance.beginPreview()
            appearance.setValue("themeId", "paper")
            appearance.applyPreview()
            shot("home-recent-paper-narrow", (1000, 640))
            appearance.beginPreview()
            appearance.setValue("themeId", "ink")
            appearance.applyPreview()
            click("studioCreateButton")
            state = ProjectState(notes)
            editor._replace_project(state, saved_content=state.content, path=None)
            shot("overview-wide", (1440, 900))
            assert find("miniMap").isVisible()
            shot("overview-narrow", (1000, 640))
            click("studioViewMenuButton")
            menu = root.findChild(QObject, "studioViewMenu")
            expression = QQmlExpression(QQmlEngine.contextForObject(menu), menu, "itemAt(6)")
            item, undefined = expression.evaluate()
            assert not expression.hasError() and not undefined
            QTest.mouseClick(root, Qt.LeftButton,
                            pos=item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())
            shot("overview-hidden-narrow", (1000, 640))
            assert not find("miniMap").isVisible()
            assert engine.rootContext().contextProperty("templates")._preview_player is None
        finally:
            playback.stop()
            root.close()
            engine.deleteLater()
            app.processEvents()
    sources = ["src/ai_piano_studio/qml/StudioHome.qml", "src/ai_piano_studio/studio.py",
               "src/ai_piano_studio/qml/StudioCommandBar.qml", "tools/studio_home_overview_evidence.py"]
    (output / "home-overview-capture.json").write_text(json.dumps({
        "platform": "Qt offscreen software; no native input, audio device or real profile",
        "sourceSha256": {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in sources},
        "captures": log,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
