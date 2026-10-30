"""tables.py

Reads tables (1099-B sales, 1095-A covered individuals and months) row by row
from block positions. A small LLM can't transcribe hundreds of transaction
rows reliably within its context; this reads any length exactly.

1. A *header line* is a line where at least three blocks match the start of a
   column's printed header (`column(...)` metadata on the row model).
   Wrapped headers ("Date sold or" / "disposed") only need their first line.
2. Columns run from one header's left edge to the next. Amounts (right-
   aligned) are placed by their right edge, other text by its centre; the
   `lead` column (description / month) takes text that starts left of the
   first other column.
3. Each later line is split into cells by those boundaries. A line with at
   least two non-lead values is a row. A line with only lead text directly
   under a row, and narrow enough to fit the lead column, continues that
   row's description. Lines starting "Total" are skipped.
4. Lines the row model recognises as section headings (`section()`, e.g.
   "Short-term ... Box A") set fields for the rows that follow.

The header carries over to following pages until a new header appears. A
line whose values don't parse as the columns' types (another table's rows)
ends the table.

*Usage:*
>>> table = read_table(blocks, BrokerTransaction)
>>> table.rows[0]["proceeds"], len(table.used)
('5,210.00', 412)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from core.layout import Block, label_words, lines_of
from core.utils import parse_date, parse_money

TOLERANCE = 3.0  # units
_TOTAL = {"total", "totals", "subtotal", "subtotals"}


def _is_total(text: str) -> bool:
    """'Total Short-term', '33 Annual Totals', 'Grand total' -- not 'VANGUARD TOTAL STK MKT ETF'."""
    words = label_words(text).split()
    return bool(words) and (words[0] in _TOTAL or (len(words) > 1 and words[1] in _TOTAL
                                                   and words[0] in {"annual", "grand", "section", "account"}))


@dataclass
class Column:
    name: str
    aliases: list[str]
    lead: bool
    left: float = 0.0
    right: float = 0.0


@dataclass
class Table:
    rows: list[dict[str, Any]] = field(default_factory=list)
    lines: list[list[Block]] = field(default_factory=list)  # the printed line(s) of each row
    used: set[int] = field(default_factory=set)  # id() of every block consumed (header, rows, sections)


def _columns(model: type[BaseModel]) -> list[Column]:
    out = []
    for name, info in model.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        if extra.get("columns"):
            out.append(Column(name, [label_words(a) for a in extra["columns"]], bool(extra.get("lead"))))
    return out


def _prefix(short: str, long: str) -> bool:
    """`short` is `long` or its first whole words."""
    return long == short or long.startswith(short + " ")


def _header_score(text: str, column: Column) -> int:
    """Length of the column alias this header block matches (0: no match). Wrapped headers match by first words."""
    words = label_words(text)
    if len(words) < 3:
        return 0
    return max((len(alias) for alias in column.aliases if _prefix(words, alias) or _prefix(alias, words)), default=0)


def _match_header(line: list[Block], columns: list[Column]) -> list[Column] | None:
    found: dict[str, Column] = {}
    for b in line:
        score, best = max(((_header_score(b.text, c), c) for c in columns if c.name not in found),
                          key=lambda sc: sc[0], default=(0, None))
        if best is not None and score:
            found[best.name] = Column(best.name, best.aliases, best.lead, b.left, b.right)
    if len([c for c in found.values() if not c.lead]) < 3:
        return None
    placed = sorted(found.values(), key=lambda c: c.left)
    lead = next((c for c in columns if c.lead), None)
    if lead and lead.name not in found:  # unlabeled row-label column (e.g. the 1095-A months)
        placed.insert(0, Column(lead.name, lead.aliases, True, 0.0, placed[0].left))
    return placed


_AMOUNT = re.compile(r"^\(?-?\$?\s?[\d,]*\d\.\d{2}\)?-?$")


def _column_for(b: Block, header: list[Column]) -> Column:
    """
    Columns run from one header's left edge to the next one's. Amounts are
    right-aligned, so an amount is placed by its right edge; other text by its
    centre. This holds whether headers are printed left-aligned, centred or
    right-aligned over their values.
    """
    if header[0].lead and b.left < header[1].left - TOLERANCE:
        return header[0]  # left-aligned lead text can run past its column
    x = b.right - TOLERANCE if _AMOUNT.match(b.text.strip()) else (b.left + b.right) / 2
    index = 0
    for i, c in enumerate(header):
        if x >= c.left - TOLERANCE:
            index = i
    return header[index]


def _assign(line: list[Block], header: list[Column]) -> dict[str, str]:
    """Cell text per column."""
    cells: dict[str, list[str]] = {}
    for b in line:
        cells.setdefault(_column_for(b, header).name, []).append(b.text)
    return {name: " ".join(parts) for name, parts in cells.items()}


def _is_value(text: str) -> bool:
    for parse in (parse_money, parse_date):
        try:
            if parse(text) is not None:
                return True
        except ValueError:
            continue
    return "various" in text.lower()


def _fits(model: type[BaseModel], cells: dict[str, str], lead: str | None) -> tuple[int, int]:
    """(cells that parse as their column's type, cells that don't), lead column excluded."""
    good = bad = 0
    for name, text in cells.items():
        if name == lead:
            continue
        try:
            model.model_validate({name: text})
            good += 1
        except ValidationError:
            bad += 1
    return good, bad


def read_table(blocks: list[Block], model: type[BaseModel]) -> Table:
    columns = _columns(model)
    section = getattr(model, "section", None)
    lead = next((c.name for c in columns if c.lead), None)
    table = Table()
    header: list[Column] | None = None
    context: dict[str, Any] = {}
    last_row_line: list[Block] | None = None

    for line in lines_of(blocks):
        text = " ".join(b.text for b in line)
        new_header = _match_header(line, columns)
        if new_header:
            header, last_row_line = new_header, None
            table.used |= {id(b) for b in line}
            continue
        state = section(text) if section else None
        cells = _assign(line, header) if header else {}
        values = [v for k, v in cells.items() if k != lead and _is_value(v)]
        if state and len(values) < 2:
            context.update({k: v for k, v in state.items() if v is not None})
            table.used |= {id(b) for b in line}
            last_row_line = None
            continue
        if header is None:
            continue
        if _is_total(cells.get(lead) or text):
            last_row_line = None
            continue
        if len(values) >= 2:
            good, bad = _fits(model, cells, lead)
            if bad > good:  # another table's rows: this one has ended
                header, last_row_line = None, None
                continue
            table.rows.append({**context, **cells})
            table.lines.append(list(line))
            table.used |= {id(b) for b in line}
            last_row_line = line
        elif (lead and last_row_line and set(cells) == {lead} and line[0].page == last_row_line[0].page
              and line[0].top - last_row_line[0].bottom < 1.2 * (line[0].bottom - line[0].top)
              and line[-1].right <= header[1].left + TOLERANCE):  # wrapped text stays inside its column
            row = table.rows[-1]
            row[lead] = f"{row.get(lead) or ''} {cells[lead]}".strip()
            table.lines[-1] += line
            table.used |= {id(b) for b in line}
            last_row_line = line
        else:
            last_row_line = None
    return table
