"""sample_data.py

Fictitious, filled-in values for every supported form. Names, TINs, and
addresses are made up for testing and do not belong to real people or
businesses.
"""

from __future__ import annotations

import random
from typing import Any


def _addr(street: str, city: str, state: str, zip_code: str) -> dict[str, str]:
    return {"street": street, "city": city, "state": state, "postal_code": zip_code}


SAMPLES: dict[str, dict[str, Any]] = {
    "w2": {
        "tax_year": 2025,
        "employee": {"name": "Maria L Gonzalez", "tin": "412-56-7890",
                     "address": _addr("1847 Willow Creek Dr", "Austin", "TX", "78745")},
        "employer": {"name": "Brightline Logistics LLC", "tin": "84-1736295",
                     "address": _addr("500 Commerce Pkwy Ste 200", "Dallas", "TX", "75201")},
        "control_number": "A7731-0042",
        "wages_tips_other_comp": "68,412.55",
        "federal_income_tax_withheld": "7,921.08",
        "social_security_wages": "72,500.00",
        "social_security_tax_withheld": "4,495.00",
        "medicare_wages_and_tips": "72,500.00",
        "medicare_tax_withheld": "1,051.25",
        "dependent_care_benefits": "2,400.00",
        "box12": [{"code": "D", "amount": "4,087.45"}, {"code": "DD", "amount": "9,816.00"}],
        "retirement_plan": True,
        "statutory_employee": False,
        "third_party_sick_pay": False,
        "box14_other": [{"description": "SDI", "amount": "312.40"}],
        "state_local": [{"state": "TX", "employer_state_id": "17-8839201", "state_wages": "68,412.55",
                         "state_income_tax": "0.00"}],
    },
    "1099_nec": {
        "tax_year": 2025,
        "corrected": False,
        "payer": {"name": "Summit Ridge Marketing Inc", "tin": "27-4410983",
                  "address": _addr("88 Harbor View Rd", "Portland", "OR", "97205"), "phone": "(503) 555-0148"},
        "recipient": {"name": "Devon K Patel", "tin": "XXX-XX-4417",
                      "address": _addr("2210 Elm St Apt 3B", "Beaverton", "OR", "97005")},
        "account_number": "VND-20931",
        "nonemployee_compensation": "24,750.00",
        "direct_sales_5000_or_more": False,
        "federal_income_tax_withheld": "0.00",
        "state_info": [{"state": "OR", "payer_state_id": "0938471-2", "state_income": "24,750.00",
                        "state_tax_withheld": "1,980.00"}],
    },
    "1099_misc": {
        "tax_year": 2025,
        "corrected": False,
        "payer": {"name": "Cedar Point Property Management", "tin": "46-2290174",
                  "address": _addr("1200 Lakeshore Blvd", "Cleveland", "OH", "44114"), "phone": "(216) 555-0172"},
        "recipient": {"name": "Thomas R Whitaker", "tin": "XXX-XX-8823",
                      "address": _addr("77 Birch Hollow Ln", "Akron", "OH", "44303")},
        "account_number": "PM-55102",
        "rents": "18,600.00",
        "royalties": "1,240.33",
        "other_income": "750.00",
        "federal_income_tax_withheld": "0.00",
        "medical_and_health_care_payments": None,
        "gross_proceeds_paid_to_attorney": None,
        "fatca_filing_requirement": False,
        "state_info": [{"state": "OH", "payer_state_id": "OH-77120934", "state_income": "20,590.33",
                        "state_tax_withheld": "411.81"}],
    },
    "1099_int": {
        "tax_year": 2025,
        "corrected": False,
        "payer": {"name": "First Harbor Savings Bank", "tin": "59-3381246",
                  "address": _addr("400 Main St", "Tampa", "FL", "33602"), "phone": "(813) 555-0190"},
        "recipient": {"name": "Angela M Brooks", "tin": "XXX-XX-2368",
                      "address": _addr("9 Palmetto Way", "Clearwater", "FL", "33755")},
        "account_number": "4471-009-221",
        "payer_rtn": "063107513",
        "interest_income": "1,382.17",
        "early_withdrawal_penalty": "45.00",
        "us_savings_bonds_and_treasury_interest": "210.50",
        "federal_income_tax_withheld": "0.00",
        "tax_exempt_interest": "96.40",
        "bond_premium": "12.75",
        "fatca_filing_requirement": False,
    },
    "1099_div": {
        "tax_year": 2025,
        "corrected": False,
        "payer": {"name": "Northgate Index Funds", "tin": "04-6129855",
                  "address": _addr("1 Financial Plaza", "Boston", "MA", "02110"), "phone": "(617) 555-0123"},
        "recipient": {"name": "Kevin J O'Brien", "tin": "XXX-XX-5590",
                      "address": _addr("315 Maple Ave", "Quincy", "MA", "02169")},
        "account_number": "88213-4410",
        "total_ordinary_dividends": "3,914.62",
        "qualified_dividends": "3,288.10",
        "total_capital_gain_distributions": "1,207.95",
        "unrecaptured_section_1250_gain": "88.12",
        "nondividend_distributions": "143.00",
        "federal_income_tax_withheld": "0.00",
        "section_199a_dividends": "214.37",
        "foreign_tax_paid": "61.08",
        "foreign_country": "Various",
        "exempt_interest_dividends": "402.50",
        "fatca_filing_requirement": False,
    },
    "1099_r": {
        "tax_year": 2025,
        "corrected": False,
        "payer": {"name": "Evergreen Retirement Services", "tin": "91-1543307",
                  "address": _addr("1500 4th Ave", "Seattle", "WA", "98101"), "phone": "(206) 555-0161"},
        "recipient": {"name": "Linda S Chen", "tin": "XXX-XX-3071",
                      "address": _addr("4020 Lake City Way NE", "Seattle", "WA", "98105")},
        "account_number": "IRA-7730215",
        "gross_distribution": "32,000.00",
        "taxable_amount": "32,000.00",
        "taxable_amount_not_determined": False,
        "total_distribution": False,
        "federal_income_tax_withheld": "6,400.00",
        "distribution_codes": "7",
        "ira_sep_simple": True,
        "fatca_filing_requirement": False,
        "date_of_payment": "06/14/2025",
    },
    "1099_g": {
        "tax_year": 2025,
        "corrected": False,
        "payer": {"name": "State of Colorado Dept of Labor and Employment", "tin": "84-6000583",
                  "address": _addr("633 17th St", "Denver", "CO", "80202"), "phone": "(303) 555-0110"},
        "recipient": {"name": "Jordan P Ellis", "tin": "XXX-XX-6142",
                      "address": _addr("2718 Pine Ridge Ct", "Aurora", "CO", "80013")},
        "account_number": "UI-0098123",
        "unemployment_compensation": "8,736.00",
        "state_or_local_income_tax_refunds": "412.00",
        "box2_tax_year": 2024,
        "federal_income_tax_withheld": "873.60",
        "trade_or_business_income": False,
        "state_info": [{"state": "CO", "payer_state_id": "CO-UI-4471", "state_tax_withheld": "384.38"}],
    },
    "ssa_1099": {
        "tax_year": 2025,
        "beneficiary": {"name": "Robert E Lawson", "tin": "XXX-XX-9128",
                        "address": _addr("61 Orchard Rd", "Lancaster", "PA", "17601")},
        "benefits_paid": "26,184.00",
        "benefits_repaid": "0.00",
        "net_benefits": "26,184.00",
        "voluntary_federal_income_tax_withheld": "2,618.40",
        "claim_number": "198-44-9128A",
        "medicare_part_b_premiums": "2,220.00",
    },
    "1098": {
        "tax_year": 2025,
        "corrected": False,
        "lender": {"name": "Keystone Home Lending NA", "tin": "23-1197642",
                   "address": _addr("2 Penn Center", "Philadelphia", "PA", "19102"), "phone": "(800) 555-0135"},
        "borrower": {"name": "Samuel and Priya Okafor", "tin": "XXX-XX-4806",
                     "address": _addr("118 Chestnut Hill Ave", "Philadelphia", "PA", "19118")},
        "account_number": "LN-30017755",
        "mortgage_interest_received": "14,982.36",
        "outstanding_mortgage_principal": "387,450.00",
        "mortgage_origination_date": "03/15/2021",
        "refund_of_overpaid_interest": "0.00",
        "mortgage_insurance_premiums": "1,104.00",
        "points_paid_on_purchase": "0.00",
        "property_address_same_as_borrower": True,
        "number_of_properties": "1",
    },
    "1098_t": {
        "tax_year": 2025,
        "corrected": False,
        "filer": {"name": "Riverside State University", "tin": "95-6006143",
                  "address": _addr("900 University Ave", "Riverside", "CA", "92521"), "phone": "(951) 555-0101"},
        "student": {"name": "Aisha N Rahman", "tin": "XXX-XX-7754",
                    "address": _addr("3300 Canyon Crest Dr Apt 12", "Riverside", "CA", "92507")},
        "account_number": "S00841276",
        "payments_received_for_qualified_tuition": "11,640.00",
        "scholarships_or_grants": "4,500.00",
        "includes_amounts_for_next_period": True,
        "at_least_half_time_student": True,
        "graduate_student": False,
    },
    "1098_e": {
        "tax_year": 2025,
        "corrected": False,
        "lender": {"name": "Pinnacle Student Loan Servicing", "tin": "52-2013874",
                   "address": _addr("PO Box 7400", "Wilkes-Barre", "PA", "18773"), "phone": "(888) 555-0177"},
        "borrower": {"name": "Tyler J Morgan", "tin": "XXX-XX-1209",
                     "address": _addr("540 W Madison St Unit 9", "Chicago", "IL", "60661")},
        "account_number": "SL-6619024",
        "student_loan_interest_received": "1,876.43",
        "excludes_origination_fees_or_capitalized_interest": False,
    },
    "k1_1065": {
        "tax_year": 2025,
        "partnership": {"name": "Blue Mesa Real Estate Partners LP", "tin": "86-3320417",
                        "address": _addr("7400 E Camelback Rd", "Scottsdale", "AZ", "85251")},
        "irs_center": "Ogden, UT",
        "publicly_traded_partnership": False,
        "partner": {"name": "Helen W Park", "tin": "XXX-XX-3364",
                    "address": _addr("12 Saguaro Ln", "Tempe", "AZ", "85281")},
        "general_partner_or_llc_member_manager": False,
        "limited_partner_or_other_llc_member": True,
        "domestic_partner": True,
        "foreign_partner": False,
        "partner_entity_type": "Individual",
        "profit_share_ending_percent": "12.5",
        "loss_share_ending_percent": "12.5",
        "capital_share_ending_percent": "12.5",
        "capital_account": {
            "beginning_capital_account": "150,000.00",
            "capital_contributed": "25,000.00",
            "current_year_net_income": "18,442.00",
            "withdrawals_and_distributions": "12,000.00",
            "ending_capital_account": "181,442.00",
        },
        "ordinary_business_income": "4,210.00",
        "net_rental_real_estate_income": "13,870.00",
        "interest_income": "362.00",
        "net_long_term_capital_gain": "1,950.00",
        "net_section_1231_gain": "-840.00",
        "distributions": [{"code": "A", "amount": "12,000.00"}],
        "other_information": [{"code": "Z", "amount": "4,210.00"}, {"code": "N", "amount": "512.00"}],
    },
    "1099_k": {
        "tax_year": 2025,
        "corrected": False,
        "filer": {"name": "Crestline Payments LLC", "tin": "45-3398120",
                  "address": _addr("250 Market St Ste 900", "San Francisco", "CA", "94105"), "phone": "(415) 555-0166"},
        "payee": {"name": "Harbor Coffee Roasters LLC", "tin": "81-2204736",
                  "address": _addr("22 Pier Rd", "Madison", "WI", "53704")},
        "filer_is_pse": True,
        "filer_is_epf_or_other_third_party": False,
        "payment_card_transactions": True,
        "third_party_network_transactions": False,
        "pse_name_and_phone": "Crestline Payments (800) 555-0129",
        "account_number": "MID-4471029",
        "gross_amount": "148,236.50",
        "card_not_present_transactions": "21,904.00",
        "merchant_category_code": "5814",
        "number_of_payment_transactions": "16,212",
        "federal_income_tax_withheld": "0.00",
        "january": "9,812.40", "february": "9,204.15", "march": "11,560.00", "april": "12,118.75",
        "may": "13,402.30", "june": "14,890.10", "july": "15,226.45", "august": "14,775.60",
        "september": "12,940.25", "october": "12,101.90", "november": "10,840.35", "december": "11,364.25",
        "state_info": [{"state": "WI", "payer_state_id": "WI-036-1178", "state_tax_withheld": "0.00"}],
    },
    "1095_a": {
        "tax_year": 2025,
        "marketplace_identifier": "17.ME*.512.002.010",
        "policy_number": "MKT-88140277",
        "policy_issuer": "Pine State Health Cooperative",
        "recipient_name": "Daniel R Fournier",
        "recipient_ssn": "XXX-XX-5512",
        "recipient_date_of_birth": "04/22/1986",
        "spouse_name": "Claire M Fournier",
        "spouse_ssn": "XXX-XX-8034",
        "spouse_date_of_birth": "09/03/1988",
        "policy_start_date": "01/01/2025",
        "policy_termination_date": "12/31/2025",
        "street_address": "48 Ocean House Rd",
        "city": "Cape Elizabeth",
        "state": "ME",
        "country_and_zip": "US 04107",
        "covered_individuals": [
            {"name": "Daniel R Fournier", "ssn": "XXX-XX-5512", "date_of_birth": "04/22/1986",
             "coverage_start_date": "01/01/2025", "coverage_termination_date": "12/31/2025"},
            {"name": "Claire M Fournier", "ssn": "XXX-XX-8034", "date_of_birth": "09/03/1988",
             "coverage_start_date": "01/01/2025", "coverage_termination_date": "12/31/2025"},
        ],
        "monthly": [
            {"month": m, "enrollment_premium": "1,184.00", "slcsp_premium": "1,262.00", "advance_ptc": "806.00"}
            for m in ("January", "February", "March", "April", "May", "June", "July", "August",
                      "September", "October", "November", "December")
        ],
        "annual_enrollment_premiums": "14,208.00",
        "annual_slcsp_premium": "15,144.00",
        "annual_advance_ptc": "9,672.00",
    },
}


# --- Multi-form documents -------------------------------------------------------------------

BROKER = {"name": "Lakeview Securities LLC", "tin": "41-1228890",
          "address": _addr("80 S 8th St Ste 1200", "Minneapolis", "MN", "55402"), "phone": "(612) 555-0144"}
INVESTOR = {"name": "Nora A Lindqvist", "tin": "XXX-XX-4417",
            "address": _addr("1907 Summit Ave", "St Paul", "MN", "55105")}
ACCOUNT = "5521-0098"

_TICKERS = ["APPLE INC", "MICROSOFT CORP", "VANGUARD TOTAL STK MKT ETF", "NVIDIA CORP", "TOTALENERGIES SE ADR",
            "JOHNSON & JOHNSON", "COSTCO WHOLESALE CORP", "ISHARES CORE S&P 500 ETF", "PROCTER & GAMBLE CO",
            "ALPHABET INC CL A", "VISA INC CL A", "PEPSICO INC"]


def _money(x: float) -> str:
    return f"{x:,.2f}" if x >= 0 else f"-{-x:,.2f}"


def broker_transactions(seed: int = 7) -> list[dict[str, Any]]:
    """Sales across three reporting categories, enough rows to run over several pages."""
    rng = random.Random(seed)
    rows = []
    for box, term, covered, count in (("A", "short", True, 18), ("D", "long", True, 16), ("E", "long", False, 5)):
        for i in range(count):
            shares = rng.choice([5, 10, 12, 20, 25, 40, 50, 75, 100])
            name = rng.choice(_TICKERS)
            proceeds = round(rng.uniform(800, 24_000), 2)
            basis = round(proceeds * rng.uniform(0.7, 1.2), 2)
            wash = round(rng.uniform(20, 300), 2) if term == "short" and basis > proceeds and i % 4 == 0 else None
            gain = round(proceeds - basis + (wash or 0), 2)
            sold = f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/2025"
            acquired = "VARIOUS" if box == "E" and i % 2 else (
                f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/{2025 if term == 'short' else rng.randint(2015, 2023)}")
            if term == "short" and acquired != "VARIOUS" and acquired[-4:] == "2025" and acquired[:5] > sold[:5]:
                acquired, sold = sold, acquired
            rows.append({
                "description": f"{shares} SH {name}", "cusip": f"{rng.randint(10**8, 10**9 - 1)}",
                "date_acquired": acquired, "date_sold": sold, "proceeds": _money(proceeds),
                "cost_basis": _money(basis), "wash_sale_loss_disallowed": _money(wash) if wash else None,
                "gain_or_loss": _money(gain), "term": term, "basis_reported_to_irs": covered, "form_8949_box": box,
            })
    return rows


CONSOLIDATED: dict[str, dict[str, Any]] = {
    "1099_div": {
        "tax_year": 2025, "payer": BROKER, "recipient": INVESTOR, "account_number": ACCOUNT,
        "total_ordinary_dividends": "2,846.19", "qualified_dividends": "2,402.77",
        "total_capital_gain_distributions": "611.40", "nondividend_distributions": "38.00",
        "federal_income_tax_withheld": "0.00", "section_199a_dividends": "127.55", "foreign_tax_paid": "44.20",
        "foreign_country": "Various",
    },
    "1099_int": {
        "tax_year": 2025, "payer": BROKER, "recipient": INVESTOR, "account_number": ACCOUNT,
        "interest_income": "418.62", "us_savings_bonds_and_treasury_interest": "1,036.50",
        "federal_income_tax_withheld": "0.00", "tax_exempt_interest": "212.00",
    },
    "1099_b": {
        "tax_year": 2025, "payer": BROKER, "recipient": INVESTOR, "account_number": ACCOUNT,
        "federal_income_tax_withheld": "0.00", "transactions": broker_transactions(),
    },
}

# Pages of other samples bound into one file, as a client scans a stack (None: not a tax form).
STACK = ["w2", "1099_nec", "1098_t", None]

