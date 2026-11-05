from pathlib import Path
from textwrap import dedent

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent


QML_DIR = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"


def load_inline_qml(qapp, source: str):
    engine = QQmlApplicationEngine()
    component = QQmlComponent(engine)
    base_url = QUrl.fromLocalFile(str(QML_DIR / "InlineTestHarness.qml"))
    component.setData(dedent(source).encode("utf-8"), base_url)
    qapp.processEvents()
    if component.isError():
        pytest.fail("\n".join(error.toString() for error in component.errors()))
    root = component.create()
    assert root is not None
    engine._inline_component = component
    engine._inline_root = root
    qapp.processEvents()
    return engine, root
