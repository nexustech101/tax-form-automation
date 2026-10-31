# Developing

How the code is organised, how each feature's request runs through it, and the usual changes as short recipes. API contracts are in the [README](../README.md); this file is about the code.

## Architecture map

Dependency direction: `api -> agent -> library -> core`. Nothing imports upwards (`core` knows nothing of HTTP or the library; `library` nothing of the assistant).

| Package | Modules | Responsibility |
|---|---|---|
| `core/` | `pipeline.py`, `parser.py`, `segment.py`, `tev.py`, `extractor.py`, `grounding.py`, `layout.py`, `tables.py`, `checkboxes.py`, `forms.py`, `schema.py`, `utils.py` | Parse, split, classify and extract forms; response models of the extraction routes |
| | `flags.py` | `Flag {field, code, message}` and `FlagCode`, the one list of flag codes |
| | `jobs.py` | `JobGate`: extraction slots shared by the extract/bulk/library-upload routes and the `extract_document` tool |
| | `auth.py`, `db.py`, `models.py`, `config.py` | API keys (registers ORM, `DATABASE_URL`), settings from env / `.env` |
| `library/` | `config.py` | `LibraryConfig`, `EmbeddingConfig`, `Endpoint` (sections of `config/model_config.yml`) |
| | `schema.py` | API models: documents, forms, `Check`, `Review`, search, stats |
| | `ingest.py` | Uploaded bytes -> `DocumentDraft` (forms, page sizes, chunks, SSNs masked in chunks) |
| | `store.py` | SQLite schema and every query; `where()` turns filters + owner into SQL |
| | `search.py` | `Embedder` (OpenAI-compatible `/embeddings`), hybrid search over chunks |
| | `files.py` | Originals on disk (`library.files`, by SHA-256), page sizes, PNG rendering, PDF page subsets |
| | `review.py` | `checks`, `corrected_fields`, `normalise`, `undecided`; review exceptions |
| | `service.py` | `Library`: one method per use case; what routes and tools call |
| `agent/` | `config.py` | `ChatConfig`, `AssistantSettings`, `load_config` (the whole YAML file) |
| | `agent.py` | `ChatClient` (OpenAI-compatible and Anthropic wire formats), `Assistant.reply` (tool loop) |
| | `tools.py` | `Tool`, `ToolContext`, `TOOLS` registry and the five tools |
| | `schema.py` | Chat request/response models |
| `api/` | `app.py` | `create_app`: state (`pipeline`, `library`, `assistant`, `jobs`, rate windows), middleware, routers, dashboard mount |
| | `deps.py` | Auth (`ClientDep`, `AdminDep`), `owner_scope`, `reviewer`, rate limits, `job_slot`, body limit, `read_upload` |
| | `errors.py` | Error envelope; `DOMAIN_ERRORS` maps domain exceptions to status + code |
| | `routes/` | `documents.py` (stateless extraction), `library.py`, `reviews.py`, `assistant.py`, `keys.py`, `system.py` |
| | `web.py` | Serves `web/dist` with SPA fallback outside API paths |
| `scripts/` | `openapi.py` | Writes `web/src/api/openapi.json` |
| `tests/` | `fakes.py` | Mock embeddings/chat transports, sample W-2 with flags, fake pipeline |
| | `e2e/fake_models.py`, `e2e/stack.py` | Fake model server; API + fake models + fresh data dir |
| `web/` | `src/api/`, `src/features/`, `src/components/`, `e2e/` | The dashboard (React + TypeScript): typed client and query hooks, one folder per page, shared components, Playwright E2E. Structure and traces: [web/README.md](../web/README.md) |

Ownership: every library query takes `owner` (the API key id as a string, or `None` for admin keys) from `api.deps.owner_scope`; `store.where` adds `d.owner = ?`. A document of another key is indistinguishable from a missing one (404).

## Request traces

### Upload: file -> forms, file, chunks

`POST /v1/library/documents` ([api/routes/library.py](../api/routes/library.py) `add_document`)

1. Dependencies: `require_api_key`, `RateLimit("library")`, `job_slot` (holds a `core.jobs.JobGate` slot for the whole request; `Busy` -> `503 busy`).
2. `read_upload` (size limit) -> `Library.find(owner, sha256)`: an existing upload of the same owner returns 200 with no work.
3. `library.ingest.read_file`: `file_type` (magic bytes, else suffix) -> for PDFs/images `scanned_chunks` -> `TaxDocumentPipeline.extract_file` (parse, `split`, `_classify_segment`, `_extract_form` -> `FormResult` per form with `validation.flags`) -> `form_chunks` for `ok` forms + `page_chunks` for other pages; `files.page_sizes`. Text files go to `markdown_chunks` / `row_chunks` / `xlsx_chunks` / `text_chunks`. SSNs are masked in every chunk.
4. `Library.add` ([library/service.py](../library/service.py)): `review.flagged_fields` per `ok` form, `Embedder.embed(chunks)`, then in a thread `Files.save` and `Store.insert` (one transaction: `documents`, `forms` with `flagged_fields` and `review_status = pending` for `ok` forms, `document_chunks` linked to their form by `form_id`; `index_dimensions` records/checks the embedding model) -> `Store.document` -> `DocumentDetail`.

### Flags: where they come from

`ValidationReport.flags` is assembled in `TaxDocumentPipeline._extract_form` ([core/pipeline.py](../core/pipeline.py)) from, in order: `_classify_segment` (`uncertain_type`), `ParsedDocument.gaps_in` (`partial_conversion`, `missing_page`), and `LLMExtractor.extract` ([core/extractor.py](../core/extractor.py)), which collects `no_output`, `clean_separators` (`text_as_json`, `multiple_values`), `validate_leniently` (`unparseable`), `grounding.verify` (`not_printed`, `wrong_label`, `unlabeled` for amounts and TINs; it also fills `sources` for the other values it finds, without flags) and `TaxForm.consistency_checks` (`inconsistent`). Field paths come from `core.flags.field_path` or the walk in `verify`, so they match `sources` keys and check fields.

### Review: read a form

`GET /v1/library/forms/{id}` ([api/routes/reviews.py](../api/routes/reviews.py)) -> `Library.form` -> `Store.form` (joins `documents` for the owner check; rebuilds the `FormResult` from the stored JSON and loads `reviews`) -> `Library._detail` -> `review.checks`: `_leaves` walks the form model class (not the data) in field order, so empty boxes appear; list fields yield one group per extracted row; value from the JSON dump, `source` from `validation.sources` (row source as fallback), flags grouped by `field`; flags naming no check become form-level. `corrected_fields` applies decisions to the dump and re-validates it with the form model.

### Review: a decision, and the chunk rebuild

`PUT /v1/library/forms/{id}/reviews/{field}` -> `Library.review`:

1. `Store.form`; `NotReviewable` (409) unless `status == "ok"`; `None` (404) when `field` is not a check path.
2. `corrected`: `review.normalise` sets the value in the dump and validates the whole form model, so the same parsers as extraction apply (`InvalidValue` -> 422).
3. `corrected_fields` before and after. If they differ: `ingest.form_chunks(form, fields=after)` and `Embedder.embed` (before any write; a 503 leaves nothing changed).
4. `Store.save_review` in one transaction: upsert or delete the `reviews` row, reopen the form if approved, and when chunks were built, delete the form's chunks (`form_id`) and insert the new ones. The FTS index follows through the triggers.
5. Returns the field's `Check`.

`POST .../approve` -> `Library.approve` -> `review.undecided(checks)`; non-empty -> `ApprovalBlocked` -> `409 review_incomplete` with `details.undecided` (mapping in `api/errors.py`) -> otherwise `Store.set_status(approved, reviewer)`.

### Pages and files

`GET .../documents/{id}/pages/{n}` (sync route, so FastAPI runs it in a thread) -> `Library.page_png` -> `Store.media` (owner check, hash, media type, page list) -> `Files.load` -> `files.render_png` (pypdfium2 at `width / page width`, or PIL with EXIF rotation) -> route sets `ETag` / answers `If-None-Match` with 304. `.../file` -> `Library.original`.

### Lists and stats

`GET /v1/library/documents` and `GET /v1/library/stats` share `_filters` (query parameters -> `DocumentFilters`). `store.where` builds the conditions (review status as `EXISTS` over `forms`, `q` as escaped `LIKE`); `store._matching` is the subquery of matching document ids; counts use `store.OPEN_FLAGS` (flagged fields of a form without a `reviews` row, via `json_each(forms.flagged_fields)`).

### Chat: the tool loop

`POST /v1/assistant/chat` -> `Assistant.reply(request, owner, pipeline, jobs)` ([agent/agent.py](../agent/agent.py)) builds a `ToolContext` (library, scope, owner, pipeline, jobs, numbered `sources`) and offers every `TOOLS` spec to `ChatClient.complete`. Each tool call is recorded in `tool_calls` and dispatched by name (`Assistant._run` -> `TOOLS[name].run(args, ctx)`); the returned text goes back as a `tool` message. After `max_tool_rounds` rounds the model is called without tools. In [agent/tools.py](../agent/tools.py): `narrow` merges the model's filters into the scope (refusing to widen it), `scoped_document` resolves `document_id` through `Library.list` with the scope and owner, `search_documents` numbers hits into `ctx.sources`, `classify_document` / `extract_document` load the stored file with `Library.original` and call the pipeline (`extract_document` inside `ctx.jobs.slot()`), and processing errors come back as `error: ...` text.

### Dashboard serving

`create_app(web_dist=...)` calls `api.web.mount_dashboard` when `web/dist/index.html` exists, after all routers: `_DashboardRoute` matches only GET/HEAD outside `API_PREFIXES`, serves the file at the path if it is inside `web/dist`, else `index.html`.

## Recipes

### Add an API route

1. Request/response models with `Field(description=...)` in the package's `schema.py` (`library/schema.py` for library data).
2. The use case as a `Library` method (or in `core` for stateless extraction); SQL in `library/store.py`, scoped with `where(..., owner)`.
3. The route in `api/routes/<area>.py`: auth via the router's `require_api_key`, `ClientDep` / `owner_scope(key)`, `Envelope[...]` response, `responses=responses(...)` for its error statuses. A new router module is included in `api/app.py`.
4. New domain exceptions: add to `DOMAIN_ERRORS` in `api/errors.py` (status, code, details).
5. Tests next to the area's (`tests/test_library.py` uses `make_test_app` with `tests.fakes`), then [change the API contract](#change-the-api-contract).

### Add an assistant tool

1. In `agent/tools.py`:

   ```python
   @tool("count_forms", "Count extracted forms per type for a client.",
         {"type": "object", "properties": {"client": {"type": "string"}}})
   async def count_forms(args: dict, ctx: ToolContext) -> str:
       filters = narrow(args, ctx.scope, ("client",))
       if isinstance(filters, str):
           return filters
       stats = await run_in_threadpool(ctx.library.stats, filters, ctx.owner)
       return ", ".join(f"{k}: {v}" for k, v in stats.forms_by_type.items()) or "No forms."
   ```

   Registration is the decorator; `Assistant` offers every registered tool. Keep tools read-only, go through `narrow` / `scoped_document` so owner and scope hold, and return `error: ...` text for bad input.
2. Mention the tool in `assistant.system_prompt` in `config/model_config.yml` and in the README tools table.
3. Test it with `run_tool(...)` in `tests/test_assistant.py` (owner `"2"` and an excluding scope must not see the document).

### Add a dashboard page

1. `web/src/features/<name>/<Name>Page.tsx` exporting the page component (styles in `<Name>Page.module.css`, built from `web/src/components/` and the tokens in `web/src/styles/tokens.css`).
2. One entry in `pages` in `web/src/features/index.ts`: `{ path: "/clients", label: "Clients", icon: Users, component: ClientsPage, nav: true }`. `nav` puts it in the sidebar; `fullBleed` drops the content padding and page scroll for pages that scroll their own panes.
3. Data through a hook in `web/src/api/<resource>.ts` (`useQuery` + `unwrap(api.GET(...))`); an endpoint that does not exist yet starts with [add an API route](#add-an-api-route) and [change the API contract](#change-the-api-contract).
4. `npm run typecheck`, `npm test`, and a step in `web/e2e/dashboard.spec.ts` when the page has a workflow. Details: [web/README.md](../web/README.md#add-a-page).

### Add a form type

1. Model in `core/forms.py` (class vars `FORM_ID`, `NAME` for display, `TITLE` as printed, `HINT` for the classifier) using `box()`, `party()`, `rows()`, `table()`, `column()`; `consistency_checks` returns `inconsistent(field, message)` flags on the field that looks off; register it in `FORMS`.
2. Fixtures in `tests/samples/` (`<id>.pdf`, `<id>_scan.png`, `<id>.json` ground truth) and tests.
3. Review checks, labels and kinds follow from the model (`Field` descriptions, the `Money`/`Tin`/`Date`/... types); the fake model server picks up `<id>.json` automatically.
4. [Change the API contract](#change-the-api-contract) (the form's schema is in the OpenAPI document).

### Change the API contract

```sh
uv run python -m scripts.openapi        # writes web/src/api/openapi.json
uv run pytest tests/test_web.py         # fails while the committed file is stale
```

Then regenerate the dashboard's types: `npm run gen:api` in `web/` (`web/src/api/schema.test.ts`, run by `npm test`, fails while `schema.d.ts` is stale), fix what `npm run typecheck` reports, and update the README contract tables. Breaking changes bump `version` in `api/app.py`.

### Add a flag code

1. Add it to `FlagCode` and the table in `core/flags.py`.
2. Emit `Flag(field=<path or None>, code="...", message="...")` where it is detected; never put a full TIN in `message`.
3. README flag table; [change the API contract](#change-the-api-contract) (the code is an enum in OpenAPI). Flags on a field that has a check block approval until decided; others are form-level.

### Change the library database schema

Edit `SCHEMA` in `library/store.py` and increase `SCHEMA_VERSION`. A database file with another version is refused with `500 outdated_library` (there are no migrations): say so in the README's breaking-change note.

## Tests

| File | Covers |
|---|---|
| `tests/test_unit.py` | Parsers, schemas, layout, checkboxes, tables, splitting, grounding flags and sources, consistency flags |
| `tests/test_pipeline.py` | Bulk duplicates, partial conversions and missing pages (fake parser/extractor) |
| `tests/test_api.py` | Auth, rate limits, validation, error envelope, body limit, job gate (fake pipeline) |
| `tests/test_assistant.py` | Ingestion, search, provider wire formats, tool loop, every tool |
| `tests/test_library.py` | Checks, decisions, approval, chunk rebuild, stats/filters, files, page rendering, library and review routes |
| `tests/test_web.py` | OpenAPI file current, SPA fallback |
| `tests/e2e/test_fake_models.py` | The fake model server answers the real clients |
| `web/src/**/*.test.ts` (`npm test`) | Corrected-value parsing, box and crop geometry, citations, answer Markdown, chat helpers (greeting, scroll, tool wording), generated types current |
| `web/e2e/dashboard.spec.ts` (`npm run e2e`) | The dashboard on the full stack: sign-in, upload, overview, review and approval, theme, assistant (panel citations and sources, page conversation, New chat), delete |
| `tests/test_integration.py` | Real Docling + Ollama (`-m integration`; skipped without the models) |

`uv run pytest -m "not integration" -q` runs everything without models. Offline, set `HF_HUB_OFFLINE=1` first (POSIX shells: prefix the command with it; PowerShell: `$env:HF_HUB_OFFLINE = "1"`).

## Run the full stack locally without GPUs

Docling runs for real (CPU; weights from the Hugging Face cache, or `DOCLING_ARTIFACTS_PATH`); extraction, embeddings and chat come from [tests/e2e/fake_models.py](../tests/e2e/fake_models.py) (see its docstring for what it answers); classification uses printed headings (`TEV_MODEL=none`).

One command (fresh temporary data directory, keys printed as JSON and written to `--state`):

```sh
uv run python -m tests.e2e.stack --api-port 8000 --models-port 11435 --state tda-e2e.json
# {"api": "http://127.0.0.1:8000", "admin_key": "ak_...", "client_key": "ak_...", "data": "<temp dir>/tda-e2e-..."}
```

Set `HF_HUB_OFFLINE=1` (as above) when Docling's weights are already cached, and `HF_HUB_CACHE` when they are not in the default cache. The command is plain Python, so it is the same on Windows. Stop it with Ctrl+C (it also stops the fake model server, a thread of the same process).

The JSON line is printed (and the state file written) before uvicorn starts; poll `GET /health` for `"status":"ok"` (Docling loads its models first, 10–30 s). `--data DIR` reuses a directory. Rate limits are raised to 1000 calls per window unless set in the environment. The built dashboard (`web/dist`) is served at `/` when present (when it existed at startup); for frontend work run `npm run dev` in `web/` instead, which proxies the API paths to :8000.

The same by hand, with the regular CLI (POSIX shell; on Windows use Git Bash, or the one command above):

```sh
mkdir -p /tmp/tda-dev
uv run python -m tests.e2e.fake_models --port 11435 &
sed -e 's#^  base_url: null#  base_url: http://127.0.0.1:11435/v1#' \
    -e 's#database: data/library.db #database: /tmp/tda-dev/library.db #' \
    -e 's#files: data/files #files: /tmp/tda-dev/files #' config/model_config.yml > /tmp/tda-dev/model_config.yml
export DATABASE_URL=sqlite:////tmp/tda-dev/keys.db OLLAMA_URL=http://127.0.0.1:11435 TEV_MODEL=none \
       ASSISTANT_CONFIG=/tmp/tda-dev/model_config.yml HF_HUB_OFFLINE=1
uv run main.py create-key --name admin --admin
uv run main.py create-key --name client          # use this key below as KEY
uv run main.py serve --port 8000 &
```

A session that exercises every feature (POSIX shell; `KEY` is the client key; Python prints the ids):

```sh
A=http://127.0.0.1:8000
set -- $(curl -s -H "X-API-Key: $KEY" -F "file=@tests/samples/w2.pdf" -F client=Gonzalez -F tax_year=2025 $A/v1/library/documents \
  | uv run python -c "import json,sys; d=json.load(sys.stdin)['data']; print(d['id'], d['forms'][0]['id'])")
DOC_ID=$1 FORM_ID=$2
curl -s -H "X-API-Key: $KEY" -F "file=@tests/samples/stack.pdf" -F client=Okafor $A/v1/library/documents >/dev/null
curl -s -H "X-API-Key: $KEY" $A/v1/library/forms/$FORM_ID                   # checks; 2 flagged fields
curl -s -o page1.png -H "X-API-Key: $KEY" "$A/v1/library/documents/$DOC_ID/pages/1?width=1200"
curl -s -X POST -H "X-API-Key: $KEY" $A/v1/library/forms/$FORM_ID/approve   # 409 review_incomplete
curl -s -X PUT -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"decision": "confirmed"}' \
  $A/v1/library/forms/$FORM_ID/reviews/control_number
curl -s -X PUT -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"decision": "corrected", "value": "68,412.55"}' \
  $A/v1/library/forms/$FORM_ID/reviews/wages_tips_other_comp
curl -s -X POST -H "X-API-Key: $KEY" $A/v1/library/forms/$FORM_ID/approve   # approved
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What were the wages tips other compensation?"}], "scope": {"client": "Gonzalez"}}' \
  $A/v1/assistant/chat                                                      # tool_calls: search_documents; cites [1]
curl -s -H "X-API-Key: $KEY" $A/v1/library/stats
```

What to expect with the fake models: `w2.pdf` gives one `ok` W-2 with two flagged fields (`wages_tips_other_comp`: `not_printed`, because the fake raised it by 1000; `control_number`: `multiple_values`); `stack.pdf` gives a W-2, a 1099-NEC and a 1098-T (two flags each) and one `unsupported` page. The chat answer quotes the reviewed value from the rebuilt chunk. A message starting with "list" makes the fake call `list_documents` instead of `search_documents`.
