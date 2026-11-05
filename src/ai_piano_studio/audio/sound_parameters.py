"""Instrument-independent tuning, shared by realtime output and file rendering."""
from array import array
from dataclasses import dataclass, replace
from math import isfinite

import numpy as np

from .sound_gain import GainRamp


@dataclass(frozen=True)
class SoundParameters:
    brightness: float = .5
    response: float = .5
    reverb: float = 0.0
    width: float = .5

    def with_value(self, name, value):
        if name not in self.__dataclass_fields__ or not isfinite(float(value)):
            raise ValueError('Unknown sound parameter or non-finite value')
        return replace(self, **{name: max(0.0, min(1.0, float(value)))})


def velocity_response(velocity, response):
    if response == .5 or velocity == 0:
        return velocity
    return max(1, min(127, round(127 * (velocity / 127) ** (2 ** (2 * response - 1)))))


def map_velocities(notes, response):
    if response == .5:
        return notes
    result = []
    for item in notes:
        note = getattr(item, 'note', item)
        changed = replace(note, velocity=velocity_response(note.velocity, response))
        result.append(replace(item, note=changed) if note is not item else changed)
    return tuple(result)


class VelocitySource:
    """Apply response at note-on, without editing the score or prefetch contract."""
    def __init__(self, source, response):
        self.source, self.response = source, response

    def __getattr__(self, name):
        return getattr(self.source, name)

    def start_voice(self, voice_id, pitch, velocity):
        return self.source.start_voice(voice_id, pitch, velocity_response(velocity, self.response()))


class SoundProcessor:
    """Causal tone shelf, stereo width and a small stereo feedback reverb.

    State spans audio blocks. Neutral settings preserve the original PCM exactly.
    Coefficients ramp over 20 ms; there is no file I/O or worker on the audio path.
    """
    def __init__(self, sample_rate, parameters=SoundParameters()):
        self.sample_rate = sample_rate
        self._ramps = [GainRamp() for _ in range(3)]
        self.parameters = parameters
        self.set(parameters, immediate=True)
        self.clear()

    def clear(self):
        self._previous = np.zeros((16, 2))
        self._delays = [np.zeros((max(1, round(self.sample_rate * seconds)), 2))
                        for seconds in (.0297, .0371, .0411, .0437)]
        self._positions = [0] * len(self._delays)
        self._fade_remaining = 0
        self._reverb_active = False

    def fade_out(self):
        self._fade_total = self._fade_remaining = max(1, round(self.sample_rate * .012))

    def set(self, parameters, *, immediate=False):
        self.parameters = parameters
        targets = ((parameters.brightness - .5) * 1.8, parameters.width * 2, parameters.reverb)
        for ramp, value in zip(self._ramps, targets):
            ramp.set(value, 0 if immediate else max(1, round(self.sample_rate * .02)))

    def process(self, data):
        if not data:
            return data
        pcm = np.frombuffer(data, dtype='<i2').reshape(-1, 2)
        frames = len(pcm)
        tone, width, wet = [ramp.block(frames) for ramp in self._ramps]
        source = pcm.astype(np.float64)
        extended = np.concatenate((self._previous, source))
        self._previous = extended[-16:].copy()
        neutral = all(np.isscalar(v) and v == target for v, target in ((tone, 0), (width, 1), (wet, 0)))
        if neutral:
            if self._reverb_active:
                for delay in self._delays:
                    delay.fill(0)
                self._reverb_active = False
            self._fade_remaining = 0
            return data
        # A short causal moving average forms the low band; residual is the shelf.
        cumulative = np.vstack((np.zeros((1, 2)), np.cumsum(extended, axis=0)))
        low = (cumulative[17:] - cumulative[:-17]) / 17
        source += tone * (source - low)
        if np.any(wet):
            self._reverb_active = True
            reflections = np.zeros_like(source)
            for index, delay in enumerate(self._delays):
                position = self._positions[index]
                offset = 0
                while offset < frames:
                    count = min(frames - offset, len(delay) - position)
                    echo = delay[position:position + count].copy()
                    # Cross-feedback decorrelates channels even for a mono source.
                    delay[position:position + count] = source[offset:offset + count] + .62 * echo[:, ::-1]
                    reflections[offset:offset + count] += echo * ([1, .65] if index % 2 else [.65, 1])
                    position = (position + count) % len(delay)
                    offset += count
                self._positions[index] = position
            source = source * (1 - wet * .25) + reflections * (wet * .16)
        elif self._reverb_active:
            for delay in self._delays:
                delay.fill(0)
            self._reverb_active = False
        mid = source.mean(axis=1, keepdims=True)
        source = mid + (source - mid) * width
        if self._fade_remaining:
            count = min(frames, self._fade_remaining)
            gain = np.zeros((frames, 1))
            gain[:count, 0] = (self._fade_remaining - np.arange(count)) / self._fade_total
            source *= gain
            self._fade_remaining -= count
            if not self._fade_remaining:
                self.clear()
        # Preserve linear quiet audio and smoothly reserve headroom for boosts.
        magnitude = np.abs(source)
        over = magnitude > 28000
        source[over] = np.copysign(28000 - 4766 * np.expm1(-(magnitude[over] - 28000) / 4766), source[over])
        return np.rint(source).astype('<i2').tobytes()


def process_export(samples, sample_rate, channels, parameters):
    if (parameters.brightness, parameters.reverb, parameters.width) == (.5, 0, .5):
        return samples, channels
    pcm = np.asarray(samples, dtype='<i2').reshape(-1, channels)
    if channels == 1:
        pcm = np.repeat(pcm, 2, axis=1)
    processor = SoundProcessor(sample_rate, parameters)
    result = array('h')
    for offset in range(0, len(pcm), 1024):
        result.frombytes(processor.process(pcm[offset:offset + 1024].tobytes()))
    # Preserve the reverb decay past the renderer's own release tail.
    if parameters.reverb:
        for offset in range(0, sample_rate * 2, 1024):
            count = min(1024, sample_rate * 2 - offset)
            result.frombytes(processor.process(bytes(count * 4)))
    return result, 2
