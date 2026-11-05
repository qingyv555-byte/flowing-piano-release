from __future__ import annotations

import subprocess
from pathlib import Path


def move_project_to_recycle_bin(project_dir: Path, library_root: Path) -> None:
    """Move one validated project directory to the Windows Recycle Bin."""
    target = project_dir.resolve(strict=True)
    root = library_root.resolve(strict=True)
    if target.parent != root or not target.is_dir():
        raise ValueError("project directory is outside the project library")
    escaped_target = str(target).replace("'", "''")
    script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        f"'{escaped_target}', "
        "'OnlyErrorDialogs', 'SendToRecycleBin')"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise OSError(result.stderr.strip() or "无法移动工程到回收站")
