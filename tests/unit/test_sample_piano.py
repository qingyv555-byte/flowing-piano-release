from __future__ import annotations

from array import array
import hashlib
import importlib.util
import json
import logging
from math import sqrt
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import perf_counter, sleep
from typing import Callable
import wave

import pytest

from ai_piano_studio.audio.profile import ReleaseMode, SAMPLE_PIANO_V1
from ai_piano_studio.audio.sample_piano import (
    DecodedSample,
    SAMPLE_ROOT_MIDI,
    LegacyVoice,
    SamplePianoEngine,
    SampleVoice,
    decode_wav,
)
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.source import (
    ContinuousPianoToneSource,
    LegacyPianoSource,
    PianoSoundSource,
    SamplePianoSource,
)
from ai_piano_studio.audio import source as source_module
from ai_piano_studio.audio.tone import PianoToneSelection
from ai_piano_studio.audio.synth import SAMPLE_RATE, write_wav
from ai_piano_studio.domain.note import Note
from tools import build_salamander_subset as salamander_converter


ROOT_NOTES = (
    "A0",
    "C1",
    "D#1",
    "A1",
    "D#2",
    "A2",
    "D#3",
    "A3",
    "D#4",
    "A4",
    "F#4",
    "D#5",
    "A5",
    "D#6",
    "A6",
    "D#7",
    "A7",
    "C8",
)
EXPECTED_ROOT_MIDI = (
    21,
    24,
    27,
    33,
    39,
    45,
    51,
    57,
    63,
    69,
    66,
    75,
    81,
    87,
    93,
    99,
    105,
    108,
)

PROJECT_ROOT = Path(__file__).parents[2]
PIANO_ROOT = PROJECT_ROOT / "resources" / "piano"
CONVERTER_ROOTS = tuple(zip(ROOT_NOTES, EXPECTED_ROOT_MIDI))
SOURCE_URL = (
    "https://archive.org/download/SalamanderGrandPianoV3/"
    "SalamanderGrandPianoV3_44.1khz16bit.tar.bz2"
)
SHA256_NOTICE = (
    "SHA-256 为下载后由 AI Piano Studio 项目本地计算，"
    "官方原始发布仅提供 SHA-1/MD5。"
)
HUMAN_PROVENANCE_TITLE = "# Salamander Grand Piano v3 Source Provenance"
HUMAN_LICENSE_SENTENCE = (
    "The retained license file is verified against the tracked CC BY 3.0 "
    "provenance above."
)
HUMAN_PROVENANCE_FIELDS = (
    "source_filename",
    "source_size",
    "source_sha1",
    "source_md5",
    "locally_computed_sha256",
    "source_url",
    "author",
    "license",
    "license_filename",
    "license_sha256",
    "retrieval_date",
)
EXPECTED_MAXIMUM_SECONDS = {
    "A0": 6.0,
    "C1": 6.0,
    "D#1": 6.0,
    "A1": 6.0,
    "D#2": 6.0,
    "A2": 6.0,
    "D#3": 4.0,
    "A3": 4.0,
    "D#4": 4.0,
    "A4": 4.0,
    "F#4": 4.0,
    "D#5": 3.0,
    "A5": 3.0,
    "D#6": 2.2,
    "A6": 2.2,
    "D#7": 2.2,
    "A7": 2.2,
    "C8": 1.5,
}


def _write_converter_source(path: Path, seconds: float) -> None:
    frames = int(seconds * 44_100)
    attack = (
        int(12_345).to_bytes(2, "little", signed=True)
        + int(-12_345).to_bytes(2, "little", signed=True)
    )
    sustain = (
        int(10_000).to_bytes(2, "little", signed=True)
        + int(-10_000).to_bytes(2, "little", signed=True)
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44_100)
        output.writeframes(attack + sustain * (frames - 1))


def _converter_source_tree(tmp_path: Path, *, complete: bool = True) -> Path:
    source = tmp_path / "SalamanderGrandPianoV3_44.1khz16bit"
    samples = source / "Samples"
    samples.mkdir(parents=True)
    if complete:
        for root_note, _root_midi in CONVERTER_ROOTS:
            for layer in (6, 7, 8, 11, 12, 13):
                _write_converter_source(
                    samples / f"{root_note}v{layer}.wav",
                    EXPECTED_MAXIMUM_SECONDS[root_note] + 0.1,
                )
    return source


def _converter_provenance(source: Path) -> tuple[Path, Path, dict[str, object]]:
    archive = source / "SalamanderGrandPianoV3_44.1khz16bit.tar.bz2"
    archive.write_bytes(b"official-fixture-archive")
    license_path = _converter_license(source)
    provenance: dict[str, object] = {
        "source_filename": archive.name,
        "source_size": archive.stat().st_size,
        "source_sha1": hashlib.sha1(archive.read_bytes()).hexdigest(),
        "source_md5": hashlib.md5(archive.read_bytes()).hexdigest(),
        "locally_computed_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_url": SOURCE_URL,
        "author": "Alexander Holm",
        "license": "CC BY 3.0",
        "retrieval_date": "2026-08-11",
        "sha256_notice": SHA256_NOTICE,
        "license_filename": license_path.name,
        "license_sha256": hashlib.sha256(license_path.read_bytes()).hexdigest(),
    }
    provenance_path = source / "source-provenance.json"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    return archive, provenance_path, provenance


def _converter_license(source: Path) -> Path:
    license_path = source / "LICENSE-CC-BY-3.0.html"
    license_path.write_text(
        "Attribution 3.0 Unported\nCC BY 3.0\n"
        "https://creativecommons.org/licenses/by/3.0/\n",
        encoding="utf-8",
    )
    (source / "README").write_text(
        "Licence:\nCC-by\nhttp://creativecommons.org/licenses/by/3.0/\n"
        "Author: Alexander Holm\n",
        encoding="utf-8",
    )
    return license_path


def _patch_converter_policy(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    provenance: dict[str, object],
    *,
    markdown_overrides: dict[str, object] | None = None,
    markdown_transform: Callable[[str], str] | None = None,
) -> None:
    authority_path = directory / "PROVENANCE-SALAMANDER.json"
    authority_path.write_text(json.dumps(provenance), encoding="utf-8")
    markdown_values = {**provenance, **(markdown_overrides or {})}
    markdown_path = directory / "PROVENANCE-SALAMANDER.md"
    markdown = (
        f"{HUMAN_PROVENANCE_TITLE}\n\n"
        + "\n".join(
            f"- {key}: `{markdown_values[key]}`"
            for key in HUMAN_PROVENANCE_FIELDS
        )
        + f"\n\n{HUMAN_LICENSE_SENTENCE}\n\n"
        + f"{markdown_values['sha256_notice']}\n"
    )
    markdown_path.write_text(
        markdown_transform(markdown) if markdown_transform else markdown,
        encoding="utf-8",
    )
    monkeypatch.setattr(salamander_converter, "AUTHORITY_PATH", authority_path)
    monkeypatch.setattr(salamander_converter, "AUTHORITY_MARKDOWN_PATH", markdown_path)


def _run_converter(
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
    output: Path,
    *,
    archive: Path | None = None,
    provenance: Path | None = None,
    license_path: Path | None = None,
    policy: dict[str, object] | None = None,
    markdown_overrides: dict[str, object] | None = None,
    markdown_transform: Callable[[str], str] | None = None,
) -> int:
    if archive is None or provenance is None:
        archive, provenance, generated_policy = _converter_provenance(source)
        policy = policy or generated_policy
    if policy is None:
        policy = json.loads(provenance.read_text(encoding="utf-8"))
    if license_path is None:
        license_path = _converter_license(source)
    _patch_converter_policy(
        monkeypatch,
        output.parent,
        policy,
        markdown_overrides=markdown_overrides,
        markdown_transform=markdown_transform,
    )
    return salamander_converter.main(
        [
            str(source),
            "--output",
            str(output),
            "--archive",
            str(archive),
            "--provenance",
            str(provenance),
            "--license",
            str(license_path),
            "--soft-layer",
            "6",
            "--hard-layer",
            "11",
        ],
    )


def test_formal_tone_catalog_has_three_complete_licensed_banks() -> None:
    """Fails until every selectable Salamander bank is packaged and licensed."""
    catalog = json.loads((PIANO_ROOT / "banks.json").read_text(encoding="utf-8"))

    assert [(item["id"], item["position"]) for item in catalog["banks"]] == [
        ("layers-06-11", 0.0),
        ("layers-07-12", 0.5),
        ("layers-08-13", 1.0),
    ]
    for item in catalog["banks"]:
        bank = PIANO_ROOT / "banks" / item["id"]
        manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
        assert len(list(bank.glob("*.wav"))) == 36
        assert manifest["source"]["soft_layer"] == item["soft_layer"]
        assert manifest["source"]["hard_layer"] == item["hard_layer"]
        assert manifest["source"]["license"] == "CC BY 3.0"


def test_salamander_converter_builds_three_formal_banks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if formal-bank generation skips a pair or weakens the provenance gate."""
    source = _converter_source_tree(tmp_path)
    archive, provenance_path, policy = _converter_provenance(source)
    piano_root = tmp_path / "resources" / "piano"
    piano_root.mkdir(parents=True)
    _patch_converter_policy(monkeypatch, tmp_path, policy)

    catalog_path = salamander_converter.build_formal_banks(
        source,
        piano_root,
        archive_path=archive,
        provenance_path=provenance_path,
        license_path=source / "LICENSE-CC-BY-3.0.html",
    )

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert [
        (item["id"], item["soft_layer"], item["hard_layer"])
        for item in catalog["banks"]
    ] == [
        ("layers-06-11", 6, 11),
        ("layers-07-12", 7, 12),
        ("layers-08-13", 8, 13),
    ]
    for item in catalog["banks"]:
        bank = piano_root / "banks" / item["id"]
        manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
        assert len(list(bank.glob("*.wav"))) == 36
        assert (
            sum(path.stat().st_size for path in bank.glob("*.wav"))
            <= 30 * 1024 * 1024
        )
        assert manifest["provenance"] == policy


def test_salamander_converter_emits_bounded_deterministic_real_wavs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if conversion changes the root/layer contract, audio format, crop, or fade."""
    source = _converter_source_tree(tmp_path)
    output = tmp_path / "generated-subset"

    archive, provenance_path, expected_provenance = _converter_provenance(source)

    result = _run_converter(
        monkeypatch,
        source,
        output,
        archive=archive,
        provenance=provenance_path,
    )

    assert result == 0
    expected_names = {
        f"{root_note.replace('#', 's')}-{layer.lower()}.wav"
        for root_note, _root_midi in CONVERTER_ROOTS
        for layer in ("Soft", "Hard")
    }
    assert {path.name for path in output.glob("*.wav")} == expected_names

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["sample_rate"] == 32_000
    assert manifest["channels"] == 2
    assert manifest["sample_width_bytes"] == 2
    assert manifest["crop"] == {"source_start_frame": 0, "tail_fade_ms": 120}
    assert manifest["provenance"] == expected_provenance
    assert [root["root_midi"] for root in manifest["roots"]] == list(EXPECTED_ROOT_MIDI)
    assert [root["root_note"] for root in manifest["roots"]] == list(ROOT_NOTES)
    assert max(
        min(abs(pitch - root) for root in EXPECTED_ROOT_MIDI)
        for pitch in range(21, 109)
    ) == 3

    for root in manifest["roots"]:
        assert set(root["layers"]) == {"Soft", "Hard"}
        expected_frames = int(EXPECTED_MAXIMUM_SECONDS[root["root_note"]] * 32_000)
        for layer in root["layers"].values():
            wav_path = output / layer["file"]
            assert layer["sha256"] == hashlib.sha256(wav_path.read_bytes()).hexdigest()
            assert len(layer["sha256"]) == 64
            assert layer["frame_count"] == expected_frames
            with wave.open(str(wav_path), "rb") as generated:
                assert generated.getcomptype() == "NONE"
                assert generated.getframerate() == 32_000
                assert generated.getnchannels() == 2
                assert generated.getsampwidth() == 2
                assert generated.getnframes() == expected_frames

    probe = output / "A0-soft.wav"
    with wave.open(str(probe), "rb") as generated:
        first_frame = generated.readframes(1)
        generated.setpos(generated.getnframes() - 3_840)
        fade = generated.readframes(3_840)
    left_attack = int.from_bytes(first_frame[:2], "little", signed=True)
    left_fade = [
        int.from_bytes(fade[offset : offset + 2], "little", signed=True)
        for offset in range(0, len(fade), 4)
    ]
    assert left_attack == 12_345
    assert left_fade[0] == 10_000
    assert left_fade[1_920] == pytest.approx(5_000, abs=2)
    assert left_fade[-1] == 0
    assert all(first >= second for first, second in zip(left_fade, left_fade[1:]))
    assert sum(path.stat().st_size for path in output.glob("*.wav")) <= 30 * 1024 * 1024


def test_salamander_converter_logs_every_missing_expected_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if an incomplete official source silently produces a partial subset."""
    source = _converter_source_tree(tmp_path, complete=False)
    output = tmp_path / "generated-subset"

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(monkeypatch, source, output)

    assert result != 0
    assert "missing 36 expected source sample(s)" in caplog.text
    assert "A0v6.wav" in caplog.text
    assert "C8v11.wav" in caplog.text
    assert not output.exists()


def test_salamander_converter_rejects_wrong_local_archive_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if regeneration accepts raw input whose recorded local fingerprint is wrong."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, provenance = _converter_provenance(source)
    provenance["locally_computed_sha256"] = "0" * 64
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    output = tmp_path / "generated-subset"

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            output,
            archive=archive,
            provenance=provenance_path,
            policy={**provenance, "locally_computed_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        )

    assert result != 0
    assert "locally_computed_sha256" in caplog.text
    assert "tracked authority" in caplog.text
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("source_size", 999),
        ("source_sha1", "0" * 40),
        ("source_md5", "0" * 32),
        ("locally_computed_sha256", "0" * 64),
    ),
)
def test_salamander_converter_rejects_each_provenance_value_outside_pinned_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    field: str,
    bad_value: object,
) -> None:
    """Fails if ignored provenance can override the tracked source identity policy."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)
    modified = {**policy, field: bad_value}
    provenance_path.write_text(json.dumps(modified), encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            policy=policy,
        )

    assert result != 0
    assert "tracked authority" in caplog.text


def test_salamander_converter_rejects_jointly_modified_archive_and_ignored_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if changing both ignored inputs can bypass the tracked trust anchor."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)
    archive.write_bytes(b"substituted-archive")
    substituted = {
        **policy,
        "source_size": archive.stat().st_size,
        "source_sha1": hashlib.sha1(archive.read_bytes()).hexdigest(),
        "source_md5": hashlib.md5(archive.read_bytes()).hexdigest(),
        "locally_computed_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    provenance_path.write_text(json.dumps(substituted), encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            policy=policy,
        )

    assert result != 0
    assert "tracked authority" in caplog.text


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("source_size", 999),
        ("source_sha1", "0" * 40),
        ("source_md5", "0" * 32),
        ("locally_computed_sha256", "0" * 64),
    ),
)
def test_salamander_converter_computes_each_archive_identity_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    field: str,
    bad_value: object,
) -> None:
    """Fails if the converter trusts matching metadata without hashing the archive."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)
    policy = {**policy, field: bad_value}
    provenance_path.write_text(json.dumps(policy), encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            policy=policy,
        )

    assert result != 0
    assert field in caplog.text
    assert "tracked authority" in caplog.text


@pytest.mark.parametrize("fault", ("missing", "modified", "substituted"))
def test_salamander_converter_rejects_missing_or_different_retained_license(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fault: str,
) -> None:
    """Fails if regeneration proceeds without the retained approved CC BY 3.0 evidence."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)
    license_path = _converter_license(source)
    if fault == "missing":
        license_path.unlink()
    elif fault == "modified":
        license_path.write_text("CC BY 4.0", encoding="utf-8")
    else:
        license_path = source / "README"

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            license_path=license_path,
            policy=policy,
        )

    assert result != 0
    assert "license" in caplog.text.casefold()


def test_salamander_converter_rejects_human_provenance_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if the human-readable provenance can drift from the machine authority."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            policy=policy,
            markdown_overrides={"license": "CC BY 4.0"},
        )

    assert result != 0
    assert "human provenance" in caplog.text


def test_salamander_converter_rejects_human_provenance_heading_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if a changed provenance title is ignored by regeneration."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            policy=policy,
            markdown_transform=lambda text: text.replace(
                HUMAN_PROVENANCE_TITLE,
                "# Unverified Piano Source Provenance",
                1,
            ),
        )

    assert result != 0
    assert "human provenance" in caplog.text


def test_salamander_converter_rejects_human_provenance_sentence_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if free-form license prose can contradict the tracked authority."""
    source = _converter_source_tree(tmp_path, complete=False)
    archive, provenance_path, policy = _converter_provenance(source)

    with caplog.at_level(logging.ERROR, logger="build_salamander_subset"):
        result = _run_converter(
            monkeypatch,
            source,
            tmp_path / "generated-subset",
            archive=archive,
            provenance=provenance_path,
            policy=policy,
            markdown_transform=lambda text: text.replace(
                HUMAN_LICENSE_SENTENCE,
                "The retained license file permits CC BY 4.0 use.",
                1,
            ),
        )

    assert result != 0
    assert "human provenance" in caplog.text


def _run_pitch_boundary_checker(
    manifest: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "check_sample_pitch_boundaries.py"),
            str(manifest),
            "--output-dir",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _load_pitch_boundary_checker():
    path = PROJECT_ROOT / "tools" / "check_sample_pitch_boundaries.py"
    spec = importlib.util.spec_from_file_location("check_sample_pitch_boundaries", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_metric_fixture(path: Path, values: list[int], *, sample_rate: int = 1_000) -> None:
    samples = array("h")
    for value in values:
        samples.extend((value, value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def test_pitch_boundary_checker_renders_every_root_edge_and_reports_metrics(
    tmp_path: Path,
) -> None:
    """Fails if any mapped edge or required acoustic/duration metric is omitted."""
    manifest = _manifest(tmp_path, frames=3_200)
    output = tmp_path / "boundary-evidence"

    result = _run_pitch_boundary_checker(manifest, output)

    assert result.returncode == 0, result.stderr
    report = json.loads((output / "pitch-boundaries.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert len(report["boundaries"]) == 36
    assert {entry["edge"] for entry in report["boundaries"]} == {"low", "high"}
    assert max(entry["semitone_distance"] for entry in report["boundaries"]) == 3
    for entry in report["boundaries"]:
        assert entry["output_duration_seconds"] > 0
        assert entry["peak"] > 0
        assert entry["rms"] > 0
        assert isinstance(entry["spectral_centroid_delta_hz"], float)
        assert entry["duration_error_percent"] <= 2.0
        assert entry["failures"] == []
        assert (output / entry["render_file"]).is_file()


@pytest.mark.parametrize(
    ("name", "values", "distance", "expected_duration", "failure"),
    (
        ("distance", [1_000] * 100, 4, 0.1, "distance_above_three_semitones"),
        ("clip", [32_767] * 100, 0, 0.1, "clipped_frames"),
        ("attack", [0] * 50 + [1_000] * 50, 0, 0.1, "missing_attack"),
        ("duration", [1_000] * 100, 0, 0.2, "duration_error"),
    ),
)
def test_pitch_boundary_checker_fails_each_required_guard_with_fixture_wav(
    tmp_path: Path,
    name: str,
    values: list[int],
    distance: int,
    expected_duration: float,
    failure: str,
) -> None:
    """Fails if a required pitch, clip, attack, or duration guard becomes permissive."""
    checker = _load_pitch_boundary_checker()
    fixture = tmp_path / f"{name}.wav"
    _write_metric_fixture(fixture, values)

    metrics = checker.analyze_wav(
        fixture,
        semitone_distance=distance,
        expected_duration=expected_duration,
        reference_centroid_hz=0.0,
    )

    assert failure in metrics["failures"]


def _write_stereo_wav(path: Path, value: int, *, frames: int = 16) -> str:
    frame = int(value).to_bytes(2, "little", signed=True) * 2
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(32_000)
        output.writeframes(frame * frames)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rms(samples: array) -> float:
    return (sum(value * value for value in samples) / max(1, len(samples))) ** .5


def _manifest(tmp_path: Path, *, frames: int = 16) -> Path:
    roots = []
    for index, (root_midi, root_note) in enumerate(zip(EXPECTED_ROOT_MIDI, ROOT_NOTES)):
        layers = {}
        for layer, offset in (("Soft", 0), ("Hard", 1_000)):
            filename = f"{root_note.replace('#', 's')}-{layer.lower()}.wav"
            checksum = _write_stereo_wav(
                tmp_path / filename,
                100 + index * 10 + offset,
                frames=frames,
            )
            layers[layer] = {
                "file": filename,
                "frame_count": frames,
                "sha256": checksum,
            }
        roots.append(
            {
                "root_midi": root_midi,
                "root_note": root_note,
                "layers": layers,
            }
        )

    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "asset_version": "test-v1",
                "sample_rate": 32_000,
                "channels": 2,
                "sample_width_bytes": 2,
                "sustain": {"loop_free": True},
                "crop": {"source_start_frame": 0, "tail_fade_ms": 120},
                "roots": roots,
            }
        ),
        encoding="utf-8",
    )
    return path


def _change_manifest(path: Path, change: Callable[[dict], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _idle_engine(path: Path, **kwargs) -> SamplePianoEngine:
    """Keep unrelated tests deterministic while production auto-preload is queued."""
    return SamplePianoEngine(path, scheduler=lambda _job: None, **kwargs)


def test_exact_root_map_covers_a0_through_c8_with_three_semitone_maximum(tmp_path: Path) -> None:
    """Fails if a root is reordered/changed or a playable key maps over three semitones."""
    engine = _idle_engine(_manifest(tmp_path))

    assert SAMPLE_ROOT_MIDI == EXPECTED_ROOT_MIDI
    assert engine.root_notes == ROOT_NOTES
    distances = [abs(pitch - engine.nearest_root(pitch)) for pitch in range(21, 109)]
    assert max(distances) == 3


def test_manifest_with_a_root_gap_over_three_semitones_selects_legacy(tmp_path: Path) -> None:
    """Fails if generated manifests can silently stretch a sample over four semitones."""
    path = _manifest(tmp_path)
    _change_manifest(path, lambda payload: payload["roots"].pop(-2))

    engine = _idle_engine(path)

    assert engine.legacy_selected
    assert "three semitones" in engine.failure_reason


@pytest.mark.parametrize(
    ("velocity", "soft", "hard"),
    ((1, 1.0, 0.0), (64, sqrt(0.5), sqrt(0.5)), (127, 0.0, 1.0)),
)
def test_velocity_uses_continuous_equal_power_soft_hard_blend(
    tmp_path: Path, velocity: int, soft: float, hard: float
) -> None:
    """Fails on a layer threshold, non-equal-power curve, or wrong endpoint selection."""
    engine = _idle_engine(_manifest(tmp_path))

    soft_gain, hard_gain = engine.velocity_layer_gains(velocity)

    assert soft_gain == pytest.approx(soft)
    assert hard_gain == pytest.approx(hard)
    assert soft_gain * soft_gain + hard_gain * hard_gain == pytest.approx(1.0)


def test_offline_sample_note_fades_after_note_off_and_ends_continuously(
    tmp_path: Path,
) -> None:
    """Fails if exported Sample Piano audio keeps full gain after noteOff or clicks at its tail."""
    engine = _idle_engine(_manifest(tmp_path, frames=64_000))
    samples = engine.render_note(60, 0.25, 90, sample_rate=32_000)
    note_off = int(0.25 * 32_000) * 2
    tail = samples[note_off:]
    third = len(tail) // 3

    assert any(tail)
    assert _rms(tail[:third]) > _rms(tail[third : 2 * third]) > _rms(tail[2 * third :])
    assert max(abs(value) for value in tail[-8:]) < 10
    assert abs(tail[-1] - tail[-3]) < 10


def test_sample_project_cancels_during_a_long_single_note(tmp_path: Path) -> None:
    """Fails if the sample renderer completes a whole note after cancellation."""
    source_pcm = array("h", [10_000, -10_000]) * int(SAMPLE_RATE * 31)
    decoded = DecodedSample(
        memoryview(source_pcm),
        sample_rate=SAMPLE_RATE,
        channels=2,
        frame_count=len(source_pcm) // 2,
    )
    engine = _idle_engine(
        _manifest(tmp_path), decoder=lambda *_args, **_kwargs: decoded
    )
    cancellation_requested = Event()
    request_thread = Thread(
        target=lambda: (sleep(0.05), cancellation_requested.set())
    )
    note = Note.create(
        id="long-sample-note",
        pitch=60,
        start_time=0.0,
        duration=30.0,
        velocity=90,
    )

    request_thread.start()
    started = perf_counter()
    try:
        samples = engine.render_project(
            (note,), SAMPLE_RATE, should_cancel=cancellation_requested.is_set
        )
    finally:
        request_thread.join()
    elapsed = perf_counter() - started

    assert cancellation_requested.is_set()
    assert samples == array("h")
    assert elapsed < 0.35


def test_sample_offline_render_has_distinct_register_and_velocity_pcm(
    tmp_path: Path,
) -> None:
    """Fails if offline Sample Piano reduces register or velocity to one scaled PCM source."""
    engine = _idle_engine(_manifest(tmp_path, frames=64_000))
    c2 = engine.render_note(36, 0.2, 80, sample_rate=32_000)
    c4_soft = engine.render_note(60, 0.2, 35, sample_rate=32_000)
    c4_hard = engine.render_note(60, 0.2, 120, sample_rate=32_000)
    c6 = engine.render_note(84, 0.2, 80, sample_rate=32_000)

    assert c2 != c4_soft
    assert c4_soft != c4_hard
    assert c4_hard != c6


def test_velocity_blend_has_no_jump_around_the_middle(tmp_path: Path) -> None:
    """Fails if velocity 64 switches layers instead of moving continuously."""
    engine = _idle_engine(_manifest(tmp_path))
    before = engine.velocity_layer_gains(63)
    middle = engine.velocity_layer_gains(64)
    after = engine.velocity_layer_gains(65)

    assert before[0] > middle[0] > after[0]
    assert before[1] < middle[1] < after[1]
    assert max(abs(a - b) for a, b in zip(before, middle)) < 0.02
    assert max(abs(a - b) for a, b in zip(middle, after)) < 0.02


def test_pitch_ratio_and_register_pan_are_bounded(tmp_path: Path) -> None:
    """Fails if transposition is not 12-TET or keyboard pan becomes distracting."""
    engine = _idle_engine(_manifest(tmp_path))

    assert engine.pitch_ratio(60, 57) == pytest.approx(2 ** (3 / 12))
    pans = [engine.pan_for_pitch(pitch) for pitch in range(21, 109)]
    assert pans[0] == pytest.approx(-0.12)
    assert abs(pans[43]) < 0.01
    assert pans[-1] == pytest.approx(0.12)
    assert max(abs(pan) for pan in pans) <= 0.12


def test_same_root_voices_share_immutable_layer_buffers(tmp_path: Path) -> None:
    """Fails if each note copies decoded PCM or exposes a mutable buffer."""
    engine = _idle_engine(_manifest(tmp_path))
    assert engine.preload_roots_sync((57,))

    first = engine.note_on(56, 40)
    second = engine.note_on(58, 100)

    assert isinstance(first, SampleVoice)
    assert isinstance(second, SampleVoice)
    assert first.soft_buffer.samples.readonly
    assert first.soft_buffer.samples.nbytes == 16 * 2 * 2
    assert first.soft_buffer is second.soft_buffer
    assert first.hard_buffer is second.hard_buffer
    engine.release_voice(first)
    engine.release_voice(second)


def test_c3_c5_preload_respects_byte_limit_and_reports_requested_roots(tmp_path: Path) -> None:
    """Fails if middle-register preload is implicit, unbounded, or includes unrelated roots."""
    root_bytes = 16 * 2 * 2 * 2
    engine = _idle_engine(
        _manifest(tmp_path), cache_byte_limit=root_bytes * 4
    )

    assert engine.c3_c5_root_midis == (45, 51, 57, 63, 66, 69)
    assert engine.preload_c3_c5_sync()
    assert engine.cache_bytes <= root_bytes * 4


def test_lru_evicts_only_unreferenced_roots_and_releases_after_voice_off(tmp_path: Path) -> None:
    """Fails if eviction invalidates an active voice or retained roots cannot later leave."""
    root_bytes = 16 * 2 * 2 * 2
    engine = _idle_engine(
        _manifest(tmp_path), cache_byte_limit=root_bytes * 2
    )
    assert engine.preload_roots_sync((21,))
    held = engine.note_on(21, 64)
    assert isinstance(held, SampleVoice)

    assert engine.preload_roots_sync((24, 27))
    assert set(engine.cached_root_midis) == {21, 27}

    engine.release_voice(held)
    assert engine.preload_roots_sync((33,))
    assert set(engine.cached_root_midis) == {27, 33}
    assert engine.cache_bytes <= root_bytes * 2


def test_note_on_cache_miss_only_schedules_and_never_decodes_synchronously(tmp_path: Path) -> None:
    """Fails if the input path performs WAV I/O/decode instead of returning one Legacy event."""
    decode_calls: list[Path] = []
    scheduled: list[Callable[[], None]] = []

    def decoder(path: Path, *, sample_rate: int, channels: int, frame_count: int):
        decode_calls.append(path)
        return decode_wav(
            path,
            sample_rate=sample_rate,
            channels=channels,
            frame_count=frame_count,
        )

    engine = SamplePianoEngine(
        _manifest(tmp_path),
        decoder=decoder,
        scheduler=scheduled.append,
    )

    cold = engine.note_on(60, 80)

    assert isinstance(cold, LegacyVoice)
    assert decode_calls == []
    assert len(scheduled) == 6

    for job in scheduled:
        job()
    warm = engine.note_on(60, 80)

    assert isinstance(warm, SampleVoice)
    assert len(decode_calls) == 12
    engine.release_voice(warm)


def test_discarded_bank_skips_decode_for_a_previously_scheduled_root(tmp_path: Path) -> None:
    """Fails if an inactive bank still decodes a root queued before cache eviction."""
    decode_calls: list[Path] = []
    scheduled: list[Callable[[], None]] = []

    def decoder(path: Path, *, sample_rate: int, channels: int, frame_count: int):
        decode_calls.append(path)
        return decode_wav(
            path,
            sample_rate=sample_rate,
            channels=channels,
            frame_count=frame_count,
        )

    engine = SamplePianoEngine(
        _manifest(tmp_path),
        decoder=decoder,
        scheduler=scheduled.append,
        preload_c3_c5_on_init=False,
    )

    assert isinstance(engine.note_on(60, 80), LegacyVoice)
    assert len(scheduled) == 1
    engine.discard_idle_cache()

    scheduled.pop()()

    assert decode_calls == []


def test_stale_warmup_decode_failure_does_not_select_legacy(tmp_path: Path) -> None:
    """Fails if a discarded asynchronous warmup turns a transient old-task error fatal."""
    holder: dict[str, SamplePianoEngine] = {}

    def decoder(_path: Path, **_kwargs) -> DecodedSample:
        holder["engine"].discard_idle_cache()
        raise OSError("interrupted old-bank read")

    engine = SamplePianoEngine(
        _manifest(tmp_path),
        decoder=decoder,
        scheduler=lambda job: job(),
        preload_c3_c5_on_init=False,
    )
    holder["engine"] = engine

    engine.preload_c3_c5_async()

    assert not engine.legacy_selected


def test_active_bank_warmup_is_not_cancelled_by_a_repeat_activation(tmp_path: Path) -> None:
    """Fails if committing an overlapping tone pair restarts its active bank preload."""
    scheduled: list[Callable[[], None]] = []
    engine = SamplePianoEngine(
        _manifest(tmp_path),
        scheduler=scheduled.append,
        preload_c3_c5_on_init=False,
    )

    engine.preload_c3_c5_async()
    engine.activate_cache()
    for job in tuple(scheduled):
        job()

    assert len(scheduled) == 6
    assert engine.cached_root_midis == engine.c3_c5_root_midis


def test_reactivated_bank_requeues_a_root_cancelled_by_an_older_warmup(
    tmp_path: Path,
) -> None:
    """Fails if an old cancelled job prevents a reactivated bank from preloading again."""
    scheduled: list[Callable[[], None]] = []
    engine = SamplePianoEngine(
        _manifest(tmp_path),
        scheduler=scheduled.append,
        preload_c3_c5_on_init=False,
    )

    assert isinstance(engine.note_on(60, 80), LegacyVoice)
    engine.discard_idle_cache()
    engine.activate_cache()
    engine.preload_c3_c5_async()
    scheduled.pop(0)()

    assert len(scheduled) == 6
    for job in tuple(scheduled):
        job()
    assert 57 in engine.cached_root_midis


def test_discarded_bank_releases_the_last_active_root_after_voice_finished(
    tmp_path: Path,
) -> None:
    """Fails if a switched-away bank retains PCM after its final audible voice ends."""
    engine = _idle_engine(_manifest(tmp_path))
    assert engine.preload_roots_sync((57,))
    voice = engine.note_on(60, 80)
    assert isinstance(voice, SampleVoice)

    engine.discard_idle_cache()
    assert engine.cached_root_midis == (57,)
    engine.release_voice(voice)

    assert engine.cached_root_midis == ()


def test_inactive_bank_still_renders_an_existing_snapshot_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if cache eviction turns an already-published offline snapshot into Legacy."""
    engine = _idle_engine(_manifest(tmp_path))
    assert engine.preload_roots_sync((57,))
    engine.discard_idle_cache()
    monkeypatch.setattr(
        "ai_piano_studio.audio.sample_piano.render_legacy_note",
        lambda *_args: pytest.fail("inactive snapshot rendered Legacy PCM"),
    )

    rendered = engine.render_note(60, 0.01, 80)

    assert not engine.legacy_selected
    assert rendered


def test_valid_construction_schedules_exact_c3_c5_roots_without_sync_decode(
    tmp_path: Path,
) -> None:
    """Fails if startup omits middle preload or performs asset I/O on the caller."""
    decode_calls: list[Path] = []
    scheduled: list[Callable[[], None]] = []

    def decoder(path: Path, *, sample_rate: int, channels: int, frame_count: int):
        decode_calls.append(path)
        return decode_wav(
            path,
            sample_rate=sample_rate,
            channels=channels,
            frame_count=frame_count,
        )

    engine = SamplePianoEngine(
        _manifest(tmp_path),
        decoder=decoder,
        scheduler=scheduled.append,
    )

    assert engine.c3_c5_root_midis == (45, 51, 57, 63, 66, 69)
    assert decode_calls == []
    assert len(scheduled) == 6

    cold = engine.note_on(60, 80)

    assert isinstance(cold, LegacyVoice)
    assert decode_calls == []
    assert len(scheduled) == 6

    for job in scheduled:
        job()

    decoded_roots = {path.name.rsplit("-", 1)[0] for path in decode_calls}
    assert decoded_roots == {"A2", "Ds3", "A3", "Ds4", "Fs4", "A4"}
    assert len(decode_calls) == 12
    warm = engine.note_on(60, 80)
    assert isinstance(warm, SampleVoice)
    assert len(decode_calls) == 12
    engine.release_voice(warm)


@pytest.mark.parametrize("fault", ("malformed", "missing", "checksum", "decode"))
def test_asset_fault_logs_once_and_atomically_selects_legacy_for_session(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fault: str,
) -> None:
    """Fails if a bad asset retries/log-spams or allows mixed session source state."""
    path = _manifest(tmp_path)
    if fault == "malformed":
        _change_manifest(path, lambda payload: payload.pop("asset_version"))
    elif fault == "missing":
        (tmp_path / "A0-soft.wav").unlink()
    elif fault == "checksum":
        _change_manifest(
            path,
            lambda payload: payload["roots"][0]["layers"]["Soft"].__setitem__(
                "sha256", "0" * 64
            ),
        )
    else:
        broken = tmp_path / "A0-soft.wav"
        broken.write_bytes(b"not a wav")
        checksum = hashlib.sha256(broken.read_bytes()).hexdigest()
        _change_manifest(
            path,
            lambda payload: payload["roots"][0]["layers"]["Soft"].__setitem__(
                "sha256", checksum
            ),
        )

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.sample_piano"):
        engine = _idle_engine(path)
        if not engine.legacy_selected:
            assert not engine.preload_roots_sync((21,))
        first_reason = engine.failure_reason
        assert isinstance(engine.note_on(60, 64), LegacyVoice)
        assert not engine.preload_roots_sync((24,))
        assert not engine.preload_c3_c5_sync()

    failures = [record for record in caplog.records if "Legacy Piano" in record.message]
    assert engine.legacy_selected
    assert first_reason
    assert len(failures) == 1


def test_source_facade_keeps_legacy_renderer_and_sample_engine_interchangeable(
    tmp_path: Path,
) -> None:
    """Fails if callers cannot use one public source contract without changing defaults."""
    legacy = LegacyPianoSource()
    sample = SamplePianoSource(
        SamplePianoEngine(_manifest(tmp_path), scheduler=lambda _job: None)
    )

    assert isinstance(legacy, PianoSoundSource)
    assert isinstance(sample, PianoSoundSource)
    assert legacy.render_note(60, 0.01, 64).typecode == "h"
    voice = sample.start_voice("sample", 60, 64)
    assert isinstance(voice, LegacyVoice)
    sample.note_off("sample")
    sample.release_all()
    assert SAMPLE_PIANO_V1.cache_byte_limit > 0


class _ToneCoordinator:
    def __init__(self, selection: PianoToneSelection) -> None:
        self.current = selection

    def selection(self) -> PianoToneSelection:
        return self.current

    def commit(self, position: float) -> PianoToneSelection:
        self.current = PianoToneSelection(
            position,
            self.current.primary,
            1.0,
            None,
            0.0,
            "bank",
        )
        return self.current


def test_tone_snapshot_keeps_old_voice_after_new_tone_commit(tmp_path: Path) -> None:
    """Fails if active voices observe a later mutable tone selection."""
    engine = _idle_engine(_manifest(tmp_path, frames=4_096))
    assert engine.preload_roots_sync((57,))
    coordinator = _ToneCoordinator(PianoToneSelection(0.25, engine, 1.0, None, 0.0, "bank"))
    mixer = PianoVoiceMixer(sample_rate=32_000)
    source = ContinuousPianoToneSource(coordinator, mixer)

    old = source.start_voice("old", 60, 64)
    source.set_tone(0.75)
    new = source.start_voice("new", 60, 64)

    assert old.selection.position == 0.25
    assert new.selection.position == 0.75


def test_offline_tone_snapshot_equal_power_blends_both_committed_banks() -> None:
    """Fails if offline playback/export silently renders only the primary bank."""
    class RenderEngine:
        legacy_selected = False

        def __init__(self, value: int) -> None:
            self.value = value

        def render_note(self, *_args):
            return array("h", [self.value, self.value])

        def render_project(self, _notes, _sample_rate):
            return array("h", [self.value, self.value])

    first = RenderEngine(1000)
    second = RenderEngine(2000)
    selection = PianoToneSelection(
        0.25, first, 0.8, second, 0.6, "first", "second"
    )
    coordinator = _ToneCoordinator(selection)
    source = ContinuousPianoToneSource(coordinator, PianoVoiceMixer())
    snapshot = source.snapshot_renderer()
    coordinator.current = PianoToneSelection(
        0.75, second, 1.0, None, 0.0, "second"
    )

    assert snapshot.render_note(60, 0.2, 80) == array("h", [2000, 2000])
    assert snapshot.render_project(()) == array("h", [2000, 2000])


def test_continuous_source_declares_snapshot_pcm_channel_count() -> None:
    """Fails if direct continuous rendering writes stereo PCM under a mono WAV header."""
    class RenderEngine:
        legacy_selected = False

        @staticmethod
        def render_note(*_args):
            return array("h", [1000, 1000])

        @staticmethod
        def render_project(*_args):
            return array("h", [1000, 1000])

    selection = PianoToneSelection(0.0, RenderEngine(), 1.0, None, 0.0, "bank")
    source = ContinuousPianoToneSource(_ToneCoordinator(selection), PianoVoiceMixer())

    assert source.output_channels == source.snapshot_renderer().output_channels == 2


@pytest.mark.parametrize("intermediate", (False, True), ids=("exact", "intermediate"))
def test_offline_snapshot_late_bank_failure_writes_safe_mono_duration(
    tmp_path: Path, intermediate: bool
) -> None:
    """Fails if a late mono Legacy render is mislabeled or mixed as stereo."""
    class LateFailureEngine:
        legacy_selected = False

        def render_note(self, pitch, duration, velocity, sample_rate):
            self.legacy_selected = True
            return LegacyPianoSource().render_note(
                pitch, duration, velocity, sample_rate
            )

        def render_project(self, notes, sample_rate):
            self.legacy_selected = True
            return LegacyPianoSource().render_project(notes, sample_rate)

    primary = LateFailureEngine()
    secondary = LateFailureEngine() if intermediate else None
    selection = PianoToneSelection(
        0.25 if intermediate else 0.0,
        primary,
        sqrt(0.5) if intermediate else 1.0,
        secondary,
        sqrt(0.5) if intermediate else 0.0,
        "first",
        "second" if intermediate else None,
    )
    snapshot = ContinuousPianoToneSource(
        _ToneCoordinator(selection), PianoVoiceMixer()
    ).snapshot_renderer()
    samples = snapshot.render_note(60, 0.2, 80)
    target = tmp_path / "late-fallback.wav"
    write_wav(target, samples, channels=snapshot.output_channels)

    with wave.open(str(target), "rb") as rendered:
        assert rendered.getnchannels() == 1
        assert rendered.getnframes() == len(samples)
        assert rendered.getnframes() / SAMPLE_RATE == pytest.approx(
            len(samples) / SAMPLE_RATE
        )


def test_continuous_source_uses_playable_legacy_when_all_banks_are_unavailable() -> None:
    """Fails if total sample failure leaves realtime and export routes silent."""
    class UnavailableCoordinator:
        @staticmethod
        def selection():
            return None

        @staticmethod
        def commit(_position):
            return None

    mixer = PianoVoiceMixer(sample_rate=48_000)
    source = ContinuousPianoToneSource(UnavailableCoordinator(), mixer)

    voice = source.start_voice("fallback", 60, 80)
    mixed = array("h")
    mixed.frombytes(mixer.mix_frames(512))
    rendered = source.snapshot_renderer().render_note(60, 0.05, 80)

    assert isinstance(voice, LegacyVoice)
    assert any(mixed)
    assert any(rendered)


def test_continuous_source_falls_back_for_midi_pitch_below_sample_range(
    tmp_path: Path,
) -> None:
    """Fails if a valid MIDI low note crashes playback when samples start at A0."""
    engine = _idle_engine(_manifest(tmp_path, frames=4_096))
    assert engine.preload_roots_sync((57,))
    mixer = PianoVoiceMixer(sample_rate=48_000)
    source = ContinuousPianoToneSource(
        _ToneCoordinator(
            PianoToneSelection(0.0, engine, 1.0, None, 0.0, "bank")
        ),
        mixer,
        LegacyPianoSource(realtime_scheduler=lambda _job: None),
    )

    voice = source.start_voice("low-midi", 20, 80)

    assert isinstance(voice, LegacyVoice)
    mixed = array("h")
    mixed.frombytes(mixer.mix_frames(256))
    assert any(mixed)

    source.note_off("low-midi", ReleaseMode.STOP)


def test_realtime_legacy_fallback_queues_rendering_without_blocking_note_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a cold fallback synchronously synthesizes four seconds of Legacy PCM."""
    scheduled: list[Callable[[], None]] = []
    render_calls: list[tuple[object, ...]] = []

    def record_render(*args, **_kwargs):
        render_calls.append(args)
        return array("h", [1, -1])

    monkeypatch.setattr(source_module, "render_legacy_note", record_render)
    legacy = LegacyPianoSource(realtime_scheduler=scheduled.append)
    mixer = PianoVoiceMixer(sample_rate=48_000)
    source = ContinuousPianoToneSource(
        type("UnavailableCoordinator", (), {"selection": staticmethod(lambda: None)})(),
        mixer,
        legacy,
    )

    voice = source.start_voice("fallback", 60, 80)

    assert isinstance(voice, LegacyVoice)
    assert voice.source is not None
    assert any(voice.source)
    assert render_calls == []
    assert len(scheduled) == 1


def test_realtime_legacy_fallback_stays_looping_when_warmup_finishes_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if cache publication races with marking the fallback oscillator non-looping."""
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=lambda job: job())

    voice = legacy.start_voice("fallback-race", 60, 80)

    assert voice.looping


def test_realtime_legacy_cached_pcm_stays_active_until_note_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a held cache-hit Legacy voice ends when its warmup PCM is exhausted."""
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=lambda job: job())
    legacy.start_voice("warm-cache", 60, 80)
    voice = legacy.start_voice("held-cache", 60, 80)
    mixer = PianoVoiceMixer(sample_rate=source_module.SAMPLE_RATE)

    assert voice.source is not None
    mixer.start_voice(
        "held-cache",
        voice.pitch,
        voice.velocity,
        voice.source,
        source_sample_rate=voice.sample_rate,
        owner_tag="legacy-piano",
        looping=voice.looping,
    )
    mixer.mix_frames(len(voice.source) + 1)

    assert mixer.active_voice_ids == ("held-cache",)


def test_realtime_legacy_warmup_limits_pending_render_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a velocity-varying fallback passage queues unbounded Legacy synthesis."""
    scheduled: list[Callable[[], None]] = []
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=scheduled.append)

    legacy.start_voice("fallback-1", 60, 64)
    legacy.start_voice("fallback-2", 61, 65)

    assert len(scheduled) == 1


def test_realtime_legacy_warmup_queue_has_a_fixed_entry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if continuous fallback queues more synthesis than its bounded worker can absorb."""
    scheduled: list[Callable[[], None]] = []
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=scheduled.append)

    for index in range(source_module._LEGACY_REALTIME_WARMUP_QUEUE_ENTRIES + 1):
        legacy.start_voice(f"fallback-queue-{index}", index, 64)

    assert len(legacy._warmup_queue) == source_module._LEGACY_REALTIME_WARMUP_QUEUE_ENTRIES
    assert len(scheduled) == 1


def test_realtime_legacy_warmup_cache_has_a_fixed_entry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if completed Legacy warmups grow the realtime PCM cache without bound."""
    scheduled: list[Callable[[], None]] = []
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=scheduled.append)

    for index in range(source_module._LEGACY_REALTIME_CACHE_ENTRIES + 1):
        legacy.start_voice(f"fallback-cache-{index}", 60, index)
        scheduled.pop()()

    assert len(legacy._realtime_cache) == source_module._LEGACY_REALTIME_CACHE_ENTRIES


@pytest.mark.parametrize("pitch", (102, 127))
def test_realtime_legacy_fallback_preserves_high_register_pitch(
    monkeypatch: pytest.MonkeyPatch,
    pitch: int,
) -> None:
    """Fails if high MIDI fallback loops collapse into one fixed 16-frame frequency."""
    scheduled: list[Callable[[], None]] = []
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=scheduled.append)

    voice = legacy.start_voice(f"fallback-high-{pitch}", pitch, 80)

    assert voice.source is not None
    observed_frequency = voice.sample_rate / len(voice.source)
    expected_frequency = 440.0 * 2 ** ((pitch - 69) / 12)
    assert observed_frequency == pytest.approx(expected_frequency, rel=0.002)


def test_realtime_legacy_fallback_reuses_immutable_pcm_in_the_mixer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if fallback start_voice copies shared immutable PCM into a tuple per note."""
    scheduled: list[Callable[[], None]] = []
    monkeypatch.setattr(
        source_module,
        "render_legacy_note",
        lambda *_args, **_kwargs: array("h", [100, -100]),
    )
    legacy = LegacyPianoSource(realtime_scheduler=scheduled.append)
    mixer = PianoVoiceMixer(sample_rate=48_000)
    source = ContinuousPianoToneSource(
        type("UnavailableCoordinator", (), {"selection": staticmethod(lambda: None)})(),
        mixer,
        legacy,
    )

    voice = source.start_voice("fallback-shared", 60, 80)
    mixer.mix_frames(1)

    assert mixer._voices[0].contributions[0].samples is voice.source


def test_exact_landmark_has_one_bank_two_contributions_and_intermediate_has_four(
    tmp_path: Path,
) -> None:
    """Fails if tone and velocity dimensions do not form 2/4 shared PCM inputs."""
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = _idle_engine(_manifest(first_path, frames=4_096))
    second = _idle_engine(_manifest(second_path, frames=4_096))
    assert first.preload_roots_sync((57,))
    assert second.preload_roots_sync((57,))
    mixer = PianoVoiceMixer(sample_rate=32_000)
    coordinator = _ToneCoordinator(PianoToneSelection(0.0, first, 1.0, None, 0.0, "first"))
    source = ContinuousPianoToneSource(coordinator, mixer)

    exact = source.start_voice("exact", 60, 64)
    coordinator.current = PianoToneSelection(
        0.5, first, sqrt(0.5), second, sqrt(0.5), "first", "second"
    )
    blended = source.start_voice("blend", 60, 64)
    mixer.mix_frames(1)

    voices = {voice.voice_id: voice for voice in mixer._voices}
    assert len(exact.bank_voices) == 1
    assert len(blended.bank_voices) == 2
    assert len(voices["exact"].contributions) == 2
    assert len(voices["blend"].contributions) == 4
    assert voices["exact"].contributions[0].samples is exact.bank_voices[0][1].soft_buffer.samples
    assert voices["blend"].contributions[2].samples is blended.bank_voices[1][1].soft_buffer.samples


def test_velocity_endpoint_omits_zero_gain_layer_for_one_contribution(
    tmp_path: Path,
) -> None:
    """Fails if an exact tone/velocity landmark retains a silent PCM reader."""
    engine = _idle_engine(_manifest(tmp_path, frames=4_096))
    assert engine.preload_roots_sync((57,))
    mixer = PianoVoiceMixer(sample_rate=32_000)
    coordinator = _ToneCoordinator(
        PianoToneSelection(0.0, engine, 1.0, None, 0.0, "bank")
    )
    source = ContinuousPianoToneSource(coordinator, mixer)

    source.start_voice("soft", 60, 1)
    mixer.mix_frames(1)

    assert len(mixer._voices[0].contributions) == 1


def test_source_releases_engine_tokens_only_after_release_pcm_finishes(tmp_path: Path) -> None:
    """Fails if note_off releases cached engine ownership before the audible tail ends."""
    engine = _idle_engine(_manifest(tmp_path, frames=32_000))
    assert engine.preload_roots_sync((57,))
    mixer = PianoVoiceMixer(sample_rate=32_000)
    source = SamplePianoSource(engine, mixer)
    released: list[int] = []
    original_release = engine.release_voice

    def record_release(voice: SampleVoice | LegacyVoice) -> None:
        if isinstance(voice, SampleVoice):
            released.append(voice.token)
        original_release(voice)

    engine.release_voice = record_release  # type: ignore[method-assign]
    voice = source.start_voice("voice", 60, 90)
    assert isinstance(voice, SampleVoice)
    mixer.mix_frames(8)

    source.note_off("voice", ReleaseMode.NATURAL)
    assert released == []
    for _ in range(100):
        mixer.mix_frames(1_024)
        if released:
            break

    assert released == [voice.token]


def test_continuous_source_rolls_back_first_bank_if_second_bank_start_fails(
    tmp_path: Path,
) -> None:
    """Fails if a partial multi-bank start leaks the first engine token."""
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = _idle_engine(_manifest(first_path, frames=4_096))
    second = _idle_engine(_manifest(second_path, frames=4_096))
    assert first.preload_roots_sync((57,))
    assert second.preload_roots_sync((57,))
    released: list[int] = []
    original_release = first.release_voice

    def record_release(voice: SampleVoice | LegacyVoice) -> None:
        if isinstance(voice, SampleVoice):
            released.append(voice.token)
        original_release(voice)

    first.release_voice = record_release  # type: ignore[method-assign]

    def fail_note_on(_pitch: int, _velocity: int) -> SampleVoice:
        raise RuntimeError("second bank failed")

    second.note_on = fail_note_on  # type: ignore[method-assign]
    selection = PianoToneSelection(
        0.5, first, sqrt(0.5), second, sqrt(0.5), "first", "second"
    )
    source = ContinuousPianoToneSource(_ToneCoordinator(selection), PianoVoiceMixer())

    with pytest.raises(RuntimeError, match="second bank"):
        source.start_voice("voice", 60, 64)

    assert released == [1]


def test_sample_source_rolls_back_token_when_mixer_rejects_start(tmp_path: Path) -> None:
    """Fails if mixer validation failure leaves a sample cache token retained."""
    engine = _idle_engine(_manifest(tmp_path, frames=4_096))
    assert engine.preload_roots_sync((57,))
    mixer = PianoVoiceMixer(sample_rate=32_000)
    source = SamplePianoSource(engine, mixer)

    def fail_start(*_args, **_kwargs) -> None:
        raise RuntimeError("mixer failed")

    mixer.start_contributions = fail_start  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="mixer"):
        source.start_voice("voice", 60, 64)

    assert engine._active_tokens == {}


def test_cold_sample_source_mixes_playable_legacy_and_finishes_once(tmp_path: Path) -> None:
    """Fails if a warming cache miss is tracked without playable mixer PCM."""
    engine = _idle_engine(_manifest(tmp_path, frames=4_096))
    mixer = PianoVoiceMixer(sample_rate=48_000)
    source = SamplePianoSource(engine, mixer)
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)

    voice = source.start_voice("cold", 60, 64)
    mixed = array("h")
    mixed.frombytes(mixer.mix_frames(2_048))

    assert isinstance(voice, LegacyVoice)
    assert any(mixed)
    source.note_off("cold", ReleaseMode.STOP)
    mixer.mix_frames(578)
    mixer.mix_frames(2)
    assert finished == ["cold"]
    assert source._voices == {}


def test_continuous_tone_uses_available_bank_when_adjacent_bank_is_warming(
    tmp_path: Path,
) -> None:
    """Fails if one warming adjacent bank rejects an otherwise playable selection."""
    warm_path = tmp_path / "warm"
    cold_path = tmp_path / "cold"
    warm_path.mkdir()
    cold_path.mkdir()
    warm = _idle_engine(_manifest(warm_path, frames=4_096))
    cold = _idle_engine(_manifest(cold_path, frames=4_096))
    assert warm.preload_roots_sync((57,))
    selection = PianoToneSelection(
        0.5, warm, sqrt(0.5), cold, sqrt(0.5), "warm", "cold"
    )
    mixer = PianoVoiceMixer(sample_rate=32_000)
    source = ContinuousPianoToneSource(_ToneCoordinator(selection), mixer)

    voice = source.start_voice("partial", 60, 64)
    mixer.mix_frames(1)

    assert isinstance(voice.bank_voices[0][1], SampleVoice)
    assert isinstance(voice.bank_voices[1][1], LegacyVoice)
    assert len(mixer._voices[0].contributions) == 2
    assert mixer._voices[0].contributions[0].samples is voice.bank_voices[0][1].soft_buffer.samples
    source.note_off("partial", ReleaseMode.STOP)
    mixer.mix_frames(386)
    assert source._voices == {}
    assert warm._active_tokens == {}
    assert cold._active_tokens == {}


def test_continuous_tone_uses_complete_legacy_fallback_for_a_failed_bank(
    tmp_path: Path,
) -> None:
    """Fails if a failed crossfade bank leaves the surviving bank at half gain."""
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = _idle_engine(_manifest(first_path, frames=4_096))
    failed = _idle_engine(_manifest(second_path, frames=4_096))
    assert first.preload_roots_sync((57,))
    failed._select_legacy("late preload failure")
    mixer = PianoVoiceMixer(sample_rate=32_000)
    source = ContinuousPianoToneSource(
        _ToneCoordinator(
            PianoToneSelection(0.5, first, sqrt(0.5), failed, sqrt(0.5), "first", "failed")
        ),
        mixer,
        LegacyPianoSource(realtime_scheduler=lambda _job: None),
    )

    voice = source.start_voice("failed-bank", 60, 64)
    mixer.mix_frames(1)

    assert isinstance(voice, LegacyVoice)
    assert len(mixer._voices[0].contributions) == 1
    assert first._active_tokens == {}


def test_continuous_tone_all_warming_banks_mix_one_legacy_fallback_and_cleanup(
    tmp_path: Path,
) -> None:
    """Fails if an all-warming selection throws, stays silent, or leaves pending state."""
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = _idle_engine(_manifest(first_path, frames=4_096))
    second = _idle_engine(_manifest(second_path, frames=4_096))
    selection = PianoToneSelection(
        0.5, first, sqrt(0.5), second, sqrt(0.5), "first", "second"
    )
    mixer = PianoVoiceMixer(sample_rate=48_000)
    source = ContinuousPianoToneSource(_ToneCoordinator(selection), mixer)
    finished: list[str] = []
    mixer.voiceFinished.connect(finished.append)

    voice = source.start_voice("fallback", 60, 64)
    mixed = array("h")
    mixed.frombytes(mixer.mix_frames(2_048))

    assert all(isinstance(item, LegacyVoice) for _, item in voice.bank_voices)
    assert len(mixer._voices[0].contributions) == 1
    assert any(mixed)
    source.note_off("fallback", ReleaseMode.STOP)
    mixer.mix_frames(578)
    mixer.mix_frames(2)
    assert finished == ["fallback"]
    assert source._voices == {}
    assert first._active_tokens == {}
    assert second._active_tokens == {}


def test_source_render_error_is_logged_before_it_propagates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fails if a source boundary raises without the required diagnostic log."""
    source = LegacyPianoSource()

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.source"):
        with pytest.raises(ValueError, match="duration"):
            source.render_note(60, 0.0, 64)

    assert "Failed to render Legacy Piano note" in caplog.text
