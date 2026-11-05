import gc
import logging
import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtQuickControls2 import QQuickStyle

import ai_piano_studio.editor.controller as editor_controller
from ai_piano_studio.persistence.project_library import ProjectLibrary


QQuickStyle.setStyle("Basic")


@pytest.fixture
def preserved_logging_handlers():
    """Logging configuration tests must not retain a closed pytest stream."""
    from PySide6.QtCore import qInstallMessageHandler
    import ai_piano_studio.logging_config as configuration
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    previous_qt = qInstallMessageHandler(None)
    qInstallMessageHandler(previous_qt)
    previous_chain = configuration._qt_previous_handler
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    qInstallMessageHandler(previous_qt)
    configuration._qt_previous_handler = previous_chain


@pytest.fixture(autouse=True)
def isolated_default_project_library(monkeypatch, tmp_path) -> None:
    """Keep default EditorController instances out of the user's library."""

    def create_test_library() -> ProjectLibrary:
        return ProjectLibrary(Path(tmp_path) / "default-projects")

    monkeypatch.setattr(editor_controller, "ProjectLibrary", create_test_library)


@pytest.fixture(autouse=True)
def settle_qt_deletions() -> None:
    """Drain Qt workers and deferred deletions before the next test starts."""
    yield
    assert QThreadPool.globalInstance().waitForDone(5000), (
        "Qt worker pool did not settle within 5 seconds"
    )
    application = QCoreApplication.instance()
    # Cyclic engine finalizers now queue native deletion. Collect before
    # draining it so previous test windows cannot survive into the next test.
    gc.collect()
    if application is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
    gc.collect()
