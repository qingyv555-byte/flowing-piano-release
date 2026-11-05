"""Standalone optional-runtime worker; never import into the application."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import sys
import warnings


def selected_regions(page_number: int, crop: list[float] | None) -> list[dict]:
    regions = [dict(page=page_number, region='overview')]
    if crop is not None:
        regions.append(dict(page=page_number, region='crop', crop=crop))
    return regions


def render_image(source: Path, job: Path, focus_first_page: bool = True, *,
                 page_number: int | None = None, crop: list[float] | None = None) -> None:
    """Inspect dimensions before decoding; all Pillow/native work stays isolated."""
    from PIL import Image, ImageOps

    if source.stat().st_size > 8_000_000 or type(focus_first_page) is not bool:
        raise ValueError('image source exceeds limits')
    if page_number not in (None, 1):
        raise ValueError('image page number exceeds page count')
    # Pillow's warning threshold is also a hard rejection in this worker.
    Image.MAX_IMAGE_PIXELS = 40_000_000
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(source) as image:
            if (image.format not in {'PNG', 'JPEG'} or getattr(image, 'n_frames', 1) != 1
                    or min(image.size) < 1 or image.width * image.height > 40_000_000):
                raise ValueError('unsupported image or excessive dimensions')
            image.verify()
        with Image.open(source) as decoded:
            decoded.load()
            image = ImageOps.exif_transpose(decoded)
            try:
                source_dimensions = list(image.size)
                # Flatten transparency onto paper; do not forward EXIF, ICC or text chunks.
                rgba = image.convert('RGBA')
                try:
                    paper = Image.new('RGB', rgba.size, 'white')
                    paper.paste(rgba, mask=rgba.getchannel('A'))
                finally:
                    rgba.close()
                try:
                    views = ['overview']
                    if crop is not None:
                        views.append('crop')
                    elif page_number is None and focus_first_page:
                        views.append('upper_systems')
                    images, total = [], 0
                    for ordinal, region in enumerate(views, 1):
                        box = ((0, int(paper.height * .12), paper.width, max(int(paper.height * .12) + 1,
                            int(paper.height * .65))) if region == 'upper_systems' else (0, 0, *paper.size))
                        if region == 'crop':
                            left, top, right, bottom = crop
                            box = (math.floor(left * paper.width), math.floor(top * paper.height),
                                   math.ceil(right * paper.width), math.ceil(bottom * paper.height))
                        with paper.crop(box) as view:
                            edge = 1800 if region == 'overview' else 2198
                            scale = min(1, edge / max(view.size), math.sqrt(3_800_000 / (view.width * view.height)))
                            for attempt in range(5):
                                size = tuple(max(1, int(value * scale)) for value in view.size)
                                with view.resize(size, Image.Resampling.LANCZOS) as raster:
                                    output = io.BytesIO()
                                    raster.save(output, format='PNG')
                                    raw = output.getvalue()
                                if len(raw) <= min(1_000_000, 1_800_000 - total):
                                    break
                                scale *= .7
                            else:
                                raise ValueError('PNG output exceeds limit')
                        name = f'page-{ordinal}.png'
                        (job / name).write_bytes(raw)
                        total += len(raw)
                        images.append(name)
                finally:
                    paper.close()
            finally:
                image.close()
    metadata = dict(page_count=1, text='', images=images, rendered_pages=[1] * len(views),
        source_dimensions=source_dimensions)
    if page_number is not None:
        metadata['image_regions'] = selected_regions(page_number, crop)
    elif focus_first_page:
        metadata['image_regions'] = [dict(page=1, region=region) for region in views]
    (job / 'result.json').write_text(json.dumps(metadata), encoding='utf-8')


def render(source: Path, job: Path, max_pages: int, focus_first_page: bool = False, *,
           page_number: int | None = None, crop: list[float] | None = None) -> None:
    import pypdfium2 as pdfium

    if not 1 <= max_pages <= 2 or source.stat().st_size > 8_000_000:
        raise ValueError('PDF source exceeds limits')
    if type(focus_first_page) is not bool or (focus_first_page and page_number is None and max_pages != 2):
        raise ValueError('invalid focus mode')
    images, text, total = [], '', 0
    # No form environment is initialized and no document actions or JS APIs
    # are invoked. All native PDFium parsing occurs in this owned process.
    with pdfium.PdfDocument(source) as document:
        if pdfium.raw.FPDF_GetSecurityHandlerRevision(document) != -1:
            raise ValueError('encrypted PDF references are unsupported')
        page_count = len(document)
        if not 1 <= page_count <= 100:
            raise ValueError('PDF page count exceeds limit')
        views = [(0, 'overview'), (0, 'upper_systems')] if focus_first_page else [
            (index, 'overview') for index in range(min(page_count, max_pages))]
        if page_number is not None:
            if not 1 <= page_number <= page_count:
                raise ValueError('PDF page number exceeds page count')
            views = [(page_number - 1, region['region']) for region in selected_regions(page_number, crop)]
        for ordinal, (index, region) in enumerate(views, 1):
            page = document[index]
            try:
                width, height = page.get_size()
                if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                    raise ValueError('invalid page dimensions')
                if region == 'overview':
                    textpage = page.get_textpage()
                    try:
                        remaining = 4000 - len(text)
                        if remaining:
                            text += textpage.get_text_range(0, min(remaining, textpage.count_chars()))[:remaining]
                    finally:
                        textpage.close()
                margins = (0, height * .35, 0, height * .12) if region == 'upper_systems' else (0, 0, 0, 0)
                view_width, view_height = width, height - margins[1] - margins[3]
                if region == 'crop':
                    left, top, right, bottom = crop
                    view_width = width * max(right - left, 1e-6)
                    view_height = height * max(bottom - top, 1e-6)
                if region == 'overview':
                    scale = min(2.5, 1800 / max(width, height), math.sqrt(3_000_000 / (width * height)))
                else:
                    # Rerender PDF vectors at the enlarged viewport resolution.
                    # Cap the conceptual full page size to keep PDFium offsets in range.
                    scale = min(2198 / max(view_width, view_height),
                                math.sqrt(3_800_000) / math.sqrt(view_width) / math.sqrt(view_height),
                                1_000_000 / max(width, height))
                for attempt in range(5):
                    if scale <= 0 or max(view_width * scale, view_height * scale) > 2200:
                        raise ValueError('invalid raster size')
                    render_margins = margins
                    if region == 'crop':
                        # Round outward to keep even subpixel selections visible.
                        # PdfPage.render rounds crop offsets upward in raster pixels.
                        full_width, full_height = math.ceil(width * scale), math.ceil(height * scale)
                        x0, y0 = math.floor(left * width * scale), math.floor(top * height * scale)
                        x1 = max(x0 + 1, min(full_width, math.ceil(right * width * scale)))
                        y1 = max(y0 + 1, min(full_height, math.ceil(bottom * height * scale)))
                        offsets = (x0, full_height - y1, full_width - x1, y0)
                        render_margins = tuple(max(0, offset - .25) / scale for offset in offsets)
                    bitmap = page.render(scale=scale, crop=render_margins, may_draw_forms=False)
                    try:
                        image = bitmap.to_pil()
                        try:
                            if image.width * image.height > 4_000_000 or max(image.size) > 2200:
                                raise ValueError('raster size exceeds limit')
                            output = io.BytesIO()
                            image.save(output, format='PNG')
                            raw = output.getvalue()
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                    if len(raw) <= min(1_000_000, 1_800_000 - total):
                        break
                    scale *= .7
                else:
                    raise ValueError('PNG output exceeds limit')
                name = f'page-{ordinal}.png'
                (job / name).write_bytes(raw)
                total += len(raw)
                images.append(name)
            finally:
                page.close()
    metadata = dict(page_count=page_count, text=text, images=images,
        rendered_pages=[index + 1 for index, region in views])
    if page_number is not None:
        metadata['image_regions'] = selected_regions(page_number, crop)
    elif focus_first_page:
        metadata['image_regions'] = [dict(page=index + 1, region=region) for index, region in views]
    (job / 'result.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    try:
        mode = sys.argv[4] if len(sys.argv) > 4 else 'pages'
        if mode not in {'pages', 'focus', 'image', 'image-focus'}:
            raise ValueError('invalid PDF rendering mode')
        selection = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
        if mode.startswith('image'):
            render_image(Path(sys.argv[1]), Path(sys.argv[2]), mode == 'image-focus', **selection)
        else:
            render(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), mode == 'focus', **selection)
    except ModuleNotFoundError:
        sys.exit(3)
    except Exception:
        # PDF diagnostics can contain third-party content. The parent exposes
        # a stable failure and always deletes this job, including partial PNGs.
        sys.exit(1)
