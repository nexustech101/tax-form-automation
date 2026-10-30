"""forms.py

Typed schemas for every supported tax document.

Each form is a Pydantic model whose fields carry the IRS box number and
label as metadata (see `box`). That one definition drives:

* the JSON schema of the API response,
* the field guide handed to the extraction LLM (`core.extractor`),
* the tev1 classification criteria (`TITLE` / `HINT`),
* the synthetic test documents in `tests/samples/`.

Adding a form = adding one class here and registering it in `FORMS`.
"""

from __future__ import annotations

import re
import typing
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from core.utils import (
    clean_text,
    parse_acquired,
    parse_bool,
    parse_date,
    parse_money,
    parse_month,
    parse_percent,
    parse_state,
    parse_tin,
    parse_year,
)

# --- Field types -------------------------------------------------------------

_as_float = PlainSerializer(lambda v: float(v) if v is not None else None, when_used="json")

Money = Annotated[Optional[Decimal], BeforeValidator(parse_money), _as_float]
Percent = Annotated[Optional[Decimal], BeforeValidator(parse_percent), _as_float]
Tin = Annotated[Optional[str], BeforeValidator(parse_tin)]
Date = Annotated[Optional[date], BeforeValidator(parse_date)]
Check = Annotated[Optional[bool], BeforeValidator(parse_bool)]
Text = Annotated[Optional[str], BeforeValidator(clean_text)]
State = Annotated[Optional[str], BeforeValidator(parse_state)]
Year = Annotated[Optional[int], BeforeValidator(parse_year)]
Month = Annotated[Optional[str], BeforeValidator(parse_month)]
Acquired = Annotated[Optional[Union[date, Literal["VARIOUS"]]], BeforeValidator(parse_acquired)]


def box(num: str | None, label: str, **extra: Any) -> Any:
    """A form field annotated with its box number and printed label."""
    description = f"Box {num}: {label}" if num else label
    return Field(default=None, description=description, json_schema_extra={"box": num, "label": label, **extra})


def party(label: str, tin_label: str | None = None, tin_box: str | None = None) -> Any:
    """A name/address block, optionally with its TIN box."""
    return Field(
        default=None,
        description=label + (f"; TIN from '{tin_label}'" if tin_label else ""),
        json_schema_extra={"label": label, "tin_label": tin_label, "tin_box": tin_box},
    )


def rows(label: str, max_rows: int = 2) -> Any:
    return Field(default_factory=list, description=label, json_schema_extra={"label": label, "max_rows": max_rows})


def table(label: str) -> Any:
    """
    A list of rows printed as a table with one header row. `core.tables`
    reads it row by row from the layout (exact, any length); the extraction
    model only fills it when no header row is found.
    """
    return Field(default_factory=list, description=label, json_schema_extra={"label": label, "table": True})


def column(label: str, *aliases: str, lead: bool = False) -> Any:
    """A table column, found by its printed header. `lead`: the row-label column (leftmost)."""
    return Field(default=None, description=label, json_schema_extra={"columns": [label, *aliases], "lead": lead})


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Address(_Model):
    street: Text = None
    city: Text = None
    state: State = None
    postal_code: Text = None
    country: Text = None


class Party(_Model):
    name: Text = None
    tin: Tin = Field(default=None, description="SSN, ITIN or EIN")
    address: Optional[Address] = None
    phone: Text = None


class CodedAmount(_Model):
    code: Text = Field(default=None, description="Letter code printed next to the amount")
    amount: Money = Field(default=None, description="Amount")


class DescribedAmount(_Model):
    description: Text = Field(default=None, description="Description")
    amount: Money = Field(default=None, description="Amount")


class StateTaxRow(_Model):
    """State/local information block common to 1099-series forms."""

    state: State = Field(default=None, description="State")
    payer_state_id: Text = Field(default=None, description="Payer's state identification number")
    state_income: Money = Field(default=None, description="State income")
    state_tax_withheld: Money = Field(default=None, description="State tax withheld")


class TaxForm(_Model):
    """Base for all forms. Subclasses set the ClassVars."""

    FORM_ID: ClassVar[str]
    TITLE: ClassVar[str]  # "Form <number> <name>" / "Schedule <number> (Form ...) <name>": also its printed heading
    HINT: ClassVar[str]  # what distinguishes this form, for the classifier
    MULTI_PAGE: ClassVar[bool] = False  # one form may continue over pages that repeat its heading

    tax_year: Year = Field(default=None, description="Calendar / tax year printed on the form")

    def consistency_checks(self) -> list[str]:
        """Arithmetic sanity checks. Warnings only: forms can be legitimately odd."""
        return []


class InformationReturn(TaxForm):
    """1099 / 1098 family: payer + recipient, CORRECTED/VOID boxes, account no."""

    corrected: Check = box(None, "CORRECTED (if checked)")
    account_number: Text = box(None, "Account number (see instructions)")


# --- Wage statements ---------------------------------------------------------


class W2StateRow(_Model):
    state: State = Field(default=None, description="Box 15: State")
    employer_state_id: Text = Field(default=None, description="Box 15: Employer's state ID number")
    state_wages: Money = Field(default=None, description="Box 16: State wages, tips, etc.")
    state_income_tax: Money = Field(default=None, description="Box 17: State income tax")
    local_wages: Money = Field(default=None, description="Box 18: Local wages, tips, etc.")
    local_income_tax: Money = Field(default=None, description="Box 19: Local income tax")
    locality_name: Text = Field(default=None, description="Box 20: Locality name")


class W2(TaxForm):
    FORM_ID = "w2"
    TITLE = "Form W-2 Wage and Tax Statement"
    HINT = "Employer-issued wage statement with boxes for wages, tips, social security and Medicare wages and withholding."

    employee: Optional[Party] = party("e/f Employee's name, address, and ZIP code", "a Employee's social security number", "a")
    employer: Optional[Party] = party("c Employer's name, address, and ZIP code", "b Employer identification number (EIN)", "b")
    control_number: Text = box("d", "Control number")
    wages_tips_other_comp: Money = box("1", "Wages, tips, other compensation")
    federal_income_tax_withheld: Money = box("2", "Federal income tax withheld")
    social_security_wages: Money = box("3", "Social security wages")
    social_security_tax_withheld: Money = box("4", "Social security tax withheld")
    medicare_wages_and_tips: Money = box("5", "Medicare wages and tips")
    medicare_tax_withheld: Money = box("6", "Medicare tax withheld")
    social_security_tips: Money = box("7", "Social security tips")
    allocated_tips: Money = box("8", "Allocated tips")
    dependent_care_benefits: Money = box("10", "Dependent care benefits")
    nonqualified_plans: Money = box("11", "Nonqualified plans")
    box12: list[CodedAmount] = rows("Box 12a-12d: code and amount (e.g. D, DD, W)", 4)
    statutory_employee: Check = box("13", "Statutory employee")
    retirement_plan: Check = box("13", "Retirement plan")
    third_party_sick_pay: Check = box("13", "Third-party sick pay")
    box14_other: list[DescribedAmount] = rows("Box 14: Other (description and amount)", 3)
    state_local: list[W2StateRow] = rows("Boxes 15-20: state and local tax rows", 2)

    def consistency_checks(self) -> list[str]:
        out = []
        if self.social_security_wages and self.social_security_tax_withheld is not None:
            expected = self.social_security_wages * Decimal("0.062")
            if abs(expected - self.social_security_tax_withheld) > 1:
                out.append(f"box 4 ({self.social_security_tax_withheld}) is not 6.2% of box 3 ({self.social_security_wages})")
        if self.medicare_wages_and_tips and self.medicare_tax_withheld is not None:
            expected = self.medicare_wages_and_tips * Decimal("0.0145")
            if self.medicare_tax_withheld + 1 < expected:
                out.append(f"box 6 ({self.medicare_tax_withheld}) is below 1.45% of box 5 ({self.medicare_wages_and_tips})")
        return out


# --- 1099 series -------------------------------------------------------------


class Form1099NEC(InformationReturn):
    FORM_ID = "1099_nec"
    TITLE = "Form 1099-NEC Nonemployee Compensation"
    HINT = "Reports nonemployee compensation paid to independent contractors; box 1 nonemployee compensation."

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    second_tin_notice: Check = box(None, "2nd TIN not.")
    nonemployee_compensation: Money = box("1", "Nonemployee compensation")
    direct_sales_5000_or_more: Check = box("2", "Payer made direct sales totaling $5,000 or more of consumer products to recipient for resale")
    excess_golden_parachute_payments: Money = box("3", "Excess golden parachute payments")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    state_info: list[StateTaxRow] = rows("Boxes 5-7: State tax withheld, State/Payer's state no., State income", 2)


class Form1099MISC(InformationReturn):
    FORM_ID = "1099_misc"
    TITLE = "Form 1099-MISC Miscellaneous Information"
    HINT = "Reports rents, royalties, other income, prizes, medical and health care payments, attorney proceeds."

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    rents: Money = box("1", "Rents")
    royalties: Money = box("2", "Royalties")
    other_income: Money = box("3", "Other income")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    fishing_boat_proceeds: Money = box("5", "Fishing boat proceeds")
    medical_and_health_care_payments: Money = box("6", "Medical and health care payments")
    direct_sales_5000_or_more: Check = box("7", "Payer made direct sales totaling $5,000 or more of consumer products to recipient for resale")
    substitute_payments: Money = box("8", "Substitute payments in lieu of dividends or interest")
    crop_insurance_proceeds: Money = box("9", "Crop insurance proceeds")
    gross_proceeds_paid_to_attorney: Money = box("10", "Gross proceeds paid to an attorney")
    fish_purchased_for_resale: Money = box("11", "Fish purchased for resale")
    section_409a_deferrals: Money = box("12", "Section 409A deferrals")
    fatca_filing_requirement: Check = box("13", "FATCA filing requirement")
    excess_golden_parachute_payments: Money = box("14", "Excess golden parachute payments")
    nonqualified_deferred_compensation: Money = box("15", "Nonqualified deferred compensation")
    state_info: list[StateTaxRow] = rows("Boxes 16-18: State tax withheld, State/Payer's state no., State income", 2)


class Form1099INT(InformationReturn):
    FORM_ID = "1099_int"
    TITLE = "Form 1099-INT Interest Income"
    HINT = "Bank or brokerage statement of interest income, early withdrawal penalty, tax-exempt interest, bond premium."

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    payer_rtn: Text = box(None, "Payer's RTN (optional)")
    interest_income: Money = box("1", "Interest income")
    early_withdrawal_penalty: Money = box("2", "Early withdrawal penalty")
    us_savings_bonds_and_treasury_interest: Money = box("3", "Interest on U.S. Savings Bonds and Treasury obligations")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    investment_expenses: Money = box("5", "Investment expenses")
    foreign_tax_paid: Money = box("6", "Foreign tax paid")
    foreign_country: Text = box("7", "Foreign country or U.S. possession")
    tax_exempt_interest: Money = box("8", "Tax-exempt interest")
    specified_private_activity_bond_interest: Money = box("9", "Specified private activity bond interest")
    market_discount: Money = box("10", "Market discount")
    bond_premium: Money = box("11", "Bond premium")
    bond_premium_on_treasury_obligations: Money = box("12", "Bond premium on Treasury obligations")
    bond_premium_on_tax_exempt_bond: Money = box("13", "Bond premium on tax-exempt bond")
    tax_exempt_tax_credit_bond_cusip: Text = box("14", "Tax-exempt and tax credit bond CUSIP no.")
    fatca_filing_requirement: Check = box(None, "FATCA filing requirement")
    state_info: list[StateTaxRow] = rows("Boxes 15-17: State, State identification no., State tax withheld", 2)


class Form1099DIV(InformationReturn):
    FORM_ID = "1099_div"
    TITLE = "Form 1099-DIV Dividends and Distributions"
    HINT = "Reports ordinary and qualified dividends, capital gain distributions, section 199A dividends."

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    total_ordinary_dividends: Money = box("1a", "Total ordinary dividends")
    qualified_dividends: Money = box("1b", "Qualified dividends")
    total_capital_gain_distributions: Money = box("2a", "Total capital gain distr.")
    unrecaptured_section_1250_gain: Money = box("2b", "Unrecap. Sec. 1250 gain")
    section_1202_gain: Money = box("2c", "Section 1202 gain")
    collectibles_28_rate_gain: Money = box("2d", "Collectibles (28%) gain")
    section_897_ordinary_dividends: Money = box("2e", "Section 897 ordinary dividends")
    section_897_capital_gain: Money = box("2f", "Section 897 capital gain")
    nondividend_distributions: Money = box("3", "Nondividend distributions")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    section_199a_dividends: Money = box("5", "Section 199A dividends")
    investment_expenses: Money = box("6", "Investment expenses")
    foreign_tax_paid: Money = box("7", "Foreign tax paid")
    foreign_country: Text = box("8", "Foreign country or U.S. possession")
    cash_liquidation_distributions: Money = box("9", "Cash liquidation distributions")
    noncash_liquidation_distributions: Money = box("10", "Noncash liquidation distributions")
    fatca_filing_requirement: Check = box("11", "FATCA filing requirement")
    exempt_interest_dividends: Money = box("12", "Exempt-interest dividends")
    specified_private_activity_bond_interest_dividends: Money = box("13", "Specified private activity bond interest dividends")
    state_info: list[StateTaxRow] = rows("Boxes 14-16: State, State identification no., State tax withheld", 2)

    def consistency_checks(self) -> list[str]:
        if self.qualified_dividends and self.total_ordinary_dividends is not None:
            if self.qualified_dividends > self.total_ordinary_dividends:
                return ["box 1b qualified dividends exceed box 1a total ordinary dividends"]
        return []


class Form1099R(InformationReturn):
    FORM_ID = "1099_r"
    TITLE = "Form 1099-R Distributions From Pensions, Annuities, Retirement or Profit-Sharing Plans, IRAs, Insurance Contracts, etc."
    HINT = "Reports retirement plan, pension, annuity and IRA distributions with distribution codes."

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    gross_distribution: Money = box("1", "Gross distribution")
    taxable_amount: Money = box("2a", "Taxable amount")
    taxable_amount_not_determined: Check = box("2b", "Taxable amount not determined")
    total_distribution: Check = box("2b", "Total distribution")
    capital_gain: Money = box("3", "Capital gain (included in box 2a)")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    employee_contributions: Money = box("5", "Employee contributions/Designated Roth contributions or insurance premiums")
    net_unrealized_appreciation: Money = box("6", "Net unrealized appreciation in employer's securities")
    distribution_codes: Text = box("7", "Distribution code(s), plain text such as 7 or G4")
    ira_sep_simple: Check = box("7", "IRA/SEP/SIMPLE")
    other_amount: Money = box("8", "Other")
    other_percent: Percent = box("8", "Other (percentage)")
    percent_of_total_distribution: Percent = box("9a", "Your percentage of total distribution")
    total_employee_contributions: Money = box("9b", "Total employee contributions")
    amount_allocable_to_irr: Money = box("10", "Amount allocable to IRR within 5 years")
    first_year_of_designated_roth: Year = box("11", "1st year of desig. Roth contrib.")
    fatca_filing_requirement: Check = box("12", "FATCA filing requirement")
    date_of_payment: Date = box("13", "Date of payment")
    state_info: list[StateTaxRow] = rows("Boxes 14-16: State tax withheld, State/Payer's state no., State distribution", 2)

    def consistency_checks(self) -> list[str]:
        if self.taxable_amount and self.gross_distribution is not None and self.taxable_amount > self.gross_distribution:
            return ["box 2a taxable amount exceeds box 1 gross distribution"]
        return []


class Form1099G(InformationReturn):
    FORM_ID = "1099_g"
    TITLE = "Form 1099-G Certain Government Payments"
    HINT = "Government-issued: unemployment compensation, state or local income tax refunds, credits or offsets."

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    unemployment_compensation: Money = box("1", "Unemployment compensation")
    state_or_local_income_tax_refunds: Money = box("2", "State or local income tax refunds, credits, or offsets")
    box2_tax_year: Year = box("3", "Box 2 amount is for tax year")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    rtaa_payments: Money = box("5", "RTAA payments")
    taxable_grants: Money = box("6", "Taxable grants")
    agriculture_payments: Money = box("7", "Agriculture payments")
    trade_or_business_income: Check = box("8", "If checked, box 2 is trade or business income")
    market_gain: Money = box("9", "Market gain")
    state_info: list[StateTaxRow] = rows("Boxes 10a-11: State, State identification no., State income tax withheld", 2)


class SSA1099(TaxForm):
    FORM_ID = "ssa_1099"
    TITLE = "Form SSA-1099 Social Security Benefit Statement"
    HINT = "Social Security Administration benefit statement: benefits paid, benefits repaid, net benefits."

    beneficiary: Optional[Party] = party("Box 1: Name; Box 7: Address", "Box 2: Beneficiary's social security number", "2")
    benefits_paid: Money = box("3", "Benefits paid in the tax year")
    benefits_repaid: Money = box("4", "Benefits repaid to SSA in the tax year")
    net_benefits: Money = box("5", "Net benefits for the tax year (box 3 minus box 4)")
    voluntary_federal_income_tax_withheld: Money = box("6", "Voluntary federal income tax withheld")
    claim_number: Text = box("8", "Claim number (use this number if you need to contact SSA)")
    medicare_part_b_premiums: Money = box(None, "Medicare Part B premiums deducted from your benefits")

    def consistency_checks(self) -> list[str]:
        if None not in (self.benefits_paid, self.benefits_repaid, self.net_benefits):
            if self.benefits_paid - self.benefits_repaid != self.net_benefits:
                return ["box 5 net benefits does not equal box 3 minus box 4"]
        return []


# --- 1098 series -------------------------------------------------------------


class Form1098(InformationReturn):
    FORM_ID = "1098"
    TITLE = "Form 1098 Mortgage Interest Statement"
    HINT = "Lender statement of mortgage interest received, outstanding principal, points and mortgage insurance premiums."

    lender: Optional[Party] = party("RECIPIENT'S/LENDER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "RECIPIENT'S/LENDER'S TIN")
    borrower: Optional[Party] = party("PAYER'S/BORROWER'S name, street address (including apt. no.), city or town, state or province, country, and ZIP or foreign postal code", "PAYER'S/BORROWER'S TIN")
    mortgage_interest_received: Money = box("1", "Mortgage interest received from payer(s)/borrower(s)")
    outstanding_mortgage_principal: Money = box("2", "Outstanding mortgage principal")
    mortgage_origination_date: Date = box("3", "Mortgage origination date")
    refund_of_overpaid_interest: Money = box("4", "Refund of overpaid interest")
    mortgage_insurance_premiums: Money = box("5", "Mortgage insurance premiums")
    points_paid_on_purchase: Money = box("6", "Points paid on purchase of principal residence")
    property_address_same_as_borrower: Check = box("7", "If address of property securing mortgage is the same as PAYER'S/BORROWER'S address, the box is checked")
    property_address: Text = box("8", "Address or description of property securing mortgage")
    number_of_properties: Text = box("9", "Number of properties securing the mortgage")
    other: Text = box("10", "Other")
    mortgage_acquisition_date: Date = box("11", "Mortgage acquisition date")


class Form1098T(InformationReturn):
    FORM_ID = "1098_t"
    TITLE = "Form 1098-T Tuition Statement"
    HINT = "College or university tuition statement: qualified tuition payments, scholarships or grants, half-time and graduate student checkboxes."

    filer: Optional[Party] = party("FILER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone number", "FILER'S employer identification no.")
    student: Optional[Party] = party("STUDENT'S name, street address (including apt. no.), city or town, state or province, country, and ZIP or foreign postal code", "STUDENT'S TIN")
    payments_received_for_qualified_tuition: Money = box("1", "Payments received for qualified tuition and related expenses")
    adjustments_made_for_prior_year: Money = box("4", "Adjustments made for a prior year")
    scholarships_or_grants: Money = box("5", "Scholarships or grants")
    adjustments_to_scholarships_for_prior_year: Money = box("6", "Adjustments to scholarships or grants for a prior year")
    includes_amounts_for_next_period: Check = box("7", "Checked if the amount in box 1 includes amounts for an academic period beginning January-March of next year")
    at_least_half_time_student: Check = box("8", "Check if at least half-time student")
    graduate_student: Check = box("9", "Checked if a graduate student")
    insurance_contract_reimbursement: Money = box("10", "Ins. contract reimb./refund")


class Form1098E(InformationReturn):
    FORM_ID = "1098_e"
    TITLE = "Form 1098-E Student Loan Interest Statement"
    HINT = "Student loan servicer statement of student loan interest received."

    lender: Optional[Party] = party("RECIPIENT'S/LENDER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone number", "RECIPIENT'S TIN")
    borrower: Optional[Party] = party("BORROWER'S name, street address (including apt. no.), city or town, state or province, country, and ZIP or foreign postal code", "BORROWER'S TIN")
    student_loan_interest_received: Money = box("1", "Student loan interest received by lender")
    excludes_origination_fees_or_capitalized_interest: Check = box("2", "If checked, box 1 does not include loan origination fees and/or capitalized interest for loans made before September 1, 2004")


class BrokerTransaction(_Model):
    """One sale on a 1099-B (boxes 1a-1g, plus the reporting category of its section)."""

    description: Text = column("Description of property", "Description", "Security description", lead=True)
    cusip: Text = column("CUSIP number", "CUSIP")
    quantity: Text = column("Quantity", "Shares")
    date_acquired: Acquired = column("Date acquired", "Acquired")
    date_sold: Date = column("Date sold or disposed", "Date sold", "Sold")
    proceeds: Money = column("Proceeds", "Gross proceeds", "Sales price")
    cost_basis: Money = column("Cost or other basis", "Cost basis")
    accrued_market_discount: Money = column("Accrued market discount", "Market discount")
    wash_sale_loss_disallowed: Money = column("Wash sale loss disallowed", "Wash sale")
    gain_or_loss: Money = column("Gain or loss", "Realized gain or loss", "Gain/loss")
    federal_income_tax_withheld: Money = column("Federal income tax withheld", "Federal tax withheld")
    term: Optional[Literal["short", "long", "ordinary"]] = Field(
        default=None, description="Box 2: Short-term gain or loss / Long-term gain or loss / Ordinary")
    basis_reported_to_irs: Check = Field(default=None, description="Box 12: If checked, basis reported to IRS (covered)")
    form_8949_box: Optional[Literal["A", "B", "C", "D", "E", "F"]] = Field(
        default=None, description="Applicable checkbox on Form 8949 (A-F)")

    @staticmethod
    def section(text: str) -> dict[str, Any] | None:
        """
        Consolidated statements group sales under headings such as "Short-term
        transactions for covered tax lots ... Form 8949 with Box A checked".
        Returns the reporting category the heading sets for the rows below it.
        """
        t = text.lower()
        term = "short" if re.search(r"short[\s-]*term", t) else "long" if re.search(r"long[\s-]*term", t) else None
        box_letter = re.search(r"\bbox\s+([a-f])\b", t)
        if term is None and box_letter is None:
            return None
        letter = box_letter.group(1).upper() if box_letter else None
        if letter:
            term = term or ("short" if letter in "ABC" else "long")
        covered = None
        if letter in ("A", "D") or re.search(r"basis is reported|\bcovered", t):
            covered = True
        if letter in ("B", "E") or re.search(r"basis is not reported|noncovered|not reported to the irs", t):
            covered = False
        if letter is None and covered is not None:
            letter = {("short", True): "A", ("short", False): "B", ("long", True): "D", ("long", False): "E"}.get((term, covered))
        return {"term": term, "basis_reported_to_irs": covered, "form_8949_box": letter}


class Form1099B(InformationReturn):
    FORM_ID = "1099_b"
    TITLE = "Form 1099-B Proceeds From Broker and Barter Exchange Transactions"
    HINT = "Broker statement of securities sales: description, dates acquired and sold, proceeds, cost basis, wash sales, short-term or long-term."
    MULTI_PAGE = True  # transaction detail runs over many pages

    payer: Optional[Party] = party("PAYER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "PAYER'S TIN")
    recipient: Optional[Party] = party("RECIPIENT'S name, street address, city or town, state or province, country, and ZIP or foreign postal code", "RECIPIENT'S TIN")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    profit_or_loss_on_closed_contracts: Money = box("8", "Profit or (loss) realized on closed contracts")
    aggregate_profit_or_loss_on_contracts: Money = box("11", "Aggregate profit or (loss) on contracts")
    bartering: Money = box("13", "Bartering")
    transactions: list[BrokerTransaction] = table("Boxes 1a-1g: sales, one row per transaction")
    state_info: list[StateTaxRow] = rows("Boxes 14-16: State name, State identification no., State tax withheld", 2)

    def consistency_checks(self) -> list[str]:
        out = []
        for i, t in enumerate(self.transactions):
            if None in (t.proceeds, t.cost_basis, t.gain_or_loss):
                continue
            expected = t.proceeds - t.cost_basis + (t.wash_sale_loss_disallowed or 0)
            if abs(expected - t.gain_or_loss) > 1:
                out.append(f"transactions[{i}]: gain or loss {t.gain_or_loss} is not proceeds - basis + wash sale ({expected})")
        return out


class Form1099K(InformationReturn):
    FORM_ID = "1099_k"
    TITLE = "Form 1099-K Payment Card and Third Party Network Transactions"
    HINT = "Payment processor or marketplace statement of gross payment card and third party network transactions, with monthly amounts."

    filer: Optional[Party] = party("FILER'S name, street address, city or town, state or province, country, ZIP or foreign postal code, and telephone no.", "FILER'S TIN")
    payee: Optional[Party] = party("PAYEE'S name", "PAYEE'S TIN")
    filer_is_pse: Check = box(None, "Payment settlement entity (PSE)")
    filer_is_epf_or_other_third_party: Check = box(None, "Electronic Payment Facilitator (EPF)/Other third party")
    payment_card_transactions: Check = box(None, "Payment card")
    third_party_network_transactions: Check = box(None, "Third party network")
    pse_name_and_phone: Text = box(None, "PSE'S name and telephone number")
    gross_amount: Money = box("1a", "Gross amount of payment card/third party network transactions")
    card_not_present_transactions: Money = box("1b", "Card Not Present transactions")
    merchant_category_code: Text = box("2", "Merchant category code")
    number_of_payment_transactions: Text = box("3", "Number of payment transactions")
    federal_income_tax_withheld: Money = box("4", "Federal income tax withheld")
    january: Money = box("5a", "January")
    february: Money = box("5b", "February")
    march: Money = box("5c", "March")
    april: Money = box("5d", "April")
    may: Money = box("5e", "May")
    june: Money = box("5f", "June")
    july: Money = box("5g", "July")
    august: Money = box("5h", "August")
    september: Money = box("5i", "September")
    october: Money = box("5j", "October")
    november: Money = box("5k", "November")
    december: Money = box("5l", "December")
    state_info: list[StateTaxRow] = rows("Boxes 6-8: State, State identification no., State income tax withheld", 2)

    MONTHS: ClassVar[tuple[str, ...]] = ("january", "february", "march", "april", "may", "june", "july",
                                         "august", "september", "october", "november", "december")

    def consistency_checks(self) -> list[str]:
        months = [getattr(self, m) for m in self.MONTHS]
        if self.gross_amount is None or all(m is None for m in months):
            return []
        total = sum((m for m in months if m is not None), Decimal(0))
        if abs(total - self.gross_amount) > 1:
            return [f"boxes 5a-5l total {total}, but box 1a gross amount is {self.gross_amount}"]
        return []


# --- Health coverage ---------------------------------------------------------


class CoveredIndividual(_Model):
    name: Text = column("Covered individual name", lead=True)
    ssn: Tin = column("Covered individual SSN")
    date_of_birth: Date = column("Covered individual date of birth")
    coverage_start_date: Date = column("Coverage start date")
    coverage_termination_date: Date = column("Coverage termination date")


class MonthlyCoverage(_Model):
    month: Month = column("Month", lead=True)
    enrollment_premium: Money = column("Monthly enrollment premiums")
    slcsp_premium: Money = column("Monthly second lowest cost silver plan (SLCSP) premium")
    advance_ptc: Money = column("Monthly advance payment of premium tax credit")


class Form1095A(TaxForm):
    FORM_ID = "1095_a"
    TITLE = "Form 1095-A Health Insurance Marketplace Statement"
    HINT = "Health Insurance Marketplace statement: policy, covered individuals, monthly enrollment premiums, SLCSP premium and advance premium tax credit."

    marketplace_identifier: Text = box("1", "Marketplace identifier")
    policy_number: Text = box("2", "Marketplace-assigned policy number")
    policy_issuer: Text = box("3", "Policy issuer's name")
    recipient_name: Text = box("4", "Recipient's name")
    recipient_ssn: Tin = box("5", "Recipient's SSN")
    recipient_date_of_birth: Date = box("6", "Recipient's date of birth")
    spouse_name: Text = box("7", "Recipient's spouse's name")
    spouse_ssn: Tin = box("8", "Recipient's spouse's SSN")
    spouse_date_of_birth: Date = box("9", "Recipient's spouse's date of birth")
    policy_start_date: Date = box("10", "Policy start date")
    policy_termination_date: Date = box("11", "Policy termination date")
    street_address: Text = box("12", "Street address (including apartment no.)")
    city: Text = box("13", "City or town")
    state: Text = box("14", "State or province")
    country_and_zip: Text = box("15", "Country and ZIP or foreign postal code")
    covered_individuals: list[CoveredIndividual] = table("Part II: Covered individuals (lines 16-20)")
    monthly: list[MonthlyCoverage] = table("Part III: Coverage information by month (lines 21-32)")
    annual_enrollment_premiums: Money = box("33A", "Annual Totals: Monthly enrollment premiums")
    annual_slcsp_premium: Money = box("33B", "Annual Totals: Monthly second lowest cost silver plan (SLCSP) premium")
    annual_advance_ptc: Money = box("33C", "Annual Totals: Monthly advance payment of premium tax credit")

    def consistency_checks(self) -> list[str]:
        out = []
        for column_name, total_name in (("enrollment_premium", "annual_enrollment_premiums"),
                                        ("slcsp_premium", "annual_slcsp_premium"),
                                        ("advance_ptc", "annual_advance_ptc")):
            total = getattr(self, total_name)
            values = [getattr(m, column_name) for m in self.monthly if getattr(m, column_name) is not None]
            if total is not None and values and abs(sum(values, Decimal(0)) - total) > 1:
                out.append(f"line 33 {total_name} {total} does not equal the sum of the months ({sum(values, Decimal(0))})")
        return out


# --- Pass-through ------------------------------------------------------------


class K1CapitalAccount(_Model):
    beginning_capital_account: Money = Field(default=None, description="Item L: Beginning capital account")
    capital_contributed: Money = Field(default=None, description="Item L: Capital contributed during the year")
    current_year_net_income: Money = Field(default=None, description="Item L: Current year net income (loss)")
    other_increase_decrease: Money = Field(default=None, description="Item L: Other increase (decrease)")
    withdrawals_and_distributions: Money = Field(default=None, description="Item L: Withdrawals and distributions")
    ending_capital_account: Money = Field(default=None, description="Item L: Ending capital account")


class ScheduleK1_1065(TaxForm):
    FORM_ID = "k1_1065"
    TITLE = "Schedule K-1 (Form 1065) Partner's Share of Income, Deductions, Credits, etc."
    HINT = "Partnership K-1: Part I partnership info, Part II partner info, Part III partner's share of current year income with coded boxes."
    MULTI_PAGE = True  # supplemental statements follow the face page

    partnership: Optional[Party] = party("B Partnership's name, address, city, state, and ZIP code", "A Partnership's employer identification number", "A")
    irs_center: Text = box("C", "IRS center where partnership filed return")
    publicly_traded_partnership: Check = box("D", "Check if this is a publicly traded partnership (PTP)")
    partner: Optional[Party] = party("F Name, address, city, state, and ZIP code for partner", "E Partner's SSN or TIN", "E")
    general_partner_or_llc_member_manager: Check = box("G", "General partner or LLC member-manager")
    limited_partner_or_other_llc_member: Check = box("G", "Limited partner or other LLC member")
    domestic_partner: Check = box("H1", "Domestic partner")
    foreign_partner: Check = box("H1", "Foreign partner")
    partner_entity_type: Text = box("I1", "What type of entity is this partner?")
    profit_share_ending_percent: Percent = box("J", "Partner's share of profit (ending %)")
    loss_share_ending_percent: Percent = box("J", "Partner's share of loss (ending %)")
    capital_share_ending_percent: Percent = box("J", "Partner's share of capital (ending %)")
    capital_account: Optional[K1CapitalAccount] = Field(default=None, description="Item L: Partner's capital account analysis")
    ordinary_business_income: Money = box("1", "Ordinary business income (loss)")
    net_rental_real_estate_income: Money = box("2", "Net rental real estate income (loss)")
    other_net_rental_income: Money = box("3", "Other net rental income (loss)")
    guaranteed_payments_for_services: Money = box("4a", "Guaranteed payments for services")
    guaranteed_payments_for_capital: Money = box("4b", "Guaranteed payments for capital")
    total_guaranteed_payments: Money = box("4c", "Total guaranteed payments")
    interest_income: Money = box("5", "Interest income")
    ordinary_dividends: Money = box("6a", "Ordinary dividends")
    qualified_dividends: Money = box("6b", "Qualified dividends")
    dividend_equivalents: Money = box("6c", "Dividend equivalents")
    royalties: Money = box("7", "Royalties")
    net_short_term_capital_gain: Money = box("8", "Net short-term capital gain (loss)")
    net_long_term_capital_gain: Money = box("9a", "Net long-term capital gain (loss)")
    collectibles_28_gain: Money = box("9b", "Collectibles (28%) gain (loss)")
    unrecaptured_section_1250_gain: Money = box("9c", "Unrecaptured section 1250 gain")
    net_section_1231_gain: Money = box("10", "Net section 1231 gain (loss)")
    other_income: list[CodedAmount] = rows("Box 11: Other income (loss), code and amount", 2)
    section_179_deduction: Money = box("12", "Section 179 deduction")
    other_deductions: list[CodedAmount] = rows("Box 13: Other deductions, code and amount", 2)
    self_employment_earnings: list[CodedAmount] = rows("Box 14: Self-employment earnings (loss), code and amount", 2)
    credits: list[CodedAmount] = rows("Box 15: Credits, code and amount", 2)
    schedule_k3_attached: Check = box("16", "Schedule K-3 is attached if checked")
    amt_items: list[CodedAmount] = rows("Box 17: Alternative minimum tax (AMT) items, code and amount", 2)
    tax_exempt_income_and_nondeductible_expenses: list[CodedAmount] = rows("Box 18: Tax-exempt income and nondeductible expenses, code and amount", 2)
    distributions: list[CodedAmount] = rows("Box 19: Distributions, code and amount", 2)
    other_information: list[CodedAmount] = rows("Box 20: Other information, code and amount", 3)
    foreign_taxes_paid_or_accrued: Money = box("21", "Foreign taxes paid or accrued")

    def consistency_checks(self) -> list[str]:
        ca = self.capital_account
        if ca is None or ca.beginning_capital_account is None or ca.ending_capital_account is None:
            return []
        zero = Decimal(0)
        rolled = (ca.beginning_capital_account + (ca.capital_contributed or zero) + (ca.current_year_net_income or zero)
                  + (ca.other_increase_decrease or zero) - abs(ca.withdrawals_and_distributions or zero))
        if abs(rolled - ca.ending_capital_account) > 1:
            return [f"item L capital account does not roll forward (computed {rolled}, printed {ca.ending_capital_account})"]
        return []


# --- Registry ----------------------------------------------------------------

FORMS: dict[str, type[TaxForm]] = {
    form.FORM_ID: form
    for form in (
        W2, Form1099NEC, Form1099MISC, Form1099INT, Form1099DIV, Form1099B, Form1099R, Form1099G,
        Form1099K, SSA1099, Form1098, Form1098T, Form1098E, Form1095A, ScheduleK1_1065,
    )
}

UNKNOWN = "unknown"


def _submodel(annotation: Any) -> type[BaseModel] | None:
    """The BaseModel inside ``Optional[X]`` / ``list[X]``, if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        nested = _submodel(arg)
        if nested:
            return nested
    return None


def form_labels(form: type[BaseModel]) -> list[str]:
    """Every printed label on a form (boxes, party blocks, table columns)."""
    labels: list[str] = []
    for name, info in form.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        for key in ("label", "tin_label"):
            if extra.get(key):
                labels.append(str(extra[key]))
        sub = _submodel(info.annotation)
        if sub is not None and sub not in (Party, Address):
            if info.description:
                labels.append(info.description)
            labels += [f.description for f in sub.model_fields.values() if f.description]
            labels += form_labels(sub)
    return list(dict.fromkeys(labels))


def form_for(form_id: str) -> type[TaxForm]:
    try:
        return FORMS[form_id]
    except KeyError:
        raise KeyError(f"Unsupported document type: {form_id}") from None
