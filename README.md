# Tax Document API

FastAPI service for classifying and extracting data from 15 US tax form types. It accepts digital PDFs and scanned documents, separates supported forms, and returns typed JSON with validation warnings.

The pipeline uses Docling for layout and OCR, Ollama for field extraction, and either printed headings or the tev1 model for classification. An optional n8n workflow handles intake and review. A document library with an AI assistant answers questions about stored documents, using local models by default.

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
| `GET /v1/document-types` | Client or admin key | List supported forms and their field schemas |
| `POST /v1/library/documents` | Client or admin key | Add a document to the library |
| `GET /v1/library/documents` | Client or admin key | List library documents |
| `GET` / `DELETE /v1/library/documents/{id}` | Client or admin key | Read or delete one library document |
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
- `validation` reports warnings, field counts and `sources`. A valid schema is not proof of extraction accuracy or completeness.
- Every amount and TIN is checked against where it is printed ([core/grounding.py](core/grounding.py), run by `LLMExtractor.extract`). A value must sit in the box of its own label; it is removed with a warning when it is not printed at all (zero included: a blank box is not `0.00`) or only under another label (swapped fields). A value printed in the document but not in a labeled box, or whose label isn't found, is kept with a "check it" warning. `validation.sources` maps each checked field path (and each table row, e.g. `transactions[3]`) to `{label, page, box}`, with `box` as left, top, right, bottom on a page 1000 units wide.
- `document.conversion` is `partial` when Docling reported errors (listed in `conversion_errors`); `document.missing_pages` lists pages no text was read from (failed, blank or unreadable). A form gets a warning when the conversion was partial or a page inside its page range is missing. A file with no readable text at all returns `422 unreadable_document`.
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
| `ok` | Identified and extracted; inspect validation warnings |
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
| 409 | `index_mismatch` (library built with a different embedding model) |
| 422 | `unreadable_document`, `unsupported_document_type`, `multiple_forms`, `invalid_request` |
| 429 | `rate_limited` (includes `Retry-After`) |
| 503 | `model_unavailable` (includes `Retry-After: 10`); `busy` when all extraction slots and the queue are taken (includes `Retry-After: 30`) |
| 500 | `internal_error` |

## Document library and assistant

The library stores documents for search; the assistant answers questions from them. Both call model providers over plain HTTP (no SDKs), configured in [config/model_config.yml](config/model_config.yml): chat through the OpenAI-compatible API (Ollama, OpenAI, OpenRouter) or Anthropic's Messages API, embeddings through the OpenAI-compatible API. The shipped configuration is local: `qwen3.5:4b` and `nomic-embed-text` on Ollama. API keys for cloud providers are read from the environment variable named by `api_key_env`, never from the file.

### Add and search documents

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -F "file=@tests/samples/w2.pdf" -F "client=Gonzalez" -F "tax_year=2025" http://localhost:8000/v1/library/documents
curl -H "X-API-Key: YOUR_CLIENT_KEY" -H "Content-Type: application/json" -d '{"query": "wages and federal withholding", "filters": {"client": "Gonzalez", "tax_year": 2025}}' http://localhost:8000/v1/library/search
```

- Accepted: PDF and images (as above), `.txt`, `.md`, `.csv`, `.xlsx`. PDFs and images go through form extraction: each supported form is stored as its field values with its form ID and tax year; other pages are stored as page text. `extract=false` stores page text only (faster, no LLM). Up to `BULK_MAX_PAGES` pages.
- Text files are split by Markdown heading, CSV and sheet rows (header repeated in each chunk), or paragraphs, into chunks of at most `library.chunk_chars`.
- `client`, `tax_year`, `title` and `location` (where the original is kept) are optional metadata. Uploading the same file again returns the stored document (200 instead of 201) without re-processing.
- Social Security numbers are masked to the last four digits before anything is stored or embedded. The original file is not kept.
- Each key sees only the documents it added; admin keys see all of them.

Search filters (`client`, `tax_year`, `document_type`, `document_ids`) are applied in the database first, so only matching chunks are scored. The remaining chunks are ranked twice, by embedding similarity (exact cosine, scored in batches) and by keywords (SQLite FTS5, which finds exact account numbers and names), and the two rankings are combined with reciprocal rank fusion. `similarity` in each hit is the cosine similarity, `null` for keyword-only matches. Exact scoring stays fast to several hundred thousand chunks per filter; beyond that, an approximate vector index can replace `Library._semantic` without changing the API.

### Ask the assistant

```sh
curl -H "X-API-Key: YOUR_CLIENT_KEY" -H "Content-Type: application/json" -d '{"messages": [{"role": "user", "content": "What were Maria Gonzalez'"'"'s 2025 wages?"}], "scope": {"client": "Gonzalez"}}' http://localhost:8000/v1/assistant/chat
```

The route is stateless: send the conversation so far, ending with the user's message. The model searches with the `search_documents` tool (up to `assistant.max_tool_rounds` rounds, then it must answer) and cites excerpts as `[n]`; `data.sources` lists every retrieved excerpt under those numbers, with its document ID, page and similarity. `scope` limits every search; the model can narrow it but not widen it. `data.usage` reports the provider's token counts.

Configuration notes:

- Changing the embedding model or `dimensions` makes existing vectors incomparable; searches then return `409 index_mismatch`. Delete the library database (or use a new `library.database` path) and add the documents again.
- `nomic-embed-text` expects the `search_query: ` / `search_document: ` prefixes set in the file; set both to `""` for other embedding models.
- Ollama's OpenAI-compatible API cannot set the context size per request. If long answers or many excerpts get cut off, start the Ollama server with a larger `OLLAMA_CONTEXT_LENGTH` (e.g. `8192`).
- `chat.options` adds fields to each request body as is (for example `reasoning_effort`); `max_completion_tokens` there replaces `max_tokens` for models that require it.
- A provider that cannot be reached, or rejects the request, returns `503 model_unavailable` with its error in the message.

Code: [agent/config.py](agent/config.py) (settings), [agent/ingest.py](agent/ingest.py) (files to chunks), [agent/rag.py](agent/rag.py) (embeddings, storage, search), [agent/tools.py](agent/tools.py) (the search tool), [agent/agent.py](agent/agent.py) (provider wire formats and the tool loop), [api/routes/library.py](api/routes/library.py) and [api/routes/assistant.py](api/routes/assistant.py). To add a tool, define its JSON schema and function in `agent/tools.py` and dispatch it in `Assistant._run`.

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
| `STRICT_GROUNDING` | `true` | Check extracted amounts and TINs against their printed labels and positions |
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

Library uploads share the extraction job slots (`MAX_CONCURRENT_JOBS`).

## Architecture

```text
Upload -> Docling parse/OCR -> split into forms -> classify
       -> read tables + pair labels/values -> LLM field mapping
       -> read checkboxes -> normalize and validate -> verify against labels/positions -> JSON
```

| Location | Responsibility |
|---|---|
| [main.py](main.py) | Server, API-key, and model-download CLI commands |
| [api/](api/) | HTTP routes, authentication, rate limits, extraction job gate, body size limit, error responses |
| [core/pipeline.py](core/pipeline.py) | Single-form and bulk orchestration |
| [core/parser.py](core/parser.py) | Docling lifecycle, conversion, page images |
| [core/segment.py](core/segment.py), [core/tev.py](core/tev.py) | Form boundaries and classification |
| [core/layout.py](core/layout.py), [core/tables.py](core/tables.py), [core/checkboxes.py](core/checkboxes.py) | Label/value pairing, table rows, checkbox detection |
| [core/extractor.py](core/extractor.py) | Evidence chunks within the token budget, LLM mapping, answer merging |
| [core/grounding.py](core/grounding.py) | Checks amounts and TINs against their printed labels; records sources |
| [core/forms.py](core/forms.py), [core/schema.py](core/schema.py), [core/utils.py](core/utils.py) | Form definitions, response models, normalization |
| [core/auth.py](core/auth.py), [core/db.py](core/db.py), [core/models.py](core/models.py) | API-key storage and lifecycle |
| [agent/](agent/) | Document library (chunking, embeddings, filtered hybrid search) and the assistant's provider clients and tool loop |
| [tests/](tests/) | Unit/API tests, model integration tests, evaluator, synthetic fixtures |
| [n8n/](n8n/) | Optional intake and review workflow |

To add a form, define its Pydantic model and printed labels in `core/forms.py`, register it in `FORMS`, and add digital/scanned fixtures with ground truth and tests. The registry supplies classifier choices and API schemas.

## Development and verification

```sh
uv run pytest -m "not integration"
uv run pytest -m integration
uv run python -m tests.evaluate
node n8n/tests/code-nodes.test.mjs n8n/tax-document-intake.workflow.json n8n/tests/api-fixtures.json
uv run python -m scripts.licenses
```

Unit/API tests use synthetic data and a fake pipeline for HTTP behavior. Integration tests exercise Docling and Ollama, but skip automatically when Ollama or configured models are unavailable. Check the skipped count before treating a run as end-to-end verification.

The evaluator writes `tests/reports/latest.json`. Fixtures cover digital and scanned forms, mixed stacks, and consolidated statements; 1099-B coverage is in the consolidated fixtures. A field is scored when either side has a value: zero is a value (`0.00` against `null` is an error), while `null`, `""`, `[]` and an unchecked box count as missing.

Regression tests without models: the library and assistant with fake providers ([tests/test_assistant.py](tests/test_assistant.py): chunking, SSN masking, filters, key isolation, ranking, OpenAI and Anthropic request formats, the tool loop), verification (swapped, unprinted and zero values), duplicate identity and bulk duplicates, partial conversions and missing pages ([tests/test_pipeline.py](tests/test_pipeline.py)), evidence chunk budgets, the counted body limit and the job gate.

Synthetic test results do not establish accuracy on client documents. Put representative redacted documents in `tests/samples/redacted/`: each file `<name>.pdf` (or an image) with `<name>.forms.json`, a list of `{"document_type", "pages", "fields"}` in page order, plus `"status": "duplicate"` for repeated copies; `"fields": null` marks a page that is not a supported form. The evaluator and `pytest -m integration` pick them up and hold them to the scan accuracy bar. Cover the layouts your clients send (payroll providers, brokerages, phone photos) and more than one tax year.

## Deployment and data handling

- The CLI runs one worker. Each additional process loads its own models; rate limits are also per process. Shared deployments need a shared limit or gateway policy.
- Terminate TLS at a reverse proxy. Request bodies are counted as they arrive, with or without `Content-Length`; a proxy limit is still worth setting so oversized uploads stop before reaching the app.
- At most `MAX_CONCURRENT_JOBS` extract/bulk requests run at once; each holds its uploaded files in memory (up to `BULK_MAX_TOTAL_MB`). Up to `MAX_QUEUED_JOBS` more wait with their uploads spooled to temporary disk. Classification is not gated (digital PDFs need no models; scans share Docling's `DOCLING_MAX_CONCURRENCY`).
- `/health` returns HTTP 200 even when `data.status` is `degraded`. Read the body when checking readiness.
- The application database stores API-key hashes and metadata, not extracted documents. Extraction uploads are processed for the response; multipart handling may spool large uploads to temporary disk. Document evidence is sent to the configured Ollama server.
- The library database (`data/library.db` by default, gitignored) stores what is added to the library: chunk text, extracted fields and embeddings, with SSNs masked; names, addresses, amounts and EINs as printed. Treat it, and its backups, as taxpayer data. A cloud chat provider, if configured, receives the questions and the retrieved excerpts.
- Application logs primarily contain request and processing metadata. Error messages and third-party diagnostics need review before making a strict no-sensitive-data logging guarantee.
- Splitting is heuristic. Pages without text are reported in `missing_pages`, not dropped silently. Duplicates must match on every extracted value, so extraction noise can leave a real copy marked `ok` (a reviewer sees both); it no longer skips distinct forms.
- Evidence is packed into chunks by an estimated token count (every digit counts as one token, other characters one per three) with a 10% margin, so prompt, evidence and answer fit `EXTRACTOR_NUM_CTX`. It is an estimate, not the model's tokenizer.

## Optional n8n integration

The [n8n setup guide](n8n/README.md) covers the importable intake workflow, credentials, review routing, log table, and notifications. It accepts uploads, email attachments, Drive files, or webhook submissions and sends files to the bulk API.

The workflow stores extracted fields with TIN masking in `tax_document_log`; failed execution records may retain document data. Configure retention and access in n8n separately from the API.

Export into tax or practice-management software is not implemented. Consumers can map each form's `fields` object using the schemas from `GET /v1/document-types`.
