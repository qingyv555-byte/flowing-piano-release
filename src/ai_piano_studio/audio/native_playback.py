"""Native SFZ transport and ownership for the single vetted P0 instrument."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import file_digest, sha256
from itertools import islice
import json
from pathlib import Path
from threading import Event

import numpy as np
from PySide6.QtCore import QTimer

from .profile import ReleaseMode
from .project_session import PlaybackState, RealtimePlaybackSession
from .sfizz_renderer import SfizzRenderer, _BLOCK, _NativeSfizz, _events_for_score
from .sound_parameters import velocity_response


_DLL_SHA = 'aa4bfa4ad9a57cfbb3c832a50be14bfe5aeaf7a36ffcdf0178547abe8719e653'
# Sorted (relative path, SHA256) entries for the SFZ and its 66 FLAC files.
# Derived from the previously verified FreePats original archive.
_INSTRUMENT_SHA = '207c73ad81965c2b691ca8ee7ab48871cc23494db4f51367db599df2ee3b9a77'
_SFZ = 'UprightPianoKW-20220221.sfz'
# This exact pack has 66 stereo/mono files, <=585195 frames each and
# 114712384 decoded float bytes total. Preload even the longest stereo file.
_PRELOAD = 1_200_000


def _verify_pack(pack: Path) -> None:
    native = pack / 'native'
    instrument = pack / 'instrument-full'
    samples = instrument / 'samples'
    for directory in (pack, native, instrument, samples):
        if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
            raise ValueError('音源目录不符合已验证的安装结构')
    dll = native / 'sfizz.dll'
    if any(p != dll and p.suffix.lower() == '.dll' for p in native.iterdir()):
        raise ValueError('音源目录包含未知 DLL')
    files = [instrument / _SFZ, *islice(samples.iterdir(), 67)]
    if len(files) != 67:
        raise ValueError('立式钢琴采样不完整')
    hashes = []
    total_size = 0
    for path in [dll, *files]:
        if path.is_symlink() or path.is_junction() or not path.is_file() or path.stat().st_size > 40_000_000:
            raise ValueError('音源文件不符合已验证的安装结构')
        total_size += path.stat().st_size
        if total_size > 50_000_000:
            raise ValueError('音源文件体积超过已验证上限')
        with path.open('rb') as handle:
            digest = file_digest(handle, 'sha256').hexdigest()
        if path == dll:
            if digest != _DLL_SHA:
                raise ValueError('sfizz DLL 校验失败')
        else:
            hashes.append((path.relative_to(instrument).as_posix(), digest))
    canonical = json.dumps(sorted(hashes), ensure_ascii=True, separators=(',', ':')).encode()
    if sha256(canonical).hexdigest() != _INSTRUMENT_SHA:
        raise ValueError('立式钢琴谱表或采样校验失败')


class VerifiedSfizzRenderer(SfizzRenderer):
    def __init__(self, pack: Path, sample_rate: int):
        self.pack = pack
        self.export_sample_rate = sample_rate
        super().__init__(pack / 'native' / 'sfizz.dll', pack / 'instrument-full' / _SFZ)

    def _open_synth(self, sample_rate):
        _verify_pack(self.pack)
        return _NativeSfizz(self.dll_path, self.instrument_path, sample_rate, preload=_PRELOAD)


class NativeScoreStream:
    """Pull native blocks, retaining at most one partial block between pulls."""

    def __init__(self, synth, events, total_frames, response=lambda: .5):
        self.synth = synth
        self.response = response
        self.events = events
        self.total_frames = total_frames
        self.cursor = 0
        self.rendered = 0
        self._event_index = 0
        self._pending = np.empty((0, 2), dtype='<i2')
        self.fade_in = 0
        self.failure = None
        self.last_frame = np.zeros(2, dtype='<i2')

    def _block(self):
        count = min(_BLOCK, self.total_frames - self.rendered)
        limit = self.rendered + count
        while self._event_index < len(self.events) and self.events[self._event_index].frame < limit:
            event = self.events[self._event_index]
            if event.kind == 'on':
                velocity = velocity_response(event.value, self.response())
                if velocity != event.value:
                    event = replace(event, value=velocity)
            self.synth.send(event, event.frame - self.rendered)
            self._event_index += 1
        self._pending = self.synth.block(count).reshape(-1, 2)
        self.rendered = limit

    def pull(self, frames):
        output = np.zeros((frames, 2), dtype='<i2')
        written = 0
        while written < frames and self.cursor < self.total_frames:
            if not len(self._pending):
                self._block()
            count = min(frames - written, len(self._pending))
            output[written:written + count] = self._pending[:count]
            self._pending = self._pending[count:]
            self.cursor += count
            written += count
        if self.fade_in:
            count = min(frames, self.fade_in)
            start = 1 - self.fade_in / self.fade_length
            gain = start + np.arange(count) / self.fade_length
            output[:count] = np.rint(output[:count] * gain[:, None]).astype('<i2')
            self.fade_in -= count
        if frames:
            self.last_frame = output[-1].copy()
        return output

    def close(self):
        self.synth.close()


class NativePlaybackEngine:
    """One worker owns loading, seek pre-roll and disposal. Qt pulls only PCM."""

    def __init__(self, pack: Path, sample_rate: int):
        self.renderer = VerifiedSfizzRenderer(pack, sample_rate)
        self.sample_rate = sample_rate
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sfizz-playback')
        self._warm = None
        self.velocity_response_value = .5

    def _open(self):
        return self.renderer._open_synth(self.sample_rate)

    def prewarm(self):
        return self._executor.submit(self._prewarm)

    def _prewarm(self):
        if self._warm is None:
            self._warm = self._open()

    def prepare(self, notes, position, rate, cancelled):
        return self._executor.submit(self._prepare, notes, position, rate, cancelled)

    def _prepare(self, notes, position, rate, cancelled):
        if cancelled.is_set():
            return None
        events, total = _events_for_score(notes, sample_rate=self.sample_rate, playback_rate=rate)
        synth = self._warm or self._open()
        self._warm = None
        stream = NativeScoreStream(synth, events, total, lambda: self.velocity_response_value)
        try:
            target = min(total, round(position / rate * self.sample_rate))
            if target:
                # A seek restores the actual native voice and pedal state. It
                # discards the prefix one block at a time, never caches a song.
                while stream.cursor < target and not cancelled.is_set():
                    stream.pull(min(_BLOCK, target - stream.cursor))
                stream.fade_length = stream.fade_in = max(1, round(.012 * self.sample_rate))
            if cancelled.is_set():
                stream.close()
                return None
            return stream
        except BaseException:
            stream.close()
            raise

    def retire(self, stream):
        self._executor.submit(stream.close)

    def abandon(self, future):
        if not future.cancel():
            def discard():
                if future.exception() is None:
                    stream = future.result()
                    if stream is not None:
                        stream.close()
            self._executor.submit(discard)

    def _release_warm(self):
        if self._warm is not None:
            self._warm.close()
            self._warm = None

    def drop_warm(self):
        self._executor.submit(self._release_warm)

    def close(self):
        self.drop_warm()
        self._executor.shutdown()


class NativePlaybackSession(RealtimePlaybackSession):
    """Use the existing Qt transport and device clock with a native score stream."""

    def __init__(self, notes, engine, mixer, **kwargs):
        super().__init__(notes, None, mixer, **kwargs)
        self._engine = engine
        self._stream = None
        self._job = None
        self._cancel = None
        self._native_timer = QTimer(self)
        self._native_timer.setInterval(10)
        self._native_timer.timeout.connect(self._poll_native)

    @property
    def position_seconds(self):
        if self.is_playing and self._mixer.presented_audio_frame() is None:
            return self._last_presentation
        return super().position_seconds

    def _prepare_samples(self, *, resume_existing=False):
        self._is_paused = False
        self._cancel = Event()
        self._set_state(PlaybackState.PREPARING)
        self._job = self._engine.prepare(self._notes, self._position, self._playback_rate, self._cancel)
        self._native_timer.start()

    def set_playback_rate(self, rate):
        preparing = self.is_preparing and self._job is not None
        super().set_playback_rate(rate)
        if preparing:
            self.seek(self._position)

    def _poll_native(self):
        if self._job is None or not self._job.done():
            return
        job, self._job = self._job, None
        self._native_timer.stop()
        try:
            self._stream = job.result()
            if self._stream is None:
                raise RuntimeError('立式钢琴准备已取消')
            self._last_presentation = self._position
            self._frame_origin = self._mixer.attach_native_stream(self._stream)
            self._completion_frame = self._frame_origin + self._stream.total_frames - self._stream.cursor
            self._begin_preparing()
        except Exception as error:
            self._stop(ReleaseMode.STOP, preserve_pause=False)
            self.preparationFailed.emit(str(error))

    def _on_audio_ready_changed(self, ready):
        if self._stream is not None:
            super()._on_audio_ready_changed(ready)

    def _stop(self, mode, *, preserve_pause):
        self._native_timer.stop()
        if self._job is not None:
            self._cancel.set()
            self._engine.abandon(self._job)
            self._job = None
        if self._stream is not None:
            stream, self._stream = self._stream, None
            self._mixer.detach_native_stream(stream, fade=mode is not ReleaseMode.NATURAL)
            self._engine.retire(stream)
        self._timer.stop()
        self._audio_clock_origin = None
        self._is_paused = self._needs_prime_on_resume = preserve_pause
        self._set_state(PlaybackState.PAUSED if preserve_pause else PlaybackState.STOPPED)

    def process_due_events(self):
        if not self.is_playing:
            return
        if self._stream.failure is not None:
            message = self._stream.failure
            self.stop(ReleaseMode.STOP)
            self.preparationFailed.emit(message)
            return
        self.positionChanged.emit(self.position_seconds)
        frame = self._mixer.presented_audio_frame()
        if frame is not None and frame >= self._completion_frame:
            self._position = self._project_end_seconds
            self.stop(ReleaseMode.NATURAL)
            self.finished.emit()
