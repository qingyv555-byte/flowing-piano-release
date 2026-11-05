"""Independent score experiment data and strict, finite musical validation."""
from dataclasses import dataclass
import math
from pathlib import Path

from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.music_agent.source_confirmation import SourceProposal


def public_text(value, limit=400):
    import re
    text = str(value)
    # Consume the entire header value before the generic single-value matcher:
    # Authorization: Bearer <credential> contains an auth scheme plus a value.
    text = re.sub(r"(?i)\bauthorization\s*[:=]\s*[^\r\n,;]+", "Authorization=[redacted]", text)
    text = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token|password|cookie)\s*[:= ]\s*[^\s,;]+", r"\1=[redacted]", text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[:limit]


def public_url(value):
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    try:
        p = urlsplit(str(value))
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return ''
        query = urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                           if not any(word in k.lower() for word in ('token','key','auth','password','cookie','signature'))])
        return urlunsplit((p.scheme, p.netloc, p.path, query, ''))[:2048]
    except ValueError:
        return ''


class LabFailure(RuntimeError):
    def __init__(self, code, message, **details):
        self.code = public_text(code, 64)
        self.message = public_text(message)
        self.details = details
        super().__init__(self.message)

    def public(self):
        result = dict(code=self.code, message=self.message)
        for key in ('stage', 'sourceSha256', 'title', 'artist', 'edition', 'section'):
            if key in self.details:
                result[key] = public_text(self.details[key], 64)
        if 'sourceUrl' in self.details:
            result['sourceUrl'] = public_url(self.details['sourceUrl'])
        if 'imageDimensions' in self.details:
            result['imageDimensions'] = [list(row) for row in self.details['imageDimensions'][:4]
                                         if len(row) == 2 and all(type(n) is int and 0 < n <= 100000 for n in row)]
        return result


def duration(value):
    # Compare the small supported bounds before converting potentially huge ints.
    if type(value) not in (int, float) or not 5 <= value <= 30 or not math.isfinite(value):
        raise LabFailure('duration', '实验片段时长须为 5–30 秒。')
    return float(value)


@dataclass(frozen=True)
class LabCandidate:
    id: str
    query: str
    proposal: SourceProposal
    duration_seconds: float
    inspection: object = None

    def public(self):
        return dict(id=self.id, query=self.query, durationSeconds=self.duration_seconds,
                    sourceSha256=self.proposal.selected_sha256,
                    inspection=self.inspection.public() if self.inspection else None, **self.proposal.public())


@dataclass(frozen=True)
class LabDraft:
    project_path: Path
    state: ProjectState
    candidate: LabCandidate
    warnings: tuple[str, ...]
    model_id: str

    def public(self):
        return dict(projectPath=str(self.project_path), candidate=self.candidate.public(),
                    warnings=list(self.warnings), modelId=self.model_id,
                    durationSeconds=self.state.estimated_duration, noteCount=len(self.state.notes),
                    bpm=self.state.tempo.bpm, hands=[layer.name for layer in self.state.layers])


def _obj(properties):
    return dict(type='object', additionalProperties=False, properties=properties, required=list(properties))


_TIMING = dict(hand=dict(type='string', enum=['right', 'left']),
               start_beat=dict(type='number', minimum=0, maximum=300),
               duration_beats=dict(type='number', exclusiveMinimum=0, maximum=300))
MIN_VISUAL_NOTE_BEATS = 1 / 64
_NOTE_TIMING = dict(_TIMING,
    # Raw performed notes have a floor; crop tails and rests stay separate.
    duration_beats=dict(type='number', minimum=MIN_VISUAL_NOTE_BEATS, maximum=300))
TRANSCRIPTION_SCHEMA = _obj(dict(
    can_read=dict(type='boolean'), bpm=dict(type='integer', minimum=60, maximum=300),
    beats_per_bar=dict(type='integer'), beat_unit=dict(type='integer'),
    tonic=dict(type='string', enum=['C','C#','Db','D','D#','Eb','E','F','F#','Gb','G','G#','Ab','A','A#','Bb','B']),
    covered_beats=dict(type='number', exclusiveMinimum=0, maximum=300),
    uncertainty=dict(type='array', maxItems=12, items=dict(type='string', maxLength=400)),
    notes=dict(type='array', maxItems=4096, items=_obj(dict(**_NOTE_TIMING,
        pitch=dict(type=['integer','null'], minimum=0, maximum=127),
        degree=dict(type=['integer','null'], minimum=1, maximum=7),
        octave=dict(type='integer', minimum=-5, maximum=5),
        accidental=dict(type='integer', minimum=-2, maximum=2)))),
    rests=dict(type='array', maxItems=2048, items=_obj(_TIMING))))


def _schema(value, schema):
    """Small strict validator for this fixed schema, including JSON NaN rejection."""
    kind = schema['type']
    kinds = kind if isinstance(kind, list) else [kind]
    actual = ('null' if value is None else 'boolean' if type(value) is bool else
              'integer' if type(value) is int else 'number' if type(value) is float else
              'string' if type(value) is str else 'array' if type(value) is list else
              'object' if type(value) is dict else 'unknown')
    if actual not in kinds and not (actual == 'integer' and 'number' in kinds):
        raise ValueError('type')
    if value is None:
        return
    if actual in ('integer', 'number'):
        if not math.isfinite(value):
            raise ValueError('finite')
        for key, invalid in [('minimum', lambda x: value < x), ('maximum', lambda x: value > x),
                             ('exclusiveMinimum', lambda x: value <= x)]:
            if key in schema and invalid(schema[key]):
                raise ValueError('bounds')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('enum')
    if actual == 'string' and len(value) > schema.get('maxLength', 400):
        raise ValueError('text limit')
    if actual == 'string' and len(value) < schema.get('minLength', 0):
        raise ValueError('empty text')
    if actual == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema['maxItems']:
            raise ValueError('array limit')
        for row in value:
            _schema(row, schema['items'])
    if actual == 'object':
        properties = schema['properties']
        if not set(schema.get('required', ())).issubset(value):
            raise ValueError('fields')
        if schema.get('additionalProperties', True) is False and not set(value).issubset(properties):
            raise ValueError('fields')
        for key, row in value.items():
            if key in properties:
                _schema(row, properties[key])


def validate_transcription(data):
    if isinstance(data, dict) and data.get('can_read') is False:
        reasons = data.get('uncertainty', [])
        reason = '；'.join(public_text(row, 180) for row in reasons[:3] if isinstance(row, str)) if isinstance(reasons, list) else ''
        raise LabFailure('unreadable', '本次谱面不可读；' + (reason or '请更换清晰谱源后重试。'), stage='transcribing')
    try:
        _schema(data, TRANSCRIPTION_SCHEMA)
    except (ValueError, TypeError, OverflowError):
        raise LabFailure('format', '识谱结构不符合有界数据格式。') from None
    return data


def transcription_project(data, seconds):
    seconds = duration(seconds)
    validate_transcription(data)
    try:
        tempo = TempoMap(data['bpm'], data['beats_per_bar'], data['beat_unit'])
        if not data['notes']:
            raise ValueError('empty')
        covered = data['covered_beats']
        layers = tuple(MusicLayer.create(id='lab-'+hand, name=name, role=role, order=i)
                       for i, (hand, name, role) in enumerate([
                           ('right','右手','melody'), ('left','左手','accompaniment')]))
        notes = []
        intervals = dict(right=[], left=[])
        pc = {'C':0,'C#':1,'Db':1,'D':2,'D#':3,'Eb':3,'E':4,'F':5,'F#':6,'Gb':6,
              'G':7,'G#':8,'Ab':8,'A':9,'A#':10,'Bb':10,'B':11}[data['tonic']]
        for row in [*data['notes'], *data['rests']]:
            start, end = row['start_beat'], row['start_beat'] + row['duration_beats']
            if end > covered + 1e-6:
                raise ValueError('outside coverage')
            intervals[row['hand']].append((start,end))
        for rows in intervals.values():
            cursor = 0
            for start, end in sorted(rows):
                if start > cursor + 1e-5:
                    raise ValueError('unexplained gap: encode rests')
                cursor = max(cursor,end)
            if cursor < covered - 1e-5:
                raise ValueError('incomplete hand coverage')
        for row in data['notes']:
            if (row['pitch'] is None) == (row['degree'] is None):
                raise ValueError('choose one pitch representation')
            pitch = row['pitch']
            if pitch is None:
                pitch = 60 + pc + (0,2,4,5,7,9,11)[row['degree']-1] + 12*row['octave'] + row['accidental']
            elif row['octave'] or row['accidental']:
                raise ValueError('MIDI pitch already includes octave/accidental')
            # Validate pitches even when they fall after the requested crop.
            if not 0 <= pitch <= 127:
                raise ValueError('pitch')
            # Keep the half-open crop decision in source beats. At BPM110,
            # 27.5 beats is exactly 15s but beats_to_seconds rounds just below it.
            cutoff_beat = seconds * tempo.bpm / 60
            if row['start_beat'] >= cutoff_beat:
                continue
            start = tempo.beats_to_seconds(row['start_beat'])
            end_beat = min(cutoff_beat,row['start_beat']+row['duration_beats'])
            end = min(seconds, tempo.beats_to_seconds(end_beat))
            if start < end:
                notes.append(Note.create(pitch=pitch, start_time=start, duration=end-start, layer_id='lab-'+row['hand']))
        if not notes:
            raise ValueError('no notes in excerpt')
        state = ProjectState(notes=tuple(notes), layers=layers, tempo=tempo)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise LabFailure('music_contract', '谱面音高、节奏、拍号或双手覆盖不完整；请核对来源。') from None
    warnings = list(data['uncertainty'])
    if tempo.beats_to_seconds(covered) < seconds - .05:
        warnings.append('可见谱面覆盖不足所选时长；已保留实际片段，未补写音符或拉长尾音。')
    return state, tuple(warnings)
