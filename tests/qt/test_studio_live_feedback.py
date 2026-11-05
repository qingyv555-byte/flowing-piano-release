"""Real audition voices, with memory PCM and no global keyboard/device use."""
import pytest

from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.visualizer import AudioVisualizerController, PcmSnapshotTap
from ai_piano_studio.keyboard.controller import KeyboardController
from test_studio_feedback import load_feedback, rig


def sample_live(rig, qapp, seconds, frames=1_024):
    mixer, _player, _playback, feedback = rig
    mixer.consumed = seconds
    mixer._read_output_frames(frames)
    qapp.processEvents()
    feedback.sample()
    return feedback.voices


def finish_live(rig, qapp):
    mixer, _player, playback, _feedback = rig
    playback.stopAudition(ReleaseMode.STOP.value)
    mixer._read_output_frames(1_024)
    qapp.processEvents()


def test_single_audition_wakes_from_real_start_and_has_no_score_coordinates(rig, qapp):
    _mixer, _player, playback, feedback = rig
    original = playback._editor.state
    assert not feedback.liveActive and not playback.playing
    voice_id = playback.startAudition(66, 100)
    assert voice_id > 0 and feedback.liveActive
    feedback.sample()
    assert feedback.voices == []  # Accepted command has not yet emitted PCM.
    row = sample_live(rig, qapp, 0.02)[0]
    assert row["voiceId"] == f"audition:{voice_id}"
    assert row["source"] == "audition" and row["positioned"] is False
    assert row["noteId"] == row["layerId"] == ""
    assert row["phase"] == "held" and row["attack"] > 0.8
    assert row["elapsedSeconds"] == pytest.approx(0.02)
    assert row["duration"] == row["progress"] == 0
    assert playback._editor.state is original
    finish_live(rig, qapp)
    assert not feedback.liveActive and feedback.voices == []


def test_repeated_live_pitch_release_keeps_new_instances_and_actual_phase(rig, qapp):
    _mixer, _player, playback, feedback = rig
    identifiers = []
    for index in range(5):
        identifiers.append(playback.startAudition(66, 100))
        rows = sample_live(rig, qapp, 0.01 * (index + 1))
        assert next(row for row in rows if row["voiceId"] == f"audition:{identifiers[-1]}")["attack"] > 0.8
    assert len({row["voiceId"] for row in rows}) == 5
    playback.releaseAudition(identifiers[0], ReleaseMode.NATURAL.value)
    rows = sample_live(rig, qapp, 0.06)
    assert next(row for row in rows if row["voiceId"] == f"audition:{identifiers[0]}")["phase"] == "tail"
    assert next(row for row in rows if row["voiceId"] == f"audition:{identifiers[-1]}")["phase"] == "held"
    playback.releaseAudition(identifiers[0], ReleaseMode.STOP.value)
    rows = sample_live(rig, qapp, 0.08)
    assert f"audition:{identifiers[0]}" not in {row["voiceId"] for row in rows}
    assert f"audition:{identifiers[-1]}" in {row["voiceId"] for row in rows}
    assert feedback.liveActive
    finish_live(rig, qapp)


def test_disabled_feedback_still_retires_live_source_without_a_display_poller(rig, qapp):
    _mixer, _player, playback, feedback = rig
    feedback.setEnabled(False)
    playback.startAudition(66)
    assert feedback.liveActive
    sample_live(rig, qapp, 0.01)
    assert feedback.voices == []
    feedback.setEnabled(True)
    assert sample_live(rig, qapp, 0.02)[0]["attack"] == 0
    feedback.setEnabled(False)
    finish_live(rig, qapp)
    assert not feedback.liveActive and not feedback._live


def test_live_latency_and_device_clock_reset_do_not_replay_an_old_attack(rig, qapp):
    mixer, _player, playback, feedback = rig
    mixer.consumed = 2.0
    mixer.latency = 0.08
    playback.startAudition(66)
    assert sample_live(rig, qapp, 2.04) == []
    row = sample_live(rig, qapp, 2.1)[0]
    assert row["elapsedSeconds"] == pytest.approx(0.02)
    assert row["attack"] > 0.8
    assert sample_live(rig, qapp, 0.01) == []
    row = sample_live(rig, qapp, 0.12)[0]
    assert row["attack"] == 0
    finish_live(rig, qapp)


def test_failed_audition_never_activates_feedback(rig, monkeypatch):
    _mixer, player, playback, feedback = rig
    def fail(*_args):
        raise RuntimeError("controlled missing source")
    monkeypatch.setattr(player.piano_source, "start_voice", fail)
    assert playback.startAudition(66) == -1
    assert not feedback.liveActive and feedback.voices == []


def test_live_metadata_is_bounded_by_existing_polyphony(rig, qapp):
    _mixer, _player, playback, feedback = rig
    for pitch in range(40, 80):
        playback.startAudition(pitch)
    assert len(feedback._live) == 24
    rows = sample_live(rig, qapp, 0.02)
    assert len(rows) == 24
    assert len({row["voiceId"] for row in rows}) == 24
    finish_live(rig, qapp)


def test_real_keyboard_route_keeps_score_unchanged_and_releases_its_voice(rig, qapp):
    _mixer, _player, playback, _feedback = rig
    original = playback._editor.state
    keyboard = KeyboardController(playback._editor, playback)
    keyboard.pressPitch(60)
    keyboard.pressPitch(64)
    rows = sample_live(rig, qapp, 0.02)
    assert {row["pitch"] for row in rows} == {60, 64}
    assert all(row["positioned"] is False for row in rows)
    keyboard.releasePitch(60)
    rows = sample_live(rig, qapp, 0.04)
    assert next(row for row in rows if row["pitch"] == 60)["phase"] == "tail"
    assert next(row for row in rows if row["pitch"] == 64)["phase"] == "held"
    assert playback._editor.state is original
    keyboard.releasePitch(64)
    finish_live(rig, qapp)


def test_live_qml_demand_and_master_mute_use_existing_pcm_subscription(rig, qapp):
    mixer, _player, playback, feedback = rig
    tap = PcmSnapshotTap()
    mixer.set_visualizer_tap(tap)
    visualizer = AudioVisualizerController(tap, auto_start=False)
    engine, root, effect = load_feedback(qapp, playback, visualizer, feedback)
    try:
        assert not effect.property("playing") and not effect.property("analysisRequested")
        playback.startAudition(66)
        assert effect.property("playing") and effect.property("analysisRequested")
        sample_live(rig, qapp, 0.02)
        visualizer.process_once()
        qapp.processEvents()
        assert effect.property("displayRms") > 0
        assert feedback.voices[0]["attack"] > 0.8
        playback.setVolume(0)
        assert effect.property("displayRms") == 0
        assert not effect.property("analysisRequested")
        effect.setProperty("active", False)
        assert not effect.property("animationRunning")
        finish_live(rig, qapp)
        assert not feedback.liveActive and not effect.property("playing")
    finally:
        visualizer.shutdown()
        root.deleteLater()
        engine.deleteLater()
        qapp.processEvents()


def test_suspended_audio_retires_live_demand_and_resume_does_not_retrigger(rig, qapp):
    mixer, _player, playback, feedback = rig
    playback.startAudition(66)
    assert sample_live(rig, qapp, 0.02)[0]["attack"] > 0.8
    mixer._set_audio_ready_locked(False)
    assert not feedback.liveActive and feedback.voices == []
    mixer._set_audio_ready_locked(True)
    assert feedback.liveActive
    assert sample_live(rig, qapp, 0.04)[0]["attack"] == 0
    finish_live(rig, qapp)


def test_single_selected_note_audition_reports_only_its_real_live_voice(rig, qapp):
    _mixer, _player, playback, feedback = rig
    playback._editor.selectNote("held")
    assert feedback.liveActive and not playback.playing
    rows = sample_live(rig, qapp, 0.02)
    assert len(rows) == 1 and rows[0]["pitch"] == 66
    assert rows[0]["source"] == "audition" and rows[0]["positioned"] is False
    finish_live(rig, qapp)


def test_no_device_clock_uses_real_rendered_pcm_age(rig, qapp, monkeypatch):
    mixer, _player, playback, _feedback = rig
    monkeypatch.setattr(mixer, "processed_audio_seconds", lambda: None)
    playback.startAudition(66)
    row = sample_live(rig, qapp, 99, frames=960)[0]
    assert row["elapsedSeconds"] == pytest.approx(0.02)
    finish_live(rig, qapp)


@pytest.mark.parametrize("enabled", [True, False])
def test_overflow_follows_actual_releasing_victim_and_keeps_oldest_live(rig, qapp, enabled):
    mixer, _player, playback, feedback = rig
    feedback.setEnabled(enabled)
    identifiers = [playback.startAudition(66) for _ in range(24)]
    sample_live(rig, qapp, 0.02)
    oldest = f"audition:{identifiers[0]}"
    releasing = f"audition:{identifiers[-1]}"
    playback.releaseAudition(identifiers[-1], ReleaseMode.NATURAL.value)
    sample_live(rig, qapp, 0.04)
    newest = playback.startAudition(66)
    rows = sample_live(rig, qapp, 0.06)
    assert oldest in mixer.active_voice_ids and releasing not in mixer.active_voice_ids
    assert len(feedback._live) <= 24
    assert set(feedback._live) == set(mixer.active_voice_ids)
    if enabled:
        assert {row["voiceId"] for row in rows} == set(mixer.active_voice_ids)
    for identifier in identifiers[1:] + [newest]:
        playback.releaseAudition(identifier, ReleaseMode.STOP.value)
    sample_live(rig, qapp, 0.08)
    assert mixer.active_voice_ids == (oldest,)
    assert feedback.liveActive and set(feedback._live) == {oldest}
    feedback.setEnabled(True)
    feedback.sample()
    assert [row["voiceId"] for row in feedback.voices] == [oldest]
    finish_live(rig, qapp)
