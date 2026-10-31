# Dashboard (`web/`)

React 19 + TypeScript (strict) single-page app for reviewing the document library: overview, documents, the review workspace, the assistant and settings. Built with Vite to `web/dist`, which the API serves at `/` ([api/web.py](../api/web.py)). Every API type is generated from [src/api/openapi.json](src/api/openapi.json).

## Commands

Run in `web/` (Node 20.19+ or 22.12+).

| Command | What it does |
|---|---|
| `npm ci` | Install the pinned dependencies (`package-lock.json`) |
| `npm run dev` | Vite dev server on :5173; `/v1`, `/auth`, `/api-keys`, `/health`, `/docs`, `/redoc`, `/openapi.json` are proxied to `API_URL` (default `http://127.0.0.1:8000`) |
| `npm run build` | `tsc -b` then `vite build` -> `web/dist` |
| `npm run preview` | Serve `web/dist` on :4173 with the same proxy |
| `npm run typecheck` | `tsc -b --noEmit` (app, config and E2E files) |
| `npm run gen:api` | `src/api/openapi.json` -> `src/api/schema.d.ts` (openapi-typescript) |
| `npm test` | Vitest unit tests (`src/**/*.test.ts`) |
| `npm run e2e` | `npm run build`, then Playwright against a real stack on fake models (see [E2E](#e2e)) |

A backend for local work without GPUs: `uv run python -m tests.e2e.stack --api-port 8000 --models-port 11435` from the repository root (prints an admin and a client key; see [docs/DEVELOPING.md](../docs/DEVELOPING.md#run-the-full-stack-locally-without-gpus)), then `npm run dev` and sign in with the client key.


Styling is plain CSS: CSS modules next to each component (`Button.module.css`), global tokens in `src/styles/tokens.css`. No UI kit, CSS framework, state library or chart library.

## Add a page

1. Create `src/features/<name>/<Name>Page.tsx` (and `<Name>Page.module.css`) exporting the component.
2. Add one line to `pages` in [src/features/index.ts](src/features/index.ts):

   ```ts
   { path: "/clients", label: "Clients", icon: Users, component: ClientsPage, nav: true },
   ```

`nav: true` lists it in the sidebar (in array order); `icon` is a lucide-react icon. `App.tsx` turns every entry into a route inside `Shell`; `fullBleed: true` renders it without the content padding and page scroll (the review and assistant pages manage their own scrolling). Start the page with `<PageHeader title=... />` and put content in `<Card>`s.

## Call a new endpoint

1. Change the API (models with `Field` descriptions), then from the repository root: `uv run python -m scripts.openapi` (writes `src/api/openapi.json`; `tests/test_web.py` fails while it is stale).
2. `npm run gen:api` (`src/api/schema.test.ts` fails while `schema.d.ts` is stale). Never edit `schema.d.ts` or declare API shapes by hand.
3. Add a hook to the resource's file in `src/api/` (or a new file):

   ```ts
   export function useClientSummary(client: string) {
     return useQuery({
       queryKey: ["clients", client],
       queryFn: () => unwrap(api.GET("/v1/library/clients/{client}", { params: { path: { client } } })),
     });
   }
   ```

   `unwrap` returns `data` from the `{data}` envelope or throws `ApiError {status, code, message, details}` built from the error envelope; `ok` is for 204 and binary responses (`parseAs: "blob"`). Paths, parameters and bodies are type-checked against `schema.d.ts`.
4. If a component needs the type by name, add an alias in `src/api/types.ts` (`Schemas["ClientSummary"]`; aliases go through openapi-fetch's `Readable`, so they match what the client returns).
5. Mutations invalidate what they change: library writes call `invalidateLibrary` (document lists and stats); review writes also refresh the form and its document (`review.ts`).

## How things run

### Requests and auth

`api/client.ts` creates one openapi-fetch client. Its middleware adds `X-API-Key` (set by `setApiKey`) to every request, and on a 401 calls the handler registered by `AuthProvider`, which clears the key, empties the query cache and shows the sign-in screen with a notice. Sign-in calls `GET /auth/me` with the entered key; the key is kept in `sessionStorage["tda.key"]`, or `localStorage` with "Remember on this device". On load, a stored key is verified with `/auth/me` before anything renders. Query defaults (`App.tsx`): 15 s stale time, no refetch on focus, no retry of 4xx errors.

### Documents and upload

`DocumentsPage` keeps its filters (`q`, `client`, `tax_year`, `document_type`, `review_status`, `offset`) in the URL, so the overview's links, the top bar search (`/documents?q=`) and reloads land on the same list; `?upload=1` opens the upload dialog. The list is `GET /v1/library/documents` (25 per page), its counts come with each row (`forms_pending`, `open_flags`, `review_status`). Upload sends the files one after another (`POST /v1/library/documents`, multipart: `file`, `client`, `tax_year`, `extract=true`); each row shows its own result or error message (409/413/415/422/503 bodies). Short form names (`W-2`) come from `GET /v1/document-types` (`name`), fetched once per session (`useTypeName`).

### Review workspace

`/documents/:id?form=<form id>&page=<n>` ([features/review](src/features/review)):

1. `ReviewPage` loads the document (`GET /v1/library/documents/{id}`: pages with sizes, forms). Without `?form`, it opens the extracted form containing `?page` (assistant citations), else the first extracted form. Duplicate and unsupported forms show an explanation instead of checks.
2. `useForm` loads `GET /v1/library/forms/{id}`: checks, form flags, progress. `FormReview` filters them (Flagged: has flags; Unchecked: no decision; All), defaulting to Flagged when the form has open flags.
3. Pages: `usePageImage` fetches `GET .../pages/{n}?width=1600` as a blob (the route needs the key header, so no `<img src>` to the API), caches it for the session and hands out an object URL. The viewer and every crop use the same cached image.
4. Geometry ([geometry.ts](src/features/review/geometry.ts)): `ValueSource.box` is `[left, top, right, bottom]` on a page 1000 units wide (both axes; a page is `1000 * height / width` units tall). `boxStyle` places overlays in percent of the page, so they follow the zoom; `cropStyle` sizes the page as a background image so that the box plus 12 units of padding fills at most 200 x 36 px. Checks without a source (a value not found in the page text, a removed value, an empty box) show "Not located".
5. Selecting a check (click, `j`/`k`/arrows, or a box on the page) switches the viewer to the source page and scrolls both the row and the box into view.
6. Decisions: `PUT /v1/library/forms/{id}/reviews/{field}` with `confirmed`, `corrected` (value from the kind-aware editor: select for checkboxes, choices and months, date input for dates, text otherwise; [values.ts](src/features/review/values.ts) catches obvious typos, the API parses and answers `422 invalid_value` with the message shown in the row) or `rejected`; Undo is `DELETE` on the same path. The returned check replaces the row in the cache at once, then the form, document, list and stats queries refetch. After a decision the next visible row is selected.
7. Approve is disabled, with the reason, while a flagged field has no decision; `POST .../approve` answering `409 review_incomplete` shows the message and selects the first field of `details.undecided`. An approved form shows who approved it and a Reopen button (`POST .../reopen`).

Shortcuts (ignored while typing): `j`/`k` or arrows move, `c` confirm, `e` correct, `r` reject, `u` undo, `Esc` cancels an edit. `Enter` saves a correction.

### Assistant

`AssistantProvider` ([app/assistant.tsx](src/app/assistant.tsx)) holds one in-memory conversation and scope shared by the slide-over (`AssistantPanel`, opened from the top bar or "Ask about this document") and `/assistant` (`AssistantPage`); both render `Chat` from [features/assistant](src/features/assistant):

| File | Part |
|---|---|
| `Chat.tsx` | Empty state (page: greeting, composer, suggestion pills, "Needs review"; panel: intro and suggestion list), conversation thread, composer dock |
| `Composer.tsx` | Question box, "+" menu (upload, attach), scope pills or document chips, send / stop |
| `Message.tsx` | User bubble, answer (Markdown, citations, sources, tool line, copy), pending and failure rows |
| `chatUtils.ts`, `markdown.ts`, `citations.ts` | `greeting`, `isNearBottom`, `describeTool`; the Markdown subset; `[n]` citations |

1. Scope: `{client?, tax_year?, documents[]}`, sent as `toFilters(scope)` (`client`, `tax_year`, `document_ids`). The composer shows "All clients" / "Any year" pills (clients from `GET /v1/library/stats`, the last eight years) or, once documents are attached, a chip per document; attaching clears client and year, since all filters must match. Opening the panel on a review page scopes it to that document (the page registers it with `setCurrentDocument`).
2. "+" menu: "Upload a document" uploads one file with `useUploadDocument` (the scope's client and year; a chip shows "Uploading…", errors show in the composer) and attaches the result; "Attach a library document" searches `GET /v1/library/documents?q=` (8 results; Enter picks the first) and attaches the pick. Menus are `components/Menu.tsx`: arrows, Home and End move, Esc closes and refocuses the trigger, Tab or a click outside closes.
3. Sending (Enter; Shift+Enter breaks the line; the textarea grows to 8 rows) appends the question and posts the whole conversation and the scope to `POST /v1/assistant/chat` (the API is stateless). While it runs, Send becomes Stop (aborts the request; "Stopped." with Retry) and the thread shows "Searching documents…" ("Reading <title>…" with one attached document) with the elapsed seconds after 5 s: the API answers once, so the wording follows the scope, not live tool progress. A failure shows its message with Retry, which resends the conversation.
4. An answer is parsed by `parseMarkdown` (paragraphs, headings, lists, fenced code, bold, inline code; rendered as elements, never as HTML). `[n]` becomes a citation chip (only numbers present in `sources`; hover or focus shows title and page). Below: "Sources" cards for the cited excerpts (title, form name, page), the other retrieved excerpts behind "+n more retrieved", the tool line (`describeTool`, e.g. `Searched “wages” · Read w2`; expands to each call's name and arguments) and Copy. A citation or source opens `/documents/<document_id>?page=<page_number>`.
5. The thread keeps the newest message in view unless the user scrolled more than 80 px up (`isNearBottom`, also on resize); sending always returns to the end. "New chat" (page top-right, panel header; shown once there is a conversation) aborts a running request and clears the turns; a late reply is dropped.
6. Empty page: "Needs review" lists up to three documents from `GET /v1/library/documents?review_status=pending` with open flags; a card asks "What still needs review in this document?" scoped to it. Suggestions switch to document questions while documents are attached.

## Theming

Tokens are CSS custom properties in [src/styles/tokens.css](src/styles/tokens.css): `:root` is the light theme, `:root[data-theme="dark"]` overrides the colours. `ThemeProvider` stores the choice (`light` default, `dark`, `system`) in `localStorage["tda.theme"]` (failures ignored) and sets `data-theme` on `<html>`, following `prefers-color-scheme` while on `system`; the inline script in `index.html` does the same before the first paint.

| Group | Tokens |
|---|---|
| Type | `--font` (Inter Variable), `--mono`, `--fs-xs` 11.5 / `--fs-sm` 12.5 / `--fs-md` 14 (base) / `--fs-lg` 16 / `--fs-xl` 20 / `--fs-2xl` 26 px; `--fs-chat` 15 (messages, composer), `--fs-display` 28 (assistant greeting) |
| Spacing | `--s-1` 4, `--s-2` 8, `--s-3` 12, `--s-4` 16, `--s-5` 20, `--s-6` 24, `--s-8` 32, `--s-10` 40 px |
| Shape | `--r-sm` 4, `--r` 6, `--r-lg` 8, `--r-xl` 12, `--r-2xl` 18 px, `--r-full`; `--shadow-xs`, `--shadow-sm`, `--shadow-lg`, `--shadow-popover` (menus), `--shadow-composer` / `--shadow-composer-focus`; `--control-h` 32 px |
| Surfaces | `--bg`, `--surface`, `--surface-2`, `--surface-hover`, `--surface-sunken`, `--border`, `--border-strong`, `--overlay`, `--bubble` (the user's chat messages) |
| Text | `--text`, `--text-2`, `--text-3` (4.5:1 or more on every surface token in both themes), `--text-on-accent` |
| Accent | `--accent`, `--accent-hover`, `--accent-text`, `--accent-soft`, `--accent-border`, `--focus-ring` |
| States | `--success`, `--warning`, `--danger`, `--info`, each with `-soft` and `-border` |
| Page overlays | `--box`, `--box-flag`, `--box-done`, `--box-selected`, each with `-fill` |

Tones map to meaning in [src/lib/flags.ts](src/lib/flags.ts): removed values (`not_printed`, `wrong_label`, `unparseable`, `no_output`) are `danger`, values to double-check `warning`, form-level facts `info`; decisions are confirmed `success`, corrected `accent`, rejected `danger`. Amounts use `.num` (tabular numerals).

## Tests

Unit tests (Vitest, `npm test`):

| File | Covers |
|---|---|
| `src/features/review/values.test.ts` | Corrected-value parsing per kind, editor start values |
| `src/features/review/geometry.test.ts` | Box placement in percent, crops with padding and clamping |
| `src/features/assistant/citations.test.ts` | `[n]` / `[1, 2]` citations, unknown numbers left as text |
| `src/features/assistant/markdown.test.ts` | Paragraphs, lists with continuations, headings, fenced code, bold and code spans, HTML kept as text |
| `src/features/assistant/chatUtils.test.ts` | Greeting by hour, scroll stickiness threshold, tool-call wording |
| `src/api/schema.test.ts` | `schema.d.ts` is current with `openapi.json` |
| `src/filenames.test.ts` | No two modules differ only in case (Windows and macOS ignore case, so `./Chat` could load `chat.ts`) |

### E2E

`npm run e2e` builds, then [e2e/global-setup.ts](e2e/global-setup.ts) runs `uv run --no-sync python -m tests.e2e.stack` from the repository root with a fresh temporary data directory (API on `E2E_API_PORT`, default 8765; fake models on `E2E_MODELS_PORT`, default the next port), serving the new `web/dist`, waits for `/health` to report `ok` (Docling loads first) and stops it after the run (its process group; `taskkill /T` on Windows). It sets `HF_HUB_OFFLINE=1` unless the environment says otherwise; set `HF_HUB_CACHE` (or `DOCLING_ARTIFACTS_PATH`) when Docling's weights are not in the default cache. Playwright is pinned to 1.63.0 to match the installed Chromium; `npx playwright install chromium` provides one elsewhere.

[e2e/dashboard.spec.ts](e2e/dashboard.spec.ts) is one test in steps: wrong key refused and sign-in; upload `tests/samples/w2.pdf` and `stack.pdf` for a client; overview counts (2 documents, 4 forms, 4 awaiting review, 8 open flags); W-2 review: Approve disabled with its reason, confirm, correct (Enter), reject and undo by keyboard, approve; dark theme kept after reload; assistant panel: an answer with a search tool line, a citation, and a source that opens the document at its page; assistant page: the same conversation, a `list_documents` question sent with Enter, New chat back to the greeting; delete with confirmation.

## Dependencies

| Package | Version | Use |
|---|---|---|
| react, react-dom | 19.2.9 | UI |
| react-router | 7.18.4 | Routing (library mode: `BrowserRouter`, `Routes`) |
| @tanstack/react-query | 5.104.1 | Server state: caching, invalidation, mutations |
| openapi-fetch | 0.17.0 | Typed fetch client over `schema.d.ts` |
| lucide-react | 1.54.0 | Icons |
| @fontsource-variable/inter | 5.3.0 | Inter variable font, bundled |
| vite, @vitejs/plugin-react | 7.3.7, 5.2.0 | Dev server, build |
| typescript | 5.9.3 | Type checking (openapi-typescript 7 supports TypeScript 5) |
| openapi-typescript | 7.13.0 | `npm run gen:api` |
| vitest | 5.0.5 | Unit tests |
| @playwright/test | 1.63.0 | E2E |
| @types/react, @types/react-dom, @types/node | 19.2.18, 19.2.7, 22.20.5 | Types (Node types for config, E2E and the schema test) |
