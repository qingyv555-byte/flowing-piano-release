"""Opt-in Studio preview of an installed SFZ, not a silent global sound switch.

P0: prepare a bounded 8-second excerpt on a worker using the same native SFZ
renderer used for WAV export. Only an explicit user click may start QMediaPlayer.
The existing project source/mixer, user project and user settings are unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import logging
from threading import Event

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Signal, Slot, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from ai_piano_studio.audio.sfizz_renderer import SfizzRenderer
from ai_piano_studio.audio.sound_gain import scale_export
from ai_piano_studio.audio.synth import write_wav
from ai_piano_studio.domain.control_event import sustain_snapshot, sounding_duration
from ai_piano_studio.domain.note import Note
from ai_piano_studio.audio.sound_parameters import SoundParameters, map_velocities, process_export

log = logging.getLogger(__name__)
_PREVIEW_SECONDS = 8.0


class _RenderSignals(QObject):
    ready = Signal(int, str)
    failed = Signal(int, str)


class _PreviewJob(QRunnable):
    def __init__(
        self, generation: int, native: Path, sfz: Path,
        notes: tuple[Note, ...], destination: Path, gain: float = 1.0, parameters=SoundParameters(),
    ):
        super().__init__()
        self.generation = generation
        self.native = native
        self.sfz = sfz
        self.notes = notes
        self.destination = destination
        self.gain = gain
        self.parameters = parameters
        self.signals = _RenderSignals()
        self.cancelled = Event()

    def run(self):
        try:
            renderer = SfizzRenderer(self.native, self.sfz)
            samples = renderer.render_project(map_velocities(self.notes, self.parameters.response), should_cancel=self.cancelled.is_set)
            if self.cancelled.is_set():
                self.signals.failed.emit(self.generation, "试听已取消")
                return
            samples, channels = process_export(scale_export(samples, self.gain), 44100, 2, self.parameters)
            write_wav(self.destination, samples, channels=channels)
            if self.cancelled.is_set():
                self.destination.unlink(missing_ok=True)
                self.signals.failed.emit(self.generation, "试听已取消")
                return
            self.signals.ready.emit(self.generation, str(self.destination))
        except Exception:
            log.exception("Could not render optional SFZ preview")
            self.destination.unlink(missing_ok=True)
            self.signals.failed.emit(self.generation, "立式钢琴试听准备失败，请检查音源文件。")


def _preview_notes(state, start_seconds: float) -> tuple[Note, ...]:
    limit = start_seconds + _PREVIEW_SECONDS
    wrapped = sustain_snapshot(state.playback_notes, state.controls)
    result: list[Note] = []
    for item in wrapped:
        beginning = max(start_seconds, item.start_time)
        ending = min(limit, item.start_time + sounding_duration(item))
        if ending <= beginning:
            continue
        # New audio-only snapshots never enter the editor or History.
        result.append(replace(
            item.note, start_time=beginning - start_seconds,
            duration=ending - beginning))
    return tuple(result)


class SfzPreviewController(QObject):
    changed = Signal()

    def __init__(self, editor, playback, pack_dir: Path, parent=None):
        super().__init__(parent)
        self._editor, self._playback = editor, playback
        self._pack = Path(pack_dir)
        self._dll = self._pack / "native" / "sfizz.dll"
        self._sfz = self._pack / "instrument-full" / "UprightPianoKW-20220221.sfz"
        self._status = "未开始"
        self._busy = False
        self._closed = False
        self._generation = 0
        self._temp = TemporaryDirectory(prefix="flowing-sfz-preview-")
        self._job = None
        self._jobs: dict[int, _PreviewJob] = {}
        self._media = None
        self._output = None

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return self._dll.is_file() and self._sfz.is_file() and not self._closed

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def statusMessage(self) -> str:
        return self._status

    def _update(self, message: str, busy: bool = False):
        self._status, self._busy = message, busy
        self.changed.emit()

    @Slot()
    def audition(self):
        if not self.available or self._busy:
            self._update("立式钢琴音源尚未安装或正在准备。")
            return
        try:
            position = max(0, float(self._playback.positionSeconds))
            state = self._editor.state
            # At a completed song position, start at the opening.
            if position >= state.playback_duration:
                position = 0
            notes = _preview_notes(state, position)
            if not notes:
                self._update("当前播放位置没有音符；可先回到歌曲开头。")
                return
            if self._media is not None:
                self._media.stop()
            self._generation += 1
            generation = self._generation
            target = Path(self._temp.name) / f"upright-{generation}.wav"
            source_gain = getattr(self._playback, 'project_sound_gain', None)
            gain = source_gain('upright-sfz', export=True) if callable(source_gain) else 1.0
            parameters_getter = getattr(self._playback, 'project_sound_parameters', None)
            parameters = parameters_getter('upright-sfz') if callable(parameters_getter) else SoundParameters()
            job = _PreviewJob(generation, self._dll, self._sfz, notes, target, gain, parameters)
            self._job = job
            self._jobs[generation] = job
            job.signals.ready.connect(self._ready)
            job.signals.failed.connect(self._failed)
            self._update("正在准备立式钢琴音源…", True)
            QThreadPool.globalInstance().start(job)
        except Exception:
            log.exception("Could not schedule SFZ preview")
            self._update("立式钢琴试听未能开始。")

    @Slot(int, str)
    def _ready(self, generation: int, filename: str):
        self._jobs.pop(generation, None)
        if self._closed or generation != self._generation:
            Path(filename).unlink(missing_ok=True)
            if self._closed and not self._jobs:
                self._finish_shutdown()
            return
        self._job = None
        try:
            # Only a real user audition action can enter this path. Never
            # start audio at app initialization or during normal tests.
            if self._playback.playing:
                self._playback.stop()
            if self._media is None:
                self._media = QMediaPlayer(self)
                self._output = QAudioOutput(self)
                self._media.setAudioOutput(self._output)
                self._media.errorOccurred.connect(self._media_failed)
            if self._output is not None:
                self._output.setVolume(max(0.0, min(1.0, float(self._playback.volume))))
            self._media.stop()
            self._media.setSource(QUrl.fromLocalFile(filename))
            self._update("正在试听立式钢琴（独立 8 秒片段）")
            self._media.play()
        except Exception:
            log.exception("Could not play SFZ preview")
            self._update("已生成试听，但音频设备无法播放。")

    @Slot(int, str)
    def _failed(self, generation: int, reason: str):
        self._jobs.pop(generation, None)
        if self._closed or generation != self._generation:
            if self._closed and not self._jobs:
                self._finish_shutdown()
            return
        self._job = None
        self._update(reason)

    def _media_failed(self, *_):
        self._update("立式钢琴试听播放失败；已保留原钢琴音源。")

    @Slot()
    def stop(self):
        self._generation += 1
        for job in self._jobs.values():
            job.cancelled.set()
        if self._media is not None:
            self._media.stop()
        self._update("试听已停止")

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.stop()
        if self._media is not None and hasattr(self._media, "setSource"):
            # QtMedia's FFmpeg decoder can hold a Windows WAV handle beyond
            # stop(), preventing temp cleanup until its source is detached.
            self._media.setSource(QUrl())
        # Cleanup only after an in-flight native renderer has exited.
        if not self._jobs:
            self._finish_shutdown()

    def _finish_shutdown(self):
        if self._temp is not None:
            try:
                self._temp.cleanup()
            except OSError:
                log.debug("Preview temporary files still held by decoder", exc_info=True)
            self._temp = None
