"""agent/tools.py

Tools the assistant can call: a provider-neutral JSON-schema spec plus the
code that runs it. One tool for now: `search_documents`.

Searches always run inside the request's scope (`ChatRequest.scope`) and the
caller's API key: the model can narrow a search with its own filters, never
widen it. A call that asks for something outside the scope gets an error
message back instead of results.
"""

from __future__ import annotations

from pydantic import ValidationError

from agent.rag import Library
from agent.schema import SearchFilters, SearchHit
from core.forms import FORMS

FILTERS = ("client", "tax_year", "document_type")


def search_spec(max_results: int) -> dict:
    return {
        "name": "search_documents",
        "description": (
            "Search the firm's stored tax and business documents by meaning and by keywords. Returns numbered "
            "excerpts with their document, client, form, tax year and page. Filters narrow the search."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in words or exact terms "
                                                           "(e.g. 'mortgage interest paid', 'account 5521-0098')."},
                "client": {"type": "string", "description": "Client name as stored."},
                "tax_year": {"type": "integer"},
                "document_type": {"type": "string", "enum": [*FORMS, "text"],
                                  "description": "Form id, or 'text' for letters, notes and other non-form content."},
                "limit": {"type": "integer", "minimum": 1, "maximum": max_results},
            },
            "required": ["query"],
        },
    }


async def search_documents(
    args: dict, *, library: Library, 
    scope: SearchFilters, owner: str | None
) -> list[SearchHit] | str:
    """Hits, or an error message for the model when the arguments are invalid or out of scope."""
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return "error: `query` is required"
    try:
        requested = SearchFilters.model_validate({k: args.get(k) for k in FILTERS})
    except ValidationError as exc:
        return f"error: invalid filters: {exc.errors()[0]['msg']}"
    for key, value in scope.model_dump(exclude_none=True).items():
        asked = getattr(requested, key)
        if asked is not None and asked != value and \
            not (key == "client" and str(asked).lower() == str(value).lower()):
            return f"error: searches are limited to {key} = {value}"
    filters = requested.model_copy(update=scope.model_dump(exclude_none=True))
    limit = args.get("limit") if isinstance(args.get("limit"), int) else None
    return await library.search(query, filters, owner, limit)
