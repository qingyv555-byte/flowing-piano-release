"""Opt-in A/B music studies using the real Studio editor and its audio engine."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
from uuid import uuid4

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.logging_config import configure_logging
from ai_piano_studio.studio import build_studio_engine


def prepare_study(engine, editor, profile_home: Path, style: str) -> Path:
    """Give each launch a new editable copy; preserve all earlier studies."""
    if style not in {"string", "weave"}:
        raise ValueError("Unknown music study style")
    profile_home = profile_home.resolve()
    source = Path(__file__).parent / "resources/music-reactive-study.pianoai.json"
    study_home = profile_home / "Studies"
    study_home.mkdir(parents=True, exist_ok=True)
    target = study_home / f"{style}-{uuid4().hex[:10]}.pianoai.json"
    shutil.copyfile(source, target)
    appearance = engine.rootContext().contextProperty("studioAppearance")
    appearance.beginPreview()
    for key, value in {"feedbackStyle": style, "feedbackStrength": "balanced",
                       "feedbackLevel": "standard", "themeId": "ink"}.items():
        appearance.setValue(key, value)
    if not appearance.applyPreview():
        raise OSError(appearance.importError)
    editor.openProject(str(target))
    if editor.currentFilePath != str(target):
        raise OSError("Unable to load the study project")
    engine.rootObjects()[0].setProperty("pageState", "editor")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="flowing piano · 音乐反馈 A/B 样片")
    parser.add_argument("--style", choices=("string", "weave"), required=True)
    parser.add_argument("--profile-home", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication([])
    home = args.profile_home or (Path(QStandardPaths.writableLocation(
        QStandardPaths.GenericDataLocation)) / "Flowing Piano Music Studies" / args.style)
    configure_logging(home / "logs")
    app, engine, editor, playback, keyboard = build_studio_engine(home)
    initialize_deferred_services(engine)
    prepare_study(engine, editor, home, args.style)
    if args.smoke_test:
        QTimer.singleShot(0, app.quit)
    # Opening a study never auto-plays or changes the user's system volume.
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
