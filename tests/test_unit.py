"""Fast tests: parsers, schemas, layout pairing, grounding. No models needed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.extractor import field_guide, ground, llm_schema, validate_leniently
from core.forms import FORMS, W2, Form1099NEC, ScheduleK1_1065, SSA1099, form_labels
from core.layout import Block, pair_blocks, render_evidence
from core.parser import UnsupportedDocument, sniff
from core.tev import NONE_OPTION, build_state, criteria
from core.utils import parse_acquired, parse_bool, parse_date, parse_money, parse_month, parse_percent, parse_tin


# --- value parsers ------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("$1,234.5", Decimal("1234.50")),
    ("(12.00)", Decimal("-12.00")),
    ("-840.00", Decimal("-840.00")),
    (68412.55, Decimal("68412.55")),
    ("3.672.21", Decimal("3672.21")), ("1.234,56", Decimal("1234.56")), ("68412.55", Decimal("68412.55")),
    ("", None), ("-", None), (None, None),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def test_parse_money_rejects_text():
    with pytest.raises(ValueError):
        parse_money("see statement")


@pytest.mark.parametrize("raw, expected", [
    ("123456789", "123-45-6789"),
    ("123-45-6789", "123-45-6789"),
    ("12-3456789", "12-3456789"),
    ("***-**-6789", "XXX-XX-6789"),
    ("xxx-xx-6789", "XXX-XX-6789"),
])
def test_parse_tin(raw, expected):
    assert parse_tin(raw) == expected


def test_parse_tin_rejects_wrong_length():
    with pytest.raises(ValueError):
        parse_tin("12345")


def test_parse_misc():
    assert parse_date("03/15/2021") == date(2021, 3, 15)
    assert parse_date("2021-03-15") == date(2021, 3, 15)
    assert parse_bool("X") is True and parse_bool("☐") is False and parse_bool(None) is None
    assert parse_percent("12.5%") == Decimal("12.5000")
    assert parse_acquired("Various") == "VARIOUS" and parse_acquired("01/15/2024") == date(2024, 1, 15)
    assert parse_month("21 January") == "January" and parse_month("Sept.") == "September"


# --- form schemas ---------------------------------------------------------------------


def test_every_form_has_metadata_and_schema():
    assert len(FORMS) == 15
    for form_id, form in FORMS.items():
        assert form.FORM_ID == form_id
        assert form.TITLE and form.HINT
        schema = form.model_json_schema(mode="serialization")
        assert schema["type"] == "object"
        assert form_labels(form), form_id
        assert field_guide(form), form_id
        llm = llm_schema(form)
        assert set(llm["required"]) == set(llm["properties"])


def test_money_serialises_as_number():
    w2 = W2.model_validate({"wages_tips_other_comp": "68,412.55", "retirement_plan": "X"})
    dumped = w2.model_dump(mode="json")
    assert dumped["wages_tips_other_comp"] == 68412.55
    assert dumped["retirement_plan"] is True


def test_validate_leniently_drops_only_bad_values():
    form, warnings = validate_leniently(W2, {
        "wages_tips_other_comp": "68,412.55",
        "federal_income_tax_withheld": "see attached",
        "employee": {"name": "Maria", "tin": "12"},
        "box12": [{"code": "D", "amount": "1,000.00"}],
    })
    assert form.wages_tips_other_comp == Decimal("68412.55")
    assert form.federal_income_tax_withheld is None
    assert form.employee.name == "Maria" and form.employee.tin is None
    assert form.box12[0].amount == Decimal("1000.00")
    assert len(warnings) == 2


def test_grounding_removes_values_not_in_document():
    form = Form1099NEC.model_validate({
        "nonemployee_compensation": "24,750.00",
        "federal_income_tax_withheld": "999.99",
        "payer": {"name": "Acme", "tin": "27-4410983"},
        "recipient": {"name": "Dev", "tin": "XXX-XX-4417"},
    })
    text = "PAYER'S TIN 27-4410983\nRECIPIENT'S TIN XXX-XX-4417\n1 Nonemployee compensation 24,750.00"
    dropped = ground(form, text)
    assert dropped == ["federal_income_tax_withheld"]
    assert form.nonemployee_compensation == Decimal("24750.00")
    assert form.payer.tin == "27-4410983"


def test_consistency_checks():
    ok = W2.model_validate({"social_security_wages": "1000", "social_security_tax_withheld": "62",
                            "medicare_wages_and_tips": "1000", "medicare_tax_withheld": "14.50"})
    assert ok.consistency_checks() == []
    bad = W2.model_validate({"social_security_wages": "1000", "social_security_tax_withheld": "10"})
    assert "6.2%" in bad.consistency_checks()[0]
    assert SSA1099.model_validate({"benefits_paid": "100", "benefits_repaid": "0", "net_benefits": "90"}).consistency_checks()
    k1 = ScheduleK1_1065.model_validate({"capital_account": {
        "beginning_capital_account": "100", "capital_contributed": "10", "current_year_net_income": "5",
        "withdrawals_and_distributions": "15", "ending_capital_account": "100"}})
    assert k1.consistency_checks() == []


# --- layout pairing -----------------------------------------------------------------------


def _b(left, top, text, right=None):
    return Block(1, left, top, right if right is not None else left + 6 * len(text), top + 10, text)


def test_pairing_attaches_right_aligned_values_to_label_in_same_box():
    blocks = [
        _b(10, 100, "a Employee's social security number"), _b(500, 100, "4 Social security tax withheld"),
        _b(390, 125, "412-56-7890"), _b(700, 125, "4,495.00"),
    ]
    text = render_evidence(pair_blocks(blocks, form_labels(W2)))
    assert "a Employee's social security number => 412-56-7890" in text
    assert "4 Social security tax withheld => 4,495.00" in text


def test_pairing_merges_wrapped_labels_and_checkmarks():
    blocks = [
        _b(10, 100, "2 Payer made direct sales totaling $5,000 or"),
        _b(10, 111, "more of consumer products to recipient for resale"),
        _b(400, 100, "X", right=406), _b(412, 100, "FATCA filing requirement"),
    ]
    text = render_evidence(pair_blocks(blocks, form_labels(Form1099NEC) + ["FATCA filing requirement"]))
    assert "for resale => (blank)" in text
    assert "FATCA filing requirement => X" in text


# --- classification input ---------------------------------------------------------------


def test_build_state_puts_form_titles_first_and_caps_length():
    text = "lots of noise\n" * 500 + "Form 1099-INT Interest Income\n"
    state = build_state(text, 300)
    assert state.startswith("Form 1099-INT Interest Income")
    assert len(state) <= 300


def test_criteria_cover_all_forms_and_none():
    assert set(criteria()) == set(FORMS) | {NONE_OPTION}


# --- upload sniffing -------------------------------------------------------------------------


def test_sniff_uses_magic_bytes():
    assert sniff(b"%PDF-1.7 ...")[0] == "application/pdf"
    assert sniff(b"\x89PNG\r\n\x1a\n....")[0] == "image/png"
    assert sniff(b"\xff\xd8\xff\xe0...")[0] == "image/jpeg"
    with pytest.raises(UnsupportedDocument):
        sniff(b"PK\x03\x04 a zip file")


def test_clean_separators():
    from core.extractor import clean_separators

    data = {"account_number": "SL-6619024 | 2", "rows": [{"code": "A | B"}], "name": "Plain"}
    warnings = clean_separators(data)
    assert data == {"account_number": "SL-6619024", "rows": [{"code": "A"}], "name": "Plain"}
    assert len(warnings) == 2


def test_json_in_text_field_is_flattened():
    from core.extractor import clean_separators

    data = {"distribution_codes": '[{"code": "7", "amount": null}]', "note": "[draft] keep"}
    warnings = clean_separators(data)
    assert data == {"distribution_codes": "7", "note": "[draft] keep"}
    assert len(warnings) == 1


# --- checkboxes (page image) --------------------------------------------------------------

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.checkboxes import checkbox_states, find_squares  # noqa: E402
from core.extractor import checkbox_labels  # noqa: E402
from core.forms import Form1098T, Form1099B, Form1099K, Form1095A  # noqa: E402

BOX = 13  # ~8 pt in block units; the test page is 1000 px wide, so 1 px = 1 unit


def _page():
    return np.full((1300, 1000), 255, np.uint8)


def _box(img, x, y, mark=None):
    cv2.rectangle(img, (x, y), (x + BOX, y + BOX), 0, 1)
    if mark == "x":  # vector X, no text
        cv2.line(img, (x + 3, y + 3), (x + BOX - 3, y + BOX - 3), 0, 2)
        cv2.line(img, (x + BOX - 3, y + 3), (x + 3, y + BOX - 3), 0, 2)
    elif mark == "tick":
        cv2.line(img, (x + 3, y + 7), (x + 6, y + BOX - 3), 0, 2)
        cv2.line(img, (x + 6, y + BOX - 3), (x + BOX - 2, y + 3), 0, 2)
    elif mark == "fill":
        cv2.rectangle(img, (x, y), (x + BOX, y + BOX), 0, -1)


def test_checkboxes_box_left_of_label_with_vector_x():
    img = _page()
    _box(img, 80, 100, "x")
    _box(img, 280, 100)
    blocks = [Block(1, 98, 100, 200, 112, "13 Retirement plan"), Block(1, 298, 100, 420, 112, "13 Third-party sick pay")]
    states = checkbox_states(blocks, checkbox_labels(W2), lambda page: img)
    assert states == {"retirement_plan": True, "third_party_sick_pay": False}


def test_checkboxes_below_labels_like_a_real_w2():
    img = _page()
    for x, mark in ((60, None), (210, "tick"), (360, None)):
        _box(img, x + 40, 130, mark)
    blocks = [Block(1, 60, 100, 160, 110, "Statutory employee"), Block(1, 210, 100, 300, 110, "Retirement plan"),
              Block(1, 360, 100, 470, 110, "Third-party sick pay")]
    states = checkbox_states(blocks, checkbox_labels(W2), lambda page: img)
    assert states == {"statutory_employee": False, "retirement_plan": True, "third_party_sick_pay": False}


def test_checkboxes_right_of_label_and_inline_mark_like_a_1098t():
    img = _page()
    _box(img, 905, 330, "fill")  # box 8: square at the right end of the line
    _box(img, 505, 380)  # box 9: square at the start of the line, text layer ran the label over it
    blocks = [Block(1, 732, 330, 890, 344, "8 Check if at least half-time student"),
              Block(1, 507, 380, 690, 394, "X 9 Checked if a graduate student")]
    states = checkbox_states(blocks, checkbox_labels(Form1098T), lambda page: img)
    assert states == {"at_least_half_time_student": True, "graduate_student": False}


def test_checkboxes_ignore_ocr_misreads():
    """OCR text says '冈' (ticked) and 'X' (empty box); the pixels decide."""
    img = _page()
    _box(img, 50, 445, "x")
    _box(img, 280, 448)
    blocks = [Block(1, 54, 446, 74, 464, "冈"), Block(1, 74, 445, 160, 458, "13 Retirement plan"),
              Block(1, 281, 448, 294, 459, "X"), Block(1, 296, 442, 400, 458, "13 Third-party sick pay")]
    states = checkbox_states(blocks, checkbox_labels(W2), lambda page: img)
    assert states == {"retirement_plan": True, "third_party_sick_pay": False}


def test_checkboxes_missing_label_or_box_is_left_to_the_model():
    img = _page()
    blocks = [Block(1, 98, 100, 200, 112, "13 Retirement plan")]  # no square printed anywhere
    assert checkbox_states(blocks, checkbox_labels(W2), lambda page: img) == {}
    assert checkbox_states([], checkbox_labels(W2), lambda page: img) == {}


def test_square_finder_rejects_letters():
    img = _page()
    # Printed-size glyphs (~9 pt at 150 DPI): round or open shapes fail the edge/corner tests.
    cv2.putText(img, "O D 0 B Q", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 1)
    cv2.putText(img, "O D 0 B Q", (100, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2)
    _box(img, 400, 185)
    squares = find_squares(img, page=1)
    assert len(squares) == 1 and squares[0].left == pytest.approx(400, abs=1)


# --- tables ------------------------------------------------------------------------------

from core.tables import read_table  # noqa: E402
from core.forms import BrokerTransaction, MonthlyCoverage  # noqa: E402


def _row(top, cells, page=1):
    return [Block(page, left, top, left + 8 * len(text), top + 10, text) for left, text in cells]


HEADER_1099B = [(40, "Description of property"), (300, "Date acquired"), (400, "Date sold or disposed"),
                (520, "Proceeds"), (620, "Cost or other basis"), (740, "Wash sale loss disallowed"),
                (880, "Gain or loss")]


def test_table_reads_1099b_rows_sections_and_wrapped_descriptions():
    blocks = (
        _row(80, [(40, "SHORT-TERM TRANSACTIONS FOR COVERED TAX LOTS - Form 8949 with Box A checked")])
        + _row(100, HEADER_1099B)
        + _row(110, [(40, "40 SH VANGUARD TOTAL STK"), (300, "06/19/2025"), (400, "09/04/2025"), (520, "9,959.91"),
                     (620, "7,212.40"), (880, "2,747.51")])
        + _row(120, [(40, "100 SH APPLE INC"), (300, "01/15/2025"), (400, "06/02/2025"), (520, "19,450.00"),
                     (620, "17,210.00"), (880, "2,240.00")])
        + _row(131, [(40, "CUSIP 037833100")])
        + _row(150, [(40, "Total Short-term"), (520, "19,450.00"), (620, "17,210.00")])
        + _row(200, [(40, "LONG-TERM TRANSACTIONS FOR NONCOVERED TAX LOTS - Box E")])
        + _row(30, HEADER_1099B, page=2)
        + _row(50, [(40, "50 SH TOTALENERGIES SE"), (300, "VARIOUS"), (400, "11/03/2025"), (520, "2,900.00"),
                    (620, "3,400.00"), (740, "120.00"), (880, "-380.00")], page=2)
    )
    table = read_table(blocks, BrokerTransaction)
    rows = [BrokerTransaction.model_validate(r) for r in table.rows]
    assert len(rows) == 3  # the Total line is not a sale; a fund named "... TOTAL ..." is
    v, a, e = rows
    assert v.description == "40 SH VANGUARD TOTAL STK"
    assert a.description == "100 SH APPLE INC CUSIP 037833100"
    assert (a.term, a.basis_reported_to_irs, a.form_8949_box) == ("short", True, "A")
    assert a.proceeds == Decimal("19450.00") and a.gain_or_loss == Decimal("2240.00")
    assert e.description.startswith("50 SH TOTALENERGIES")  # 'Total' inside a name is not a total line
    assert (e.term, e.basis_reported_to_irs, e.form_8949_box) == ("long", False, "E")
    assert e.date_acquired == "VARIOUS" and e.wash_sale_loss_disallowed == Decimal("120.00")


def test_table_unlabeled_month_column_and_annual_totals():
    blocks = (
        # Long headers wrap inside their columns; only the first line is needed.
        [Block(1, 300, 100, 460, 110, "A. Monthly enrollment premiums"),
         Block(1, 500, 100, 690, 110, "B. Monthly second lowest cost silver plan"),
         Block(1, 720, 100, 920, 110, "C. Monthly advance payment of premium tax credit")]
        + _row(120, [(40, "21 January"), (330, "450.00"), (530, "520.00"), (760, "300.00")])
        + _row(136, [(40, "22 February"), (330, "450.00"), (530, "520.00"), (760, "300.00")])
        + _row(152, [(40, "33 Annual Totals"), (330, "900.00"), (530, "1,040.00"), (760, "600.00")])
    )
    rows = [MonthlyCoverage.model_validate(r) for r in read_table(blocks, MonthlyCoverage).rows]
    assert [r.month for r in rows] == ["January", "February"]
    assert rows[1].advance_ptc == Decimal("300.00")


def test_table_without_header_reads_nothing():
    assert read_table(_row(100, [(40, "Proceeds"), (300, "1,000.00")]), BrokerTransaction).rows == []


# --- segmentation ------------------------------------------------------------------------

from core.parser import ParsedDocument  # noqa: E402
from core.segment import split  # noqa: E402


def _doc(blocks, pages):
    return ParsedDocument("f.pdf", "application/pdf", "0" * 64, pages, True, blocks,
                          {p: 1294.0 for p in range(1, pages + 1)}, 0)


def test_split_stack_of_forms_one_per_page():
    blocks = [Block(1, 30, 30, 200, 45, "Form W-2"), Block(1, 30, 100, 200, 110, "1 Wages 1,000.00"),
              Block(2, 30, 30, 200, 45, "Form 1099-NEC"), Block(2, 30, 100, 200, 110, "1 Nonemployee 500.00"),
              Block(3, 30, 100, 200, 110, "Instructions for Recipient ... instead of Form W-2 because"),
              Block(4, 30, 30, 200, 45, "Form 1098-T")]
    segments = split(_doc(blocks, 4))
    # The instructions page has no heading and follows a single-page form: its own (unidentified) segment.
    assert [(s.form_hint, s.pages) for s in segments] == [("w2", [1]), ("1099_nec", [2]), (None, [3]), ("1098_t", [4])]


def test_split_consolidated_statement_into_sections_with_shared_header():
    blocks = [Block(1, 30, 20, 400, 35, "2025 TAX REPORTING STATEMENT  Account 5521-0098"),
              Block(1, 30, 40, 400, 55, "PAYER'S TIN 41-1228890  RECIPIENT'S TIN XXX-XX-4417"),
              Block(1, 30, 100, 500, 115, "2025 Form 1099-DIV Dividends and Distributions"),
              Block(1, 30, 120, 300, 130, "1a Total ordinary dividends 812.40"),
              Block(1, 30, 300, 500, 315, "2025 Form 1099-INT Interest Income"),
              Block(1, 30, 320, 300, 330, "1 Interest income 96.12"),
              Block(2, 30, 20, 600, 35, "Form 1099-B Proceeds From Broker and Barter Exchange Transactions"),
              Block(2, 30, 60, 300, 70, "100 SH APPLE INC 19,450.00"),
              Block(3, 30, 20, 600, 35, "Form 1099-B (continued) Proceeds From Broker"),
              Block(3, 30, 60, 300, 70, "50 SH MSFT 2,900.00")]
    segments = split(_doc(blocks, 3))
    assert [(s.form_hint, s.pages) for s in segments] == [("1099_div", [1]), ("1099_int", [1]), ("1099_b", [2, 3])]
    assert all("PAYER'S TIN 41-1228890" in s.text for s in segments)
    assert "Interest income" not in "\n".join(b.text for b in segments[0].blocks)


def test_split_copies_of_one_form_on_a_page_stay_one_form():
    blocks = [Block(1, 30, 30, 200, 45, "Form W-2"), Block(1, 30, 640, 200, 655, "Form W-2"),
              Block(1, 30, 100, 200, 110, "1 Wages 1,000.00"), Block(1, 30, 700, 200, 710, "1 Wages 1,000.00")]
    assert [(s.form_hint, s.pages) for s in split(_doc(blocks, 1))] == [("w2", [1])]


def test_split_reads_ocr_glued_and_split_headings_and_footers():
    blocks = [Block(1, 40, 50, 115, 60, "Form"), Block(1, 97, 52, 194, 62, "n 1098-E"),
              Block(2, 43, 63, 401, 75, "Form 1099-MISCMiscellaneous Information"),
              Block(3, 47, 50, 459, 62, "Form SSA-109 Social Security Beft Statement"),
              Block(3, 47, 1246, 430, 1258, "Form SSA-1099 Social Security Benefit Statement (keep for your records)www.irs.gov"),
              Block(4, 56, 287, 432, 299, "2025 Form1099-DIV Dividends and Distributions")]
    assert [(s.form_hint, s.pages) for s in split(_doc(blocks, 4))] == [
        ("1098_e", [1]), ("1099_misc", [2]), ("ssa_1099", [3]), ("1099_div", [4])]


def test_split_without_headings_is_one_unlabelled_form():
    blocks = [Block(1, 30, 100, 200, 110, "Wages 1,000.00"), Block(2, 30, 100, 200, 110, "more")]
    assert [(s.form_hint, s.pages) for s in split(_doc(blocks, 2))] == [(None, [1, 2])]


# --- new forms / merging -------------------------------------------------------------------


def test_merge_answers_first_value_wins_and_lists_concatenate():
    from core.extractor import merge_answers

    merged = merge_answers([{"payer": {"name": "A", "tin": None}, "rows": [1], "x": None},
                            {"payer": {"name": "B", "tin": "12"}, "rows": [2], "x": "5"}])
    assert merged == {"payer": {"name": "A", "tin": "12"}, "rows": [1, 2], "x": "5"}


def test_new_form_consistency_checks():
    k = Form1099K.model_validate({"gross_amount": "300", "january": "100", "february": "100", "march": "50"})
    assert "boxes 5a-5l" in k.consistency_checks()[0]
    a = Form1095A.model_validate({"monthly": [{"month": "Jan", "enrollment_premium": "450"},
                                              {"month": "Feb", "enrollment_premium": "450"}],
                                  "annual_enrollment_premiums": "900"})
    assert a.consistency_checks() == []
    b = Form1099B.model_validate({"transactions": [{"proceeds": "100", "cost_basis": "80", "gain_or_loss": "25"}]})
    assert "gain or loss" in b.consistency_checks()[0]


def test_broker_section_headings():
    s = BrokerTransaction.section
    assert s("Short-term transactions for which basis is reported to the IRS (Box A)") == \
        {"term": "short", "basis_reported_to_irs": True, "form_8949_box": "A"}
    assert s("LONG TERM - NONCOVERED") == {"term": "long", "basis_reported_to_irs": False, "form_8949_box": "E"}
    assert s("100 SH APPLE INC") is None


def test_heading_classifier_needs_no_model():
    import asyncio

    from core.tev import HeadingClassifier

    c = asyncio.run(HeadingClassifier().classify("PAYER'S TIN 12-3456789\n2025 Form 1099-INT Interest Income\n1 Interest income 5.00"))
    assert (c.document_type, c.model) == ("1099_int", "headings")
    assert asyncio.run(HeadingClassifier().classify("INVOICE #10442\nTotal due $949.50")).document_type == "unknown"

