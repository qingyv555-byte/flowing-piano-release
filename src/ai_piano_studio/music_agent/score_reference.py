"""Bounded visual score references rendered only in an optional child runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import time

from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.song_resolver import SourceCancelled
from ai_piano_studio.music_agent.transcription import IsolatedMusicRuntime, _stop_owned_runtime
from ai_piano_studio.music_agent.codex_native import _WindowsProcessJob


_WORKER_PATH = Path(__file__).with_name('score_reference_worker.py')


class ScoreReferenceUnavailable(RuntimeError):
    """The optional PDF runtime has not been installed."""


class ScoreReferenceFailed(RuntimeError):
    """The source or worker did not produce a bounded visual reference."""


def _stop_renderer(process, owned_job) -> None:
    """Close the original lifetime container even when its root has exited."""
    errors = 0
    try:
        if owned_job is not None:
            errors = owned_job.close()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # The owned session has already exited, including descendants.
                errors = 0
    finally:
        if process is not None:
            _stop_owned_runtime(process)
    if errors:
        raise ScoreReferenceFailed('PDF runtime cleanup could not be verified')


def _read_result(job: Path, max_pages: int, focus_first_page: bool = False, *, image_input: bool = False,
                 regions: list[dict] | None = None) -> dict:
    if regions is None and focus_first_page:
        regions = [dict(page=1, region='overview'), dict(page=1, region='upper_systems')]
    metadata = job / 'result.json'
    if not metadata.is_file() or metadata.is_symlink() or metadata.stat().st_size > 32_000:
        raise ScoreReferenceFailed('PDF renderer returned no bounded metadata')
    result = json.loads(metadata.read_text(encoding='utf-8'))
    if not isinstance(result, dict):
        raise ScoreReferenceFailed('invalid PDF reference metadata')
    count, text = result.get('page_count'), result.get('text')
    pages, names = result.get('rendered_pages'), result.get('images')
    if (type(count) is not int or not 1 <= count <= 100
            or not isinstance(text, str) or len(text) > 4000
            or not isinstance(pages, list) or not isinstance(names, list)
            or not 1 <= len(pages) <= (max_pages if regions else min(count, max_pages)) or len(pages) != len(names)
            or any(type(page) is not int or not 1 <= page <= count for page in pages)
            or pages != ([region['page'] for region in regions] if regions else list(range(1, len(pages) + 1)))):
        raise ScoreReferenceFailed('invalid PDF reference metadata')
    expected_keys = {'page_count', 'text', 'rendered_pages', 'images'}
    if image_input:
        expected_keys.add('source_dimensions')
    if regions:
        expected_keys.add('image_regions')
    if set(result) != expected_keys or (image_input and (count != 1 or text != '')):
        raise ScoreReferenceFailed('invalid score reference metadata')
    if image_input:
        dimensions = result['source_dimensions']
        if (not isinstance(dimensions, list) or len(dimensions) != 2
                or any(type(value) is not int or value < 1 for value in dimensions)
                or dimensions[0] * dimensions[1] > 40_000_000):
            raise ScoreReferenceFailed('invalid score source dimensions')
    if regions:
        claimed = result['image_regions']
        if (claimed != regions or any(type(region['page']) is not int for region in claimed)
                or any(type(value) not in (int, float)
                       for region in claimed for value in region.get('crop', ()))):
            raise ScoreReferenceFailed('invalid PDF reference regions')
    images, total = [], 0
    for ordinal, name in enumerate(names, 1):
        if name != f'page-{ordinal}.png':
            raise ScoreReferenceFailed('invalid PDF image path')
        path = job / name
        if (not path.is_file() or path.is_symlink() or path.resolve().parent != job.resolve()
                or not 45 <= path.stat().st_size <= 1_000_000):
            raise ScoreReferenceFailed('invalid PDF image output')
        raw = path.read_bytes()
        total += len(raw)
        if total > 1_800_000 or raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' or raw[-12:] != b'\x00\x00\x00\x00IEND\xaeB`\x82':
            raise ScoreReferenceFailed('invalid PDF image output')
        width, height = struct.unpack('>II', raw[16:24])
        if not 1 <= width <= 2200 or not 1 <= height <= 2200 or width * height > 4_000_000:
            raise ScoreReferenceFailed('PDF image dimensions exceed limit')
        images.append(raw)
    bounded = dict(page_count=count, text=text, images=tuple(images), rendered_pages=pages)
    if image_input:
        bounded['source_dimensions'] = dimensions
    if regions:
        bounded['image_regions'] = regions
    return bounded


def render_score_reference(raw: bytes, home: Path, *, cancelled=lambda: False,
                           progress=lambda event: None, max_pages: int = 2,
                           timeout: float = 30, focus_first_page: bool = False,
                           image_input: bool = False, page_number: int | None = None,
                           crop: list[float] | None = None) -> dict:
    """Return bounded PNG views; image_input accepts only genuine PNG/JPEG sources."""
    if type(image_input) is not bool:
        raise ValueError('image_input must be boolean')
    signature = (isinstance(raw, bytes) and
        (raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')) if image_input else raw.startswith(b'%PDF-')))
    if not signature or len(raw) > 8_000_000:
        raise ScoreReferenceFailed('invalid or oversized score source')
    if type(max_pages) is not int or not 1 <= max_pages <= 2:
        raise ValueError('max_pages must be one or two')
    if page_number is not None and (type(page_number) is not int or not 1 <= page_number <= (1 if image_input else 100)):
        raise ValueError('invalid page_number')
    if crop is not None and (
        not isinstance(crop, list) or len(crop) != 4
        or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in crop)
        or not (crop[0] < crop[2] and crop[1] < crop[3])
        or max_pages != 2
    ):
        raise ValueError('crop requires four ordered normalized coordinates and two image slots')
    regions = None
    selection = []
    if page_number is not None or crop is not None:
        page_number = page_number or 1
        regions = [dict(page=page_number, region='overview')]
        if crop is not None:
            crop = list(crop)
            regions.append(dict(page=page_number, region='crop', crop=crop))
        selection = [json.dumps(dict(page_number=page_number, crop=crop))]
    if type(focus_first_page) is not bool or (focus_first_page and not regions and max_pages != 2):
        raise ValueError('focus_first_page requires a boolean and two image slots')
    if not 0 < timeout <= 120:
        raise ValueError('timeout must be positive and at most 120 seconds')
    if cancelled():
        raise SourceCancelled()
    python = IsolatedMusicRuntime.discover(Path(home))
    if python is None or not python.is_file():
        raise ScoreReferenceUnavailable('optional PDF runtime is unavailable')
    jobs = Path(home) / 'assistant/runtime-jobs'
    jobs.mkdir(parents=True, exist_ok=True)
    progress(ProviderProgressEvent('parsing'))
    try:
        with tempfile.TemporaryDirectory(prefix='score-', dir=jobs) as temporary:
            job = Path(temporary)
            source = job / ('source.image' if image_input else 'source.pdf')
            source.write_bytes(raw)
            with (job / 'output.log').open('wb') as output:
                owned_job = _WindowsProcessJob() if os.name == 'nt' else None
                process = None
                try:
                    process = subprocess.Popen([str(python), '-I', str(_WORKER_PATH), str(source), str(job), str(max_pages),
                        ('image-focus' if focus_first_page else 'image') if image_input else
                        ('focus' if focus_first_page else 'pages'), *selection],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                        creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == 'nt' else 0,
                        start_new_session=os.name != 'nt')
                    if owned_job is not None:
                        owned_job.attach_and_resume(process)
                    (job / 'process.json').write_text(json.dumps(dict(pid=process.pid,
                        startedAt=time.time(), purpose='Score reference rendering')), encoding='utf-8')
                    started, heartbeat = time.monotonic(), 0.0
                    while True:
                        if cancelled():
                            raise SourceCancelled()
                        # Check final output too: a short-lived worker can exceed
                        # limits before the first poll observes it running.
                        files = list(job.iterdir())
                        if (len(files) > 8 or any(path.is_symlink() or not path.is_file() for path in files)
                                or (job / 'output.log').stat().st_size > 1_000_000
                                or sum(path.stat().st_size for path in files) > len(raw) + 3_000_000):
                            raise ScoreReferenceFailed('PDF runtime output exceeded limit')
                        if process.poll() is not None:
                            break
                        now = time.monotonic()
                        if now - started >= timeout:
                            raise ScoreReferenceFailed('PDF rendering timed out')
                        if now - heartbeat >= 1:
                            progress(ProviderProgressEvent('heartbeat', process_alive=True))
                            heartbeat = now
                        time.sleep(.05)
                    if process.returncode == 3:
                        raise ScoreReferenceUnavailable('optional PDF renderer is unavailable')
                    if process.returncode:
                        raise ScoreReferenceFailed('PDF rendering failed')
                    result = _read_result(job, max_pages, focus_first_page, image_input=image_input, regions=regions)
                    progress(ProviderProgressEvent('parsing', current=len(result['images']),
                        total=len(result['images']), process_alive=False))
                    return result
                finally:
                    _stop_renderer(process, owned_job)
    except (OSError, ValueError, TypeError, struct.error) as exc:
        raise ScoreReferenceFailed('PDF reference output could not be read') from exc


def render_score_image_reference(raw: bytes, home: Path, *, cancelled=lambda: False,
                                 progress=lambda event: None, focus_first_page: bool = True,
                                 timeout: float = 30, page_number: int | None = None,
                                 crop: list[float] | None = None) -> dict:
    """Decode PNG/JPEG in the owned runtime; return overview and upper systems by default.

    Filename and MIME are intentionally absent. The image yields page_count=1,
    empty text, and the same rendered_pages/image_regions contract as focused PDF.
    source_dimensions contains actual width/height after EXIF orientation, before
    any view crop or resize; it does not assert that notation is legible.
    """
    return render_score_reference(raw, home, cancelled=cancelled, progress=progress,
        focus_first_page=focus_first_page, timeout=timeout, image_input=True,
        page_number=page_number, crop=crop)
