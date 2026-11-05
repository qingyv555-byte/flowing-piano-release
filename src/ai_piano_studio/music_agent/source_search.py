"""Bounded discovery results; web metadata is never proof of song identity."""
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit, quote
from urllib.request import Request, build_opener

from ai_piano_studio.music_agent.song_resolver import (
    MAX_SOURCE_BYTES, ResolvedMusic, SourceCancelled, _NoRedirect,
    normalize_title, validate_midi,
)


@dataclass(frozen=True)
class MusicSourceCandidate:
    title: str
    source_url: str
    midi_url: str = ""
    detail: str = ""


@dataclass(frozen=True)
class MusicSourceSearchResult:
    summary: str
    sources: tuple[MusicSourceCandidate, ...] = ()


def public_https_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError("invalid source URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError("source URL must be credential-free HTTPS")
    return value


def parse_search_result(data: object) -> MusicSourceSearchResult:
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise ValueError("invalid source search result")
    rows = data.get("sources")
    if not isinstance(rows, list) or len(rows) > 5:
        raise ValueError("invalid source candidates")
    sources = []
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str)
                for k in ("title", "source_url", "midi_url", "detail")):
            raise ValueError("invalid source candidate")
        if not row["title"].strip() or len(row["title"]) > 160 or len(row["detail"]) > 500:
            raise ValueError("invalid source description")
        sources.append(MusicSourceCandidate(row["title"], public_https_url(row["source_url"]),
            public_https_url(row["midi_url"]) if row["midi_url"] else "", row["detail"]))
    return MusicSourceSearchResult(data["summary"][:1000], tuple(sources))


def downloadable_midi_url(candidate: MusicSourceCandidate, request) -> str:
    """Only reviewed static hosts/buckets; no model-chosen servers/redirects."""
    parsed = urlsplit(public_https_url(candidate.midi_url))
    path = unquote(parsed.path)
    host = "raw.githubusercontent.com"
    if parsed.hostname == "github.com":
        parts = path.split("/")
        if len(parts) < 6 or parts[3] != "blob":
            raise ValueError("not a GitHub file")
        path = "/".join(parts[:3] + parts[4:])
    elif (parsed.hostname == "s3-ap-southeast-1.amazonaws.com"
          and path.startswith("/music.bensonby.me/")
          and urlsplit(public_https_url(candidate.source_url)).hostname == "music.bensonby.me"):
        # Author's score page links to this exact public bucket. Never allow
        # other tenants of the same shared S3 host.
        host = parsed.hostname
    elif parsed.hostname != "raw.githubusercontent.com":
        raise ValueError("source needs manual import")
    filename = PurePosixPath(path).name
    # A model's metadata cannot relabel an unrelated file as the requested song.
    if (PurePosixPath(filename).suffix.lower() not in {".mid", ".midi"}
            or normalize_title(request.title) not in normalize_title(filename)
            or any(part in {".", ".."} for part in path.split("/"))):
        raise ValueError("source filename does not match the requested song")
    return urlunsplit(("https", host, quote(path, safe="/()@-._~"), "", ""))


def fetch_midi(url: str, cancelled) -> bytes:
    chunks, size = [], 0
    with build_opener(_NoRedirect()).open(Request(url, headers={
            "User-Agent": "Flowing-Piano/0.1 (user-requested score lookup)"}), timeout=15) as response:
        while True:
            if cancelled():
                raise SourceCancelled()
            chunk = response.read(64_000)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > MAX_SOURCE_BYTES:
                raise ValueError("music source exceeds limit")
            chunks.append(chunk)


def resolve_search_candidate(candidate, request, *, cancelled=lambda: False, fetch=fetch_midi):
    url = downloadable_midi_url(candidate, request)
    if cancelled():
        raise SourceCancelled()
    raw = fetch(url, cancelled)
    parsed = validate_midi(raw)
    if cancelled():
        raise SourceCancelled()
    return ResolvedMusic(request.title + " · 网络 MIDI（待核对）", parsed, dict(
        kind="online_reference_midi", verifiedNotes=False, accuracy="online_edition_unverified",
        requestedTitle=request.title, requestedArtist=request.artist, source_url=candidate.source_url,
        download_url=url, sha256=hashlib.sha256(raw).hexdigest(), noteCount=len(parsed.notes),
        license="以来源页说明为准", attribution=candidate.title,
        qualityWarning="已解析网络 MIDI；曲名及编配来自第三方，未经原版录音比对，请先试听核对。"))
