from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import sys
from time import perf_counter

import PySide6

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.harmony.suggestion_service import HarmonySuggestionService
from ai_piano_studio.harmony.types import AnalysisRequest, MelodyNoteSnapshot


MEASURE_TICKS = 1_920
MELODY_PATTERN = (
    (72, 0, 480),
    (83, 480, 240),
    (76, 720, 240),
    (79, 960, 480),
    (77, 1_440, 240),
    (81, 1_680, 120),
    (79, 1_800, 60),
    (72, 1_860, 60),
)


def build_request(measure_count: int, note_count: int) -> AnalysisRequest:
    melody = tuple(
        MelodyNoteSnapshot(
            note_id=f"benchmark-{index}",
            pitch=MELODY_PATTERN[index % 8][0],
            start_tick=(index // 8) * MEASURE_TICKS + MELODY_PATTERN[index % 8][1],
            duration_ticks=MELODY_PATTERN[index % 8][2],
            velocity=72 + index % 9,
        )
        for index in range(note_count)
    )
    return AnalysisRequest(
        project_identity="benchmark-project",
        project_revision=1,
        generation_id=1,
        mode="preview",
        melody=melody,
        tempo=TempoMap(),
        completed_measure_indices=frozenset(range(measure_count)),
        current_tick=measure_count * MEASURE_TICKS,
        min_pitch=36,
        max_pitch=84,
    )


def benchmark(label: str, measure_count: int, note_count: int, limit_ms: int) -> dict:
    request = build_request(measure_count, note_count)
    started = perf_counter()
    result = HarmonySuggestionService().analyze(request)
    elapsed_ms = (perf_counter() - started) * 1_000
    return {
        "label": label,
        "measures": measure_count,
        "notes": note_count,
        "suggestions": len(result.suggestions),
        "elapsed_ms": round(elapsed_ms, 3),
        "limit_ms": limit_ms,
        "passed": elapsed_ms < limit_ms,
    }


def build_report() -> dict:
    return {
        "benchmarks": [
            benchmark("4 measures", 4, 32, 100),
            benchmark("16 measures", 16, 128, 100),
            benchmark("2,000 notes", 250, 2_000, 500),
        ],
        "environment": {
            "python": platform.python_version(),
            "pyside6": PySide6.__version__,
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark deterministic harmony analysis")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        environment = report["environment"]
        print(
            "Harmony benchmark | "
            f"Python {environment['python']} | PySide6 {environment['pyside6']} | "
            f"{environment['platform']} | CPU {environment['processor']}"
        )
        for item in report["benchmarks"]:
            status = "PASS" if item["passed"] else "FAIL"
            print(
                f"{status}  {item['label']}: {item['elapsed_ms']:.3f} ms "
                f"(< {item['limit_ms']} ms; {item['notes']} notes; "
                f"{item['suggestions']} suggestions)"
            )
    return 0 if all(item["passed"] for item in report["benchmarks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
