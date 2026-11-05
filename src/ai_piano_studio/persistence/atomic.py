from __future__ import annotations

import os
from pathlib import Path
import tempfile


def atomic_write_bytes(path: str | Path, payload: bytes) -> Path:
    """Write bytes beside the destination and replace it only after success."""

    destination = Path(path)
    if destination.exists() and destination.is_dir():
        raise OSError(f"destination is a directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        return destination
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
