# Tax Document API

FastAPI service that takes US tax documents (PDF or image, digital or scanned), finds every form in each file, identifies it, extracts every filled field, and returns validated, typed JSON.

> Output is machine-extracted decision support, not tax or legal advice. Every value must be checked against the source document by a qualified person before use. See [DISCLAIMER.md](DISCLAIMER.md). Third-party licenses: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Supported documents

| id | Form | What it carries |
|---|---|---|
| `w2` | W-2 Wage and Tax Statement | wages, federal/SS/Medicare withholding, box 12 codes, box 13 checkboxes, state/local rows |
| `1099_nec` | 1099-NEC | contractor compensation |
| `1099_misc` | 1099-MISC | rents, royalties, other income, attorney proceeds |
| `1099_int` | 1099-INT | interest, early-withdrawal penalty, tax-exempt interest, bond premium |
| `1099_div` | 1099-DIV | ordinary/qualified dividends, capital gain distributions, §199A |
| `1099_b` | 1099-B | every sale (description, CUSIP, dates, proceeds, basis, wash sale, gain/loss), with term, covered status and Form 8949 box |
| `1099_r` | 1099-R | retirement/IRA distributions, taxable amount, distribution codes |
| `1099_g` | 1099-G | unemployment compensation, state tax refunds |
| `1099_k` | 1099-K | gross payment card / third-party network amounts, monthly amounts 5a-5l, filer type |
| `ssa_1099` | SSA-1099 | Social Security benefits |
| `1098` | 1098 | mortgage interest, points, PMI, outstanding principal |
| `1098_t` | 1098-T | tuition payments, scholarships, student status |
| `1098_e` | 1098-E | student loan interest |
| `1095_a` | 1095-A | policy, covered individuals, monthly premiums / SLCSP / advance credit, annual totals |
| `k1_1065` | Schedule K-1 (Form 1065) | partnership share of income, coded items, capital account |

**Files with several forms** go to the bulk route: a client's stack of scans, multi-copy W-2 pages, and consolidated brokerage statements (1099-DIV + 1099-INT + 1099-B ... in one PDF, with hundreds of sales). Each form is identified and extracted on its own; pages that are not a supported form are reported, never dropped silently.

To add a form, add one Pydantic class in [core/forms.py](core/forms.py) and register it in `FORMS`. Its `TITLE` is its printed heading (used to split files and as classifier option), its box labels drive the extraction prompt and response schema, `table(...)` fields are read row by row, and the sample generator renders it.

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), and [Ollama](https://ollama.com) 0.35 or later.

```bash
uv sync
ollama pull tev1          # classifier (4B decision model) -- or set TEV_MODEL=none, see below
ollama pull qwen3.5:4b    # field-mapping LLM (any Ollama model with JSON output works)
uv run main.py create-key --name admin --admin
uv run main.py serve --host 0.0.0.0 --port 8000
```

On first start Docling downloads its layout and OCR weights (about 200 MB) into the Hugging Face cache. For offline or air-gapped servers:

```bash
uv run main.py download-models --to models/docling
# then in .env: DOCLING_ARTIFACTS_PATH=models/docling
```

Interactive docs: `http://localhost:8000/docs`. In the docs page, clear any optional field you don't use: Swagger fills them with the placeholder `string`.

**Without tev1.** The tev1 weights have no final license yet ([THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md#tev1)). `TEV_MODEL=none` identifies forms from their printed headings instead: no model, no GPU memory, and on the bundled samples it identifies every form (see [Verification](#verification)). Documents whose heading is missing or unreadable come back `unknown` in that mode, where tev1 could still recognise them from their content.

### GPU

`torch` installs as a CPU build by default, and Docling runs fine on CPU: about 1-4 s per page once warm. To run Docling's layout model on an NVIDIA GPU, install a CUDA build of torch and set `DOCLING_DEVICE=cuda`. Ollama uses the GPU on its own.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///tax_document_database.db` | API-key store (only key hashes are stored; documents are never persisted) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint (deliberately not `OLLAMA_HOST`, which is the server's bind address) |
| `TEV_MODEL` | `tev1` | Classification model (`tev1:0.8b` for small machines; `none`: printed headings only) |
| `TEV_MIN_CONFIDENCE` | `0.5` | Below this, tev's answer is `unknown` (a printed heading can still identify the form) |
| `EXTRACTOR_MODEL` | `qwen3.5:4b` | LLM that maps label/value evidence onto the form schema |
| `EXTRACTOR_NUM_CTX` | `8192` | LLM context; evidence above `1.5 x` this many characters is sent page by page |
| `STRICT_GROUNDING` | `true` | Drop extracted amounts/TINs whose digits do not appear in the document |
| `PIPELINE_RATE_CALLS` / `PIPELINE_RATE_SECONDS` | `30` / `60` | `/extract` limit, per API key |
| `BULK_RATE_CALLS` / `BULK_RATE_SECONDS` | `10` / `60` | `/extract/bulk` limit, per API key |
| `CLASSIFY_RATE_CALLS` / `CLASSIFY_RATE_SECONDS` | `120` / `60` | `/classify` limit, per API key |
| `MAX_UPLOAD_MB` / `MAX_PAGES` | `20` / `10` | Per file; page limit of the single-form routes |
| `BULK_MAX_FILES` / `BULK_MAX_PAGES` / `BULK_MAX_TOTAL_MB` | `20` / `100` / `100` | Bulk route: files per request, pages per file, request size |
| `DOCLING_DEVICE` | `auto` | `cpu`, `cuda`, `mps` |
| `DOCLING_THREADS` | `4` | CPU threads for Docling models |
| `DOCLING_MAX_CONCURRENCY` | `1` | Conversions in flight per process |
| `DOCLING_TIMEOUT_S` | `120` | Per-document conversion timeout |
| `DOCLING_TABLES` | `false` | TableFormer (not needed: tables are read from raw text cells) |
| `ALLOW_KEY_BOOTSTRAP` | `false` | Let the first `POST /api-keys` run without auth |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | Structured JSON logs to stdout |

## API

Every document route requires `X-API-Key`. Responses are wrapped in `{"data": ...}`. Errors are `{"error": {"code", "message", "request_id", "details"}}` with a matching HTTP status. Each response carries an `X-Request-ID` header (send your own to correlate).

| Route | Auth | |
|---|---|---|
| `POST /v1/documents/extract` | key | One file holding one form → its fields |
| `POST /v1/documents/extract/bulk` | key | Files holding any number of forms → every form's fields |
| `POST /v1/documents/classify` | key | File or text → form type (tev1 only) |
| `GET /v1/document-types` | key | Supported forms and the JSON schema of each `fields` object |
| `GET /health` | none | `ok` / `degraded`: Docling loaded, Ollama reachable, models pulled |
| `GET /auth/me` | key | Check a key |
| `POST /api-keys`, `GET /api-keys`, `DELETE /api-keys/{id}` | admin | Issue, list, revoke client keys |

Accepted files: PDF, PNG, JPEG, TIFF, BMP, WEBP, detected from the file's bytes (not its name or `Content-Type`).

### `POST /v1/documents/extract`

```bash
curl -H "X-API-Key: $KEY" -F "file=@w2.pdf" http://localhost:8000/v1/documents/extract
```

```jsonc
{
  "data": {
    "request_id": "6f0c…",
    "document_type": "w2",
    "form_name": "Form W-2 Wage and Tax Statement",
    "classification": {"document_type": "w2", "confidence": 0.9986, "probabilities": {"w2": 0.9986, "none": 0.0012}, "model": "tev1"},
    "fields": {
      "tax_year": 2025,
      "employee": {"name": "Maria L Gonzalez", "tin": "412-56-7890", "address": {"street": "1847 Willow Creek Dr", "city": "Austin", "state": "TX", "postal_code": "78745"}},
      "wages_tips_other_comp": 68412.55,
      "box12": [{"code": "D", "amount": 4087.45}, {"code": "DD", "amount": 9816.0}],
      "retirement_plan": true,
      "state_local": [{"state": "TX", "employer_state_id": "17-8839201", "state_wages": 68412.55, "state_income_tax": 0.0}]
    },
    "validation": {"valid": true, "warnings": [], "fields_found": 24, "fields_total": 33},
    "document": {"filename": "w2.pdf", "media_type": "application/pdf", "pages": 1, "text_layer": true, "sha256": "…"},
    "extraction_model": "qwen3.5:4b",
    "timings_ms": {"parse": 1900, "classify": 380, "extract": 5200, "total": 7500}
  }
}
```

* `fields` follows the per-form schema (`GET /v1/document-types`). Amounts are numbers, dates are ISO-8601, checkboxes are booleans, TINs are normalised (`123-45-6789`, `12-3456789`, masked `XXX-XX-1234`). Blank boxes are `null`.
* `validation.warnings` lists every value that was dropped (unparseable, or not found in the document text) and every failed arithmetic check (W-2 box 4 ≠ 6.2% of box 3, 1099-B gain ≠ proceeds − basis + wash sale, 1099-K months ≠ box 1a, 1095-A months ≠ line 33). The rest of the document is still returned.
* `?document_type=w2` skips splitting and classification: the whole file is treated as that form.
* A file with several forms is refused with `422 multiple_forms`; `details.forms` lists each form's pages and printed heading. Send it to the bulk route.
* A file that isn't a supported form returns `422 unsupported_document_type`, with tev's probabilities in `details`.

### `POST /v1/documents/extract/bulk`

One or more files in the `files` field. Each file may hold any number of forms.

```bash
curl -H "X-API-Key: $KEY" -F "files=@stack_scan.pdf" -F "files=@consolidated_1099.pdf" \
     http://localhost:8000/v1/documents/extract/bulk
```

```jsonc
{
  "data": {
    "request_id": "…",
    "files": [
      {
        "filename": "consolidated_1099.pdf", "status": "ok", "error": null,
        "document": {"filename": "consolidated_1099.pdf", "media_type": "application/pdf", "pages": 4, "text_layer": true, "sha256": "…"},
        "forms": [
          {"index": 0, "status": "ok", "pages": [1], "document_type": "1099_div", "form_name": "Form 1099-DIV …",
           "classification": {…}, "fields": {…}, "validation": {…}, "text_layer": true, "duplicate_of": null,
           "extraction_model": "qwen3.5:4b", "timings_ms": {"classify": …, "extract": …, "total": …}},
          {"index": 1, "status": "ok", "pages": [1], "document_type": "1099_int", …},
          {"index": 2, "status": "ok", "pages": [2, 3, 4], "document_type": "1099_b",
           "fields": {"payer": {…}, "transactions": [
             {"description": "40 SH VANGUARD TOTAL STK MKT ETF", "cusip": "162275869", "date_acquired": "2025-06-19",
              "date_sold": "2025-09-04", "proceeds": 9959.91, "cost_basis": 7212.4, "gain_or_loss": 2747.51,
              "term": "short", "basis_reported_to_irs": true, "form_8949_box": "A"}, …]}, …}
        ]
      },
      {"filename": "notes.docx", "status": "failed", "error": {"code": "unsupported_media_type", "message": "…"}, "document": null, "forms": []}
    ],
    "summary": {"files": 2, "failed_files": 1, "forms": 3, "unsupported": 0, "duplicates": 0, "by_type": {"1099_div": 1, "1099_int": 1, "1099_b": 1}},
    "timings_ms": {"total": …}
  }
}
```

Per form, `status` is:

| status | meaning | `fields` |
|---|---|---|
| `ok` | identified and extracted | the form's fields |
| `unsupported` | these pages are not a supported form (invoice, letter, Form 1040, instructions page) | `null` |
| `duplicate` | another copy of form `duplicate_of` in the same file (same TINs and amounts: W-2 Copy B/C/2) | the copy's fields |

A file that can't be read (wrong type, too many pages, damaged) has `status: failed` and an `error` with the same codes as the single route; the other files are still processed. A model backend outage fails the whole request with `503`. The request is synchronous: a 30-page statement takes minutes, so set client timeouts accordingly (the n8n workflow allows 5 minutes per file).

### `POST /v1/documents/classify`

```bash
curl -H "X-API-Key: $KEY" -F "file=@scan.png" http://localhost:8000/v1/documents/classify
curl -H "X-API-Key: $KEY" -F "text=Form 1099-INT Interest Income 2025 ..." http://localhost:8000/v1/documents/classify
```

Send `file` or `text`, not both. Digital PDFs are classified from their embedded text layer with no Docling models involved; scans and images are OCR'd first. The file is classified as a whole: use the bulk route to find every form in a multi-form file.

### Status codes

| Status | `error.code` | When |
|---|---|---|
| 400 | `bad_request` | empty file; both or neither of `file` / `text` |
| 401 | `unauthorized` | missing or invalid key |
| 403 | `forbidden` | admin route with a client key |
| 413 | `payload_too_large`, `too_many_pages` | file, file count, request size or page count over the limit |
| 415 | `unsupported_media_type` | not a PDF or supported image |
| 422 | `unreadable_document`, `unsupported_document_type`, `multiple_forms`, `invalid_request` | damaged/encrypted file; not a supported form; several forms on the single route; bad parameters |
| 429 | `rate_limited` | over the per-key limit (`Retry-After` header) |
| 503 | `model_unavailable` | Ollama down or a model not pulled (`Retry-After: 10`) |

## Where the data goes

The API is stateless. For each request it returns the extracted fields in the HTTP response and keeps nothing: documents and values are not written to disk or logs. The only stored data is API-key metadata (hashed keys). Logs record request ids, routes, timings, form types and counts.

The caller decides where the data goes. In the delivered setup that is the n8n workflow ([n8n/README.md](n8n/README.md)), which for every form:

1. writes one row to the n8n data table `tax_document_log`: client, file, pages, form, status (`ready` / `needs_review` / `failed`), the assistant's notes, and `extracted_fields` (the full `fields` JSON, TINs masked to the last 4 digits);
2. emails the reviewer when a person should look;
3. alerts the team's Slack / Telegram / WhatsApp when a file could not be processed or the workflow failed.

Staff read the log table in n8n and act on the emails. The table lives in n8n's own database (SQLite by default, Postgres if n8n is configured for it). n8n is set not to keep the data of successful runs; failed runs are kept for diagnosis and contain document data until deleted.

**Getting the data into tax software is not built yet.** The integration contract is the `fields` object of each form, whose schema is published at `GET /v1/document-types` and is stable per form. Delivering into a client's tax or practice-management software means adding one step after **Build Log Record** (or a consumer of the API) that maps those fields to what that software imports.

## How it works

1. **Parse** ([core/parser.py](core/parser.py)): one Docling `DocumentConverter` per process is built and warmed at startup, so layout and OCR weights stay in memory. OCR (RapidOCR on onnxruntime, direction classifier off) runs only on regions without a PDF text layer. File type comes from magic bytes. The result is line-level text blocks with page coordinates (0-1000 across the page width), each marked as OCR or text layer, plus a 150-DPI grayscale image of any page that is needed later.
2. **Split** ([core/segment.py](core/segment.py)): printed form headings and footers ("Form 1099-INT", "Schedule K-1 (Form 1065)") mark where each form starts. Pages with several form types (consolidated statements) are cut into bands, and the account header above the first section is shared with every form. Repeated headings of one type on a page are copies and stay one form. Pages without a heading continue a multi-page form (1099-B, K-1); after a single-page form they become a form of their own for the classifier to judge. OCR damage to headings (`Form1099-DIV`, `1099-MISCMiscellaneous`, a stray letter) is repaired before matching.
3. **Classify** ([core/tev.py](core/tev.py)): tev1 receives a digest of the form's own text (title lines first, under 3k characters) and one `choice` question covering every supported form plus `none`. If tev is unsure but the form printed a supported heading, the heading decides and a warning says so. `TEV_MODEL=none` uses the heading only.
4. **Tables** ([core/tables.py](core/tables.py)): `table(...)` fields (1099-B sales, 1095-A Parts II and III) are read row by row. A header row is found by its column labels (first line of wrapped headers is enough). Columns run from one header's left edge to the next; amounts are placed by their right edge, text by its centre. Rows must have at least two values that parse as their column types, wrapped descriptions are joined, "Total" lines are skipped, and section headings ("Short-term … Box A") set term, covered status and Form 8949 box for the rows below. Rows read here are removed from the LLM's evidence.
5. **Pair labels with values** ([core/layout.py](core/layout.py)): Docling's reading order splits a box form's values from their labels, so the raw line cells and their boxes are used instead. The form's printed labels are recognised, wrapped label lines joined, PDF lines split where they cross box borders, and each value attached to the nearest label above-left of it: `1 Wages, tips, other compensation => 68,412.55`.
6. **Map to the schema** ([core/extractor.py](core/extractor.py)): a local LLM (Ollama structured output, temperature 0) turns those lines into the form's JSON: name/address splitting, coded boxes (W-2 box 12, K-1 boxes 11-20), state rows. Evidence over the budget is sent page by page and the answers merged (first value wins, lists concatenate).
7. **Checkboxes** ([core/checkboxes.py](core/checkboxes.py)): read from the page image, not from text (OCR reads a ticked box as `冈` and an empty one as `X`; many PDFs draw the X as lines). Each checkbox label's first line is located, printed squares are found (5-20 pt, ink on all four edges and corners), and each label gets the nearest square to its left, right, below, or at the start of its line, never across a printed cell rule. A square is checked when ink covers part of its interior. This overrides the LLM wherever the box was found.
8. **Validate** ([core/utils.py](core/utils.py), [core/forms.py](core/forms.py)): deterministic parsers normalise amounts (including OCR's `3.672.21` for `3,672.21`), TINs, dates, months and checkboxes. Invalid values are dropped one by one with a warning. Grounding removes amounts and TINs that don't appear in the document text. Per-form arithmetic checks add warnings.

### Tracing a request

Every log line is JSON with the `request_id`; these are the call paths and the log messages they emit.

| Feature | Call path | Log messages |
|---|---|---|
| Single extract | `api/routes/documents.py:extract_document` → `api.deps.read_upload` → `core.pipeline.TaxDocumentPipeline.extract` → `DoclingParser.parse` → `segment.split` (→ `MultipleForms` if > 1) → `_classify_segment` → `_extract_form` → `LLMExtractor.extract` | `request`, `classified`, `extracted`, `document processed` |
| Bulk extract | `extract_bulk` → `TaxDocumentPipeline.extract_bulk` → `_extract_file` per file (file errors in `FILE_ERRORS` become `status: failed`) → `split` → per segment `_classify_segment`, `_extract_form`, `identity` (duplicates) → `BulkSummary` | `classified` / `extracted` per form, `bulk processed` |
| Classify | `classify_document` → `DoclingParser.quick_text` (text layer, or parse) → `TevClassifier.classify` (`build_state` → Ollama `systemone`) | `classified` |
| Extraction steps | `LLMExtractor.extract`: `tables.read_table` → `layout.pair_blocks` / `render_evidence` → `_ask` (Ollama chat, per chunk) → `merge_answers` → `clean_separators` → `validate_leniently` → `checkboxes.checkbox_states` → `ground` → `consistency_checks` | `extracted` (`llm_calls`, `table_rows`, `warnings`, `dropped`) |
| Errors | domain exceptions → `api/errors.py:DOMAIN_ERRORS` (status, code, details); HTTP errors and validation in `api.errors.register` | `model backend unavailable`, `unhandled error` |
| Rate limits / auth | `api.deps.RateLimit` (per route scope, per key hash, windows in `app.state.rate_windows`); `api.deps.require_api_key` | `request` with status 429 / 401 |

To see why a value came out as it did, call the pieces directly on a parsed file:

```python
from core.config import Settings
from core.parser import DoclingParser
from core.segment import split
from core.layout import pair_blocks, render_evidence
from core.forms import FORMS, form_labels

parser = DoclingParser(Settings()); parser.load()
doc = parser.parse_sync(open("stack.pdf", "rb").read(), "stack.pdf", max_pages=100)
for seg in split(doc):
    print(seg.form_hint, seg.pages)                                   # where each form is
form = FORMS["w2"]; seg = split(doc)[0]
print(render_evidence(pair_blocks(seg.all_blocks, form_labels(form))))  # what the LLM sees
```

## Project layout

```
main.py                  CLI: serve | create-key | download-models; `app` built lazily for uvicorn
api/                     HTTP layer only
  app.py                 create_app: lifespan, request middleware, routers
  routes/documents.py    /v1/documents/extract, /extract/bulk, /classify, /v1/document-types
  routes/keys.py         /api-keys
  routes/system.py       /health, /auth/me
  deps.py                auth, pipeline/settings access, RateLimit, read_upload
  errors.py              error envelope; domain exception -> status/code table
core/                    domain logic (no HTTP)
  pipeline.py            parse -> split -> classify -> extract; single and bulk
  parser.py              Docling converter, file sniffing, page images
  segment.py             split a file into forms by printed headings
  tev.py                 tev1 classifier; heading-only classifier
  layout.py              text blocks, lines, label/value pairing, evidence
  tables.py              row-by-row table reader
  checkboxes.py          checkbox states from the page image
  extractor.py           LLM mapping, validation, grounding
  forms.py               form schemas (the single source for labels, schema, prompts, samples)
  schema.py              response models
  utils.py               value parsers, logging
  auth.py, db.py, models.py, config.py
scripts/                 sample generator, sample data, license listing
tests/                   unit, API and integration tests; evaluator; synthetic samples
n8n/                     intake workflow, its guide and Code-node tests
```

## n8n workflow

[n8n/](n8n/) contains a ready-to-import n8n workflow. It collects documents from an upload form, an email inbox, Google Drive, or a webhook, sends each file to the bulk route, has an AI "Accountant Assistant" review every form, logs everything to an n8n data table, emails a reviewer when a person should look, and alerts the team's Slack, Telegram and WhatsApp when the pipeline fails. Setup guide for non-technical staff: [n8n/README.md](n8n/README.md).

## Development

```bash
uv run python -m scripts.generate_samples    # synthetic forms + ground truth in tests/samples/
uv run pytest -m "not integration"           # fast: parsers, layout, checkboxes, tables, splitting, auth, API
uv run pytest -m integration                 # real models over every sample (needs Ollama)
uv run python -m tests.evaluate              # per-field accuracy report -> tests/reports/latest.json
node n8n/tests/code-nodes.test.mjs n8n/tax-document-intake.workflow.json n8n/tests/api-fixtures.json
uv run python -m scripts.licenses            # installed packages and their licenses
```

Every single-form sample is rendered twice: a digital PDF, and a 150-DPI skewed, blurred, noisy PNG "scan" that has to go through OCR. Checkboxes are drawn the way each real form prints them (W-2 box 13 below its labels, 1098-T / 1099-R at the right end of the cell, others to the left) and marked with a typed X, a vector X or a tick. Two multi-form files test splitting: `consolidated_1099.pdf` (1099-DIV and 1099-INT on page 1, 39 sales over pages 2-4 in three Form 8949 categories) and `stack.pdf` (W-2, 1099-NEC, 1098-T and an invoice), each also as an image-only `_scan.pdf`. Their ground truth is `<name>.forms.json`. All names, TINs and addresses are fictitious.

## Verification

Measured on the bundled samples (`tests/samples`, 32 files: 14 forms digital and scanned, plus the two multi-form files digital and scanned) with the real Docling parse, for the stages that don't use the LLM:

| Stage | Result |
|---|---|
| Splitting files into forms (type and pages) | 32 / 32 files |
| Checkbox states | 97 / 97 correct where the printed box was found. One box was not found (1098-T box 7 on the scan) and is left to the LLM. Consolidated statements print no CORRECTED / FATCA boxes, so those come from the LLM too |
| 1099-B and 1095-A table cells | 889 / 890; the miss is OCR running two words together (`10 SHJOHNSON & JOHNSON`) on the scanned statement |

The LLM's field mapping needs Ollama: run `uv run python -m tests.evaluate` and `uv run pytest -m integration` on the server. These are synthetic documents; accuracy on real client documents must be measured on real (redacted) samples.

## Deployment notes

* Run one process per model copy (`workers=1` in `main.py serve`) and scale with more instances behind a load balancer. The rate limiter is in-process, so move it to Redis if you run multiple instances against the same keys.
* Put the API behind TLS (reverse proxy). API keys are bearer secrets.
* **Ollama memory decides latency.** Measured resident sizes: `tev1` about 6.3 GB, `qwen3.5:4b` about 3.7 GB (8k context). Keeping both loaded needs roughly 10-11 GB of VRAM. With less (e.g. an 8 GB card), Ollama swaps them on every extract request, adding about 15-20 s; the Oct 27 evaluation run measured 8.6-9.2 s per classification on such a machine. Options: `TEV_MODEL=tev1:0.8b` (about 1.5 GB), `TEV_MODEL=none` (no classifier model), or route classification-only traffic to `/v1/documents/classify`, which only needs tev1. `systemone` ignores `options.num_ctx`, and a lower-context tev1 tag barely shrinks it because the footprint is mostly weights.
* Bulk requests are processed file by file, form by form, within one HTTP request. Size client timeouts for the largest statements you expect, or send one file per request.
