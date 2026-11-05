"""Public builds work without private artwork; local installations stay usable."""
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QColor, QImage, QImageReader

from ai_piano_studio.appearance.studio_settings import StudioAppearance


def test_profile_without_private_artwork_has_no_broken_skin_choices(qapp, tmp_path):
    appearance = StudioAppearance(
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
        tmp_path / "backgrounds",
    )
    assert appearance.characterSkins == []
    before = appearance.values
    appearance.applyCharacterSkin("gale")
    assert appearance.values == before
    assert appearance.backgroundUrl("skin:gale") == ""


def test_local_artwork_is_profile_scoped_and_survives_preview(qapp, tmp_path):
    home = tmp_path / "backgrounds"
    artwork = home / "character-skins" / "skin-gale.png"
    artwork.parent.mkdir(parents=True)
    image = QImage(48, 32, QImage.Format_RGB32)
    image.fill(QColor("#c77d62"))
    assert image.save(str(artwork))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    appearance = StudioAppearance(settings, home)
    assert [skin["key"] for skin in appearance.characterSkins] == ["gale"]
    appearance.beginPreview()
    appearance.applyCharacterSkin("gale")
    assert QImageReader(QUrl(appearance.backgroundUrl("skin:gale")).toLocalFile()).canRead()
    assert appearance.applyPreview()
    reopened = StudioAppearance(settings, home)
    assert reopened.values["characterSkin"] == "gale"
    assert artwork.is_file()
    other = StudioAppearance(
        QSettings(str(tmp_path / "other.ini"), QSettings.IniFormat),
        tmp_path / "other-backgrounds",
    )
    assert other.characterSkins == []
    assert other.backgroundUrl("skin:gale") == ""
