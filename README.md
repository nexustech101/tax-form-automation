# Tax Document API

FastAPI service for classifying and extracting data from 15 US tax form types. It accepts digital PDFs and scanned documents, separates supported forms, and returns typed JSON with structured review flags.

The pipeline uses Docling for layout and OCR, Ollama for field extraction, and either printed headings or the tev1 model for classification. A document library stores uploaded files, their extracted forms and each reviewer's decisions; a review dashboard (`web/`) and an AI assistant with read-only tools work on it, using local models by default. An optional n8n workflow handles intake and review.

**v2 breaking change:** `validation.warnings` (strings) is replaced by `validation.flags` (objects with `field`, `code`, `message`), and the assistant response's `searches` count by `tool_calls`. A library database created before v2 is refused on first use (`500 outdated_library`): delete `data/library.db` and add the documents again.

Output requires human review against the source document. See [DISCLAIMER.md](DISCLAIMER.md) and [third-party licenses](THIRD_PARTY_LICENSES.md).

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and a running [Ollama](https://ollama.com) server. The default classifier requires Ollama's `systemone` API and the `tev1` model.

From the repository root:

Optionally:

```sh
ollama pull tev1
ollama pull qwen3.5:4b
ollama pull nomic-embed-text   # document library search
```

Then,

```sh
uv sync --locked
uv run main.py create-key --name admin --admin
uv run main.py create-key --name client
uv run main.py serve --host 127.0.0.1 --port 8000
```

Save each generated key when it is printed; it is shown only once. Use the client key for document requests and the admin key for key management.

Open [interactive API docs](http://localhost:8000/docs) or [health status](http://localhost:8000/health). Docling loads its models at startup and may download weights on the first run. In Swagger, clear unused optional fields if they contain the placeholder `string`.

For a smaller model footprint, set `TEV_MODEL=none` in `.env` and omit `ollama pull tev1`. Both extraction and classification then identify forms by printed headings; unreadable or missing headings can produce `unknown`. The extraction LLM is still required.

## API usage

Document routes require an `X-API-Key` header. Successful responses use a `data` envelope; errors use an `error` object with `code`, `message`, `request_id`, and optional `details`. Responses include `X-Request-ID`; callers may supply their own.

| Route | Authentication | Purpose |
|---|---|---|
| `POST /v1/documents/extract` | Client or admin key | Extract one form from one file |
| `POST /v1/documents/extract/bulk` | Client or admin key | Extract forms from one or more files |
| `POST /v1/documents/classify` | Client or admin key | Classify a file or supplied text |
| `GET /v1/document-types` | Client or admin key | List supported forms: `id`, short `name` (`W-2`), `title`, field schema |
| `POST /v1/library/documents` | Client or admin key | Store a document: original file, forms, search chunks |
| `GET /v1/library/documents` | Client or admin key | List documents (filters, review counts) |
| `GET` / `DELETE /v1/library/documents/{id}` | Client or admin key | Read (pages, forms) or delete one document |
| `GET /v1/library/documents/{id}/pages/{n}` | Client or admin key | One page as PNG (`?width=`) |
| `GET /v1/library/documents/{id}/file` | Client or admin key | Download the original file |
| `GET /v1/library/stats` | Client or admin key | Dashboard counts (same filters as the list) |
| `GET /v1/library/forms/{form_id}` | Client or admin key | A form as review checks |
| `PUT` / `DELETE /v1/library/forms/{form_id}/reviews/{field}` | Client or admin key | Set or clear the decision on one field |
| `POST /v1/library/forms/{form_id}/approve` / `.../reopen` | Client or admin key | Approve a form / set it back to pending |
| `POST /v1/library/search` | Client or admin key | Search the library |
| `POST /v1/assistant/chat` | Client or admin key | Ask the assistant about library documents |
| `GET /health` | None | Report parser and model readiness |
| `GET /auth/me` | Client or admin key | Check the current key |
| `POST /api-keys` | Admin key | Create a key |
| `GET /api-keys` | Admin key | List keys |
| `DELETE /api-keys/{id}` | Admin key | Revoke a key |

Accepted file types: PDF, PNG, JPEG, TIFF, BMP, and WEBP, detected from file bytes. Multi-frame images are normalized to one image; use a multi-page PDF for document stacks.

### Extract one form

Replace `YOUR_CLIENT_KEY` with the generated client key. In Windows PowerShell, use `curl.exe` if `curl` resolves to an alias.

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -F "file=@tests/samples/w2.pdf" http://localhost:8000/v1/documents/extract
```

The response includes `document_type`, `form_name`, `classification`, `fields`, `validation`, `document`, `extraction_model`, and `timings_ms` inside `data`.

- `fields` follows the form schema published by `GET /v1/document-types`. Amounts are JSON numbers, dates are normalized, and checkboxes are booleans or `null`. Missing scalar values generally become `null`; repeated fields use arrays.
- `validation` reports `flags`, field counts and `sources`; `valid` is true when there are no flags. A valid schema is not proof of extraction accuracy or completeness.
- Each flag is `{field, code, message}`. `field` is a field path (`employee.tin`, `box12[0].amount`, the syntax of `sources` keys) or `null` for the whole form; `message` never contains a full TIN. Codes ([core/flags.py](core/flags.py), listed in the OpenAPI schema as `Flag.code`):

| `code` | Meaning |
|---|---|
| `not_printed` | Value removed: not printed in the document |
| `wrong_label` | Value removed: printed only under another field's label (swapped) |
| `unlabeled` | Value kept, but not found in its labeled box: check it |
| `unparseable` | Value dropped: not a valid amount, date, TIN, ... |
| `multiple_values` | Several printed values in one box; the first was kept |
| `text_as_json` | The model returned JSON for a text field; flattened |
| `no_output` | The extraction model returned no usable JSON (form-level) |
| `inconsistent` | Amounts don't add up (e.g. W-2 box 4 vs 6.2% of box 3); on the field that looks off |
| `partial_conversion` | The file was only partly converted (form-level) |
| `missing_page` | A page inside the form's page range produced no text (form-level) |
| `uncertain_type` | Identified by its printed heading; the classifier was not confident (form-level) |

- Every amount and TIN is checked against where it is printed ([core/grounding.py](core/grounding.py), run by `LLMExtractor.extract`). A value must sit in the box of its own label; it is removed (`not_printed`) when it is not printed at all (zero included: a blank box is not `0.00`), or (`wrong_label`) when printed only under another label. A value printed in the document but not in a labeled box, or whose label isn't found, is kept (`unlabeled`). Other values (names, addresses, codes, dates, years, checkboxes) are only located, never removed or flagged: the first printed block containing the value, under the field's own label first. `validation.sources` maps each kept amount or TIN, each located value (and each table row, e.g. `transactions[3]`) to `{label, page, box}`, with `box` as left, top, right, bottom on a page 1000 units wide, origin top-left; a value not found has no entry.
- `document.conversion` is `partial` when Docling reported errors (listed in `conversion_errors`); `document.missing_pages` lists pages no text was read from (failed, blank or unreadable). A form gets a `partial_conversion` flag when the conversion was partial and a `missing_page` flag when a page inside its page range is missing. A file with no readable text at all returns `422 unreadable_document`.
- Add `?document_type=w2` to treat the entire file as a known form, bypassing splitting and classification.
- A detected multi-form file returns `422 multiple_forms`; send it to the bulk route. Unidentified forms return `422 unsupported_document_type`.

### Extract multiple forms

Use the multipart field `files`, repeated for each file:

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -F "files=@tests/samples/stack.pdf" -F "files=@tests/samples/consolidated_1099.pdf" http://localhost:8000/v1/documents/extract/bulk
```

The response contains `data.files`, `data.summary`, and `data.timings_ms`. Each file includes metadata and a `forms` array. Each form identifies its source pages and has one of these statuses:

| Form status | Meaning |
|---|---|
| `ok` | Identified and extracted; inspect `validation.flags` |
| `unsupported` | Segment could not be identified as a supported form; `fields` is `null` |
| `duplicate` | Same form type and every extracted value identical (tax year, parties, account, amounts, table rows) to an earlier form in this file, `duplicate_of`. Forms with no TIN and no amount are never duplicates |

Parsing failures are reported as file-level `status: failed` with an `error`; other files continue. Each file's `document` carries `conversion` and `missing_pages` as above. Upload validation errors and model backend failures can fail the whole request. Summary `forms` and `by_type` count only forms with status `ok`.

Bulk processing is synchronous and sequential. Large statements can take minutes; set client timeouts accordingly or submit one file per request.

### Classify without extracting

Send exactly one of `file` or `text`:

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -F "file=@tests/samples/w2.pdf" http://localhost:8000/v1/documents/classify
curl -H "X-API-Key: YOUR_CLIENT_KEY" -F "text=Form 1099-INT Interest Income 2025" http://localhost:8000/v1/documents/classify
```

This route returns one classification, using the configured classifier. Digital PDFs use embedded text from the first two pages when available; scans and images go through OCR. Use bulk extraction to separate multiple forms. `document` metadata can be `null` for text input or the embedded-text fast path.

### Error codes

| HTTP status | Common `error.code` values |
|---|---|
| 400 | `bad_request` |
| 401 / 403 | `unauthorized` / `forbidden` |
| 413 | `payload_too_large`, `too_many_pages` |
| 415 | `unsupported_media_type` |
| 404 | `not_found` |
| 409 | `index_mismatch` (library built with a different embedding model); `review_incomplete` (approval with undecided flagged fields, listed in `details.undecided`); `not_reviewable` (unsupported or duplicate form) |
| 422 | `unreadable_document`, `unsupported_document_type`, `multiple_forms`, `invalid_request`, `invalid_value` (a corrected value that does not parse; `details.field`) |
| 429 | `rate_limited` (includes `Retry-After`) |
| 503 | `model_unavailable` (includes `Retry-After: 10`); `busy` when all extraction slots and the queue are taken (includes `Retry-After: 30`) |
| 500 | `internal_error`; `outdated_library` (library database from before v2) |

## Document library, review and assistant

The library is the system of record for uploaded documents: the original file, every form found in it with its extracted fields and flags, each reviewer's decisions, and the search chunks the assistant reads. The review routes serve the dashboard; the assistant answers questions from the same records. Embeddings and chat call model providers over plain HTTP (no SDKs), configured in [config/model_config.yml](config/model_config.yml): chat through the OpenAI-compatible API (Ollama, OpenAI, OpenRouter) or Anthropic's Messages API, embeddings through the OpenAI-compatible API. The shipped configuration is local: `qwen3.5:4b` and `nomic-embed-text` on Ollama. API keys for cloud providers are read from the environment variable named by `api_key_env`, never from the file.

Each key sees only the documents it added; admin keys see all of them. This applies to every library, review and assistant route: another key's document is a `404`.

### Add and search documents

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -F "file=@tests/samples/w2.pdf" -F "client=Gonzalez" -F "tax_year=2025" http://localhost:8000/v1/library/documents
curl -H "X-API-Key: YOUR_CLIENT_KEY" -H "Content-Type: application/json" -d '{"query": "wages and federal withholding", "filters": {"client": "Gonzalez", "tax_year": 2025}}' http://localhost:8000/v1/library/search
```

- Accepted: PDF and images (as above), `.txt`, `.md`, `.csv`, `.xlsx`. PDFs and images go through form extraction (`extract_file`, as the bulk route): every form found is stored with its status (`ok`, `unsupported`, `duplicate`), pages, classification, fields and validation; forms with status `ok` start with `review_status: pending`. Search chunks: each `ok` form as `path: value` lines with its form ID and tax year, plus the text of pages no form was extracted from. `extract=false` stores page text only (faster, no LLM). Up to `BULK_MAX_PAGES` pages.
- Text files have no forms. They are split by Markdown heading, CSV and sheet rows (header repeated in each chunk), or paragraphs, into chunks of at most `library.chunk_chars`.
- The original file is kept under `library.files` (default `data/files/`), named by its SHA-256; two keys uploading the same file share one copy, removed with the last document using it.
- `client`, `tax_year`, `title` and `location` (where the original came from) are optional metadata. Uploading the same file again returns the stored document (200 instead of 201) without re-processing.
- Social Security numbers are masked to the last four digits in every search chunk before it is stored or embedded. Stored form fields keep values as extracted, so a reviewer can compare them with the page.

Search filters (`client`, `tax_year`, `document_type`, `document_ids`) are applied in the database first, so only matching chunks are scored. The remaining chunks are ranked twice, by embedding similarity (exact cosine, scored in batches) and by keywords (SQLite FTS5, which finds exact account numbers and names), and the two rankings are combined with reciprocal rank fusion. `similarity` in each hit is the cosine similarity, `null` for keyword-only matches. Exact scoring stays fast to several hundred thousand chunks per filter; beyond that, an approximate vector index can replace `library.search.semantic` without changing the API.

### Documents, pages and stats

| Route | Contract |
|---|---|
| `GET /v1/library/documents` | Query: `client`, `tax_year`, `document_type` (form ID or `text`; match any chunk), `review_status` (`pending`: a form awaits approval; `approved`: every `ok` form approved), `q` (substring of title or client), `limit` (≤200), `offset`, `order` (`newest` / `oldest`). `data: {count, documents[]}`; each document has `id, title, type, media_type, length_bytes, location, content_hash, client, tax_year, created_at, chunks, document_types, page_count, review_status` (`null` without forms), `forms_pending, forms_approved, open_flags` |
| `GET /v1/library/documents/{id}` | The document plus `pages: [{n, width, height}]` (PDF points or image pixels) and `forms: [{id, index, status, pages, document_type, form_name, duplicate_of, review_status, open_flags}]` |
| `GET /v1/library/documents/{id}/pages/{n}` | `image/png`, `width` 100–2400 px (default 1200). PDFs are rendered at that width, images scaled to it. `ETag` = `"<sha256>-<n>-<width>"` (`If-None-Match` gives 304), `Cache-Control: private, max-age=86400`. A `ValueSource.box` maps onto the image by multiplying each coordinate by `width / 1000` |
| `GET /v1/library/documents/{id}/file` | The uploaded bytes, `Content-Disposition: attachment` |
| `GET /v1/library/stats` | Same query filters as the list. `data: {documents, forms` (status `ok`), `forms_by_type, forms_by_review_status: {pending, approved}, open_flags, clients: [{client, documents, forms_pending, open_flags}]}` |

`open_flags` counts flagged fields (fields with at least one flag that names them) without a review decision.

### Review a form

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" http://localhost:8000/v1/library/forms/FORM_ID
curl -X PUT -H "X-API-Key: YOUR_CLIENT_KEY" -H "Content-Type: application/json" -d '{"decision": "corrected", "value": "68,412.55"}' http://localhost:8000/v1/library/forms/FORM_ID/reviews/wages_tips_other_comp
curl -X POST -H "X-API-Key: YOUR_CLIENT_KEY" http://localhost:8000/v1/library/forms/FORM_ID/approve
```

`GET /v1/library/forms/{form_id}` returns the form summary fields plus `document_id, classification, text_layer, extraction_model, approved_by, approved_at`, and:

- `checks`: every scalar field of the form schema in schema order, empty ones included (so a reviewer can fill a box the extractor missed); list fields contribute their extracted rows only. Each check: `field` (path), `label` (the field's description, e.g. `Box 1: Wages, tips, other compensation`; a party's TIN uses its printed TIN label), `group` (`Employee`, `Employee › Address`, `Box 12a-12d: code and amount (e.g. D, DD, W) #1`, or `null`), `kind` (`money, percent, tin, date, check, state, year, month, acquired, choice, text`), `options` (for `choice`), `value` (as extracted, JSON scalar), `source` (`{label, page, box}` or `null`; table rows use the row's box), `flags`, `review` (`{decision, value, note, reviewer, reviewer_id, updated_at}` or `null`).
- `flags`: form-level flags (no field, or a field without a check).
- `checked` (checks with a decision), `total`, `open_flags`.
- `corrected_fields`: the fields with every decision applied; `null` for unsupported forms.

Rules ([library/review.py](library/review.py), [library/service.py](library/service.py)):

- `PUT .../reviews/{field}` body `{decision, value?, note?}`: `confirmed` (value right), `corrected` (`value` required, parsed like extracted values: `"1,234.50"` → `1234.5`, dates to ISO; `422 invalid_value` otherwise), `rejected` (the value is wrong and has no correct value: `null` in `corrected_fields`). Returns the updated check. `DELETE` clears the decision. Unknown fields are `404`; unsupported and duplicate forms are `409 not_reviewable`. The decision records the calling key's id and name.
- `POST .../approve` requires a decision on every flagged field: otherwise `409 review_incomplete` with `details.undecided` (field paths). `POST .../reopen` sets the form back to `pending`; so does any `PUT` or `DELETE` of a decision on an approved form.
- When a decision changes the form's values, its search chunks are rebuilt from `corrected_fields` in the same transaction, so search and the assistant answer from reviewed values.

### Ask the assistant

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -H "Content-Type: application/json" -d '{"messages": [{"role": "user", "content": "What were Maria Gonzalez'"'"'s 2025 wages?"}], "scope": {"client": "Gonzalez"}}' http://localhost:8000/v1/assistant/chat
```

The route is stateless: send the conversation so far, ending with the user's message. The model calls tools (up to `assistant.max_tool_rounds` rounds, then it must answer) and cites search excerpts as `[n]`. Response `data`: `message`, `sources` (every excerpt `search_documents` retrieved, numbered as cited, with document ID, page and similarity), `tool_calls` (`[{name, arguments}]`, in order), `model`, `usage` (the provider's token counts).

Tools ([agent/tools.py](agent/tools.py)), all read-only:

| Tool | Arguments | Returns to the model |
|---|---|---|
| `search_documents` | `query`, `client`, `tax_year`, `document_type`, `limit` | Numbered excerpts with document ID, client, form, year, page |
| `list_documents` | `client`, `tax_year`, `document_type`, `review_status`, `limit` | One line per document: ID, title, client, year, forms, review counts, open flags |
| `get_document` | `document_id` | Metadata, each form with review status and open flags, reviewed values (SSNs masked) |
| `classify_document` | `document_id`, `pages` | The configured classifier's result on the stored file (`pages`: a subset of a PDF) |
| `extract_document` | `document_id`, `document_type` | Docling + extraction on the stored file: fields and flags, not saved. Holds an extraction slot (`MAX_CONCURRENT_JOBS`) |

`scope` (`client`, `tax_year`, `document_type`, `document_ids`) limits every tool; the model can narrow it but not widen it, and a document outside it is reported as not found. Invalid arguments and processing errors (busy, unreadable file, model unavailable) go back to the model as `error: ...` text.

Configuration notes:

- Changing the embedding model or `dimensions` makes existing vectors incomparable; searches then return `409 index_mismatch`. Delete the library database (or use a new `library.database` path) and add the documents again.
- `nomic-embed-text` expects the `search_query: ` / `search_document: ` prefixes set in the file; set both to `""` for other embedding models.
- Ollama's OpenAI-compatible API cannot set the context size per request. If long answers or many excerpts get cut off, start the Ollama server with a larger `OLLAMA_CONTEXT_LENGTH` (e.g. `8192`).
- `chat.options` adds fields to each request body as is (for example `reasoning_effort`); `max_completion_tokens` there replaces `max_tokens` for models that require it.
- A provider that cannot be reached, or rejects the request, returns `503 model_unavailable` with its error in the message.

Code: [library/](library/) (storage, search, files, review rules; module map in [library/__init__.py](library/__init__.py)), [agent/](agent/) (chat wire formats and tool loop in [agent/agent.py](agent/agent.py), tools in [agent/tools.py](agent/tools.py)), [api/routes/library.py](api/routes/library.py), [api/routes/reviews.py](api/routes/reviews.py), [api/routes/assistant.py](api/routes/assistant.py). [docs/DEVELOPING.md](docs/DEVELOPING.md) traces each request and has recipes for adding routes, tools and flag codes.

## Dashboard

When `web/dist/index.html` exists, the API serves the built dashboard at `/` ([api/web.py](api/web.py)): files by path, `index.html` for any other `GET` outside the API paths (`/v1/`, `/api-keys`, `/auth/`, `/health`, `/docs`, `/redoc`, `/openapi.json`), whose unknown paths stay JSON `404`s. Without a build, `/` redirects to `/docs`.

Overview (counts, forms by type, review queue, clients), documents (filters, upload, delete), the review workspace (page viewer with source boxes, each value beside a crop of where it was read, flags, confirm / correct / reject, approval, keyboard shortcuts), the assistant (slide-over on every page and a full page; scope by client, tax year or attached documents, upload from the chat; citations open the document at the page) and settings (theme, API keys for admins). Sign in with an API key. Structure, request traces and recipes: [web/README.md](web/README.md).

```sh
cd web
npm ci
npm run dev          # http://localhost:5173, API paths proxied to http://127.0.0.1:8000 (API_URL to change)
npm run build        # web/dist, served by the API at / (restart the API after the first build)
npm run typecheck
npm test             # unit tests
npm run e2e          # build + Playwright on tests.e2e.stack (fake models; set HF_HUB_CACHE if Docling weights are elsewhere)
```

The dashboard's API types are generated from `web/src/api/openapi.json`. After changing an API model or route, regenerate both (no models are loaded):

```sh
uv run python -m scripts.openapi     # web/src/api/openapi.json; tests/test_web.py fails while it is stale
cd web
npm run gen:api                      # web/src/api/schema.d.ts; npm test fails while it is stale
```

## Supported forms

Form IDs are used by `document_type` and the schema endpoint. Field definitions live in [core/forms.py](core/forms.py).

| ID | Form | Main data |
|---|---|---|
| `w2` | W-2 | Wages, withholding, coded boxes, state/local rows |
| `1099_nec` | 1099-NEC | Nonemployee compensation |
| `1099_misc` | 1099-MISC | Rents, royalties, other income |
| `1099_int` | 1099-INT | Interest income and related amounts |
| `1099_div` | 1099-DIV | Dividends and capital gain distributions |
| `1099_b` | 1099-B | Sales, proceeds, basis, wash sales, transaction categories |
| `1099_r` | 1099-R | Retirement distributions and distribution codes |
| `1099_g` | 1099-G | Government payments |
| `1099_k` | 1099-K | Payment transactions and monthly amounts |
| `ssa_1099` | SSA-1099 | Social Security benefits |
| `1098` | 1098 | Mortgage interest and loan details |
| `1098_t` | 1098-T | Tuition, scholarships, student status |
| `1098_e` | 1098-E | Student loan interest |
| `1095_a` | 1095-A | Coverage, covered individuals, monthly and annual amounts |
| `k1_1065` | Schedule K-1 (Form 1065) | Partnership income, coded items, capital account |

## Configuration

Settings are read from environment variables and the repository-root `.env`. Start with [.env.example](.env.example); [core/config.py](core/config.py) defines the complete settings model.

### Models and parsing

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Model server URL; separate from Ollama's server-side `OLLAMA_HOST` |
| `OLLAMA_TIMEOUT_S` / `OLLAMA_KEEP_ALIVE` | `180` / `30m` | Model request timeout and residency |
| `TEV_MODEL` | `tev1` | Classifier model; `none` selects heading-only classification |
| `TEV_MIN_CONFIDENCE` / `TEV_MAX_STATE_CHARS` | `0.5` / `3000` | Classifier acceptance threshold and input limit |
| `EXTRACTOR_MODEL` / `EXTRACTOR_NUM_CTX` | `qwen3.5:4b` / `8192` | Extraction model and context size (tokens) |
| `EXTRACTOR_OUTPUT_TOKENS` | `2048` | Answer budget (`num_predict`); evidence chunks get the rest of the context |
| `STRICT_GROUNDING` | `true` | Check extracted amounts and TINs against their printed labels and positions, and record where each value was read (`validation.sources`, used by the review) |
| `DOCLING_DEVICE` / `DOCLING_THREADS` | `auto` / `4` | Device (`auto`, `cpu`, `cuda`, `mps`) and CPU threads |
| `DOCLING_MAX_CONCURRENCY` | `1` | Concurrent conversions per process |
| `DOCLING_TIMEOUT_S` | `120` | Document conversion timeout |
| `DOCLING_TABLES` | `false` | Enable TableFormer; the pipeline already has a separate table reader |
| `DOCLING_WARMUP` | `true` | Run a small conversion at startup |
| `DOCLING_ARTIFACTS_PATH` | Unset | Local Docling model directory |

To prepare Docling weights for offline use:

```sh
uv run main.py download-models --to models/docling
```

Then set `DOCLING_ARTIFACTS_PATH=models/docling`. Ollama models must also be available locally. GPU use depends on the installed runtime and available memory; using heading-only classification removes the classifier model's memory requirement.

### API and limits

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `Tax Document API` | API title |
| `DATABASE_URL` | `sqlite:///<project-root>/tax_document_database.db` | API-key metadata database |
| `ALLOW_KEY_BOOTSTRAP` | `false` | Allow unauthenticated creation of the first key |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | Log level and JSON output |
| `MAX_UPLOAD_MB` / `MAX_PAGES` | `20` / `10` | Per-file upload limit and single-route page limit |
| `BULK_MAX_FILES` / `BULK_MAX_PAGES` | `20` / `100` | Files per bulk request and pages per file |
| `BULK_MAX_TOTAL_MB` | `100` | Bulk request body limit, counted as bytes arrive (other routes: `MAX_UPLOAD_MB`) |
| `MAX_CONCURRENT_JOBS` / `MAX_QUEUED_JOBS` | `2` / `8` | Extract and bulk requests running at once / waiting; beyond that `503 busy` |
| `PIPELINE_RATE_CALLS` / `PIPELINE_RATE_SECONDS` | `30` / `60` | Single-extraction requests per key per window |
| `BULK_RATE_CALLS` / `BULK_RATE_SECONDS` | `10` / `60` | Bulk requests per key per window |
| `CLASSIFY_RATE_CALLS` / `CLASSIFY_RATE_SECONDS` | `120` / `60` | Classification requests per key per window |
| `LIBRARY_RATE_CALLS` / `SEARCH_RATE_CALLS` / `ASSISTANT_RATE_CALLS` | `30` / `120` / `30` | Library uploads, searches and assistant requests per key per window (`*_RATE_SECONDS`, default `60`) |
| `ASSISTANT_CONFIG` | `config/model_config.yml` | Assistant, embedding and library settings |

Library uploads and the assistant's `extract_document` tool share the extraction job slots (`MAX_CONCURRENT_JOBS`).

## Architecture

```text
Upload -> Docling parse/OCR -> split into forms -> classify
       -> read tables + pair labels/values -> LLM field mapping
       -> read checkboxes -> normalize and validate -> verify against labels/positions -> JSON
```

| Location | Responsibility |
|---|---|
| [main.py](main.py) | Server, API-key, and model-download CLI commands |
| [api/](api/) | HTTP routes, authentication, rate limits, body size limit, error responses, dashboard serving ([api/web.py](api/web.py)) |
| [core/pipeline.py](core/pipeline.py) | Single-form and bulk orchestration |
| [core/parser.py](core/parser.py) | Docling lifecycle, conversion, page images |
| [core/segment.py](core/segment.py), [core/tev.py](core/tev.py) | Form boundaries and classification |
| [core/layout.py](core/layout.py), [core/tables.py](core/tables.py), [core/checkboxes.py](core/checkboxes.py) | Label/value pairing, table rows, checkbox detection |
| [core/extractor.py](core/extractor.py) | Evidence chunks within the token budget, LLM mapping, answer merging |
| [core/grounding.py](core/grounding.py) | Checks amounts and TINs against their printed labels; records where every value was read |
| [core/flags.py](core/flags.py) | `Flag` and the flag codes every producer uses |
| [core/jobs.py](core/jobs.py) | Extraction job gate (`MAX_CONCURRENT_JOBS` / `MAX_QUEUED_JOBS`) |
| [core/forms.py](core/forms.py), [core/schema.py](core/schema.py), [core/utils.py](core/utils.py) | Form definitions, response models, normalization |
| [core/auth.py](core/auth.py), [core/db.py](core/db.py), [core/models.py](core/models.py) | API-key storage and lifecycle |
| [library/](library/) | Stored documents: SQLite store (documents, forms, reviews, chunks), ingestion, hybrid search, original files and page rendering, review rules |
| [agent/](agent/) | The assistant: config, provider wire formats, tool loop, tools |
| [scripts/](scripts/) | OpenAPI export for the dashboard, license listing |
| [tests/](tests/) | Unit/API tests, model integration tests, evaluator, synthetic fixtures, fake model server and E2E stack ([tests/e2e/](tests/e2e/)) |
| [n8n/](n8n/) | Optional intake and review workflow |

Dependency direction: `api -> agent -> library -> core`. Request traces and change recipes: [docs/DEVELOPING.md](docs/DEVELOPING.md).

To add a form, define its Pydantic model (with `NAME`, `TITLE`, `HINT`) and printed labels in `core/forms.py`, register it in `FORMS`, and add digital/scanned fixtures with ground truth and tests. The registry supplies classifier choices, API schemas and review checks.

## Development and verification

```sh
uv run pytest -m "not integration"
uv run pytest -m integration
uv run python -m tests.evaluate
node n8n/tests/code-nodes.test.mjs n8n/tax-document-intake.workflow.json n8n/tests/api-fixtures.json
uv run python -m scripts.licenses
uv run python -m scripts.openapi     # after an API change: web/src/api/openapi.json
```

Unit/API tests use synthetic data and a fake pipeline for HTTP behavior. Integration tests exercise Docling and Ollama, but skip automatically when Ollama or configured models are unavailable. Check the skipped count before treating a run as end-to-end verification.

To run the whole API without GPUs or Ollama (real Docling, fake extraction/embedding/chat models, fresh data directory, printed keys): `uv run python -m tests.e2e.stack --api-port 8000 --models-port 11435`. See [docs/DEVELOPING.md](docs/DEVELOPING.md#run-the-full-stack-locally-without-gpus).

The evaluator writes `tests/reports/latest.json`. Fixtures cover digital and scanned forms, mixed stacks, and consolidated statements; 1099-B coverage is in the consolidated fixtures. A field is scored when either side has a value: zero is a value (`0.00` against `null` is an error), while `null`, `""`, `[]` and an unchecked box count as missing.

Regression tests without models: the library and assistant with fake providers ([tests/test_assistant.py](tests/test_assistant.py): chunking, SSN masking, filters, key isolation, ranking, OpenAI and Anthropic request formats, the tool loop, every tool with owner and scope limits), the review contract ([tests/test_library.py](tests/test_library.py): checks, decisions, approval and reopening, chunk rebuilds, stats and filters, page rendering, file storage, routes), the dashboard contract ([tests/test_web.py](tests/test_web.py): OpenAPI file current, SPA fallback), verification (swapped, unprinted and zero values), duplicate identity and bulk duplicates, partial conversions and missing pages ([tests/test_pipeline.py](tests/test_pipeline.py)), evidence chunk budgets, the counted body limit and the job gate.

Synthetic test results do not establish accuracy on client documents. Put representative redacted documents in `tests/samples/redacted/`: each file `<name>.pdf` (or an image) with `<name>.forms.json`, a list of `{"document_type", "pages", "fields"}` in page order, plus `"status": "duplicate"` for repeated copies; `"fields": null` marks a page that is not a supported form. The evaluator and `pytest -m integration` pick them up and hold them to the scan accuracy bar. Cover the layouts your clients send (payroll providers, brokerages, phone photos) and more than one tax year.

## Deployment and data handling

- The CLI runs one worker. Each additional process loads its own models; rate limits are also per process. Shared deployments need a shared limit or gateway policy.
- Terminate TLS at a reverse proxy. Request bodies are counted as they arrive, with or without `Content-Length`; a proxy limit is still worth setting so oversized uploads stop before reaching the app.
- At most `MAX_CONCURRENT_JOBS` extract/bulk requests run at once; each holds its uploaded files in memory (up to `BULK_MAX_TOTAL_MB`). Up to `MAX_QUEUED_JOBS` more wait with their uploads spooled to temporary disk. Classification is not gated (digital PDFs need no models; scans share Docling's `DOCLING_MAX_CONCURRENCY`).
- `/health` returns HTTP 200 even when `data.status` is `degraded`. Read the body when checking readiness.
- The application database stores API-key hashes and metadata, not extracted documents. Extraction routes (`/v1/documents/...`) process uploads for the response and store nothing; multipart handling may spool large uploads to temporary disk. Document evidence is sent to the configured Ollama server.
- The library stores what is added to it: the original files (`data/files/` by default) and, in `data/library.db`, extracted forms with their fields as read (full TINs included), review decisions with the reviewer's key name and time, and search chunks and embeddings with SSNs masked (names, addresses, amounts and EINs as printed). Both paths are gitignored. Treat them, and their backups, as taxpayer data; deleting a document removes its file once no other key's document uses it. A cloud chat provider, if configured, receives the questions and the tool results (search excerpts, document summaries with SSNs masked).
- Application logs primarily contain request and processing metadata. Error messages and third-party diagnostics need review before making a strict no-sensitive-data logging guarantee.
- Splitting is heuristic. Pages without text are reported in `missing_pages`, not dropped silently. Duplicates must match on every extracted value, so extraction noise can leave a real copy marked `ok` (a reviewer sees both); it no longer skips distinct forms.
- Evidence is packed into chunks by an estimated token count (every digit counts as one token, other characters one per three) with a 10% margin, so prompt, evidence and answer fit `EXTRACTOR_NUM_CTX`. It is an estimate, not the model's tokenizer.

## Optional n8n integration

The [n8n setup guide](n8n/README.md) covers the importable intake workflow, credentials, review routing, log table, and notifications. It accepts uploads, email attachments, Drive files, or webhook submissions and sends files to the bulk API.

The workflow stores extracted fields with TIN masking in `tax_document_log`; failed execution records may retain document data. Configure retention and access in n8n separately from the API.

Export into tax or practice-management software is not implemented. Consumers can map each form's `fields` object using the schemas from `GET /v1/document-types`.
