"""layout.py

Turns a Docling document into label/value evidence for box-style forms.

Docling's reading order works for prose but separates a tax form's values
from their labels (all the amounts end up in a column at the end). This
module uses each text block's bounding box instead:

1. blocks that match a known label of the classified form are *anchors*;
2. every other block is a *value*, attached to the nearest anchor above-left
   of it (IRS boxes print the label top-left and the value below/right);
3. single-character check marks also pair with a label to their right.

The result reads like ``1 Wages, tips, other compensation => 68,412.55``
and is what the extraction LLM sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Iterable

from docling.datamodel.document import ConversionResult
from docling_core.types.doc import DoclingDocument, TableItem, TextItem

SCALE = 1000.0  # coordinates are normalised to 0..1000 of page width
LINE_TOLERANCE = 0.5  # share of a line's height two blocks may differ in top and still share a line
CHECK_MARKS = {"x", "X", "✓", "✔", "☒", "☑", "■"}
GENERIC_LABELS = ("amount", "description", "code", "state", "corrected", "void")


@dataclass
class Block:
    page: int
    left: float
    top: float
    right: float
    bottom: float
    text: str
    is_label: bool = False
    values: list["Block"] = field(default_factory=list)
    ocr: bool = False  # read by OCR (scan/photo region) rather than a PDF text layer


def blocks_from_conversion(result: ConversionResult) -> list[Block]:
    """
    Line-level text blocks with page-scaled, top-left-origin boxes.

    Uses the raw page cells (PDF text lines or OCR lines) rather than the
    layout model's clusters, which sometimes merge neighbouring boxes. PDF
    text lines are re-split on wide horizontal gaps using the word cells,
    because docling-parse joins everything that shares a baseline.
    Requires ``generate_parsed_pages=True``; otherwise falls back to the
    document's items.
    """
    blocks: list[Block] = []
    for page in result.pages:
        if page.size is None or not page.cells:
            continue
        k = SCALE / page.size.width
        words = page.parsed_page.word_cells if page.parsed_page is not None else []
        for cell in page.cells:
            box = cell.rect.to_bounding_box().to_top_left_origin(page.size.height)
            pieces = [] if cell.from_ocr else _split_on_gaps(box, words, page.size.height)
            for l, t, r, b, text in pieces or [(box.l, box.t, box.r, box.b, cell.text)]:
                text = re.sub(r"\s+", " ", text).strip()
                if text in CHECK_MARKS:
                    text = "X"
                if text:
                    blocks.append(Block(page.page_no, l * k, t * k, r * k, b * k, text, ocr=bool(cell.from_ocr)))
    if not blocks:
        return blocks_from_document(result.document)
    blocks.sort(key=lambda b: (b.page, round(b.top / 4), b.left))
    return blocks


def page_heights(result: ConversionResult) -> dict[int, float]:
    """Page height in block units (page width is 1000)."""
    return {
        page.page_no: page.size.height * SCALE / page.size.width
        for page in result.pages if page.size is not None and page.size.width
    }


def _split_on_gaps(line_box, words, page_height: float) -> list[tuple[float, float, float, float, str]]:
    """Regroup the words inside a PDF text line into runs separated by wide gaps."""
    inside = []
    for w in words:
        wb = w.rect.to_bounding_box().to_top_left_origin(page_height)
        cx, cy = (wb.l + wb.r) / 2, (wb.t + wb.b) / 2
        if line_box.l - 1 <= cx <= line_box.r + 1 and line_box.t - 1 <= cy <= line_box.b + 1:
            inside.append((wb, w.text))
    if len(inside) < 2:
        return []
    inside.sort(key=lambda x: x[0].l)
    height = max(line_box.b - line_box.t, 1.0)
    runs, current = [], [inside[0]]
    for prev, cur in zip(inside, inside[1:]):
        if cur[0].l - prev[0].r > max(height, 4.0):  # word spacing is ~0.3x height
            runs.append(current)
            current = []
        current.append(cur)
    runs.append(current)
    if len(runs) == 1:
        return []
    return [
        (run[0][0].l, min(w.t for w, _ in run), run[-1][0].r, max(w.b for w, _ in run), " ".join(t for _, t in run))
        for run in runs
    ]


def blocks_from_document(doc: DoclingDocument) -> list[Block]:
    """Fallback: every text item / table cell of the converted document."""
    blocks: list[Block] = []

    def add(page_no: int, bbox, text: str) -> None:
        text = re.sub(r"\s+", " ", text or "").strip()
        page = doc.pages.get(page_no)
        if not text or page is None or page.size is None:
            return
        b = bbox.to_top_left_origin(page.size.height)
        k = SCALE / page.size.width
        blocks.append(Block(page_no, b.l * k, b.t * k, b.r * k, b.b * k, text))

    for item, _level in doc.iterate_items():
        if isinstance(item, TableItem) and item.prov:
            for cell in item.data.table_cells:
                if cell.bbox is not None:
                    add(item.prov[0].page_no, cell.bbox, cell.text)
        elif isinstance(item, TextItem) and item.prov:
            add(item.prov[0].page_no, item.prov[0].bbox, item.text)

    blocks.sort(key=lambda b: (b.page, round(b.top / 4), b.left))
    return blocks


# --- Label matching ---------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


_LEAD_TOKENS = re.compile(r"^(?:[a-z0-9]{1,2}\s+)+")  # box numbers, letters, mark glyphs


def label_words(text: str) -> str:
    """
    Comparable words of a printed label: lower case, ASCII only, leading box
    numbers / letters / mark glyphs removed.

    *Usage:*
    >>> label_words("区H1 Domestic partner"), label_words("X 8 Check if at least half-time student")
    ('domestic partner', 'check if at least half time student')
    """
    norm = _norm(re.sub(r"[^\x00-\x7f]", " ", text))
    stripped = _LEAD_TOKENS.sub("", norm)
    return stripped if len(stripped) >= 4 else norm


def _strip_box_prefix(text: str) -> str:
    """'1 Wages, tips' -> 'wages tips'; 'Box 15: State' -> 'state'."""
    return re.sub(r"^(box(es)?\s+)?[0-9]{1,2}[a-z]?(\s*-\s*[0-9]{1,2}[a-z]?)?\b\s*|^[a-z]{1,2}[0-9]?\s+(?=[a-z])", "", _norm(text))


class LabelMatcher:
    def __init__(self, labels: Iterable[str]):
        cleaned = {_strip_box_prefix(label) for label in labels} | set(GENERIC_LABELS)
        self.labels = sorted((label for label in cleaned if len(label) >= 4), key=len, reverse=True)
        self.short = {label for label in cleaned if 2 <= len(label) < 4}  # 'May': matched only as a whole block

    def continues(self, first: str, second: str) -> bool:
        """True when `second` is the wrapped next line of the label `first`."""
        joined = _strip_box_prefix(f"{first} {second}")
        return any(joined in label for label in self.labels)

    def match(self, text: str) -> tuple[bool, str | None]:
        """Return (is_label, trailing_value) for a block's text."""
        norm = _norm(text)
        if not norm or not re.search(r"[a-z]{3,}", norm):
            return False, None
        stripped = _strip_box_prefix(text)
        if stripped in self.short:
            return True, None
        for label in self.labels:
            if stripped == label or (len(label) >= 8 and label in norm):
                return True, _trailing_value(text, label)
            if len(stripped) >= 8 and stripped in label:
                return True, None
            if SequenceMatcher(None, stripped, label).ratio() >= 0.82:
                return True, None
        return False, None


_VALUEISH = re.compile(r"[\d$(]|^[xX✓✔☒☑]$")


def _trailing_value(text: str, label: str) -> str | None:
    """Docling sometimes merges 'Federal income tax withheld 7,921.08' into one block."""
    words = text.split()
    for cut in range(len(words) - 1, 0, -1):
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        if _norm(head).endswith(label) and _VALUEISH.search(tail):
            return tail
    return None


# --- Pairing ------------------------------------------------------------------------


def _anchor_for(value: Block, anchors: list[Block]) -> Block | None:
    best, best_score = None, float("inf")
    for a in anchors:
        if a.page != value.page:
            continue
        # Check marks: a label on the same line, just to the right.
        if value.text in CHECK_MARKS and 0 <= a.left - value.right < 40:
            middle = (value.top + value.bottom) / 2
            if a.top - 8 <= middle <= a.bottom + 8:
                return a
        if a.top > value.top + 2 or a.left > value.left + 4:
            continue  # labels print top-left of their box: above, and starting left of the value
        vgap = value.top - a.top
        if vgap > 120:
            continue
        score = vgap + 0.15 * max(0.0, value.left - a.left)
        if score < best_score:
            best, best_score = a, score
    return best


_LEADING_MARK = re.compile(r"^(\S)\s+(\S.*)$")


def _split_leading_checkmarks(blocks: list[Block], matcher: LabelMatcher) -> list[Block]:
    """
    PDF text layers join a ticked box with its label ('X 8 Check if at least
    half-time student') when they sit close together. Split the mark off so it
    pairs with the label like any other check mark.
    """
    out: list[Block] = []
    for b in blocks:
        m = _LEADING_MARK.match(b.text)
        rest = m.group(2) if m else ""
        if m and m.group(1) in CHECK_MARKS and (matcher.match(rest)[0] or matcher.continues("", rest)):
            width = max(b.bottom - b.top, 4.0)
            out.append(Block(b.page, b.left, b.top, b.left + width, b.bottom, "X"))
            out.append(Block(b.page, b.left + width + 4, b.top, b.right, b.bottom, rest))
        else:
            out.append(b)
    return out


def _merge_wrapped_labels(blocks: list[Block], matcher: LabelMatcher) -> list[Block]:
    """Join a label's continuation lines ('...foreign postal' / 'code, and telephone no.')."""
    merged: list[Block] = []
    for b in blocks:
        host = next((
            a for a in reversed(merged[-8:])
            if a.is_label and a.page == b.page and abs(a.left - b.left) < 8
            and -0.6 * (b.bottom - b.top) <= b.top - a.bottom < 1.6 * max(b.bottom - b.top, 1.0)
            and matcher.continues(a.text, b.text)
        ), None)
        if host is None:
            merged.append(b)
            continue
        host.text = f"{host.text} {b.text}"
        host.right, host.bottom = max(host.right, b.right), max(host.bottom, b.bottom)
    return merged


def pair_blocks(blocks: list[Block], labels: Iterable[str]) -> list[Block]:
    """Mark anchors and attach values. Returns new blocks in reading order (inputs are not modified)."""
    matcher = LabelMatcher(labels)
    blocks = _split_leading_checkmarks([replace(b, values=[]) for b in blocks], matcher)
    synthetic: list[tuple[Block, str]] = []
    for b in blocks:
        b.is_label, trailing = matcher.match(b.text)
        b.values = []
        if trailing:
            synthetic.append((b, trailing))
    blocks = _merge_wrapped_labels(blocks, matcher)
    anchors = [b for b in blocks if b.is_label]
    for b in blocks:
        if b.is_label:
            continue
        anchor = _anchor_for(b, anchors)
        if anchor is not None:
            anchor.values.append(b)
    for anchor, text in synthetic:
        anchor.values.insert(0, Block(anchor.page, anchor.right, anchor.top, anchor.right, anchor.bottom, text))
    return blocks


def render_evidence(blocks: list[Block]) -> str:
    """
    One line per label with its values, unlabelled text kept in place.

    *Usage:*
    >>> print(render_evidence(pair_blocks(blocks, labels)))
    1 Wages, tips, other compensation => 68,412.55
    """
    attached = {id(v) for b in blocks for v in b.values}
    lines: list[str] = []
    page = None
    for b in blocks:
        if b.page != page:
            page = b.page
            lines.append(f"--- page {page} ---")
        if b.is_label:
            values = " | ".join(v.text for v in sorted(b.values, key=lambda v: (v.top, v.left)))
            lines.append(f"{b.text} => {values or '(blank)'}")
        elif id(b) not in attached:
            lines.append(b.text)
    return "\n".join(lines)


def lines_of(blocks: list[Block]) -> list[list[Block]]:
    """Group blocks into visual lines (per page, top to bottom, left to right)."""
    lines: list[list[Block]] = []
    for b in sorted(blocks, key=lambda b: (b.page, b.top, b.left)):
        if lines:
            last = lines[-1]
            ref = last[0]
            height = max(ref.bottom - ref.top, b.bottom - b.top, 4.0)
            if ref.page == b.page and abs(b.top - ref.top) <= LINE_TOLERANCE * height:
                last.append(b)
                continue
        lines.append([b])
    return [sorted(line, key=lambda b: b.left) for line in lines]
