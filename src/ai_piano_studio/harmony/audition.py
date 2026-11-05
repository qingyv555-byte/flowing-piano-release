from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import QSoundEffect

from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, PianoVoiceProfile, ReleaseMode
from ai_piano_studio.audio.mixer import MASTER_OUTPUT_GAIN
from ai_piano_studio.audio.source import PianoSoundSource
from ai_piano_studio.audio.synth import render_project, write_wav
from ai_piano_studio.domain.note import Note


AUDITION_DURATION_SECONDS = 1.2
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _RetiringEffect:
    timer: QTimer
    started_at: float
    duration: float
    start_volume: float


class HarmonyAuditionPlayer(QObject):
    """Play a rendered chord without involving project playback state."""

    playingChanged = Signal()
    effectCountChanged = Signal()
    failed = Signal(str)

    def __init__(
        self,
        parent=None,
        effect: QSoundEffect | None = None,
        renderer: Callable[[Iterable[Note]], object] = render_project,
        effect_factory: Callable[[], QSoundEffect] | None = None,
        profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
        clock: Callable[[], float] | None = None,
        piano_source: PianoSoundSource | None = None,
    ) -> None:
        super().__init__(parent)
        self._effect = effect or QSoundEffect(self)
        if self._effect.parent() is None:
            self._effect.setParent(self)
        self._effect_factory = effect_factory or (lambda: QSoundEffect(self))
        self._renderer = renderer
        self.piano_profile = profile
        self.piano_source = piano_source
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="ai-piano-harmony-")
        self._file_serial = 0
        self._playing = False
        self._retiring_effects: dict[QSoundEffect, _RetiringEffect] = {}
        self._available_effects: list[QSoundEffect] = []
        self._effect_volumes: dict[QSoundEffect, float] = {}
        self._clock = clock or time.monotonic
        self._closing = False
        self._connect_effect(self._effect)
        self.destroyed.connect(self._cleanup)

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(int, notify=effectCountChanged)
    def effectCount(self) -> int:
        return int(self._effect is not None) + len(self._retiring_effects)

    @Slot("QVariantList", int, float)
    def audition(self, pitches: list[int], velocity: int, volume: float) -> None:
        if self._closing:
            return
        try:
            notes = tuple(
                Note.create(
                    id=f"harmony-audition-{self._file_serial}-{index}",
                    pitch=int(pitch),
                    start_time=0.0,
                    duration=AUDITION_DURATION_SECONDS,
                    velocity=int(velocity),
                )
                for index, pitch in enumerate(pitches)
            )
            if not notes:
                return
            self._file_serial += 1
            path = Path(self._temporary_directory.name) / f"chord-{self._file_serial}.wav"
            renderer = (
                self.piano_source.snapshot_renderer()
                if self.piano_source is not None
                else None
            )
            rendered = (
                renderer.render_project(notes)
                if renderer is not None
                else self._render(notes)
            )
            write_wav(
                path,
                rendered,
                channels=getattr(renderer, "output_channels", 1),
            )
            if self._effect is not None and self._effect.isPlaying():
                self._retire_effect(self._effect, ReleaseMode.SAFE)
                self._effect = self._new_effect()
            effect = self._effect or self._new_effect()
            self._effect = effect
            effect.setSource(QUrl.fromLocalFile(str(path)))
            boosted_volume = min(1.0, max(0.0, float(volume)) * MASTER_OUTPUT_GAIN)
            effect.setVolume(boosted_volume)
            self._effect_volumes[effect] = boosted_volume
            effect.play()
            self.effectCountChanged.emit()
            self._set_playing(True)
        except Exception:
            logger.exception("Harmony audition failed")
            self.failed.emit("无法试听和弦")

    @Slot()
    def stop(self, mode: ReleaseMode = ReleaseMode.SAFE) -> None:
        if self._effect is not None:
            self._retire_effect(self._effect, mode)
            self._effect = None
            self.effectCountChanged.emit()
        self._set_playing(False)

    @Slot()
    def close(self) -> None:
        self._closing = True
        if self._effect is not None:
            self._retire_effect(self._effect, ReleaseMode.STOP)
            self._effect = None
            self.effectCountChanged.emit()
        for effect in tuple(self._retiring_effects):
            self._retire_effect(effect, ReleaseMode.STOP)
        self._set_playing(False)
        self._finish_close_if_settled()

    def _new_effect(self) -> QSoundEffect:
        if self._available_effects:
            return self._available_effects.pop()
        effect = self._effect_factory()
        if effect.parent() is None:
            effect.setParent(self)
        self._connect_effect(effect)
        return effect

    def _connect_effect(self, effect: QSoundEffect) -> None:
        effect.playingChanged.connect(lambda: self._effect_playing_changed(effect))

    def _render(self, notes: Iterable[Note]) -> object:
        if self._renderer is render_project:
            return render_project(notes, profile=self.piano_profile)
        return self._renderer(notes)

    def _retire_effect(self, effect: QSoundEffect, mode: ReleaseMode) -> None:
        if not self._closing:
            for retiring in tuple(self._retiring_effects):
                if retiring is not effect:
                    self._finish_retirement(retiring)
        previous = self._retiring_effects.pop(effect, None)
        if previous is not None:
            previous.timer.stop()
            previous.timer.deleteLater()
        timer = QTimer(self)
        duration = (
            self.piano_profile.stop_fade_seconds
            if mode is ReleaseMode.STOP
            else self.piano_profile.safe_release_seconds
        )
        initial_volume = (
            self._retirement_volume(previous)
            if previous is not None
            else self._effect_volumes.pop(effect, 1.0)
        )
        timer.setInterval(max(1, min(16, round(duration * 1000))))
        timer.timeout.connect(self.process_retirements)
        self._retiring_effects[effect] = _RetiringEffect(
            timer=timer,
            started_at=self._clock(),
            duration=duration,
            start_volume=initial_volume,
        )
        timer.start()

    def process_retirements(self) -> None:
        for effect, retirement in tuple(self._retiring_effects.items()):
            progress = min(
                1.0,
                max(0.0, self._clock() - retirement.started_at) / retirement.duration,
            )
            effect.setVolume(retirement.start_volume * (1.0 - progress))
            if progress >= 1.0:
                self._finish_retirement(effect)

    def _retirement_volume(self, retirement: _RetiringEffect) -> float:
        progress = min(
            1.0,
            max(0.0, self._clock() - retirement.started_at) / retirement.duration,
        )
        return retirement.start_volume * (1.0 - progress)

    def _finish_retirement(self, effect: QSoundEffect) -> None:
        retirement = self._retiring_effects.pop(effect, None)
        if retirement is not None:
            retirement.timer.stop()
            retirement.timer.deleteLater()
        self._effect_volumes.pop(effect, None)
        try:
            effect.stop()
            effect.setSource(QUrl())
        except Exception:
            logger.exception("Harmony audition effect retirement failed")
        if not self._closing and effect is not self._effect:
            self._available_effects.append(effect)
        self.effectCountChanged.emit()
        self._finish_close_if_settled()

    def _finish_close_if_settled(self) -> None:
        if self._closing and not self._retiring_effects:
            self._available_effects.clear()
            self._cleanup()

    def _effect_playing_changed(self, effect: QSoundEffect) -> None:
        if effect is self._effect:
            self._set_playing(bool(effect.isPlaying()))

    def _set_playing(self, value: bool) -> None:
        if value == self._playing:
            return
        self._playing = value
        self.playingChanged.emit()

    def _cleanup(self, *_args) -> None:
        self._temporary_directory.cleanup()
