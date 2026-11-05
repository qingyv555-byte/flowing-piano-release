from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from ai_piano_studio.music_agent.security import SecretScrubbingFilter


_qt_previous_handler = None


def default_log_directory() -> Path:
    return Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ) / "AI Piano Studio" / "logs"


def _qt_message_handler(message_type, context, message: str) -> None:
    level = {
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(message_type)
    if level is not None:
        logging.getLogger("qt").log(level, message)
    if _qt_previous_handler is not None:
        _qt_previous_handler(message_type, context, message)


def install_qt_message_logging() -> None:
    global _qt_previous_handler
    previous = qInstallMessageHandler(_qt_message_handler)
    if previous is not _qt_message_handler:
        _qt_previous_handler = previous


def configure_logging(log_dir: Path | None = None) -> Path:
    directory = log_dir or default_log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "app.log"

    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    scrubber = SecretScrubbingFilter()
    file_handler.addFilter(scrubber)
    root.addHandler(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(scrubber)
    root.addHandler(console)
    install_qt_message_logging()
    return log_path
