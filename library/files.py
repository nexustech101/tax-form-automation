"""library/files.py

Original uploads, kept for review: one file per distinct upload under
`library.files`, named by its SHA-256 (two keys uploading the same file share
it; it is removed with the last document using it). Pages render to PNG for
the review dashboard.

Page coordinates: `ValueSource.box` is on a page 1000 units wide, origin top
left, on both axes. A page rendered `width` pixels wide maps a box by
multiplying each coordinate by `width / 1000`.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from library.schema import PageInfo

MAX_WIDTH = 2400  # rendered page width limit, pixels
SUFFIXES = {
    "application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tiff", "image/bmp": ".bmp",
    "image/webp": ".webp", "text/plain": ".txt", "text/markdown": ".md", "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}  # download file names


class Files:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, sha256: str) -> Path:
        if len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256):
            raise ValueError("not a SHA-256 hex digest")
        return self.root / sha256

    def save(self, sha256: str, data: bytes) -> None:
        path = self._path(sha256)
        if path.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def load(self, sha256: str) -> bytes | None:
        path = self._path(sha256)
        return path.read_bytes() if path.exists() else None

    def delete(self, sha256: str) -> None:
        self._path(sha256).unlink(missing_ok=True)


def _image(data: bytes) -> Image.Image:
    """The first frame, upright (EXIF rotation applied, as the parser does)."""
    img = Image.open(io.BytesIO(data))
    return ImageOps.exif_transpose(img)


def page_sizes(data: bytes, media_type: str) -> list[PageInfo]:
    """Page sizes: PDF points, or image pixels (one page)."""
    if media_type == "application/pdf":
        pdf = pdfium.PdfDocument(data)
        try:
            sizes = [pdf[i].get_size() for i in range(len(pdf))]
            return [PageInfo(n=i + 1, width=round(w, 2), height=round(h, 2)) for i, (w, h) in enumerate(sizes)]
        finally:
            pdf.close()
    if media_type.startswith("image/"):
        with _image(data) as img:
            return [PageInfo(n=1, width=img.width, height=img.height)]
    return []


def render_png(data: bytes, media_type: str, page: int, width: int) -> bytes | None:
    """Page `page` (from 1) as a PNG `width` pixels wide; None when the page does not exist."""
    width = max(1, min(width, MAX_WIDTH))
    if media_type == "application/pdf":
        pdf = pdfium.PdfDocument(data)
        try:
            if not 1 <= page <= len(pdf):
                return None
            pdf_page = pdf[page - 1]
            image = pdf_page.render(scale=width / pdf_page.get_width()).to_pil()
        finally:
            pdf.close()
    elif media_type.startswith("image/") and page == 1:
        with _image(data) as img:
            image = img.convert("RGB")
            image = image.resize((width, max(1, round(image.height * width / image.width))), Image.LANCZOS)
    else:
        return None
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()


def pdf_pages(data: bytes, pages: list[int]) -> bytes:
    """A PDF of the given pages (from 1) of `data`, in that order; pages that don't exist are skipped."""
    src = pdfium.PdfDocument(data)
    out = pdfium.PdfDocument.new()
    try:
        out.import_pages(src, [p - 1 for p in pages if 1 <= p <= len(src)])
        buffer = io.BytesIO()
        out.save(buffer)
        return buffer.getvalue()
    finally:
        out.close()
        src.close()
