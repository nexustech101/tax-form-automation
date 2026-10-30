"""agent/ingest.py

Turns an uploaded file into a `DocumentDraft`: chunks of text with metadata,
ready for `Library.add`.

| File                         | Chunks                                                                |
|------------------------------|-----------------------------------------------------------------------|
| PDF / image, `extract=true`  | per extracted form: its fields as `name: value` lines (form id and    |
|                              | tax year as metadata; long tables split by rows), plus the text of    |
|                              | pages no form was extracted from                                      |
| PDF / image, `extract=false` | text per page                                                         |
| .md                          | sections by heading                                                   |
| .txt                         | paragraphs                                                            |
| .csv / .xlsx                 | rows, header repeated in every chunk (.xlsx: per sheet)               |

Every chunk is at most `chunk_chars` long. SSNs (123-45-6789 or 123 45 6789)
are masked to their last four digits before anything is stored or embedded;
EINs (12-3456789) are kept. Bare nine-digit numbers are not touched: they are
as often account numbers or CUSIPs.

*Usage:*
>>> draft = await read_file(pipeline, data, "stack.pdf", chunk_chars=1500, client="Okafor")
>>> [(c.document_type, c.page_number) for c in draft.chunks]
[('w2', 1), ('1099_nec', 2), ('text', 4)]
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path

from agent.rag import ChunkDraft, DocumentDraft
from agent.schema import FileType
from core.layout import lines_of
from core.parser import ConversionFailed, ParsedDocument, UnsupportedDocument, sniff
from core.pipeline import TaxDocumentPipeline
from core.schema import FormResult

TEXT_SUFFIXES = {".txt": FileType.TXT, ".md": FileType.MD, ".csv": FileType.CSV, ".xlsx": FileType.XLSX}
_SSN = re.compile(r"\b\d{3}([- ])\d{2}\1(\d{4})\b")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def mask_ssns(text: str) -> str:
    """*Usage:*
    >>> mask_ssns("SSN 412-56-7890, EIN 84-1736295")
    'SSN XXX-XX-7890, EIN 84-1736295'
    """
    return _SSN.sub(r"XXX-XX-\2", text)


def file_type(filename: str, data: bytes) -> FileType:
    try:
        media_type, _ = sniff(data)
        return FileType.PDF if media_type == "application/pdf" else FileType.IMAGE
    except UnsupportedDocument:
        kind = TEXT_SUFFIXES.get(Path(filename).suffix.lower())
        if kind is None:
            raise UnsupportedDocument(
                "Unsupported file type. Upload a PDF, an image, or a .txt, .md, .csv or .xlsx file.") from None
        return kind


# --- Packing lines into chunks ------------------------------------------------------------


def pack(lines: list[str], chunk_chars: int, head: str = "") -> list[str]:
    """Lines into chunks of at most `chunk_chars`, each starting with `head`; a line longer than a chunk is cut."""
    room = max(chunk_chars - len(head) - 1, 50)
    pieces = [line[i:i + room] for line in lines for i in range(0, max(len(line), 1), room)]
    chunks, current, size = [], [], 0
    for piece in pieces:
        if current and size + len(piece) + 1 > room:
            chunks.append(current)
            current, size = [], 0
        current.append(piece)
        size += len(piece) + 1
    if current:
        chunks.append(current)
    return ["\n".join(([head] if head else []) + c).strip() for c in chunks if any(x.strip() for x in c)]


def text_chunks(text: str, chunk_chars: int, **meta) -> list[ChunkDraft]:
    """Paragraphs (blank-line separated) packed into chunks."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    lines = [line for p in paragraphs for line in (p.splitlines() + [""])]
    return [ChunkDraft(content=c, document_type="text", **meta) for c in pack(lines, chunk_chars)]


def markdown_chunks(text: str, chunk_chars: int) -> list[ChunkDraft]:
    """One or more chunks per heading section; the section title and level are kept."""
    sections: list[tuple[str, int | None, list[str]]] = [("", None, [])]
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            sections.append((heading.group(2), len(heading.group(1)), []))
        else:
            sections[-1][2].append(line)
    return [chunk for title, level, lines in sections
            for chunk in text_chunks("\n".join(lines), chunk_chars, section_title=title, header_level=level)]


def row_chunks(rows: list[list[str]], chunk_chars: int, sheet_name: str | None = None) -> list[ChunkDraft]:
    """Table rows as `a, b, c` lines; the first row is the header, repeated in every chunk."""
    lines = [", ".join(cell.strip() for cell in row) for row in rows if any(cell.strip() for cell in row)]
    if not lines:
        return []
    head, body = lines[0], lines[1:] or [""]
    return [ChunkDraft(content=c, document_type="text", section_title=sheet_name or "", sheet_name=sheet_name)
            for c in pack(body, chunk_chars, head=head)]


def xlsx_chunks(data: bytes, chunk_chars: int) -> list[ChunkDraft]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types for damaged files
        raise ConversionFailed(f"Unreadable spreadsheet: {exc}") from exc
    try:
        chunks = []
        for sheet in book.worksheets:
            rows = [["" if v is None else str(v) for v in row] for row in sheet.iter_rows(values_only=True)]
            chunks += row_chunks(rows, chunk_chars, sheet_name=sheet.title)
        return chunks
    finally:
        book.close()


# --- Scanned and digital documents -------------------------------------------------------------


def page_chunks(doc: ParsedDocument, pages: set[int], chunk_chars: int) -> list[ChunkDraft]:
    """The text of `pages`, line by line in reading order."""
    chunks = []
    for page in sorted(pages):
        lines = [" ".join(b.text for b in line) for line in lines_of([b for b in doc.blocks if b.page == page])]
        chunks += [ChunkDraft(content=c, document_type="text", section_title=f"Page {page}", page_number=page)
                   for c in pack(lines, chunk_chars)]
    return chunks


def _lines(value, path: str = "") -> list[str]:
    """Flatten extracted fields to `path: value` lines; a row of a list stays on one line."""
    if isinstance(value, dict):
        return [line for k, v in value.items() for line in _lines(v, f"{path}.{k}" if path else k)]
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out += [f"{path}[{i}]: " + "; ".join(_lines(v))] if isinstance(v, dict) else _lines(v, f"{path}[{i}]")
        return out
    if value is None or value == "":
        return []
    return [f"{path}: {value}"]


def form_chunks(form: FormResult, chunk_chars: int) -> list[ChunkDraft]:
    """An extracted form as `path: value` lines; the heading line (form, year, pages) leads every chunk."""
    fields = form.fields.model_dump(mode="json", exclude_none=True)
    year = fields.get("tax_year")
    pages = ", ".join(map(str, form.pages))
    head = f"{form.form_name}" + (f", tax year {year}" if year else "") + f" (page {pages})"
    return [ChunkDraft(content=c, document_type=form.document_type, section_title=form.form_name or "",
                       page_number=form.pages[0] if form.pages else None, tax_year=year)
            for c in pack(_lines(fields), chunk_chars, head=head)]


async def scanned_chunks(pipeline: TaxDocumentPipeline, data: bytes, filename: str, extract: bool,
                         chunk_chars: int) -> list[ChunkDraft]:
    if not extract:
        doc = await pipeline.parser.parse(data, filename, max_pages=pipeline.settings.bulk_max_pages)
        return page_chunks(doc, {b.page for b in doc.blocks}, chunk_chars)
    result, doc = await pipeline.extract_file(data, filename)
    forms = [f for f in result.forms if f.status == "ok"]
    handled = {p for f in result.forms if f.status != "unsupported" for p in f.pages}  # duplicates are copies: skipped
    return ([c for f in forms for c in form_chunks(f, chunk_chars)]
            + page_chunks(doc, {b.page for b in doc.blocks} - handled, chunk_chars))


async def read_file(pipeline: TaxDocumentPipeline, data: bytes, filename: str, *, chunk_chars: int,
                    extract: bool = True, title: str | None = None, location: str | None = None,
                    client: str | None = None, tax_year: int | None = None) -> DocumentDraft:
    kind = file_type(filename, data)
    if kind in (FileType.PDF, FileType.IMAGE):
        chunks = await scanned_chunks(pipeline, data, filename, extract, chunk_chars)
    elif kind is FileType.XLSX:
        chunks = xlsx_chunks(data, chunk_chars)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnsupportedDocument(f"{filename} is not UTF-8 text.") from None
        if kind is FileType.MD:
            chunks = markdown_chunks(text, chunk_chars)
        elif kind is FileType.CSV:
            chunks = row_chunks(list(csv.reader(io.StringIO(text))), chunk_chars)
        else:
            chunks = text_chunks(text, chunk_chars)
    if not chunks:
        raise ConversionFailed("No text could be read from the document.")
    for chunk in chunks:
        chunk.content = mask_ssns(chunk.content)
    return DocumentDraft(title=title or Path(filename).stem, type=kind, length_bytes=len(data),
                         location=location or filename, content_hash=hashlib.sha256(data).hexdigest(),
                         client=client, tax_year=tax_year, chunks=chunks)
