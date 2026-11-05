from __future__ import annotations

import json

from ai_piano_studio.startup_profiler import REQUIRED_PHASES, StartupProfiler


def test_startup_profiler_writes_privacy_safe_phase_report(tmp_path) -> None:
    report_path = tmp_path / "startup.json"
    profiler = StartupProfiler(enabled=True, output_path=report_path)

    for phase in REQUIRED_PHASES:
        profiler.mark(phase)
    profiler.write_report()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert [marker["phase"] for marker in report["markers"]] == list(REQUIRED_PHASES)
    assert set(report) == {"schema_version", "pid", "markers", "required_markers"}
    assert report["required_markers"] == list(REQUIRED_PHASES)
    assert all(
        set(marker) == {"phase", "elapsed_ms", "delta_ms", "rss_mb"}
        for marker in report["markers"]
    )
    assert all(marker["elapsed_ms"] >= 0 for marker in report["markers"])
    assert all(marker["rss_mb"] >= 0 for marker in report["markers"])


def test_disabled_startup_profiler_never_writes_a_report(tmp_path) -> None:
    report_path = tmp_path / "startup.json"
    profiler = StartupProfiler(enabled=False, output_path=report_path)

    profiler.mark("process_start")
    profiler.write_report()

    assert not report_path.exists()


def test_startup_profiler_rejects_unknown_phase(tmp_path) -> None:
    profiler = StartupProfiler(enabled=True, output_path=tmp_path / "startup.json")

    try:
        profiler.mark("not-a-startup-phase")
    except ValueError as error:
        assert "not-a-startup-phase" in str(error)
    else:
        raise AssertionError("unknown startup phases must be rejected")
