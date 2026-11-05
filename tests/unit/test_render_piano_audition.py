from __future__ import annotations

from array import array
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave

import pytest

from ai_piano_studio.audio.sample_piano import SAMPLE_ROOT_MIDI
from ai_piano_studio.audio.synth import SAMPLE_RATE
from tools.render_piano_audition import render_audition_set


EXPECTED_STEMS = {
    "c2", "c4", "c6", "c4-soft", "c4-medium", "c4-hard", "c-major",
    "a-minor", "f-major", "rapid-c4", "short-melody", "release-test",
}
TONE_POSITIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
ROOT_NOTES = (
    "A0", "C1", "D#1", "A1", "D#2", "A2", "D#3", "A3", "D#4", "A4",
    "F#4", "D#5", "A5", "D#6", "A6", "D#7", "A7", "C8",
)


def test_render_audition_set_writes_legacy_and_all_continuous_positions(
    tmp_path: Path,
) -> None:
    output = tmp_path / "audition"

    paths = render_audition_set(output, _catalog(tmp_path))

    expected_directories = {"legacy", *(f"tone-{position:.2f}" for position in TONE_POSITIONS)}
    assert {path.relative_to(output).parent.as_posix() for path in paths} == expected_directories
    assert len(paths) == len(expected_directories) * len(EXPECTED_STEMS)
    for directory in expected_directories:
        assert {path.stem for path in (output / directory).glob("*.wav")} == EXPECTED_STEMS


def test_continuous_audition_reports_checksums_durations_and_never_uses_legacy(
    tmp_path: Path,
) -> None:
    output = tmp_path / "audition"
    render_audition_set(output, _catalog(tmp_path))

    for position in TONE_POSITIONS:
        directory = output / f"tone-{position:.2f}"
        report = _report(directory / "report.json")
        assert report["engine"] == "ContinuousPianoToneSource"
        assert report["tone_position"] == position
        assert report["legacy_fallback"] is False
        assert report["bank_ids"] in (
            ["layers-06-11"],
            ["layers-06-11", "layers-07-12"],
            ["layers-07-12"],
            ["layers-07-12", "layers-08-13"],
            ["layers-08-13"],
        )
        assert len(report["files"]) == 12
        assert all(item["duration_seconds"] > 0 for item in report["files"])
        assert all(len(item["sha256"]) == 64 for item in report["files"])
        for item in report["files"]:
            path = directory / item["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
            with wave.open(str(path), "rb") as wav:
                assert wav.getnchannels() == 2
                assert wav.getsampwidth() == 2
                assert wav.getframerate() == SAMPLE_RATE
                assert item["duration_seconds"] == pytest.approx(wav.getnframes() / SAMPLE_RATE)


def test_cli_renders_continuous_audition_from_an_explicit_catalog(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    output = tmp_path / "formal"
    catalog = _catalog(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_piano_audition.py",
            "--output", str(output),
            "--catalog", str(catalog),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert len(tuple((output / "legacy").glob("*.wav"))) == 12
    assert len(tuple((output / "tone-0.25").glob("*.wav"))) == 12


def _catalog(tmp_path: Path) -> Path:
    banks = []
    for bank_id, position, soft_layer, hard_layer, value in (
        ("layers-06-11", 0.0, 6, 11, 300),
        ("layers-07-12", 0.5, 7, 12, 600),
        ("layers-08-13", 1.0, 8, 13, 900),
    ):
        bank = tmp_path / bank_id
        bank.mkdir()
        roots = []
        for index, (root_midi, root_note) in enumerate(zip(SAMPLE_ROOT_MIDI, ROOT_NOTES)):
            layers = {}
            for layer_name, layer, offset in (("Soft", soft_layer, 0), ("Hard", hard_layer, 100)):
                filename = f"{root_note.replace('#', 's')}-{layer_name.lower()}.wav"
                path = bank / filename
                _write_stereo_wav(path, value + index + offset)
                layers[layer_name] = {
                    "file": filename,
                    "frame_count": 320,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "source_layer": layer,
                }
            roots.append({"root_midi": root_midi, "root_note": root_note, "layers": layers})
        manifest = bank / "manifest.json"
        manifest.write_text(json.dumps({
            "asset_version": "test-piano", "sample_rate": 32_000, "channels": 2,
            "sample_width_bytes": 2, "sustain": {"loop_free": True},
            "crop": {"source_start_frame": 0, "tail_fade_ms": 120}, "roots": roots,
        }), encoding="utf-8")
        banks.append({"id": bank_id, "position": position, "manifest": str(manifest.relative_to(tmp_path))})
    catalog = tmp_path / "banks.json"
    catalog.write_text(json.dumps({"banks": banks}), encoding="utf-8")
    return catalog


def _write_stereo_wav(path: Path, value: int) -> None:
    samples = array("h", (value for _ in range(320 * 2)))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(32_000)
        output.writeframes(samples.tobytes())


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
