"""Render the approved FP vector into PNG and multi-resolution Windows ICO."""
from pathlib import Path
import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    root = Path(__file__).resolve().parents[1]
    brand = root / "src/ai_piano_studio/resources/brand"
    renderer = QSvgRenderer(str(brand / "flowing-piano.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid brand SVG")
    chunks = []
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    for size in (*sizes, 512, 1024):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("PNG export failed")
        if size in sizes:
            chunks.append(bytes(data))
        if size == 1024:
            (brand / "flowing-piano.png").write_bytes(bytes(data))
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, chunks):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   1, 32, len(data), offset))
        offset += len(data)
    (brand / "flowing-piano.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(chunks))


if __name__ == "__main__":
    main()
