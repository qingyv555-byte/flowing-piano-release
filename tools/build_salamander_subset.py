"""Build a reproducible two-layer Salamander Grand Piano v3 sample subset."""

from __future__ import annotations

import argparse
from array import array
from datetime import date
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sys
import tempfile
import wave


logger = logging.getLogger("build_salamander_subset")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = PROJECT_ROOT / "resources" / "piano" / "PROVENANCE-SALAMANDER.json"
AUTHORITY_MARKDOWN_PATH = (
    PROJECT_ROOT / "resources" / "piano" / "PROVENANCE-SALAMANDER.md"
)
OFFICIAL_DIRECTORY_NAME = "SalamanderGrandPianoV3_44.1khz16bit"
OFFICIAL_ARCHIVE_NAME = f"{OFFICIAL_DIRECTORY_NAME}.tar.bz2"
OFFICIAL_LICENSE_FILENAME = "LICENSE-CC-BY-3.0.html"
OFFICIAL_SOURCE_URL = (
    "https://archive.org/download/SalamanderGrandPianoV3/"
    f"{OFFICIAL_ARCHIVE_NAME}"
)
OFFICIAL_AUTHOR = "Alexander Holm"
OFFICIAL_LICENSE = "CC BY 3.0"
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
PROVENANCE_KEYS = frozenset(
    {
        "source_filename",
        "source_size",
        "source_sha1",
        "source_md5",
        "locally_computed_sha256",
        "source_url",
        "author",
        "license",
        "retrieval_date",
        "sha256_notice",
        "license_filename",
        "license_sha256",
    }
)
SOURCE_SAMPLE_RATE = 44_100
OUTPUT_SAMPLE_RATE = 32_000
OUTPUT_CHANNELS = 2
OUTPUT_SAMPLE_WIDTH = 2
TAIL_FADE_MS = 120
MAXIMUM_OUTPUT_BYTES = 30 * 1024 * 1024
FORMAL_BANKS = (
    ("layers-06-11", "柔和", 0.0, 6, 11),
    ("layers-07-12", "均衡", 0.5, 7, 12),
    ("layers-08-13", "明亮", 1.0, 8, 13),
)

ROOTS = (
    ("A0", 21, 6.0),
    ("C1", 24, 6.0),
    ("D#1", 27, 6.0),
    ("A1", 33, 6.0),
    ("D#2", 39, 6.0),
    ("A2", 45, 6.0),
    ("D#3", 51, 4.0),
    ("A3", 57, 4.0),
    ("D#4", 63, 4.0),
    ("A4", 69, 4.0),
    ("F#4", 66, 4.0),
    ("D#5", 75, 3.0),
    ("A5", 81, 3.0),
    ("D#6", 87, 2.2),
    ("A6", 93, 2.2),
    ("D#7", 99, 2.2),
    ("A7", 105, 2.2),
    ("C8", 108, 1.5),
)


class ConversionError(RuntimeError):
    """Raised when a subset cannot be built without violating its contract."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert an explicit local official Salamander Grand Piano v3 source "
            "directory into a bounded two-layer WAV subset. No assets are downloaded."
        )
    )
    parser.add_argument("source", type=Path, help="official 44.1 kHz/16-bit source directory")
    parser.add_argument("--output", type=Path, required=True, help="new or empty output directory")
    parser.add_argument("--archive", type=Path, required=True, help="verified official source archive")
    parser.add_argument("--provenance", type=Path, required=True, help="local source provenance JSON")
    parser.add_argument("--license", type=Path, required=True, help="retained official license file")
    parser.add_argument("--soft-layer", type=int, required=True, help="source velocity layer index")
    parser.add_argument("--hard-layer", type=int, required=True, help="source velocity layer index")
    return parser


def _validated_directory(path: Path) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ConversionError(f"official source directory is unavailable: {path}") from error
    if not resolved.is_dir():
        raise ConversionError(f"official source path is not a directory: {resolved}")
    if resolved.name != OFFICIAL_DIRECTORY_NAME:
        raise ConversionError(
            "official source directory must be named "
            f"{OFFICIAL_DIRECTORY_NAME}: {resolved}"
        )
    return resolved


def _validated_output(path: Path, source: Path) -> Path:
    output = path.expanduser().resolve()
    if output == source or source in output.parents:
        raise ConversionError("output directory must not be inside the official source directory")
    if output.exists():
        if not output.is_dir():
            raise ConversionError(f"output path is not a directory: {output}")
        if any(output.iterdir()):
            raise ConversionError(f"output directory must be empty: {output}")
    return output


def _validated_source_file(path: Path, source: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ConversionError(f"{label} is unavailable: {path}") from error
    if not resolved.is_file():
        raise ConversionError(f"{label} is not a file: {resolved}")
    if source not in resolved.parents:
        raise ConversionError(f"{label} must be inside the official source directory: {resolved}")
    return resolved


def _required_hash(payload: dict[str, object], name: str, length: int) -> str:
    value = payload.get(name)
    if (
        not isinstance(value, str)
        or len(value) != length
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ConversionError(f"provenance {name} must be {length} lowercase hex characters")
    return value


def _file_hashes(path: Path) -> dict[str, str]:
    digests = {
        "source_sha1": hashlib.sha1(),
        "source_md5": hashlib.md5(),
        "locally_computed_sha256": hashlib.sha256(),
    }
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            for digest in digests.values():
                digest.update(chunk)
    return {name: digest.hexdigest() for name, digest in digests.items()}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConversionError(f"invalid {label} JSON: {path}") from error
    if not isinstance(payload, dict):
        raise ConversionError(f"{label} root must be an object")
    return payload


def _validated_authority() -> dict[str, object]:
    authority = _read_json_object(AUTHORITY_PATH, "tracked authority")
    if set(authority) != PROVENANCE_KEYS:
        missing = sorted(PROVENANCE_KEYS - set(authority))
        unexpected = sorted(set(authority) - PROVENANCE_KEYS)
        raise ConversionError(
            f"tracked authority fields differ; missing={missing}, unexpected={unexpected}"
        )
    if authority["source_filename"] != OFFICIAL_ARCHIVE_NAME:
        raise ConversionError(f"tracked authority must name {OFFICIAL_ARCHIVE_NAME}")
    source_size = authority["source_size"]
    if isinstance(source_size, bool) or not isinstance(source_size, int) or source_size <= 0:
        raise ConversionError("tracked authority source_size must be a positive integer")
    _required_hash(authority, "source_sha1", 40)
    _required_hash(authority, "source_md5", 32)
    _required_hash(authority, "locally_computed_sha256", 64)
    _required_hash(authority, "license_sha256", 64)
    if authority["source_url"] != OFFICIAL_SOURCE_URL:
        raise ConversionError("tracked authority URL is not the approved official archive")
    if authority["author"] != OFFICIAL_AUTHOR:
        raise ConversionError("tracked authority author is not Alexander Holm")
    if authority["license"] != OFFICIAL_LICENSE:
        raise ConversionError("tracked authority license is not CC BY 3.0")
    if authority["license_filename"] != OFFICIAL_LICENSE_FILENAME:
        raise ConversionError(f"tracked authority must name {OFFICIAL_LICENSE_FILENAME}")
    if authority["sha256_notice"] != SHA256_NOTICE:
        raise ConversionError("tracked authority local SHA-256 notice is missing or changed")
    retrieval_date = authority["retrieval_date"]
    if not isinstance(retrieval_date, str):
        raise ConversionError("tracked authority retrieval_date must be an ISO date")
    try:
        date.fromisoformat(retrieval_date)
    except ValueError as error:
        raise ConversionError("tracked authority retrieval_date must be an ISO date") from error
    return authority


def _render_human_provenance(authority: dict[str, object]) -> str:
    fields = "\n".join(
        f"- {name}: `{authority[name]}`" for name in HUMAN_PROVENANCE_FIELDS
    )
    return (
        f"{HUMAN_PROVENANCE_TITLE}\n\n{fields}\n\n"
        f"{HUMAN_LICENSE_SENTENCE}\n\n{authority['sha256_notice']}\n"
    )


def _validate_human_provenance(authority: dict[str, object]) -> None:
    try:
        text = AUTHORITY_MARKDOWN_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConversionError(
            f"human provenance document is unavailable: {AUTHORITY_MARKDOWN_PATH}"
        ) from error
    if text != _render_human_provenance(authority):
        raise ConversionError("human provenance differs from tracked authority")


def _validated_provenance(
    source: Path,
    archive_path: Path,
    provenance_path: Path,
    license_path: Path,
) -> dict[str, object]:
    authority = _validated_authority()
    _validate_human_provenance(authority)
    archive = _validated_source_file(archive_path, source, "official source archive")
    provenance = _validated_source_file(provenance_path, source, "source provenance")
    retained_license = _validated_source_file(license_path, source, "retained source license")
    payload = _read_json_object(provenance, "source provenance")
    if payload != authority:
        differing = sorted(
            key
            for key in set(payload) | set(authority)
            if payload.get(key) != authority.get(key)
        )
        raise ConversionError(
            f"source provenance differs from tracked authority: fields={differing}"
        )
    if archive.name != authority["source_filename"]:
        raise ConversionError("source archive filename differs from tracked authority")
    if retained_license.name != authority["license_filename"]:
        raise ConversionError("retained source license filename differs from tracked authority")
    if archive.stat().st_size != authority["source_size"]:
        raise ConversionError("source archive source_size differs from tracked authority")
    actual_hashes = _file_hashes(archive)
    for field, actual in actual_hashes.items():
        if actual != authority[field]:
            raise ConversionError(f"source archive {field} differs from tracked authority")
    if _file_sha256(retained_license) != authority["license_sha256"]:
        raise ConversionError("retained source license SHA-256 differs from tracked authority")
    return authority


def _validate_layers(soft_layer: int, hard_layer: int) -> None:
    for name, value in (("soft", soft_layer), ("hard", hard_layer)):
        if isinstance(value, bool) or value <= 0:
            raise ConversionError(f"{name} layer must be a positive integer")
    if soft_layer == hard_layer:
        raise ConversionError("soft and hard layers must be different")


def _source_files(source: Path, soft_layer: int, hard_layer: int) -> dict[tuple[str, str], Path]:
    by_name: dict[str, list[Path]] = {}
    for candidate in source.rglob("*"):
        if not candidate.is_file() or candidate.suffix.casefold() != ".wav":
            continue
        resolved = candidate.resolve(strict=True)
        if source not in resolved.parents:
            raise ConversionError(f"source WAV resolves outside official directory: {candidate}")
        by_name.setdefault(candidate.name.casefold(), []).append(resolved)

    selected: dict[tuple[str, str], Path] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for root_note, _root_midi, _maximum_seconds in ROOTS:
        for layer_name, source_layer in (("Soft", soft_layer), ("Hard", hard_layer)):
            expected = f"{root_note}v{source_layer}.wav"
            matches = by_name.get(expected.casefold(), [])
            if not matches:
                missing.append(expected)
            elif len(matches) > 1:
                ambiguous.append(expected)
            else:
                selected[(root_note, layer_name)] = matches[0]

    reports: list[str] = []
    if missing:
        reports.append(
            f"missing {len(missing)} expected source sample(s):\n  "
            + "\n  ".join(missing)
        )
    if ambiguous:
        reports.append(
            f"ambiguous {len(ambiguous)} expected source sample(s):\n  "
            + "\n  ".join(ambiguous)
        )
    if reports:
        raise ConversionError("\n".join(reports))
    return selected


def _read_source(path: Path, maximum_seconds: float) -> tuple[array, int, int]:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE":
                raise ConversionError(f"compressed WAV is unsupported: {path.name}")
            if source.getsampwidth() != OUTPUT_SAMPLE_WIDTH:
                raise ConversionError(f"source WAV must be signed 16-bit PCM: {path.name}")
            if source.getframerate() != SOURCE_SAMPLE_RATE:
                raise ConversionError(f"source WAV must be 44.1 kHz: {path.name}")
            channels = source.getnchannels()
            if channels not in (1, 2):
                raise ConversionError(f"source WAV must be mono or stereo: {path.name}")
            frame_count = min(
                source.getnframes(),
                int(maximum_seconds * SOURCE_SAMPLE_RATE),
            )
            payload = source.readframes(frame_count)
    except (EOFError, wave.Error) as error:
        raise ConversionError(f"invalid source WAV {path.name}: {error}") from error

    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) != frame_count * channels:
        raise ConversionError(f"truncated source WAV: {path.name}")
    if frame_count <= 1:
        raise ConversionError(f"source WAV has too few frames: {path.name}")
    return samples, channels, frame_count


def _rounded_division(value: int, divisor: int) -> int:
    if value >= 0:
        return (value + divisor // 2) // divisor
    return -((-value + divisor // 2) // divisor)


def _resample_stereo(samples: array, channels: int, source_frames: int) -> array:
    output_frames = int(source_frames * OUTPUT_SAMPLE_RATE / SOURCE_SAMPLE_RATE)
    output = array("h")
    append = output.append
    for output_frame in range(output_frames):
        position_numerator = output_frame * SOURCE_SAMPLE_RATE
        source_frame = min(source_frames - 1, position_numerator // OUTPUT_SAMPLE_RATE)
        next_frame = min(source_frames - 1, source_frame + 1)
        fraction = position_numerator % OUTPUT_SAMPLE_RATE
        for output_channel in range(OUTPUT_CHANNELS):
            source_channel = output_channel if channels == 2 else 0
            first = samples[source_frame * channels + source_channel]
            second = samples[next_frame * channels + source_channel]
            weighted = first * (OUTPUT_SAMPLE_RATE - fraction) + second * fraction
            append(_rounded_division(weighted, OUTPUT_SAMPLE_RATE))
    return output


def _fade_tail(samples: array) -> None:
    frame_count = len(samples) // OUTPUT_CHANNELS
    fade_frames = min(frame_count, OUTPUT_SAMPLE_RATE * TAIL_FADE_MS // 1000)
    if fade_frames <= 1:
        for index in range(len(samples)):
            samples[index] = 0
        return
    denominator = fade_frames - 1
    first_fade_frame = frame_count - fade_frames
    for offset in range(fade_frames):
        numerator = denominator - offset
        frame = first_fade_frame + offset
        for channel in range(OUTPUT_CHANNELS):
            index = frame * OUTPUT_CHANNELS + channel
            samples[index] = _rounded_division(samples[index] * numerator, denominator)


def _output_name(root_note: str, layer_name: str) -> str:
    return f"{root_note.replace('#', 's')}-{layer_name.lower()}.wav"


def _write_wav(path: Path, samples: array) -> None:
    payload = array("h", samples)
    if sys.byteorder != "little":
        payload.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(OUTPUT_CHANNELS)
        output.setsampwidth(OUTPUT_SAMPLE_WIDTH)
        output.setframerate(OUTPUT_SAMPLE_RATE)
        output.writeframes(payload.tobytes())


def build_subset(
    source_path: Path,
    output_path: Path,
    *,
    archive_path: Path,
    provenance_path: Path,
    license_path: Path,
    soft_layer: int,
    hard_layer: int,
) -> Path:
    """Build the selected local layer pair and return its generated manifest path."""
    _validate_layers(soft_layer, hard_layer)
    source = _validated_directory(source_path)
    output = _validated_output(output_path, source)
    provenance = _validated_provenance(
        source,
        archive_path,
        provenance_path,
        license_path,
    )
    selected = _source_files(source, soft_layer, hard_layer)

    decoded: dict[tuple[str, str], array] = {}
    projected_bytes = 0
    for root_note, _root_midi, maximum_seconds in ROOTS:
        for layer_name in ("Soft", "Hard"):
            samples, channels, source_frames = _read_source(
                selected[(root_note, layer_name)], maximum_seconds
            )
            converted = _resample_stereo(samples, channels, source_frames)
            _fade_tail(converted)
            decoded[(root_note, layer_name)] = converted
            projected_bytes += 44 + len(converted) * OUTPUT_SAMPLE_WIDTH
    if projected_bytes > MAXIMUM_OUTPUT_BYTES:
        raise ConversionError(
            f"generated WAV payload would exceed 30 MiB: {projected_bytes} bytes"
        )

    output.mkdir(parents=True, exist_ok=True)
    roots: list[dict[str, object]] = []
    total_wav_bytes = 0
    for root_note, root_midi, maximum_seconds in ROOTS:
        layers: dict[str, object] = {}
        for layer_name, source_layer in (("Soft", soft_layer), ("Hard", hard_layer)):
            filename = _output_name(root_note, layer_name)
            destination = output / filename
            samples = decoded[(root_note, layer_name)]
            _write_wav(destination, samples)
            size = destination.stat().st_size
            total_wav_bytes += size
            layers[layer_name] = {
                "file": filename,
                "frame_count": len(samples) // OUTPUT_CHANNELS,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "source_file": selected[(root_note, layer_name)].relative_to(source).as_posix(),
                "source_layer": source_layer,
            }
        roots.append(
            {
                "root_midi": root_midi,
                "root_note": root_note,
                "maximum_duration_seconds": maximum_seconds,
                "layers": layers,
            }
        )
    if total_wav_bytes > MAXIMUM_OUTPUT_BYTES:
        raise ConversionError(
            f"generated WAV payload exceeds 30 MiB: {total_wav_bytes} bytes"
        )

    manifest = {
        "asset_version": "salamander-v3-subset-candidate",
        "channels": OUTPUT_CHANNELS,
        "crop": {"source_start_frame": 0, "tail_fade_ms": TAIL_FADE_MS},
        "provenance": provenance,
        "roots": roots,
        "sample_rate": OUTPUT_SAMPLE_RATE,
        "sample_width_bytes": OUTPUT_SAMPLE_WIDTH,
        "source": {
            "author": OFFICIAL_AUTHOR,
            "directory": OFFICIAL_DIRECTORY_NAME,
            "hard_layer": hard_layer,
            "license": OFFICIAL_LICENSE,
            "project": "Salamander Grand Piano v3",
            "soft_layer": soft_layer,
        },
        "sustain": {"loop_free": True},
        "total_wav_bytes": total_wav_bytes,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


def _verify_formal_banks(
    banks_root: Path,
    expected_provenance: dict[str, object],
) -> list[dict[str, object]]:
    """Validate generated banks before atomically replacing the tracked catalog."""
    catalog: list[dict[str, object]] = []
    wav_count = 0
    for bank_id, label, position, soft_layer, hard_layer in FORMAL_BANKS:
        bank = banks_root / bank_id
        manifest_path = bank / "manifest.json"
        manifest = _read_json_object(manifest_path, f"formal bank {bank_id} manifest")
        wavs = list(bank.glob("*.wav"))
        if len(wavs) != 36:
            raise ConversionError(f"formal bank {bank_id} must contain exactly 36 WAVs")
        wav_count += len(wavs)
        total_wav_bytes = sum(path.stat().st_size for path in wavs)
        if total_wav_bytes > MAXIMUM_OUTPUT_BYTES:
            raise ConversionError(f"formal bank {bank_id} exceeds 30 MiB")
        source_metadata = manifest.get("source")
        if not isinstance(source_metadata, dict) or (
            source_metadata.get("soft_layer"), source_metadata.get("hard_layer")
        ) != (soft_layer, hard_layer):
            raise ConversionError(f"formal bank {bank_id} has unexpected source layers")
        if manifest.get("provenance") != expected_provenance:
            raise ConversionError(f"formal bank {bank_id} provenance differs from tracked authority")
        if manifest.get("total_wav_bytes") != total_wav_bytes:
            raise ConversionError(f"formal bank {bank_id} byte total is inconsistent")
        catalog.append(
            {
                "id": bank_id,
                "label": label,
                "position": position,
                "manifest": f"banks/{bank_id}/manifest.json",
                "soft_layer": soft_layer,
                "hard_layer": hard_layer,
            }
        )
    if wav_count != 108:
        raise ConversionError(f"formal banks must contain exactly 108 WAVs, found {wav_count}")
    return catalog


def build_formal_banks(
    source_path: Path,
    piano_root: Path,
    *,
    archive_path: Path,
    provenance_path: Path,
    license_path: Path,
) -> Path:
    """Build and validate all selectable Salamander banks as one replaceable tree."""
    source = _validated_directory(source_path)
    root = piano_root.expanduser().resolve()
    if root == source or source in root.parents:
        raise ConversionError("formal bank output must not be inside the official source directory")
    root.mkdir(parents=True, exist_ok=True)
    provenance = _validated_provenance(
        source, archive_path, provenance_path, license_path
    )
    with tempfile.TemporaryDirectory(
        prefix=".formal-banks-", dir=root.parent
    ) as staging_parent:
        staging_root = Path(staging_parent)
        staging_banks = staging_root / "banks"
        for bank_id, _label, _position, soft_layer, hard_layer in FORMAL_BANKS:
            build_subset(
                source,
                staging_banks / bank_id,
                archive_path=archive_path,
                provenance_path=provenance_path,
                license_path=license_path,
                soft_layer=soft_layer,
                hard_layer=hard_layer,
            )
        catalog = {"banks": _verify_formal_banks(staging_banks, provenance)}
        staging_catalog = staging_root / "banks.json"
        staging_catalog.write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        destination_banks = root / "banks"
        destination_catalog = root / "banks.json"
        backup_banks = staging_root / "previous-banks"
        backup_catalog = staging_root / "previous-banks.json"
        try:
            if destination_banks.exists():
                destination_banks.replace(backup_banks)
            if destination_catalog.exists():
                destination_catalog.replace(backup_catalog)
            staging_banks.replace(destination_banks)
            staging_catalog.replace(destination_catalog)
        except OSError as error:
            logger.exception("Formal Salamander bank replacement failed")
            if destination_banks.exists():
                shutil.rmtree(destination_banks)
            if backup_banks.exists():
                backup_banks.replace(destination_banks)
            if destination_catalog.exists():
                destination_catalog.unlink()
            if backup_catalog.exists():
                backup_catalog.replace(destination_catalog)
            raise ConversionError("unable to replace formal Salamander banks") from error
    return root / "banks.json"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        manifest = build_subset(
            args.source,
            args.output,
            archive_path=args.archive,
            provenance_path=args.provenance,
            license_path=args.license,
            soft_layer=args.soft_layer,
            hard_layer=args.hard_layer,
        )
    except Exception as error:
        logger.error("Salamander subset conversion failed: %s", error)
        return 1
    logger.info("Generated Salamander subset manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
