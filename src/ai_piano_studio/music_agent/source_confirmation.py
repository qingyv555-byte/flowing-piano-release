"""Bounded source identity proposals built from inspected content, never approvals.

Only the controller may retain a proposal as approved after a trusted UI action.
A proposal contains no model-supplied authorization field or executable content.
"""
from dataclasses import dataclass
import math
import re
from ai_piano_studio.music_agent.source_search import public_https_url


@dataclass(frozen=True, slots=True)
class InspectedSource:
    url: str
    sha256: str
    text: str
    links: tuple[str, ...] = ()
    artifact_kind: str = ''
    is_html: bool = False

    @property
    def selectable_music(self):
        return self.artifact_kind in {'parsed_midi', 'parsed_musicxml', 'parsed_mml', 'score_pdf', 'score_image'}


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    url: str
    sha256: str
    quote: str


@dataclass(frozen=True, slots=True)
class SourceProposal:
    title: str
    artist: str
    edition: str
    section: str
    selected_url: str
    selected_sha256: str
    start_seconds: float
    evidence: tuple[SourceEvidence, ...]
    summary: str
    uncertainty: str

    @classmethod
    def from_payload(cls, payload, inspected):
        fields = {'title', 'artist', 'edition', 'section', 'selected_url', 'start_seconds',
                  'evidence', 'summary', 'uncertainty'}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError('Proposal fields do not match the source identity contract')
        def bounded(key, limit, empty=False):
            value = payload[key]
            if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
                raise ValueError('Invalid bounded source identity field: ' + key)
            return value.strip()
        title = bounded('title', 160)
        artist = bounded('artist', 160, True)
        edition, section = bounded('edition', 160), bounded('section', 160)
        url = public_https_url(bounded('selected_url', 2048))
        selected = inspected.get(url)
        if selected is None:
            raise ValueError('Selected source must have been fetched in this task')
        if not selected.selectable_music:
            raise ValueError('Select an actual inspected music artifact, not an identity page. Inspect a linked MIDI/MusicXML/MML or image/PDF score; keep the page in identity evidence.')
        url = selected.url
        start = payload['start_seconds']
        if type(start) not in (int, float) or not math.isfinite(start) or not 0 <= start <= 3600:
            raise ValueError('Invalid source section start')
        rows = payload['evidence']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
            raise ValueError('Supply one to six inspected identity excerpts')
        evidence = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'url', 'quote'}:
                raise ValueError('Invalid source evidence fields')
            evidence_url, quote = row['url'], row['quote']
            if not isinstance(evidence_url, str) or len(evidence_url) > 2048:
                raise ValueError('Invalid evidence URL')
            source = inspected.get(evidence_url)
            if (source is None or not isinstance(quote, str) or not 1 <= len(quote.strip()) <= 500
                    or quote.strip() not in source.text):
                raise ValueError('Identity quote must occur in actually inspected source content')
            evidence.append(SourceEvidence(source.url, source.sha256, quote.strip()))
        if not any(e.url == url or any(link == url or (inspected.get(link) is not None and inspected[link].url == url)
                                      for link in inspected[e.url].links) for e in evidence):
            raise ValueError('Selected source must link from an inspected identity evidence page')
        summary = bounded('summary', 800)
        uncertainty = bounded('uncertainty', 800, True)
        # Multiple excerpts/URLs need not be independent. Never assert independence.
        if len({item.sha256 for item in evidence}) < 2:
            uncertainty = (uncertainty[:650] + ' 尚未取得两份独立身份依据；来源编配及段落仍需用户核对。').strip()
        return cls(title, artist, edition, section, url, selected.sha256, float(start),
                   tuple(evidence), summary, uncertainty)

    def matches(self, url, sha256):
        return self.selected_url == url and self.selected_sha256 == sha256

    def public(self):
        return dict(title=self.title, artist=self.artist, edition=self.edition, section=self.section,
                    selectedUrl=self.selected_url, startSeconds=self.start_seconds,
                    evidence=[dict(url=e.url, quote=e.quote) for e in self.evidence],
                    summary=self.summary, uncertainty=self.uncertainty)


_EXCERPT_DURATION_RE = re.compile(
    r'(?<![a-zA-Z\d.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[+-]?(?:inf(?:inity)?|nan))\s*(?:秒|seconds?|s)(?![a-zA-Z])', re.I)
_DURATION_ERROR = '试听时长必须大于 0 秒且不超过 600 秒，请调整时长后重试。'


def normalize_excerpt_seconds(value: float) -> float:
    """Validate the effective excerpt duration without clipping user intent."""
    if type(value) not in (int, float):
        raise ValueError(_DURATION_ERROR)
    try:
        seconds = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(_DURATION_ERROR) from error
    if not math.isfinite(seconds) or not 0 < seconds <= 600:
        raise ValueError(_DURATION_ERROR)
    return seconds


def _duration_directives(instruction: str):
    """Yield duration tokens outside opaque names/URLs, not arbitrary source data."""
    protected = [match.span() for match in re.finditer(
        r'https?://[^\s<>"\'，。；！？]+|《[^》]*》|“[^”]*”|「[^」]*」|"[^"\n]*"', instruction, re.I)]
    for match in _EXCERPT_DURATION_RE.finditer(instruction):
        if any(start <= match.start() < end for start, end in protected):
            continue
        # An English name continues after its duration-shaped token, as in
        # "30 Seconds to Mars". A duration directive has an explicit cue or
        # ends its clause; source names are never rewritten to satisfy parsers.
        following = re.match(r'\s+([A-Za-z]+)', instruction[match.end():])
        preceding = re.split(r'[，,。；;\n]', instruction[:match.start()])[-1]
        if (following and following.group(1).lower() not in {'please', 'long', 'of', 'excerpt', 'clip', 'preview'}
                and not re.search(r'(?:试听|时长(?:为|是)?|截取|改为|改成|延长到|缩短到|duration(?:\s*(?:of|is|:))?|\bfor|excerpt|preview|length(?:\s*(?:of|is|:))?)\s*$', preceding, re.I)):
            continue
        yield match


def requested_excerpt_seconds(instruction: str, default: float = 15.0, *, unambiguous_only=False) -> float:
    """The latest explicit duration wins; unsupported values never become defaults."""
    if unambiguous_only:
        # Supplements are separate user messages. Multiple historical values
        # must not make an explicit newer correction look ambiguous.
        messages = re.split(r'\n\n用户补充：|\n用户纠正：|\n当前任务要求：', instruction)
        for message in reversed(messages):
            matches = list(_duration_directives(message))
            if matches:
                values = {float(match.group(1)) for match in matches}
                return normalize_excerpt_seconds(
                    values.pop() if len(values) == 1 else default)
        return normalize_excerpt_seconds(default)
    matches = list(_duration_directives(instruction))
    return normalize_excerpt_seconds(float(matches[-1].group(1)) if matches else default)


def normalize_excerpt_instruction(instruction: str) -> str:
    """Keep legacy parsers consistent with the latest trusted duration correction."""
    from decimal import Decimal
    seconds = requested_excerpt_seconds(instruction)
    canonical = format(Decimal(str(seconds)), 'f')
    # Compatibility utility only. Production retains the original user text.
    result = instruction
    for match in reversed(list(_duration_directives(instruction))):
        result = result[:match.start()] + canonical + '秒' + result[match.end():]
    return result
