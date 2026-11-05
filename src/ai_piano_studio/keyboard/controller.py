from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Property, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.domain.project import MIN_NOTE_DURATION
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.keyboard.layout import (
    DEFAULT_OCTAVE,
    clamp_octave,
)
from ai_piano_studio.keyboard.settings import PlayingSettings, AVAILABLE_KEYS, validate_settings
from ai_piano_studio.domain.note import Note, midi_to_name
from ai_piano_studio.domain.control_event import ControlEvent


logger = logging.getLogger(__name__)

_DEFAULT_DURATION = 0.5
_TIME_TICK_MS = 33


class KeyboardController(QObject):
    """Turns the computer keyboard into a piano.

    A S D F G H J play C D E F G A B in the current octave; Up/Down shift
    the octave. While recording, each key press appends a note to the
    project as a single undoable gesture.
    """

    octaveChanged = Signal()
    recordingChanged = Signal()
    recordTimeChanged = Signal()
    lastRecordedNoteChanged = Signal()
    feedbackRequested = Signal(str)
    keyNoteOn = Signal(int)
    keyNoteOff = Signal(int)
    preferencesChanged = Signal()
    inputStateChanged = Signal()

    def __init__(
        self,
        editor: EditorController,
        playback: PlaybackController,
        clock: Callable[[], float] | None = None,
        parent=None,
        settings: PlayingSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._playback = playback
        self._clock = clock or time.monotonic
        self._octave = DEFAULT_OCTAVE
        self._recording = False
        self._stage = 'idle'
        self._pedal_sources = set()
        self._sustained = set()
        self._pedal_tokens = set()
        self._capture_releases = set()
        self._event_order = 0
        self._take_notes = {}
        self._take_controls = []
        self._take_dirty = False
        self._target_layer = ''
        self._countdown = 0
        self._record_rate = 1.0
        self._click = None
        self._pedal_lane = False
        self._suspended = set()
        self._target_channel = None
        self._target_track = None
        self._record_origin = 0.0
        self._record_offset = 0.0
        self._record_seconds = 0.0
        self._preview_velocity = 75
        self._preview_duration = _DEFAULT_DURATION
        self._input_enabled = False
        self.preferences = settings or PlayingSettings()
        self._draft = self.preferences.snapshot()
        self._settings_error = self.preferences.warning
        self._capture_row = -2  # -2 idle, -1 pedal, otherwise mapping row
        self._trial_enabled = False
        self._resume_trial_after_capture = False
        self._owned_voice_ids: set[int] = set()
        self._native_keys: dict[int, object] = {}
        self._pitch_counts: dict[int, int] = {}
        self._key_map = {}
        self._apply_profile()
        self._held: dict[object, tuple[str | None, float, int, int | None]] = {}
        self._tapped_pitch_counts: dict[int, int] = {}
        self._tap_generation = 0
        self._recorded_ids: list[str] = []
        self._last_recorded_note_id = ""
        self._ticker = QTimer(self)
        self._ticker.setInterval(_TIME_TICK_MS)
        self._ticker.timeout.connect(self._tick)
        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(15)
        self._monitor_timer.timeout.connect(self._monitor_tick)
        self._monitor_beat = -1
        playback.playingChanged.connect(self._monitor_state)
        source = getattr(playback, 'piano_source', None)
        mixer = getattr(source, 'mixer', None)
        if mixer is not None:
            mixer.voiceFinished.connect(self._voice_finished)
            mixer.audioReadyChanged.connect(self._audio_ready_changed)
        editor.recordingBoundaryRequested.connect(self.stopRecording)
        if hasattr(playback, 'transportInterrupted'):
            playback.transportInterrupted.connect(self.stopRecording)
        app = QGuiApplication.instance()
        if app is not None:
            app.focusObjectChanged.connect(self._focus_changed)

    @Property('QVariantMap', notify=preferencesChanged)
    def settingsDraft(self):
        return self._draft

    @Property(str, notify=preferencesChanged)
    def settingsError(self):
        return self._settings_error

    @Property(bool, notify=inputStateChanged)
    def tryingKeys(self):
        return self._trial_enabled

    @Property(bool, notify=inputStateChanged)
    def capturingKey(self):
        return self._capture_row != -2

    @Property('QVariantList', notify=inputStateChanged)
    def heldKeys(self):
        return [midi_to_name(held[2]) for held in self._held.values()]

    @Property('QVariantList', notify=preferencesChanged)
    def keyHints(self):
        return [{'key': chr(key), 'pitch': midi_to_name(pitch + (self._octave - 4) * 12) if 21 <= pitch + (self._octave - 4) * 12 <= 108 else '超出音域'}
                for key, pitch in self._key_map.items()]

    def _apply_profile(self, profile=None):
        profile = profile or self.preferences.profile
        self._key_map = {row['key']: row['pitch'] for row in profile['mappings'] if row['enabled']}
        self._pedal_key = profile['pedalKey']

    @Slot()
    def beginSettings(self):
        if self.recording:
            self.feedbackRequested.emit('请先结束录制再修改演奏设置')
            return
        self.releaseAll()
        self._draft = self.preferences.snapshot()
        self._settings_error = self.preferences.warning
        self.preferencesChanged.emit()

    @Slot('QVariantMap', result=bool)
    def updateSettingsDraft(self, values):
        if self.recording or self._held:
            self._settings_error = '请先停止录制并松开全部琴键'
            self.preferencesChanged.emit()
            return False
        try:
            self._draft = validate_settings(values)
            self._settings_error = ''
            if self._trial_enabled:
                self._apply_profile(next(p for p in self._draft['profiles']
                                         if p['id'] == self._draft['activeProfile']))
        except (ValueError, TypeError) as exc:
            self._settings_error = str(exc)
            self.preferencesChanged.emit()
            return False
        self.preferencesChanged.emit()
        return True

    @Slot(result=bool)
    def applySettings(self):
        if self.recording or self._held:
            self._settings_error = '请先停止录制并松开全部琴键'
            self.preferencesChanged.emit()
            return False
        try:
            self.preferences.apply(self._draft)
        except (ValueError, OSError) as exc:
            self._settings_error = f'未能保存，原方案未改变：{exc}'
            self.preferencesChanged.emit()
            return False
        self.cancelSettings()
        return True

    @Slot()
    def cancelSettings(self):
        self.releaseAll()
        self._trial_enabled = False
        self._resume_trial_after_capture = False
        self._capture_row = -2
        self._draft = self.preferences.snapshot()
        self._apply_profile()
        self.inputStateChanged.emit()
        self.preferencesChanged.emit()
        self._monitor_state()

    @Slot(str, bool)
    def suspendInput(self, owner, suspended):
        if suspended:
            self._suspended.add(owner)
            self.releaseAll()
        else:
            self._suspended.discard(owner)

    @Property(bool, notify=inputStateChanged)
    def showPedalLane(self):
        return self._pedal_lane

    @Slot()
    def togglePedalLane(self):
        self._pedal_lane = not self._pedal_lane
        self.inputStateChanged.emit()

    def _voice_finished(self, identifier):
        prefix, _, number = identifier.partition(':')
        if prefix == 'audition' and number.isdigit():
            voice_id = int(number)
            self._owned_voice_ids.discard(voice_id)
            self._sustained.discard(voice_id)

    def _audio_ready_changed(self, ready):
        if not ready and self._stage in ('countin','recording'):
            self.releaseAll()
            self.feedbackRequested.emit('音频输出中断，已录内容已保留')

    @Slot()
    def stopAllSounds(self):
        self.releaseAll()
        stop = getattr(self._playback, 'stop', None)
        if callable(stop):
            stop()
        stop_audition = getattr(self._playback,'stopAudition',None)
        if callable(stop_audition):
            stop_audition('stop')
        mixer = getattr(getattr(self._playback,'piano_source',None),'mixer',None)
        if mixer is not None:
            mixer.release_all(ReleaseMode.STOP)
        if self._click is not None:
            self._click.stop()

    def _monitor_state(self, *_):
        self._monitor_beat = -1
        if getattr(self._playback, 'playing', False) and self.preferences.snapshot()['metronome'] == 'always':
            self._monitor_timer.start()
        else:
            self._monitor_timer.stop()

    def _monitor_tick(self):
        if self._recording:
            return
        tempo = self._editor.state.playback_tempo
        seconds_per_beat = 60.0 / tempo.bpm * 4 / tempo.beat_unit
        beat = int(self._playback.positionSeconds / seconds_per_beat)
        if beat == self._monitor_beat:
            return
        self._monitor_beat = beat
        mixer = getattr(getattr(self._playback, 'piano_source', None), 'mixer', None)
        if mixer is not None and mixer.is_audio_ready():
            from ai_piano_studio.keyboard.metronome import MonitorClick
            if self._click is None:
                self._click = MonitorClick(mixer)
            self._click.tick(beat % tempo.beats_per_bar == 0, self.preferences.snapshot()['metronomeVolume'])

    @Slot(bool)
    def setTryKeys(self, enabled):
        if self.recording:
            return
        self.releaseAll()
        self._trial_enabled = bool(enabled)
        self._resume_trial_after_capture = False
        self._capture_row = -2
        self._apply_profile(next(p for p in self._draft['profiles']
                                 if p['id'] == self._draft['activeProfile']) if enabled else None)
        self.inputStateChanged.emit()

    @Slot(int)
    def captureKey(self, row):
        if self.recording:
            return
        if row == self._capture_row:
            resume_trial = self._resume_trial_after_capture
            self._capture_row = -2
            self._resume_trial_after_capture = False
            if resume_trial:
                self.setTryKeys(True)
            else:
                self.inputStateChanged.emit()
            return
        profile = next(p for p in self._draft['profiles'] if p['id'] == self._draft['activeProfile'])
        if profile['id'] == 'default' or row < -1 or row >= len(profile['mappings']):
            self._settings_error = '请先复制默认方案，并选择有效映射'
            self.preferencesChanged.emit()
            return
        resume_trial = self._trial_enabled or self._resume_trial_after_capture
        self.releaseAll()
        self._capture_row = row
        self._resume_trial_after_capture = resume_trial
        self._trial_enabled = False
        self.inputStateChanged.emit()

    def _capture(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._capture_row = -2
        elif key in AVAILABLE_KEYS and not (event.modifiers() & (
                Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            from copy import deepcopy
            draft = deepcopy(self._draft)
            profile = next(p for p in draft['profiles'] if p['id'] == draft['activeProfile'])
            if self._capture_row == -1:
                profile['pedalKey'] = key
            else:
                profile['mappings'][self._capture_row]['key'] = key
            if self.updateSettingsDraft(draft):
                self._capture_row = -2
        else:
            self._settings_error = '请选择单独的字母、数字或标点键；Esc 取消'
            self.preferencesChanged.emit()
        if self._capture_row == -2 and self._resume_trial_after_capture:
            self.setTryKeys(True)
        self.inputStateChanged.emit()

    @Slot(QObject)
    def _focus_changed(self, _focus=None):
        if self._typing_focused():
            self.releaseAll()

    @Property(str, notify=recordTimeChanged)
    def recordingStatus(self):
        if self._stage == 'preparing':
            return '正在准备声音…'
        if self._stage == 'countin':
            return f'预备 {self._countdown}'
        return f'录制 · {self._target_metadata.name} · 从 {self._record_offset:.1f}s' if self._recording else ''

    @Property(bool, notify=inputStateChanged)
    def pedalDown(self):
        return bool(self._pedal_sources)

    @Slot(bool)
    def setPedal(self, down):
        self._set_pedal('screen', down)

    def _set_pedal(self, source, down):
        received_at = self._clock()
        self._advance_capture()
        before = self.pedalDown
        if down:
            self._pedal_sources.add(source)
        else:
            self._pedal_sources.discard(source)
        if before == self.pedalDown:
            return
        if self._stage == 'recording':
            self._record_control(127 if self.pedalDown else 0, self._record_elapsed(received_at))
        if not self.pedalDown:
            for voice_id in tuple(self._sustained):
                self._release_voice(voice_id, 'natural')
            self._sustained.clear()
        self.inputStateChanged.emit()

    def _record_control(self, value, at=None):
        self._event_order += 1
        event = ControlEvent.create(time=self._record_elapsed() if at is None else at, value=value,
                                   layer_id=self._target_layer, order=self._event_order,
                                   channel=self._target_channel, track=self._target_track)
        self._take_controls.append(event)
        self._take_dirty = True

    @Property(int, notify=octaveChanged)
    def octave(self) -> int:
        return self._octave

    @Property(bool, notify=recordingChanged)
    def recording(self) -> bool:
        return self._recording

    @Property(float, notify=recordTimeChanged)
    def recordSeconds(self) -> float:
        return self._record_seconds

    @Property(float, notify=recordTimeChanged)
    def recordCursorSeconds(self) -> float:
        """Current recording position, anchored at the transport cursor."""
        return self._record_offset + self._record_seconds

    @Property(str, notify=lastRecordedNoteChanged)
    def lastRecordedNoteId(self) -> str:
        """Expose the newest note so the timeline can confirm recording visually."""
        return self._last_recorded_note_id

    @Slot()
    def octaveUp(self) -> None:
        self._set_octave(self._octave + 1)

    @Slot()
    def octaveDown(self) -> None:
        self._set_octave(self._octave - 1)

    def _set_octave(self, octave: int) -> None:
        clamped = clamp_octave(octave)
        if clamped != self._octave:
            self._octave = clamped
            self.octaveChanged.emit()
            self.preferencesChanged.emit()

    @Slot()
    def toggleRecording(self) -> None:
        if self._recording:
            self.stopRecording()
        else:
            self.startRecording()

    @Slot(bool)
    def setInputEnabled(self, enabled: bool) -> None:
        """Allow physical computer-keyboard input only while the editor owns it."""
        self._input_enabled = bool(enabled)
        if not self._input_enabled:
            self.releaseAll()

    @Slot()
    def releaseAll(self) -> None:
        """Release live keys and preview audio before editor input is suspended."""
        interrupted = self._recording
        self._tap_generation += 1
        if self._recording:
            self._finish_recording(release_voices=False)
        else:
            for token in list(self._held):
                self._note_off(token, release_voice=False)
        for pitch in list(self._tapped_pitch_counts):
            self.keyNoteOff.emit(pitch)
        self._tapped_pitch_counts.clear()
        self._native_keys.clear()
        self._pedal_sources.clear()
        self._pedal_tokens.clear()
        self._sustained.clear()
        # Only the voices started by this input scope. Other auditions and
        # project playback may share the same source/mixer.
        for voice_id in tuple(self._owned_voice_ids):
            self._release_voice(voice_id, ReleaseMode.SAFE.value)
        self._owned_voice_ids.clear()
        self.inputStateChanged.emit()
        if interrupted:
            self.feedbackRequested.emit('录制已中断，内容已保留')

    @Slot()
    def startRecording(self) -> None:
        try:
            self._start_recording()
        except Exception:
            logger.exception('Recording setup failed')
            if self._recording:
                self.cancelRecording()
            elif self._editor.recordingTakeActive:
                self._editor.finishRecordingTake(cancel=True)
            self.feedbackRequested.emit('录制准备失败，原有内容未改变，请检查声音输出后重试')

    def _start_recording(self) -> None:
        if self._recording:
            return
        if not self._key_map:
            self.feedbackRequested.emit('当前方案没有启用的音符按键；可用屏幕琴键录入，或先到“演奏”添加映射。')
        self._target_layer = self._editor.activeLayerId
        routes = {(n.channel,n.track) for n in self._editor.state.active_notes if n.layer_id == self._target_layer}
        self._target_channel, self._target_track = next(iter(routes)) if len(routes) == 1 else (None, None)
        if not self._editor.beginRecordingTake(self._target_layer):
            self.feedbackRequested.emit('目标音乐层已锁定，或另一个编辑尚未结束')
            return
        # Freeze accompaniment before preparation/count-in. pause() may emit the
        # normal transport boundary; this controller is not recording yet.
        if getattr(self._playback, 'playing', False):
            self._playback.pause()
        self._record_rate = float(getattr(self._playback, 'playbackRate', 1.0))
        self._record_offset = float(getattr(self._playback, 'positionSeconds', 0.0))
        self._record_origin = self._clock()
        self._event_order = max(
            [0, *(e.order for e in self._editor.state.controls),
             *(order or 0 for n in self._editor.state.notes for order in (n.on_order, n.off_order))])
        self._take_notes = {}
        self._take_controls = []
        self._take_dirty = False
        self._target_metadata = self._editor.state.layer(self._target_layer)
        self._monitor_target = not self._target_metadata.muted and (
            not any(layer.solo for layer in self._editor.state.layers) or self._target_metadata.solo)
        if not self._monitor_target:
            self.feedbackRequested.emit('录入正常；目标层受静音/独听限制，不监听新输入。停止后可在音乐层 M / S 调整。')
        self._stage = 'preparing'
        self._prepare_started = self._clock()
        tempo = self._editor.state.tempo
        self._beat_seconds = 60.0 / tempo.bpm * 4 / tempo.beat_unit / self._record_rate
        self._beats_per_bar = tempo.beats_per_bar
        self._count_beats = self.preferences.snapshot()['countInBars'] * tempo.beats_per_bar
        self._next_click = 0
        self._recorded_ids = []
        if self._last_recorded_note_id:
            self._last_recorded_note_id = ""
            self.lastRecordedNoteChanged.emit()
        self._record_seconds = 0.0
        self._recording = True
        self._ticker.start()
        self.recordingChanged.emit()
        self.recordTimeChanged.emit()
        try:
            source = getattr(self._playback, 'piano_source', None)
            self._record_mixer = getattr(source, 'mixer', None)
            if self._record_mixer is not None:
                self._record_mixer.start_audio()
            self._advance_prepare()
        except Exception:
            logger.exception('Recording audio preparation failed')
            self.cancelRecording()
            self.feedbackRequested.emit('声音尚未就绪，本次未开始录制')

    def _advance_prepare(self):
        if self._stage != 'preparing':
            return
        if self._clock() - self._prepare_started > 5:
            self.cancelRecording()
            self.feedbackRequested.emit('音频准备超时，本次未录制')
            return
        if self._record_mixer is not None and not self._record_mixer.is_audio_ready():
            return
        self._record_origin = self._clock() + self._count_beats * self._beat_seconds
        self._stage = 'countin' if self._count_beats else 'recording'
        self._countdown = self._count_beats
        if not self._count_beats:
            self._begin_capture()

    def _begin_capture(self):
        self._stage = 'recording'
        for token, (_, _, pitch, voice_id) in tuple(self._held.items()):
            note_id = self._capture_note(pitch, self._record_offset)
            self._held[token] = (note_id, self._record_offset, pitch, voice_id)
        if self.pedalDown:
            self._record_control(127, self._record_offset)
        play = getattr(self._playback, 'play', None)
        if self._editor.state.playback_notes and callable(play) and not getattr(self._playback,'playing',False):
            resume = getattr(self._playback, 'resumeRecordingAt', None)
            if callable(resume):
                resume(self._record_elapsed())
            else:
                play()

    def _advance_capture(self):
        """Apply the musical boundary before input mutates held key/pedal state.

        The display timer is allowed to arrive late; it never decides which
        side of the boundary an input event belongs to.
        """
        self._advance_prepare()
        if self._stage == 'countin' and self._clock() >= self._record_origin:
            self._begin_capture()

    def _capture_note(self, pitch, start):
        self._event_order += 1
        note = Note.create(pitch=pitch, start_time=start, duration=MIN_NOTE_DURATION,
            velocity=self.preferences.snapshot()['velocity'], status='pending',
            layer_id=self._target_layer, channel=self._target_channel, track=self._target_track,
            on_order=self._event_order, release_mode=self._target_metadata.default_release_mode,
            release_ms=self._target_metadata.default_release_ms)
        self._take_notes[note.id] = note
        self._take_dirty = True
        self._recorded_ids.append(note.id)
        return note.id

    def _flush_take(self):
        if not self._take_dirty:
            return
        self._editor.recordingCall(self._editor.previewRecordedEvents,
            tuple(self._take_notes.values()), tuple(self._take_controls))
        self._take_dirty = False
        if self._recorded_ids and self._last_recorded_note_id != self._recorded_ids[-1]:
            self._last_recorded_note_id = self._recorded_ids[-1]
            self.lastRecordedNoteChanged.emit()

    @Slot()
    def cancelRecording(self):
        self._finish_recording(release_voices=True, cancel=True)

    @Slot()
    def stopRecording(self) -> None:
        self._finish_recording(release_voices=True)

    def _finish_recording(self, *, release_voices: bool, cancel=False) -> None:
        if not self._recording:
            return
        if not cancel:
            self._advance_capture()
        if not self._recording:
            return
        for token in list(self._held):
            self._note_off(token, release_voice=release_voices, advance_boundary=False)
        if self._stage == 'recording' and self.pedalDown:
            self._record_control(0)
        if not cancel:
            self._flush_take()
        for voice_id in tuple(self._sustained):
            self._release_voice(voice_id, 'safe')
        self._sustained.clear()
        self._pedal_sources.clear()
        self._pedal_tokens.clear()
        self._recording = False
        self._stage = 'idle'
        self._ticker.stop()
        if self._click is not None:
            self._click.stop()
        self._editor.finishRecordingTake(cancel)
        if self._recorded_ids and not cancel:
            self._editor.selectNotes(list(self._recorded_ids))
            self.feedbackRequested.emit(f"已录制 {len(self._recorded_ids)} 个音符")
        self.recordingChanged.emit()
        self.recordTimeChanged.emit()
        self.inputStateChanged.emit()

    def eventFilter(self, watched: QObject, event) -> bool:
        if event.type() in {
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.ApplicationStateChange,
        }:
            self.releaseAll()
            return False
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride and self.capturingKey:
            event.accept()
            return True
        if kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        key = event.key()
        scan = event.nativeScanCode()
        token = ('native', scan) if scan else key
        if not scan or (kind == QEvent.Type.KeyRelease and token not in self._held and token not in self._pedal_tokens):
            token = self._native_keys.get(key, token)
        if event.isAutoRepeat():
            return token in self._held or token in self._pedal_tokens or token in self._capture_releases or self.capturingKey
        if kind == QEvent.Type.KeyRelease:
            if token in self._capture_releases:
                self._capture_releases.discard(token)
                return True
            if token in self._pedal_tokens:
                self._pedal_tokens.discard(token)
                self._set_pedal(token, False)
                return True
            if token in self._held:
                self._note_off(token)
                self._native_keys = {k: v for k, v in self._native_keys.items() if v != token}
                return True
            return self.capturingKey
        if self.capturingKey:
            self._capture_releases.add(token)
            self._capture(event)
            return True
        if self._suspended or not (self._input_enabled or self._trial_enabled):
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        if self._typing_focused():
            return False
        if key == self._pedal_key:
            self._native_keys[key] = token
            self._pedal_tokens.add(token)
            self._set_pedal(token, True)
            return True
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            return self._handle_press(key, Qt.NoModifier)
        if key not in self._key_map:
            return False
        self._native_keys[key] = token
        return self._note_on(self._key_map[key] + (self._octave - 4) * 12, token)

    def _handle_press(self, key: int, modifiers) -> bool:
        if modifiers != Qt.KeyboardModifier.NoModifier or self._typing_focused():
            return False
        if key == Qt.Key.Key_Up:
            self.octaveUp()
            return True
        if key == Qt.Key.Key_Down:
            self.octaveDown()
            return True
        if key in self._held:
            return True
        return self.pressKey(key)

    def pressKey(self, key: int) -> bool:
        """Play the note for a key; also record it when recording."""
        base_pitch = self._key_map.get(key)
        pitch = None if base_pitch is None else base_pitch + (self._octave - 4) * 12
        if pitch is None:
            return False
        return self._note_on(pitch, key)

    def releaseKey(self, key: int) -> None:
        """Finish a held note: its duration spans press to release."""
        self._note_off(key)

    @Slot(int)
    def pressPitch(self, pitch: int) -> None:
        """Virtual piano input: play (and record) a pitch directly."""
        pitch = int(pitch)
        if not 0 <= pitch <= 127:
            return
        self._note_on(pitch, ("pitch", pitch))

    @Slot(int)
    def releasePitch(self, pitch: int) -> None:
        """Virtual piano input: finish a pitch started with pressPitch."""
        self._note_off(("pitch", int(pitch)))

    @Slot(int)
    def tapPitch(self, pitch: int) -> None:
        """Audition a virtual piano key for the configured preview duration."""
        pitch = int(pitch)
        if not 0 <= pitch <= 127:
            return
        if self._recording:
            self.pressPitch(pitch)
            generation = self._tap_generation
            QTimer.singleShot(
                max(80, round(self._preview_duration * 1000)),
                lambda: self._finish_tapped_recording_pitch(pitch, generation),
            )
            return
        voice_id = self._start_audition(pitch, self._draft['velocity'] if self._trial_enabled else self._preview_velocity)
        self._tapped_pitch_counts[pitch] = self._tapped_pitch_counts.get(pitch, 0) + 1
        self.keyNoteOn.emit(pitch)
        generation = self._tap_generation
        QTimer.singleShot(
            max(80, round(self._preview_duration * 1000)),
            lambda: self._finish_tapped_pitch(pitch, generation, voice_id),
        )

    @Slot(int, float)
    def setPreviewDefaults(self, velocity: int, duration: float) -> None:
        """Set UI-only audition values for virtual and computer piano input."""
        self._preview_velocity = max(1, min(127, int(velocity)))
        self._preview_duration = max(MIN_NOTE_DURATION, min(4.0, float(duration)))

    def _note_on(self, pitch: int, token: object) -> bool:
        received_at = self._clock()
        was_recording = self._recording
        self._advance_capture()
        if was_recording and not self._recording:
            return False
        received_at = self._record_elapsed(received_at) if self._stage == 'recording' else 0.0
        if not 21 <= pitch <= 108:
            self.feedbackRequested.emit('当前移调超出钢琴音域 A0–C8')
            return False
        if token in self._held:
            return True
        if self._recording:
            voice_id = self._start_audition(pitch, self.preferences.snapshot()['velocity']) if self._monitor_target else None
        else:
            voice_id = self._start_audition(pitch, self._draft['velocity'] if self._trial_enabled else self._preview_velocity)
        if voice_id == -1:
            self.releaseAll()
            self.feedbackRequested.emit('声音未能启动，已保留录入内容，请检查音频输出')
            return False
        note_id: str | None = None
        start_time = 0.0
        if self._stage == 'recording':
            start_time = received_at
            note_id = self._capture_note(pitch, start_time)
        self._held[token] = (note_id, start_time, pitch, voice_id)
        self._pitch_counts[pitch] = self._pitch_counts.get(pitch, 0) + 1
        if self._pitch_counts[pitch] == 1:
            self.keyNoteOn.emit(pitch)
        self.inputStateChanged.emit()
        return True

    def _note_off(self, token: object, *, release_voice: bool = True, advance_boundary=True) -> None:
        received_at = self._clock()
        if advance_boundary:
            self._advance_capture()
        held = self._held.pop(token, None)
        if held is None:
            return
        note_id, start_time, pitch, voice_id = held
        if note_id is not None:
            duration = max(self._record_elapsed(received_at) - start_time, MIN_NOTE_DURATION)
            self._event_order += 1
            self._take_notes[note_id] = self._take_notes[note_id].with_changes(duration=duration, off_order=self._event_order)
            self._take_dirty = True
        self._pitch_counts[pitch] = max(0, self._pitch_counts.get(pitch, 1) - 1)
        if not self._pitch_counts[pitch]:
            self._pitch_counts.pop(pitch, None)
            self.keyNoteOff.emit(pitch)
        self.inputStateChanged.emit()
        if release_voice:
            if self.pedalDown and voice_id is not None and voice_id > 0:
                self._sustained.add(voice_id)
            else:
                self._release_voice(voice_id, ReleaseMode.NATURAL.value)

    def _start_audition(self, pitch: int, velocity: int) -> int | None:
        start = getattr(self._playback, "startAudition", None)
        if start is not None:
            try:
                voice_id = start(pitch, velocity)
                if isinstance(voice_id, int) and voice_id > 0:
                    self._owned_voice_ids.add(voice_id)
                return voice_id
            except Exception:
                logger.exception("Keyboard audition start failed")
                return None
        audition_with_duration = getattr(self._playback, "auditionNoteWithDuration", None)
        try:
            if audition_with_duration is not None:
                audition_with_duration(pitch, velocity, self._preview_duration)
            else:
                self._playback.auditionNote(pitch, velocity)
        except Exception:
            logger.exception("Keyboard compatibility audition failed")
        return None

    def _release_voice(self, voice_id: int | None, mode: str) -> None:
        if mode != 'natural':
            self._owned_voice_ids.discard(voice_id)
        if voice_id is not None and voice_id > 0:
            release = getattr(self._playback, "releaseAudition", None)
            if release is not None:
                try:
                    release(voice_id, mode)
                    return
                except Exception:
                    logger.exception("Keyboard audition voice release failed")

    def _finish_tapped_pitch(
        self, pitch: int, generation: int, voice_id: int | None
    ) -> None:
        if generation != self._tap_generation:
            return
        if self.pedalDown and voice_id is not None and voice_id > 0:
            self._sustained.add(voice_id)
        else:
            self._release_voice(voice_id, ReleaseMode.NATURAL.value)
        remaining = self._tapped_pitch_counts.get(pitch, 0) - 1
        if remaining > 0:
            self._tapped_pitch_counts[pitch] = remaining
            return
        self._tapped_pitch_counts.pop(pitch, None)
        self.keyNoteOff.emit(pitch)

    def _finish_tapped_recording_pitch(self, pitch: int, generation: int) -> None:
        if generation != self._tap_generation:
            return
        self.releasePitch(pitch)

    def _record_elapsed(self, at=None) -> float:
        return self._record_offset + max(0.0, (self._clock() if at is None else at) - self._record_origin) * self._record_rate

    def _tick(self) -> None:
        if not self._recording:
            return
        self._advance_prepare()
        if self._stage == 'preparing' or not self._recording:
            return
        elapsed = self._clock() - self._record_origin
        beat = int((elapsed + self._count_beats * self._beat_seconds) / self._beat_seconds)
        if beat >= self._next_click:
            self._next_click = beat + 1
            enabled = elapsed < 0 or self.preferences.snapshot()['metronome'] in ('record', 'always')
            if enabled and self._record_mixer is not None:
                from ai_piano_studio.keyboard.metronome import MonitorClick
                if self._click is None:
                    self._click = MonitorClick(self._record_mixer)
                self._click.tick(beat % self._beats_per_bar == 0, self.preferences.snapshot()['metronomeVolume'])
        if self._stage == 'countin':
            self._countdown = max(0, self._count_beats - beat)
            if elapsed >= 0:
                self._begin_capture()
                self._flush_take()
            self.recordTimeChanged.emit()
            return
        self._record_seconds = max(0.0, elapsed) * self._record_rate
        for note_id, start_time, _pitch, _voice_id in self._held.values():
            if note_id is not None:
                duration = max(self._record_elapsed() - start_time, MIN_NOTE_DURATION)
                self._take_notes[note_id] = self._take_notes[note_id].with_changes(duration=duration)
                self._take_dirty = True
        self._flush_take()
        self.recordTimeChanged.emit()

    @staticmethod
    def _typing_focused() -> bool:
        focus = QGuiApplication.focusObject()
        if focus is None or not isValid(focus):
            return False
        return any(focus.inherits(name) for name in (
            'QQuickTextInput', 'QQuickTextEdit', 'QLineEdit', 'QTextEdit',
            'QPlainTextEdit', 'QAbstractSpinBox', 'QQuickComboBox', 'QQuickSpinBox',
            'QMenu', 'QDialog'))
