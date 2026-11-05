"""Transactional appearance preferences for the opt-in Studio profile."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QRunnable, QSettings, QSize, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QImageReader

from .image_palette import image_seed, tint_color
from .character_skins import SKINS, THEME_NAMES, artwork_url


DEFAULTS = {
    "characterSkin": "none", "themePalette": "none",
    "noteColorSource": "palette", "customNoteColor": "#53a79b",
    # A quiet single green is the default; part colors remain opt-in.
    "roleColorMode": "uniform", "noteColorDefaultsVersion": 1,
    "roleMelodyColor": "#f1bb78",
    "roleAccompanimentColor": "#8cb9d7",
    "roleBassColor": "#89c1a6",
    "roleDrumsColor": "#ca9cad",
    "roleOtherColor": "#aabac3",
    "noteStyle": "flat",
    "themeId": "paper", "homeSource": "builtin", "editorSource": "builtin",
    "backgroundImportSize": "1440",
    "homeOpacity": 0.36, "editorOpacity": 0.12, "homeDim": 0.2, "editorDim": 0.5,
    "homeFit": "cover", "editorFit": "cover", "homeFocusX": 0.5, "homeFocusY": 0.5,
    "editorFocusX": 0.5, "editorFocusY": 0.5, "feedbackLevel": "standard",
    "feedbackStyle": "glass", "feedbackStrength": "balanced",
    "glassHue": 0.46, "glassSaturation": 0.28, "glassHeight": 0.22,
    "glassWidth": 1.0, "glassPositionX": 0.5, "glassPositionY": 1.0,
    "noteFeedback": True, "keyFeedback": True, "spectrum": True, "atmosphere": True,
    "reducedMotion": False, "motionFps": 30,
    "homeAmbient": True,
    "adaptiveTheme": False, "themeImageTarget": "home",
    "themeSeedSource": "palette",
    "customThemeColor": "#668ca9",
}
_CHOICES = {"characterSkin": {"none", *SKINS},
            "noteStyle": {"flat", "rounded", "velocity"},
            "roleColorMode": {"uniform", "auto", "custom"},
            "themePalette": {"none", *SKINS}, "noteColorSource": {"palette", "custom"},
            "themeId": {"ink", "paper"}, "feedbackLevel": {"off", "standard", "rich"},
            "themeImageTarget": {"home", "editor"},
            "themeSeedSource": {"palette", "image", "custom"},
            "backgroundImportSize": {"1080", "1440", "2160"},
            "feedbackStyle": {"legacy", "string", "weave", "glass"},
            "feedbackStrength": {"gentle", "balanced", "vivid"},
            "homeFit": {"cover", "contain"}, "editorFit": {"cover", "contain"}}
_BACKGROUND_IMPORT_SIZES = {
    "1080": QSize(1920, 1080),
    "1440": QSize(2560, 1440),
    "2160": QSize(3840, 2160),
}
_OWNED_NAME = re.compile(r"studio-[0-9a-f]{32}\.(png|gif)$")
_SETTINGS_KEY = "studioAppearance/values"
_RATES_KEY = "studioAppearance/animationRates"


def _import_image(source: str, home: Path, maximum_size: QSize) -> tuple[Path, float, str]:
    """Validate and decode only in a worker; return a small, owned local asset."""
    url = QUrl(source)
    if url.scheme() and not url.isLocalFile() and len(url.scheme()) != 1:
        raise ValueError("请选择电脑上的图片文件。")
    path = Path(url.toLocalFile() if url.isLocalFile() else source)
    if not path.is_file():
        raise ValueError("找不到这张图片，请重新选择。")
    if path.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("图片超过 24 MB，请选择更小的文件。")
    home.mkdir(parents=True, exist_ok=True)
    # Freeze a bounded snapshot before decoding, even if the source is edited
    # concurrently. The staging directory disappears on success and failure.
    with TemporaryDirectory(prefix="studio-import-", dir=home) as staging:
        copied = Path(staging) / "source"
        with path.open("rb") as original, copied.open("wb") as snapshot:
            total = 0
            while chunk := original.read(1024 * 1024):
                total += len(chunk)
                if total > 24 * 1024 * 1024:
                    raise ValueError("图片超过 24 MB，请选择更小的文件。")
                snapshot.write(chunk)
        return _prepare_image(copied, home, maximum_size)


def _prepare_image(path: Path, home: Path, maximum_size: QSize) -> tuple[Path, float, str]:
    reader = QImageReader(str(path))
    try:
        return _decode_image(reader, path, home, maximum_size)
    finally:
        # On Windows an exception traceback retains the reader, so release its
        # QFile before TemporaryDirectory attempts to remove the snapshot.
        reader.setFileName("")


def _decode_image(
    reader: QImageReader, path: Path, home: Path, maximum_size: QSize
) -> tuple[Path, float, str]:
    reader.setAutoTransform(True)
    image_format = bytes(reader.format()).lower()
    if image_format not in {b"png", b"jpeg", b"webp", b"bmp", b"gif"} or not reader.canRead():
        raise ValueError("无法读取这张图片，请选择 PNG、JPG、WebP、BMP 或 GIF。")
    size = reader.size()
    pixels = size.width() * size.height()
    if size.isEmpty():
        raise ValueError("图片尺寸无效，请换一张图片。")
    if max(size.width(), size.height()) > 65535 or pixels > 512_000_000:
        raise ValueError("图片尺寸异常，无法安全处理，请换一张较小的图片。")
    animated = image_format == b"gif"
    if animated and (size.width() > 1280 or size.height() > 720):
        raise ValueError("GIF 请控制在 1280 × 720 以内，以保持编辑流畅。")
    scaled = False
    if not animated and (
        size.width() > maximum_size.width() or size.height() > maximum_size.height()
    ):
        reader.setScaledSize(size.scaled(maximum_size, Qt.KeepAspectRatio))
        scaled = True
    first = reader.read()
    if first.isNull():
        raise ValueError("图片内容不完整，请换一张图片。")
    if not animated and (
        first.width() > maximum_size.width() or first.height() > maximum_size.height()
    ):
        first = first.scaled(maximum_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled = True
    maximum_fps = 0.0
    if animated:
        frames = 1
        maximum_fps = 1000 / max(20, reader.nextImageDelay())
        # Validate every frame while retaining only one decoded frame at a time.
        while reader.canRead():
            frames += 1
            if frames > 180 or pixels * frames > 64_000_000:
                raise ValueError("GIF 过长或过于复杂，请使用更短、更小的动画。")
            if reader.read().isNull():
                raise ValueError("GIF 动画损坏，请换一个文件。")
            maximum_fps = max(maximum_fps, 1000 / max(20, reader.nextImageDelay()))
    home.mkdir(parents=True, exist_ok=True)
    stem = home / ("studio-" + uuid4().hex)
    cover = stem.with_suffix(".png")
    result = stem.with_suffix(".gif") if animated else cover
    try:
        if not first.save(str(cover), "PNG"):
            raise OSError("Unable to save background cover")
        if animated:
            shutil.copyfile(path, result)
        if animated:
            notice = f"已导入 GIF {first.width()} × {first.height()}，保留动画。"
        elif scaled:
            notice = f"已自动缩小为 {first.width()} × {first.height()}，原图保持不变。"
        else:
            notice = f"已导入 {first.width()} × {first.height()}，保持原尺寸。"
        return result, maximum_fps, notice
    except OSError:
        cover.unlink(missing_ok=True)
        result.unlink(missing_ok=True)
        raise


class _ImportJob(QRunnable):
    def __init__(
        self, owner: StudioAppearance, token: int, target: str, source: str,
        maximum_size: QSize,
    ):
        super().__init__()
        self.owner, self.token, self.target, self.source = owner, token, target, source
        self.maximum_size = QSize(maximum_size)

    def run(self):
        result, error, notice, maximum_fps = "", "", "", 0.0
        try:
            path, maximum_fps, notice = _import_image(
                self.source, self.owner._asset_home, self.maximum_size
            )
            result = str(path)
        except ValueError as exc:
            error = str(exc)
        except (OSError, RuntimeError):
            error = "无法保存背景图片，请检查文件权限和剩余空间后重试。"
        try:
            self.owner._import_finished.emit(
                self.token, self.target, result, error, maximum_fps, notice
            )
        except RuntimeError:
            # The window can be destroyed while its bounded worker is finishing.
            if result:
                path = Path(result)
                path.unlink(missing_ok=True)
                path.with_suffix(".png").unlink(missing_ok=True)


class _SeedJob(QRunnable):
    def __init__(self, owner, source):
        super().__init__()
        self.owner, self.source = owner, source

    def run(self):
        path = Path(QUrl(self.source).toLocalFile()).with_suffix(".png")
        try:
            seed = image_seed(str(path))
            self.owner._seed_finished.emit(self.source, seed)
        except RuntimeError:
            # The owning window may already have been destroyed.
            return


class StudioAppearance(QObject):
    changed = Signal()
    _import_finished = Signal(int, str, str, str, float, str)
    _seed_finished = Signal(str, QColor)

    def __init__(self, settings: QSettings, asset_home: Path, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._asset_home = Path(asset_home).resolve()
        self._values = dict(DEFAULTS)
        self._error = ""
        self._notice = ""
        self._running = False
        self._pending: dict[str, tuple[int, str]] = {}
        self._serial = 0
        self._latest = {"home": 0, "editor": 0}
        self._preview = False
        self._rates: dict[str, float] = {}
        self._seeds: dict[str, QColor] = {}
        self._sampling: set[str] = set()
        self._owned = {p for p in self._asset_home.glob("studio-*") if _OWNED_NAME.fullmatch(p.name)}
        try:
            saved = json.loads(str(settings.value(_SETTINGS_KEY, "{}")))
            if isinstance(saved, dict):
                for key, value in saved.items():
                    if key in DEFAULTS:
                        self._values[key] = self._normalize(key, value)
                # Older versions shared the palette and note-material key.
                # Migrate in memory; never overwrite preferences until Apply.
                if "themePalette" not in saved:
                    self._values["themePalette"] = self._values["characterSkin"]
                if "noteColorDefaultsVersion" not in saved and self._values["roleColorMode"] == "auto":
                    self._values.update(roleColorMode="uniform", characterSkin="none", noteColorSource="palette")
        except (ValueError, TypeError):
            self._error = "外观设置无法读取，已恢复默认外观。"
        try:
            rates = json.loads(str(settings.value(_RATES_KEY, "{}")))
            if isinstance(rates, dict):
                self._rates = {source: float(rate) for source, rate in rates.items()
                               if isinstance(rate, (int, float)) and 0 < rate <= 50}
        except (ValueError, TypeError):
            self._rates = {}
        self._committed = dict(self._values)
        self._baseline = dict(self._values)
        self._import_finished.connect(self._finish_import, Qt.QueuedConnection)
        self._seed_finished.connect(self._finish_seed, Qt.QueuedConnection)
        self.changed.connect(self._ensure_seed)
        self._prune_assets()
        self._ensure_seed()

    def _ensure_seed(self):
        if self._values["themeSeedSource"] == "custom":
            return
        if self._values["themePalette"] in SKINS and self._values["themeSeedSource"] == "palette":
            return
        source = self._values[self._values["themeImageTarget"] + "Source"]
        if source.startswith("skin:"):
            return
        if (self._values["adaptiveTheme"] and source not in ("builtin", "none")
                and source not in self._seeds and source not in self._sampling):
            self._sampling.add(source)
            QThreadPool.globalInstance().start(_SeedJob(self, source))

    @Slot(str, QColor)
    def _finish_seed(self, source, seed):
        self._sampling.discard(source)
        self._seeds[source] = seed
        self.changed.emit()

    @Property(QColor, notify=changed)
    def themeSeed(self):
        if not self._values["adaptiveTheme"]:
            return QColor("transparent")
        if self._values["themeSeedSource"] == "custom":
            return QColor(self._values["customThemeColor"])
        if self._values["themePalette"] in SKINS and self._values["themeSeedSource"] == "palette":
            return QColor(SKINS[self._values["themePalette"]]["seed"])
        source = self._values[self._values["themeImageTarget"] + "Source"]
        if source.startswith("skin:") and source[5:] in SKINS:
            return QColor(SKINS[source[5:]]["seed"])
        return self._seeds.get(source, QColor("transparent"))

    @Property("QVariantList", constant=True)
    def characterSkins(self):
        return [{"key": key, "name": skin["name"], "art": art}
                for key, skin in SKINS.items()
                if (art := artwork_url(key, self._asset_home))]

    @Property("QVariantList", constant=True)
    def characterThemes(self):
        return [{"key": key, "name": THEME_NAMES[key], "color": skin["seed"]}
                for key, skin in SKINS.items()]

    @Slot(str)
    def applyCharacterTheme(self, key):
        if key not in SKINS:
            return
        skin = SKINS[key]
        # Theme selection never changes note color or material.
        self._values.update(themePalette=key, themeId="ink", adaptiveTheme=True, themeSeedSource="palette",
                            themeImageTarget="editor", glassHue=skin["hue"],
                            glassSaturation=0.25, glassHeight=0.16)
        self.changed.emit()

    @Slot(str)
    def applyCustomThemeColor(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return
        color = QColor(value)
        # Color is independent of the user's wallpaper, light/dark base and
        # native note style. The existing outer preview transaction owns save.
        self._values.update(customThemeColor=color.name(), themeSeedSource="custom",
                            adaptiveTheme=True, glassHue=max(0.0, color.hsvHueF()),
                            glassSaturation=color.hsvSaturationF())
        self.changed.emit()

    @Slot(str)
    def applyNotePalette(self, key):
        if key not in {"none", *SKINS}:
            return
        self._values.update(characterSkin=key, noteColorSource="palette")
        self.changed.emit()

    @Slot(str)
    def applyCustomNoteColor(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return
        self._values.update(customNoteColor=QColor(value).name(), noteColorSource="custom", roleColorMode="uniform")
        self.changed.emit()

    @Slot(str, result=str)
    def backgroundUrl(self, source):
        return artwork_url(source[5:], self._asset_home) if source.startswith("skin:") else source

    @Slot(str)
    def applyCharacterSkin(self, key):
        if key != "none" and not artwork_url(key, self._asset_home):
            return
        self._invalidate_imports()
        # One notification keeps the palette, artwork and note material in sync.
        preset_keys = ("characterSkin", "themePalette", "noteColorSource", "themeId", "adaptiveTheme", "themeImageTarget", "themeSeedSource",
                       "homeSource", "editorSource", "homeOpacity", "editorOpacity",
                       "homeDim", "editorDim", "homeFit", "editorFit",
                       "homeFocusX", "homeFocusY", "editorFocusX", "editorFocusY",
                       "feedbackStyle", "glassHue", "glassSaturation", "glassHeight")
        preset = {name: DEFAULTS[name] for name in preset_keys}
        if key in SKINS:
            skin = SKINS[key]
            preset.update(characterSkin=key, themePalette=key, themeId="ink", adaptiveTheme=True, themeImageTarget="editor",
                          homeSource=f"skin:{key}", editorSource=f"skin:{key}",
                          homeOpacity=1.0, editorOpacity=1.0,
                          homeDim=skin["dim"], editorDim=skin["dim"],
                          glassHue=skin["hue"], glassSaturation=0.25, glassHeight=0.16)
        self._values.update(preset)
        self.changed.emit()

    @Slot(str, result=QColor)
    def tintColor(self, value):
        return tint_color(value, self.themeSeed)

    @Property("QVariantMap", notify=changed)
    def values(self):
        return dict(self._values)

    @Property(bool, notify=changed)
    def importBusy(self):
        return self._running or bool(self._pending)

    @Property(str, notify=changed)
    def importError(self):
        return self._error

    @Property(str, notify=changed)
    def importNotice(self):
        return self._notice

    def _normalize(self, key, value):
        default = DEFAULTS[key]
        if key == "noteColorDefaultsVersion":
            return default
        if key in ("customThemeColor", "customNoteColor",
                   "roleMelodyColor", "roleAccompanimentColor", "roleBassColor",
                   "roleDrumsColor", "roleOtherColor"):
            return QColor(value).name() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default
        if key in _CHOICES:
            return value if isinstance(value, str) and value in _CHOICES[key] else default
        if key.endswith("Source"):
            if value in ("builtin", "none"):
                return value
            if isinstance(value, str) and value.startswith("skin:") and value[5:] in SKINS:
                return value if artwork_url(value[5:], self._asset_home) else "builtin"
            url = QUrl(str(value))
            path = Path(url.toLocalFile()).resolve() if url.isLocalFile() else None
            if (path and path.parent == self._asset_home and _OWNED_NAME.fullmatch(path.name)
                    and path.is_file()):
                return QUrl.fromLocalFile(str(path)).toString()
            return "builtin"
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).lower() in {"true", "1"}
        try:
            number = float(value)
            if key == "motionFps":
                return int(number) if number in (15, 30, 60) else default
            if key in ("glassHeight", "glassWidth"):
                lower, upper = (0.08, 0.40) if key == "glassHeight" else (0.25, 1.0)
                return max(lower, min(upper, number)) if math.isfinite(number) else default
            return max(0.0, min(1.0, number)) if math.isfinite(number) else default
        except (ValueError, TypeError, OverflowError):
            return default

    @Slot(str, "QVariant")
    def setValue(self, key, value):
        if key not in DEFAULTS:
            return
        if key in ("roleMelodyColor", "roleAccompanimentColor", "roleBassColor",
                   "roleDrumsColor", "roleOtherColor"):
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                return
        normalized = self._normalize(key, value)
        if key.endswith("Source"):
            self._serial += 1
            self._latest[key[:-6]] = self._serial
            self._pending.pop(key[:-6], None)
        if self._values[key] != normalized:
            self._values[key] = normalized
            self.changed.emit()

    @Slot()
    def beginPreview(self):
        if not self._preview:
            self._baseline = dict(self._values)
            self._preview = True
        self._error = ""
        self._notice = ""
        self.changed.emit()

    def _invalidate_imports(self):
        self._serial += 1
        self._latest = {"home": self._serial, "editor": self._serial}
        self._pending.clear()

    @Slot(result=bool)
    def applyPreview(self):
        if self.importBusy:
            self._error = "图片还在准备中，请稍候再应用。"
            self.changed.emit()
            return False
        previous = {key: self._settings.value(key) for key in (_SETTINGS_KEY, _RATES_KEY)}
        self._settings.setValue(_SETTINGS_KEY, json.dumps(self._values, ensure_ascii=False))
        self._settings.setValue(_RATES_KEY, json.dumps(self._rates))
        self._settings.sync()
        if self._settings.status() != QSettings.NoError:
            for key, old in previous.items():
                if old is None:
                    self._settings.remove(key)
                else:
                    self._settings.setValue(key, old)
            self._error = "外观设置未能保存，请检查文件权限或剩余空间后重试。"
            self.changed.emit()
            return False
        self._committed = dict(self._values)
        self._baseline = dict(self._values)
        self._preview = False
        self._error = ""
        self._notice = ""
        self._prune_assets()
        self.changed.emit()
        return True

    @Slot()
    def cancelPreview(self):
        self._invalidate_imports()
        self._values = dict(self._baseline)
        self._preview = False
        self._error = ""
        self._notice = ""
        self._prune_assets()
        self.changed.emit()

    @Slot()
    def resetPreview(self):
        self._invalidate_imports()
        self._values = dict(DEFAULTS)
        self._error = ""
        self._prune_assets()
        self.changed.emit()

    @Slot(str, str)
    def importBackground(self, target, source):
        if target not in self._latest:
            return
        self._serial += 1
        self._latest[target] = self._serial
        self._pending[target] = (self._serial, source)
        self._error = ""
        self._notice = ""
        self._start_next()
        self.changed.emit()

    def _start_next(self):
        if self._running or not self._pending:
            return
        target = next(iter(self._pending))
        token, source = self._pending.pop(target)
        self._running = True
        maximum_size = _BACKGROUND_IMPORT_SIZES[self._values["backgroundImportSize"]]
        QThreadPool.globalInstance().start(
            _ImportJob(self, token, target, source, maximum_size)
        )

    @Slot(int, str, str, str, float, str)
    def _finish_import(self, token, target, source, error, maximum_fps, notice):
        self._running = False
        if source:
            path = Path(source)
            self._owned.update({path, path.with_suffix(".png")})
        if self._latest[target] == token:
            self._error = error
            self._notice = notice if source and not error else ""
            if source:
                url = QUrl.fromLocalFile(source).toString()
                self._values[target + "Source"] = url
                if maximum_fps:
                    self._rates[url] = maximum_fps
        self._prune_assets()
        self._start_next()
        self.changed.emit()

    @Slot(str, int, result=float)
    def animationSpeed(self, source, fps):
        """Bound GIF presentation by slowing its native timing, without a UI timer."""
        limit = fps if fps in (15, 30, 60) else 30
        return min(1.0, limit / self._rates.get(source, 50.0))

    def _prune_assets(self):
        retained = set()
        for values in (self._committed, self._baseline, self._values):
            for key in ("homeSource", "editorSource"):
                url = QUrl(values[key])
                if url.isLocalFile():
                    path = Path(url.toLocalFile()).resolve()
                    retained.update({path, path.with_suffix(".png")})
        for path in self._owned - retained:
            try:
                path.unlink(missing_ok=True)
                self._owned.discard(path)
                self._rates.pop(QUrl.fromLocalFile(str(path)).toString(), None)
            except OSError:
                self._error = "部分旧背景暂时无法清理，关闭图片占用程序后再试。"
