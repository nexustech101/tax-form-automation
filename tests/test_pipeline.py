"""Pipeline regressions with stand-in parser and extractor: duplicates, partial conversions, missing pages.

The real splitting, classification by printed heading, identity and warnings run; only Docling and the LLM are faked.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from docling.datamodel.base_models import ConversionStatus

from core.config import get_settings
from core.extractor import Extraction
from core.layout import Block
from core.parser import ConversionFailed, DoclingParser, ParsedDocument
from core.pipeline import TaxDocumentPipeline

SALE_A = {"description": "100 SH APPLE INC", "proceeds": "19,450.00", "cost_basis": "17,210.00"}
SALE_B = {"description": "50 SH MSFT", "proceeds": "2,900.00", "cost_basis": "3,400.00"}
BROKER = {"tax_year": 2025, "payer": {"name": "Lakeview", "tin": "41-1228890"}, "account_number": "5521-0098"}


class FakeParser:
    loaded = True

    def __init__(self, doc: ParsedDocument):
        self.doc = doc

    async def parse(self, data, filename, max_pages=None):
        return self.doc


class FakeExtractor:
    """Returns the fields registered for the form's first page."""

    model = "fake"

    def __init__(self, fields_by_page: dict[int, dict]):
        self.fields_by_page = fields_by_page

    async def extract(self, form, blocks, page_image):
        page = min(b.page for b in blocks)
        return Extraction(form.model_validate(self.fields_by_page.get(page, {})), model=self.model)


def _doc(headings: dict[int, str], pages: int, **conversion) -> ParsedDocument:
    blocks = [Block(p, 30, 30, 400, 45, h) for p, h in headings.items()]
    blocks += [Block(p, 30, 100, 300, 110, f"line on page {p}") for p in headings]
    return ParsedDocument("f.pdf", "application/pdf", "0" * 64, pages, True, blocks,
                          {p: 1294.0 for p in range(1, pages + 1)}, 0, **conversion)


def _run(doc: ParsedDocument, fields_by_page: dict[int, dict]):
    settings = get_settings().model_copy(update={"tev_model": "none"})
    pipeline = TaxDocumentPipeline(settings, parser=FakeParser(doc), extractor=FakeExtractor(fields_by_page))
    return asyncio.run(pipeline.extract_bulk([(b"%PDF-", "f.pdf")], request_id="t")).files[0]


# --- duplicates -------------------------------------------------------------------------------


def test_bulk_keeps_1099bs_that_differ_only_in_transactions_or_year():
    heading = "Form 1099-B Proceeds From Broker and Barter Exchange Transactions"
    doc = _doc({1: heading, 2: heading, 3: heading, 4: heading}, 4)
    file = _run(doc, {
        1: {**BROKER, "transactions": [SALE_A]},
        2: {**BROKER, "transactions": [SALE_B]},          # same broker, account and year: other sales
        3: {**BROKER, "tax_year": 2024, "transactions": [SALE_A]},  # same sales, other year
        4: {**BROKER, "transactions": [SALE_A]},          # a true copy of page 1
    })
    assert [(f.status, f.duplicate_of) for f in file.forms] == [("ok", None), ("ok", None), ("ok", None), ("duplicate", 0)]


def test_bulk_never_marks_empty_extractions_as_duplicates():
    doc = _doc({1: "Form W-2 Wage and Tax Statement", 2: "Form W-2 Wage and Tax Statement"}, 2)
    file = _run(doc, {})  # the model read nothing from either page
    assert [f.status for f in file.forms] == ["ok", "ok"]


# --- partial conversions and missing pages ----------------------------------------------------------


def test_partial_conversion_is_reported_on_the_file_and_every_form():
    heading = "Schedule K-1 (Form 1065) Partner's Share of Income, Deductions, Credits, etc."
    doc = _doc({1: heading, 3: "continued statement"}, 4,
               conversion_errors=["Page 2: layout model failed"], missing_pages=[2, 4])
    file = _run(doc, {1: {"ordinary_business_income": "4,210.00"}})
    assert file.document.conversion == "partial"
    assert file.document.missing_pages == [2, 4]
    assert file.document.conversion_errors == ["Page 2: layout model failed"]
    (form,) = file.forms
    assert form.pages == [1, 3] and not form.validation.valid
    assert any("only partly converted" in w for w in form.validation.warnings)
    assert any("page(s) 2 inside this form" in w for w in form.validation.warnings)


def test_blank_page_between_single_page_forms_is_listed_but_flags_no_form():
    doc = _doc({1: "Form W-2 Wage and Tax Statement", 3: "Form 1099-NEC Nonemployee Compensation"}, 3, missing_pages=[2])
    file = _run(doc, {1: {"wages_tips_other_comp": "10.00"}, 3: {"nonemployee_compensation": "5.00"}})
    assert file.document.conversion == "complete" and file.document.missing_pages == [2]
    assert all(f.validation.valid for f in file.forms)


def _pdf(pages: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(612, 792)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


@pytest.fixture
def parser(monkeypatch):
    """DoclingParser with the Docling conversion replaced: `convert` sets what Docling returns."""
    parser = DoclingParser(get_settings())
    state = {}

    def convert(status, errors, pages_with_text):
        result = SimpleNamespace(status=status, errors=[SimpleNamespace(error_message=e) for e in errors])
        blocks = [Block(p, 10, 10, 100, 20, f"text {p}") for p in pages_with_text]
        state["result"], state["blocks"] = result, blocks

    monkeypatch.setattr(parser, "_convert", lambda *a: state["result"])
    monkeypatch.setattr("core.parser.blocks_from_conversion", lambda result: state["blocks"])
    monkeypatch.setattr("core.parser.page_heights", lambda result: {})
    parser.convert = convert
    return parser


def test_parser_reports_pages_docling_skipped_and_its_errors(parser):
    parser.convert(ConversionStatus.PARTIAL_SUCCESS, ["Page 2: timeout"], pages_with_text=[1, 3])
    doc = parser.parse_sync(_pdf(4), "statement.pdf")
    assert doc.pages == 4  # counted from the file, not from what Docling returned
    assert doc.missing_pages == [2, 4]
    assert doc.conversion_errors == ["Page 2: timeout"]


def test_parser_refuses_a_file_without_any_text(parser):
    parser.convert(ConversionStatus.SUCCESS, [], pages_with_text=[])
    with pytest.raises(ConversionFailed, match="No text could be read"):
        parser.parse_sync(_pdf(1), "blank.pdf")

