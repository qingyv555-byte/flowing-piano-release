from __future__ import annotations

import logging
from math import isfinite
from pathlib import Path

from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot

from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.audio.preview import AudioPreviewManager
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, PianoVoiceProfile, ReleaseMode
from ai_piano_studio.audio.selection_audition import SelectionAuditionController
from ai_piano_studio.audio.source import PianoSoundSource
from ai_piano_studio.audio.sound_gain import PLAYBACK_GAIN, SOURCE_TRIM_LIMIT, scale_export, source_gain
from ai_piano_studio.audio.sound_parameters import SoundParameters, map_velocities, process_export
from ai_piano_studio.audio.synth import SAMPLE_RATE, render_project, write_wav
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import sustain_snapshot
from ai_piano_studio.editor.controller import EditorController


logger = logging.getLogger(__name__)


class _AudioExportSignals(QObject):
    succeeded = Signal(str)
    failed = Signal(str)


class _AudioExportJob(QRunnable):
    def __init__(
        self,
        path: Path,
        notes,
        profile: PianoVoiceProfile,
        renderer: PianoSoundSource | None = None,
        gain: float = 1.0,
        parameters: SoundParameters = SoundParameters(),
    ) -> None:
        super().__init__()
        self._path = path
        self._notes = notes
        self._profile = profile
        self._renderer = renderer
        self._gain = gain
        self._parameters = parameters
        self.signals = _AudioExportSignals()

    def run(self) -> None:
        try:
            rate = getattr(self._renderer, 'export_sample_rate', None)
            notes = map_velocities(self._notes, self._parameters.response)
            if self._renderer is None:
                samples = render_project(notes, profile=self._profile)
            elif rate is None:
                samples = self._renderer.render_project(notes)
            else:
                samples = self._renderer.render_project(notes, sample_rate=rate)
            samples, channels = process_export(scale_export(samples, self._gain), rate or SAMPLE_RATE,
                getattr(self._renderer, 'output_channels', 1), self._parameters)
            write_wav(
                self._path,
                samples,
                sample_rate=rate or SAMPLE_RATE,
                channels=channels,
            )
            self.signals.succeeded.emit(str(self._path))
        except Exception as error:
            logger.exception("Audio export worker failed")
            self.signals.failed.emit(str(error))


class PlaybackController(QObject):
    projectSoundChanged = Signal()
    """Bridges the editor state and the audio player.

    The editor remains the single source of truth; this controller only
    reads note snapshots and never mutates project state.
    """

    playingChanged = Signal()
    preparingChanged = Signal()
    positionChanged = Signal()
    activePitchesChanged = Signal()
    visualMetricsChanged = Signal()
    durationChanged = Signal()
    detachedPreviewChanged = Signal()
    volumeChanged = Signal()
    playbackRateChanged = Signal()
    audioExportingChanged = Signal()
    soloSelectionChanged = Signal()
    feedbackRequested = Signal(str)
    audioExported = Signal(str)
    audioFailed = Signal(str)
    transportInterrupted = Signal()

    def __init__(
        self,
        editor: EditorController,
        player: AudioPlayer | None = None,
        parent=None,
        piano_profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
        piano_source: PianoSoundSource | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self.piano_source = piano_source or getattr(player, "piano_source", None)
        self._player = (
            player
            if player is not None
            else AudioPlayer(self, piano_source=self.piano_source)
        )
        self._selection_audition = SelectionAuditionController(
            self._editor, self, self._player, self
        )
        self.piano_profile = piano_profile
        self._position = 0.0
        self._playing = False
        self._pause_requested = False
        self._temporary_audition_active = False
        self._solo_selection_enabled = False
        self._playing_notes: tuple[Note, ...] | None = None
        self._detached_preview_active = False
        self._detached_duration = 0.0
        self._detached_transition = False
        self._active_pitches: list[int] = []
        self._effective_snapshot_key: tuple[object, object] | None = None
        self._effective_snapshot: tuple[Note, ...] = ()
        self._total_seconds = self._compute_total()
        self._volume = 0.65
        self._playback_rate = 1.0
        self._is_exporting = False
        self._export_jobs: set[_AudioExportJob] = set()
        self._native_sound = None
        self._sound_job = None
        self._sound_id = 'salamander'
        self._sound_status = ''
        self._sound_trims = {'salamander': 0.0, 'upright-sfz': 0.0}
        self._sound_parameters = {key: SoundParameters() for key in self._sound_trims}
        self._sound_settings = None
        self._sound_parameter_preview = False
        temporary_finished = getattr(self._player, 'temporaryFinished', None)
        if temporary_finished is not None:
            temporary_finished.connect(self.restoreSoundParameterPreview)
        self._sound_timer = QTimer(self)
        self._sound_timer.setInterval(15)
        self._sound_timer.timeout.connect(self._finish_sound_selection)
        self._preview_manager = AudioPreviewManager(
            play=self.startAudition,
            stop=self.releaseAudition,
        )
        self._drag_preview_mode = "grid"
        self._audition_generation = 0
        self._project_session_id = self._editor.projectSessionId
        self._preview_manager.set_mode("single")
        failure_signal = getattr(self._player, "playbackFailed", None)
        if failure_signal is not None:
            failure_signal.connect(self._on_playback_failed)
        volume_setter = getattr(self._player, "set_volume", None)
        if volume_setter is not None:
            volume_setter(self._volume)
        rate_setter = getattr(self._player, "set_playback_rate", None)
        if rate_setter is not None:
            rate_setter(self._playback_rate)
        self._player.positionChanged.connect(self._on_position)
        self._player.playingChanged.connect(self._on_playing)
        preparing = getattr(self._player, 'preparingChanged', None)
        if preparing is not None:
            preparing.connect(lambda _value: self.preparingChanged.emit())
        self._prefetched_snapshot = None
        self._editor.selectionChanged.connect(self._audition_selection)
        self._editor.noteCreated.connect(self.auditionNoteWithDuration)
        self._editor.stateChanged.connect(self._on_editor_state)
        self._editor.gesturePreviewRequested.connect(self._preview_gesture_note)
        self._editor.gesturePreviewFinished.connect(self._finish_gesture_preview)

    def _on_playback_failed(self, message: str) -> None:
        self.transportInterrupted.emit()
        logger.error("Asynchronous playback failure: %s", message)
        self.feedbackRequested.emit("播放失败，请检查系统音频输出后重试")

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(bool, notify=preparingChanged)
    def preparing(self) -> bool:
        query = getattr(self._player, 'is_preparing', None)
        return bool(query()) if callable(query) else False

    @Property(QObject, constant=True)
    def selectionAudition(self) -> SelectionAuditionController:
        return self._selection_audition

    @Property(bool, notify=soloSelectionChanged)
    def soloSelection(self) -> bool:
        return self._solo_selection_enabled

    @Property(float, notify=positionChanged)
    def positionSeconds(self) -> float:
        return self._position

    @Property("QVariantList", notify=activePitchesChanged)
    def activePitches(self) -> list[int]:
        return self._active_pitches

    @Property(float, notify=visualMetricsChanged)
    def visualNoteDensity(self) -> float:
        """Return a bounded local note-density signal for visual effects only."""
        notes = self._visual_window_notes()
        return min(1.0, len(notes) / 8.0)

    @Property(float, notify=visualMetricsChanged)
    def visualEnergy(self) -> float:
        """Combine local density, active voices, and velocity without changing music data."""
        active = [
            note
            for note in (self._playing_notes if self._detached_preview_active
                         else self._editor.state.effective_notes(for_playback=True))
            if note.start_time <= self._position < note.start_time + note.duration
        ]
        if not active:
            return 0.0
        average_velocity = sum(note.velocity for note in active) / (127.0 * len(active))
        polyphony = min(1.0, len(active) / 6.0)
        density = self.visualNoteDensity
        return min(1.0, 0.18 + average_velocity * 0.42 + polyphony * 0.22 + density * 0.18)

    @Property(float, notify=visualMetricsChanged)
    def visualPitchBand(self) -> float:
        """Return the current register as 0..1 for a secondary color influence."""
        active = self._active_pitches
        if not active:
            return 0.5
        return max(0.0, min(1.0, (sum(active) / len(active) - 36.0) / 60.0))

    @Property(float, notify=durationChanged)
    def totalSeconds(self) -> float:
        if self._detached_preview_active and self._playing_notes:
            return self._detached_duration
        return self._total_seconds

    @Property("QVariantList", notify=detachedPreviewChanged)
    def detachedPreviewNotes(self) -> list[dict]:
        if not self._detached_preview_active or not self._playing_notes:
            return []
        return [dict(pitch=n.pitch, startTime=n.start_time, duration=n.duration)
                for n in self._playing_notes]

    def _clear_detached_preview(self) -> None:
        if self._detached_preview_active:
            self._detached_preview_active = False
            self.detachedPreviewChanged.emit()
            self.durationChanged.emit()

    @Property(float, notify=volumeChanged)
    def volume(self) -> float:
        return self._volume

    @Property(float, notify=playbackRateChanged)
    def playbackRate(self) -> float:
        return self._playback_rate

    @Property(bool, notify=audioExportingChanged)
    def audioExporting(self) -> bool:
        return self._is_exporting

    def _compute_total(self) -> float:
        return self._editor.state.playback_duration

    def _compute_active_pitches(self) -> list[int]:
        position = self._position
        return sorted(
            {
                note.pitch
                for note in (self._playing_notes if self._detached_preview_active and self._playing_notes
                             else self._playback_snapshot_notes())
                if note.start_time <= position < note.start_time + note.duration
            }
        )

    def _playback_snapshot_notes(self) -> tuple[Note, ...]:
        state = self._editor.state
        key = self._effective_snapshot_key
        if key is None or key[0] is not state.playback_notes or key[1] is not state.layers or key[2] is not state.playback_controls:
            self._effective_snapshot = sustain_snapshot(state.effective_notes(for_playback=True), state.effective_controls(True))
            self._effective_snapshot_key = (state.playback_notes, state.layers, state.playback_controls)
        notes = self._effective_snapshot
        if not self._solo_selection_enabled:
            return notes
        return tuple(
            note for note in notes if note.id in self._editor.state.selected_ids
        )

    def _refresh_active_pitches(self) -> None:
        active = self._compute_active_pitches()
        if active != self._active_pitches:
            self._active_pitches = active
            self.activePitchesChanged.emit()

    def _on_editor_state(self) -> None:
        project_session_id = self._editor.projectSessionId
        if project_session_id != self._project_session_id:
            self._selection_audition.stop()
            self._temporary_audition_active = False
            if self._solo_selection_enabled:
                self._solo_selection_enabled = False
                self.soloSelectionChanged.emit()
                self._selection_audition.soloSelectionChanged.emit()
            self._clear_detached_preview()
            self._player.stop(ReleaseMode.STOP)
            self.stopAudition(ReleaseMode.STOP.value)
            self._project_session_id = project_session_id
            self._playing_notes = None
            self._on_position(0.0)
            if self._playing:
                self._playing = False
                self.playingChanged.emit()
        total = self._compute_total()
        if total != self._total_seconds:
            self._total_seconds = total
            self.durationChanged.emit()
        notes = self._playback_snapshot_notes()
        if notes is not self._prefetched_snapshot:
            self._prefetched_snapshot = notes
            prepare = getattr(self._player, 'prefetch_notes', None)
            if callable(prepare):
                prepare(notes, self._position)
        if self._solo_selection_enabled and self._playing and not notes:
            self.stop()
            return
        if (
            self._playing
            and not self._detached_preview_active
            and notes != self._playing_notes
        ):
            self._refresh_playing_snapshot(notes)
        self._refresh_active_pitches()
        self.visualMetricsChanged.emit()

    def _refresh_playing_snapshot(self, notes: tuple[Note, ...]) -> None:
        """Replace an in-flight render while retaining its playhead position."""
        if not notes:
            self.stop()
            self._playing_notes = ()
            return
        try:
            position = min(self._position, self._total_seconds)
            self._player.play_notes(notes)
            self._player.seek(position)
            self._playing_notes = notes
        except Exception:
            logger.exception("Playback snapshot refresh failed")
            self.feedbackRequested.emit("播放中的音符更新失败")

    @Slot()
    def play(self) -> None:
        # A loop restart is deferred by one event turn so stale completion
        # signals cannot re-enter the old session.  Cancel it explicitly
        # before normal transport starts, even if the old temporary session
        # already reported itself finished.
        self._selection_audition.stop()
        self._temporary_audition_active = False
        notes = self._playback_snapshot_notes()
        if not notes:
            self.feedbackRequested.emit(
                "请先选择音符再播放" if self._solo_selection_enabled
                else "请先创建音符再播放"
            )
            return
        try:
            desired_position = min(self._position, self._total_seconds)
            self._pause_requested = False
            self._clear_detached_preview()
            snapshot = tuple(notes)
            play_at = getattr(self._player, 'play_notes_at', None)
            if callable(play_at):
                play_at(snapshot, desired_position)
            else:
                self._player.play_notes(snapshot)
            if desired_position > 0:
                player_position = getattr(self._player, "position_seconds", None)
                current_position = (
                    float(player_position()) if callable(player_position) else None
                )
                if current_position is None or abs(current_position - desired_position) > 1e-6:
                    self._player.seek(desired_position)
            self._playing_notes = snapshot
        except Exception:
            logger.exception("Playback failed")
            self.transportInterrupted.emit()
            self.feedbackRequested.emit("播放失败，请重试")

    def resumeRecordingAt(self, seconds: float) -> None:
        """Resume backing at the recorder's boundary without ending its take."""
        self._on_position(min(max(0.0, seconds), self._total_seconds))
        self.play()

    @Slot()
    def pause(self) -> None:
        self.transportInterrupted.emit()
        self._selection_audition.stop()
        self._temporary_audition_active = False
        self._pause_requested = True
        self._player.pause()
        if self._sound_id == 'upright-sfz':
            self._on_position(self._player.position_seconds())
        if self._playing:
            self._playing = False
            self.playingChanged.emit()

    @Slot()
    def stop(self) -> None:
        self.transportInterrupted.emit()
        self._selection_audition.stop()
        self._temporary_audition_active = False
        self._pause_requested = True
        self._clear_detached_preview()
        self.stopAudition(ReleaseMode.STOP.value)
        self._player.stop(ReleaseMode.STOP)
        self._on_position(0.0)

    @Slot(bool)
    def setSoloSelection(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._solo_selection_enabled:
            return
        self._solo_selection_enabled = value
        self.soloSelectionChanged.emit()
        self._selection_audition.soloSelectionChanged.emit()
        if not self._playing:
            return
        notes = self._playback_snapshot_notes()
        if not notes:
            self.stop()
            return
        if notes != self._playing_notes:
            self._refresh_playing_snapshot(notes)

    def _start_temporary_audition(self, notes: tuple[Note, ...]) -> None:
        if not isinstance(notes, tuple) or not notes:
            raise ValueError("temporary audition requires a non-empty note tuple")
        if self._playing:
            # Selection audition owns a separate temporary session. Stop the
            # normal transport without letting its stopped signal reset the
            # user's main playhead; a later normal Play can resume explicitly.
            self._pause_requested = True
            self._playing = False
            self.playingChanged.emit()
        self._temporary_audition_active = True
        try:
            self._player.play_temporary_notes(notes)
        except Exception:
            self._temporary_audition_active = False
            raise

    def _stop_temporary_audition(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        stop = getattr(self._player, "stop_temporary_notes", None)
        if callable(stop):
            try:
                stop(mode)
            except TypeError:
                stop()
        self._temporary_audition_active = False

    def _finish_temporary_audition(self) -> None:
        finish = getattr(self._player, "finish_temporary_notes", None)
        if callable(finish):
            finish()
        self._temporary_audition_active = False

    def play_detached_notes(self, notes: tuple[Note, ...]) -> None:
        """Play a temporary snapshot without making it the editor's project state."""
        if not isinstance(notes, tuple) or not notes or any(
            not isinstance(note, Note) for note in notes
        ):
            raise ValueError("notes must be a non-empty tuple of Note")
        self.stop()
        self._detached_preview_active = True
        self._pause_requested = False
        self._playing_notes = notes
        self._detached_duration = max(note.start_time + note.duration for note in notes)
        self.detachedPreviewChanged.emit()
        self.durationChanged.emit()
        self._detached_transition = True
        try:
            self._player.play_notes(notes)
        except Exception:
            self._clear_detached_preview()
            raise
        finally:
            self._detached_transition = False

    def stop_detached_preview(self) -> None:
        """Stop a proposal snapshot if one is active, leaving the editor untouched."""
        if self._detached_preview_active:
            self.stop()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        self.transportInterrupted.emit()
        self._selection_audition.stop()
        target = min(max(0.0, float(seconds)), self.totalSeconds)
        self._detached_transition = self._detached_preview_active
        try:
            self._player.seek(target)
        finally:
            self._detached_transition = False
        self._on_position(target)

    @Slot(int, int, result=int)
    def startAudition(self, pitch: int, velocity: int = 75) -> int:
        return self._start_audition(pitch, velocity)

    @Slot(int, int, result=int)
    def auditionNote(self, pitch: int, velocity: int = 75) -> int:
        """Backward-compatible alias for callers that do not need the voice id."""
        return self.startAudition(pitch, velocity)

    @Slot(int, int, float)
    def auditionNoteWithDuration(self, pitch: int, velocity: int, duration: float) -> None:
        voice_id = self._start_audition(pitch, velocity, duration)
        self._schedule_natural_release(voice_id, duration)

    @Slot(int, str)
    @Slot(int)
    def releaseAudition(
        self, voice_id: int, mode: str = ReleaseMode.NATURAL.value
    ) -> None:
        try:
            release = getattr(self._player, "release_audition", None)
            if release is not None:
                release(int(voice_id), ReleaseMode(mode))
        except Exception:
            logger.exception("Audition voice release failed")

    @Slot(str)
    @Slot()
    def stopAudition(self, mode: str = ReleaseMode.SAFE.value) -> None:
        self._audition_generation += 1
        self._preview_manager.stop_all(mode)
        self._stop_preview_audio(mode)

    @Slot(float)
    def setVolume(self, volume: float) -> None:
        value = max(0.0, min(1.0, float(volume)))
        if value != self._volume:
            self._volume = value
            setter = getattr(self._player, "set_volume", None)
            if setter is not None:
                setter(value)
            self.volumeChanged.emit()

    @Slot(float)
    def setPlaybackRate(self, rate: float) -> None:
        if self._editor.recordingTakeActive:
            self.feedbackRequested.emit('录制中保持当前播放速度')
            return
        value = max(0.5, min(2.0, float(rate)))
        if value != self._playback_rate:
            self._playback_rate = value
            setter = getattr(self._player, "set_playback_rate", None)
            if setter is not None:
                setter(value)
            self.playbackRateChanged.emit()

    @Slot(str)
    def exportAudio(self, raw_path: str) -> None:
        """Render the current project to a shareable WAV file."""
        self._editor.recordingBoundaryRequested.emit()
        if self._is_exporting:
            self.feedbackRequested.emit("正在导出音频，请稍候")
            return
        try:
            self._editor.synchronizeNow()
            if self._editor.state.has_unsynced_changes:
                raise ValueError('finish the current edit before audio export')
            notes = sustain_snapshot(self._editor.state.effective_notes(for_playback=True), self._editor.state.effective_controls(True))
            if not notes:
                raise ValueError("project has no notes to export")
            url = QUrl(raw_path)
            path = Path(url.toLocalFile() if url.isLocalFile() else raw_path)
            if self._sound_id == 'upright-sfz':
                renderer = self._native_sound.renderer
            else:
                renderer = self.piano_source.snapshot_renderer() if self.piano_source is not None else None
            job = _AudioExportJob(path, notes, self.piano_profile, renderer,
                                  self.project_sound_gain(self._sound_id, export=True), self.project_sound_parameters())
            job.signals.succeeded.connect(lambda exported_path: self._finish_audio_export(job, exported_path))
            job.signals.failed.connect(lambda message: self._finish_audio_export_failure(job, message))
            self._export_jobs.add(job)
            self._is_exporting = True
            self.audioExportingChanged.emit()
            QThreadPool.globalInstance().start(job)
        except Exception as error:
            logger.exception("Audio export failed")
            self.audioFailed.emit(str(error))
            self.feedbackRequested.emit("无法导出音频")

    projectSoundId = Property(str, lambda self: self._sound_id, notify=projectSoundChanged)
    projectSoundAvailable = Property(bool, lambda self: self._native_sound is not None, notify=projectSoundChanged)
    projectSoundPreparing = Property(bool, lambda self: self._sound_job is not None, notify=projectSoundChanged)
    projectSoundStatus = Property(str, lambda self: self._sound_status, notify=projectSoundChanged)
    projectSoundTrim = Property(float, lambda self: self._sound_trims[self._sound_id], notify=projectSoundChanged)
    soundParametersAvailable = Property(bool, lambda self: self.piano_source is not None and
        callable(getattr(getattr(self.piano_source, 'mixer', None), 'set_sound_parameters', None)), notify=projectSoundChanged)
    projectSoundBrightness = Property(float, lambda self: self.project_sound_parameters().brightness, notify=projectSoundChanged)
    projectSoundResponse = Property(float, lambda self: self.project_sound_parameters().response, notify=projectSoundChanged)
    projectSoundReverb = Property(float, lambda self: self.project_sound_parameters().reverb, notify=projectSoundChanged)
    projectSoundWidth = Property(float, lambda self: self.project_sound_parameters().width, notify=projectSoundChanged)

    def project_sound_parameters(self, sound_id=None):
        return self._sound_parameters[sound_id or self._sound_id]

    def _apply_sound_parameters(self, *, reset=False):
        parameters = self.project_sound_parameters()
        self._player.velocity_response_value = parameters.response
        if self._native_sound is not None:
            self._native_sound.velocity_response_value = parameters.response
        if self.soundParametersAvailable:
            self.piano_source.mixer.set_sound_parameters(parameters, reset=reset)

    @Slot(str, float)
    def setProjectSoundParameter(self, name, value):
        try:
            parameters = self.project_sound_parameters().with_value(name, value)
        except (ValueError, TypeError):
            return
        self._sound_parameters[self._sound_id] = parameters
        if self._sound_settings is not None:
            self._sound_settings.setValue(f'sourceParameters/{self._sound_id}/{name}', getattr(parameters, name))
        self._apply_sound_parameters()
        self.projectSoundChanged.emit()

    @Slot()
    def resetProjectSoundParameters(self):
        self.setProjectSoundTrim(0)
        for name, value in vars(SoundParameters()).items():
            self.setProjectSoundParameter(name, value)

    @Slot()
    def auditionCurrentSound(self):
        self._start_temporary_audition(tuple(Note.create(pitch=pitch, velocity=80,
            start_time=index * .18, duration=.8) for index, pitch in enumerate((60, 64, 67))))

    def _sound_draft(self, values):
        source = values['soundId']
        parameters = self.project_sound_parameters(source)
        for name, key in (('brightness', 'tone'), ('response', 'response'), ('reverb', 'reverb'), ('width', 'width')):
            parameters = parameters.with_value(name, values[key])
        return source, parameters, self._bounded_sound_trim(values['sourceTrim'])

    @Slot(str, 'QVariantMap')
    def applySoundSettings(self, sound_id, values):
        try:
            source, parameters, trim = self._sound_draft(values)
        except (KeyError, ValueError, TypeError):
            return
        if source != sound_id:
            return
        self.restoreSoundParameterPreview()
        self._sound_parameters[source] = parameters
        self._sound_trims[source] = trim
        if self._sound_settings is not None:
            self._sound_settings.setValue(f'sourceTrim/{source}', trim)
            for name, value in vars(parameters).items():
                self._sound_settings.setValue(f'sourceParameters/{source}/{name}', value)
        if source == self._sound_id:
            self._apply_sound_parameters()
            self._apply_sound_gains()
        self.projectSoundChanged.emit()

    @Slot('QVariantMap')
    def previewSoundParameters(self, values):
        try:
            source, parameters, trim = self._sound_draft(values)
        except (KeyError, ValueError, TypeError):
            return
        if source != self._sound_id:
            self.feedbackRequested.emit('音源已切换，请重新打开声音设置后试听。')
            return
        self.restoreSoundParameterPreview()
        self._stop_temporary_audition(ReleaseMode.STOP)
        self._player.velocity_response_value = parameters.response
        if self._native_sound is not None:
            self._native_sound.velocity_response_value = parameters.response
        self.piano_source.mixer.set_sound_parameters(parameters, reset=True)
        gains = {key: self.project_sound_gain(key) for key in self._sound_trims}
        gains[source] *= 10 ** ((trim - self._sound_trims[source]) / 20)
        self.piano_source.mixer.set_sound_gains(gains['salamander'], gains['upright-sfz'])
        try:
            self.auditionCurrentSound()
        except Exception:
            self._apply_sound_parameters(reset=True)
            self._apply_sound_gains()
            raise
        self._sound_parameter_preview = True

    @Slot()
    def restoreSoundParameterPreview(self):
        if not self._sound_parameter_preview:
            return
        self._sound_parameter_preview = False
        self._stop_temporary_audition(ReleaseMode.STOP)
        self._apply_sound_parameters(reset=True)
        self._apply_sound_gains()

    @Slot()
    def auditionCurrentSoundExcerpt(self):
        from .sfz_preview import _preview_notes
        notes = _preview_notes(self._editor.state, self._position)
        if not notes:
            self.feedbackRequested.emit('当前播放位置没有音符；可先回到歌曲开头。')
            return
        self._start_temporary_audition(notes)

    @Slot()
    def stopCurrentSoundPreview(self):
        self.restoreSoundParameterPreview()
        self._stop_temporary_audition(ReleaseMode.STOP)

    def configure_project_sound(self, pack: Path, *, settings=None) -> None:
        from .native_playback import NativePlaybackEngine

        try:
            self._native_sound = NativePlaybackEngine(pack, self.piano_source.mixer.sample_rate)
        except FileNotFoundError:
            # The built-in source still owns usable tuning controls when the
            # optional SFZ pack is absent.
            self._native_sound = None
        self._sound_settings = settings
        if settings is not None:
            for sound_id in self._sound_trims:
                try:
                    self._sound_trims[sound_id] = self._bounded_sound_trim(settings.value(f'sourceTrim/{sound_id}', 0))
                except (ValueError, TypeError):
                    self._sound_trims[sound_id] = 0.0
                parameters = self._sound_parameters[sound_id]
                for name, default in vars(parameters).items():
                    try:
                        parameters = parameters.with_value(name, settings.value(f'sourceParameters/{sound_id}/{name}', default))
                    except (ValueError, TypeError):
                        continue
                self._sound_parameters[sound_id] = parameters
        self._apply_sound_gains()
        self._apply_sound_parameters(reset=True)
        self.projectSoundChanged.emit()

    @staticmethod
    def _bounded_sound_trim(value) -> float:
        value = float(value)
        if not isfinite(value):
            raise ValueError('sound trim must be finite')
        return min(SOURCE_TRIM_LIMIT, max(-SOURCE_TRIM_LIMIT, value))

    def project_sound_gain(self, sound_id: str, *, export: bool = False) -> float:
        if self._native_sound is None:
            return 10 ** (self._sound_trims[sound_id] / 20) * (1.0 if export else PLAYBACK_GAIN)
        return source_gain(sound_id, self._sound_trims[sound_id], export=export)

    def _apply_sound_gains(self) -> None:
        self.piano_source.mixer.set_sound_gains(
            self.project_sound_gain('salamander'), self.project_sound_gain('upright-sfz'))

    @Slot(float)
    def setProjectSoundTrim(self, db: float) -> None:
        if not self.soundParametersAvailable or not isfinite(db):
            return
        value = self._bounded_sound_trim(db)
        if value == self.projectSoundTrim:
            return
        self._sound_trims[self._sound_id] = value
        if self._sound_settings is not None:
            self._sound_settings.setValue(f'sourceTrim/{self._sound_id}', value)
        self._apply_sound_gains()
        self.projectSoundChanged.emit()

    @Slot(str)
    def selectProjectSound(self, sound_id: str) -> None:
        if sound_id not in ('salamander', 'upright-sfz') or self._native_sound is None:
            return
        if sound_id == 'salamander':
            if self._sound_job is not None:
                self._sound_job.cancel()
                self._sound_job = None
                self._sound_timer.stop()
                self._native_sound.drop_warm()
            self._commit_sound(sound_id)
        elif self._sound_job is None and self._sound_id != sound_id:
            self._sound_job = self._native_sound.prewarm()
            self._sound_status = '正在校验并预热立式钢琴…'
            self._sound_timer.start()
            self.projectSoundChanged.emit()

    def _finish_sound_selection(self) -> None:
        job = self._sound_job
        if job is None or not job.done():
            return
        self._sound_job = None
        self._sound_timer.stop()
        try:
            job.result()
        except Exception as error:
            logger.exception('SFZ prewarm failed')
            self._sound_status = f'立式钢琴不可用：{error}'
            self.projectSoundChanged.emit()
            return
        self._commit_sound('upright-sfz')

    def _commit_sound(self, sound_id: str) -> None:
        self.restoreSoundParameterPreview()
        if self._sound_id != sound_id:
            resume = (self._playing or self.preparing) and not self._temporary_audition_active and not self._detached_preview_active
            position = self._player.position_seconds() if self._playing and resume else self._position
            self._pause_requested = True
            self.transportInterrupted.emit()
            self._player.use_native_project_engine(self._native_sound if sound_id == 'upright-sfz' else None)
            self._sound_id = sound_id
            self._apply_sound_parameters(reset=True)
            self._on_position(position)
            if sound_id == 'salamander' and self._native_sound is not None:
                self._native_sound.drop_warm()
            if resume:
                self.play()
        self._sound_status = '立式钢琴已就绪' if sound_id == 'upright-sfz' else '已选择 Salamander'
        self.projectSoundChanged.emit()

    def shutdown_project_sound(self) -> None:
        if self._native_sound is None:
            return
        self._sound_timer.stop()
        if self._sound_job is not None:
            self._sound_job.cancel()
            self._sound_job = None
        if self._sound_id == 'upright-sfz':
            self._player.use_native_project_engine(None)
        self._native_sound.close()
        self._native_sound = None

    def _finish_audio_export(self, job: _AudioExportJob, path: str) -> None:
        self._export_jobs.discard(job)
        self._is_exporting = bool(self._export_jobs)
        self.audioExportingChanged.emit()
        self.audioExported.emit(path)

    def _finish_audio_export_failure(self, job: _AudioExportJob, message: str) -> None:
        self._export_jobs.discard(job)
        self._is_exporting = bool(self._export_jobs)
        self.audioExportingChanged.emit()
        self.audioFailed.emit(message)
        self.feedbackRequested.emit("无法导出音频")

    @Slot(str)
    def setDragPreviewMode(self, mode: str) -> None:
        value = "grid" if mode in {"single", "release"} else mode
        if value not in {"off", "grid", "continuous"}:
            value = "grid"
        self._drag_preview_mode = value
        self._preview_manager.set_mode("single" if value == "grid" else value)

    def _start_audition(
        self, pitch: int, velocity: int, duration: float | None = None
    ) -> int:
        try:
            voice_id = self._player.audition(pitch, velocity, duration)
            return voice_id if isinstance(voice_id, int) else -1
        except Exception:
            logger.exception("Audition voice start failed")
            return -1

    def _stop_preview_audio(self, mode: str) -> None:
        try:
            stop = getattr(self._player, "stop_audition", None)
            if stop is not None:
                stop(ReleaseMode(mode))
        except TypeError:
            try:
                stop()  # type: ignore[misc]
            except Exception:
                logger.exception("Audition release-all failed")
        except Exception:
            logger.exception("Audition release-all failed")

    def _schedule_natural_release(self, voice_id: int, duration: float) -> None:
        if voice_id < 0:
            return
        generation = self._audition_generation
        QTimer.singleShot(
            max(0, round(float(duration) * 1000)),
            lambda: self._release_scheduled_voice(voice_id, generation),
        )

    def _release_scheduled_voice(self, voice_id: int, generation: int) -> None:
        if generation != self._audition_generation:
            return
        self.releaseAudition(voice_id, ReleaseMode.NATURAL.value)

    def _preview_gesture_note(self, pitch: int, velocity: int) -> None:
        self._preview_manager.preview(pitch, velocity)

    def _finish_gesture_preview(self, committed: bool) -> None:
        return None

    def _audition_selection(self) -> None:
        selected = self._editor.state.selected_notes
        if len(selected) == 1:
            note = selected[0]
            self.auditionNoteWithDuration(note.pitch, note.velocity, note.duration)

    def _on_position(self, seconds: float) -> None:
        self._position = seconds
        self._refresh_active_pitches()
        self.positionChanged.emit()
        self.visualMetricsChanged.emit()

    def _visual_window_notes(self) -> tuple[Note, ...]:
        start = self._position - 0.5
        end = self._position + 0.75
        return tuple(
            note
            for note in (self._playing_notes if self._detached_preview_active and self._playing_notes
                         else self._editor.state.effective_notes(for_playback=True))
            if note.start_time < end and note.start_time + note.duration >= start
        )

    def _on_playing(self, playing: bool) -> None:
        if not playing and self._detached_transition:
            return
        effective_playing = bool(playing) and not self._pause_requested
        if not effective_playing:
            self._clear_detached_preview()
        if not effective_playing and not self._pause_requested:
            self._on_position(0.0)
        if effective_playing == self._playing:
            return
        self._playing = effective_playing
        self.playingChanged.emit()
