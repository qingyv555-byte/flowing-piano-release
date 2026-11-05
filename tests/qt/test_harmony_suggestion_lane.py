from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtQml import QQmlProperty
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.suggestion_service import measure_ticks
from ai_piano_studio.harmony.types import (
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    ScoreBreakdown,
)


QML_DIR = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"


def _visual_item(root_item: QQuickItem, object_name: str) -> QQuickItem:
    pending = [root_item]
    while pending:
        item = pending.pop()
        if item.objectName() == object_name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"missing visual item: {object_name}")


def _accessible_name(item: QQuickItem) -> str:
    interface = QAccessible.queryAccessibleInterface(item)
    assert interface is not None
    return interface.text(QAccessible.Text.Name)


def _window_point(root, item: QQuickItem) -> QPoint:
    point = item.mapToItem(
        root.contentItem(), QPointF(item.width() / 2, item.height() / 2)
    )
    return QPoint(round(point.x()), round(point.y()))


def _candidate(symbol: str, quality: str, pitches: tuple[int, int, int]) -> ChordCandidate:
    return ChordCandidate(
        chord_symbol=symbol,
        quality=quality,
        diatonic_degree=1,
        pitches=pitches,
        velocity=72,
        score=ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.9),
    )


def _result(
    project_identity: str,
    *,
    temporary: bool = False,
    duration_ticks: int = 1_920,
    candidate_count: int = 3,
) -> HarmonyAnalysisResult:
    candidates = (
        _candidate("C", "major", (60, 64, 67)),
        _candidate("Am", "minor", (57, 60, 64)),
        _candidate("F", "major", (53, 57, 60)),
    )
    return HarmonyAnalysisResult(
        project_identity=project_identity,
        project_revision=1,
        generation_id=1,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.9, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=0,
                start_tick=0,
                duration_ticks=duration_ticks,
                temporary=temporary,
                candidates=candidates[:candidate_count],
            ),
        ),
    )


def _load_lane(
    qapp,
    tmp_path,
    *,
    temporary: bool = False,
    tempo: TempoMap | None = None,
    duration_ticks: int = 1_920,
    candidate_count: int = 3,
):
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    initial_controller = (
        EditorController(ProjectState(tempo=tempo)) if tempo is not None else None
    )
    _, engine, controller, playback, keyboard = build_engine(
        controller=initial_controller,
        harmony_settings=settings,
        experience_settings=ExperienceSettings(settings=QSettings(str(tmp_path / "experience.ini"), QSettings.IniFormat)),
    )
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    harmony = engine.rootContext().contextProperty("harmony")
    harmony.previewModel.apply_result(
        _result(
            harmony._project_identity,
            temporary=temporary,
            duration_ticks=duration_ticks,
            candidate_count=candidate_count,
        )
    )
    qapp.processEvents()
    lane = root.findChild(QQuickItem, "harmonySuggestionLane")
    assert lane is not None
    return engine, root, lane, harmony, playback, controller, keyboard


def _popup_for(item: QQuickItem) -> QObject:
    for child in item.findChildren(QObject):
        if child.property("opened") is not None:
            return child
    raise AssertionError("missing popup child")


def test_lane_component_declares_preview_only_geometry_virtualization_and_actions() -> None:
    source = (QML_DIR / "HarmonySuggestionLane.qml").read_text(encoding="utf-8")

    for declaration in (
        "required property var theme",
        "required property var model",
        "required property real viewStartSeconds",
        "required property real pixelsPerSecond",
        "required property real keyWidth",
        "required property real playheadSeconds",
        "required property string statusText",
        "required property bool analysisPending",
        "signal candidateRequested(int measureIndex, int candidateIndex)",
        "signal addRequested(int measureIndex)",
        "signal dismissRequested(int measureIndex)",
        "signal whyRequested(int measureIndex)",
        "signal regenerateRequested(int measureIndex)",
    ):
        assert declaration in source
    assert "height: 60" in source
    assert "&& model && model.count > 0" not in source
    assert "startSeconds + durationSeconds > root.viewStartSeconds" in source
    assert "startSeconds < root.viewStartSeconds + root.visibleSeconds" in source
    assert "indices.length < 2" in source


def test_empty_preview_lane_announces_pending_and_controller_status(qapp, tmp_path) -> None:
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    _, engine, controller, playback, keyboard = build_engine(harmony_settings=settings)
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    harmony = engine.rootContext().contextProperty("harmony")
    try:
        qapp.processEvents()
        empty_state = _visual_item(root.contentItem(), "harmonySuggestionEmptyState")
        assert empty_state.property("visible") is True
        assert empty_state.property("text") == "先画几个旋律音符，我才能推荐和弦。"
        assert _accessible_name(empty_state) == "先画几个旋律音符，我才能推荐和弦。"

        harmony._set_analysis_pending(True)
        qapp.processEvents()
        assert empty_state.property("text") == "正在听你的旋律…"

        harmony._set_analysis_pending(False)
        harmony._set_harmony_status_text("整首已分析，没有覆盖已有和弦。")
        qapp.processEvents()
        assert empty_state.property("text") == "整首已分析，没有覆盖已有和弦。"
    finally:
        root.close()
        harmony.close()


def test_lane_is_preview_only_fixed_height_and_tiles_follow_timeline_transform(qapp, tmp_path) -> None:
    engine, root, lane, harmony, _, controller, keyboard = _load_lane(qapp, tmp_path)
    timeline = root.findChild(QQuickItem, "timeline")

    assert lane.property("visible") is True
    assert lane.property("height") == pytest.approx(60)
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    assert tile.property("x") == pytest.approx(timeline.property("keyWidth"))
    assert tile.property("width") == pytest.approx(160)

    timeline.setProperty("viewStartSeconds", 0.5)
    timeline.setProperty("pixelsPerSecond", 100.0)
    qapp.processEvents()
    assert tile.property("x") == pytest.approx(timeline.property("keyWidth") - 50)
    assert tile.property("width") == pytest.approx(200)

    harmony.setMode("off")
    qapp.processEvents()
    assert lane.property("visible") is False
    root.close()


def test_lane_collapses_to_primary_but_expands_for_playhead_with_at_most_two_alternatives(qapp, tmp_path) -> None:
    engine, root, lane, _, playback, controller, keyboard = _load_lane(qapp, tmp_path, temporary=True)
    tile = _visual_item(lane, "harmonySuggestionTile-0")

    lane.setProperty("playheadSeconds", 3.0)
    qapp.processEvents()
    assert tile.property("expanded") is False
    assert tile.property("alternativeCount") == 0
    _visual_item(lane, "harmonyTemporaryBadge-0")

    lane.setProperty("playheadSeconds", 0.5)
    qapp.processEvents()
    assert tile.property("expanded") is True
    assert tile.property("alternativeCount") == 2
    _visual_item(lane, "harmonyCandidate-0-1")
    _visual_item(lane, "harmonyCandidate-0-2")
    root.close()


def test_lane_alternatives_exclude_the_selected_candidate(qapp, tmp_path) -> None:
    engine, root, lane, harmony, _, controller, keyboard = _load_lane(qapp, tmp_path)
    lane.setProperty("playheadSeconds", 0.5)
    harmony.previewModel.selectCandidate(0, 1)
    qapp.processEvents()

    _visual_item(lane, "harmonyCandidate-0-0")
    _visual_item(lane, "harmonyCandidate-0-2")
    with pytest.raises(AssertionError):
        _visual_item(lane, "harmonyCandidate-0-1")
    root.close()


def test_narrow_expanded_tile_keeps_its_action_control_inside_its_bounds(qapp, tmp_path) -> None:
    engine, root, lane, _, _, controller, keyboard = _load_lane(qapp, tmp_path)
    lane.setProperty("playheadSeconds", 0.5)
    qapp.processEvents()
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    actions = _visual_item(lane, "harmonyActionsMenu-0")

    assert tile.property("compactActions") is True
    assert actions.x() >= 0
    assert actions.x() + actions.width() <= tile.width()
    root.close()


def test_mouse_opens_action_menu_for_six_eight_at_minimum_zoom_and_maximum_bpm(
    qapp, tmp_path
) -> None:
    tempo = TempoMap(bpm=300, beats_per_bar=6, beat_unit=8)
    engine, root, lane, _, _, controller, keyboard = _load_lane(
        qapp,
        tmp_path,
        tempo=tempo,
        duration_ticks=measure_ticks(tempo),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    timeline.setProperty("pixelsPerSecond", 30.0)
    lane.setProperty("playheadSeconds", 0.1)
    qapp.processEvents()
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    actions = _visual_item(lane, "harmonyActionsMenu-0")
    popup = _popup_for(actions)

    assert timeline.property("barWidth") == pytest.approx(18.0)
    assert popup.property("opened") is False
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _window_point(root, actions),
    )
    qapp.processEvents()

    assert popup.property("opened") is True
    assert actions.x() >= 0
    assert actions.x() + actions.width() <= tile.width()
    root.close()


def test_max_zoom_measure_uses_compact_menu_when_direct_actions_would_clip(
    qapp, tmp_path
) -> None:
    tempo = TempoMap(bpm=120, beats_per_bar=4, beat_unit=4)
    engine, root, lane, _, _, controller, keyboard = _load_lane(
        qapp,
        tmp_path,
        tempo=tempo,
        duration_ticks=measure_ticks(tempo),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    timeline.setProperty("pixelsPerSecond", 320.0)
    lane.setProperty("playheadSeconds", 0.1)
    qapp.processEvents()
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    actions = _visual_item(lane, "harmonyActionsMenu-0")
    popup = _popup_for(actions)

    assert timeline.property("barWidth") == pytest.approx(640.0)
    assert tile.property("directActionsRequiredWidth") > tile.property("timelineWidth")
    assert tile.property("compactActions") is True
    assert actions.property("visible") is True
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _window_point(root, actions),
    )
    qapp.processEvents()

    assert popup.property("opened") is True
    for object_name in (
        "harmonyCandidateMenu-0-1",
        "harmonyCandidateMenu-0-2",
        "harmonyApplyMenu-0",
        "harmonyRegenerateMenu-0",
        "harmonyWhyMenu-0",
        "harmonyDismissMenu-0",
    ):
        item = popup.findChild(QObject, object_name)
        assert item is not None
        assert item.property("visible") is True
    root.close()


def test_measure_uses_direct_actions_when_the_measured_controls_fit(qapp, tmp_path) -> None:
    tempo = TempoMap(bpm=120, beats_per_bar=4, beat_unit=4)
    engine, root, lane, _, _, controller, keyboard = _load_lane(
        qapp,
        tmp_path,
        tempo=tempo,
        duration_ticks=measure_ticks(tempo),
        candidate_count=1,
    )
    timeline = root.findChild(QQuickItem, "timeline")
    timeline.setProperty("pixelsPerSecond", 320.0)
    lane.setProperty("playheadSeconds", 0.1)
    qapp.processEvents()
    tile = _visual_item(lane, "harmonySuggestionTile-0")

    assert tile.property("timelineWidth") == pytest.approx(640.0)
    assert tile.property("directActionsRequiredWidth") < tile.property("timelineWidth")
    assert tile.property("compactActions") is False
    _visual_item(lane, "harmonyAdd-0")
    root.close()


def test_sufficiently_wide_measure_shows_complete_direct_action_row(qapp, tmp_path) -> None:
    tempo = TempoMap(bpm=60, beats_per_bar=4, beat_unit=4)
    engine, root, lane, _, _, controller, keyboard = _load_lane(
        qapp,
        tmp_path,
        tempo=tempo,
        duration_ticks=measure_ticks(tempo),
    )
    timeline = root.findChild(QQuickItem, "timeline")
    timeline.setProperty("pixelsPerSecond", 320.0)
    lane.setProperty("playheadSeconds", 0.1)
    qapp.processEvents()
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    dismiss = _visual_item(lane, "harmonyDismiss-0")
    dismiss_left = dismiss.mapToItem(tile, QPointF(0, 0)).x()
    dismiss_right = dismiss.mapToItem(tile, QPointF(dismiss.width(), 0)).x()

    assert timeline.property("barWidth") == pytest.approx(1_280.0)
    assert tile.property("compactActions") is False
    assert dismiss.property("visible") is True
    assert dismiss_left >= 0
    assert dismiss_right <= tile.width()
    root.close()


def test_keyboard_tab_focuses_collapsed_summary_without_expanding(qapp, tmp_path) -> None:
    engine, root, lane, _, _, controller, keyboard = _load_lane(qapp, tmp_path)
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    lane.setProperty("playheadSeconds", 3.0)
    previous = tile.nextItemInFocusChain(False)
    previous.forceActiveFocus()

    QTest.keyClick(root, Qt.Key_Tab)
    qapp.processEvents()

    assert root.property("activeFocusItem") == tile
    assert tile.property("activeFocus") is True
    assert tile.property("expanded") is False
    assert QQmlProperty(tile, "border.color").read() == root.property(
        "appTheme"
    ).property("focusRing")
    assert _accessible_name(tile) == (
        "第 1 小节，推荐 C 大三和弦，C4 E4 G4，按 Enter 展开候选"
    )

    QTest.keyClick(root, Qt.Key_Tab)
    qapp.processEvents()
    assert root.property("activeFocusItem") != tile
    assert tile.property("expanded") is False
    root.close()


def test_keyboard_enter_space_and_escape_toggle_summary_and_restore_focus(qapp, tmp_path) -> None:
    engine, root, lane, _, _, controller, keyboard = _load_lane(qapp, tmp_path)
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    lane.setProperty("playheadSeconds", 3.0)
    tile.forceActiveFocus()

    QTest.keyClick(root, Qt.Key_Enter)
    qapp.processEvents()
    assert tile.property("expanded") is True
    assert "已展开，按 Enter 收起候选" in _accessible_name(tile)

    QTest.keyClick(root, Qt.Key_Enter)
    qapp.processEvents()
    assert tile.property("expanded") is False

    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert tile.property("expanded") is True

    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert tile.property("expanded") is False

    QTest.keyClick(root, Qt.Key_Space)
    qapp.processEvents()
    assert tile.property("expanded") is True

    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert tile.property("expanded") is False
    assert tile.property("activeFocus") is True
    root.close()


def test_keyboard_can_collapse_playhead_expansion_until_playhead_leaves(qapp, tmp_path) -> None:
    engine, root, lane, _, _, controller, keyboard = _load_lane(qapp, tmp_path)
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    lane.setProperty("playheadSeconds", 0.5)
    tile.forceActiveFocus()
    qapp.processEvents()
    assert tile.property("expanded") is True

    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert tile.property("expanded") is False
    assert tile.property("activeFocus") is True

    lane.setProperty("playheadSeconds", 3.0)
    lane.setProperty("playheadSeconds", 0.5)
    qapp.processEvents()
    assert tile.property("expanded") is True
    root.close()


def test_keyboard_tab_reaches_every_compact_action_and_escape_returns_to_summary(qapp, tmp_path) -> None:
    engine, root, lane, _, _, controller, keyboard = _load_lane(qapp, tmp_path)
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    lane.setProperty("playheadSeconds", 3.0)
    tile.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)
    qapp.processEvents()
    assert tile.property("expanded") is True
    assert tile.property("compactActions") is True

    expected = (
        ("harmonyCandidateMenu-0-1", "替代候选 Am"),
        ("harmonyCandidateMenu-0-2", "替代候选 F"),
        ("harmonyApplyMenu-0", "添加这个和弦"),
        ("harmonyRegenerateMenu-0", "换一个和弦"),
        ("harmonyWhyMenu-0", "为什么推荐这个和弦"),
        ("harmonyDismissMenu-0", "关闭本小节预览"),
    )
    for object_name, accessible_name in expected:
        QTest.keyClick(root, Qt.Key_Tab)
        qapp.processEvents()
        focused = root.property("activeFocusItem")
        assert focused is not None
        assert focused.objectName() == object_name
        assert _accessible_name(focused) == accessible_name

    QTest.keyClick(root, Qt.Key_Backtab)
    qapp.processEvents()
    assert root.property("activeFocusItem").objectName() == "harmonyWhyMenu-0"

    QTest.keyClick(root, Qt.Key_Escape)
    qapp.processEvents()
    assert tile.property("expanded") is False
    assert tile.property("activeFocus") is True
    root.close()


def test_mouse_summary_click_toggles_expansion(qapp, tmp_path) -> None:
    engine, root, lane, _, _, controller, keyboard = _load_lane(qapp, tmp_path)
    tile = _visual_item(lane, "harmonySuggestionTile-0")
    lane.setProperty("playheadSeconds", 3.0)

    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _window_point(root, tile),
    )
    qapp.processEvents()
    assert tile.property("expanded") is True
    assert tile.property("activeFocus") is True

    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        _window_point(root, tile),
    )
    qapp.processEvents()
    assert tile.property("expanded") is False
    root.close()


def test_lane_actions_route_only_through_harmony_controller_slots() -> None:
    main_source = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    controller_source = (
        QML_DIR.parents[0] / "harmony" / "controller.py"
    ).read_text(encoding="utf-8")

    assert "onCandidateRequested: harmony.selectCandidate" in main_source
    assert "onDismissRequested: harmony.dismissMeasurePreview" in main_source
    assert "onAddRequested: harmony.requestManualAdd" in main_source
    assert "onWhyRequested: harmony.requestExplanation" in main_source
    assert "onRegenerateRequested: harmony.regenerateMeasure" in main_source
    for slot in (
        "def selectCandidate(self, measure_index: int, candidate_index: int)",
        "def dismissMeasurePreview(self, measure_index: int)",
        "def requestManualAdd(self, measure_index: int)",
        "def requestExplanation(self, measure_index: int)",
        "def regenerateMeasure(self, measure_index: int)",
    ):
        assert slot in controller_source


def test_task10_actions_are_present_in_lane_and_mode_surface(qapp, tmp_path) -> None:
    engine, root, lane, _harmony, _playback, _controller, _keyboard = _load_lane(
        qapp, tmp_path
    )
    lane.setProperty("playheadSeconds", 0.5)
    qapp.processEvents()

    _visual_item(lane, "harmonyRegenerate-0")
    assert root.findChild(object, "harmonyClearGeneratedAction") is not None
    assert root.findChild(object, "harmonyClearConfirmDialog") is not None
    assert root.findChild(object, "harmonyTemporaryConfirmDialog") is not None
    root.close()
