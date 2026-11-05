from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from ai_piano_studio.appearance.scene_controller import SceneController
from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.experience.settings import ExperienceSettings
from ai_piano_studio.harmony.preview_model import HarmonyPreviewModel
from ai_piano_studio.harmony.types import (
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    ScoreBreakdown,
)
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.__main__ import build_engine
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.save_preferences import SavePreferences


QML_DIR = Path(__file__).parents[2] / "src" / "ai_piano_studio" / "qml"


def _preview_result() -> HarmonyAnalysisResult:
    candidate = ChordCandidate(
        chord_symbol="C",
        quality="major",
        diatonic_degree=1,
        pitches=(60, 64, 67),
        velocity=72,
        score=ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.9),
    )
    return HarmonyAnalysisResult(
        project_identity="project-a",
        project_revision=1,
        generation_id=1,
        mode="preview",
        key_estimate=KeyEstimate(0, "C", "major", 0.9, False, False),
        suggestions=(
            MeasureSuggestion(
                measure_index=0,
                start_tick=1_152,
                duration_ticks=768,
                temporary=False,
                candidates=(candidate,),
            ),
        ),
    )


def _load_preview_shell(qapp, tmp_path):
    note = Note.create(id="real", pitch=60, start_time=1.2, duration=0.8)
    editor = EditorController(ProjectState((note,), frozenset()))
    preview_model = HarmonyPreviewModel(editor)
    preview_model.apply_result(_preview_result())
    engine = QQmlApplicationEngine()
    playback = PlaybackController(editor, parent=engine)
    keyboard = KeyboardController(editor, playback, parent=engine)
    appearance = SceneController(
        parent=engine,
        settings=QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat),
    )
    appearance.setThemeId("dream-meadow")
    experience = ExperienceSettings(
        parent=engine,
        settings=QSettings(str(tmp_path / "experience.ini"), QSettings.Format.IniFormat),
    )
    save_preferences = SavePreferences(
        parent=engine,
        settings=QSettings(str(tmp_path / "save.ini"), QSettings.Format.IniFormat),
    )
    engine.rootContext().setContextProperty("editor", editor)
    engine.rootContext().setContextProperty("playback", playback)
    engine.rootContext().setContextProperty("keyboard", keyboard)
    engine.rootContext().setContextProperty("appearance", appearance)
    engine.rootContext().setContextProperty("scene", appearance)
    engine.rootContext().setContextProperty("experience", experience)
    engine.rootContext().setContextProperty("harmonyPreview", preview_model)
    engine.rootContext().setContextProperty("savePreferences", save_preferences)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    qapp.processEvents()
    assert len(engine.rootObjects()) == 1
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    return engine, root, editor


def _visual_item(root_item: QQuickItem, object_name: str) -> QQuickItem:
    pending = [root_item]
    while pending:
        item = pending.pop()
        if item.objectName() == object_name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f"missing visual item: {object_name}")


def _window_point(root, item: QQuickItem) -> QPoint:
    point = item.mapToItem(root.contentItem(), QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(point.x()), round(point.y()))


def test_preview_notes_are_visible_below_real_notes_and_transparent_to_clicks(qapp, qtbot, tmp_path) -> None:
    engine, root, editor = _load_preview_shell(qapp, tmp_path)
    timeline = root.findChild(QQuickItem, "timeline")
    real_note = _visual_item(timeline, "noteItem-real")
    overlapping_preview = _visual_item(timeline, "harmonyVirtualNote-0-60")
    blank_preview = _visual_item(timeline, "harmonyVirtualNote-0-64")

    assert timeline.property("visible") is True
    assert overlapping_preview.property("visible") is True
    assert blank_preview.property("visible") is True
    assert overlapping_preview.property("activeFocusOnTab") is False
    assert blank_preview.property("activeFocusOnTab") is False
    assert overlapping_preview.property("width") > 0
    assert overlapping_preview.property("height") > 0
    assert overlapping_preview.property("z") < real_note.property("z")
    qtbot.waitUntil(
        lambda: overlapping_preview.property("opacity") == pytest.approx(0.72),
        timeout=500,
    )

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _window_point(root, overlapping_preview))
    qapp.processEvents()
    assert editor.state.selected_ids == frozenset({"real"})

    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _window_point(root, blank_preview))
    qapp.processEvents()
    # Classic mode first exits the selected-note context; preview is passive on both clicks.
    assert not editor.state.selected_ids
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _window_point(root, blank_preview))
    qapp.processEvents()
    assert editor.noteModel.rowCount() == 2
    root.close()


def test_preview_delegate_source_stays_display_only() -> None:
    source = (QML_DIR / "Timeline.qml").read_text(encoding="utf-8")
    start = source.index('objectName: "harmonyVirtualNote-"')
    end = source.index('objectName: "recordingStatusBadge"', start)
    delegate_source = source[start:end]

    assert "strokeStyle: ShapePath.DashLine" in delegate_source
    for forbidden in ("MouseArea", "HoverHandler", "focus:", "Drag", "contextMenu"):
        assert forbidden not in delegate_source


def test_no_harmony_context_disables_the_mode_opener_without_reference_errors(
    qapp, qtlog, tmp_path
) -> None:
    engine, root, _editor = _load_preview_shell(qapp, tmp_path)
    mode_button = root.findChild(QObject, "moreHarmonyAction")
    mode_popup = root.findChild(QObject, "harmonyModePopup")
    assert mode_button is not None and mode_popup is not None

    assert mode_button.property("enabled") is False
    mode_button.triggered.emit()
    qapp.processEvents()

    assert mode_popup.property("opened") is False
    assert not [
        record for record in qtlog.records
        if "ReferenceError: harmony is not defined" in record.message
    ]
    root.close()


def test_application_engine_exposes_the_harmony_preview_model(qapp, tmp_path) -> None:
    editor = EditorController(ProjectState())
    harmony_settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    _, engine, _, _, _ = build_engine(
        controller=editor,
        harmony_settings=harmony_settings,
        experience_settings=ExperienceSettings(settings=QSettings(str(tmp_path / 'experience.ini'), QSettings.IniFormat)),
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        ),
    )
    try:
        assert isinstance(
            engine.rootContext().contextProperty("harmonyPreview"), HarmonyPreviewModel
        )
    finally:
        engine.rootObjects()[0].close()


def test_saved_project_keeps_manual_tail_preview_visible_in_qml(
    qapp, qtbot, tmp_path, caplog
) -> None:
    project_library = ProjectLibrary(tmp_path / "projects")
    editor = EditorController(
        project_library=project_library,
        autosave_interval_ms=5_000,
    )
    project_path = project_library.next_project_path()
    project_path.parent.mkdir(parents=True, exist_ok=True)
    with qtbot.waitSignal(editor.saveSucceeded, timeout=2_000):
        editor.saveProject(str(project_path))
    harmony_settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    harmony_settings.setMode("preview")
    _, engine, _, _, _ = build_engine(
        controller=editor,
        harmony_settings=harmony_settings,
        experience_settings=ExperienceSettings(settings=QSettings(str(tmp_path / 'experience.ini'), QSettings.IniFormat)),
        appearance_settings=QSettings(
            str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat
        ),
    )
    root = engine.rootObjects()[0]
    root.setProperty("width", 1366)
    root.setProperty("height", 768)
    root.setProperty("pageState", "editor")
    qapp.processEvents()
    harmony = engine.rootContext().contextProperty("harmony")
    lane = _visual_item(root.contentItem(), "harmonySuggestionLane")
    try:
        assert editor.currentFilePath == str(project_path)
        for index, pitch in enumerate((60, 60, 64, 67, 72)):
            editor._execute(
                editor.state.add_note(
                    Note.create(
                        id=f"c-major-{index}",
                        pitch=pitch,
                        start_time=index * 0.5,
                        duration=0.5,
                    )
                )
            )

        assert editor.currentFilePath == str(project_path)
        qtbot.waitUntil(
            lambda: harmony.latestResult is not None
            and len(harmony.latestResult.suggestions) == 1,
            timeout=2_000,
        )

        assert harmony.latestResult.key_estimate.tonic_pitch_class == 0
        assert harmony.latestResult.suggestions[0].measure_index == 1
        assert harmony.previewModel.count == 1, caplog.text
        assert lane.property("visible") is True
        qtbot.waitUntil(lambda: lane.width() > 0, timeout=500)
        assert lane.height() == pytest.approx(60)
        _visual_item(lane, "harmonySuggestionTile-1")
    finally:
        root.close()
        harmony.close()


def test_preview_lane_reserves_space_when_empty_without_moving_the_timeline(
    qapp, qtbot, tmp_path
) -> None:
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    _, engine, editor, playback, keyboard = build_engine(harmony_settings=settings, experience_settings=ExperienceSettings(settings=QSettings(str(tmp_path / 'experience.ini'), QSettings.IniFormat)))
    root = engine.rootObjects()[0]
    root.setProperty("width", 1366)
    root.setProperty("height", 768)
    root.setProperty("pageState", "editor")
    root.setVisible(True)
    harmony = engine.rootContext().contextProperty("harmony")
    lane = _visual_item(root.contentItem(), "harmonySuggestionLane")
    timeline = root.findChild(QQuickItem, "timeline")
    assert timeline is not None
    try:
        qapp.processEvents()
        original_timeline_geometry = (timeline.y(), timeline.height())
        assert harmony.previewModel.count == 0
        assert lane.property("visible") is True
        assert lane.height() == pytest.approx(60)
        assert (timeline.y(), timeline.height()) == original_timeline_geometry

        candidate_result = _preview_result()
        harmony.previewModel.apply_result(
            HarmonyAnalysisResult(
                harmony._project_identity,
                harmony.revision,
                harmony.analysisGeneration,
                "preview",
                candidate_result.key_estimate,
                candidate_result.suggestions,
            )
        )
        qapp.processEvents()
        assert harmony.previewModel.count == 1
        assert lane.height() == pytest.approx(60)
        assert (timeline.y(), timeline.height()) == original_timeline_geometry

        harmony.previewModel.clear()
        qapp.processEvents()
        assert lane.height() == pytest.approx(60)
        assert (timeline.y(), timeline.height()) == original_timeline_geometry

        harmony.setMode("auto")
        qapp.processEvents()
        assert lane.property("visible") is False
        qtbot.waitUntil(
            lambda: timeline.height() == pytest.approx(original_timeline_geometry[1] + 60),
            timeout=500,
        )

        harmony.setMode("off")
        qapp.processEvents()
        assert lane.property("visible") is False
        assert timeline.height() == pytest.approx(
            original_timeline_geometry[1] + 60
        )
    finally:
        root.close()
        harmony.close()


def test_real_low_confidence_service_result_reaches_the_qml_empty_state(
    qapp, qtbot, tmp_path
) -> None:
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    _, engine, editor, _playback, _keyboard = build_engine(harmony_settings=settings, experience_settings=ExperienceSettings(settings=QSettings(str(tmp_path / 'experience.ini'), QSettings.IniFormat)))
    root = engine.rootObjects()[0]
    root.setProperty("pageState", "editor")
    harmony = engine.rootContext().contextProperty("harmony")
    empty_state = _visual_item(root.contentItem(), "harmonySuggestionEmptyState")
    notes = (
        Note.create(id="c", pitch=72, start_time=0.0, duration=0.25),
        Note.create(id="c-sharp", pitch=73, start_time=0.25, duration=0.25),
    )
    try:
        editor._execute(ProjectState(notes, frozenset({note.id for note in notes})))
        harmony.requestAnalysis()
        qtbot.waitUntil(
            lambda: harmony.latestResult is not None and not harmony.analysisPending,
            timeout=2_000,
        )
        qapp.processEvents()

        assert harmony.latestResult.suggestions == ()
        assert harmony.latestResult.key_estimate.low_confidence is True
        assert harmony.latestResult.message == "再弹几拍，我正在判断适合的和弦"
        assert empty_state.property("visible") is True
        assert empty_state.property("text") == (
            "旋律里的线索还不够明确。再补几拍，或者点“检测整首并生成”。"
        )
    finally:
        root.close()
        harmony.close()
