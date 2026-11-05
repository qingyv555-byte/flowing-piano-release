"""Deterministic real session/mixer/PCM evidence; no output device is opened."""
from array import array
from dataclasses import replace
from pathlib import Path
import wave

import pytest
from PySide6.QtCore import QObject, QUrl, QRect
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.visualizer import AudioVisualizerController, PcmSnapshotTap
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController

ROOT = Path(__file__).parents[2]


class OutputWithoutDevice(PianoVoiceMixer):
    """Only replace hardware IO; retain real mixing, release and PCM tap."""
    def __init__(self):
        super().__init__(sample_rate=48_000)
        self.consumed = 0.0
        self.latency = 0.0
        self._test_clock_origin = 0.0

    def start_audio(self, **_kwargs):
        self._sink_frame_origin = self.rendered_frames
        self._test_clock_origin = self.consumed
        self._audio_ready = True

    def pause_audio(self):
        self._audio_ready = False

    def resume_audio(self):
        self._audio_ready = True

    def processed_audio_seconds(self):
        return self.consumed - self._test_clock_origin

    def output_latency_seconds(self):
        return self.latency


class RecordedPianoSource:
    def __init__(self, mixer):
        self.mixer = mixer
        asset = ROOT / "resources/piano/banks/layers-08-13/Fs4-hard.wav"
        with wave.open(str(asset)) as recording:
            assert recording.getsampwidth() == 2
            self.rate = recording.getframerate()
            channels = recording.getnchannels()
            samples = array("h", recording.readframes(recording.getnframes()))
            self.samples = samples[::channels]

    def start_voice(self, voice_id, pitch, velocity):
        self.mixer.start_voice(voice_id, pitch, velocity, self.samples,
                              source_sample_rate=self.rate, owner_tag="test")

    def note_off(self, voice_id, mode, **kwargs):
        self.mixer.note_off(voice_id, mode, **kwargs)


def note(identifier, start=0.0, duration=1.0, pitch=66):
    return Note.create(id=identifier, pitch=pitch, start_time=start,
                       duration=duration, velocity=100)


@pytest.fixture
def rig(qapp):
    from ai_piano_studio.audio.studio_feedback import StudioFeedbackController

    mixer = OutputWithoutDevice()
    source = RecordedPianoSource(mixer)
    editor = EditorController(ProjectState(notes=(note("held"),)))
    player = AudioPlayer(piano_source=source, mixer=mixer)
    playback = PlaybackController(editor, player=player, piano_source=source)
    feedback = StudioFeedbackController(playback)
    feedback.setEnabled(True)
    yield mixer, player, playback, feedback
    feedback.setEnabled(False)
    player.stop_temporary_notes(ReleaseMode.STOP)
    player.stop(ReleaseMode.STOP)
    player.deleteLater()
    qapp.processEvents()


def advance(rig, seconds):
    mixer, player, _playback, feedback = rig
    mixer.consumed = seconds
    session = player._temporary_session or player._project_session
    session.process_due_events()
    # A consumed clock cannot outrun the PCM actually pulled. Include the
    # queued buffer in rendering, but not in the presentation clock.
    target = mixer._sink_frame_origin + round((mixer.processed_audio_seconds() + mixer.latency) * mixer.sample_rate)
    mixer._read_output_frames(max(1, target - mixer.rendered_frames))
    feedback.sample()
    return feedback.voices


def test_distinct_same_pitch_voices_attack_and_then_become_actual_tails(rig):
    _mixer, player, _playback, feedback = rig
    player.play_notes(tuple(note(str(i), i * 0.1, 0.06) for i in range(5)))
    for index in range(5):
        voices = advance(rig, index * 0.1 + 0.01)
        attack = next(row for row in voices if row["noteId"] == str(index))
        assert attack["attack"] > 0.8
        assert attack["phase"] == "held"
        assert attack["voiceId"].endswith(":" + str(index))
    voices = advance(rig, 0.5)
    assert any(row["phase"] == "tail" for row in voices)
    assert all(row["progress"] == 1.0 for row in voices)


def test_feedback_uses_audio_latency_and_never_replays_old_attacks_on_seek(rig):
    mixer, player, playback, feedback = rig
    mixer.latency = 0.08
    player.play_notes((note("held", duration=3), note("future", start=0.2)))
    rows = advance(rig, 0.24)
    assert {row["noteId"] for row in rows} == {"held", "future"}
    assert rows[0]["progress"] == pytest.approx(0.24 / 3)
    player.seek(0.9)
    rows = advance(rig, 0.3)
    assert rows and all(row["attack"] == 0 for row in rows)
    playback.pause()
    feedback.sample()
    assert feedback.voices == []
    feedback.setEnabled(False)
    assert feedback.voices == []


def test_selection_feedback_retains_original_timeline_coordinates(rig):
    _mixer, player, playback, feedback = rig
    playback.selectionAudition._position_origin_seconds = 5.0
    player.play_temporary_notes((note("selected", duration=2),))
    rows = advance(rig, 0.2)
    assert [row["noteId"] for row in rows] == ["selected"]
    assert rows[0]["startTime"] == 5.0
    assert rows[0]["progress"] == pytest.approx(0.1)


def test_hiding_mid_note_and_resuming_does_not_create_an_attack(rig):
    _mixer, player, _playback, feedback = rig
    player.play_notes((note("held", duration=3),))
    assert advance(rig, 0.01)[0]["attack"] > 0
    feedback.setEnabled(False)
    feedback.setEnabled(True)
    assert advance(rig, 0.02)[0]["attack"] == 0


def load_feedback(qapp, playback, visualizer, feedback):
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("testPlayback", playback)
    context.setContextProperty("testVisualizer", visualizer)
    context.setContextProperty("testFeedback", feedback)
    component = QQmlComponent(engine)
    component.setData(b'''
        import QtQuick
        Item {
            width: 900; height: 480
            property alias feedback: effect
            QtObject { id: config; objectName: "feedbackSettings"; property var values: ({feedbackLevel: "rich",
                feedbackStyle: "weave", glassWidth: 1, glassPositionX: 0.5,
                noteFeedback: true, keyFeedback: true, spectrum: true,
                atmosphere: true, reducedMotion: false, motionFps: 30}) }
            QtObject { id: roll; objectName: "feedbackTimeline"; property real keyWidth: 72
                property int highestPitch: 84; property real rowHeight: 18
                property real pixelsPerSecond: 80; property real viewStartSeconds: 0
                property bool focusMode: false }
            StudioFeedback { id: effect; anchors.fill: parent
                theme: null; settings: config; timeline: roll
                playbackController: testPlayback; visualizer: testVisualizer
                feedbackController: testFeedback }
        }
    ''', QUrl.fromLocalFile(str(ROOT / "src/ai_piano_studio/qml/FeedbackHarness.qml")))
    assert not component.isError(), [e.toString() for e in component.errors()]
    root = component.create()
    assert root is not None, [e.toString() for e in component.errors()]
    engine._component = component
    engine._root = root
    qapp.processEvents()
    return engine, root, root.findChild(QObject, "studioFeedback")


def test_recorded_pcm_reaches_qml_and_zero_volume_and_lifecycle_clear_it(qapp, rig):
    mixer, player, playback, feedback = rig
    tap = PcmSnapshotTap()
    mixer.set_visualizer_tap(tap)
    visualizer = AudioVisualizerController(tap, auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    try:
        player.play_notes((note("pcm", duration=3),))
        advance(rig, 0.02)
        visualizer.process_once()
        qapp.processEvents()
        assert visualizer.rms > 0
        assert effect.property("displayRms") > 0
        assert effect.property("bandCount") <= 32
        assert effect.property("analysisRequested") is True
        assert feedback.voices[0]["attack"] > 0.8
        playback.setVolume(0)
        qapp.processEvents()
        assert effect.property("displayRms") == 0
        assert effect.property("analysisRequested") is False
        playback.setVolume(0.65)
        effect.setProperty("active", False)
        qapp.processEvents()
        assert effect.property("analysisRequested") is False
        assert effect.property("animationRunning") is False
        assert feedback.enabled is False
        assert feedback.voices == []
    finally:
        visualizer.shutdown()
        root.deleteLater()
        engine.deleteLater()
        qapp.processEvents()


def test_feedback_follows_the_actual_mute_and_solo_snapshot(rig):
    _mixer, _player, playback, _feedback = rig
    layers = (MusicLayer.default(), MusicLayer.create(id="muted", name="Muted", muted=True, order=1),
              MusicLayer.create(id="solo", name="Solo", solo=True, order=2))
    notes = tuple(replace(note(layer.id), layer_id=layer.id) for layer in layers)
    # Construct the project before play; no selection gesture or audition occurs.
    playback._editor._history._state = ProjectState(notes=notes, layers=layers)
    playback.play()
    rows = advance(rig, 0.02)
    assert {row["noteId"] for row in rows} == {"solo"}


def test_pitch_row_tracks_real_audio_with_one_wash_per_pitch_and_clears_on_pause(qapp, rig):
    from tests.qt.test_studio_workflows import qml

    _mixer, player, playback, feedback = rig
    visualizer = AudioVisualizerController(PcmSnapshotTap(), auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    try:
        player.play_notes((note("one", duration=3),))
        advance(rig, .02)
        def find_row(pitch):
            pending = [effect]
            while pending:
                item = pending.pop()
                if item.objectName() == "studioPitchRow-" + str(pitch):
                    return item
                pending.extend(item.childItems())
            raise AssertionError("Missing pitch row")
        row = find_row(66)
        assert row is not None
        assert row.x() == 72 and row.width() == 828
        assert row.y() == (84 - 66) * 18 and row.height() == 18
        level = row.property("targetOpacity")
        assert 0 < level <= .135
        assert find_row(65).property("targetOpacity") == 0
        # Two voices of the same pitch do not stack translucent rectangles.
        player.stop(ReleaseMode.STOP)
        player.play_notes((note("one", duration=3), note("two", duration=3)))
        advance(rig, .04)
        assert row.property("targetOpacity") == pytest.approx(level)
        playback.setVolume(0)
        assert row.property("targetOpacity") == 0
        playback.setVolume(.65)
        assert row.property("targetOpacity") > 0
        config = root.findChild(QObject, "feedbackSettings")
        qml(config, 'values = Object.assign({}, values, {noteFeedback: false}); true')
        assert not row.isVisible()
        qml(config, 'values = Object.assign({}, values, {noteFeedback: true, reducedMotion: true}); true')
        assert row.property("opacity") == pytest.approx(row.property("targetOpacity"))
        playback.pause()
        assert row.property("opacity") == 0
    finally:
        visualizer.shutdown()
        root.deleteLater()
        engine.deleteLater()
        qapp.processEvents()


def test_feedback_layer_visibility_and_color_update_without_rebuilding_note_index(rig):
    _mixer, _player, playback, feedback = rig
    layer = MusicLayer.create(id="hidden", name="Hidden but sounding", role="melody",
                              custom_color="#E4A25C", visible=False, locked=True)
    state = ProjectState(notes=(replace(note("hidden-note", duration=3), layer_id=layer.id),),
                         layers=(layer,))
    playback._editor._history._state = state
    playback.play()
    row = advance(rig, 0.02)[0]
    assert (row["layerVisible"], row["layerRole"], row["layerCustomColor"]) == (
        False, "melody", "#E4A25C")
    note_index = feedback._notes
    playback._editor._history._state = replace(
        state, layers=(replace(layer, visible=True, locked=False, color_role="bass",
                               custom_color=None),))
    row = advance(rig, 0.04)[0]
    assert (row["layerVisible"], row["layerRole"], row["layerCustomColor"]) == (
        True, "bass", "")
    assert feedback._notes is note_index


def test_pause_resume_speed_and_new_session_keep_attack_epochs_separate(rig):
    _mixer, player, playback, feedback = rig
    notes = (note("held", duration=3), note("later", start=0.5))
    player.play_notes(notes)
    first_id = advance(rig, 0.02)[0]["voiceId"]
    playback.pause()
    assert feedback.voices == []
    player.play_notes(notes)
    assert advance(rig, 0.04)[0]["attack"] == 0
    playback.setPlaybackRate(2)
    rows = advance(rig, 0.29)
    later = next(row for row in rows if row["noteId"] == "later")
    assert later["attack"] > 0
    # Rate changes cancel the previous PCM generation before rebuilding.
    assert next(row for row in rows if row["noteId"] == "held")["voiceId"] != first_id
    assert next(row for row in rows if row["noteId"] == "held")["attack"] == 0
    player.stop(ReleaseMode.STOP)
    feedback.sample()
    assert feedback.voices == []
    player.play_notes((note("new", duration=2),))
    rows = advance(rig, 0.3)
    assert rows[0]["noteId"] == "new" and rows[0]["attack"] > 0
    assert rows[0]["voiceId"] != first_id


def test_composite_children_keep_individual_real_triggers_and_score_unchanged(rig):
    from ai_piano_studio.editor.composite import CompositeNoteGrouper

    _mixer, player, playback, _feedback = rig
    notes = tuple(note(f"child:{i}", start=i * 0.12, duration=0.09) for i in range(5))
    group = CompositeNoteGrouper().group(notes)[0]
    assert group.is_composite and group.child_note_ids == tuple(n.id for n in notes)
    original_state = playback._editor.state
    player.play_notes(notes)
    seen = []
    for i in range(5):
        rows = advance(rig, i * 0.12 + 0.01)
        triggered = next(row for row in rows if row["noteId"] == notes[i].id)
        assert triggered["attack"] > 0.8
        seen.append(triggered["voiceId"])
    assert len(set(seen)) == 5
    assert playback._editor.state is original_state


def test_backward_seek_starts_a_fresh_epoch_without_old_attack_suppression(rig):
    _mixer, player, _playback, _feedback = rig
    player.play_notes((note("start", duration=2),))
    previous_id = advance(rig, 0.6)[0]["voiceId"]
    player.seek(0)
    row = advance(rig, 0.61)[0]
    assert row["voiceId"] != previous_id
    assert row["attack"] > 0.8


def test_dense_chord_snapshot_respects_real_mixer_polyphony(rig):
    mixer, player, _playback, _feedback = rig
    player.play_notes(tuple(note(f"chord-{i}", pitch=40 + i) for i in range(40)))
    rows = advance(rig, 0.02)
    assert 0 < len(rows) <= 24
    assert {row["voiceId"] for row in rows} <= set(mixer.active_voice_ids)


@pytest.mark.parametrize("target, prop, value", [
    ("effect", "active", False), ("effect", "visible", False),
    ("effect", "windowActive", False), ("effect", "powerSave", True),
    ("timeline", "focusMode", True),
])
def test_qml_demand_stops_for_inactive_or_restricted_views(qapp, rig, target, prop, value):
    _mixer, player, playback, feedback = rig
    visualizer = AudioVisualizerController(PcmSnapshotTap(), auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    try:
        player.play_notes((note("held", duration=3),))
        advance(rig, 0.02)
        obj = effect if target == "effect" else root.findChild(QObject, "feedbackTimeline")
        obj.setProperty(prop, value)
        qapp.processEvents()
        assert effect.property("analysisRequested") is False
        if prop in {"active", "visible", "windowActive"}:
            assert effect.property("animationRunning") is False
            assert feedback.voices == []
        else:
            assert effect.property("eventsRequested") is True
    finally:
        visualizer.shutdown()
        root.deleteLater()
        engine.deleteLater()
        qapp.processEvents()


def test_reduced_motion_preserves_hold_but_disables_atmosphere(qapp, rig):
    _mixer, player, playback, feedback = rig
    visualizer = AudioVisualizerController(PcmSnapshotTap(), auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    try:
        player.play_notes((note("held", duration=3),))
        advance(rig, 0.02)
        config = root.findChild(QObject, "feedbackSettings")
        config.setProperty("values", {"feedbackLevel": "rich", "reducedMotion": True,
                                      "spectrum": False})
        qapp.processEvents()
        assert effect.property("atmosphereEnabled") is False
        assert effect.property("analysisRequested") is False
        assert effect.property("eventsRequested") is True
        assert feedback.voices[0]["phase"] == "held"
        assert feedback.voices[0]["progress"] > 0
    finally:
        visualizer.shutdown()
        root.deleteLater()
        engine.deleteLater()
        qapp.processEvents()


def test_offscreen_spectrum_pixels_change_with_recorded_pcm_and_master_mute(qapp, rig):
    mixer, player, playback, feedback = rig
    tap = PcmSnapshotTap()
    mixer.set_visualizer_tap(tap)
    visualizer = AudioVisualizerController(tap, auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    window = QQuickWindow()
    window.resize(900, 480)
    root.setParentItem(window.contentItem())
    try:
        window.show()  # Tests force QT_QPA_PLATFORM=offscreen in conftest.py.
        player.play_notes((note("recorded", duration=3),))
        advance(rig, 0.02)
        visualizer.process_once()
        QTest.qWait(75)
        sounding = window.grabWindow().copy(QRect(80, 370, 790, 105))
        assert not sounding.isNull()
        playback.setVolume(0)
        QTest.qWait(75)
        muted = window.grabWindow().copy(QRect(80, 370, 790, 105))
        assert not muted.isNull()
        assert sounding != muted
        assert effect.property("displayRms") == 0
    finally:
        visualizer.shutdown()
        window.close()
        root.setParentItem(None)
        root.deleteLater()
        window.deleteLater()
        engine.deleteLater()
        qapp.processEvents()
