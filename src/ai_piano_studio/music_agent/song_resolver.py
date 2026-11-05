"""App-owned, licensed music resolution independent of model web support.

A metadata/name match never becomes a successful resolution without parsed
notes. Network access is limited to catalog-authored HTTPS URLs, not model URLs.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import URLError
from zipfile import ZipFile, BadZipFile

from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.persistence.atomic import atomic_write_bytes
from ai_piano_studio.persistence.midi import MidiImportResult, parse_midi


CATALOG_HOME = Path(__file__).resolve().parents[1] / "resources/music_catalog"
MAX_SOURCE_BYTES = 2_000_000
MAX_SOURCE_NOTES = 30_000
DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|seconds?|s)(?![a-zA-Z])", re.I)


class SourceCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MusicRequest:
    instruction: str
    title: str
    named: bool
    artist: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedMusic:
    title: str
    midi: MidiImportResult
    provenance: dict[str, object]


class MusicSourceProvider(Protocol):
    def resolve(self, request: MusicRequest, cancelled: Callable[[], bool],
                progress: Callable[[ProviderProgressEvent], None]) -> ResolvedMusic | None: ...


def normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.casefold())


def _trailing_song_attribution(text: str):
    return re.fullmatch(
        r'(?:请|麻烦|帮我|给我)*\s*(?:写|来|弹|演奏|播放|找)\s*(?:一(?:段|首|曲))?\s*'
        r'(?:\d+(?:\.\d+)?\s*(?:秒|seconds?|s)\s*的?\s*)?'
        r'([《“"]?[^，,。！？!?\n]{2,60})[，,]\s*([\w· ]{2,20})\s*的(?:歌曲|歌|作品)[。！!\s]*',
        text.strip(), re.I,
    )


def corrected_song_title(text: str) -> str:
    match = re.fullmatch(
        r'[《“"]?([^》”"，,。！？!?\n]{2,30})[》”"]?[，,]\s*'
        r'(?:你)?(?:是不是|是否|可能)?(?:搜错|找错|认错)(?:了)?(?:歌名|歌曲|歌|曲子)?[^，,\n]*',
        text.strip(),
    )
    return match.group(1).strip() if match else ''


def music_request(instruction: str) -> MusicRequest:
    quoted = re.search(r"[《“\"]([^》”\"]{1,100})[》”\"]", instruction)
    title = quoted.group(1) if quoted else instruction.strip()
    artist = ""
    for known in ("月光奏鸣曲", "青花瓷", "七里香", "夜曲", "月光"):
        if known in instruction:
            title = known
            break
    if "周杰伦" in instruction:
        artist = "周杰伦"
    elif "月光奏鸣曲" in instruction or "贝多芬" in instruction:
        artist = "Ludwig van Beethoven"
    # Natural artist/title requests must not depend on the catalogue's artists.
    action = re.match(
        r"^(?:(?:请|麻烦|帮我)\s*)?(?:给我\s*)?"
        r"(?:来|弹|演奏|播放|放|点|我想听|我想要|我要|想听)\s*"
        r"(?:一?\s*(?:首|段|曲)|点)?\s*(.+)", instruction.strip())
    action_body = DURATION_RE.sub('', action.group(1)).lstrip('的 ') if action else ''
    natural_artist_title = re.match(r"([^的，,。！？!?\n]{1,24})的\s*([^，,。！？!?\n]{1,100})", action_body)
    if natural_artist_title and not re.search(
        r"风格|感觉|温柔|悲伤|忧伤|安静|舒缓|轻快|原创|即兴|类似|一点|钢琴|音乐|伴奏|适合|按照|慢|快",
        natural_artist_title.group(1),
    ):
        artist = natural_artist_title.group(1).strip()
        if not quoted and title == instruction.strip():
            title = re.split(r"(?:用钢琴|钢琴版|弹一下|演奏)", natural_artist_title.group(2))[0].strip() or natural_artist_title.group(2).strip()
    else:
        natural_artist_title = None
    artist_title = re.search(r"周杰伦(?:的)?\s*([^，,。！？!?\n]{2,40})", instruction)
    if not quoted and title == instruction.strip() and artist_title:
        candidate = re.split(r"(?:用钢琴|钢琴版|弹一下|演奏)", artist_title.group(1))[0].strip()
        if candidate and candidate not in {'的歌', '歌曲', '的歌曲', '作品', '的作品'} and not any(word in candidate for word in ("风格", "原创", "感觉", "类型")):
            title = candidate
    attribution = _trailing_song_attribution(instruction)
    if attribution:
        title = attribution.group(1).strip(' 《》“”"')
        artist = attribution.group(2).strip()
    correction = corrected_song_title(instruction)
    if correction:
        title = correction
    named = bool(quoted or re.search(r"奏鸣曲|青花瓷|七里香|夜曲|(?:秦时明月|胡彦斌).{0,12}月光|月光(?=\s*(?:[，,。]|$|\d))|原曲|原版(?=.{0,8}(?:旋律|歌曲|音乐|曲|钢琴|伴奏))|moonlight|sonata|(?:弹|演奏|点歌).{1,60}|\b(?:play|perform)\b", instruction, re.I))
    named |= bool(artist_title and title != instruction.strip())
    named |= bool(natural_artist_title or attribution or correction)
    positive = re.sub(r"(?:不要|不接受|不准|禁止)\s*(?:原创|创作|替代|创意演绎)", "", instruction)
    original = re.search(r"(?:创作|写|生成|compose).{0,30}(?:原创(?!替代)|original)|(?:原创(?!替代)|original).{0,16}(?:钢琴|曲|音乐|piano|piece)|\bcompose\b", positive, re.I)
    if original:
        named = False
    return MusicRequest(instruction, title[:160], named, artist)


def requests_named_song(instruction: str) -> bool:
    """Recognize a requested song without relying on an artist catalogue."""
    text = instruction.strip()
    if re.search(r"介绍|推荐|歌词|链接|下载|了解", text):
        return False
    if _trailing_song_attribution(text) or corrected_song_title(text):
        return music_request(text).named
    return bool(re.match(
        r"^(?:(?:请|帮我|麻烦)\s*)?(?:给我\s*)?"
        r"(?:来|弹|演奏|播放|放|点|我想听|我要|我想要)", text,
    ) and music_request(text).named)


def validate_midi(raw: bytes) -> MidiImportResult:
    if len(raw) > MAX_SOURCE_BYTES or not raw.startswith(b"MThd"):
        raise ValueError("invalid or oversized music source")
    parsed = parse_midi(raw)
    if not 1 <= len(parsed.notes) <= MAX_SOURCE_NOTES:
        raise ValueError("music source has no valid bounded note data")
    return parsed


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class CatalogSourceProvider:
    def __init__(self, cache_home: Path, *, catalog_home: Path = CATALOG_HOME,
                 fetch: Callable[[str, Callable[[], bool]], bytes] | None = None):
        self.cache_home = Path(cache_home)
        self.catalog_home = Path(catalog_home)
        self.entries = json.loads((self.catalog_home / "catalog.json").read_text(encoding="utf-8"))
        self._fetch = fetch or self._download

    def match(self, instruction: str) -> dict | None:
        normalized = normalize_title(instruction)
        return next((entry for entry in self.entries if any(
            normalize_title(alias) in normalized for alias in entry["aliases"])), None)

    def _download(self, url: str, cancelled: Callable[[], bool]) -> bytes:
        # Only exact, code-distributed URLs are allowed. No redirect, credentials,
        # login scraping or arbitrary model-supplied addresses.
        if url not in {e["download_url"] for e in self.entries} or not url.startswith("https://www.mutopiaproject.org/"):
            raise ValueError("music source is not in the licensed catalog")
        request = Request(url, headers={"User-Agent": "Flowing-Piano/0.1 (licensed music catalog)"})
        chunks, total = [], 0
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            while True:
                if cancelled():
                    raise SourceCancelled()
                chunk = response.read(64_000)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("music download exceeds limit")
                chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _checked(raw: bytes, entry: dict) -> MidiImportResult:
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("music source hash mismatch")
        return validate_midi(raw)

    def resolve(self, request, cancelled, progress):
        entry = self.match(request.instruction)
        if entry is None:
            return None
        if cancelled():
            raise SourceCancelled()
        progress(ProviderProgressEvent("parsing"))
        cached = self.cache_home / entry["file"]
        parsed, raw, location = None, b"", ""
        for path, label in ((cached, "cache"), (self.catalog_home / entry["file"], "bundled")):
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    continue
                candidate = path.read_bytes()
                parsed = self._checked(candidate, entry)
                raw, location = candidate, label
                break
            except (OSError, ValueError):
                continue
        if parsed is None:
            archive = self._fetch(entry["download_url"], cancelled)
            if len(archive) > MAX_SOURCE_BYTES:
                raise ValueError("oversized archive")
            try:
                with ZipFile(BytesIO(archive)) as zipped:
                    info = zipped.getinfo(entry["archive_member"])
                    if info.file_size > MAX_SOURCE_BYTES or info.is_dir():
                        raise ValueError("invalid music archive member")
                    raw = zipped.read(info)
                parsed = self._checked(raw, entry)
                location = "online"
            except (BadZipFile, KeyError) as error:
                raise ValueError("invalid music archive") from error
        if cancelled():
            raise SourceCancelled()
        self.cache_home.mkdir(parents=True, exist_ok=True)
        if location != "cache":
            atomic_write_bytes(cached, raw)
        provenance = {key: entry[key] for key in ("id", "composer", "source_url", "license", "license_url", "attribution", "sha256", "edition")}
        provenance.update(kind="licensed_score_midi", verifiedNotes=True, location=location,
                          noteCount=len(parsed.notes), accuracy="score_edition_not_recording")
        progress(ProviderProgressEvent("parsing", current=len(parsed.notes), total=len(parsed.notes)))
        return ResolvedMusic(entry["title"], parsed, provenance)


class LocalMusicSourceProvider:
    """Only the app's explicit MusicLibrary directory; never scan user disks."""
    def __init__(self, library_home: Path):
        self.home = Path(library_home)

    def resolve(self, request, cancelled, progress):
        if not self.home.is_dir():
            return None
        title = normalize_title(request.instruction)
        candidates = sorted(self.home.glob("*.mid"))[:128] + sorted(self.home.glob("*.midi"))[:128]
        for path in candidates:
            if cancelled():
                raise SourceCancelled()
            stem = normalize_title(path.stem)
            if len(stem) < 2 or stem not in title or path.resolve().parent != self.home.resolve():
                continue
            if path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            progress(ProviderProgressEvent("parsing"))
            raw = path.read_bytes()
            parsed = validate_midi(raw)
            return ResolvedMusic(path.stem, parsed, dict(kind="local_user_reference", verifiedNotes=True,
                accuracy="user_supplied_edition", noteCount=len(parsed.notes), sha256=hashlib.sha256(raw).hexdigest(),
                source_url="", license="用户本地资料", attribution="用户提供的本地音乐资料"))
        return None


class SongResolver:
    def __init__(self, home: Path, *, providers: Sequence[MusicSourceProvider] | None = None):
        self.catalog = CatalogSourceProvider(Path(home) / "assistant/music-cache")
        from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime
        runtime = IsolatedMusicRuntime(Path(home))
        self.providers = tuple(providers) if providers is not None else (
            LocalMusicSourceProvider(Path(home) / "MusicLibrary"), self.catalog,
            ConvertedLocalSourceProvider(Path(home) / "MusicLibrary", runtime, "score"),
            ConvertedLocalSourceProvider(Path(home) / "MusicLibrary", runtime, "audio"))

    def recognizes(self, instruction: str) -> bool:
        return self.catalog.match(instruction) is not None

    def resolve(self, instruction: str, *, cancelled=lambda: False, progress=lambda event: None) -> ResolvedMusic | None:
        request = music_request(instruction)
        progress(ProviderProgressEvent("understanding"))
        if not request.named:
            return None
        progress(ProviderProgressEvent("searching"))
        for provider in self.providers:
            if cancelled():
                raise SourceCancelled()
            try:
                result = provider.resolve(request, cancelled, progress)
            except SourceCancelled:
                raise
            except (OSError, ValueError, RuntimeError) as error:
                from ai_piano_studio.music_agent.transcription import MusicRuntimeUnavailable, MusicRuntimeFailed
                code = ("runtime_unavailable" if isinstance(error, MusicRuntimeUnavailable) else
                        "transcription_failed" if isinstance(error, MusicRuntimeFailed) else
                        "network_unavailable" if isinstance(error, (URLError, TimeoutError)) else "source_rejected")
                progress(ProviderProgressEvent("source_failed", detail_code=code, process_alive=False))
                continue
            if result is not None:
                return result
        return None


class ConvertedLocalSourceProvider:
    """Digital scores and authorized audio from the explicit local music library."""
    def __init__(self, home: Path, runtime, kind: str):
        self.home, self.runtime, self.kind = Path(home), runtime, kind

    def resolve(self, request, cancelled, progress):
        if not self.home.is_dir():
            return None
        extensions = {".musicxml", ".mxl", ".xml"} if self.kind == "score" else {".wav", ".flac"}
        title = normalize_title(request.instruction)
        for path in sorted(self.home.iterdir())[:256]:
            if cancelled():
                raise SourceCancelled()
            stem = normalize_title(path.stem)
            if self.kind == "score" and path.suffix.casefold() == ".pdf" and len(stem) >= 2 and stem in title:
                progress(ProviderProgressEvent("source_failed", detail_code="score_ocr_unavailable"))
                continue
            if (path.suffix.casefold() not in extensions or len(stem) < 2 or stem not in title
                    or not path.is_file() or path.resolve().parent != self.home.resolve()):
                continue
            parsed = self.runtime.convert(path, self.kind, cancelled=cancelled, progress=progress)
            audio = self.kind == "audio"
            label = "自动转谱（待核对）" if audio else "数字乐谱"
            return ResolvedMusic(path.stem + " · " + label, parsed, dict(
                kind="automatic_audio_transcription" if audio else "local_digital_score",
                verifiedNotes=not audio, noteCount=len(parsed.notes),
                accuracy="automatic_unverified" if audio else "user_supplied_score_edition",
                source_url="", license="用户本地资料", attribution="用户提供的本地音乐资料",
                converter="Spotify Basic Pitch 0.4 / ONNX CPU" if audio else "music21 / MusicXML",
                qualityWarning="自动识别可能漏音、错音；未分离人声及伴奏，需试听核对。" if audio else "以用户提供的数字乐谱版本为依据。"))
        return None
