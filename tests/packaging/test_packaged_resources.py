from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
import runpy
from types import SimpleNamespace


ROOT = Path(__file__).parents[2]
FINAL_PIANO_BANK_BYTES = 18_510_384
FINAL_PIANO_RESOURCE_BYTES = 55_531_152


EXPECTED_PACKAGED_FAQ_HEADINGS = (
    "和弦到底什么时候出现？",
    "为什么打开旧工程没有马上铺满和弦？",
    "实时预览和“检测整首并生成”有什么区别？",
    "为什么这次没有添加和弦？",
    "为什么已有和弦/重叠音会被跳过？",
    "怎样更容易得到可靠建议？",
)


def test_packaged_resources_locate_the_updated_harmony_beginner_guide() -> None:
    guide = files("ai_piano_studio").joinpath(
        "resources", "help", "harmony_beginner_guide.md"
    )

    assert guide.is_file()
    guide_text = guide.read_text(encoding="utf-8")
    assert guide_text.startswith(
        "# AI Piano Studio 和弦入门指南"
    )
    faq_headings = re.findall(r"^### (.+)$", guide_text, flags=re.MULTILINE)
    assert len(re.findall(r"^## ", guide_text, flags=re.MULTILINE)) == 13
    missing = [
        heading for heading in EXPECTED_PACKAGED_FAQ_HEADINGS
        if heading not in faq_headings
    ]

    assert missing == [], f"packaged guide is missing FAQ headings: {missing}"


def test_pyinstaller_collects_only_formal_piano_resources(
    tmp_path: Path,
) -> None:
    """Fails if packaging includes raw assets or omits a formal piano resource."""
    spec_root = tmp_path / "build-worktree"
    package_root = spec_root / "src" / "ai_piano_studio"
    (package_root / "qml").mkdir(parents=True)
    (package_root / "resources").mkdir()
    piano_root = spec_root / "resources" / "piano"
    piano_root.mkdir(parents=True)
    (piano_root / "LICENSE-SALAMANDER.txt").write_text("license", encoding="utf-8")
    (piano_root / "PROVENANCE-SALAMANDER.json").write_text("{}", encoding="utf-8")
    (piano_root / "PROVENANCE-SALAMANDER.md").write_text("provenance", encoding="utf-8")
    (piano_root / "banks.json").write_text("{\"banks\": []}", encoding="utf-8")
    (piano_root / "banks" / "layers-06-11").mkdir(parents=True)
    (piano_root / "banks" / "layers-06-11" / "A0-soft.wav").write_bytes(b"bank")
    (piano_root / "source-assets").mkdir()
    (piano_root / "source-assets" / "raw.wav").write_bytes(b"raw")
    (piano_root / "SalamanderGrandPianoV3_44.1khz16bit.tar.bz2").write_bytes(b"raw")
    (spec_root / "THIRD_PARTY_NOTICES.md").write_text("notice", encoding="utf-8")
    for name in ("LICENSE", "COPYRIGHT"):
        (spec_root / name).write_text("notice", encoding="utf-8")
    (spec_root / "docs").mkdir()
    (spec_root / "docs" / "LICENSING.md").write_text("guide", encoding="utf-8")
    captured: dict[str, object] = {}

    def analysis(*args, **kwargs):
        captured["datas"] = kwargs["datas"]
        return SimpleNamespace(
            scripts=[], pure=[], binaries=[], datas=kwargs["datas"], zipped_data=[]
        )

    runpy.run_path(
        str(ROOT / "ai_piano_studio.spec"),
        init_globals={
            "SPECPATH": str(spec_root),
            "Analysis": analysis,
            "PYZ": lambda *args, **kwargs: object(),
            "EXE": lambda *args, **kwargs: object(),
            "COLLECT": lambda *args, **kwargs: object(),
        },
    )

    assert captured["datas"] == [
        (str(spec_root / "build" / "release-notices"), "ai_piano_studio/resources/third-party"),
        (str(package_root / "qml"), "ai_piano_studio/qml"),
        (str(package_root / "resources"), "ai_piano_studio/resources"),
        (str(piano_root / "banks.json"), "ai_piano_studio/resources/piano"),
        (str(piano_root / "banks"), "ai_piano_studio/resources/piano/banks"),
        (str(piano_root / "LICENSE-SALAMANDER.txt"), "ai_piano_studio/resources/piano"),
        (str(piano_root / "PROVENANCE-SALAMANDER.json"), "ai_piano_studio/resources/piano"),
        (str(piano_root / "PROVENANCE-SALAMANDER.md"), "ai_piano_studio/resources/piano"),
        (str(spec_root / "THIRD_PARTY_NOTICES.md"), "ai_piano_studio/resources"),
        (str(spec_root / "LICENSE"), "ai_piano_studio/resources"),
        (str(spec_root / "COPYRIGHT"), "ai_piano_studio/resources"),
        (str(spec_root / "docs" / "LICENSING.md"), "ai_piano_studio/resources"),
    ]


def test_formal_piano_package_data_is_complete_and_excludes_raw_assets() -> None:
    """Fails if a distributable piano bank is incomplete or ships working files."""
    piano_root = ROOT / "resources" / "piano"
    catalog = piano_root / "banks.json"
    manifests = sorted(piano_root.glob("banks/*/manifest.json"))
    wavs = sorted(piano_root.glob("banks/*/*.wav"))

    assert catalog.is_file()
    assert [path.parent.name for path in manifests] == [
        "layers-06-11",
        "layers-07-12",
        "layers-08-13",
    ]
    assert len(wavs) == 108
    assert all(path.stat().st_size > 44 for path in wavs)
    assert sum(path.stat().st_size for path in wavs) == FINAL_PIANO_RESOURCE_BYTES
    assert {
        bank.name: sum(path.stat().st_size for path in bank.glob("*.wav"))
        for bank in (piano_root / "banks").iterdir()
        if bank.is_dir()
    } == {
        "layers-06-11": FINAL_PIANO_BANK_BYTES,
        "layers-07-12": FINAL_PIANO_BANK_BYTES,
        "layers-08-13": FINAL_PIANO_BANK_BYTES,
    }
    assert (piano_root / "LICENSE-SALAMANDER.txt").is_file()
    assert (piano_root / "PROVENANCE-SALAMANDER.json").is_file()
    assert (piano_root / "PROVENANCE-SALAMANDER.md").is_file()
    assert (ROOT / "THIRD_PARTY_NOTICES.md").is_file()
    assert not any(
        (piano_root / name).exists()
        for name in ("archive", "source-assets", "audition")
    )
