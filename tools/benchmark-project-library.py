"""Measure library indexing without parsing untouched project note JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time
import sys

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ai_piano_studio.persistence.project_library import ProjectLibrary


def measure(count: int) -> float:
    with tempfile.TemporaryDirectory(prefix="ai-piano-library-benchmark-") as temporary:
        library = ProjectLibrary(Path(temporary) / "Projects")
        for index in range(count):
            target = library.root / f"project-{index:03d}" / "project.pianoai.json"
            target.parent.mkdir(parents=True)
            target.write_text("{}", encoding="utf-8")
        started = time.perf_counter()
        entries = library.entries([])
        elapsed = time.perf_counter() - started
        if len(entries) != count:
            raise RuntimeError(f"expected {count} entries, got {len(entries)}")
        return elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "counts": {
            str(count): round(measure(count), 6)
            for count in (0, 10, 100, 500)
        },
        "unit": "seconds",
        "method": "ProjectLibrary.entries with no recent paths; untouched JSON is not parsed",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
