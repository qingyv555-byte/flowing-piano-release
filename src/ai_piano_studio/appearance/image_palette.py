"""Bounded first-frame sampling and luminance-preserving theme tinting."""
import colorsys
from collections import defaultdict

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImageReader


def image_seed(path: str) -> QColor:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    reader.setScaledSize(reader.size().scaled(QSize(48, 48), Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return QColor("transparent")
    image = image.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buckets = defaultdict(float)
    samples = defaultdict(list)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            hue, saturation, lightness, alpha = color.getHslF()
            if alpha < 0.5 or saturation < 0.08 or not 0.08 < lightness < 0.92:
                continue
            bucket = int(hue * 24) % 24
            buckets[bucket] += saturation * alpha
            samples[bucket].append(color)
    if not buckets:
        return QColor("transparent")  # Neutral images retain the brand palette.
    colors = samples[max(buckets, key=buckets.get)]
    return QColor.fromRgbF(
        sum(c.redF() for c in colors) / len(colors),
        sum(c.greenF() for c in colors) / len(colors),
        sum(c.blueF() for c in colors) / len(colors),
    )


def luminance(color: QColor) -> float:
    def linear(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    return sum(weight * linear(channel) for weight, channel in
               zip((0.2126, 0.7152, 0.0722), color.getRgbF()[:3]))


def tint_color(value: str, seed: QColor) -> QColor:
    color = QColor(value)
    if seed.alpha() == 0:
        return color
    target = luminance(color)
    hue = max(0, seed.hslHueF())
    saturation = min(0.32, seed.hslSaturationF(), color.hslSaturationF())
    low, high = 0.0, 1.0
    for _ in range(18):
        lightness = (low + high) / 2
        rgb = colorsys.hls_to_rgb(hue, lightness, saturation)
        result = QColor.fromRgbF(*rgb, color.alphaF())
        if luminance(result) < target:
            low = lightness
        else:
            high = lightness
    return result
