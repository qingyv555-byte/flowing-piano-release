"""Prepare the DLL search path before PySide6 is imported in a frozen app."""

from __future__ import annotations

import os
import ctypes
from pathlib import Path
import sys


_dll_directory_handles = []
_base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
_candidates = (
    _base,
    _base / "_internal",
    _base / "PySide6",
    _base / "shiboken6",
    _base / "_internal" / "PySide6",
    _base / "_internal" / "shiboken6",
)
_add_dll_directory = getattr(os, "add_dll_directory", None)
if _add_dll_directory is not None:
    _seen = set()
    _path_prefix = []
    for _candidate in _candidates:
        _resolved = _candidate.resolve()
        if _resolved in _seen or not _resolved.is_dir():
            continue
        _seen.add(_resolved)
        _path_prefix.append(str(_resolved))
        _dll_directory_handles.append(_add_dll_directory(str(_resolved)))
    # Qt6Core's ICU dependency must resolve to the copy shipped beside the
    # frozen app before Windows falls through to an older system ICU DLL.
    if _path_prefix:
        os.environ["PATH"] = os.pathsep.join(
            [*_path_prefix, os.environ.get("PATH", "")]
        )

    # SetDllDirectoryW is intentionally used in addition to PATH: dependent
    # DLL resolution can prefer System32 over PATH on Windows. The frozen
    # runtime root contains the matching ICU build shipped with Qt.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _set_dll_directory = _kernel32.SetDllDirectoryW
    _set_dll_directory.argtypes = [ctypes.c_wchar_p]
    _set_dll_directory.restype = ctypes.c_bool
    _runtime_root = next(
        (
            _candidate
            for _candidate in _seen
            if (_candidate / "PySide6" / "Qt6Core.dll").is_file()
        ),
        None,
    )
    if _runtime_root is not None:
        _set_dll_directory(str(_runtime_root))

    # PySide6's extension modules depend on shiboken6. Load it explicitly so
    # the frozen interpreter does not resolve a stale system copy first.
    for _directory in _seen:
        _shiboken = _directory / "shiboken6.abi3.dll"
        if _shiboken.is_file():
            ctypes.WinDLL(str(_shiboken))
            break

    # Mirror PySide6's own Windows bootstrap: importing the Python package
    # loads its Shiboken extension before any QtCore extension is imported.
    import shiboken6  # noqa: F401
