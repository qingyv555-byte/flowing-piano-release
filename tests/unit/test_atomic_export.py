from __future__ import annotations

from pathlib import Path

import pytest

from ai_piano_studio.persistence.atomic import atomic_write_bytes


def test_atomic_write_replaces_destination_only_after_complete_write(tmp_path: Path) -> None:
    target = tmp_path / "render.wav"
    target.write_bytes(b"old")

    assert atomic_write_bytes(target, b"new") == target
    assert target.read_bytes() == b"new"
    assert not list(tmp_path.glob(".*.tmp"))


def test_atomic_write_rejects_a_directory_without_destroying_other_files(tmp_path: Path) -> None:
    target = tmp_path / "render.wav"
    target.write_bytes(b"old")
    with pytest.raises(OSError):
        atomic_write_bytes(tmp_path, b"bad")
    assert target.read_bytes() == b"old"
