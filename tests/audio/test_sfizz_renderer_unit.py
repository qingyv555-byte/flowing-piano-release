"""Native SFZ event boundaries and failure policy without optional binaries."""
import pytest

from ai_piano_studio.audio.sfizz_renderer import _events_for_score
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import ControlEvent, sustain_snapshot


def test_sfizz_events_keep_millisecond_timing_and_pedal():
    note = Note.create(pitch=60, start_time=0.125, duration=0.5, velocity=87)
    controls = [
        ControlEvent.create(time=0.2, value=127),
        ControlEvent.create(time=0.9, value=0),
    ]
    events, frames = _events_for_score((note,), controls=controls)
    assert [(e.frame, e.kind, e.data, e.value) for e in events] == [
        (5512, "on", 60, 87),
        (8820, "cc", 64, 127),
        (27562, "off", 60, 0),
        (39690, "cc", 64, 0),
    ]
    assert frames > 39690


def test_same_frame_off_before_on_and_cc_first():
    notes = (
        Note.create(pitch=69, start_time=0, duration=1),
        Note.create(pitch=69, start_time=1, duration=1),
    )
    events, _ = _events_for_score(notes, controls=(
        ControlEvent.create(time=1, value=0),))
    simultaneous = [e.kind for e in events if e.frame == 44100]
    assert simultaneous == ["cc", "off", "on"]


@pytest.mark.parametrize("seconds", [601, 999999])
def test_bounded_sfizz_render_rejects_huge_scores(seconds):
    with pytest.raises(ValueError, match="bound"):
        _events_for_score((Note.create(pitch=60, start_time=0, duration=seconds),))


def test_empty_score_is_not_silently_rendered():
    with pytest.raises(ValueError, match="empty"):
        _events_for_score(())


def test_bad_rate_is_rejected():
    with pytest.raises(ValueError, match="sample rate"):
        _events_for_score((Note.create(pitch=60, start_time=0, duration=1),),
                          sample_rate=0)


def test_resolved_per_layer_pedal_release_survives_sfizz_conversion():
    first = Note.create(pitch=62, start_time=0, duration=0.25)
    extended = sustain_snapshot(
        (first,), (
            ControlEvent.create(time=0.1, value=127),
            ControlEvent.create(time=0.75, value=0),
        ))
    assert extended[0].duration == 0.25
    assert extended[0].sounding_duration == 0.75
    events, _ = _events_for_score(extended)
    assert [(e.kind, e.frame) for e in events] == [
        ("on", 0), ("off", round(0.75 * 44100))]
