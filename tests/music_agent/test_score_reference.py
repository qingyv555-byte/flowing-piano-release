"""Bounded PDF reference rendering and owned worker lifecycle."""
import base64
import json
import os
from pathlib import Path
import sys
import runpy
import signal
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from ai_piano_studio.music_agent import score_reference as reference
from ai_piano_studio.music_agent.song_resolver import SourceCancelled


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=')


def worker(tmp_path, monkeypatch, body):
    script = tmp_path / 'worker.py'
    script.write_text('import sys, pathlib, json, base64, time\njob = pathlib.Path(sys.argv[2])\n' + body, encoding='utf-8')
    monkeypatch.setattr(reference, '_WORKER_PATH', script)
    monkeypatch.setattr(reference.IsolatedMusicRuntime, 'discover', lambda home: Path(sys.executable))


def assert_clean(home):
    assert list((home / 'assistant/runtime-jobs').iterdir()) == []


def test_bounded_images_text_and_no_paths_escape_worker(tmp_path, monkeypatch):
    worker(tmp_path, monkeypatch, f"(job/'page-1.png').write_bytes(base64.b64decode({base64.b64encode(PNG).decode()!r}))\n(job/'result.json').write_text(json.dumps(dict(page_count=3,text='hello',rendered_pages=[1],images=['page-1.png'])))\n")
    result = reference.render_score_reference(b'%PDF-1.4\n', tmp_path)
    assert result == dict(page_count=3, text='hello', rendered_pages=[1], images=(PNG,))
    assert_clean(tmp_path)


@pytest.mark.parametrize('payload', [b'not pdf', b'%PDF-1.4\n' + b'x' * 8_000_000], ids=['invalid', 'oversized'])
def test_invalid_or_oversized_input_rejected_before_start(tmp_path, payload):
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_reference(payload, tmp_path)
    assert not (tmp_path / 'assistant').exists()


def test_missing_runtime_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(reference.IsolatedMusicRuntime, 'discover', lambda home: None)
    with pytest.raises(reference.ScoreReferenceUnavailable):
        reference.render_score_reference(b'%PDF-1.4\n', tmp_path)


@pytest.mark.parametrize('mode', ['timeout', 'cancel', 'logs', 'crash'])
def test_worker_failures_reap_process_and_remove_job(tmp_path, monkeypatch, mode):
    body = "time.sleep(20)\n"
    if mode == 'logs':
        body = "print('x'*1_100_000, flush=True)\ntime.sleep(20)\n"
    if mode == 'crash':
        body = 'sys.exit(2)\n'
    worker(tmp_path, monkeypatch, body)
    native = reference.subprocess.Popen
    processes = []
    def launch(*args, **kwargs):
        process = native(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(reference.subprocess, 'Popen', launch)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return mode == 'cancel' and calls > 2
    with pytest.raises(SourceCancelled if mode == 'cancel' else reference.ScoreReferenceFailed):
        reference.render_score_reference(b'%PDF-1.4\n', tmp_path, cancelled=cancelled, timeout=.2 if mode == 'timeout' else 5)
    assert processes and all(process.poll() is not None for process in processes)
    assert_clean(tmp_path)


@pytest.mark.parametrize('mode', ['root-exits', 'live-root-cancel'])
def test_owned_descendant_is_reaped_even_after_root_exits(tmp_path, monkeypatch, mode):
    pid_file, proceed = tmp_path / 'helper.pid', tmp_path / 'proceed'
    worker(tmp_path, monkeypatch,
        "import subprocess\n"
        "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(helper.pid))\n"
        f"while not pathlib.Path({str(proceed)!r}).exists(): time.sleep(.01)\n"
        + ('sys.exit(1)\n' if mode == 'root-exits' else 'time.sleep(30)\n'))
    cancel, finished = threading.Event(), threading.Event()
    errors = []
    def render():
        try:
            reference.render_score_reference(b'%PDF-1.4\n', tmp_path,
                cancelled=cancel.is_set, timeout=5)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()
    thread = threading.Thread(target=render, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert pid_file.exists(), 'controlled helper failed to start'
    pid = int(pid_file.read_text())
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000 | 0x1, False, pid)
        assert handle
    started = time.monotonic()
    try:
        proceed.touch()
        if mode == 'live-root-cancel':
            cancel.set()
        bounded = finished.wait(2)
        if os.name == 'nt':
            helper_gone = kernel.WaitForSingleObject(handle, 0) == 0
        else:
            try:
                os.kill(pid, 0)
                # An orphan zombie has exited and closed its inherited files.
                stat = Path(f'/proc/{pid}/stat')
                helper_gone = stat.exists() and stat.read_text().split()[2] == 'Z'
            except ProcessLookupError:
                helper_gone = True
    finally:
        # The regression deliberately exercises broken cleanup. Retain an exact
        # helper handle on Windows so a failure cannot leave the test probe live.
        if os.name == 'nt':
            if kernel.WaitForSingleObject(handle, 0) != 0:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 1000)
            kernel.CloseHandle(handle)
        elif not locals().get('helper_gone', False):
            os.kill(pid, signal.SIGKILL)
        cancel.set()
        thread.join(timeout=2)
    assert bounded, 'renderer cleanup exceeded bound'
    assert helper_gone, 'owned helper survived renderer cleanup'
    assert time.monotonic() - started < 2
    expected = SourceCancelled if mode == 'live-root-cancel' else reference.ScoreReferenceFailed
    assert len(errors) == 1 and isinstance(errors[0], expected)
    assert_clean(tmp_path)


@pytest.mark.parametrize('metadata', [
    dict(page_count=1,text='',rendered_pages=[1],images=['../outside.png']),
    dict(page_count=101,text='',rendered_pages=[1],images=['page-1.png']),
    dict(page_count=1,text='x'*4001,rendered_pages=[1],images=['page-1.png']),
    dict(page_count=1,text='',rendered_pages=[2],images=['page-1.png']),
])
def test_untrusted_worker_metadata_is_rejected(tmp_path, monkeypatch, metadata):
    worker(tmp_path, monkeypatch, f"(job/'result.json').write_text({json.dumps(metadata)!r})\n")
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_reference(b'%PDF-1.4\n', tmp_path)
    assert_clean(tmp_path)


def test_invalid_png_is_rejected(tmp_path, monkeypatch):
    worker(tmp_path, monkeypatch, "(job/'page-1.png').write_bytes(b'not PNG')\n(job/'result.json').write_text(json.dumps(dict(page_count=1,text='',rendered_pages=[1],images=['page-1.png'])))\n")
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_reference(b'%PDF-1.4\n', tmp_path)
    assert_clean(tmp_path)


def synthetic_pdf(pages=3):
    """Small genuine PDF with text and vector staff lines, no PDF dependency."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'']
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    children = []
    for index in range(pages):
        page_id, stream_id = len(objects) + 1, len(objects) + 2
        children.append(f'{page_id} 0 R')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>'.encode())
        stream = f'BT /F1 24 Tf 72 720 Td (Score page {index + 1}) Tj ET\n'.encode()
        for y in range(600, 641, 10):
            stream += f'72 {y} m 540 {y} l S\n'.encode()
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'endstream')
    objects[1] = f'<< /Type /Pages /Count {pages} /Kids [{" ".join(children)}] >>'.encode()
    raw, offsets = b'%PDF-1.4\n', [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(raw)
    raw += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    raw += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return raw + f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


def test_real_pdf_worker_renders_only_first_two_pages(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional PDF runtime not installed')
    result = reference.render_score_reference(synthetic_pdf(), tmp_path)
    assert result['page_count'] == 3
    assert result['rendered_pages'] == [1, 2]
    assert 'Score page 1' in result['text'] and 'Score page 2' in result['text']
    assert 'Score page 3' not in result['text']
    assert len(result['images']) == 2
    assert all(raw.startswith(b'\x89PNG\r\n\x1a\n') for raw in result['images'])
    assert sum(map(len, result['images'])) <= 1_800_000
    assert_clean(tmp_path)


def test_focus_rerenders_first_page_at_higher_resolution_with_explicit_regions(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional PDF runtime not installed')
    result = reference.render_score_reference(synthetic_pdf(), tmp_path, focus_first_page=True)
    assert result['page_count'] == 3
    assert result['rendered_pages'] == [1, 1]
    assert result['image_regions'] == [dict(page=1, region='overview'), dict(page=1, region='upper_systems')]
    assert 'Score page 1' in result['text'] and 'Score page 2' not in result['text']
    overview, focus = [struct.unpack('>II', raw[16:24]) for raw in result['images']]
    assert focus[0] > overview[0]
    assert .51 < focus[1] / focus[0] / (792 / 612) < .55
    assert all(max(size) <= 2200 and size[0] * size[1] <= 4_000_000 for size in [overview, focus])
    assert all(len(raw) <= 1_000_000 for raw in result['images'])
    assert sum(map(len, result['images'])) <= 1_800_000
    assert_clean(tmp_path)


@pytest.mark.parametrize('kwargs', [dict(focus_first_page='yes'), dict(focus_first_page=True, max_pages=1)])
def test_invalid_focus_mode_is_rejected_before_launch(tmp_path, kwargs):
    with pytest.raises(ValueError):
        reference.render_score_reference(synthetic_pdf(), tmp_path, **kwargs)
    assert not (tmp_path / 'assistant').exists()


@pytest.mark.parametrize('focus', [False, True])
def test_duplicate_page_claim_requires_focus_and_explicit_regions(tmp_path, monkeypatch, focus):
    metadata = dict(page_count=3, text='', rendered_pages=[1, 1], images=['page-1.png', 'page-2.png'])
    worker(tmp_path, monkeypatch, f"(job/'result.json').write_text({json.dumps(metadata)!r})\n")
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_reference(synthetic_pdf(), tmp_path, focus_first_page=focus)
    assert_clean(tmp_path)


@pytest.mark.parametrize('raw', [b'%PDF-1.4\ninvalid', synthetic_pdf(101)], ids=['corrupt', 'too-many-pages'])
def test_real_worker_rejects_invalid_or_excessive_pdf(tmp_path, raw):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional PDF runtime not installed')
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_reference(raw, tmp_path)
    assert_clean(tmp_path)


def test_even_blank_password_encrypted_documents_rejected_before_page_access(tmp_path, monkeypatch):
    module = runpy.run_path(str(reference._WORKER_PATH))
    class EncryptedDocument:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def __len__(self):
            return 1
        def __getitem__(self, index):
            raise AssertionError('encrypted page must not be accessed')
    fake = SimpleNamespace(PdfDocument=lambda source: EncryptedDocument(),
        raw=SimpleNamespace(FPDF_GetSecurityHandlerRevision=lambda document: 2))
    monkeypatch.setitem(sys.modules, 'pypdfium2', fake)
    source = tmp_path / 'encrypted.pdf'
    source.write_bytes(b'%PDF-1.4\n')
    with pytest.raises(ValueError, match='encrypted'):
        module['render'](source, tmp_path, 2)


def test_image_reference_uses_actual_signature_and_rejects_before_launch(tmp_path):
    for raw in [b'not an image', b'%PDF-1.4\n', PNG + b'x' * 8_000_000]:
        with pytest.raises(reference.ScoreReferenceFailed):
            reference.render_score_image_reference(raw, tmp_path)
    assert not (tmp_path / 'assistant').exists()


def test_real_png_image_worker_returns_overview_and_upper_crop(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional image runtime not installed')
    # A genuine, dependency-free PNG fixture; color distinguishes the cropped region.
    import zlib
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    width, height = 2500, 3400
    pixels = b''.join(b'\x00' + bytes([y % 256, 100, 200]) * width for y in range(height))
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')
    result = reference.render_score_image_reference(raw, tmp_path)
    assert result['page_count'] == 1 and result['rendered_pages'] == [1, 1]
    assert result['source_dimensions'] == [2500, 3400]
    assert result['text'] == ''
    assert result['image_regions'] == [dict(page=1, region='overview'), dict(page=1, region='upper_systems')]
    sizes = [struct.unpack('>II', image[16:24]) for image in result['images']]
    assert sizes[1][0] > sizes[0][0]
    assert .51 < sizes[1][1] / sizes[1][0] / (height / width) < .55
    assert all(max(size) <= 2200 and size[0] * size[1] <= 4_000_000 for size in sizes)
    assert all(len(image) <= 1_000_000 for image in result['images'])
    assert sum(map(len, result['images'])) <= 1_800_000

    crop = [.25, .5, .75, .75]
    selected = reference.render_score_image_reference(raw, tmp_path, crop=crop)
    assert selected['source_dimensions'] == [width, height]
    assert selected['rendered_pages'] == [1, 1]
    assert selected['image_regions'] == [dict(page=1, region='overview'), dict(page=1, region='crop', crop=crop)]
    assert struct.unpack('>II', selected['images'][1][16:24]) == (1250, 850)
    from PySide6.QtGui import QImage
    selected_pixels = QImage.fromData(selected['images'][1])
    assert selected_pixels.pixelColor(0, 0).red() == int(height * crop[1]) % 256
    assert selected_pixels.pixelColor(0, 849).red() == (int(height * crop[3]) - 1) % 256
    with pytest.raises(ValueError, match='page_number'):
        reference.render_score_image_reference(raw, tmp_path, page_number=2)
    assert_clean(tmp_path)


@pytest.mark.parametrize('raw', [PNG[:30], b'\xff\xd8\xffbad-jpeg',
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 10000, 10000) + PNG[24:]],
    ids=['truncated-png', 'corrupt-jpeg', 'excessive-pixels'])
def test_real_image_worker_rejects_malformed_or_excessive_source(tmp_path, raw):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional image runtime not installed')
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_image_reference(raw, tmp_path)
    assert_clean(tmp_path)


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_image_worker_failure_is_cleaned_up(tmp_path, monkeypatch, mode):
    worker(tmp_path, monkeypatch, 'time.sleep(20)\n')
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return mode == 'cancel' and calls > 2
    with pytest.raises(SourceCancelled if mode == 'cancel' else reference.ScoreReferenceFailed):
        reference.render_score_image_reference(PNG, tmp_path, cancelled=cancelled, timeout=.2)
    assert_clean(tmp_path)


JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAgABgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigAooooAKKKKACiiigD//2Q==')


@pytest.mark.parametrize('focus', [True, False])
def test_real_jpeg_works_without_filename_or_mime(tmp_path, focus):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional image runtime not installed')
    result = reference.render_score_image_reference(JPEG, tmp_path, focus_first_page=focus)
    assert result['source_dimensions'] == [24, 32]
    assert result['page_count'] == 1 and result['text'] == ''
    assert result['rendered_pages'] == ([1, 1] if focus else [1])
    assert len(result['images']) == (2 if focus else 1)
    assert ('image_regions' in result) is focus
    assert_clean(tmp_path)


@pytest.mark.parametrize('change', [dict(page_count=2), dict(text='untrusted text'),
    dict(rendered_pages=[1, 2]), dict(extra='untrusted'), dict(image_regions=[])] )
def test_image_worker_metadata_must_describe_one_image_page(tmp_path, monkeypatch, change):
    metadata = dict(page_count=1, text='', rendered_pages=[1, 1], images=['page-1.png', 'page-2.png'],
        source_dimensions=[24, 32],
        image_regions=[dict(page=1, region='overview'), dict(page=1, region='upper_systems')])
    metadata.update(change)
    worker(tmp_path, monkeypatch, f"(job/'result.json').write_text({json.dumps(metadata)!r})\n")
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_image_reference(JPEG, tmp_path)
    assert_clean(tmp_path)


def test_image_pixel_limit_is_checked_before_verify_or_load(tmp_path, monkeypatch):
    module = runpy.run_path(str(reference._WORKER_PATH))
    class HugeImage:
        format, n_frames, size, width, height = 'PNG', 1, (8000, 6000), 8000, 6000
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def verify(self):
            raise AssertionError('oversized image must be rejected before verify')
        def load(self):
            raise AssertionError('oversized image must be rejected before load')
    fake_image = SimpleNamespace(open=lambda source: HugeImage(), DecompressionBombWarning=UserWarning)
    monkeypatch.setitem(sys.modules, 'PIL', SimpleNamespace(Image=fake_image, ImageOps=SimpleNamespace()))
    source = tmp_path / 'huge.png'
    source.write_bytes(PNG)
    with pytest.raises(ValueError, match='dimensions'):
        module['render_image'](source, tmp_path)


def test_image_pre_cancel_does_not_create_jobs(tmp_path):
    with pytest.raises(SourceCancelled):
        reference.render_score_image_reference(JPEG, tmp_path, cancelled=lambda: True)
    assert not (tmp_path / 'assistant').exists()


@pytest.mark.parametrize('dimensions', [None, '200x260', [200], [200, 260, 3],
    [True, 260], [200, 260.0], [0, 260], [-1, 260], [10000, 4001]])
def test_image_source_dimensions_are_strictly_validated(tmp_path, dimensions):
    metadata = dict(page_count=1, text='', rendered_pages=[1], images=['page-1.png'],
        source_dimensions=dimensions)
    (tmp_path / 'result.json').write_text(json.dumps(metadata), encoding='utf-8')
    (tmp_path / 'page-1.png').write_bytes(PNG)
    with pytest.raises(reference.ScoreReferenceFailed, match='source dimensions'):
        reference._read_result(tmp_path, 2, image_input=True)


def test_source_dimensions_required_only_for_image_input(tmp_path):
    metadata = dict(page_count=1, text='', rendered_pages=[1], images=['page-1.png'])
    (tmp_path / 'result.json').write_text(json.dumps(metadata), encoding='utf-8')
    (tmp_path / 'page-1.png').write_bytes(PNG)
    with pytest.raises(reference.ScoreReferenceFailed):
        reference._read_result(tmp_path, 2, image_input=True)
    result = reference._read_result(tmp_path, 2)
    assert 'source_dimensions' not in result
    metadata['source_dimensions'] = [200, 260]
    (tmp_path / 'result.json').write_text(json.dumps(metadata), encoding='utf-8')
    with pytest.raises(reference.ScoreReferenceFailed):
        reference._read_result(tmp_path, 2)


def test_real_thumbnail_reports_source_size_without_upscaling_or_rejection(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional image runtime not installed')
    import zlib
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 200, 260, 8, 2, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff' * 600) * 260)) + chunk(b'IEND', b'')
    result = reference.render_score_image_reference(raw, tmp_path)
    assert result['source_dimensions'] == [200, 260]
    assert struct.unpack('>II', result['images'][0][16:24]) == (200, 260)
    assert result['rendered_pages'] == [1, 1]
    assert_clean(tmp_path)


def test_real_image_source_dimensions_follow_exif_orientation(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional image runtime not installed')
    # A single orientation=6 EXIF tag rotates the 24x32 source to 32x24.
    # Encode the TIFF IFD explicitly: tag, SHORT type, count, inline value, next IFD.
    exif = b'Exif\x00\x00II\x2a\x00' + struct.pack('<IH', 8, 1) + struct.pack('<HHIHHI', 274, 3, 1, 6, 0, 0)
    raw = JPEG[:2] + b'\xff\xe1' + struct.pack('>H', len(exif) + 2) + exif + JPEG[2:]
    result = reference.render_score_image_reference(raw, tmp_path)
    assert result['source_dimensions'] == [32, 24]
    assert struct.unpack('>II', result['images'][0][16:24]) == (32, 24)
    assert_clean(tmp_path)


@pytest.mark.parametrize('crop', [None, [.125, .25, .875, .625], [.5, .5, .5000000001, .5000000001]])
def test_select_later_pdf_page(tmp_path, crop):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional PDF runtime not installed')
    result = reference.render_score_reference(
        synthetic_pdf(), tmp_path, page_number=3, crop=crop, focus_first_page=True)
    assert result['page_count'] == 3
    assert result['text'] == 'Score page 3'
    assert result['rendered_pages'] == ([3] if crop is None else [3, 3])
    regions = [dict(page=3, region='overview')]
    if crop is not None:
        regions.append(dict(page=3, region='crop', crop=crop))
        width, height = struct.unpack('>II', result['images'][1][16:24])
        if crop[2] - crop[0] < .0001:
            assert (width, height) == (1, 1)
        else:
            assert width / height == pytest.approx(612 / 792 * 2, abs=.005)
    assert result['image_regions'] == regions
    assert_clean(tmp_path)


def test_pdf_crop_uses_top_left_coordinates_instead_of_inverted_y(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional PDF runtime not installed')
    from PySide6.QtGui import QImage
    # Synthetic staff lines lie at PDF y=600..640, near the visual top.
    counts = []
    for crop in ([0, .16, 1, .26], [0, .74, 1, .84]):
        result = reference.render_score_reference(synthetic_pdf(), tmp_path, page_number=3, crop=crop)
        raster = QImage.fromData(result['images'][1])
        counts.append(sum(raster.pixelColor(raster.width() // 2, y).lightness() < 128
                          for y in range(raster.height())))
    assert counts[0] >= 5 and counts[1] == 0
    assert_clean(tmp_path)


@pytest.mark.parametrize('kwargs', [
    dict(page_number=0), dict(page_number=101), dict(page_number=True), dict(page_number=1.0),
    dict(crop=[0, 0, float('nan'), 1]), dict(crop=[0, 0, 1, float('inf')]),
    dict(crop=[False, 0, 1, 1]), dict(crop=[.5, 0, .5, 1]), dict(crop=[0, 0, 1.1, 1]),
    dict(crop=[0, 0, 1]), dict(crop=[0, 0, 1, 1], max_pages=1),
])
def test_invalid_selection_rejected_before_launch(tmp_path, kwargs):
    with pytest.raises(ValueError):
        reference.render_score_reference(synthetic_pdf(), tmp_path, **kwargs)
    assert not (tmp_path / 'assistant').exists()


def test_page_number_exceeds_document(tmp_path):
    if reference.IsolatedMusicRuntime.discover(tmp_path) is None:
        pytest.skip('optional PDF runtime not installed')
    with pytest.raises(reference.ScoreReferenceFailed):
        reference.render_score_reference(synthetic_pdf(), tmp_path, page_number=4)
    assert_clean(tmp_path)


@pytest.mark.parametrize('pages, overview_page, crop', [
    ([2, 2], 3, [0, 0, 1, 1]),
    ([3, 3], 2, [0, 0, 1, 1]),
    ([3, 3], 3.0, [0, 0, 1, 1]),
    ([3, 3], 3, [False, 0, 1, 1]),
    ([3, 3], 3, [.25, 0, 1, 1]),
])
def test_untrusted_selected_metadata_rejected(tmp_path, monkeypatch, pages, overview_page, crop):
    metadata = dict(page_count=3, text='', rendered_pages=pages, images=['page-1.png', 'page-2.png'],
        image_regions=[dict(page=overview_page, region='overview'), dict(page=3, region='crop', crop=crop)])
    worker(tmp_path, monkeypatch, f"(job/'result.json').write_text({json.dumps(metadata)!r})\n")
    with pytest.raises(reference.ScoreReferenceFailed, match='metadata|regions'):
        reference.render_score_reference(synthetic_pdf(), tmp_path, page_number=3, crop=[0, 0, 1, 1])
    assert_clean(tmp_path)
