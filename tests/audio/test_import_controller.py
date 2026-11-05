from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

from ai_piano_studio.audio.import_controller import AudioImportController
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.project_json import load_project


def _write_tone(path: Path, seconds: float = 5.0) -> None:
    sample_rate = 22_050
    count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    int(
                        0.35
                        * 32767
                        * math.sin(2 * math.pi * 440 * index / sample_rate)
                    ),
                )
                for index in range(count)
            )
        )


class _PlaybackProbe:
    def __init__(self) -> None:
        self.played = []
        self.stopped = 0

    def play_detached_notes(self, notes) -> None:
        self.played.append(notes)

    def stop_detached_preview(self) -> None:
        self.stopped += 1


def _ready(controller: AudioImportController) -> None:
    controller._analysis_project_session_id = controller._editor.projectSessionId
    controller.session._on_ready(
        {
            "durationSeconds": 4.0,
            "sampleRate": 22_050,
            "channels": 1,
            "tempoEstimate": 84,
            "confidence": 0.8,
            "range": {"lowestName": "C4", "highestName": "G4"},
            "rawDetectedNotes": [],
            "detectedNotes": [
                {"pitch": 60, "startTime": 0.0, "duration": 0.5, "velocity": 80},
                {"pitch": 67, "startTime": 0.5, "duration": 0.5, "velocity": 72},
            ],
        }
    )


def test_audio_preview_uses_detached_notes_without_mutating_project() -> None:
    editor = EditorController(ProjectState())
    playback = _PlaybackProbe()
    controller = AudioImportController(editor, playback)
    _ready(controller)

    assert controller.previewResult() is True
    assert len(playback.played) == 1
    assert len(playback.played[0]) == 2
    assert editor.state.active_notes == ()

    controller.stopPreview()
    assert playback.stopped == 1


def test_extraction_controls_reprocess_temporary_result_without_project_mutation() -> None:
    editor = EditorController(ProjectState())
    controller = AudioImportController(editor, _PlaybackProbe())
    controller._analysis_project_session_id = editor.projectSessionId
    controller.session._on_ready(
        {
            "durationSeconds": 2.0,
            "sampleRate": 22_050,
            "channels": 1,
            "tempoEstimate": 120,
            "confidence": 0.8,
            "range": {"lowestName": "C4", "highestName": "G4"},
            "rawDetectedNotes": [],
            "detectedNotes": [
                {"pitch": 48, "startTime": 0.0, "duration": 0.5, "confidence": 0.8},
                {"pitch": 67, "startTime": 0.0, "duration": 0.5, "confidence": 0.9},
            ],
        }
    )
    before = editor.state

    assert controller.setExtractionControls(
        "bass", 0.08, 0.0, 1.0, 0.5, 21, 108
    ) is True
    assert [note["pitch"] for note in controller.session.detectedNotes] == [48]
    assert editor.state == before

    assert controller.setExtractionControls(
        "unknown", -5, 3.0, -1.0, 5.0, 120, 10
    ) is True
    assert controller.session.extractionMode == "auto"
    assert controller.session.minimumNoteDuration == 0.01
    assert controller.session.noiseThreshold == 1.0
    assert controller.session.pitchMin == 21
    assert controller.session.pitchMax == 108


def test_audio_import_publishes_compact_summary_and_commits_notes_once(
    qtbot, tmp_path
) -> None:
    editor = EditorController(ProjectState())
    summaries: list[str] = []
    controller = AudioImportController(
        editor,
        _PlaybackProbe(),
        assistant_summary=summaries.append,
    )
    _ready(controller)

    assert controller.importResult("project_start", 0.0, "detected") is True
    assert controller.session.status == "imported"
    assert editor.bpm == 84
    assert len(editor.state.active_notes) == 2
    assert editor._history.undo_depth == 1
    assert controller.session.targetImportRange == {
        "mode": "project_start",
        "startSeconds": 0.0,
    }
    assert summaries and "detected notes: 2" in summaries[0]

    saved = tmp_path / "imported.pianoai.json"
    with qtbot.waitSignal(editor.saveSucceeded, timeout=2000):
        editor.saveProject(str(saved))
    document = json.loads(saved.read_text(encoding="utf-8"))
    assert "audio" not in document
    assert "pcm" not in document
    reloaded = load_project(saved)
    assert [(note.pitch, note.start_time, note.duration) for note in reloaded.notes] == [
        (60, 0.0, 0.5),
        (67, 0.5, 0.5),
    ]


def test_project_switch_cancels_audio_analysis_and_rejects_stale_result(
    qapp, qtbot, tmp_path
) -> None:
    source = tmp_path / "project-a.wav"
    _write_tone(source)
    editor = EditorController(ProjectState())
    controller = AudioImportController(editor)

    assert controller.startAnalysis(str(source)) is True
    qtbot.waitUntil(
        lambda: controller.session.status == "analyzing",
        timeout=5_000,
    )

    editor.newProject()

    qtbot.waitUntil(
        lambda: controller.session.status == "cancelled"
        and controller.session._thread is None,
        timeout=10_000,
    )
    assert controller.importResult() is False
    assert editor.state.active_notes == ()
