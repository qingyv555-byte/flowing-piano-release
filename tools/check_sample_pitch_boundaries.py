"""Render and validate the furthest pitch mapped to every Sample Piano root."""

from __future__ import annotations

import argparse
from array import array
import json
import logging
from math import cos, pi, sin, sqrt
from pathlib import Path
import sys
import wave


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_piano_studio.audio.sample_piano import SamplePianoEngine


logger = logging.getLogger("check_sample_pitch_boundaries")
MINIMUM_MIDI = 21
MAXIMUM_MIDI = 108
MAXIMUM_SEMITONE_DISTANCE = 3
MAXIMUM_DURATION_ERROR_PERCENT = 2.0
ATTACK_WINDOW_SECONDS = 0.05
SPECTRAL_WINDOW_FRAMES = 256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and validate low/high pitch boundaries for every sample root."
    )
    parser.add_argument("manifest", type=Path, help="candidate Sample Piano manifest")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new or empty directory for rendered boundary WAVs and JSON report",
    )
    return parser


def _read_wav(path: Path) -> tuple[array, int]:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE":
                raise ValueError("WAV must be uncompressed PCM")
            if source.getnchannels() != 2 or source.getsampwidth() != 2:
                raise ValueError("WAV must be stereo signed 16-bit PCM")
            sample_rate = source.getframerate()
            payload = source.readframes(source.getnframes())
    except (EOFError, wave.Error) as error:
        raise ValueError(f"invalid WAV {path}: {error}") from error
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples or len(samples) % 2:
        raise ValueError(f"WAV has no complete stereo frames: {path}")
    return samples, sample_rate


def _write_wav(path: Path, samples: array, sample_rate: int) -> None:
    payload = array("h", samples)
    if sys.byteorder != "little":
        payload.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(payload.tobytes())


def _spectral_centroid_hz(samples: array, sample_rate: int) -> float:
    frame_count = len(samples) // 2
    window_size = min(frame_count, SPECTRAL_WINDOW_FRAMES)
    if window_size < 2:
        return 0.0
    mono = [
        (samples[frame * 2] + samples[frame * 2 + 1]) / 2.0
        for frame in range(window_size)
    ]
    weighted_frequency = 0.0
    total_magnitude = 0.0
    denominator = window_size - 1
    for bin_index in range(window_size // 2 + 1):
        real = 0.0
        imaginary = 0.0
        for frame, value in enumerate(mono):
            windowed = value * (0.5 - 0.5 * cos(2.0 * pi * frame / denominator))
            phase = 2.0 * pi * bin_index * frame / window_size
            real += windowed * cos(phase)
            imaginary -= windowed * sin(phase)
        magnitude = sqrt(real * real + imaginary * imaginary)
        frequency = bin_index * sample_rate / window_size
        weighted_frequency += frequency * magnitude
        total_magnitude += magnitude
    if total_magnitude == 0.0:
        return 0.0
    return weighted_frequency / total_magnitude


def _analyze_samples(
    samples: array,
    sample_rate: int,
    *,
    semitone_distance: int,
    expected_duration: float,
    reference_centroid_hz: float,
) -> dict[str, object]:
    if sample_rate <= 0 or expected_duration <= 0:
        raise ValueError("sample rate and expected duration must be positive")
    frame_count = len(samples) // 2
    output_duration = frame_count / sample_rate
    duration_error_percent = abs(output_duration / expected_duration - 1.0) * 100.0
    absolute = [abs(sample) for sample in samples]
    peak_sample = max(absolute, default=0)
    peak = peak_sample / 32_768.0
    rms = sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32_768.0
    clipped_frames = sum(
        1
        for frame in range(frame_count)
        if samples[frame * 2] in (-32_768, 32_767)
        or samples[frame * 2 + 1] in (-32_768, 32_767)
    )
    attack_frames = min(frame_count, max(1, round(sample_rate * ATTACK_WINDOW_SECONDS)))
    attack_peak = max((abs(sample) for sample in samples[: attack_frames * 2]), default=0)
    missing_attack = peak_sample == 0 or attack_peak < max(64.0, peak_sample * 0.05)
    centroid = _spectral_centroid_hz(samples, sample_rate)

    failures: list[str] = []
    if semitone_distance > MAXIMUM_SEMITONE_DISTANCE:
        failures.append("distance_above_three_semitones")
    if clipped_frames:
        failures.append("clipped_frames")
    if missing_attack:
        failures.append("missing_attack")
    if duration_error_percent > MAXIMUM_DURATION_ERROR_PERCENT:
        failures.append("duration_error")
    return {
        "output_duration_seconds": output_duration,
        "expected_duration_seconds": expected_duration,
        "duration_error_percent": duration_error_percent,
        "peak": peak,
        "rms": rms,
        "clipped_frames": clipped_frames,
        "attack_peak": attack_peak / 32_768.0,
        "spectral_centroid_hz": centroid,
        "spectral_centroid_delta_hz": float(centroid - reference_centroid_hz),
        "failures": failures,
    }


def analyze_wav(
    path: Path,
    *,
    semitone_distance: int,
    expected_duration: float,
    reference_centroid_hz: float,
) -> dict[str, object]:
    """Return boundary metrics and required guard failures for one stereo WAV."""
    samples, sample_rate = _read_wav(path)
    return _analyze_samples(
        samples,
        sample_rate,
        semitone_distance=semitone_distance,
        expected_duration=expected_duration,
        reference_centroid_hz=reference_centroid_hz,
    )


def _manifest_roots(manifest: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    roots = payload.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ValueError("manifest roots must be a non-empty array")
    return payload, roots


def _root_frame_count(root: dict[str, object]) -> int:
    layers = root.get("layers")
    if not isinstance(layers, dict):
        raise ValueError("root layers must be an object")
    frame_counts: list[int] = []
    for layer_name in ("Soft", "Hard"):
        layer = layers.get(layer_name)
        if not isinstance(layer, dict):
            raise ValueError(f"root is missing {layer_name} layer")
        frame_count = layer.get("frame_count")
        if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count <= 0:
            raise ValueError(f"{layer_name} frame_count must be a positive integer")
        frame_counts.append(frame_count)
    return min(frame_counts)


def check_boundaries(manifest_path: Path, output_dir: Path) -> Path:
    """Render every furthest mapped edge and return the JSON report path."""
    manifest = manifest_path.expanduser().resolve(strict=True)
    output = output_dir.expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"output directory must be new or empty: {output}")
    payload, roots = _manifest_roots(manifest)
    sample_rate = payload.get("sample_rate")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("manifest sample_rate must be a positive integer")
    engine = SamplePianoEngine(manifest, scheduler=lambda _job: None)
    if engine.legacy_selected:
        raise ValueError(f"Sample Piano manifest was rejected: {engine.failure_reason}")

    assignments: dict[int, list[int]] = {int(root["root_midi"]): [] for root in roots}
    for pitch in range(MINIMUM_MIDI, MAXIMUM_MIDI + 1):
        assignments.setdefault(engine.nearest_root(pitch), []).append(pitch)

    output.mkdir(parents=True, exist_ok=True)
    boundaries: list[dict[str, object]] = []
    for root in roots:
        root_midi = int(root["root_midi"])
        root_note = str(root["root_note"])
        mapped = assignments.get(root_midi, [])
        if not mapped:
            raise ValueError(f"root {root_note} has no mapped playable pitches")
        root_duration = _root_frame_count(root) / sample_rate
        reference_samples = engine.render_note(
            root_midi,
            root_duration * 2.0,
            64,
            sample_rate,
        )
        reference_centroid = _spectral_centroid_hz(reference_samples, sample_rate)
        safe_note = root_note.replace("#", "s")
        for edge, pitch in (("low", min(mapped)), ("high", max(mapped))):
            semitone_delta = pitch - root_midi
            semitone_distance = abs(semitone_delta)
            pitch_ratio = 2.0 ** (semitone_delta / 12.0)
            expected_duration = root_duration / pitch_ratio
            samples = engine.render_note(pitch, root_duration * 2.0, 64, sample_rate)
            filename = f"{safe_note}-{edge}-midi{pitch}.wav"
            render_path = output / filename
            _write_wav(render_path, samples, sample_rate)
            metrics = analyze_wav(
                render_path,
                semitone_distance=semitone_distance,
                expected_duration=expected_duration,
                reference_centroid_hz=reference_centroid,
            )
            boundaries.append(
                {
                    "root_note": root_note,
                    "root_midi": root_midi,
                    "edge": edge,
                    "mapped_pitch": pitch,
                    "semitone_distance": semitone_distance,
                    "render_file": filename,
                    **metrics,
                }
            )

    passed = all(not entry["failures"] for entry in boundaries)
    report = {
        "manifest": str(manifest),
        "sample_rate": sample_rate,
        "passed": passed,
        "boundaries": boundaries,
    }
    report_path = output / "pitch-boundaries.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        report_path = check_boundaries(args.manifest, args.output_dir)
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as error:
        logger.exception("Pitch-boundary check failed: %s", error)
        return 1
    if not report["passed"]:
        logger.error("Pitch-boundary guards failed; see %s", report_path)
        return 1
    logger.info("Pitch-boundary guards passed: %s", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
