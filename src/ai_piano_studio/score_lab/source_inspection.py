"""URL/hash-bound managed preview assets and advisory visual inspection."""
from dataclasses import dataclass, replace
import hashlib
from http.client import HTTPException
from pathlib import Path
import tempfile
import time
from ai_piano_studio.music_agent import resource_tools, score_reference
from ai_piano_studio.music_agent.song_resolver import SourceCancelled
from .contracts import LabFailure, public_text, public_url
from .visual_preflight import assess_readability


@dataclass(frozen=True)
class SourceInspection:
    source_url: str
    source_sha256: str
    status: str
    reason: str
    dimensions: tuple = ()
    page_count: int = 0
    preview_paths: tuple = ()
    # Actual inspected origin relationships, never supplied by the model.
    origin_bindings: tuple = ()
    image_families: tuple = ()

    def public(self):
        return dict(sourceUrl=public_url(self.source_url),sourceSha256=self.source_sha256,
            status=self.status,readabilityReason=public_text(self.reason),
            dimensions=[list(d) for d in self.dimensions],pageCount=self.page_count,
            previewUrls=[p.as_uri() for p in self.preview_paths if p.is_file() and not p.is_symlink()],
            accuracyVerified=False)

    def same_version_evidence(self, other):
        # Exact bytes or an explicit same-img variant family supplies private
        # resource evidence. Approval identity/scope remains the caller's gate.
        # Merely sharing a page with several arrangements is insufficient.
        return bool(isinstance(other,SourceInspection) and self.status=='readable' and
            other.status=='readable' and (self.source_sha256==other.source_sha256 or
                bool(set(self.image_families) & set(other.image_families))))


class SourceInspector:
    def __init__(self,home,model_id,*,runner=None,cancelled=lambda:False,progress=lambda event:None,deadline=None):
        self.home=Path(home); self.model_id=model_id; self.runner=runner
        self.cancelled=cancelled; self.progress=progress
        self.deadline=deadline if deadline is not None else time.monotonic()+240
        self._temporary=None; self.cache={}; self.probes=0; self.selected=None; self._page_families={}

    def close(self):
        if self._temporary is not None: self._temporary.cleanup(); self._temporary=None
        self.cache.clear(); self._page_families.clear(); self.selected=None

    def _remaining(self):
        if self.cancelled(): raise LabFailure('cancelled','已取消谱面检查。')
        remaining=self.deadline-time.monotonic()
        if remaining<=0: raise LabFailure('timeout','谱面检查超时。')
        return remaining

    def inspect(self,source,origins=()):
        self._remaining()
        digest=source.sha256
        bindings=tuple(dict.fromkeys((p.url,p.sha256,source.url) for p in origins if source.url in p.links))
        families=[]
        # Re-fetch only actually inspected linked origin pages, binding their
        # exact SHA before interpreting a specific img family.
        for origin_url,origin_sha,_ in bindings[:2]:
            if origin_url==source.url: continue
            key=(origin_url,origin_sha)
            if key not in self._page_families:
                try:
                    page,page_url,mime=resource_tools.fetch_public_resource(origin_url,cancelled=lambda:self.cancelled() or time.monotonic()>=self.deadline)
                    self._remaining()
                    valid=page_url==origin_url and hashlib.sha256(page).hexdigest()==origin_sha and len(page)<=2_000_000
                    self._page_families[key]=_image_families(page.decode('utf-8',errors='replace'),origin_url) if valid else ()
                except (ValueError,OSError,HTTPException,SourceCancelled):
                    self._remaining()
                    self._page_families[key]=()
            for family in self._page_families[key]:
                if source.url in family:
                    families.append((origin_url,origin_sha,tuple(sorted(family))))
        if digest in self.cache:
            return replace(self.cache[digest],source_url=source.url,origin_bindings=bindings,image_families=tuple(families))
        def result(status,reason,dimensions=(),page_count=0,preview_paths=()):
            value=SourceInspection(source.url,digest,status,reason,dimensions,page_count,preview_paths,bindings,tuple(families))
            self.cache[digest]=value
            return value
        try:
            raw,url,_=resource_tools.fetch_public_resource(source.url,cancelled=lambda:self.cancelled() or time.monotonic()>=self.deadline)
            self._remaining()
            if url!=source.url or hashlib.sha256(raw).hexdigest()!=digest:
                return result('unavailable','谱源地址或 SHA256 已改变，需重新检查。')
            if len(raw)>8_000_000: return result('unavailable','谱源超过8MB限制。')
            image=source.artifact_kind=='score_image'
            if image:
                from PySide6.QtCore import QBuffer,QByteArray,QIODevice
                from PySide6.QtGui import QImageReader
                buffer=QBuffer(); buffer.setData(QByteArray(raw)); buffer.open(QIODevice.ReadOnly)
                reader=QImageReader(buffer); size=reader.size()
                if not size.isValid() or size.width()*size.height()>40_000_000:
                    return result('unavailable','图像尺寸无效或超过4000万像素限制。')
                dimensions=((size.width(),size.height()),)
                if max(dimensions[0])<512:
                    return result('unreadable',f'原图仅 {size.width()}×{size.height()} 像素；请查找高清原图或其他谱源。',dimensions)
            else: dimensions=()
            if self.probes>=2: return result('unavailable','本次最多检查两份不同内容的谱面；请采用可解析谱源或重新查找。',dimensions)
            # Includes bounded rendering in each <=60 second visual-probe budget.
            self.probes+=1; probe_deadline=min(self.deadline,time.monotonic()+60)
            renderer=score_reference.render_score_image_reference if image else score_reference.render_score_reference
            rendered=renderer(raw,self.home,cancelled=lambda:self.cancelled() or time.monotonic()>=probe_deadline,
                focus_first_page=True,timeout=min(30,self._remaining(),max(.001,probe_deadline-time.monotonic())))
            self._remaining()
            if self._temporary is None:
                root=self.home/'score-lab'/'inspection-assets'; root.mkdir(parents=True,exist_ok=True)
                self._temporary=tempfile.TemporaryDirectory(prefix='request-',dir=root)
            job=Path(self._temporary.name)/digest; job.mkdir()
            previews=[]
            for i,png in enumerate(rendered['images']):
                path=job/f'preview-{i}.png'; path.write_bytes(png); previews.append(path)
            if image:
                original=job/('original.png' if raw.startswith(b'\x89PNG') else 'original.jpg'); original.write_bytes(raw)
                images=[original]
            else:
                images=previews
                from PySide6.QtGui import QImageReader
                dimensions=tuple((s.width(),s.height()) for p in previews for s in [QImageReader(str(p)).size()])
            remaining=min(self._remaining(),probe_deadline-time.monotonic())
            checked=assess_readability(images,job,self.model_id,timeout=remaining,runner=self.runner,cancelled=self.cancelled,progress=self.progress)
            self._remaining()
            if time.monotonic() >= probe_deadline:
                raise LabFailure("readability_timeout","谱面可读性检查超过本次60秒上限。")
            return result('readable' if checked['readable'] else 'unreadable',checked['reason'],dimensions,rendered['page_count'],tuple(previews))
        except SourceCancelled:
            self._remaining()
            return result('unavailable','谱面有界渲染已停止。',dimensions if 'dimensions' in locals() else ())
        except LabFailure as error:
            if error.code in {'cancelled','timeout'} and (self.cancelled() or time.monotonic()>=self.deadline): raise
            return result('unavailable',error.message,dimensions if 'dimensions' in locals() else ())
        except (ValueError,OSError,HTTPException,score_reference.ScoreReferenceFailed,score_reference.ScoreReferenceUnavailable):
            self._remaining()
            return result('unavailable','谱面有界检查未通过，未证明可读。',dimensions if 'dimensions' in locals() else ())


def _image_families(html, base):
    """Only explicit same-img src/srcset or enclosing original-link evidence."""
    from html.parser import HTMLParser
    from urllib.parse import urljoin, urlsplit
    class Images(HTMLParser):
        def __init__(self):
            super().__init__(); self.anchors=[]; self.families=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='a': self.anchors.append(attrs.get('href',''))
            if tag!='img': return
            values=[attrs.get('src','')]
            # Restrict to simple HTTP URL candidates; ambiguous srcset syntax
            # yields no relationship rather than guessed URL transformations.
            for item in attrs.get('srcset','').split(','):
                parts=item.split()
                if len(parts)==2 and parts[1].endswith(('w','x')):
                    try: valid=float(parts[1][:-1])>0
                    except ValueError: valid=False
                    if valid: values.append(parts[0])
            if self.anchors: values.append(self.anchors[-1])
            urls=set()
            for value in values:
                if not value: continue
                url=urljoin(base,value); parsed=urlsplit(url)
                if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
                    urls.add(url)
            if len(urls)>1: self.families.append(frozenset(urls))
        def handle_endtag(self,tag):
            if tag=='a' and self.anchors: self.anchors.pop()
    parser=Images(); parser.feed(html)
    return tuple(parser.families)
