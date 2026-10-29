"""generate_samples.py

Render filled, IRS-style test documents for every form in `core.forms.FORMS`
plus a ground-truth JSON for each.

Output (default ``tests/samples``):

    <form>.pdf            digitally generated PDF (text layer)
    <form>_scan.png       the same page rasterised, skewed and noised (needs OCR);
                          multi-page forms become an image-only <form>_scan.pdf
    <form>.json           expected `fields` payload
    not_a_tax_form.pdf    negative sample for the classifier
    consolidated_1099.pdf brokerage statement: 1099-DIV + 1099-INT + 1099-B (3 pages)
    stack.pdf             W-2, 1099-NEC, 1098-T and an invoice bound into one file
    <name>_scan.pdf       image-only versions of the two above
    <name>.forms.json     expected forms of a multi-form file: [{document_type, pages, fields}]

Checkboxes are drawn the way each real form prints them (W-2 box 13 below its
labels, 1098-T / 1099-R to the right of the label, others to the left) and
marked with a typed X, a vector X or a tick, so the checkbox reader is tested
on every layout.

*Usage:*
    uv run python -m scripts.generate_samples [--out tests/samples]
"""

from __future__ import annotations

import argparse
import json
import random
import typing
from decimal import Decimal
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pypdfium2 import PdfDocument
from PIL import Image, ImageFilter
from pydantic import BaseModel
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from core.forms import FORMS, BrokerTransaction, InformationReturn, Party, TaxForm
from scripts.sample_data import ACCOUNT, BROKER, CONSOLIDATED, INVESTOR, SAMPLES, STACK

PAGE_W, PAGE_H = letter
MARGIN = 30
LABEL_FONT, LABEL_SIZE = "Helvetica", 6.5
VALUE_FONT, VALUE_SIZE = "Courier-Bold", 9.5
BOX_H = 30
ROW_FONT, ROW_SIZE, ROW_H = "Helvetica", 7.5, 11

# form id -> (where the square sits relative to its label, how the box is marked)
CHECK_STYLE = {
    "w2": ("below", "vector"),
    "1098_t": ("right", "text"),
    "1099_r": ("right", "tick"),
    "k1_1065": ("left", "vector"),
    "1099_k": ("left", "tick"),
}


# --- Value formatting ----------------------------------------------------------


def fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "X" if value else ""
    if isinstance(value, Decimal):
        text = f"{abs(value):,.2f}"
        return f"({text})" if value < 0 else text
    if hasattr(value, "strftime"):
        return value.strftime("%m/%d/%Y")
    return str(value)


def address_lines(p: Party) -> list[str]:
    lines = [p.name or ""]
    if p.address:
        a = p.address
        lines.append(a.street or "")
        lines.append(", ".join(x for x in (a.city, f"{a.state or ''} {a.postal_code or ''}".strip()) if x))
    if p.phone:
        lines.append(p.phone)
    return [line for line in lines if line]


def field_kind(annotation) -> str:
    """'party' | 'rows' | 'model' | 'check' | 'scalar'."""
    args = typing.get_args(annotation)
    inner = [a for a in args if a is not type(None)] or [annotation]
    origin = typing.get_origin(annotation)
    if origin is list:
        return "rows"
    if Party in inner:
        return "party"
    if any(isinstance(a, type) and issubclass(a, BaseModel) for a in inner):
        return "model"
    if bool in args:  # Optional[bool]; pydantic strips the Annotated metadata
        return "check"
    return "scalar"


# --- Drawing -------------------------------------------------------------------


class Painter:
    def __init__(self, canvas: Canvas):
        self.c = canvas

    def label(self, x, y, w, text, size=LABEL_SIZE):
        lines = simpleSplit(text, LABEL_FONT, size, w - 4)
        self.c.setFont(LABEL_FONT, size)
        for i, line in enumerate(lines[:3]):
            self.c.drawString(x + 2, y - size - 1 - i * (size + 1), line)
        return len(lines[:3]) * (size + 1)

    def value_box(self, x, y, w, h, label, value):
        self.c.rect(x, y - h, w, h)
        self.label(x, y, w, label)
        self.c.setFont(VALUE_FONT, VALUE_SIZE)
        self.c.drawRightString(x + w - 4, y - h + 4, fmt(value))

    def check_box(self, x, y, w, h, label, value, style=("left", "text")):
        placement, mark = style
        self.c.rect(x, y - h, w, h)
        if placement == "below":  # label on top, square under it (W-2 box 13)
            self.label(x, y, w, label)
            bx, by = x + 6, y - h + 3
        elif placement == "right":  # label left, square at the right end of the box
            self.label(x, y - h + 15, w - 16, label)
            bx, by = x + w - 12, y - h + 4
        else:
            self.label(x + 12, y - h + 15, w - 14, label)
            bx, by = x + 3, y - h + 4
        self.c.rect(bx, by, 8, 8)
        if not value:
            return
        if mark == "text":
            self.c.setFont(VALUE_FONT, 9)
            self.c.drawString(bx + 1, by + 1, "X")
        elif mark == "vector":
            self.c.setLineWidth(1.2)
            self.c.line(bx + 1.5, by + 1.5, bx + 6.5, by + 6.5)
            self.c.line(bx + 1.5, by + 6.5, bx + 6.5, by + 1.5)
            self.c.setLineWidth(1)
        else:  # tick
            self.c.setLineWidth(1.2)
            self.c.line(bx + 1.5, by + 4, bx + 3.5, by + 1.5)
            self.c.line(bx + 3.5, by + 1.5, bx + 7, by + 7)
            self.c.setLineWidth(1)

    def text_block(self, x, y, w, h, label, lines):
        self.c.rect(x, y - h, w, h)
        used = self.label(x, y, w, label)
        self.c.setFont(VALUE_FONT, VALUE_SIZE)
        for i, line in enumerate(lines):
            self.c.drawString(x + 6, y - used - 12 - i * 11, line)


def draw_header(p: Painter, form: TaxForm, y: float) -> float:
    c = p.c
    title = type(form).TITLE
    words = title.split(" ")
    head, name = " ".join(words[:2]), " ".join(words[2:])  # "Form W-2" / "Schedule K-1"
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN, y - 16, head)
    c.setFont("Helvetica", 9)
    for i, line in enumerate(simpleSplit(name, "Helvetica", 9, 330)):
        c.drawString(MARGIN + 110, y - 12 - i * 10, line)
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(PAGE_W - MARGIN, y - 16, str(form.tax_year or ""))
    c.setFont("Helvetica", 7)
    c.drawRightString(PAGE_W - MARGIN, y - 26, "Copy B  For Recipient   OMB No. 1545-0000")
    c.drawString(MARGIN, y - 36, "Department of the Treasury - Internal Revenue Service")
    y -= 44
    if isinstance(form, InformationReturn):
        p.check_box(MARGIN, y, 120, 18, "CORRECTED (if checked)", form.corrected)
        y -= 22
    return y


def render(form: TaxForm, path: Path) -> None:
    c = Canvas(str(path), pagesize=letter)
    p = Painter(c)
    style = CHECK_STYLE.get(type(form).FORM_ID, ("left", "text"))
    y = draw_header(p, form, PAGE_H - MARGIN)
    half = (PAGE_W - 2 * MARGIN) / 2
    left_y = right_y = y

    scalars, checks, tables, models = [], [], [], []
    for name, info in type(form).model_fields.items():
        if name in ("tax_year", "corrected"):
            continue
        extra = info.json_schema_extra or {}
        value = getattr(form, name)
        kind = field_kind(info.annotation)
        if kind == "party":
            if value is None:
                continue
            h = 26 + 11 * len(address_lines(value))
            p.text_block(MARGIN, left_y, half, h, extra["label"], address_lines(value))
            left_y -= h
            if extra.get("tin_label"):
                p.value_box(MARGIN, left_y, half, BOX_H, extra["tin_label"], value.tin)
                left_y -= BOX_H
        elif kind == "rows" and extra.get("table"):
            tables.append((info, value))
        elif kind == "rows":
            tables.append((info, value))
        elif kind == "model":
            models.append((info, value))
        elif kind == "check":
            checks.append((extra.get("box"), extra.get("label", name), value))
        else:
            scalars.append((extra.get("box"), extra.get("label", name), value))

    # Numbered boxes: two columns on the right, then four columns full width.
    items = [("v", *s) for s in scalars] + [("c", *s) for s in checks]
    col = 0
    for kind, num, label, value in items:
        text = f"{num}  {label}" if num else label
        if right_y > left_y:  # beside the party blocks
            x, w = MARGIN + half + (col % 2) * half / 2, half / 2
            ncols = 2
        else:
            x, w = MARGIN + (col % 4) * half / 2, half / 2
            ncols = 4
        if kind == "c":
            p.check_box(x, right_y, w, BOX_H, text, value, style)
        else:
            p.value_box(x, right_y, w, BOX_H, text, value)
        col += 1
        if col % ncols == 0:
            right_y -= BOX_H
            col = 0
            if right_y < MARGIN + 80:
                c.showPage()
                right_y = left_y = PAGE_H - MARGIN
    if col:
        right_y -= BOX_H
    y = min(left_y, right_y) - 6

    for info, value in models:
        if value is None:
            continue
        p.label(MARGIN, y, PAGE_W, (info.description or "").upper(), size=7)
        y -= 10
        sub = type(value).model_fields
        for i, (name, sinfo) in enumerate(sub.items()):
            x = MARGIN + (i % 3) * (PAGE_W - 2 * MARGIN) / 3
            p.value_box(x, y, (PAGE_W - 2 * MARGIN) / 3, BOX_H, (sinfo.description or name).replace("Item L: ", ""), getattr(value, name))
            if i % 3 == 2:
                y -= BOX_H
        y -= BOX_H + 6

    for info, value in tables:
        if (info.json_schema_extra or {}).get("table"):
            y = draw_table(c, y, info, value)
            continue
        sub_model = typing.get_args(info.annotation)[0]
        cols = list(sub_model.model_fields.items())
        width = (PAGE_W - 2 * MARGIN) / len(cols)
        n_rows = max(len(value), 1)
        if y - BOX_H * n_rows - 12 < MARGIN:
            c.showPage()
            y = PAGE_H - MARGIN
        p.label(MARGIN, y, PAGE_W, info.json_schema_extra["label"], size=7)
        y -= 10
        for r in range(n_rows):
            row = value[r] if r < len(value) else None
            for i, (name, sinfo) in enumerate(cols):
                text = sinfo.description or name.replace("_", " ").title()
                p.value_box(MARGIN + i * width, y, width, BOX_H, text, getattr(row, name) if row else None)
            y -= BOX_H
        y -= 6

    c.setFont("Helvetica", 6)
    c.drawString(MARGIN, MARGIN - 12, f"{type(form).TITLE}  (keep for your records)  www.irs.gov")
    c.save()


def _columns_with_data(model, rows) -> list[tuple[str, Any]]:
    return [(name, info) for name, info in model.model_fields.items()
            if (info.json_schema_extra or {}).get("columns") and any(getattr(r, name) is not None for r in rows)]


def draw_header_row(c: Canvas, y: float, cols, widths, show_lead_label: bool = True) -> float:
    """Column headers wrapped inside their columns; returns the y below them."""
    c.setFont(LABEL_FONT, LABEL_SIZE)
    x, lowest = MARGIN, y
    for (name, info), w in zip(cols, widths):
        lead = (info.json_schema_extra or {}).get("lead")
        if lead and not show_lead_label:
            x += w
            continue
        label = info.description
        lines = simpleSplit(label, LABEL_FONT, LABEL_SIZE, w - 6)
        for i, line in enumerate(lines[:3]):
            c.drawString(x + 2, y - LABEL_SIZE - i * (LABEL_SIZE + 1), line)
        lowest = min(lowest, y - LABEL_SIZE - (len(lines[:3]) - 1) * (LABEL_SIZE + 1))
        x += w
    c.line(MARGIN, lowest - 3, PAGE_W - MARGIN, lowest - 3)
    return lowest - 6


def draw_rows(c: Canvas, y: float, cols, widths, rows, lead_width_wrap: bool = True) -> float:
    c.setFont(ROW_FONT, ROW_SIZE)
    for row in rows:
        x, used = MARGIN, 1
        for (name, info), w in zip(cols, widths):
            text = fmt(getattr(row, name))
            if (info.json_schema_extra or {}).get("lead"):
                lines = simpleSplit(text, ROW_FONT, ROW_SIZE, w - 8) if lead_width_wrap else [text]
                for i, line in enumerate(lines):
                    c.drawString(x + 2, y - ROW_SIZE - i * ROW_H, line)
                used = max(used, len(lines))
            elif isinstance(getattr(row, name), Decimal):
                c.drawRightString(x + w - 4, y - ROW_SIZE, text)
            else:
                c.drawString(x + 2, y - ROW_SIZE, text)
            x += w
        y -= used * ROW_H
    return y


def draw_table(c: Canvas, y: float, info, rows) -> float:
    """A `table(...)` field: one header row, then one printed line per row (1095-A parts II and III)."""
    model = typing.get_args(info.annotation)[0]
    cols = _columns_with_data(model, rows)
    first = (PAGE_W - 2 * MARGIN) * 0.22
    widths = [first] + [((PAGE_W - 2 * MARGIN) - first) / (len(cols) - 1)] * (len(cols) - 1)
    if y - ROW_H * (len(rows) + 4) < MARGIN:
        c.showPage()
        y = PAGE_H - MARGIN
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN, y - 9, info.json_schema_extra["label"])
    y = draw_header_row(c, y - 14, cols, widths, show_lead_label=model.__name__ != "MonthlyCoverage")
    return draw_rows(c, y, cols, widths, rows) - 8


def render_consolidated(path: Path) -> list[dict]:
    """
    A brokerage "Tax Reporting Statement": account header, then 1099-DIV and
    1099-INT sections on page 1, then 1099-B detail by reporting category
    over the following pages. Returns the expected forms.
    """
    forms = {k: FORMS[k].model_validate(v) for k, v in CONSOLIDATED.items()}
    c = Canvas(str(path), pagesize=letter)
    p = Painter(c)
    y = PAGE_H - MARGIN

    def page_header(y: float) -> float:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(MARGIN, y - 14, "2025 TAX REPORTING STATEMENT")
        c.setFont("Helvetica", 8)
        c.drawRightString(PAGE_W - MARGIN, y - 12, f"Account {ACCOUNT}")
        return y - 24

    y = page_header(y)
    left = [BROKER["name"], BROKER["address"]["street"],
            f"{BROKER['address']['city']}, {BROKER['address']['state']} {BROKER['address']['postal_code']}", BROKER["phone"]]
    right = [INVESTOR["name"], INVESTOR["address"]["street"],
             f"{INVESTOR['address']['city']}, {INVESTOR['address']['state']} {INVESTOR['address']['postal_code']}"]
    half = (PAGE_W - 2 * MARGIN) / 2
    p.text_block(MARGIN, y, half, 74, "PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", left)
    p.text_block(MARGIN + half, y, half, 74, "RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", right)
    y -= 74
    p.value_box(MARGIN, y, half / 2, BOX_H, "PAYER'S TIN", BROKER["tin"])
    p.value_box(MARGIN + half / 2, y, half / 2, BOX_H, "RECIPIENT'S TIN", INVESTOR["tin"])
    p.value_box(MARGIN + half, y, half, BOX_H, "Account number (see instructions)", ACCOUNT)
    y -= BOX_H + 18

    def box_section(y: float, form: TaxForm) -> float:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN, y - 11, f"2025 {type(form).TITLE}")
        c.setFont("Helvetica", 6.5)
        c.drawRightString(PAGE_W - MARGIN, y - 10, "Copy B for Recipient  OMB No. 1545-0000")
        y -= 18
        items = [(info.json_schema_extra["box"], info.json_schema_extra["label"], getattr(form, name))
                 for name, info in type(form).model_fields.items()
                 if isinstance(info.json_schema_extra, dict) and info.json_schema_extra.get("box")
                 and not isinstance(getattr(form, name), (bool, list)) and getattr(form, name) is not None]
        for i, (num, label, value) in enumerate(items):
            col = i % 2
            x = MARGIN + col * half
            c.setFont(LABEL_FONT, 7.5)
            c.drawString(x + 2, y - 9, f"{num}  {label}")
            c.setFont(VALUE_FONT, 9)
            c.drawRightString(x + half - 10, y - 9, fmt(value))
            if col == 1 or i == len(items) - 1:
                y -= 13
        return y - 14

    y = box_section(y, forms["1099_div"])
    y = box_section(y, forms["1099_int"])

    # 1099-B detail, one reporting category after another, over as many pages as needed.
    b = forms["1099_b"]
    cols = _columns_with_data(BrokerTransaction, b.transactions)
    first = (PAGE_W - 2 * MARGIN) * 0.26
    widths = [first] + [((PAGE_W - 2 * MARGIN) - first) / (len(cols) - 1)] * (len(cols) - 1)
    titles = {"A": "SHORT-TERM TRANSACTIONS FOR COVERED TAX LOTS [Report on Form 8949, Part I, with Box A checked]",
              "D": "LONG-TERM TRANSACTIONS FOR COVERED TAX LOTS [Report on Form 8949, Part II, with Box D checked]",
              "E": "LONG-TERM TRANSACTIONS FOR NONCOVERED TAX LOTS [Report on Form 8949, Part II, with Box E checked]"}
    footer = lambda: (c.setFont("Helvetica", 6), c.drawString(MARGIN, MARGIN - 12, f"Account {ACCOUNT}  Page {c.getPageNumber()}"))

    def new_b_page(continued: bool) -> float:
        footer()
        c.showPage()
        y = page_header(PAGE_H - MARGIN)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN, y - 11, "Form 1099-B" + (" (continued)" if continued else "") +
                     " Proceeds From Broker and Barter Exchange Transactions")
        return y - 18

    y = new_b_page(False)
    rows_on_page = 0
    for box_letter in ("A", "D", "E"):
        rows = [t for t in b.transactions if t.form_8949_box == box_letter]
        if y < MARGIN + 120 or rows_on_page > 14:
            y, rows_on_page = new_b_page(True), 0
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(MARGIN, y - 9, titles[box_letter])
        y = draw_header_row(c, y - 13, cols, widths)
        for t in rows:
            if y < MARGIN + 40 or rows_on_page >= 16:
                y, rows_on_page = new_b_page(True), 0
                y = draw_header_row(c, y, cols, widths)
            y = draw_rows(c, y, cols, widths, [t])
            rows_on_page += 1
        total = sum((t.proceeds for t in rows), Decimal(0))
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(MARGIN + 2, y - 9, f"Total {titles[box_letter].split(' [')[0].title()}")
        c.drawRightString(MARGIN + sum(widths[:[n for n, _ in cols].index("proceeds") + 1]) - 4, y - 9, fmt(total))
        y -= 22
    footer()
    c.save()
    pages = len(PdfDocument(str(path)))
    out = []
    for i, form_id in enumerate(("1099_div", "1099_int", "1099_b")):
        out.append({"document_type": form_id, "pages": [1] if i < 2 else list(range(2, pages + 1)),
                    "fields": forms[form_id].model_dump(mode="json")})
    return out


def bind(paths: list[Path], out: Path) -> None:
    """Concatenate PDFs into one file (a client's stack of documents)."""
    merged = PdfDocument.new()
    for path in paths:
        source = PdfDocument(str(path))
        merged.import_pages(source)
    merged.save(str(out))


def render_not_a_form(path: Path) -> None:
    c = Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(MARGIN, PAGE_H - 60, "INVOICE #10442")
    c.setFont("Helvetica", 10)
    lines = [
        "Greenway Landscaping Co. - 410 Oak St, Madison, WI 53703",
        "Bill to: Harbor Coffee Roasters, 22 Pier Rd, Madison, WI 53704",
        "Date: 09/12/2025      Due: 10/12/2025",
        "",
        "Lawn maintenance (September)          4 visits     $480.00",
        "Hedge trimming                        1            $150.00",
        "Mulch, 6 cubic yards                  6            $270.00",
        "",
        "Subtotal $900.00     Sales tax (5.5%) $49.50     Total due $949.50",
        "Thank you for your business! Please remit payment within 30 days.",
    ]
    for i, line in enumerate(lines):
        c.drawString(MARGIN, PAGE_H - 100 - i * 16, line)
    c.save()


def scan(pdf_path: Path, out_stem: Path, seed: int) -> Path:
    """
    Rasterise at 150 DPI and degrade like an office scanner. One page -> PNG;
    several pages -> an image-only PDF (no text layer, so it needs OCR too).
    """
    rng = random.Random(seed)
    pages = []
    for page in pdfium.PdfDocument(str(pdf_path)):
        img = page.render(scale=150 / 72).to_pil().convert("L")
        img = img.rotate(rng.uniform(-0.8, 0.8), expand=True, fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        img = Image.blend(img, Image.effect_noise(img.size, 18), 0.08)
        pages.append(img)
    if len(pages) == 1:
        out = out_stem.with_suffix(".png")
        pages[0].save(out, optimize=True)
    else:
        out = out_stem.with_suffix(".pdf")
        pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("tests/samples"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for i, (form_id, data) in enumerate(SAMPLES.items()):
        form = FORMS[form_id].model_validate(data)
        pdf = args.out / f"{form_id}.pdf"
        render(form, pdf)
        for stale in args.out.glob(f"{form_id}_scan.*"):
            stale.unlink()
        scanned = scan(pdf, args.out / f"{form_id}_scan", seed=i)
        truth = form.model_dump(mode="json")
        (args.out / f"{form_id}.json").write_text(json.dumps(truth, indent=2) + "\n")
        print(f"wrote {pdf.name}, {scanned.name}, {form_id}.json")

    render_not_a_form(args.out / "not_a_tax_form.pdf")
    print("wrote not_a_tax_form.pdf")

    consolidated = args.out / "consolidated_1099.pdf"
    multi = {"consolidated_1099": render_consolidated(consolidated)}
    stack = args.out / "stack.pdf"
    bind([args.out / (f"{f}.pdf" if f else "not_a_tax_form.pdf") for f in STACK], stack)
    multi["stack"] = [
        {"document_type": f or "unknown", "pages": [i + 1],
         "fields": json.loads((args.out / f"{f}.json").read_text()) if f else None}
        for i, f in enumerate(STACK)
    ]
    for j, name in enumerate(multi):
        scan(args.out / f"{name}.pdf", args.out / f"{name}_scan", seed=100 + j)
        (args.out / f"{name}.forms.json").write_text(json.dumps(multi[name], indent=2) + "\n")
        print(f"wrote {name}.pdf, {name}_scan.pdf, {name}.forms.json")


if __name__ == "__main__":
    main()
