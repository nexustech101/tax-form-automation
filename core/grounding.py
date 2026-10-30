"""grounding.py

Checks every extracted amount and TIN against where it is printed.

Each value is looked up in the paired layout (`core.layout.pair_blocks`):
the label anchors with the values attached to their boxes, plus unlabeled
text. Per value:

| Printed where                                    | Result                                    |
|--------------------------------------------------|-------------------------------------------|
| in the box of its own label                      | kept; source = that label and position    |
| only in other labels' boxes, own label found     | removed: swapped into the wrong field     |
| unlabeled text, or its own label isn't on the page | kept, with a warning to check it        |
| nowhere                                          | removed: not in the document              |

Zero is a value like any other: a blank box is not "0.00".

Values inside `rows(...)` lists (W-2 box 12, state rows, K-1 codes) are
looked up under the list's and the row model's labels. Rows read by
`core.tables` come from fixed positions and are not re-checked; their
source is the printed row.

*Usage:*
>>> result = verify(fields, pair_blocks(blocks, form_labels(form)), table_lines={"transactions": lines})
>>> result.dropped, result.sources["payer.tin"].label
(['federal_income_tax_withheld'], "PAYER'S TIN")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

from pydantic import BaseModel

from core.forms import _submodel
from core.layout import Block, _strip_box_prefix
from core.schema import ValueSource
from core.utils import parse_money, parse_tin

_AMOUNT = re.compile(r"\$?\d(?:[\d,.]*\d)?")
_TIN = re.compile(r"[\dX*]{2,3}[- ]?[\dX*]{2,7}[- ]?[\dX*]{0,4}")


@dataclass
class Verification:
    sources: dict[str, ValueSource] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Found:
    """One printed occurrence of a value."""

    anchor: Block | None  # None: unlabeled text
    block: Block


# --- Reading values out of text ----------------------------------------------------


def _amounts(text: str) -> set[Decimal]:
    out = set()
    for token in _AMOUNT.findall(text):
        try:
            amount = parse_money(token)
        except ValueError:
            continue
        if amount is not None:
            out.add(abs(amount))
    return out


def _tins(text: str) -> set[str]:
    out = set()
    for token in _TIN.findall(text.upper()):
        try:
            tin = parse_tin(token)
        except ValueError:
            continue
        if tin:
            out.add(tin.replace("-", ""))
    return out


def _key(value: object) -> tuple[str, object] | None:
    """What to look for in the text: ('amount', abs value) or ('tin', digits with X)."""
    if isinstance(value, Decimal):
        return "amount", abs(value)
    if isinstance(value, str):
        return "tin", value.replace("-", "")
    return None


class _Index:
    """Where every amount and TIN is printed, by label."""

    def __init__(self, paired: list[Block]):
        self.where: dict[tuple[str, object], list[_Found]] = {}
        attached = {id(v) for b in paired for v in b.values}
        for b in paired:
            if b.is_label:
                for v in b.values:
                    self._add(_Found(b, v))
            elif id(b) not in attached:
                self._add(_Found(None, b))
        self.anchors = [b for b in paired if b.is_label]

    def _add(self, found: _Found) -> None:
        for amount in _amounts(found.block.text):
            self.where.setdefault(("amount", amount), []).append(found)
        for tin in _tins(found.block.text):
            self.where.setdefault(("tin", tin), []).append(found)

    def labelled(self, labels: list[str]) -> list[Block]:
        """Anchors printing one of `labels` (box numbers ignored, wrapped labels matched by their start)."""
        wanted = [w for w in (_strip_box_prefix(label) for label in labels) if len(w) >= 2]
        out = []
        for a in self.anchors:
            text = _strip_box_prefix(a.text)
            for w in wanted:
                short, long = sorted((text, w), key=len)
                if text == w or (len(short) >= 8 and long.startswith(short)) \
                        or (len(short) >= 4 and SequenceMatcher(None, text, w).ratio() >= 0.82):
                    out.append(a)
                    break
        return out


# --- Field labels ------------------------------------------------------------------


def _labels(info, extra_key: str = "label") -> list[str]:
    """Printed labels a field may sit under: its metadata label and its description ('Item L: X' -> 'X')."""
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    out = [extra[extra_key]] if extra.get(extra_key) else []
    if extra_key == "label" and info.description:
        out += [info.description, info.description.split(": ", 1)[-1]]
    return out


def _row_labels(info) -> list[str]:
    """Labels for values inside a `rows(...)` list: the list's own and those of the row model's fields."""
    sub = _submodel(info.annotation)
    out = _labels(info)
    for sub_info in (sub.model_fields.values() if sub else []):
        out += _labels(sub_info)
    return out


# --- Verification ------------------------------------------------------------------


def _source(found: _Found) -> ValueSource:
    b = found.block
    return ValueSource(label=found.anchor.text if found.anchor else None, page=b.page,
                       box=(round(b.left, 1), round(b.top, 1), round(b.right, 1), round(b.bottom, 1)))


def _row_source(label: str, line: list[Block]) -> ValueSource:
    return ValueSource(label=label, page=line[0].page,
                       box=(round(min(b.left for b in line), 1), round(min(b.top for b in line), 1),
                            round(max(b.right for b in line), 1), round(max(b.bottom for b in line), 1)))


def verify(form: BaseModel, paired: list[Block], table_lines: dict[str, list[list[Block]]] | None = None) -> Verification:
    """
    Check every amount and TIN of `form` against `paired` (output of `pair_blocks`).
    Removes values that are not printed or sit under another label, in place.
    `table_lines`: field name -> printed lines of each row read by `core.tables`.
    """
    index = _Index(paired)
    result = Verification()
    tables = table_lines or {}

    def check(owner: BaseModel, name: str, value: object, path: str, labels: list[str]) -> None:
        key = _key(value)
        if key is None:
            return
        shown = f"TIN ending {str(value)[-4:]}" if key[0] == "tin" else str(value)  # warnings reach logs and emails
        found = index.where.get(key, [])
        own = index.labelled(labels)
        under_own = [f for f in found if f.anchor is not None and f.anchor in own]
        if under_own:
            result.sources[path] = _source(under_own[0])
        elif not found:
            setattr(owner, name, None)
            result.dropped.append(path)
            result.warnings.append(f"{path}: removed, {shown} is not printed in the document")
        elif own and all(f.anchor is not None for f in found):
            setattr(owner, name, None)
            result.dropped.append(path)
            other = found[0].anchor.text
            result.warnings.append(f"{path}: removed, {shown} is printed under '{other}', not under its own label")
        else:
            result.sources[path] = _source(found[0])
            result.warnings.append(f"{path}: {shown} is printed in the document, but not in its labeled box; check it")

    def walk(obj: BaseModel, path: str, inherited: list[str] | None) -> None:
        for name, info in type(obj).model_fields.items():
            value = getattr(obj, name)
            here = f"{path}{name}"
            labels = inherited if inherited is not None else _labels(info)
            if isinstance(value, list):
                if name in tables:  # read row by row from the layout: positions are the source
                    for i, line in enumerate(tables[name][:len(value)]):
                        result.sources[f"{here}[{i}]"] = _row_source(name, line)
                    continue
                for i, row in enumerate(value):
                    if isinstance(row, BaseModel):
                        walk(row, f"{here}[{i}].", _row_labels(info))
            elif isinstance(value, BaseModel):
                party_tin = _labels(info, "tin_label")
                if party_tin and getattr(value, "tin", None):
                    check(value, "tin", value.tin, f"{here}.tin", party_tin)
                walk(value, here + ".", inherited)
            elif isinstance(value, Decimal):
                check(obj, name, value, here, labels)
            elif (name == "ssn" or name.endswith("_ssn")) and isinstance(value, str):
                check(obj, name, value, here, labels)

    walk(form, "", None)
    return result
