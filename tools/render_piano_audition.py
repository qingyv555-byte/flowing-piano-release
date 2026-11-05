"""Render Legacy and continuous three-bank piano audition evidence."""

from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_piano_studio.audio.source import ContinuousPianoToneSource, LegacyPianoSource
from ai_piano_studio.audio.tone import PianoToneCoordinator
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.synth import SAMPLE_RATE
from ai_piano_studio.domain.note import Note


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_TIMESTAMP = "2026-08-11T00:00:00+00:00"
TONE_POSITIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True, slots=True)
class AuditionFixture:
    stem: str
    notes: tuple[Note, ...]


def _note(note_id: str, pitch: int, start_time: float, duration: float, velocity: int) -> Note:
    return Note.create(
        id=note_id,
        pitch=pitch,
        start_time=start_time,
        duration=duration,
        velocity=velocity,
        created_at=_FIXTURE_TIMESTAMP,
        updated_at=_FIXTURE_TIMESTAMP,
    )


AUDITION_SCORE = (
    AuditionFixture("c2", (_note("c2", 36, 0.0, 0.70, 80),)),
    AuditionFixture("c4", (_note("c4", 60, 0.0, 0.70, 80),)),
    AuditionFixture("c6", (_note("c6", 84, 0.0, 0.70, 80),)),
    AuditionFixture("c4-soft", (_note("c4-soft", 60, 0.0, 0.70, 35),)),
    AuditionFixture("c4-medium", (_note("c4-medium", 60, 0.0, 0.70, 80),)),
    AuditionFixture("c4-hard", (_note("c4-hard", 60, 0.0, 0.70, 120),)),
    AuditionFixture(
        "c-major",
        (
            _note("c-major-c", 60, 0.0, 1.00, 96),
            _note("c-major-e", 64, 0.0, 1.00, 96),
            _note("c-major-g", 67, 0.0, 1.00, 96),
        ),
    ),
    AuditionFixture(
        "a-minor",
        (
            _note("a-minor-a", 57, 0.0, 1.00, 96),
            _note("a-minor-c", 60, 0.0, 1.00, 96),
            _note("a-minor-e", 64, 0.0, 1.00, 96),
        ),
    ),
    AuditionFixture(
        "f-major",
        (
            _note("f-major-f", 53, 0.0, 1.00, 96),
            _note("f-major-a", 57, 0.0, 1.00, 96),
            _note("f-major-c", 60, 0.0, 1.00, 96),
        ),
    ),
    AuditionFixture(
        "rapid-c4",
        tuple(
            _note(f"rapid-{index + 1}", 60, index * 0.15, 0.12, 100)
            for index in range(6)
        ),
    ),
    AuditionFixture(
        "short-melody",
        (
            _note("melody-1", 60, 0.00, 0.30, 84),
            _note("melody-2", 64, 0.35, 0.30, 88),
            _note("melody-3", 67, 0.70, 0.30, 92),
            _note("melody-4", 72, 1.05, 0.50, 96),
            _note("melody-5", 67, 1.60, 0.25, 88),
            _note("melody-6", 64, 1.90, 0.25, 84),
            _note("melody-7", 60, 2.20, 0.60, 92),
        ),
    ),
    AuditionFixture("release-test", (_note("release", 60, 0.0, 0.20, 100),)),
)


def render_audition_set(
    output_dir: Path,
    catalog_path: Path,
) -> list[Path]:
    """Render the common score through Legacy and every formal tone position."""
    try:
        return _render_audition_set(output_dir, catalog_path)
    except Exception:
        logger.exception(
            "Failed to render piano audition set output=%s catalog=%s",
            output_dir,
            catalog_path,
        )
        raise


def _render_audition_set(
    output_dir: Path,
    catalog_path: Path,
) -> list[Path]:
    output = output_dir.expanduser().resolve()
    catalog = catalog_path.expanduser().resolve(strict=True)
    coordinator = PianoToneCoordinator(catalog)
    sources: list[tuple[str, str, object, int, float | None]] = [
        ("legacy", "LegacyPianoSource", LegacyPianoSource(), 1, None)
    ]
    for position in TONE_POSITIONS:
        selection = coordinator.commit(position)
        if selection is None:
            raise ValueError(f"continuous tone {position:.2f} has no formal selection")
        sources.append(
            (
                f"tone-{position:.2f}",
                "ContinuousPianoToneSource",
                ContinuousPianoToneSource(coordinator, PianoVoiceMixer(sample_rate=SAMPLE_RATE)),
                2,
                position,
            )
        )
    paths: list[Path] = []
    score = _score_report()
    for directory, engine_name, source, channels, position in sources:
        if position is not None:
            selection = coordinator.commit(position)
            if selection is None:
                raise ValueError(f"continuous tone {position:.2f} has no formal selection")
        rendered: list[tuple[AuditionFixture, array]] = []
        for fixture in AUDITION_SCORE:
            samples = source.render_project(fixture.notes, SAMPLE_RATE)
            if position is not None and any(
                engine.legacy_selected
                for engine in (selection.primary,)
                + (() if selection.secondary is None else (selection.secondary,))
            ):
                raise ValueError(
                    "continuous tone selected Legacy Piano during "
                    f"{fixture.stem} at {position:.2f}"
                )
            rendered.append((fixture, samples))
        engine_dir = output / directory
        _prepare_output_directory(engine_dir, include_readme=False)
        files: list[dict[str, object]] = []
        for fixture, samples in rendered:
            path = engine_dir / f"{fixture.stem}.wav"
            _write_pcm_wav(path, samples, channels=channels, sample_rate=SAMPLE_RATE)
            paths.append(path)
            files.append(_file_report(path, fixture.stem))

        report: dict[str, object] = {
            "engine": engine_name,
            "catalog": str(catalog),
            "score": score,
            "files": files,
            "byte_total": sum(int(item["bytes"]) for item in files),
        }
        if position is not None:
            report.update(
                {
                    "tone_position": position,
                    "bank_ids": list(selection.bank_ids),
                    "legacy_fallback": False,
                }
            )
        _write_json(engine_dir / "report.json", report)
    return paths


def _prepare_output_directory(path: Path, *, include_readme: bool) -> None:
    allowed = {f"{fixture.stem}.wav" for fixture in AUDITION_SCORE} | {"report.json"}
    if include_readme:
        allowed.add("README.md")
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"audition output is not a directory: {path}")
        unexpected = sorted(item.name for item in path.iterdir() if item.name not in allowed)
        if unexpected:
            raise ValueError(f"audition output contains unexpected entries: {unexpected}")
    path.mkdir(parents=True, exist_ok=True)


def _score_report() -> list[dict[str, object]]:
    return [
        {
            "stem": fixture.stem,
            "notes": [
                {
                    "pitch": note.pitch,
                    "start_time": note.start_time,
                    "duration": note.duration,
                    "velocity": note.velocity,
                }
                for note in fixture.notes
            ],
        }
        for fixture in AUDITION_SCORE
    ]


def _file_report(path: Path, stem: str) -> dict[str, object]:
    with wave.open(str(path), "rb") as source:
        duration = source.getnframes() / source.getframerate()
    return {
        "stem": stem,
        "file": path.name,
        "sha256": _file_sha256(path),
        "bytes": path.stat().st_size,
        "duration_seconds": duration,
    }


def _write_pcm_wav(path: Path, samples: array, *, channels: int, sample_rate: int) -> None:
    if len(samples) % channels:
        raise ValueError(f"PCM sample count is not divisible by {channels} channels")
    payload = array("h", samples)
    if sys.byteorder != "little":
        payload.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(payload.tobytes())


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Legacy and continuous piano tone audition evidence."
    )
    parser.add_argument("--output", type=Path, required=True, help="A/B output directory")
    parser.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="formal three-bank piano catalog",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        paths = render_audition_set(args.output, args.catalog)
    except Exception:
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
