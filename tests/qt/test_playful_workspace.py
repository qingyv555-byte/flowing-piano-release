"""Beginner workflows: truthful feedback and controls reachable on small windows."""

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.domain.note import Note
from tests.qt.qml_test_utils import load_inline_qml
from tests.qt.test_qml_smoke import load_shell, DeterministicPlaybackPlayer


def test_checked_button_and_optional_field_error_have_visible_feedback(qapp):
    engine, root = load_inline_qml(qapp, '''
        import QtQuick
        import "."
        Item {
            Theme { id: theme }
            AppButton { objectName: "toggle"; theme: theme; checkable: true; text: "吸附" }
            AppField { objectName: "field"; theme: theme; label: "持续几拍" }
        }
    ''')
    toggle = root.findChild(QObject, "toggle")
    background = toggle.findChild(QObject, "buttonBackground")
    before = background.property("color")
    toggle.setProperty("checked", True)
    qapp.processEvents()
    assert background.property("color") != before
    toggle.setProperty("checked", False)
    qapp.processEvents()
    assert background.property("color") == before
    field = root.findChild(QObject, "field")
    normal_height = field.property("implicitHeight")
    field.setProperty("errorText", "请输入拍数")
    assert field.property("implicitHeight") > normal_height
    field.setProperty("errorText", "")
    assert field.property("implicitHeight") == normal_height


@pytest.mark.parametrize("width,height", [(1280, 720), (1920, 1080)])
def test_stage_popovers_fit_close_and_return_space_to_playback(qapp, width, height):
    player = DeterministicPlaybackPlayer()
    notes = tuple(Note.create(id=f"stage-{i}", pitch=[60, 64, 67, 72, 69, 67][i % 6],
                              start_time=i * 0.5, duration=0.4) for i in range(24))
    engine, root, controller = load_shell(qapp, notes, player=player,
                                          selected_ids={"stage-2", "stage-3"})
    root.setWidth(width)
    root.setHeight(height)
    QTest.qWait(100)
    toolbar = root.findChild(QQuickItem, "commandBar")
    for name in ("moreMenuButton", "assistantButton", "saveButton",
                 "importButton", "exportButton"):
        item = root.findChild(QQuickItem, name)
        position = item.mapToScene(QPointF(0, 0))
        assert item.isVisible()
        assert position.x() >= 0
        assert position.x() + item.width() <= root.width()
    output = Path(".pytest-tmp/ui-refresh")
    output.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(output / f"stage-{width}.png"))
    background_action = root.findChild(QObject, "moreBackgroundSettingsAction")
    background_action.triggered.emit()
    QTest.qWait(250)
    popup = root.findChild(QObject, "backgroundSettingsDialog")
    assert popup.property("opened")
    assert popup.property("width") <= toolbar.width()
    assert root.grabWindow().save(str(output / f"background-settings-{width}.png"))
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(250)
    assert not popup.property("opened")
    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert engine.rootContext().contextProperty("playback").playing
    root.close()


def test_import_collapsing_controls_releases_space_and_proposal_hides_zeroes(qapp, qtbot):
    from ai_piano_studio.__main__ import build_engine

    app, engine, controller, playback, keyboard = build_engine()
    root = engine.rootObjects()[0]
    root.setProperty("audioImportDialogRequested", True)
    qapp.processEvents()
    dialog = root.findChild(QObject, "audioImportDialog")
    dialog.setProperty("visible", True)
    qapp.processEvents()
    compact_height = dialog.property("height")
    before = controller.state
    toggle = dialog.findChild(QObject, "audioImportAdvancedToggle")
    toggle.clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(lambda: dialog.property("height") > compact_height)
    toggle.clicked.emit()
    qapp.processEvents()
    qtbot.waitUntil(lambda: dialog.property("height") == compact_height)
    assert controller.state == before
    dialog.close()

    engine.rootContext().contextProperty("assistant").openWindow()
    qapp.processEvents()
    proposal = root.findChild(QObject, "assistantProposalChangesDialog")
    proposal.setProperty("inspector", {
        "available": True, "summary": {"notesAdded": 2}, "bpm": {"changed": False}
    })
    qapp.processEvents()
    items = proposal.findChild(QObject, "assistantProposalSummaryItems")
    assert items.property("count") == 1
    proposal.setProperty("showAllMetrics", True)
    qapp.processEvents()
    assert items.property("count") == 8
    assert controller.state == before
    root.close()
