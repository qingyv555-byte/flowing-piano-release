from __future__ import annotations

from dataclasses import dataclass

from ai_piano_studio.audio.importer import apply_extraction_controls


@dataclass(frozen=True)
class ReferenceCase:
    name: str
    candidates: tuple[dict[str, object], ...]


REFERENCE_CASES = (
    ReferenceCase(
        "monophonic melody",
        tuple(
            {"pitch": pitch, "startTime": index * 0.5, "duration": 0.45, "confidence": 0.9}
            for index, pitch in enumerate((60, 62, 64, 65, 67))
        ),
    ),
    ReferenceCase(
        "melody plus accompaniment",
        (
            {"pitch": 60, "startTime": 0.0, "duration": 0.5, "confidence": 0.95},
            {"pitch": 48, "startTime": 0.0, "duration": 0.5, "confidence": 0.8},
            {"pitch": 64, "startTime": 0.5, "duration": 0.5, "confidence": 0.9},
            {"pitch": 52, "startTime": 0.5, "duration": 0.5, "confidence": 0.8},
        ),
    ),
    ReferenceCase(
        "bass heavy",
        (
            {"pitch": 36, "startTime": 0.0, "duration": 0.8, "confidence": 0.9},
            {"pitch": 43, "startTime": 0.8, "duration": 0.8, "confidence": 0.85},
            {"pitch": 67, "startTime": 0.0, "duration": 0.4, "confidence": 0.7},
        ),
    ),
    ReferenceCase(
        "dense polyphonic passage",
        tuple(
            {
                "pitch": pitch,
                "startTime": index * 0.25,
                "duration": 0.22,
                "confidence": 0.75 + (pitch % 3) * 0.05,
            }
            for index in range(8)
            for pitch in (48 + index % 5, 60 + index % 7, 72 + index % 4)
        ),
    ),
    ReferenceCase("silence/noise", ()),
)


def test_reference_cases_have_valid_timing_and_mode_specific_reduction() -> None:
    for case in REFERENCE_CASES:
        automatic = apply_extraction_controls(
            case.candidates, mode="auto", min_note_duration=0.08, quantize_strength=0.0
        )
        melody = apply_extraction_controls(
            case.candidates, mode="melody", min_note_duration=0.08, quantize_strength=0.0
        )
        simplified = apply_extraction_controls(
            case.candidates,
            mode="simplified",
            min_note_duration=0.08,
            simplify_density=0.0,
            quantize_strength=0.0,
        )
        for result in (automatic, melody, simplified):
            assert all(
                note["startTime"] >= 0 and note["duration"] > 0
                for note in result
            ), case.name
        assert len(melody) <= len(automatic), case.name
        assert len(simplified) <= len(automatic), case.name


def test_reference_modes_preserve_musical_intent_without_claiming_transcription_accuracy() -> None:
    melody_case = REFERENCE_CASES[0]
    melody = apply_extraction_controls(
        melody_case.candidates, mode="melody", min_note_duration=0.08, quantize_strength=0.0
    )
    bass_case = REFERENCE_CASES[2]
    bass = apply_extraction_controls(
        bass_case.candidates, mode="bass", min_note_duration=0.08, quantize_strength=0.0
    )

    assert [note["pitch"] for note in melody] == [60, 62, 64, 65, 67]
    assert [note["pitch"] for note in bass] == [36, 43]
    assert apply_extraction_controls(REFERENCE_CASES[-1].candidates) == []
