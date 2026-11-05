"""Studio instrument level calibration and shared PCM gain processing."""
from array import array

import numpy as np


SOURCE_TRIM_LIMIT = 9.0
# Playback starts 2.2x louder at the same master-volume setting. Export levels
# retain their existing calibration; scale_pcm still limits boosted peaks.
PLAYBACK_GAIN = 2.2
# Calibrated using matched sections of the same score. Salamander's live
# mixer has additional gain, so its offline renderer has its own baseline.
_LIVE_DB = {'salamander': -11.0, 'upright-sfz': 5.0}
_EXPORT_DB = {'salamander': 0.0, 'upright-sfz': 5.0}


def source_gain(sound_id: str, trim_db: float, *, export: bool = False) -> float:
    calibration = _EXPORT_DB if export else _LIVE_DB
    return 10 ** ((calibration[sound_id] + trim_db) / 20) * (1.0 if export else PLAYBACK_GAIN)


def scale_pcm(samples: np.ndarray, gain: float | np.ndarray) -> np.ndarray:
    scalar_gain = np.isscalar(gain)
    if scalar_gain and gain == 1.0:
        return samples
    values = samples * gain
    # Smoothly reserve headroom for boosted peaks. Attenuation remains linear.
    boosted = gain > 1.0
    if (boosted if scalar_gain else np.any(boosted)):
        knee = 28_000
        magnitude = np.abs(values)
        over = (magnitude > knee) & boosted
        headroom = 32_766 - knee
        values[over] = np.copysign(knee - headroom * np.expm1(-(magnitude[over] - knee) / headroom), values[over])
    return np.rint(values).astype('<i2')


def scale_export(samples: array, gain: float) -> array:
    if gain == 1.0:
        return samples
    source = np.asarray(samples, dtype=np.int16)
    result = array('h')
    for offset in range(0, len(source), 65_536):
        result.frombytes(scale_pcm(source[offset:offset + 65_536], gain).tobytes())
    return result


class GainRamp:
    def __init__(self):
        self.current = self.target = 1.0
        self.remaining = 0

    def set(self, target: float, frames: int) -> None:
        if target != self.target:
            self.target = target
            self.remaining = frames
            if not frames:
                self.current = target

    def block(self, frames: int) -> float | np.ndarray:
        if not self.remaining:
            return self.current
        count = min(frames, self.remaining)
        step = (self.target - self.current) / self.remaining
        values = np.full((frames, 1), self.target)
        values[:count, 0] = self.current + step * np.arange(1, count + 1)
        self.remaining -= count
        self.current = float(values[count - 1, 0]) if self.remaining else self.target
        return values
