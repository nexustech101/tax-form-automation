"""segment.py

Splits one parsed file into the forms it contains: a client's stack of scans,
a W-2 followed by its state copy, or a consolidated brokerage statement
(1099-DIV, 1099-INT and 1099-B sections in one PDF).

Boundaries come from the printed form headings ("Form 1099-INT", "Schedule
K-1 (Form 1065)"), which every supported form carries:

* A page whose headings name one form type starts a new form, except a page
  that continues a multi-page form (`TaxForm.MULTI_PAGE`: 1099-B detail
  pages, K-1 supplemental statements) whose heading is not at the top.
  Page footers ("Form 1098-E ... (keep for your records)") count: OCR often
  garbles the big heading but reads the small footer.
* A page whose headings name several form types (a consolidated statement
  page) is cut into horizontal bands at the first heading of each type. The
  account header above the first band (payer, recipient, account number) is
  kept as shared context for every form in the file.
* Several headings of the same type on one page are copies (W-2 Copy B /
  Copy C / Copy 2) and stay one form.
* A page without a heading continues the previous form when that form is
  multi-page (or unidentified); after a single-page form it is a form of its
  own, identified by the classifier (an invoice in a stack, an unreadable
  heading). A file without any heading is one form.

Segments only carry a *hint* from the heading; the pipeline still classifies
each one.

*Usage:*
>>> segments = split(parsed_document)
>>> [(s.form_hint, s.pages) for s in segments]
[('1099_div', [1]), ('1099_int', [1]), ('1099_b', [2, 3, 4])]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.forms import FORMS
from core.layout import Block, lines_of
from core.parser import ParsedDocument

TOP_OF_PAGE = 0.2  # share of the page height where a continuing form's new instance must start
BAND_MARGIN = 6.0  # units above a heading that still belong to its band


@dataclass
class Heading:
    form_id: str
    page: int
    top: float
    text: str


@dataclass
class Segment:
    form_hint: str | None  # form id from the printed heading, if any
    pages: list[int]
    blocks: list[Block]
    context: list[Block] = field(default_factory=list)  # shared statement header, read before `blocks`

    @property
    def all_blocks(self) -> list[Block]:
        return self.context + self.blocks

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.all_blocks)

    @property
    def text_layer(self) -> bool:
        """False when any part of the form was read by OCR."""
        return not any(b.ocr for b in self.blocks)


def _patterns() -> list[tuple[str, re.Pattern, str, int]]:
    """(form id, designation regex, first words of the name, longest heading length) per form."""
    out = []
    for form_id, form in FORMS.items():
        m = re.match(r"(Form|Schedule)\s+(\S+)(\s+\(Form \d+\))?\s+(.*)", form.TITLE)
        assert m, f"{form_id}: TITLE must read 'Form <number> <name>'"
        number = m.group(2)
        # "1099-R" must not match "1099-RA"; plain "1098" must not match "1098-T".
        parts = [re.escape(p) for p in number.split("-")]
        code = r"\s*-?\s*".join(parts)
        follow = r"(?![\w-])" if "-" in number else r"(?!\s*-\s*[A-Za-z]\b)(?!\w)"
        regex = re.compile(rf"(?<![\w-]){code}{follow}", re.IGNORECASE)
        name_words = " ".join(re.sub(r"[^a-z ]", "", m.group(4).lower()).split()[:3])
        out.append((form_id, regex, name_words, len(form.TITLE) + 45))  # room for "(keep for your records)"
    return out


_PATTERNS = _patterns()


def _ocr_fixes(text: str) -> str:
    """Undo common OCR joins: 'Form1099-DIV' -> 'Form 1099-DIV', '1099-MISCMiscellaneous' -> '1099-MISC Miscellaneous'."""
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)
    return re.sub(r"(?i)\b(form|schedule)(?=[0-9A-Z])", r"\1 ", text)


def _heading(text: str) -> str | None:
    text = _ocr_fixes(text)
    for form_id, regex, name_words, max_len in _PATTERNS:
        m = regex.search(text)
        if not m or len(text) > max_len:
            continue
        before = " ".join(re.sub(r"[^a-z0-9 ]", " ", text[:m.start()].lower()).split())
        after = " ".join(re.sub(r"[^a-z ]", "", text[m.end():].lower()).split())
        if _PREFIX.fullmatch(before) or (_YEAR.fullmatch(before) and after.startswith(name_words)):
            return form_id
    return None


def find_headings(blocks: list[Block]) -> list[Heading]:
    """
    Blocks that print a form's heading or footer: "[2025] Form 1099-INT ...",
    or the number followed by the start of its name ("2025 1099-DIV
    Dividends and ..."). Only a year may come before it, so prose that
    mentions a form ("... instead of Form W-2 because ...") is not a heading.
    OCR splits are handled by also trying each pair of neighbouring blocks.
    """
    out = []
    for line in lines_of(blocks):
        candidates = [(b.text, b) for b in line] + [(f"{a.text} {b.text}", a) for a, b in zip(line, line[1:])]
        for text, block in candidates:
            form_id = _heading(text)
            if form_id:
                out.append(Heading(form_id, block.page, block.top, text))
                break
    return out


_PREFIX = re.compile(r"(\d{4} )?(irs )?(form|schedule)( [a-z])?")  # one stray OCR letter allowed
_YEAR = re.compile(r"(\d{4})?")


def _starts_new(current: Segment | None, heading: Heading, page_height: float) -> bool:
    if current is None or current.form_hint != heading.form_id:
        return True
    if FORMS[heading.form_id].MULTI_PAGE:
        return heading.top < TOP_OF_PAGE * page_height and "continued" not in heading.text.lower()
    return True


def split(document: ParsedDocument) -> list[Segment]:
    headings = find_headings(document.blocks)
    pages = sorted({b.page for b in document.blocks})
    if not headings:
        return [Segment(None, pages, list(document.blocks))] if pages else []

    by_page: dict[int, list[Block]] = {p: [] for p in pages}
    for b in document.blocks:
        by_page[b.page].append(b)

    segments: list[Segment] = []
    context: list[Block] = []
    current: Segment | None = None

    def start(form_id: str | None, page: int, blocks: list[Block]) -> Segment:
        segment = Segment(form_id, [page], blocks)
        segments.append(segment)
        return segment

    def extend(segment: Segment, page: int, blocks: list[Block]) -> None:
        if page not in segment.pages:
            segment.pages.append(page)
        segment.blocks.extend(blocks)

    for page in pages:
        blocks = by_page[page]
        height = document.page_heights.get(page, 1294.0)
        page_headings = sorted((h for h in headings if h.page == page), key=lambda h: h.top)
        types = list(dict.fromkeys(h.form_id for h in page_headings))

        if not types:
            if current is not None and (current.form_hint is None or FORMS[current.form_hint].MULTI_PAGE):
                extend(current, page, blocks)
            else:
                current = start(None, page, list(blocks))
            continue

        if len(types) == 1:
            first = page_headings[0]
            if _starts_new(current, first, height):
                current = start(first.form_id, page, list(blocks))
            else:
                extend(current, page, blocks)
            continue

        # Several form types on one page: cut into bands at the first heading of each type.
        firsts = [next(h for h in page_headings if h.form_id == t) for t in types]
        cuts = [h.top - BAND_MARGIN for h in firsts] + [float("inf")]
        above = [b for b in blocks if b.top < cuts[0]]
        if current is None and not segments:
            context = above  # statement header: payer, recipient, account number
        elif current is not None:
            extend(current, page, above)
        for heading, top, bottom in zip(firsts, cuts, cuts[1:]):
            band = [b for b in blocks if top <= b.top < bottom]
            if _starts_new(current, heading, height):
                current = start(heading.form_id, page, band)
            else:
                extend(current, page, band)

    for segment in segments:
        segment.context = context
    return segments
