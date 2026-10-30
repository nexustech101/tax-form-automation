"""parser.py

Docling document parser, configured for a long-running API process.

Production choices:

* **One converter per process, built at startup.** `DocumentConverter`
  caches its initialised pipeline (layout model + OCR sessions) keyed by the
  pipeline options, so building it once and reusing it keeps the weights hot.
  `load()` initialises the PDF and image pipelines and runs a tiny warm-up
  conversion, because RapidOCR creates its ONNX sessions lazily on first use.
* **OCR only where needed.** The default OCR mode skips regions that already
  have a PDF text layer, so digital PDFs pay almost nothing for OCR and
  scans/photos are OCR'd in full. RapidOCR on onnxruntime is the lightest
  engine Docling supports.
* **Nothing the extractor does not read.** Table structure (TableFormer),
  page/picture images, enrichments and picture classification are off; the
  extractor reads Docling's raw line cells (`generate_parsed_pages`).
* **Bounded work.** Conversions are limited by page count, file size and a
  timeout, and a semaphore caps how many run at once (models are CPU/GPU
  bound; more parallelism only thrashes).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pypdfium2 as pdfium
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
from docling_core.types.io import DocumentStream
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageDraw

from core.config import Settings
from core.layout import Block, blocks_from_conversion, page_heights

log = logging.getLogger(__name__)

# Magic bytes -> (media type, docling input format). Content-Type headers are
# client-controlled, so the payload itself decides.
SIGNATURES: list[tuple[bytes, str, InputFormat]] = [
    (b"%PDF-", "application/pdf", InputFormat.PDF),
    (b"\x89PNG\r\n\x1a\n", "image/png", InputFormat.IMAGE),
    (b"\xff\xd8\xff", "image/jpeg", InputFormat.IMAGE),
    (b"II*\x00", "image/tiff", InputFormat.IMAGE),
    (b"MM\x00*", "image/tiff", InputFormat.IMAGE),
    (b"BM", "image/bmp", InputFormat.IMAGE),
]
EXTENSIONS = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tiff", "image/bmp": ".bmp"}
SUPPORTED_MEDIA_TYPES = sorted(EXTENSIONS)


class UnsupportedDocument(ValueError):
    """The upload is not a PDF or supported image."""


class DocumentTooLarge(ValueError):
    """Too many pages for this deployment's limits."""


class ConversionFailed(RuntimeError):
    """Docling could not convert the document (corrupt, encrypted, timeout)."""


IMAGE_DPI = 150  # page images for checkbox detection


@dataclass
class ParsedDocument:
    filename: str
    media_type: str
    sha256: str
    pages: int
    text_layer: bool
    blocks: list[Block]
    page_heights: dict[int, float]  # page_no -> height in block units (width is 1000)
    parse_ms: int
    source: bytes = field(default=b"", repr=False)  # the bytes Docling converted
    conversion_errors: list[str] = field(default_factory=list)  # Docling reported a partial conversion
    missing_pages: list[int] = field(default_factory=list)  # pages no text was read from
    _images: dict[int, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    def gaps_in(self, pages: list[int]) -> list[str]:
        """
        Warnings for a form on `pages`: the file was only partly converted, or a
        page inside the form's page range produced no text.
        """
        out = []
        if self.conversion_errors:
            out.append(f"the file was only partly converted ({'; '.join(self.conversion_errors)[:300]}); "
                       "values may be missing")
        holes = [p for p in self.missing_pages if pages and min(pages) < p < max(pages)]
        if holes:
            out.append(f"no text could be read from page(s) {', '.join(map(str, holes))} inside this form")
        return out

    def page_image(self, page_no: int) -> np.ndarray | None:
        """
        Grayscale image of a page whose width spans the blocks' 0..1000 units:
        the PDF page rendered at 150 DPI, or the uploaded image itself.
        """
        if page_no in self._images:
            return self._images[page_no]
        image = None
        try:
            if self.media_type == "application/pdf":
                pdf = pdfium.PdfDocument(self.source)
                try:
                    if 1 <= page_no <= len(pdf):
                        bitmap = pdf[page_no - 1].render(scale=IMAGE_DPI / 72, grayscale=True)
                        image = np.array(bitmap.to_pil().convert("L"))
                finally:
                    pdf.close()
            elif page_no == 1:
                with Image.open(io.BytesIO(self.source)) as img:
                    image = np.array(img.convert("L"))
        except (pdfium.PdfiumError, OSError):
            image = None
        self._images[page_no] = image
        return image


def sniff(data: bytes) -> tuple[str, InputFormat]:
    """*Usage:*
    >>> sniff(b"%PDF-1.7 ...")
    ('application/pdf', <InputFormat.PDF: 'pdf'>)
    """
    for magic, media_type, fmt in SIGNATURES:
        if data.startswith(magic):
            return media_type, fmt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", InputFormat.IMAGE
    raise UnsupportedDocument(f"Unsupported file type. Upload one of: {', '.join(SUPPORTED_MEDIA_TYPES)}, image/webp.")


def pdf_text_layer(data: bytes, max_pages: int = 2) -> tuple[int, str]:
    """(page count, embedded text of the first pages). Cheap: no models run."""
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise ConversionFailed(f"Unreadable PDF: {exc}") from exc
    try:
        texts = []
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            texts.append(page.get_textpage().get_text_range())
        return len(pdf), "\n".join(texts).strip()
    finally:
        pdf.close()


class DoclingParser:
    """Process-wide Docling converter. Create once, call `load()` at startup."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._converter: DocumentConverter | None = None
        self._slots = asyncio.Semaphore(max(1, settings.docling_max_concurrency))
        self.loaded = False

    # --- lifecycle ---------------------------------------------------------

    def _pipeline_options(self) -> PdfPipelineOptions:
        s = self.settings
        options = PdfPipelineOptions(
            do_ocr=True,
            # No text-direction classifier: pages are upright, and it flips short lines upside down.
            ocr_options=RapidOcrOptions(use_cls=False),
            do_table_structure=s.docling_tables,
            table_structure_options=TableStructureOptions(mode=TableFormerMode.FAST, do_cell_matching=True),
            generate_parsed_pages=True,  # raw line/word cells for core.layout
            generate_page_images=False,
            generate_picture_images=False,
            do_picture_classification=False,
            do_picture_description=False,
            do_code_enrichment=False,
            do_formula_enrichment=False,
            document_timeout=s.docling_timeout_s,
            accelerator_options=AcceleratorOptions(
                device=AcceleratorDevice(s.docling_device), num_threads=s.docling_threads,
            ),
        )
        if s.docling_artifacts_path:
            options.artifacts_path = s.docling_artifacts_path
        return options

    def load(self) -> None:
        """Build the converter, load all model weights and warm the OCR sessions."""
        if self.loaded:
            return
        started = time.perf_counter()
        options = self._pipeline_options()
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
            },
        )
        self._converter.initialize_pipeline(InputFormat.PDF)
        self._converter.initialize_pipeline(InputFormat.IMAGE)
        if self.settings.docling_warmup:
            self._convert(_warmup_png(), "warmup.png", InputFormat.IMAGE, pages=1)
        self.loaded = True
        log.info("docling ready", extra={"ctx": {"load_ms": int((time.perf_counter() - started) * 1000)}})

    # --- conversion --------------------------------------------------------

    def _convert(self, data: bytes, filename: str, fmt: InputFormat, pages: int) -> ConversionResult:
        assert self._converter is not None, "DoclingParser.load() was not called"
        result = self._converter.convert(
            DocumentStream(name=filename, stream=io.BytesIO(data)),
            raises_on_error=False,
            max_num_pages=max(pages, 1),
            max_file_size=self.settings.max_upload_mb * 1024 * 1024,
        )
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            reasons = "; ".join(e.error_message for e in result.errors) or result.status.value
            raise ConversionFailed(f"Document conversion failed: {reasons}")
        return result

    def _check_pages(self, pages: int, max_pages: int | None) -> None:
        limit = max_pages or self.settings.max_pages
        if pages > limit:
            raise DocumentTooLarge(f"Document has {pages} pages; the limit is {limit}.")

    def parse_sync(self, data: bytes, filename: str, max_pages: int | None = None) -> ParsedDocument:
        started = time.perf_counter()
        media_type, fmt = sniff(data)
        pages, text_layer = 1, False
        if fmt is InputFormat.PDF:
            pages, embedded = pdf_text_layer(data)
            text_layer = len(embedded) > 20
            self._check_pages(pages, max_pages)
        else:
            data = _normalise_image(data)
        safe_name = _safe_filename(filename, media_type)
        result = self._convert(data, safe_name, fmt, pages)
        blocks = blocks_from_conversion(result)
        if not blocks:
            raise ConversionFailed("No text could be read from the document: it may be blank or unreadable.")
        read = {b.page for b in blocks}
        return ParsedDocument(
            filename=filename,
            media_type=media_type,
            sha256=hashlib.sha256(data).hexdigest(),
            pages=pages,  # from the file itself: pages Docling skipped still count
            text_layer=text_layer,
            blocks=blocks,
            page_heights=page_heights(result),
            parse_ms=int((time.perf_counter() - started) * 1000),
            source=data,
            conversion_errors=[e.error_message for e in result.errors] if result.status is ConversionStatus.PARTIAL_SUCCESS else [],
            missing_pages=[p for p in range(1, pages + 1) if p not in read],
        )

    async def parse(self, data: bytes, filename: str, max_pages: int | None = None) -> ParsedDocument:
        """Convert in a worker thread, at most `docling_max_concurrency` at a time."""
        async with self._slots:
            return await run_in_threadpool(self.parse_sync, data, filename, max_pages)

    async def quick_text(self, data: bytes, filename: str) -> tuple[str, ParsedDocument | None]:
        """
        Text for classification only. Digital PDFs use the embedded text layer
        (milliseconds, no models); scans and images go through Docling OCR.
        """
        media_type, fmt = sniff(data)
        if fmt is InputFormat.PDF:
            pages, embedded = await run_in_threadpool(pdf_text_layer, data)
            self._check_pages(pages, None)
            if len(embedded) > 20:
                return embedded, None
        parsed = await self.parse(data, filename)
        return parsed.text, parsed


def _safe_filename(filename: str, media_type: str) -> str:
    """Docling picks the backend from the extension, so make it match the bytes."""
    stem = "".join(ch for ch in (filename.rsplit(".", 1)[0] or "upload") if ch.isalnum() or ch in "-_")[:64]
    return f"{stem or 'upload'}{EXTENSIONS.get(media_type, '.png')}"


def _normalise_image(data: bytes) -> bytes:
    """Re-encode images as PNG (handles WEBP/CMYK/palette/EXIF rotation) and cap size."""
    from PIL import ImageOps

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # 3400px ~ letter page at 300 DPI; larger only slows OCR.
            img.thumbnail((3400, 3400))
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise UnsupportedDocument(f"Unreadable image: {exc}") from exc


def _warmup_png() -> bytes:
    img = Image.new("L", (900, 300), 255)
    ImageDraw.Draw(img).text((40, 120), "Form W-2 Wage and Tax Statement 2025  1,234.56", fill=0)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
