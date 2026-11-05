from __future__ import annotations

import io
import wave
from array import array
from math import cos, exp, isfinite, pi, sin, sqrt
from pathlib import Path
from typing import Callable, Iterable

from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, LEGACY_PIANO, PianoVoiceProfile, ReleaseMode
from ai_piano_studio.domain.control_event import sounding_duration
from ai_piano_studio.domain.note import Note
from ai_piano_studio.persistence.atomic import atomic_write_bytes


SAMPLE_RATE = 44100
ATTACK_SECONDS = 0.005
RELEASE_SECONDS = 0.08
MAX_AMPLITUDE = 0.95

# Additive harmonics: (frequency multiplier, relative amplitude).
_HARMONICS = ((1.0, 1.0), (2.0, 0.45), (3.0, 0.22), (4.0, 0.1))
_HARMONIC_TOTAL = sum(gain for _, gain in _HARMONICS)
_CANCELLATION_POLL_FRAMES = 1024


def midi_to_frequency(pitch: int) -> float:
    if not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127:
        raise ValueError("pitch must be an integer from 0 to 127")
    return 440.0 * 2.0 ** ((pitch - 69) / 12)


def render_note(
    pitch: int,
    duration: float,
    velocity: int,
    sample_rate: int = SAMPLE_RATE,
    *,
    profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
    should_cancel: Callable[[], bool] | None = None,
    release_seconds: float | None = None,
) -> array:
    """Render a single note as 16-bit mono PCM samples."""
    frequency = midi_to_frequency(pitch)
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not isfinite(float(duration))
        or duration <= 0
    ):
        raise ValueError("duration must be a number greater than 0")
    if not isinstance(velocity, int) or isinstance(velocity, bool) or not 0 <= velocity <= 127:
        raise ValueError("velocity must be an integer from 0 to 127")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(profile, PianoVoiceProfile):
        raise ValueError("profile must be a PianoVoiceProfile")

    if profile is LEGACY_PIANO:
        return _render_legacy_note(
            pitch, duration, velocity, sample_rate, should_cancel=should_cancel,
            release_seconds=release_seconds,
        )

    release = release_seconds if release_seconds is not None else profile.release_seconds(pitch, velocity, 0.8, ReleaseMode.NATURAL)
    total = int((duration + release) * sample_rate)
    note_off = int(duration * sample_rate)
    amplitude = 0.82 * (velocity / 127)
    attack = profile.attack_seconds(velocity)
    gains = profile.partial_gains(pitch, velocity)
    nyquist = sample_rate / 2.0
    register = min(1.0, max(0.0, (pitch - 36) / 48))
    inharmonicity = 0.00003 + (1.0 - register) * 0.00012
    decay_seconds = profile.decay_seconds(pitch)
    partial_parameters: list[float] = []
    for partial_index, gain in enumerate(gains, start=1):
        partial_frequency = frequency * partial_index * sqrt(
            1.0 + inharmonicity * partial_index * partial_index
        )
        if partial_frequency >= nyquist:
            partial_parameters.extend((0.0, 1.0, 0.0, 1.0))
            continue
        angular_step = 2.0 * pi * partial_frequency / sample_rate
        decay = decay_seconds / (1.0 + 0.22 * (partial_index - 1))
        partial_parameters.extend(
            (
                sin(angular_step),
                cos(angular_step),
                gain,
                exp(-1.0 / (sample_rate * decay)),
            )
        )
    (
        sine_step_1, cosine_step_1, gain_1, decay_step_1,
        sine_step_2, cosine_step_2, gain_2, decay_step_2,
        sine_step_3, cosine_step_3, gain_3, decay_step_3,
        sine_step_4, cosine_step_4, gain_4, decay_step_4,
        sine_step_5, cosine_step_5, gain_5, decay_step_5,
        sine_step_6, cosine_step_6, gain_6, decay_step_6,
    ) = partial_parameters
    phase_step_1 = complex(cosine_step_1, sine_step_1)
    phase_step_2 = complex(cosine_step_2, sine_step_2)
    phase_step_3 = complex(cosine_step_3, sine_step_3)
    phase_step_4 = complex(cosine_step_4, sine_step_4)
    phase_step_5 = complex(cosine_step_5, sine_step_5)
    phase_step_6 = complex(cosine_step_6, sine_step_6)
    phase_1 = phase_2 = phase_3 = phase_4 = phase_5 = phase_6 = 1.0 + 0.0j
    decay_1 = decay_2 = decay_3 = decay_4 = decay_5 = decay_6 = 1.0

    hammer_frequency = frequency * 7.3
    hammer_gain = profile.hammer_gain(velocity) if hammer_frequency < nyquist else 0.0
    hammer_step = 2.0 * pi * hammer_frequency / sample_rate
    hammer_sine_step = sin(hammer_step)
    hammer_cosine_step = cos(hammer_step)
    hammer_phase_step = complex(hammer_cosine_step, hammer_sine_step)
    hammer_phase = 1.0 + 0.0j
    hammer_decay_step = exp(-1.0 / (sample_rate * 0.018))
    attack_frames = attack * sample_rate
    release_frames = release * sample_rate
    gain_total = sum(gains)
    samples = array("h", bytes(2 * total))

    if should_cancel is None:
        for index in range(total):
            envelope = 1.0 if index >= attack_frames else index / attack_frames
            if index >= note_off:
                release_progress = (index - duration * sample_rate) / release_frames
                if release_progress > 1.0:
                    release_progress = 1.0
                envelope *= (1.0 - release_progress) ** profile.release_curve

            value = (
                gain_1 * decay_1 * phase_1.imag
                + gain_2 * decay_2 * phase_2.imag
                + gain_3 * decay_3 * phase_3.imag
                + gain_4 * decay_4 * phase_4.imag
                + gain_5 * decay_5 * phase_5.imag
                + gain_6 * decay_6 * phase_6.imag
            )
            hammer = hammer_gain * hammer_phase.imag
            phase_1 *= phase_step_1
            phase_2 *= phase_step_2
            phase_3 *= phase_step_3
            phase_4 *= phase_step_4
            phase_5 *= phase_step_5
            phase_6 *= phase_step_6
            hammer_phase *= hammer_phase_step
            decay_1 *= decay_step_1
            decay_2 *= decay_step_2
            decay_3 *= decay_step_3
            decay_4 *= decay_step_4
            decay_5 *= decay_step_5
            decay_6 *= decay_step_6
            hammer_gain *= hammer_decay_step
            pcm = int(32767 * amplitude * envelope * (value / gain_total + hammer))
            if pcm < -32768:
                samples[index] = -32768
            elif pcm > 32767:
                samples[index] = 32767
            else:
                samples[index] = pcm
        return samples

    for index in range(total):
        if _cancelled_at(should_cancel, index):
            return array("h")
        envelope = 1.0 if index >= attack_frames else index / attack_frames
        if index >= note_off:
            release_progress = (index - duration * sample_rate) / release_frames
            if release_progress > 1.0:
                release_progress = 1.0
            envelope *= (1.0 - release_progress) ** profile.release_curve

        value = (
            gain_1 * decay_1 * phase_1.imag
            + gain_2 * decay_2 * phase_2.imag
            + gain_3 * decay_3 * phase_3.imag
            + gain_4 * decay_4 * phase_4.imag
            + gain_5 * decay_5 * phase_5.imag
            + gain_6 * decay_6 * phase_6.imag
        )
        hammer = hammer_gain * hammer_phase.imag
        phase_1 *= phase_step_1
        phase_2 *= phase_step_2
        phase_3 *= phase_step_3
        phase_4 *= phase_step_4
        phase_5 *= phase_step_5
        phase_6 *= phase_step_6
        hammer_phase *= hammer_phase_step
        decay_1 *= decay_step_1
        decay_2 *= decay_step_2
        decay_3 *= decay_step_3
        decay_4 *= decay_step_4
        decay_5 *= decay_step_5
        decay_6 *= decay_step_6
        hammer_gain *= hammer_decay_step
        pcm = int(32767 * amplitude * envelope * (value / gain_total + hammer))
        if pcm < -32768:
            samples[index] = -32768
        elif pcm > 32767:
            samples[index] = 32767
        else:
            samples[index] = pcm
    return samples


def _render_legacy_note(
    pitch: int,
    duration: float,
    velocity: int,
    sample_rate: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
    release_seconds: float | None = None,
) -> array:
    """Keep the former fixed-four-partial renderer available for A/B comparison."""

    frequency = midi_to_frequency(pitch)
    release = RELEASE_SECONDS if release_seconds is None else release_seconds
    total = int((duration + release) * sample_rate)
    amplitude = 0.9 * (velocity / 127) / _HARMONIC_TOTAL
    # Lower pitches ring longer, like a real piano string.
    decay_tau = max(0.15, 1.4 - pitch * 0.008)
    sustain_end = duration * sample_rate

    samples = array("h", bytes(2 * total))
    if should_cancel is None:
        for i in range(total):
            t = i / sample_rate
            envelope = min(1.0, t / ATTACK_SECONDS) * exp(-t / decay_tau)
            if i > sustain_end:
                envelope *= max(0.0, 1.0 - (t - duration) / release)
            value = 0.0
            for multiplier, gain in _HARMONICS:
                value += gain * sin(2.0 * pi * frequency * multiplier * t)
            samples[i] = int(32767 * amplitude * envelope * value)
        return samples

    for i in range(total):
        if _cancelled_at(should_cancel, i):
            return array("h")
        t = i / sample_rate
        envelope = min(1.0, t / ATTACK_SECONDS) * exp(-t / decay_tau)
        if i > sustain_end:
            envelope *= max(0.0, 1.0 - (t - duration) / release)
        value = 0.0
        for multiplier, gain in _HARMONICS:
            value += gain * sin(2.0 * pi * frequency * multiplier * t)
        samples[i] = int(32767 * amplitude * envelope * value)
    return samples


def note_release_override(note):
    if note.release_mode == 'cut':
        return 0.012
    if note.release_mode == 'custom':
        return note.release_ms / 1000.0
    return None


def render_project(
    notes: Iterable[Note],
    sample_rate: int = SAMPLE_RATE,
    *,
    profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
    should_cancel: Callable[[], bool] | None = None,
) -> array:
    """Mix all notes into one 16-bit mono PCM buffer aligned to note start times."""
    notes = tuple(notes)
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(profile, PianoVoiceProfile):
        raise ValueError("profile must be a PianoVoiceProfile")
    if not notes:
        return array("h")
    if should_cancel is None:
        return _render_project_without_cancellation(notes, sample_rate, profile)
    if should_cancel():
        return array("h")

    end_time = max(
        note.start_time
        + sounding_duration(note)
        + (
            note_release_override(note) if note_release_override(note) is not None else
            RELEASE_SECONDS
            if profile is LEGACY_PIANO
            else profile.release_seconds(note.pitch, note.velocity, 0.8, ReleaseMode.NATURAL)
        )
        for note in notes
    )
    total = int(end_time * sample_rate) + 1
    mix = [0.0] * total
    for note in notes:
        if should_cancel is not None and should_cancel():
            return array("h")
        offset = int(note.start_time * sample_rate)
        rendered = render_note(
            note.pitch,
            sounding_duration(note),
            note.velocity,
            sample_rate,
            profile=profile,
            should_cancel=should_cancel,
            **({'release_seconds': note_release_override(note)} if note_release_override(note) is not None else {}),
        )
        if should_cancel is not None and should_cancel():
            return array("h")
        for index, sample in enumerate(rendered):
            if _cancelled_at(should_cancel, index):
                return array("h")
            mix[offset + index] += sample / 32767.0

    peak = 0.0
    for index, value in enumerate(mix):
        if _cancelled_at(should_cancel, index):
            return array("h")
        peak = max(peak, abs(value))
    scale = profile.peak_amplitude / peak if peak > profile.peak_amplitude else 1.0
    samples = array("h")
    for index, value in enumerate(mix):
        if _cancelled_at(should_cancel, index):
            return array("h")
        samples.append(max(-32768, min(32767, int(32767 * value * scale))))
    return samples


def _cancelled_at(should_cancel: Callable[[], bool] | None, index: int) -> bool:
    return (
        should_cancel is not None
        and index % _CANCELLATION_POLL_FRAMES == 0
        and should_cancel()
    )


def _render_project_without_cancellation(
    notes: tuple[Note, ...], sample_rate: int, profile: PianoVoiceProfile
) -> array:
    """Retain the pre-cancellation offline render hot path unchanged."""
    end_time = max(
        note.start_time
        + sounding_duration(note)
        + (
            note_release_override(note) if note_release_override(note) is not None else
            RELEASE_SECONDS
            if profile is LEGACY_PIANO
            else profile.release_seconds(note.pitch, note.velocity, 0.8, ReleaseMode.NATURAL)
        )
        for note in notes
    )
    total = int(end_time * sample_rate) + 1
    mix = [0.0] * total
    for note in notes:
        offset = int(note.start_time * sample_rate)
        rendered = render_note(note.pitch, sounding_duration(note), note.velocity, sample_rate, profile=profile,
                               **({'release_seconds':note_release_override(note)} if note_release_override(note) is not None else {}))
        for index, sample in enumerate(rendered):
            mix[offset + index] += sample / 32767.0

    peak = max((abs(value) for value in mix), default=0.0)
    scale = profile.peak_amplitude / peak if peak > profile.peak_amplitude else 1.0
    return array("h", (max(-32768, min(32767, int(32767 * value * scale))) for value in mix))


def encode_wav(
    samples: array, sample_rate: int = SAMPLE_RATE, *, channels: int = 1
) -> bytes:
    if isinstance(channels, bool) or not isinstance(channels, int) or channels <= 0:
        raise ValueError("channels must be a positive integer")
    if len(samples) % channels:
        raise ValueError("sample count must contain complete channel frames")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


def write_wav(
    path: Path,
    samples: array,
    sample_rate: int = SAMPLE_RATE,
    *,
    channels: int = 1,
) -> Path:
    return atomic_write_bytes(path, encode_wav(samples, sample_rate, channels=channels))
