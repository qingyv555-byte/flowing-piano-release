from __future__ import annotations

import json
import logging
from math import sqrt
from pathlib import Path
from threading import Event, Thread

import pytest

from ai_piano_studio.audio.tone import PianoToneCoordinator


class FakeEngine:
    def __init__(self, manifest_path: Path, *, failed: bool = False) -> None:
        self.manifest_path = manifest_path
        self.legacy_selected = failed
        self.failure_reason = "decoder unavailable" if failed else ""
        self.warmups = 0
        self.activations = 0
        self.discards = 0

    def preload_c3_c5_async(self) -> None:
        self.warmups += 1

    def activate_cache(self) -> None:
        self.activations += 1

    def discard_idle_cache(self) -> None:
        self.discards += 1


def _catalog(tmp_path: Path, *, positions=(0.0, 0.5, 1.0)) -> Path:
    banks = []
    for index, position in enumerate(positions):
        bank_id = ("layers-06-11", "layers-07-12", "layers-08-13")[index]
        manifest = tmp_path / bank_id / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "sample_rate": 32_000,
                    "channels": 2,
                    "crop": {"source_start_frame": 0, "tail_fade_ms": 120},
                    "roots": [{"root_midi": 21}, {"root_midi": 24}],
                }
            ),
            encoding="utf-8",
        )
        banks.append({"id": bank_id, "position": position, "manifest": str(manifest.relative_to(tmp_path))})
    path = tmp_path / "banks.json"
    path.write_text(json.dumps({"banks": banks}), encoding="utf-8")
    return path


@pytest.fixture
def coordinator(tmp_path: Path) -> PianoToneCoordinator:
    return PianoToneCoordinator(_catalog(tmp_path), engine_factory=FakeEngine)


@pytest.mark.parametrize(("position", "ids", "gains"), [
    (0.0, ("layers-06-11",), (1.0,)),
    (0.25, ("layers-06-11", "layers-07-12"), (sqrt(.5), sqrt(.5))),
    (0.5, ("layers-07-12",), (1.0,)),
    (0.75, ("layers-07-12", "layers-08-13"), (sqrt(.5), sqrt(.5))),
    (1.0, ("layers-08-13",), (1.0,)),
])
def test_selection_uses_continuous_equal_power_adjacent_banks(coordinator, position, ids, gains):
    """Fails if a tone position picks a non-adjacent bank or uses a discontinuous gain."""
    selection = coordinator.commit(position)

    assert selection is not None
    assert selection.bank_ids == ids
    assert selection.gains == pytest.approx(gains)


def test_selection_warms_only_the_committed_adjacent_banks(tmp_path: Path) -> None:
    """Fails if startup or a commit warms a bank outside the active tone pair."""
    engines: list[FakeEngine] = []

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines.append(engine)
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    assert coordinator.selection() is None
    assert coordinator.commit(.25) is not None
    assert [engine.warmups for engine in engines] == [1, 1, 0]
    assert coordinator.commit(.75) is not None
    assert [engine.warmups for engine in engines] == [1, 2, 1]
    assert all(not hasattr(engine, "_piano_tone_bank_id") for engine in engines)


def test_selection_remains_a_pure_published_snapshot_after_a_late_failure(
    tmp_path: Path,
) -> None:
    """Fails if realtime selection performs failure logging or cache maintenance."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    assert coordinator.commit(.5).bank_ids == ("layers-07-12",)
    engines["layers-07-12"].legacy_selected = True
    engines["layers-07-12"].failure_reason = "background preload failure"

    published = coordinator.selection()

    assert published is not None
    assert published.bank_ids == ("layers-07-12",)
    assert engines["layers-07-12"].warmups == 1
    assert engines["layers-07-12"].activations == 1


def test_selection_returns_published_snapshot_while_failure_log_blocks(
    tmp_path: Path,
) -> None:
    """Fails if availability logging holds the snapshot lock on the realtime path."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    published = coordinator.commit(.5)
    assert published is not None
    engines["layers-07-12"].legacy_selected = True
    engines["layers-07-12"].failure_reason = "background preload failure"

    failure_logged = Event()
    release_log = Event()
    refresh_done = Event()
    selection_done = Event()
    selection_result = []

    class BlockingFailureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "layers-07-12" in record.getMessage():
                failure_logged.set()
                release_log.wait()

    handler = BlockingFailureHandler(level=logging.ERROR)
    tone_logger = logging.getLogger("ai_piano_studio.audio.tone")
    tone_logger.addHandler(handler)
    try:
        refresh_thread = Thread(
            target=lambda: (coordinator.available_interval(), refresh_done.set())
        )
        refresh_thread.start()
        assert failure_logged.wait(timeout=1)

        def read_selection() -> None:
            selection_result.append(coordinator.selection())
            selection_done.set()

        selection_thread = Thread(target=read_selection)
        selection_thread.start()
        assert selection_done.wait(timeout=1)
    finally:
        release_log.set()
        refresh_thread.join(timeout=1)
        selection_thread.join(timeout=1)
        tone_logger.removeHandler(handler)
        handler.close()

    assert refresh_done.is_set()
    assert selection_result[0] is not published
    assert selection_result[0].bank_ids == ("layers-06-11",)


def test_availability_refresh_replaces_a_failed_published_bank(tmp_path: Path) -> None:
    """Fails if the availability poll leaves new notes bound to a failed engine."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    assert coordinator.commit(.5).bank_ids == ("layers-07-12",)
    engines["layers-07-12"].legacy_selected = True
    engines["layers-07-12"].failure_reason = "background preload failure"

    assert coordinator.available_interval() == (0.0, 1.0)
    replacement = coordinator.selection()

    assert replacement is not None
    assert replacement.bank_ids == ("layers-06-11",)
    assert replacement.position == .5
    assert coordinator.status_message() == "Sample Piano"


def test_commit_refreshes_a_failed_current_selection_before_publishing(tmp_path: Path) -> None:
    """Fails if a newly committed tone can retain an engine that already selected Legacy."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    assert coordinator.commit(.5).bank_ids == ("layers-07-12",)
    engines["layers-07-12"].legacy_selected = True
    engines["layers-07-12"].failure_reason = "background preload failure"

    replacement = coordinator.commit(.5)

    assert replacement is not None
    assert replacement.bank_ids == ("layers-06-11",)


def test_failed_bank_retains_old_selection_and_logs_once(tmp_path: Path, caplog) -> None:
    """Fails if a bad bank replaces a playable selection or repeatedly logs its failure."""
    bad_id = "layers-08-13"

    def factory(path: Path) -> FakeEngine:
        return FakeEngine(path, failed=path.parent.name == bad_id)

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.tone"):
        coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
        original = coordinator.commit(.5)
        assert coordinator.commit(1.0) == original
        assert coordinator.commit(1.0) == original

    assert original is not None
    failures = [record for record in caplog.records if bad_id in record.message]
    assert len(failures) == 1
    assert "decoder unavailable" in failures[0].message


def test_late_idle_bank_failure_clamps_a_later_commit_to_the_remaining_bank(tmp_path: Path, caplog) -> None:
    """Fails if a failed bank remains selectable after its PCM cache is released."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.tone"):
        coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
        coordinator.commit(.75)
        original = coordinator.commit(.5)
        engines["layers-08-13"].legacy_selected = True
        engines["layers-08-13"].failure_reason = "checksum mismatch"
        replacement = coordinator.commit(1.0)
        assert replacement is not None
        assert replacement.bank_ids == ("layers-07-12",)
        assert coordinator.status_message() == "Sample Piano"

    failures = [record for record in caplog.records if "layers-08-13" in record.message]
    assert len(failures) == 1
    assert "checksum mismatch" in failures[0].message


def test_failed_middle_bank_never_turns_endpoints_into_adjacent_crossfade(tmp_path: Path) -> None:
    """Fails if filtering a failed landmark lets warm and brilliant banks bridge it."""
    coordinator = PianoToneCoordinator(
        _catalog(tmp_path),
        engine_factory=lambda path: FakeEngine(
            path, failed=path.parent.name == "layers-07-12"
        ),
    )
    original = coordinator.commit(0.0)

    assert original is not None
    assert coordinator.commit(0.75) == original
    assert coordinator.commit(1.0) is not None
    assert coordinator.commit(0.75).bank_ids == ("layers-08-13",)


def test_late_failure_refreshes_legacy_availability_and_logs_bank_once(tmp_path: Path, caplog) -> None:
    """Fails if polling after every late engine failure leaves a stale playable status."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    coordinator.commit(.25)
    coordinator.commit(.75)
    for engine in engines.values():
        engine.legacy_selected = True
        engine.failure_reason = "background preload failure"

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.tone"):
        assert coordinator.available_interval() is None
        assert coordinator.status_message() == "Legacy Piano"
        assert coordinator.status_message() == "Legacy Piano"

    for bank_id in engines:
        failures = [record for record in caplog.records if bank_id in record.message]
        assert len(failures) == 1
        assert "background preload failure" in failures[0].message


def test_late_idle_bank_failure_logs_when_a_commit_refreshes_availability(tmp_path: Path, caplog) -> None:
    """Fails if a discarded engine can retain a stale selectable state."""
    engines: dict[str, FakeEngine] = {}

    def factory(path: Path) -> FakeEngine:
        engine = FakeEngine(path)
        engines[path.parent.name] = engine
        return engine

    coordinator = PianoToneCoordinator(_catalog(tmp_path), engine_factory=factory)
    coordinator.commit(.75)
    original = coordinator.commit(.5)
    engines["layers-08-13"].legacy_selected = True
    engines["layers-08-13"].failure_reason = "background preload failure"

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.tone"):
        replacement = coordinator.commit(1.0)
        assert replacement is not None
        assert replacement.bank_ids == ("layers-07-12",)
        assert coordinator.status_message() == "Sample Piano"

    failures = [record for record in caplog.records if "layers-08-13" in record.message]
    assert len(failures) == 1


def test_one_usable_bank_clamps_interval_and_no_bank_uses_legacy_status(tmp_path: Path) -> None:
    """Fails if unavailable banks leave a selectable gap or no-bank state looks playable."""
    coordinator = PianoToneCoordinator(
        _catalog(tmp_path),
        engine_factory=lambda path: FakeEngine(path, failed=path.parent.name != "layers-07-12"),
    )
    assert coordinator.available_interval() == (0.5, 0.5)
    selection = coordinator.commit(0.0)
    assert selection is not None
    assert selection.position == .5

    unavailable = PianoToneCoordinator(
        _catalog(tmp_path), engine_factory=lambda path: FakeEngine(path, failed=True)
    )
    assert unavailable.commit(0.0) is None
    assert unavailable.commit(0.5) is None
    assert unavailable.commit(1.0) is None
    assert unavailable.selection() is None
    assert unavailable.available_interval() is None
    assert unavailable.status_message() == "Legacy Piano"


def test_catalog_rejects_incompatible_root_map_without_constructing_engines(tmp_path: Path) -> None:
    """Fails if a catalog can crossfade banks with incompatible sample contracts."""
    catalog = _catalog(tmp_path)
    bad_manifest = tmp_path / "layers-08-13" / "manifest.json"
    payload = json.loads(bad_manifest.read_text(encoding="utf-8"))
    payload["roots"][1]["root_midi"] = 25
    bad_manifest.write_text(json.dumps(payload), encoding="utf-8")
    constructed = []

    coordinator = PianoToneCoordinator(catalog, engine_factory=lambda path: constructed.append(path))

    assert constructed == []
    assert coordinator.available_interval() is None
    assert coordinator.status_message() == "Legacy Piano"


def test_catalog_rejects_incompatible_crop_without_constructing_engines(tmp_path: Path) -> None:
    """Fails if banks with different crop contracts can be blended."""
    catalog = _catalog(tmp_path)
    bad_manifest = tmp_path / "layers-08-13" / "manifest.json"
    payload = json.loads(bad_manifest.read_text(encoding="utf-8"))
    payload["crop"]["tail_fade_ms"] = 80
    bad_manifest.write_text(json.dumps(payload), encoding="utf-8")
    constructed = []

    coordinator = PianoToneCoordinator(catalog, engine_factory=constructed.append)

    assert constructed == []
    assert coordinator.available_interval() is None
    assert coordinator.status_message() == "Legacy Piano"


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda payload: payload["banks"].append(payload["banks"][0].copy()), "duplicate"),
        (lambda payload: payload["banks"][1].__setitem__("position", 0.0), "duplicate"),
        (lambda payload: payload["banks"][2].__setitem__("position", 0.4), "increasing"),
        (lambda payload: payload["banks"][1].__setitem__("position", -0.1), "range"),
        (lambda payload: payload["banks"][1].__setitem__("position", 1.2), "range"),
        (lambda payload: payload["banks"][1].__setitem__("manifest", "missing.json"), "missing"),
    ),
)
def test_catalog_rejects_invalid_topology_before_constructing_engines(tmp_path: Path, change, message) -> None:
    """Fails if malformed catalog topology can be silently normalized into a selection."""
    catalog = _catalog(tmp_path)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    change(payload)
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    constructed = []

    coordinator = PianoToneCoordinator(catalog, engine_factory=constructed.append)

    assert constructed == []
    assert coordinator.status_message() == "Legacy Piano"
