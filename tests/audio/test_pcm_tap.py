from __future__ import annotations

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.visualizer import PcmSnapshotTap


def test_pcm_snapshot_tap_keeps_only_a_bounded_latest_payload() -> None:
    tap = PcmSnapshotTap(enabled=True, max_bytes=8)

    tap.publish(b"0123456789")

    assert tap.read_latest() == b"23456789"
    assert tap.publish_count == 1
    tap.clear()
    assert tap.read_latest() is None


def test_disabled_pcm_snapshot_tap_does_no_work() -> None:
    tap = PcmSnapshotTap(enabled=False, max_bytes=8)

    tap.publish(b"01234567")

    assert tap.read_latest() is None
    assert tap.publish_count == 0


def test_enabling_tap_starts_a_fresh_payload_epoch() -> None:
    tap = PcmSnapshotTap()
    tap.publish(b"old-payload")

    tap.set_enabled(True)

    assert tap.read_latest() is None
    tap.publish(b"new-payload")
    assert tap.read_latest() == b"new-payload"


def test_mixer_tap_observes_final_output_without_changing_audio_bytes() -> None:
    source = (12_000, -12_000) * 2_000
    tapped = PianoVoiceMixer(sample_rate=8_000)
    reference = PianoVoiceMixer(sample_rate=8_000)
    tap = PcmSnapshotTap()
    tapped.set_visualizer_tap(tap)
    for mixer in (tapped, reference):
        mixer.start_voice(
            "voice",
            60,
            100,
            source,
            source_sample_rate=8_000,
            owner_tag="test",
        )

    tapped_payload = tapped._read_output_frames(128)
    reference_payload = reference._read_output_frames(128)

    assert tapped_payload == reference_payload
    assert tap.read_latest() == tapped_payload


def test_disabling_mixer_tap_stops_publication() -> None:
    mixer = PianoVoiceMixer(sample_rate=8_000)
    tap = PcmSnapshotTap()
    mixer.set_visualizer_tap(tap)
    mixer.start_voice(
        "voice",
        60,
        100,
        (10_000,) * 2_000,
        source_sample_rate=8_000,
        owner_tag="test",
    )
    mixer._read_output_frames(64)
    assert tap.read_latest() is not None

    tap.clear()
    tap.set_enabled(False)
    mixer._read_output_frames(64)

    assert tap.read_latest() is None
