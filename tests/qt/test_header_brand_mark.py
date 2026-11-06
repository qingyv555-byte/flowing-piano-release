from pathlib import Path
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import qml


def test_header_uses_unbadged_mark_and_theme_specific_artwork(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    mark = root.findChild(QObject, "windowChromeBrandMark")
    assert mark is not None
    assert mark.width() >= 24 and mark.height() >= 24
    settings = engine.rootContext().contextProperty("studioAppearance")
    for theme, filename in (("ink", "flowing-piano-header-light.svg"),
                            ("paper", "flowing-piano-header.svg")):
        settings.setValue("themeId", theme)
        qtbot.waitUntil(lambda: qml(mark, "status === Image.Ready"))
        assert mark.property("source").toString().endswith(filename)
    assert mark.metaObject().className().startswith("QQuickImage")


def test_header_variants_keep_the_approved_fp_paths_and_no_badge():
    brand = Path(__file__).resolve().parents[2] / "src/ai_piano_studio/resources/brand"
    ns = {"svg": "http://www.w3.org/2000/svg"}
    master = ET.parse(brand / "flowing-piano.svg").getroot()
    paths = [p.attrib["d"] for p in master.findall("svg:path", ns)]
    for name in ("flowing-piano-header.svg", "flowing-piano-header-light.svg"):
        header = ET.parse(brand / name).getroot()
        assert [p.attrib["d"] for p in header.findall("svg:path", ns)] == paths
        assert not any(header.findall(f"svg:{tag}", ns) for tag in ("rect", "circle", "ellipse"))
