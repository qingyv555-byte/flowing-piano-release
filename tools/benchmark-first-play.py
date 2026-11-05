"""Measure first-play dispatch and first mixer block latency on Windows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.audio.source import ContinuousPianoToneSource
from ai_piano_studio.audio.tone import PianoToneCoordinator
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "min_ms": round(ordered[0], 3),
        "median_ms": round(median(ordered), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--output",
        default="docs/performance/first-play-latest.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.runs < 5:
        raise SystemExit("--runs must be at least 5")

    project_root = Path(__file__).resolve().parents[1]
    app = QApplication.instance() or QApplication([])
    coordinator = PianoToneCoordinator(
        project_root / "resources" / "piano" / "banks.json"
    )
    mixer = PianoVoiceMixer()
    source = ContinuousPianoToneSource(coordinator, mixer)
    coordinator.commit(0.0)
    editor = EditorController(
        ProjectState(
            (Note.create(id="first-play", pitch=60, start_time=0.0, duration=0.5),)
        )
    )
    player = AudioPlayer(parent=app, mixer=mixer, piano_source=source)
    playback = PlaybackController(
        editor,
        player,
        parent=app,
        piano_source=source,
    )

    rows: list[dict[str, object]] = []
    for run in range(1, args.runs + 1):
        playback.stop()
        app.processEvents()
        playing_at: list[float] = []

        def on_playing_changed() -> None:
            if not playing_at:
                playing_at.append(perf_counter())

        playback.playingChanged.connect(on_playing_changed)
        started = perf_counter()
        playback.play()
        play_returned = perf_counter()
        mixer.mix_frames(480)
        first_mix = perf_counter()
        app.processEvents()
        playback.playingChanged.disconnect(on_playing_changed)
        signal_time = playing_at[0] if playing_at else play_returned
        rows.append(
            {
                "run": run,
                "play_request_to_playing_signal_ms": round(
                    (signal_time - started) * 1000, 3
                ),
                "play_request_to_play_return_ms": round(
                    (play_returned - started) * 1000, 3
                ),
                "play_request_to_first_mixer_block_ms": round(
                    (first_mix - started) * 1000, 3
                ),
                "active_voice_count": len(mixer.active_voice_ids),
            }
        )

    playback.stop()
    mixer.stop_audio()
    app.processEvents()
    output = Path(args.output)
    if not output.is_absolute():
        output = project_root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    signal_values = [
        float(row["play_request_to_playing_signal_ms"]) for row in rows
    ]
    return_values = [float(row["play_request_to_play_return_ms"]) for row in rows]
    mix_values = [
        float(row["play_request_to_first_mixer_block_ms"]) for row in rows
    ]
    document = {
        "schema_version": 1,
        "definition": {
            "playing_signal": "playback.play() call to PlaybackController.playingChanged(true)",
            "first_mixer_block": "playback.play() call through the first 480-frame mixer block",
            "environment": "isolated Qt offscreen process; no network or user settings are read",
        },
        "runs": rows,
        "summary": {
            "all": {
                "playing_signal_ms": _summary(signal_values),
                "play_return_ms": _summary(return_values),
                "first_mixer_block_ms": _summary(mix_values),
            },
            "cold_first_run": rows[0],
            "warm_runs": {
                "count": len(rows) - 1,
                "playing_signal_ms": _summary(signal_values[1:]),
                "play_return_ms": _summary(return_values[1:]),
                "first_mixer_block_ms": _summary(mix_values[1:]),
            },
        },
    }
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document["summary"], ensure_ascii=False, indent=2))
    print(f"Raw first-play report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
