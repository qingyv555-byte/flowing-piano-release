from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock
from time import perf_counter

import pytest
from PySide6.QtCore import QSettings, QTimer

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.harmony.suggestion_service import HarmonySuggestionService
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    MelodyNoteSnapshot,
    ScoreBreakdown,
)


_MEASURE_TICKS = 1_920
_MELODY_PATTERN = (
    (72, 0, 480),
    (83, 480, 240),
    (76, 720, 240),
    (79, 960, 480),
    (77, 1_440, 240),
    (81, 1_680, 120),
    (79, 1_800, 60),
    (72, 1_860, 60),
)


def _request(*, measure_count: int, note_count: int | None = None) -> AnalysisRequest:
    total_notes = note_count if note_count is not None else measure_count * 8
    melody = tuple(
        MelodyNoteSnapshot(
            note_id=f"melody-{index}",
            pitch=_MELODY_PATTERN[index % 8][0],
            start_tick=(index // 8) * _MEASURE_TICKS
            + _MELODY_PATTERN[index % 8][1],
            duration_ticks=_MELODY_PATTERN[index % 8][2],
            velocity=72 + index % 9,
        )
        for index in range(total_notes)
    )
    return AnalysisRequest(
        project_identity="performance-project",
        project_revision=7,
        generation_id=3,
        mode="preview",
        melody=melody,
        tempo=TempoMap(),
        completed_measure_indices=frozenset(range(measure_count)),
        current_tick=measure_count * _MEASURE_TICKS,
        min_pitch=36,
        max_pitch=84,
    )


@pytest.mark.parametrize("measure_count", (4, 16))
def test_regular_harmony_analysis_finishes_under_100_ms(measure_count: int) -> None:
    request = _request(measure_count=measure_count)

    started = perf_counter()
    result = HarmonySuggestionService().analyze(request)
    elapsed_ms = (perf_counter() - started) * 1_000

    assert len(result.suggestions) == measure_count
    assert elapsed_ms < 100, f"{measure_count} measures took {elapsed_ms:.3f} ms"


def test_two_thousand_note_harmony_analysis_finishes_under_500_ms() -> None:
    request = _request(measure_count=250, note_count=2_000)

    started = perf_counter()
    result = HarmonySuggestionService().analyze(request)
    elapsed_ms = (perf_counter() - started) * 1_000

    assert len(result.suggestions) == 250
    assert elapsed_ms < 500, f"2,000 notes took {elapsed_ms:.3f} ms"


def test_benchmark_cli_reports_timings_gates_and_host_information() -> None:
    project_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [sys.executable, str(project_root / "tools" / "benchmark_harmony.py"), "--json"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert [(item["label"], item["limit_ms"]) for item in report["benchmarks"]] == [
        ("4 measures", 100),
        ("16 measures", 100),
        ("2,000 notes", 500),
    ]
    assert all(item["elapsed_ms"] >= 0 for item in report["benchmarks"])
    assert all(item["passed"] is True for item in report["benchmarks"])
    assert report["environment"]["python"]
    assert report["environment"]["pyside6"]
    assert report["environment"]["platform"]


class SlowService:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.finished = Event()
        self._lock = Lock()
        self.requests: list[AnalysisRequest] = []

    def analyze(self, request: AnalysisRequest) -> HarmonyAnalysisResult:
        with self._lock:
            self.requests.append(request)
        self.started.set()
        try:
            assert self.release.wait(timeout=2.0)
            return _preview_result(request)
        finally:
            self.finished.set()


def _preview_result(request: AnalysisRequest, chord_symbol: str = "C") -> HarmonyAnalysisResult:
    score = ScoreBreakdown(0.8, 0.8, 0.0, 0.0, 0.0, 0.0, 0.8, 0.0, 0.9, 0.9)
    candidate = ChordCandidate(chord_symbol, "major", 0, (48, 52, 55), 50, score)
    return HarmonyAnalysisResult(
        project_identity=request.project_identity,
        project_revision=request.project_revision,
        generation_id=request.generation_id,
        mode=request.mode,
        key_estimate=KeyEstimate(0, "C", "major", 0.9, False, False),
        suggestions=(MeasureSuggestion(0, 0, _MEASURE_TICKS, False, (candidate,)),),
    )


@pytest.fixture
def slow_controller(qtbot, tmp_path):
    notes = (
        Note.create(id="seed-1", pitch=72, start_time=0.0, duration=0.5),
        Note.create(id="seed-2", pitch=76, start_time=0.5, duration=0.5),
        Note.create(id="seed-3", pitch=79, start_time=1.0, duration=0.5),
    )
    editor = EditorController(ProjectState(notes))
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    service = SlowService()
    controller = HarmonySuggestionController(editor, settings, service=service)
    yield controller, service
    service.release.set()
    controller.close()


def test_slow_analysis_does_not_block_queued_qt_signals(slow_controller, qtbot) -> None:
    controller, service = slow_controller

    controller.requestAnalysis()
    qtbot.waitUntil(service.started.is_set, timeout=2_000)
    queued_signal_processed = Event()
    QTimer.singleShot(0, queued_signal_processed.set)

    qtbot.waitUntil(queued_signal_processed.is_set, timeout=500)
    assert controller.analysisPending is True

    service.release.set()
    qtbot.waitUntil(service.finished.is_set, timeout=2_000)
    qtbot.waitUntil(lambda: not controller.analysisPending, timeout=2_000)
    assert controller.previewModel.count == 1


def test_stale_slow_result_does_not_replace_preview_model(slow_controller, qtbot) -> None:
    controller, service = slow_controller
    baseline_request = controller._snapshot_request(include_manual_tail=True)
    controller.previewModel.apply_result(_preview_result(baseline_request, "F"))
    baseline_index = controller.previewModel.index(0, 0)
    assert controller.previewModel.data(
        baseline_index, controller.previewModel.chordSymbolRole
    ) == "F"

    controller.requestAnalysis()
    qtbot.waitUntil(service.started.is_set, timeout=2_000)
    old_request = service.requests[0]
    controller.editor.createNoteAt(120, 20)
    assert controller.revision == old_request.project_revision + 1

    service.release.set()
    qtbot.waitUntil(service.finished.is_set, timeout=2_000)
    qtbot.waitUntil(
        lambda: not any(
            job._request is old_request for job in controller._jobs.values()
        ),
        timeout=2_000,
    )

    current_index = controller.previewModel.index(0, 0)
    assert controller.previewModel.count == 1
    assert controller.previewModel.data(
        current_index, controller.previewModel.chordSymbolRole
    ) == "F"
