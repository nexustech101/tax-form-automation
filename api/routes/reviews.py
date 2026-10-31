"""Form review: read a form as checks, record a decision per field, approve or reopen the form.

Decisions are recorded under the calling key (id and name). Owner scoping as in the library routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import ClientDep, LibraryDep, owner_scope, require_api_key, reviewer
from api.errors import responses
from core.schema import Envelope
from library.schema import Check, FormDetail, ReviewRequest

router = APIRouter(prefix="/v1/library/forms", tags=["Review"], dependencies=[Depends(require_api_key)],
                   responses=responses(401, 404))


@router.get("/{form_id}", response_model=Envelope[FormDetail])
def get_form(form_id: str, key: ClientDep, library: LibraryDep) -> Envelope[FormDetail]:
    """
    The form as a flat list of `checks` (every field in schema order, with its
    value, source box, flags and review), form-level `flags`, progress
    (`checked` / `total`, `open_flags`) and `corrected_fields`.
    """
    form = library.form(form_id, owner_scope(key))
    if form is None:
        raise HTTPException(404, "Form not found.")
    return Envelope(data=form)


@router.put("/{form_id}/reviews/{field:path}", response_model=Envelope[Check], responses=responses(409, 422, 503))
async def set_review(form_id: str, field: str, body: ReviewRequest, key: ClientDep,
                     library: LibraryDep) -> Envelope[Check]:
    """
    Record `confirmed`, `corrected` (with `value`, parsed like extracted
    values; `422 invalid_value` when it does not parse) or `rejected` for one
    field path (e.g. `box12[0].amount`). Replaces an earlier decision, and
    reopens an approved form. A change of value rebuilds the form's search
    chunks, so the assistant answers from reviewed values.
    """
    check = await library.review(form_id, field, body, reviewer(key), owner_scope(key))
    if check is None:
        raise HTTPException(404, "Form or field not found.")
    return Envelope(data=check)


@router.delete("/{form_id}/reviews/{field:path}", response_model=Envelope[Check], responses=responses(409, 503))
async def clear_review(form_id: str, field: str, key: ClientDep, library: LibraryDep) -> Envelope[Check]:
    """Remove the decision on one field (reopens an approved form)."""
    check = await library.review(form_id, field, None, reviewer(key), owner_scope(key))
    if check is None:
        raise HTTPException(404, "Form or field not found.")
    return Envelope(data=check)


@router.post("/{form_id}/approve", response_model=Envelope[FormDetail], responses=responses(409))
def approve(form_id: str, key: ClientDep, library: LibraryDep) -> Envelope[FormDetail]:
    """
    Mark the form reviewed. Every flagged field needs a decision first:
    otherwise `409 review_incomplete` with `details.undecided` (the field paths).
    """
    form = library.approve(form_id, reviewer(key), owner_scope(key))
    if form is None:
        raise HTTPException(404, "Form not found.")
    return Envelope(data=form)


@router.post("/{form_id}/reopen", response_model=Envelope[FormDetail], responses=responses(409))
def reopen(form_id: str, key: ClientDep, library: LibraryDep) -> Envelope[FormDetail]:
    """Set an approved form back to `pending`. Decisions are kept."""
    form = library.reopen(form_id, owner_scope(key))
    if form is None:
        raise HTTPException(404, "Form not found.")
    return Envelope(data=form)
