"""Regression for the invalid focus wrapper observed during full B-03 checks."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from shiboken6 import delete

from ai_piano_studio.keyboard.controller import KeyboardController


def test_deleted_focus_object_is_not_queried(qapp, monkeypatch):
    focus = QObject()
    delete(focus)
    monkeypatch.setattr(QGuiApplication, 'focusObject', staticmethod(lambda: focus))
    assert KeyboardController._typing_focused() is False
