"""Optional native SFZ rendering, isolated from the existing realtime engine.

The caller supplies an already-vetted, locally installed instrument and the
exact sfizz.dll path. This module NEVER downloads packages, mutates user
preferences, or silently substitutes a different instrument.

Native SFZ loading can follow relative sample/include references. Only use
this renderer on trusted packages extracted into a bounded owned directory;
an arbitrary user-supplied SFZ requires an import validator first.
"""
from __future__ import annotations

from array import array
import ctypes
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from ai_piano_studio.audio.synth import SAMPLE_RATE
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import ControlEvent, SoundingNote, sounding_duration

_BLOCK = 512
_MAX_SECONDS = 600.0
_MAX_EVENTS = 120_000


@dataclass(frozen=True, order=True)
class _Event:
    frame: int
    order: int
    kind: str
    data: int
    value: int


def _events_for_score(
    notes: Iterable[Note],
    *,
    controls: Iterable[ControlEvent] = (),
    sample_rate: int = SAMPLE_RATE,
    playback_rate: float = 1.0,
) -> tuple[tuple[_Event, ...], int]:
    if type(sample_rate) is not int or not 8_000 <= sample_rate <= 192_000:
        raise ValueError("invalid output sample rate")
    if not isfinite(playback_rate) or playback_rate <= 0:
        raise ValueError("invalid playback rate")
    notes, controls = tuple(notes), tuple(controls)
    if len(notes) * 2 + len(controls) > _MAX_EVENTS:
        raise ValueError("score contains too many SFZ events")
    events: list[_Event] = []
    musical_end = 0
    for note in notes:
        if not isinstance(note, Note):
            # A SoundingNote wraps an authoritative Note; never accept a
            # generated object with arbitrary fields or methods.
            if not isinstance(note, SoundingNote):
                raise TypeError("notes must contain Note or SoundingNote")
        if note.status == "deleted":
            continue
        if not isfinite(note.start_time) or not isfinite(note.duration) or note.duration <= 0:
            raise ValueError("note timing must be positive and finite")
        start = round(note.start_time / playback_rate * sample_rate)
        # The main playback pipeline can pass a SoundingNote carrying a
        # previously resolved CC64 pedal release. Do not erase it by using
        # the shorter physical key duration.
        effective_duration = sounding_duration(note) if not controls else note.duration
        stop = round((note.start_time + effective_duration) / playback_rate * sample_rate)
        if stop <= start:
            stop = start + 1
        events.append(_Event(start, 2, "on", note.pitch, note.velocity))
        events.append(_Event(stop, 1, "off", note.pitch, 0))
        musical_end = max(musical_end, stop)
    for event in controls:
        if not isinstance(event, ControlEvent) or event.controller != 64:
            raise ValueError("only CC64 control events are supported")
        frame = round(event.time / playback_rate * sample_rate)
        events.append(_Event(frame, 0, "cc", 64, event.value))
        musical_end = max(musical_end, frame)
    if not musical_end:
        raise ValueError("cannot render an empty SFZ score")
    if musical_end * playback_rate / sample_rate > _MAX_SECONDS:
        raise ValueError("SFZ render duration exceeds bound")
    # Pad for native instrument release samples without adding a new note.
    total_frames = musical_end + round(min(2.0, max(0.7, musical_end / sample_rate * 0.02)) * sample_rate)
    return tuple(sorted(events)), total_frames


class _NativeSfizz:
    """One private native synth handle owned by one rendering operation."""

    def __init__(self, library_path: Path, instrument_path: Path, sample_rate: int, *, preload: int = 0):
        if library_path.name.lower() != "sfizz.dll" or not library_path.is_file():
            raise FileNotFoundError("sfizz.dll is not installed")
        if instrument_path.suffix.lower() != ".sfz" or not instrument_path.is_file():
            raise FileNotFoundError("installed SFZ instrument not found")
        dll = ctypes.CDLL(str(library_path.resolve()))
        dll.sfizz_create_synth.argtypes = []
        dll.sfizz_create_synth.restype = ctypes.c_void_p
        dll.sfizz_free.argtypes = [ctypes.c_void_p]
        dll.sfizz_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_float]
        dll.sfizz_set_samples_per_block.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.sfizz_load_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        dll.sfizz_load_file.restype = ctypes.c_bool
        dll.sfizz_send_note_on.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.sfizz_send_note_off.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.sfizz_send_cc.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.sfizz_render_block.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
            ctypes.c_int, ctypes.c_int]
        self.dll = dll
        self.handle = dll.sfizz_create_synth()
        if not self.handle:
            raise RuntimeError("sfizz could not create a synth")
        try:
            dll.sfizz_set_sample_rate(self.handle, float(sample_rate))
            dll.sfizz_set_samples_per_block(self.handle, _BLOCK)
            if preload:
                dll.sfizz_set_preload_size.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                dll.sfizz_set_preload_size.restype = None
                dll.sfizz_set_preload_size(self.handle, preload)
            if not dll.sfizz_load_file(
                    self.handle, str(instrument_path.resolve()).encode("utf-8")):
                raise RuntimeError("sfizz could not load the installed SFZ")
        except BaseException:
            self.close()
            raise
        self._left = (ctypes.c_float * _BLOCK)()
        self._right = (ctypes.c_float * _BLOCK)()
        self._channels = (ctypes.POINTER(ctypes.c_float) * 2)(
            ctypes.cast(self._left, ctypes.POINTER(ctypes.c_float)),
            ctypes.cast(self._right, ctypes.POINTER(ctypes.c_float)))
        self._left_view = np.ctypeslib.as_array(self._left)
        self._right_view = np.ctypeslib.as_array(self._right)

    def send(self, event: _Event, offset: int) -> None:
        if event.kind == "on":
            self.dll.sfizz_send_note_on(self.handle, offset, event.data, event.value)
        elif event.kind == "off":
            self.dll.sfizz_send_note_off(self.handle, offset, event.data, event.value)
        else:
            self.dll.sfizz_send_cc(self.handle, offset, event.data, event.value)

    def block(self, frame_count: int) -> np.ndarray:
        self.dll.sfizz_render_block(self.handle, self._channels, 2, frame_count)
        # Stereo int16 output compatible with PianoSoundSource and write_wav.
        left = self._left_view[:frame_count]
        right = self._right_view[:frame_count]
        stereo = np.stack((left, right), axis=-1)
        return np.rint(np.clip(stereo, -0.96, 0.96) * 32767).astype("<i2").reshape(-1)

    def close(self) -> None:
        if self.handle:
            self.dll.sfizz_free(self.handle)
            self.handle = None

class SfizzRenderer:
    """Offline native SFZ renderer for a vetted optional instrument pack.

    No disk I/O or loading occurs on the audio pull thread. Product realtime
    routing is deliberately unchanged until a separate timing gate passes.
    """

    output_channels = 2

    def __init__(self, dll_path: Path | str, instrument_path: Path | str):
        self.dll_path = Path(dll_path)
        self.instrument_path = Path(instrument_path)
        if not self.dll_path.is_file():
            raise FileNotFoundError("sfizz.dll is missing")
        if not self.instrument_path.is_file():
            raise FileNotFoundError("SFZ instrument is missing")

    def snapshot_renderer(self):
        return self

    def render_note(
        self, pitch: int, duration: float, velocity: int,
        sample_rate: int = SAMPLE_RATE,
    ) -> array:
        return self.render_project(
            (Note.create(pitch=pitch, start_time=0, duration=duration, velocity=velocity),),
            sample_rate=sample_rate)

    def render_project(
        self, notes: Iterable[Note], sample_rate: int = SAMPLE_RATE,
        *, controls: Iterable[ControlEvent] = (),
        should_cancel: Callable[[], bool] | None = None,
    ) -> array:
        events, total_frames = _events_for_score(
            notes, controls=controls, sample_rate=sample_rate)
        # One native synth per export avoids cross-thread races and stale pedal
        # state, and guarantees complete release when cancellation occurs.
        synth = self._open_synth(sample_rate)
        try:
            output = array("h")
            index = 0
            position = 0
            while position < total_frames:
                if should_cancel is not None and should_cancel():
                    return array("h")
                count = min(_BLOCK, total_frames - position)
                limit = position + count
                while index < len(events) and events[index].frame < limit:
                    synth.send(events[index], events[index].frame - position)
                    index += 1
                output.frombytes(synth.block(count).tobytes())
                position = limit
            return output
        finally:
            synth.close()

    def _open_synth(self, sample_rate: int):
        return _NativeSfizz(self.dll_path, self.instrument_path, sample_rate)

    def render_project_cancellable(
        self, notes: Iterable[Note], should_cancel: Callable[[], bool]
    ) -> array:
        return self.render_project(notes, should_cancel=should_cancel)
