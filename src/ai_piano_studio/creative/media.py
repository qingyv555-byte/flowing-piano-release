"""One bounded still image, normalized in memory without source metadata."""
import base64
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter

MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ImageInput:
    data: bytes = field(repr=False)
    width: int
    height: int
    mime: str = 'image/png'

    def __post_init__(self):
        if (self.mime != 'image/png' or not self.data.startswith(b'\x89PNG\r\n\x1a\n')
                or len(self.data) > MAX_BYTES or not 0 < self.width <= 2048 or not 0 < self.height <= 2048):
            raise ValueError('invalid normalized image')

    @property
    def data_url(self):
        return 'data:image/png;base64,' + base64.b64encode(self.data).decode('ascii')


def prepare_image(path):
    if '://' in str(path):
        raise ValueError('不读取远程图片 URL')
    target = Path(path)
    if target.stat().st_size > MAX_BYTES:
        raise ValueError('图片必须小于 10 MiB')
    with target.open('rb') as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('图片超过大小限制')
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 20_000_000:
        raise ValueError('图片尺寸无效或像素总量过大')
    if reader.supportsAnimation() or reader.imageCount() > 1:
        raise ValueError('首版仅支持单张静态图片')
    if bytes(reader.format()).lower() not in {b'png', b'jpeg', b'jpg', b'webp', b'bmp'}:
        raise ValueError('请选择 PNG、JPEG、WebP 或 BMP 静态图片')
    source = reader.read()
    if source.isNull():
        raise ValueError('图片解码失败')
    return normalize_image(source)


def normalize_image(source):
    if source.isNull() or source.width() * source.height() > 20_000_000:
        raise ValueError('图片尺寸无效或像素总量过大')
    if max(source.width(), source.height()) > 2048:
        source = source.scaled(2048, 2048, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # A fresh raster carries none of the source's text, EXIF or private metadata.
    raster = QImage(source.size(), QImage.Format_RGB32)
    raster.fill(Qt.white)
    painter = QPainter(raster)
    painter.drawImage(0, 0, source)
    painter.end()
    encoded = QByteArray()
    output = QBuffer(encoded)
    output.open(QIODevice.WriteOnly)
    if not raster.save(output, 'PNG'):
        raise ValueError('图片转换失败')
    return ImageInput(bytes(encoded), raster.width(), raster.height())


def attach_openai_image(messages, attachments):
    if len(attachments) != 1 or not isinstance(attachments[0], ImageInput):
        raise ValueError('only one normalized still image is supported')
    result = [dict(message) for message in messages]
    # Attach to the source user turn, even during later tool continuation.
    index = next((i for i in range(len(result)-1, -1, -1) if result[i].get('role') == 'user'), None)
    if index is None or not isinstance(result[index].get('content'), str):
        raise ValueError('image requires a textual user turn')
    result[index]['content'] = [
        {'type': 'text', 'text': result[index]['content']},
        {'type': 'image_url', 'image_url': {'url': attachments[0].data_url}},
    ]
    return result


class ImageTaskSignals(QObject):
    finished = Signal(int, object, str)


class ImageTask(QRunnable):
    def __init__(self, path, epoch):
        super().__init__()
        self.path, self.epoch = path, epoch
        self.signals = ImageTaskSignals()

    def run(self):
        try:
            result = normalize_image(self.path) if isinstance(self.path, QImage) else prepare_image(self.path)
            self.signals.finished.emit(self.epoch, result, '')
        except Exception:
            self.signals.finished.emit(self.epoch, None, '图片无法处理，请选择小于10 MiB、2000万像素以内的静态图片。')
