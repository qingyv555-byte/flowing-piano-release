"""Read-only, bounded projection of the existing realtime transport for Studio.

The legacy QML bridge exposes pitches only. Keep the necessary private session
access here: never infer attacks from editor rectangles or create an audio bus.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.project_session import RealtimePlaybackSession


@dataclass(slots=True)
class _AuditionClock:
    origin: float | None
    allow_attack: bool = True


class StudioFeedbackController(QObject):
    enabledChanged = Signal()
    snapshotChanged = Signal()
    liveActiveChanged = Signal()

    def __init__(self, playback: PlaybackController, parent: QObject | None = None):
        super().__init__(parent)
        self._playback = playback
        self._enabled = False
        self._voices: list[dict] = []
        self._epoch: tuple | None = None
        self._notes: dict = {}
        self._layer_snapshot: tuple | None = None
        self._layers: dict = {}
        self._attack_floor = 0.0
        self._attack_floor_epoch: tuple | None = None
        self._mixer = playback._player._mixer
        self._live: dict[str, _AuditionClock] = {}
        self._live_active = False
        playback.playingChanged.connect(self._transport_changed)
        playback.selectionAudition.playingChanged.connect(self._transport_changed)
        playback._player.auditionStarted.connect(self._audition_started)
        # Completion is already emitted by the mixer. Always deliver it on the
        # UI thread, including memory-output tests, never inside its audio lock.
        self._mixer.voiceFinished.connect(self._audition_finished, Qt.QueuedConnection)
        self._mixer.audioReadyChanged.connect(self._refresh_live_active)

    @Property(bool, notify=enabledChanged)
    def enabled(self) -> bool:
        return self._enabled

    @Property("QVariantList", notify=snapshotChanged)
    def voices(self) -> list[dict]:
        return self._voices

    @Property(bool, notify=liveActiveChanged)
    def liveActive(self) -> bool:
        return self._live_active

    @Slot(str)
    def _audition_started(self, voice_id: str) -> None:
        if len(self._live) >= 24:
            active_ids = set(self._mixer.active_voice_ids)
            unrendered = next((identifier for identifier in self._live
                               if identifier not in active_ids), None)
            if unrendered is not None:
                self._live.pop(unrendered)
        # Do not guess the mixer's victim when all 24 are sounding. Its queued
        # completion will reconcile the actual survivors and newly admitted ID.
        if len(self._live) < 24:
            self._live[voice_id] = _AuditionClock(self._mixer.processed_audio_seconds())
        self._refresh_live_active()
        # The demand notification above lets a visible view subscribe. A voice
        # born while hidden/off must not flash when that view later reappears.
        if not self._enabled and voice_id in self._live:
            self._live[voice_id].allow_attack = False

    @Slot(str)
    def _audition_finished(self, voice_id: str) -> None:
        if not voice_id.startswith("audition:"):
            return
        self._live.pop(voice_id, None)
        active_ids = tuple(identifier for identifier in self._mixer.active_voice_ids
                           if identifier.startswith("audition:"))
        # The mixer may prefer a newer released voice over an older held voice.
        # Keep its actual identities, not a second FIFO eviction policy. Missing
        # overflow clocks use rendered PCM age and never replay a delayed attack.
        reconciled = {identifier: self._live.get(identifier, _AuditionClock(None, False))
                      for identifier in active_ids}
        for identifier, clock in self._live.items():
            if len(reconciled) >= 24:
                break
            if identifier not in reconciled:
                reconciled[identifier] = clock  # Accepted, still-pending command.
        self._live = reconciled
        self._replace([row for row in self._voices if row["voiceId"] != voice_id])
        self._refresh_live_active()

    @Slot()
    @Slot(bool)
    def _refresh_live_active(self, _ready: bool | None = None) -> None:
        active = bool(self._live) and self._mixer.is_audio_ready()
        if active != self._live_active:
            self._live_active = active
            if not active:
                for clock in self._live.values():
                    clock.allow_attack = False
                self._replace([row for row in self._voices if row.get("positioned", True)])
            self.liveActiveChanged.emit()

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        if bool(enabled) == self._enabled:
            return
        self._enabled = bool(enabled)
        session, _origin = self._session()
        # Re-entering a visible view cannot turn an existing hold into an attack.
        if session is not None and session.is_playing:
            epoch = (id(session), session._generation, id(session._notes))
            if epoch == self._epoch or session.position_seconds > 0.04:
                self._attack_floor = session.position_seconds + 1e-6
            else:
                self._attack_floor = session._position
            self._attack_floor_epoch = epoch
        self.enabledChanged.emit()
        if not self._enabled:
            for clock in self._live.values():
                clock.allow_attack = False
            self._replace([])

    def _session(self) -> tuple[RealtimePlaybackSession | None, float]:
        player = self._playback._player
        temporary = getattr(player, "_temporary_session", None)
        if isinstance(temporary, RealtimePlaybackSession) and temporary.is_playing:
            return temporary, self._playback.selectionAudition._position_origin_seconds
        project = getattr(player, "_project_session", None)
        if isinstance(project, RealtimePlaybackSession):
            return project, 0.0
        return None, 0.0

    @Slot()
    def _transport_changed(self) -> None:
        session, _origin = self._session()
        if session is None or not session.is_playing:
            if session is not None and (id(session), session._generation, id(session._notes)) == self._epoch:
                # Pause preserves this epoch; stop/seek creates a new epoch and
                # must not carry its old suppression floor into fresh attacks.
                self._attack_floor = session.position_seconds + 1e-6
                self._attack_floor_epoch = self._epoch
            self._replace([row for row in self._voices if not row.get("positioned", True)])

    def _live_rows(self) -> list[dict]:
        if not self._live_active:
            return []
        # Copy only immutable scalars under the same bounded lock as the existing
        # active_voice_ids snapshot. No PCM, Qt notification or score work here.
        with self._mixer._state_lock:
            snapshot = tuple(
                (voice.voice_id, voice.pitch, voice.velocity,
                 voice.envelope_mode is not None,
                 voice.contributions[0].read_position / voice.contributions[0].sample_rate)
                for voice in self._mixer._voices if voice.voice_id in self._live
            )
        consumed = self._mixer.processed_audio_seconds()
        latency = self._mixer.output_latency_seconds() or 0.0
        rows = []
        for voice_id, pitch, velocity, released, rendered_age in snapshot:
            clock = self._live[voice_id]
            if consumed is not None and clock.origin is not None:
                if consumed < clock.origin:
                    # Device clock reset: retain the real hold/release, discard
                    # the old onset instead of replaying it in the new clock.
                    clock.origin = consumed
                    clock.allow_attack = False
                elapsed = consumed - clock.origin - latency
            else:
                elapsed = rendered_age - latency
            if elapsed < 0:
                continue
            rows.append({
                "noteId": "", "voiceId": voice_id, "pitch": pitch,
                "layerId": "", "layerVisible": True, "layerRole": "other",
                "layerCustomColor": "", "startTime": 0.0, "duration": 0.0,
                "velocity": velocity / 127.0, "progress": 0.0,
                "phase": "tail" if released else "held",
                "attack": max(0.0, 1.0 - elapsed / 0.18) if clock.allow_attack else 0.0,
                "elapsedSeconds": elapsed, "positioned": False, "source": "audition",
            })
        return rows

    @Slot()
    def sample(self) -> None:
        """Called at the view's capped refresh rate; owns no timer or music clock."""
        session, origin = self._session()
        if not self._enabled:
            self._replace([])
            return
        live_rows = self._live_rows()
        if session is None or not session.is_playing:
            self._replace(live_rows)
            return
        position = session.position_seconds  # Includes backend output latency.
        epoch = (id(session), session._generation, id(session._notes))
        if epoch != self._epoch:
            self._epoch = epoch
            self._notes = {note.id: note for note in session._notes}
            # Seek primes held notes; those starts are earlier than this origin.
            self._attack_floor = (max(session._position, min(self._attack_floor, position))
                                  if self._attack_floor_epoch == epoch else session._position)
            self._attack_floor_epoch = epoch
        mixer_ids = set(session._mixer.active_voice_ids)
        # MusicLayer is immutable. Refresh identity/color only when its tuple
        # changes, independently of the playback note index. Visibility controls
        # editing only; mute/solo have already filtered the scheduled session.
        layers = self._playback._editor.state.layers
        if layers is not self._layer_snapshot:
            self._layer_snapshot = layers
            self._layers = {layer.id: layer for layer in layers}
        rows = []
        # Mixer polyphony is capped at 24. Exclude queued future notes and stolen
        # voices; a release tail remains present only while its PCM voice exists.
        for voice_id in session._active_voice_ids & mixer_ids:
            note_id = voice_id.split(":", 2)[-1]
            note = self._notes.get(note_id)
            if note is None or note.start_time > position + 1e-6:
                continue
            layer = self._layers.get(note.layer_id)
            elapsed = max(0.0, position - note.start_time)
            attack = (max(0.0, 1.0 - elapsed / 0.18)
                      if note.start_time >= self._attack_floor else 0.0)
            rows.append({
                "noteId": note.id, "voiceId": voice_id, "pitch": note.pitch,
                "layerId": note.layer_id, "startTime": origin + note.start_time,
                "layerVisible": layer.visible if layer is not None else True,
                "layerRole": layer.color_role if layer is not None else "other",
                "layerCustomColor": (layer.custom_color or "") if layer is not None else "",
                "duration": note.duration, "velocity": note.velocity / 127.0,
                "progress": min(1.0, elapsed / max(0.001, note.duration)),
                "phase": "tail" if elapsed >= note.duration else "held",
                "attack": attack,
                "elapsedSeconds": elapsed, "positioned": True,
                "source": "selection" if session is self._playback._player._temporary_session else "project",
            })
        rows.sort(key=lambda row: (row["startTime"], row["noteId"]))
        self._replace((rows + live_rows)[-24:])

    def _replace(self, rows: list[dict]) -> None:
        if rows != self._voices:
            self._voices = rows
            self.snapshotChanged.emit()
