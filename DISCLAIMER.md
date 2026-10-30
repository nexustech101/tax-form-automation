# Disclaimer and limitation of liability

This document applies to the Tax Document API (including its document library
and AI assistant), the n8n intake workflow in `n8n/`, and everything they produce (together, "the Software"). It is a
template written by the developer, not legal advice. Have an attorney
licensed in your state review it, and the contract it is attached to, before
relying on it.

## 1. Not tax, legal or accounting advice

The Software reads tax documents and reports what is printed on them. It does
not prepare returns, give tax, legal or accounting advice, or make filing
decisions. The notes written by the n8n "Accountant Assistant" (summaries,
issues, client questions, where amounts are usually reported) and the answers
of the API's document assistant are suggestions for a qualified preparer to
check, not advice. The assistant answers from excerpts it retrieves; it can
miss a document, cite the wrong one, or misstate what an excerpt says. The Software is not affiliated
with or endorsed by the IRS or any government agency.

## 2. Every value must be reviewed by a qualified person

All output is produced automatically by software and machine-learning models
and **can be wrong or incomplete**, including when it reports no warnings. In
particular:

* text read by OCR from scans and photos can contain misread characters,
  digits and punctuation;
* forms, layouts and documents the Software was not built or tested for
  (including handwritten forms and statements from issuers it has not seen)
  may be misidentified or partly read;
* consistency checks and grounding remove some errors but do not detect all of them;
* a value may be correct as printed while the source document itself is wrong.

Before any value is used on a return, filing, or in advice to a client, a
qualified person must compare it with the source document. The Software marks
documents it considers risky (`needs_review`, validation warnings), but a
document *not* marked is not a guarantee of correctness.

## 3. No warranty

THE SOFTWARE IS PROVIDED "AS IS" AND "AS AVAILABLE", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR
A PARTICULAR PURPOSE, ACCURACY, AND NON-INFRINGEMENT. No statement of accuracy
in the documentation (including test results on sample documents) is a
warranty about the client's documents.

## 4. Limitation of liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE DEVELOPER IS NOT LIABLE FOR ANY
INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL OR PUNITIVE DAMAGES, OR FOR LOST
PROFITS, LOST DATA, TAX, PENALTIES, INTEREST, OR COSTS OF AMENDED OR LATE
FILINGS, ARISING FROM USE OF OR RELIANCE ON THE SOFTWARE OR ITS OUTPUT, EVEN IF
ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. THE DEVELOPER'S TOTAL LIABILITY FOR
ANY CLAIM IS LIMITED TO THE FEES PAID FOR THE SOFTWARE OR SERVICE GIVING RISE TO
THE CLAIM IN THE TWELVE MONTHS BEFORE THE CLAIM.

## 5. The client's responsibilities

The Software runs on systems the client controls. The client is responsible for:

* reviewing all output before use (section 2);
* the security of the machines, networks, backups and accounts it runs on,
  including access to the API keys, the n8n instance, its execution history and
  the `tax_document_log` table;
* its obligations as a holder of taxpayer data, including a written
  information security plan under the FTC Safeguards Rule (see IRS
  Publication 5708) and the IRS data-security guidance for tax professionals
  (Publication 4557);
* how long documents and extracted data are kept, and their deletion,
  including the document library database and its backups;
* obtaining any consent its clients' documents require before they are processed;
* the licenses of third-party components it runs, including n8n and the
  model weights (see `THIRD_PARTY_LICENSES.md`).

## 6. Data handling, as designed

* The extraction routes do not store uploaded documents or extracted values.
  The API-key database stores only hashed keys and their metadata. Logs carry
  request ids, timings, form types and counts, not document text or values.
* Documents added to the document library (`/v1/library/documents`) are stored
  as searchable text and extracted fields in the library database
  (`library.database` in `config/model_config.yml`), with Social Security
  numbers masked to the last four digits before storage. Names, addresses,
  amounts and employer identification numbers are stored as printed. The
  original file is not kept. Each API key sees only the documents it added;
  admin keys see all of them. Documents stay until deleted through the API.
* Models run locally through Ollama; documents are not sent to an outside AI
  service unless the client reconfigures the workflow or
  `config/model_config.yml` to use one. When the assistant is configured with
  a cloud provider, that provider receives the questions asked and the
  library excerpts retrieved to answer them.
* The n8n workflow masks tax identification numbers to the last four digits
  before they reach the AI assistant, the log table or any email or chat alert.

These are properties of the Software as delivered. Changes to its
configuration or code by anyone other than the developer can change them.

## 7. Third-party software and models

The Software depends on third-party packages, services and model weights that
are provided by others under their own licenses and without warranty from the
developer. Their availability, licensing and behaviour may change.

## 8. Sample documents

The documents in `tests/samples/` are synthetic. All names, tax identification
numbers, addresses and amounts in them are fictitious.
