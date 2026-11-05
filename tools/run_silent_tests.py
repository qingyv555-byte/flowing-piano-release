"""Mute test audio temporarily; Windows can persist it for later Python runs."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import sys
import uuid
from pathlib import Path

import pytest


@contextmanager
def temporary_mute(get_mute, set_mute):
    previous = get_mute()
    try:
        set_mute(True)
        if not get_mute():
            raise RuntimeError("Test audio session did not mute")
        yield
    finally:
        set_mute(previous)
        if get_mute() != previous:
            raise RuntimeError("Could not restore the pre-test audio mute state")


@contextmanager
def mute_test_session():
    ole = ctypes.OleDLL("ole32")
    ole.CoInitialize(None)
    def guid(value):
        return ctypes.create_string_buffer(uuid.UUID(value).bytes_le, 16)
    def call(pointer, index, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
        result = fn(pointer, *args)
        if result < 0:
            raise OSError(f"Windows audio session operation failed: {result:#x}")
    enumerator = ctypes.c_void_p()
    ole.CoCreateInstance(guid("BCDE0395-E52F-467C-8E3D-C4579291692E"), None, 1,
                         guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), ctypes.byref(enumerator))
    if not enumerator.value:
        raise RuntimeError("Cannot initialize Windows audio session; refusing audible tests")
    device = ctypes.c_void_p()
    call(enumerator, 4, [ctypes.c_int, ctypes.c_int, ctypes.c_void_p], 0, 1, ctypes.byref(device))
    manager = ctypes.c_void_p()
    call(device, 3, [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p],
         guid("BFA971F1-4D5E-40BB-935E-967039BFBEE4"), 23, None, ctypes.byref(manager))
    volume = ctypes.c_void_p()
    call(manager, 4, [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], None, 0, ctypes.byref(volume))
    def get_mute():
        muted = wintypes.BOOL()
        call(volume, 6, [ctypes.c_void_p], ctypes.byref(muted))
        return bool(muted.value)

    def set_mute(value):
        call(volume, 5, [wintypes.BOOL, ctypes.c_void_p], value, None)

    with temporary_mute(get_mute, set_mute):
        yield enumerator, device, manager, volume


def run_tests(arguments):
    with mute_test_session():
        print("Windows test-process audio temporarily muted; original state restored on exit.", flush=True)
        return pytest.main(arguments)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(run_tests(sys.argv[1:]))
