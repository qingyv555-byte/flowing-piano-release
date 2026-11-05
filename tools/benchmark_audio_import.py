"""Measure the dependency-free local melody analyzer on simple WAV-like PCM."""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import time
import tracemalloc

import numpy as np

from ai_piano_studio.audio.importer import analyze_pcm


def benchmark(seconds: int, sample_rate: int = 22_050) -> dict[str, object]:
    time_axis = np.arange(seconds * sample_rate, dtype=np.float32) / sample_rate
    pcm = (
        0.28 * np.sin(2 * np.pi * 440.0 * time_axis)
        + 0.08 * np.sin(2 * np.pi * 523.25 * time_axis)
    ).astype(np.float32)
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = analyze_pcm(pcm, sample_rate, sensitivity="standard")
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "seconds": seconds,
        "analysisTimeSeconds": round(elapsed, 6),
        "peakTracemallocMiB": round(peak_bytes / (1024 * 1024), 3),
        "outputNoteCount": len(result.get("detectedNotes", [])),
        "tempoEstimate": result.get("tempoEstimate", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path outside the source tree",
    )
    args = parser.parse_args()
    report = {
        "sampleRate": 22_050,
        "input": "deterministic monophonic synthetic PCM",
        "cases": [benchmark(seconds) for seconds in (10, 30, 60)],
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
