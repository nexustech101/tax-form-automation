// Runs the workflow's Code nodes with a small n8n-like runtime ($input, $json, $(...)) and checks behaviour.
// usage (from the repo root): node n8n/tests/code-nodes.test.mjs n8n/tax-document-intake.workflow.json n8n/tests/api-fixtures.json
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const wf = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const fx = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const byName = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// ctx: node name -> array of output items (what $('Node') sees). Paired item = same index.
async function run(name, items, ctx = {}, pairIndex = 0) {
  const node = byName[name];
  const each = node.parameters.mode === 'runOnceForEachItem';
  const $ = (n) => {
    if (!ctx[n]) throw new Error(`$('${n}') not executed`);
    return { all: () => ctx[n], first: () => ctx[n][0], item: ctx[n][pairIndex], itemMatching: (i) => ctx[n][i] };
  };
  const fn = new AsyncFunction('$input', '$json', '$', '$workflow', node.parameters.jsCode);
  const $workflow = { id: 'wf1' };
  if (!each) {
    const out = await fn({ all: () => items, first: () => items[0] }, items[0]?.json, $, $workflow);
    return out;
  }
  const out = [];
  for (let i = 0; i < items.length; i++) {
    const $i = (n) => ({ all: () => ctx[n], first: () => ctx[n][0], item: ctx[n][i], itemMatching: (k) => ctx[n][k] });
    const r = await fn({ item: items[i] }, items[i].json, $i, $workflow);
    out.push(...(Array.isArray(r) ? r : [r]));
  }
  return out;
}

const results = [];
async function test(title, body) {
  try { await body(); results.push(`ok   ${title}`); }
  catch (e) { results.push(`FAIL ${title}\n     ${e.message.split('\n').join('\n     ')}`); }
}

const pdf = (name, size = '120 kB') => ({ fileName: name, mimeType: 'application/pdf', fileSize: size, id: `bin-${name}` });

// ---------------------------------------------------------------- intake
await test('form upload: one item per file, fields mapped', async () => {
  const out = await run('Prepare Form Files', [{ json: { 'Your full name': ' Maria Gonzalez ', 'Email address': 'Maria@Example.com', 'Tax year': '2025' },
    binary: { Tax_documents_0: pdf('w2.pdf'), Tax_documents_1: pdf('1099.pdf') } }]);
  assert.equal(out.length, 2);
  assert.deepEqual(Object.keys(out[1].binary), ['document']);
  assert.equal(out[0].json.client_email, 'maria@example.com');
  assert.equal(out[0].json.client_name, 'Maria Gonzalez');
  assert.equal(out[1].json.file_name, '1099.pdf');
});

await test('email: sender parsed, year from subject, logo skipped', async () => {
  const out = await run('Prepare Email Attachments', [{ json: { from: '"Devon Patel" <DEVON@example.com>', subject: 'My 2025 tax docs' },
    binary: { attachment_0: pdf('nec.pdf'), attachment_1: { fileName: 'logo.png', mimeType: 'image/png', fileSize: '8.2 kB' },
              attachment_2: { fileName: 'photo.jpg', mimeType: 'image/jpeg', fileSize: '1.4 MB' } } }]);
  assert.deepEqual(out.map((o) => o.json.file_name), ['nec.pdf', 'photo.jpg']);
  assert.equal(out[0].json.client_email, 'devon@example.com');
  assert.equal(out[0].json.client_name, 'Devon Patel');
  assert.equal(out[0].json.tax_year, '2025');
});

await test('drive: file moved to document', async () => {
  const out = await run('Prepare Drive File', [{ json: { id: 'f1', name: 'W2 2025 Gonzalez.pdf' }, binary: { data: pdf('W2 2025 Gonzalez.pdf') } }]);
  assert.equal(out[0].binary.document.fileName, 'W2 2025 Gonzalez.pdf');
  assert.equal(out[0].json.tax_year, '2025');
});

await test('api webhook: body fields + files', async () => {
  const out = await run('Prepare API Upload', [{ json: { body: { client_email: 'A@B.com', client_name: 'A B' } }, binary: { file: pdf('x.pdf') } }]);
  assert.equal(out[0].json.client_email, 'a@b.com');
  assert.equal(out[0].json.source, 'API');
});

// ---------------------------------------------------------------- pipeline
const intake = { json: { source: 'Upload form', client_name: 'Maria Gonzalez', client_email: 'maria@example.com', tax_year: '2025',
  file_name: 'w2.pdf', received_at: '2026-10-27T12:00:00Z' }, binary: { document: pdf('w2.pdf') } };
const settingsOut = await run('Firm Settings', [intake, structuredClone(intake)]);

await test('settings: id + settings added, binary kept', async () => {
  assert.equal(settingsOut.length, 2);
  assert.ok(settingsOut[0].json.document_id && settingsOut[0].json.document_id !== settingsOut[1].json.document_id);
  assert.equal(settingsOut[0].json.settings.min_confidence, 0.9);
  assert.ok(settingsOut[0].binary.document);
});

const httpOk = { json: { statusCode: 200, body: fx.bulk } };
let split;
await test('split forms: one item per form; duplicate copy skipped; unsupported page -> problem', async () => {
  split = await run('Split Forms', [httpOk], { 'Firm Settings': settingsOut });
  assert.deepEqual(split.map((o) => o.json.kind), ['form', 'problem']);
  const [form, problem] = split.map((o) => o.json);
  assert.equal(form.doc.document_id, `${settingsOut[0].json.document_id}-1`);
  assert.equal(problem.doc.document_id, `${settingsOut[0].json.document_id}-3`);
  assert.deepEqual(problem.problem, { code: 'unsupported_pages', pages: '3', request_id: 'req-bulk' });
  assert.equal(form.form_count, 2);
  assert.ok(split.every((o) => o.pairedItem.item === 0));
});

await test('split forms: unreadable file -> problem with the API error code', async () => {
  const out = await run('Split Forms', [{ json: { statusCode: 200, body: fx.bulk_failed } }], { 'Firm Settings': settingsOut });
  assert.equal(out.length, 1);
  assert.equal(out[0].json.problem.code, 'unsupported_media_type');
  assert.equal(out[0].json.doc.document_id, settingsOut[0].json.document_id);
});

await test('split forms: unread pages -> one problem per file; explained as needs_review with partial note', async () => {
  const out = await run('Split Forms', [{ json: { statusCode: 200, body: fx.bulk_partial } }], { 'Firm Settings': settingsOut });
  const unread = out.filter((o) => o.json.problem?.code === 'missing_pages');
  assert.equal(unread.length, 1);
  assert.equal(unread[0].json.doc.document_id, `${settingsOut[0].json.document_id}-unread`);
  const r = (await run('Explain Failure', unread, { 'Firm Settings': settingsOut }))[0].json.record;
  assert.equal(r.status, 'needs_review');
  assert.equal(r.pages, '4');
  assert.match(r.summary, /page\(s\) 4 .*only partly converted/);
  assert.equal(split.filter((o) => o.json.problem?.code === 'missing_pages').length, 0, 'complete file reported unread pages');
});

let reviewIn;
await test('prepare review: TINs masked, form position flagged', async () => {
  const out = await run('Prepare Review', [split[0]]);
  reviewIn = out;
  const p = out[0].json.payload;
  assert.equal(out[0].json.action, 'review_document');
  assert.equal(out[0].json.query, 'maria@example.com');
  assert.equal(p.fields.employee.tin, 'XXX-XX-7890');
  assert.equal(p.fields.employer.tin, 'XXX-XX-6295');
  assert.ok(!JSON.stringify(out[0].json).includes('412-56-7890'), 'full SSN leaked');
  assert.deepEqual(p.rule_flags, ['One of 2 forms in this file (page 1).']);
  assert.equal(p.pages, '1');
});

await test('prepare review: scan, low confidence, warnings, year mismatch all flagged', async () => {
  const item = structuredClone(split[0]);
  item.json.form.classification.confidence = 0.62;
  item.json.form.text_layer = false;
  item.json.form.validation.warnings = ['box 4 (10) is not 6.2% of box 3 (1000)'];
  item.json.doc.tax_year = '2024';
  item.json.form_count = 1;
  const out = await run('Prepare Review', [item]);
  const f = out[0].json.payload.rule_flags.join(' | ');
  for (const needle of ['uncertain', 'Scanned', '6.2%', 'tax year 2024']) assert.ok(f.includes(needle), `missing flag: ${needle} in ${f}`);
});

await test('prepare review: long 1099-B summarised for the assistant, kept whole for the log', async () => {
  const item = structuredClone(split[0]);
  item.json.form.document_type = '1099_b';
  item.json.form.fields = { recipient: { tin: '123-45-6789' }, transactions: Array.from({ length: 40 }, (_, i) =>
    ({ description: `s${i}`, proceeds: 100, cost_basis: 90, form_8949_box: i < 30 ? 'A' : 'D' })) };
  const out = await run('Prepare Review', [item]);
  const p = out[0].json.payload.fields;
  assert.equal(p.transactions.length, 10);
  assert.deepEqual(p.transaction_summary.by_form_8949_box.A, { sales: 30, proceeds: 3000, cost_basis: 2700, wash_sale_loss_disallowed: 0 });
  assert.equal(out[0].json.fields.transactions.length, 40);
  assert.equal(out[0].json.fields.recipient.tin, 'XXX-XX-6789');
});

await test('prepare review: 1095-A SSNs masked', async () => {
  const item = structuredClone(split[0]);
  item.json.form.fields = { recipient_ssn: '412-56-7890', covered_individuals: [{ name: 'A', ssn: '412-56-7891' }] };
  const out = await run('Prepare Review', [item]);
  assert.ok(!JSON.stringify(out[0].json).match(/412-56-789/), 'SSN leaked');
});

// ---------------------------------------------------------------- sub-workflow: review
const entry = [{ json: { action: 'review_document', query: 'maria@example.com', payload: { document_id: 'doc-1' } } }];
await test('return review: passes agent output through', async () => {
  const output = { summary: 'W-2 from Brightline.', review_needed: false, issues: [], client_follow_ups: [], return_notes: ['Box 1 -> line 1a'] };
  const out = await run('Return Review', [{ json: { output } }], { 'Sub-workflow Entry': entry });
  assert.deepEqual(out[0].json, { document_id: 'doc-1', review: output });
});

await test('return review: fails closed when the agent errored', async () => {
  const out = await run('Return Review', [{ json: { error: { message: 'model timeout' } } }], { 'Sub-workflow Entry': entry });
  assert.equal(out[0].json.review.review_needed, true);
  assert.match(out[0].json.review.issues[0], /model timeout/);
});

// ---------------------------------------------------------------- build record
await test('log record: routine single-form doc + agent says fine -> ready', async () => {
  const single = structuredClone(reviewIn); single[0].json.payload.rule_flags = [];
  const id = single[0].json.payload.document_id;
  const out = await run('Build Log Record', [{ json: { document_id: id, review: { summary: 'ok', review_needed: false, issues: [] } } }],
    { 'Prepare Review': single });
  assert.equal(out[0].json.record.status, 'ready');
});

await test('log record: columns complete, fields masked', async () => {
  const id = reviewIn[0].json.payload.document_id;
  const out = await run('Build Log Record', [{ json: { document_id: id, review: { summary: 'ok', review_needed: false, issues: [], client_follow_ups: [], return_notes: ['n1'] } } }],
    { 'Prepare Review': reviewIn });
  const r = out[0].json.record;
  assert.equal(r.status, 'needs_review');  // one of several forms in the file is a rule flag
  assert.equal(r.pages, '1');
  assert.equal(r.form_name, 'Form W-2 Wage and Tax Statement');
  assert.equal(r.tax_year, '2025');
  assert.equal(r.return_notes, 'n1');
  assert.ok(!r.extracted_fields.includes('412-56-7890'));
  const save = byName['Save to Document Log'].parameters.columns;
  const cols = save.schema.map((c) => c.id).sort();
  assert.deepEqual(Object.keys(r).sort(), cols, 'record keys != table columns');
  const create = byName['Create tax_document_log'].parameters.columns.column.map((c) => c.name).sort();
  assert.deepEqual(create, cols, 'create-table columns != insert columns');
});

await test('log record: agent cannot clear a rule flag', async () => {
  const flagged = structuredClone(reviewIn); flagged[0].json.payload.rule_flags = ['Scanned document'];
  const id = flagged[0].json.payload.document_id;
  const out = await run('Build Log Record', [{ json: { document_id: id, review: { summary: 'fine', review_needed: false, issues: [] } } }],
    { 'Prepare Review': flagged });
  assert.equal(out[0].json.record.status, 'needs_review');
  assert.match(out[0].json.record.issues, /Scanned/);
});

await test('log record: missing review_needed counts as needs review', async () => {
  const id = reviewIn[0].json.payload.document_id;
  const out = await run('Build Log Record', [{ json: { document_id: id, review: { summary: 'x' } } }],
    { 'Prepare Review': reviewIn });
  assert.equal(out[0].json.record.status, 'needs_review');
});

// ---------------------------------------------------------------- failures
await test('explain failure: unsupported pages -> needs_review (a person handles them), no alert', async () => {
  const out = await run('Explain Failure', [split[1]], { 'Firm Settings': settingsOut });
  const r = out[0].json.record;
  assert.equal(r.status, 'needs_review');
  assert.equal(r.pages, '3');
  assert.match(r.summary, /Page 3 of this file is not one of the supported tax forms/);
  assert.equal(r.api_request_id, 'req-bulk');
  const alerts = await run('Write Team Alert', out);
  assert.equal(alerts.length, 0);
});

await test('explain failure: unreadable file in a bulk answer -> failed', async () => {
  const problem = await run('Split Forms', [{ json: { statusCode: 200, body: fx.bulk_failed } }], { 'Firm Settings': settingsOut });
  const out = await run('Explain Failure', problem, { 'Firm Settings': settingsOut });
  assert.equal(out[0].json.record.status, 'failed');
  assert.match(out[0].json.record.summary, /PDF or a picture/);
});

await test('explain failure: API unreachable (HTTP error output)', async () => {
  const out = await run('Explain Failure', [{ json: { error: { message: 'connect ECONNREFUSED' } } }], { 'Firm Settings': settingsOut });
  assert.match(out[0].json.record.summary, /could not reach the Tax Document API at http:\/\/localhost:8000/);
});

await test('explain failure: 401 and 503 map to actionable text', async () => {
  for (const [code, re] of [[401, /API key/], [503, /Ollama/], [415, /PDF or a picture/]]) {
    const out = await run('Explain Failure', [{ json: { statusCode: code, body: { error: { code: 'x', message: 'm' } } } }], { 'Firm Settings': settingsOut });
    assert.match(out[0].json.record.summary, re);
  }
});

// ---------------------------------------------------------------- team alerts
await test('team alert: failed record escaped for Slack/Telegram, one line for WhatsApp', async () => {
  const record = { status: 'failed', file_name: 'w2 <scan>.pdf', source: 'Email', client_name: 'Jane', summary: 'Ollama is down.\nStart it.', document_id: 'd1' };
  const out = await run('Write Team Alert', [{ json: { record, settings: { firm_name: 'Acme & Co' } } }]);
  const a = out[0].json;
  assert.match(a.slack_text, /^\*\[Acme &amp; Co\] Could not process a document\*/);
  assert.ok(a.slack_text.includes('w2 &lt;scan&gt;.pdf') && a.telegram_html.includes('<b>File:</b> w2 &lt;scan&gt;.pdf'));
  assert.ok(!a.whatsapp_text.includes('\n') && a.whatsapp_text.includes('Ollama is down. Start it.'));
});

await test('team alert: workflow crash from the Error Trigger', async () => {
  const out = await run('Write Team Alert', [{ json: { execution: { id: 42, url: 'https://n8n/x/42', error: { message: 'boom' }, lastNodeExecuted: 'Split Forms' }, workflow: { name: 'Tax Intake' } } }]);
  assert.match(out[0].json.slack_text, /Tax pipeline error[\s\S]*Split Forms[\s\S]*boom/);
});

// ---------------------------------------------------------------- email
await test('reviewer email: escapes HTML from documents', async () => {
  const record = { status: 'needs_review', client_name: '<script>alert(1)</script>', client_email: 'm@e.com', file_name: 'w2.pdf', source: 'Email',
    form_name: 'Form W-2', tax_year: '2025', document_id: 'd1', summary: 'a & b', issues: 'one\ntwo', client_follow_ups: '', return_notes: '' };
  const out = await run('Write Reviewer Email', [{ json: { record, settings: settingsOut[0].json.settings } }]);
  const e = out[0].json;
  assert.ok(!e.html.includes('<script>'));
  assert.ok(e.html.includes('&lt;script&gt;') && e.html.includes('a &amp; b'));
  assert.ok(e.html.includes('<li>one</li><li>two</li>'));
  assert.equal(e.to, 'review@yourfirm.com');
  assert.match(e.subject, /needs review: w2\.pdf/);
});

// ---------------------------------------------------------------- agent tools
const histEntry = (q) => ({ 'Sub-workflow Entry': [{ json: { action: 'client_history', query: q, payload: {} } }] });
await test('client history: lists earlier docs', async () => {
  const rows = [{ json: { document_id: 'a', received_at: '2026-02-01T10:00:00Z', form_name: 'Form 1099-INT Interest Income', tax_year: '2025', file_name: 'int.pdf', status: 'ready' } }];
  const out = await run('Summarize History', rows, histEntry('maria@example.com'));
  assert.match(out[0].json.response, /2026-02-01: Form 1099-INT Interest Income 2025 \(int\.pdf, status ready\)/);
});
await test('client history: empty table / no email', async () => {
  let out = await run('Summarize History', [{ json: {} }], histEntry('maria@example.com'));
  assert.match(out[0].json.response, /No earlier documents/);
  out = await run('Summarize History', [{ json: {} }], histEntry(''));
  assert.match(out[0].json.response, /No client email/);
});

const ref = async (q) => (await run('Look Up Form Reference', [{ json: { action: 'form_reference', query: q, payload: {} } }]))[0].json.response;
await test('form reference: codes and 1040 lines', async () => {
  assert.match(await ref('W-2 box 12 DD'), /Code DD: Cost of employer-sponsored health coverage/);
  assert.match(await ref('w2 box 12 code W'), /Code W: Employer HSA/);
  assert.match(await ref('1099-R code 7'), /Code 7: Normal distribution/);
  assert.match(await ref('1099-R box 7 G'), /Code G: Direct rollover/);
  assert.match(await ref('K-1 box 20 Z'), /Code 20Z: Section 199A/);
  assert.match(await ref('1098-T'), /Form 8863/);
  assert.match(await ref('1099-INT'), /line 2b/);
  assert.match(await ref('form 1040 schedule C'), /No reference/);
  assert.match(await ref('1099-B wash sale'), /code W/);
  assert.match(await ref('1099-K'), /Schedule C/);
  assert.match(await ref('1095-A'), /Form 8962/);
  assert.doesNotMatch(await ref('W-2 box 12 DD'), /Code W:/, 'form name read as code W');
  assert.doesNotMatch(await ref('1099-R code 7'), /Code R:/, 'form name read as code R');
  assert.doesNotMatch(await ref('K-1 box 20 Z'), /Code 1/, 'form name read as a code');
});

await test('unknown action: explains valid actions', async () => {
  const out = await run('Unknown Action', [{ json: { action: 'delete_everything' } }]);
  assert.match(out[0].json.response, /Unknown action "delete_everything"/);
});

console.log(results.join('\n'));
const failed = results.filter((r) => r.startsWith('FAIL')).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
