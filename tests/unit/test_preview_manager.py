from ai_piano_studio.audio.preview import AudioPreviewManager


def test_single_preview_stops_previous_voice_before_new_pitch() -> None:
    events: list[tuple[str, int, str | None]] = []
    voice_ids = iter((101, 102))
    manager = AudioPreviewManager(
        play=lambda pitch, velocity: (
            events.append(("play", pitch, None)), next(voice_ids)
        )[1],
        stop=lambda voice_id, mode: events.append(("stop", voice_id, mode)),
    )

    manager.preview(60, 80)
    manager.preview(62, 80)

    assert events == [
        ("play", 60, None),
        ("stop", 101, "safe"),
        ("play", 62, None),
    ]


def test_off_mode_never_starts_preview_audio() -> None:
    played: list[int] = []
    manager = AudioPreviewManager(play=lambda pitch, velocity: played.append(pitch))

    manager.set_mode("off")
    manager.preview(60, 80)

    assert played == []


def test_continuous_preview_keeps_only_the_configured_number_of_voices() -> None:
    events: list[tuple[str, int, str | None]] = []
    voice_ids = iter((101, 102, 103))
    manager = AudioPreviewManager(
        play=lambda pitch, velocity: (
            events.append(("play", pitch, None)), next(voice_ids)
        )[1],
        stop=lambda voice_id, mode: events.append(("stop", voice_id, mode)),
        max_polyphony=2,
    )

    manager.set_mode("continuous")
    manager.preview(60, 80)
    manager.preview(62, 80)
    manager.preview(64, 80)

    assert events == [
        ("play", 60, None),
        ("play", 62, None),
        ("stop", 101, "safe"),
        ("play", 64, None),
    ]
