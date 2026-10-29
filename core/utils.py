"""utils.py

Logging setup and lenient value parsers shared by the form schemas.

The parsers accept what OCR and LLMs actually produce ("$1,234.56",
"(12.00)", "X", "12/31/2025") and normalise it. They raise ``ValueError``
on values that cannot be interpreted so validation can report them.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# --- Logging -----------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extra fields passed via ``extra={"ctx": {...}}``."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if ctx:
            payload.update(ctx)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party loggers are noisy at INFO and some echo document text.
    for noisy in ("docling", "docling_core", "rapidocr", "RapidOCR", "httpx", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --- Parsers -----------------------------------------------------------------

_EMPTY = {"", "-", "--", "n/a", "na", "none", "null", "0-", "$"}
_TRUE = {"x", "[x]", "yes", "y", "true", "1", "checked", "☒", "☑", "✓", "✔", "on"}
_FALSE = {"no", "n", "false", "0", "unchecked", "☐", "[ ]", "off"}


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _EMPTY)


_GROUPED = re.compile(r"[(\-$\s]*(\d{1,3}(?:[.,]\d{3})*)[.,](\d{2})[)\-\s]*")


def parse_money(value: Any) -> Decimal | None:
    """
    *Usage:*
    >>> parse_money("$1,234.5")
    Decimal('1234.50')
    >>> parse_money("(12.00)")
    Decimal('-12.00')
    >>> parse_money("3.672.21")  # OCR read the thousands comma as a period
    Decimal('3672.21')
    """
    if is_blank(value):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
    else:
        text = str(value).strip()
        negative = text.startswith("(") and text.endswith(")") or text.startswith("-") or text.endswith("-")
        grouped = _GROUPED.fullmatch(text)
        if grouped:  # OCR mixes up ',' and '.': the separator before the last two digits is the decimal point
            digits = re.sub(r"[.,]", "", grouped.group(1)) + "." + grouped.group(2)
        else:
            digits = re.sub(r"[^\d.]", "", text)
        if not digits or digits.count(".") > 1:
            raise ValueError(f"not an amount: {value!r}")
        try:
            amount = Decimal(digits)
        except InvalidOperation as exc:
            raise ValueError(f"not an amount: {value!r}") from exc
        amount = -amount if negative else amount
    return amount.quantize(Decimal("0.01"))


def parse_percent(value: Any) -> Decimal | None:
    """*Usage:*
    >>> parse_percent("25.5%")
    Decimal('25.5000')
    """
    if is_blank(value):
        return None
    text = str(value).replace("%", "").replace(",", "").strip()
    try:
        return Decimal(text).quantize(Decimal("0.0001"))
    except InvalidOperation as exc:
        raise ValueError(f"not a percentage: {value!r}") from exc


_TIN_CHARS = re.compile(r"[\dXx*]")


def parse_tin(value: Any) -> str | None:
    """
    Normalise an SSN / ITIN / EIN. Masked values (``XXX-XX-1234``) are kept.

    *Usage:*
    >>> parse_tin("123456789")
    '123-45-6789'
    >>> parse_tin("12-3456789")
    '12-3456789'
    >>> parse_tin("***-**-6789")
    'XXX-XX-6789'
    """
    if is_blank(value):
        return None
    text = str(value).strip()
    chars = "".join(_TIN_CHARS.findall(text)).upper().replace("*", "X")
    if len(chars) != 9:
        raise ValueError(f"TIN must have 9 digits: {value!r}")
    if re.fullmatch(r"[\dX]{2}-[\dX]{7}", text.upper().replace("*", "X").replace(" ", "")):
        return f"{chars[:2]}-{chars[2:]}"  # EIN layout
    return f"{chars[:3]}-{chars[3:5]}-{chars[5:]}"


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y/%m/%d")


def parse_date(value: Any) -> date | None:
    """*Usage:*
    >>> parse_date("03/15/2021")
    datetime.date(2021, 3, 15)
    """
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = re.sub(r"\s+", " ", str(value).strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {value!r}")


def parse_acquired(value: Any) -> date | str | None:
    """1099-B box 1b: a date, or 'VARIOUS' for lots bought on several dates.

    *Usage:*
    >>> parse_acquired("Various"), parse_acquired("01/15/2024")
    ('VARIOUS', datetime.date(2024, 1, 15))
    """
    if not is_blank(value) and "various" in str(value).lower():
        return "VARIOUS"
    return parse_date(value)


_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def parse_month(value: Any) -> str | None:
    """Month name from a row label.

    *Usage:*
    >>> parse_month("21 January"), parse_month("Sept.")
    ('January', 'September')
    """
    if is_blank(value):
        return None
    word = re.sub(r"[^a-z]", "", str(value).lower())
    for month in _MONTHS:
        if len(word) >= 3 and (word.startswith(month[:3]) or month in word):
            return month.capitalize()
    raise ValueError(f"not a month: {value!r}")


def parse_bool(value: Any) -> bool | None:
    """Checkbox state. A blank box is ``False`` only when explicitly unchecked.

    *Usage:*
    >>> parse_bool("X"), parse_bool("☐"), parse_bool(None)
    (True, False, None)
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE or text == "":
        return False
    raise ValueError(f"not a checkbox state: {value!r}")


def parse_year(value: Any) -> int | None:
    """*Usage:*
    >>> parse_year("Tax year 2025")
    2025
    """
    if is_blank(value):
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    if not match:
        raise ValueError(f"not a year: {value!r}")
    return int(match.group())


def clean_text(value: Any) -> str | None:
    if is_blank(value):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_state(value: Any) -> str | None:
    """*Usage:*
    >>> parse_state(" ca ")
    'CA'
    """
    text = clean_text(value)
    if text is None:
        return None
    if not re.fullmatch(r"[A-Za-z]{2}", text):
        raise ValueError(f"not a 2-letter state code: {value!r}")
    return text.upper()


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)
