"""library/review.py

The review contract of one extracted form, as plain functions over a stored
`FormResult` and its decisions:

* `checks`: every scalar field of the form schema, in schema order, as a flat
  `Check` (path, label, kind, value as extracted, where it was read, its
  flags, its review). Empty fields are included, so a reviewer can fill a box
  the extractor missed; list fields (W-2 box 12, state rows, 1099-B sales)
  contribute their extracted rows. Flags that name no check stay on the form.
* `corrected_fields`: the fields with every decision applied (corrected ->
  the reviewer's value, rejected -> null).
* `normalise`: parses a reviewer's value the way extracted values are parsed.
* `undecided`: flagged fields without a decision; approval requires none.

Field paths use the syntax of `validation.sources` and flags: `employee.tin`,
`box12[0].amount`.
"""

from __future__ import annotations

import re
import typing
from typing import Any, Iterator

from pydantic import BaseModel, ValidationError

from core.flags import Flag, field_path
from core.forms import FORMS, Address, Party, _submodel
from core.schema import FormResult, ValueSource
from core.utils import (
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
from library.schema import Check, Review, Scalar

KINDS = {parse_money: "money", parse_percent: "percent", parse_tin: "tin", parse_date: "date", parse_bool: "check",
         parse_state: "state", parse_year: "year", parse_month: "month", parse_acquired: "acquired"}
_TOKEN = re.compile(r"\w+|\[\d+\]")


class NotReviewable(ValueError):
    """Only extracted forms (status ok) are reviewed."""


class ApprovalBlocked(ValueError):
    """Flagged fields have no decision yet."""

    def __init__(self, undecided: list[str]):
        self.undecided = undecided
        super().__init__(f"{len(undecided)} flagged field(s) have no review decision.")


class InvalidValue(ValueError):
    """A corrected value that does not parse as the field's type."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"{field}: {message}")


# --- Schema leaves -----------------------------------------------------------------------------


def _human(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _kind(info) -> tuple[str, list[str] | None]:
    for meta in info.metadata:
        if getattr(meta, "func", None) in KINDS:
            return KINDS[meta.func], None
    for arg in (info.annotation, *typing.get_args(info.annotation)):
        if typing.get_origin(arg) is typing.Literal:
            return "choice", [str(a) for a in typing.get_args(arg)]
    return "text", None


def _leaves(model: type[BaseModel], data: dict | None, path: str = "", group: str | None = None,
            tin_label: str | None = None, row_source: ValueSource | None = None,
            sources: dict[str, ValueSource] | None = None) -> Iterator[dict[str, Any]]:
    """Scalar leaves of `model` with their values in `data` (a JSON dump), in schema order."""
    sources = sources or {}
    for name, info in model.model_fields.items():
        here = f"{path}.{name}" if path else name
        value = (data or {}).get(name)
        sub = _submodel(info.annotation)
        if sub is not None and typing.get_origin(info.annotation) is list:
            for i, row in enumerate(value or []):
                at = f"{here}[{i}]"
                yield from _leaves(sub, row, at, f"{info.description or _human(name)} #{i + 1}",
                                   row_source=sources.get(at), sources=sources)
        elif sub is not None:
            label = _human(name) if sub in (Party, Address) else (info.description or _human(name))
            extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
            yield from _leaves(sub, value, here, f"{group} › {label}" if group else label,
                               extra.get("tin_label"), row_source, sources)
        else:
            kind, options = _kind(info)
            label = tin_label if name == "tin" and tin_label else (info.description or _human(name))
            yield {"field": here, "label": label, "group": group, "kind": kind, "options": options, "value": value,
                   "source": sources.get(here) or row_source}


def _fields(form: FormResult) -> dict | None:
    return form.fields.model_dump(mode="json") if form.fields is not None else None


def checks(form: FormResult, reviews: dict[str, Review]) -> tuple[list[Check], list[Flag]]:
    """(checks, form-level flags). Form-level: flags without a field, or naming a field that has no check."""
    if form.fields is None:
        return [], list(form.validation.flags) if form.validation else []
    leaves = list(_leaves(type(form.fields), _fields(form), sources=form.validation.sources if form.validation else {}))
    by_field: dict[str | None, list[Flag]] = {}
    for flag in form.validation.flags if form.validation else []:
        by_field.setdefault(flag.field, []).append(flag)
    paths = {leaf["field"] for leaf in leaves}
    form_flags = [f for key, flags in by_field.items() if key not in paths for f in flags]
    return [Check(**leaf, flags=by_field.get(leaf["field"], []), review=reviews.get(leaf["field"]))
            for leaf in leaves], form_flags


def flagged_fields(form: FormResult) -> list[str]:
    """Paths of checks with at least one flag: what approval needs decided."""
    return [c.field for c in checks(form, {})[0] if c.flags]


def undecided(form_checks: list[Check]) -> list[str]:
    return [c.field for c in form_checks if c.flags and c.review is None]


# --- Values ----------------------------------------------------------------------------------


def _tokens(path: str) -> list[str | int]:
    return [int(t[1:-1]) if t.startswith("[") else t for t in _TOKEN.findall(path)]


def _set(data: dict, path: str, value: Scalar) -> None:
    """Set a leaf, creating missing objects on the way (a box inside a party the extractor found empty)."""
    tokens = _tokens(path)
    target: Any = data
    for token, following in zip(tokens, tokens[1:]):
        if isinstance(token, int):
            target = target[token]
        else:
            if target.get(token) is None:
                target[token] = [] if isinstance(following, int) else {}
            target = target[token]
    target[tokens[-1]] = value


def _get(data: Any, path: str) -> Any:
    for token in _tokens(path):
        if data is None:
            return None
        data = data[token] if isinstance(token, int) else data.get(token)
    return data


def corrected_fields(form: FormResult, reviews: dict[str, Review]) -> dict | None:
    """The fields with decisions applied, validated against the form model (so the shape stays the schema's)."""
    data = _fields(form)
    if data is None:
        return None
    for path, review in reviews.items():
        if review.decision != "confirmed":
            try:
                _set(data, path, review.value if review.decision == "corrected" else None)
            except (IndexError, KeyError, TypeError):
                continue  # a path that no longer exists in the form
    return FORMS[form.document_type].model_validate(data).model_dump(mode="json")


def normalise(form: FormResult, path: str, value: Scalar) -> Scalar:
    """A reviewer's value parsed like an extracted one ('1,234.50' -> 1234.5); raises `InvalidValue`."""
    data = _fields(form)
    _set(data, path, value)
    try:
        parsed = FORMS[form.document_type].model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        mine = [e for e in exc.errors() if field_path(e["loc"]) == path] or exc.errors()
        raise InvalidValue(path, mine[0]["msg"]) from None
    result = _get(parsed, path)
    if result is None:
        raise InvalidValue(path, "empty value; use the decision `rejected` when there is no correct value")
    return result
