"""Service-owned candidates, fresh source binding, independent saved drafts."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.music_agent import resource_tools
from ai_piano_studio.music_agent.resource_tools import MusicResourceSession
from ai_piano_studio.music_agent.source_confirmation import requested_excerpt_seconds
from ai_piano_studio.persistence.project_json import load_project, save_project
from .cli_transcriber import transcribe
from .contracts import LabCandidate, LabDraft, LabFailure, duration, transcription_project
from .discovery import discover_source


class ScoreLabService:
    def __init__(self, home: Path, model_id='gpt-6.1-sol', *, provider_factory=None, cli_runner=None, resource_factory=None):
        self.home = Path(home).resolve()
        self.model_id = model_id
        self.provider_factory, self.cli_runner, self.resource_factory = provider_factory, cli_runner, resource_factory
        self._candidate = None
        self._inspector = None

    def discover(self, query, duration_seconds=15.0, *, rejected_urls=(), rejected_hashes=(), recovery_context=None, cancelled=lambda: False, progress=lambda event: None):
        self.invalidate_candidate()
        if not isinstance(query,str) or not 1 <= len(query.strip()) <= 2000:
            raise LabFailure('query','请输入 1–2000 字的歌曲、歌手或版本线索。')
        try:
            seconds = duration(requested_excerpt_seconds(query,duration_seconds))
        except ValueError:
            raise LabFailure('duration','实验片段时长须为 5–30 秒。') from None
        if len(rejected_urls) > 192:
            raise LabFailure('rejections','本次排除来源过多，请重新开始查找。')
        progress(dict(stage='searching',message='正在独立查找公开谱源…'))
        from .source_inspection import SourceInspector
        self._inspector = SourceInspector(self.home,self.model_id,runner=self.cli_runner,cancelled=cancelled,progress=progress)
        try:
            proposal = discover_source(self.home,query,self.model_id,rejected_urls,provider_factory=self.provider_factory,
            resource_factory=self.resource_factory,cancelled=cancelled,progress=progress,
            recovery_context=recovery_context,rejected_hashes=rejected_hashes,duration_seconds=seconds,inspector=self._inspector)
        except Exception:
            self.invalidate_candidate()
            raise
        if cancelled():
            self.invalidate_candidate()
            raise LabFailure('cancelled','已取消谱源查找。')
        candidate = LabCandidate(uuid4().hex,query.strip(),proposal,seconds,self._inspector.selected)
        self._candidate = candidate
        return candidate

    def invalidate_candidate(self):
        self._candidate = None
        if self._inspector is not None:
            self._inspector.close()
            self._inspector = None

    def prepare(self, candidate, *, cancelled=lambda: False, progress=lambda event: None):
        # Object identity makes reconstructed/modified nonce-bearing candidates invalid.
        if candidate is not self._candidate or not isinstance(candidate,LabCandidate):
            raise LabFailure('stale_candidate','候选已失效，请重新查找并确认谱源。')
        self._candidate = None
        proposal = candidate.proposal
        session = None
        path = None
        image_dimensions = []
        phase = ['checking']
        report = progress
        def progress(event):
            if event.get('stage') != 'heartbeat':
                phase[0] = event.get('stage', phase[0])
            report(event)
        try:
            if cancelled():
                raise LabFailure('cancelled','已取消谱源准备。')
            seconds = duration(candidate.duration_seconds)
            session = (self.resource_factory or MusicResourceSession)(self.home,cancelled,lambda event: None)
            session.require_source_confirmation = True
            session.approved_source = proposal
            job = self.home/'score-lab'/uuid4().hex
            path = job/'project.json'
            input_strategy = 'parsed_resource'
            source_image_sha256 = None
            progress(dict(stage='checking',message='正在重新获取并核对已确认谱源指纹…'))
            result = session.execute('inspect_music_resource',dict(url=proposal.selected_url,
                title=proposal.title,source_url=proposal.selected_url))
            source = session.inspected_sources.get(proposal.selected_url)
            if source is None or not source.selectable_music or not proposal.matches(source.url,source.sha256):
                raise LabFailure('source_changed','谱源内容或地址已改变，请重新查找并确认。')
            if cancelled():
                raise LabFailure('cancelled','已取消谱源准备。')
            job.mkdir(parents=True,exist_ok=False)
            if result.data.get('status') == 'visual_score':
                if proposal.start_seconds != 0:
                    raise LabFailure('section','谱图实验仅支持可见页开头，请重新确认段落。')
                images = []
                if source.artifact_kind == 'score_image':
                    # Successful inspection/render above is the decode/safety
                    # precondition. Reacquire lossless bytes through the same
                    # bounded public transport and bind this second read too.
                    raw, final_url, _ = resource_tools.fetch_public_resource(proposal.selected_url,cancelled=cancelled)
                    if cancelled():
                        raise LabFailure('cancelled','已取消谱源准备。')
                    digest = hashlib.sha256(raw).hexdigest()
                    if not proposal.matches(final_url,digest):
                        raise LabFailure('source_changed','谱源内容或地址已改变，请重新查找并确认。')
                    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
                        suffix = '.png'
                    elif raw.startswith(b'\xff\xd8\xff'):
                        suffix = '.jpg'
                    else:
                        raise LabFailure('source_changed','已确认谱源不再提供有效的 PNG/JPEG 图像。')
                    if len(raw) > 8_000_000:
                        raise LabFailure('images','原始谱图超过实验大小限制。')
                    image = job/('score-original'+suffix)
                    image.write_bytes(raw)
                    images.append(image)
                    input_strategy = 'original_source_image'
                    source_image_sha256 = digest
                elif source.artifact_kind == 'score_pdf':
                    input_strategy = 'rendered_pdf_pages'
                    for i, raw in enumerate(result.data.get('_provider_images',())):
                        image = job/f'score-{i}.png'
                        image.write_bytes(raw)
                        images.append(image)
                else:
                    raise LabFailure('source_unreadable','检查结果未提供有效谱面类型。')
                from PySide6.QtGui import QImageReader
                for image in images:
                    size = QImageReader(str(image)).size()
                    if size.isValid():
                        image_dimensions.append([size.width(), size.height()])
                data = transcribe(images,job,self.model_id,seconds,runner=self.cli_runner,cancelled=cancelled,progress=progress)
                progress(dict(stage='validating',message='正在校验音高、节奏与实际谱面覆盖…'))
                state, warnings = transcription_project(data,seconds)
                (job/'transcription.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
            elif result.data.get('status') == 'parsed':
                parsed = session.resources[result.data['resource_id']]
                if not proposal.matches(parsed.provenance['download_url'],parsed.provenance['sha256']):
                    raise LabFailure('source_changed','解析音乐与确认谱源不一致。')
                state = self._parsed_project(parsed.midi,proposal.start_seconds,seconds)
                warnings = ('解析谱源的编配与录音一致性尚未验收；声部按原始轨道保留。', *parsed.midi.warnings)
                if state.estimated_duration < seconds - .05:
                    warnings += ('谱源实际片段短于所选时长，未补写或拉长尾音。',)
            else:
                raise LabFailure('source_unreadable','该来源未提供可读音乐或谱面，请更换来源。')
            if cancelled():
                raise LabFailure('cancelled','已取消谱源准备。')
            warnings = tuple(filter(None,(proposal.uncertainty,*warnings,'实验转谱结果，需核对实际谱面及试听。')))
            progress(dict(stage='validating',message='正在保存并重载独立工程…'))
            save_project(path,state)
            reloaded = load_project(path)
            if (state.notes != reloaded.notes or state.tempo != reloaded.tempo or state.layers != reloaded.layers
                    or state.controls != reloaded.controls):
                raise LabFailure('reload','工程重载验证不一致，未生成可试听草稿。')
            (job/'provenance.json').write_text(json.dumps(dict(candidate=candidate.public(),modelId=self.model_id,
                warnings=warnings,inputStrategy=input_strategy,sourceImageSha256=source_image_sha256),
                ensure_ascii=False,allow_nan=False),encoding='utf-8')
            if cancelled():
                raise LabFailure('cancelled','已取消谱源准备。')
            return LabDraft(path,reloaded,candidate,warnings,self.model_id)
        except LabFailure as exc:
            self.invalidate_candidate()
            exc.details.update(stage=phase[0],sourceUrl=proposal.selected_url,sourceSha256=proposal.selected_sha256,
                               imageDimensions=image_dimensions,title=proposal.title,artist=proposal.artist,edition=proposal.edition,section=proposal.section)
            if path is not None:
                path.unlink(missing_ok=True)
            raise
        except Exception:
            self.invalidate_candidate()
            if path is not None:
                path.unlink(missing_ok=True)
            if cancelled():
                raise LabFailure('cancelled','已取消谱源准备。') from None
            raise LabFailure('prepare_failed','谱源解析或工程校验失败，未生成草稿。') from None
        finally:
            if session is not None:
                session.close()

    @staticmethod
    def _parsed_project(midi,start,seconds):
        bpm = midi.bpm
        if int(bpm) != bpm:
            raise LabFailure('tempo','该谱源速度无法由当前工程无损表示。')
        if set(midi.warnings) & {'multi_tempo','multi_time_signature','tempo_clamped','unsupported_time_signature'}:
            raise LabFailure('tempo','该谱源含速度或拍号变化，当前实验无法忠实转换。')
        tempo = TempoMap(int(bpm),midi.beats_per_bar,midi.beat_unit)
        scale = tempo.seconds_per_beat / midi.ppq
        tracks = sorted({n.track for n in midi.notes} | {e.track for e in midi.controls})
        if len(midi.notes) > 100_000 or not 1 <= len(tracks) <= 128:
            raise LabFailure('note_limit','解析谱源规模超出实验限制。')
        layers = tuple(MusicLayer.create(id=f'lab-track-{track}',name=f'原始声部 {track+1}',order=i)
                       for i,track in enumerate(tracks))
        notes = []
        # Clip in source ticks before conversion, preserving the half-open
        # endpoint and real notes that begin before and cross the boundary.
        first_tick = start * midi.ppq * tempo.bpm / 60
        last_tick = (start+seconds) * midi.ppq * tempo.bpm / 60
        for row in midi.notes:
            begin, end = max(first_tick,row.start_tick), min(last_tick,row.end_tick)
            if begin < end:
                at = (begin-first_tick)*scale
                length = min(seconds-at,(end-begin)*scale)
                notes.append(Note.create(pitch=row.pitch,start_time=at,duration=length,
                    velocity=row.velocity,layer_id=f'lab-track-{row.track}',track=row.track, channel=row.channel,
                    on_order=row.on_order,off_order=row.off_order))
        if not 1 <= len(notes) <= 4096:
            raise LabFailure('note_limit','所选片段为空或音符过多，请调整段落。')
        controls = []
        prior = {}
        for row in sorted(midi.controls,key=lambda e:(e.tick,e.track,e.order)):
            at = row.tick*scale
            if at < start:
                prior[row.channel] = row
            elif at <= start+seconds:
                controls.append(ControlEvent.create(time=at-start,value=row.value,channel=row.channel,
                    track=row.track,order=row.order,layer_id=f'lab-track-{row.track}'))
        for row in prior.values():
            controls.append(ControlEvent.create(time=0,value=row.value,channel=row.channel,
                track=row.track,order=row.order,layer_id=f'lab-track-{row.track}'))
        return ProjectState(notes=tuple(notes),layers=layers,tempo=tempo,controls=tuple(controls))
