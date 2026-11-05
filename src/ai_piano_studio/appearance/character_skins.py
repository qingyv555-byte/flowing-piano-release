"""Optional local character artwork; no third-party images are distributed."""
from pathlib import Path

from PySide6.QtCore import QUrl


SKINS = {
    "luna": {"name": "露娜 · 墨羽凝眸", "seed": "#56443b", "hue": 0.10, "dim": 0.18},
    "hack": {"name": "骇爪 · 青鸢窥心", "seed": "#7960b4", "hue": 0.73, "dim": 0.20},
    "bee": {"name": "蜂医 · 赠你晴日", "seed": "#789b80", "hue": 0.36, "dim": 0.20},
    "gale": {"name": "疾风 · 花风赴你", "seed": "#c77d62", "hue": 0.06, "dim": 0.20},
    "traveler": {"name": "旅人 · 山花同行", "seed": "#668ca9", "hue": 0.56, "dim": 0.18},
}

THEME_NAMES = {"luna": "暖棕", "hack": "绀紫", "bee": "森绿", "gale": "暖橙", "traveler": "雾蓝"}


def artwork_url(key: str, asset_home: Path | None = None) -> str:
    """Resolve allowlisted artwork within this profile's private asset folder."""
    if not isinstance(key, str) or key not in SKINS or asset_home is None:
        return ""
    folder = (Path(asset_home) / "character-skins").resolve()
    path = (folder / f"skin-{key}.png").resolve()
    if path.parent != folder or not path.is_file():
        return ""
    return QUrl.fromLocalFile(str(path)).toString()
