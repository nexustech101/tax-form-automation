"""flags.py

Review flags: what extraction, verification and the consistency checks found
worth a reviewer's attention. Every producer emits `Flag`s, so a client pairs
a flag with its field without parsing text.

`field` is a field path in the syntax of `validation.sources` keys and review
checks (`employee.tin`, `box12[0].amount`), or None for the whole form.

| Code                 | Producer                        | Meaning                                                   |
|----------------------|---------------------------------|-----------------------------------------------------------|
| `not_printed`        | core.grounding                  | value removed: it is not printed in the document          |
| `wrong_label`        | core.grounding                  | value removed: printed only under another field's label   |
| `unlabeled`          | core.grounding                  | value kept, but not found in its labeled box              |
| `unparseable`        | core.extractor                  | value dropped: not a valid amount/date/TIN/...            |
| `multiple_values`    | core.extractor                  | several printed values in one box; the first was kept     |
| `text_as_json`       | core.extractor                  | the model returned JSON for a text field; flattened       |
| `no_output`          | core.extractor                  | the extraction model returned no usable JSON              |
| `inconsistent`       | core.forms (consistency_checks) | amounts don't add up (e.g. W-2 box 4 vs box 3)            |
| `partial_conversion` | core.parser                     | the file was only partly converted; values may be missing |
| `missing_page`       | core.parser                     | a page inside the form's page range produced no text      |
| `uncertain_type`     | core.pipeline                   | identified by its printed heading; the classifier unsure  |

Adding a code: add it to `FlagCode` (the OpenAPI schema lists it) and to this table.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from core.utils import mask_ssns

FlagCode = Literal[
    "not_printed", "wrong_label", "unlabeled", "unparseable", "multiple_values", "text_as_json", "no_output",
    "inconsistent", "partial_conversion", "missing_page", "uncertain_type",
]


class Flag(BaseModel):
    """Something a reviewer should check."""

    field: Optional[str] = Field(
        description="Field path (e.g. `employee.tin`, `box12[0].amount`), as in `sources`; null: the whole form.")
    code: FlagCode = Field(description="What kind of problem; see the table in core/flags.py.")
    message: str = Field(description="Human-readable explanation. SSNs in it are masked to the last four digits.")

    @field_validator("message")
    @classmethod
    def _mask(cls, message: str) -> str:
        return mask_ssns(message)  # messages quote printed values; they reach logs, alerts and the assistant


def field_path(parts: tuple | list) -> str:
    """*Usage:*
    >>> field_path(("box12", 0, "amount"))
    'box12[0].amount'
    """
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out
