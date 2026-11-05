from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock

from PySide6.QtCore import QObject, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QWindow
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.settings import HarmonySettings
from tests.qt.test_studio_workflows import no_audio_device
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    ScoreBreakdown,
)


@dataclass
class _PendingAnalysis:
    request: AnalysisRequest
    release: Event
    completed: Event
    result: HarmonyAnalysisResult | None = None


class _ControlledService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: list[_PendingAnalysis] = []

    def analyze(self, request: AnalysisRequest) -> HarmonyAnalysisResult:
        pending = _PendingAnalysis(request, Event(), Event())
        with self._lock:
            self._pending.append(pending)
        try:
            pending.release.wait()
            assert pending.result is not None
            return pending.result
        finally:
            pending.completed.set()

    def count(self) -> int:
        with self._lock:
            return len(self._pending)

    def take(self, index: int) -> _PendingAnalysis:
        with self._lock:
            return self._pending[index]

    def release_all(self) -> None:
        with self._lock:
            pending = tuple(self._pending)
        for item in pending:
            if item.result is None:
                item.result = _result_for(item.request)
            item.release.set()


def _result_for(request: AnalysisRequest) -> HarmonyAnalysisResult:
    return HarmonyAnalysisResult(
        project_identity=request.project_identity,
        project_revision=request.project_revision,
        generation_id=request.generation_id,
        mode=request.mode,
        key_estimate=KeyEstimate(None, None, None, 0.0, True, False),
        suggestions=(),
    )


def _preview_result_for(request: AnalysisRequest) -> HarmonyAnalysisResult:
    candidate = ChordCandidate(
        chord_symbol="C",
        quality="major",
        diatonic_degree=1,
        pitches=(60, 64, 67),
        velocity=72,
        score=ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.9),
    )
    return HarmonyAnalysisResult(
        project_identity=request.project_identity,
        project_revision=request.project_revision,
        generation_id=request.generation_id,
        mode=request.mode,
        key_estimate=KeyEstimate(0, "C", "major", 0.9, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=0,
                start_tick=0,
                duration_ticks=1_920,
                temporary=False,
                candidates=(candidate,),
            ),
        ),
    )


def _wait_for_request(qtbot, service: _ControlledService, index: int) -> _PendingAnalysis:
    qtbot.waitUntil(lambda: service.count() > index, timeout=2_000)
    return service.take(index)


def _wait_for_receiver(qtbot, harmony, pending: _PendingAnalysis) -> None:
    qtbot.waitUntil(pending.completed.is_set)
    qtbot.waitUntil(
        lambda: not any(
            job._request is pending.request for job in harmony._jobs.values()
        )
    )


def _visual_item(root_item: QQuickItem, object_name: str) -> QQuickItem:
    pending = [root_item]
    while pending:
        item = pending.pop()
        if item.objectName() == object_name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"missing visual item: {object_name}")


def _window_point(root, item: QQuickItem) -> QPoint:
    point = item.mapToItem(
        root.contentItem(), QPointF(item.width() / 2, item.height() / 2)
    )
    return QPoint(round(point.x()), round(point.y()))


def _relative_luminance(color: QColor) -> float:
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(foreground: QColor, background: QColor) -> float:
    light, dark = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (light + 0.05) / (dark + 0.05)


def _load_help_shell(qapp, tmp_path):
    note = Note.create(id="help-note", pitch=64, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({note.id})))
    _, engine, editor, playback, keyboard = build_engine(
        controller=editor,
        harmony_settings=HarmonySettings(
            settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
        ),
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        ),
    )
    root = engine.rootObjects()[0]
    root.setProperty("width", 1920)
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    return engine, root, editor, playback, keyboard


def test_help_route_keeps_editor_instance_but_hides_and_disables_it(qapp, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    loader = root.findChild(QObject, "editorShellLoader")
    shell = root.findChild(QObject, "editorShell")
    help_button = root.findChild(QObject, "harmonyHelpButton")
    assert loader is not None and shell is not None and help_button is not None

    help_button.clicked.emit()
    qapp.processEvents()

    assert root.property("pageState") == "harmonyHelp"
    assert loader.property("active") is True
    assert shell.property("visible") is False
    assert shell.property("enabled") is False
    assert root.findChild(QObject, "harmonyHelpViewer").property("visible") is True
    assert root.property("editorInputEnabled") is False
    root.close()


def test_help_round_trip_preserves_editor_and_preview_state_and_restores_focus(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    timeline = root.findChild(QObject, "timeline")
    inspector = root.findChild(QObject, "inspector")
    help_button = root.findChild(QObject, "harmonyHelpButton")
    harmony = engine.rootContext().contextProperty("harmony")
    help_content = engine.rootContext().contextProperty("harmonyHelp")
    service = _ControlledService()
    assert inspector is not None
    harmony._service = service
    try:
        harmony.setMode("preview")
        pending = _wait_for_request(qtbot, service, 0)
        pending.result = _preview_result_for(pending.request)
        pending.release.set()
        _wait_for_receiver(qtbot, harmony, pending)
        assert harmony.previewModel.count == 1

        inspector.setProperty("collapsed", True)
        qapp.processEvents()
        assert root.property("inspectorCollapsed") is True
        assert timeline.property("dockMiniMapToBottomRight") is True
        timeline.setProperty("viewStartSeconds", 1.25)
        timeline.setProperty("pixelsPerSecond", 96.0)
        playback.seek(0.25)
        before = (
            editor.state.content,
            editor.isDirty,
            harmony.latestResult,
            harmony.previewModel.count,
        )

        mode_button = root.findChild(QQuickItem, "moreMenuButton")
        mode_action = root.findChild(QObject, "moreHarmonyAction")
        popup = root.findChild(QObject, "harmonyModePopup")
        assert mode_button is not None and mode_action is not None and popup is not None
        mode_button.forceActiveFocus()
        mode_action.triggered.emit()
        qapp.processEvents()
        assert popup.property("opened") is True

        help_button.clicked.emit()
        qapp.processEvents()
        viewer = root.findChild(QObject, "harmonyHelpViewer")
        viewer.closeRequested.emit()
        qapp.processEvents()

        assert root.property("pageState") == "editor"
        assert (
            editor.state.content,
            editor.isDirty,
            harmony.latestResult,
            harmony.previewModel.count,
        ) == before
        assert timeline.property("viewStartSeconds") == 1.25
        assert timeline.property("pixelsPerSecond") == 96.0
        assert playback.positionSeconds == 0.25
        assert help_content.currentSectionId == "harmony-basics"
        assert mode_button.property("activeFocus") is True
        assert inspector.property("collapsed") is True
        assert root.property("inspectorCollapsed") is True
        assert timeline.property("dockMiniMapToBottomRight") is True
    finally:
        service.release_all()
        root.close()


def test_opening_help_moves_focus_to_its_close_button(qapp, qtbot, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    help_button = root.findChild(QObject, "harmonyHelpButton")
    close_button = root.findChild(QObject, "harmonyHelpCloseButton")
    assert help_button is not None and close_button is not None
    try:
        help_button.clicked.emit()

        qtbot.waitUntil(
            lambda: close_button.property("activeFocus") is True,
            timeout=1_000,
        )
    finally:
        root.close()


def test_opening_help_stops_live_inputs_without_changing_project(qapp, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    help_button = root.findChild(QObject, "harmonyHelpButton")
    content_before = editor.state.content
    keyboard.startRecording()
    assert keyboard.recording is True

    help_button.clicked.emit()
    qapp.processEvents()

    assert keyboard.recording is False
    assert playback.playing is False
    assert editor.state.content == content_before
    root.close()


def test_leaving_editor_route_invalidates_slow_harmony_work(qapp, qtbot, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    harmony = engine.rootContext().contextProperty("harmony")
    service = _ControlledService()
    harmony._service = service
    accepted: list[HarmonyAnalysisResult] = []
    harmony.analysisAccepted.connect(accepted.append)
    try:
        harmony.setMode("preview")
        pending = _wait_for_request(qtbot, service, 0)
        generation = harmony.analysisGeneration

        root.setPage("launch", False)
        qapp.processEvents()

        assert root.property("pageState") == "launch"
        assert harmony.analysisGeneration == generation + 1
        assert harmony.analysisPending is False

        pending.result = _result_for(pending.request)
        pending.release.set()
        _wait_for_receiver(qtbot, harmony, pending)
        assert accepted == []
        assert harmony.latestResult is None
        assert harmony.previewModel.count == 0
    finally:
        service.release_all()
        root.close()


def test_actual_window_deactivation_invalidates_slow_harmony_work_but_keeps_preview(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    harmony = engine.rootContext().contextProperty("harmony")
    service = _ControlledService()
    harmony._service = service
    accepted: list[HarmonyAnalysisResult] = []
    harmony.analysisAccepted.connect(accepted.append)
    other_window = QWindow()
    try:
        harmony.setMode("preview")
        accepted_pending = _wait_for_request(qtbot, service, 0)
        accepted_pending.result = _result_for(accepted_pending.request)
        accepted_pending.release.set()
        _wait_for_receiver(qtbot, harmony, accepted_pending)
        assert accepted == [accepted_pending.result]

        harmony.requestAnalysis()
        stale = _wait_for_request(qtbot, service, 1)
        generation = harmony.analysisGeneration
        root.requestActivate()
        qtbot.waitUntil(lambda: root.property("active") is True)
        other_window.show()
        other_window.requestActivate()
        qtbot.waitUntil(lambda: root.property("active") is False)

        assert harmony.analysisGeneration == generation + 1
        assert harmony.latestResult is accepted_pending.result
        assert harmony.previewModel.count == 0

        stale.result = _result_for(stale.request)
        stale.release.set()
        _wait_for_receiver(qtbot, harmony, stale)
        assert accepted == [accepted_pending.result]
        assert harmony.latestResult is accepted_pending.result
    finally:
        service.release_all()
        other_window.close()
        other_window.deleteLater()
        root.close()


def test_scrolling_help_article_updates_the_toc_highlight(qapp, qtbot, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    help_button = root.findChild(QObject, "harmonyHelpButton")
    help_content = engine.rootContext().contextProperty("harmonyHelp")
    help_button.clicked.emit()
    qapp.processEvents()
    article = root.findChild(QQuickItem, "harmonyHelpArticle")
    section_list = root.findChild(QQuickItem, "harmonyHelpSectionList")
    assert article is not None and section_list is not None
    article.positionViewAtIndex(9, 0)
    section_list.positionViewAtIndex(9, 0)
    qapp.processEvents()

    qtbot.waitUntil(lambda: help_content.currentSectionId == "dynamics-register")
    toc = _visual_item(section_list, "harmonyHelpToc-dynamics-register")
    assert toc.property("highlighted") is True
    root.close()


def test_selected_help_toc_item_uses_readable_theme_colors(qapp, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    help_content = engine.rootContext().contextProperty("harmonyHelp")
    root.findChild(QObject, "harmonyHelpButton").clicked.emit()
    qapp.processEvents()
    section_list = root.findChild(QQuickItem, "harmonyHelpSectionList")
    assert section_list is not None
    selected = _visual_item(section_list, "harmonyHelpToc-harmony-basics")
    text = _visual_item(selected, "harmonyHelpTocText-harmony-basics")
    background = _visual_item(selected, "harmonyHelpTocBackground-harmony-basics")

    assert selected.property("highlighted") is True
    assert _contrast(
        QColor(text.property("color")), QColor(background.property("color"))
    ) >= 4.5

    focused = _visual_item(section_list, "harmonyHelpToc-chord-selection")
    focused_background = _visual_item(
        focused, "harmonyHelpTocBackground-chord-selection"
    )
    focused.forceActiveFocus()
    qapp.processEvents()

    assert focused.property("activeFocus") is True
    assert help_content.currentSectionId == "harmony-basics"
    assert selected.property("highlighted") is True
    assert QColor(focused_background.property("borderColor")) == QColor("#7BE7FF")
    root.close()


def test_toc_click_scrolls_the_continuous_help_article_to_its_section(
    qapp, qtbot, tmp_path
) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    help_button = root.findChild(QObject, "harmonyHelpButton")
    help_content = engine.rootContext().contextProperty("harmonyHelp")
    help_button.clicked.emit()
    qapp.processEvents()
    article = root.findChild(QQuickItem, "harmonyHelpArticle")
    section_list = root.findChild(QQuickItem, "harmonyHelpSectionList")
    assert article is not None and section_list is not None
    section_list.positionViewAtIndex(4, 0)
    qapp.processEvents()
    toc = _visual_item(section_list, "harmonyHelpToc-chord-selection")

    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _window_point(root, toc),
    )
    qapp.processEvents()

    qtbot.waitUntil(lambda: help_content.currentSectionId == "chord-selection")
    target = _visual_item(article, "harmonyHelpArticleSection-chord-selection")
    qtbot.waitUntil(lambda: article.property("contentY") >= target.y() - 1)
    assert article.property("contentY") == target.y()
    root.close()


def test_why_action_opens_the_chord_selection_section(qapp, tmp_path) -> None:
    engine, root, editor, playback, keyboard = _load_help_shell(qapp, tmp_path)
    harmony = engine.rootContext().contextProperty("harmony")
    help_content = engine.rootContext().contextProperty("harmonyHelp")

    harmony.requestExplanation(0)
    qapp.processEvents()

    assert root.property("pageState") == "harmonyHelp"
    assert help_content.currentSectionId == "chord-selection"
    root.close()
