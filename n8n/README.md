# Tax Document Intake + Accountant Assistant: setup guide

This guide is for the people who will run the workflow day to day. No programming is needed. Wherever a step involves your server, the guide says so; ask whoever installed the **Tax Document API** for those details.

---

## What it does

```
 Client uploads a file        ─┐                                                       ┌─► Saved to the "tax_document_log" table
 Client emails an attachment  ─┤                    one item     Accountant Assistant  ├─► Email to your reviewer
 File dropped in Google Drive ─┼─► Read the file ─► per form ─►  reviews each form    ─┤   when a person should look
 Another app sends a file     ─┘                                                       └─► Slack / Telegram / WhatsApp alert
                                                                                           when something failed
```

For every file, the workflow:

1. **Finds and reads every form in it.** A file can be one form, a stack of scanned forms, or a brokerage "tax reporting statement" with several forms inside. Each form is identified (W-2, 1099-NEC, 1099-MISC, 1099-INT, 1099-DIV, 1099-B, 1099-R, 1099-G, 1099-K, SSA-1099, 1098, 1098-T, 1098-E, 1095-A, or Schedule K-1) and every filled-in box is read: names, tax IDs, addresses, amounts, checkboxes, codes, state rows, and every sale on a 1099-B. Scans and phone photos work too. Extra copies of the same form (W-2 Copy C, Copy 2) are skipped. Pages that aren't a supported form (an invoice, a letter, a Form 1040) are listed for a person to handle.
2. **Has the Accountant Assistant review it.** The AI assistant writes:
   * a short **summary** (which form, who issued it, the main amounts),
   * **what to check**: anything unusual, missing, or that doesn't add up,
   * **questions for the client**: documents or answers to request,
   * **return preparation notes**: where the main amounts go on Form 1040.
3. **Saves everything** to a table inside n8n called `tax_document_log`, one row per form.
4. **Emails your reviewer** whenever a person should look: the document is a scan, the form type is uncertain, something didn't add up, the assistant spotted an issue, some pages aren't a supported form, or the file couldn't be read at all.
5. **Alerts the team** in Slack, Telegram and WhatsApp when a file couldn't be processed or the workflow itself failed.

**What the assistant cannot do:** email clients, change data, or file anything. It only reads and writes notes. Final decisions stay with your staff.

---

## Before you start: what you need

| You need | Why | Who provides it |
|---|---|---|
| **n8n** (version 2.0 or newer) | Runs the workflow | Your IT person, or an n8n Cloud account |
| The **Tax Document API** running, its **web address**, and an **API key** | Reads the documents | Whoever installed the API (`uv run main.py create-key --name n8n`) |
| **Ollama** with the `qwen3.5:4b` model | The Accountant Assistant's AI | Same person (usually the same computer as the API) |
| An **email account that can send mail** (SMTP) | Sends "needs review" emails | Your email provider (Google Workspace, Microsoft 365, …) |
| *Optional:* an **inbox for clients to send documents to** (IMAP) | The email source | Your email provider |
| *Optional:* a **Google Drive folder** | The Drive source | Google account |
| *Optional:* a **Slack** channel, **Telegram** group and/or **WhatsApp Business** number | Team alerts when something fails | Your Slack admin; a Telegram bot from @BotFather; a Meta WhatsApp Business account |

> **n8n Cloud users:** n8n Cloud runs on the internet and can't reach a computer in your office by `localhost`. Your IT person needs to give the Tax Document API and Ollama secure public web addresses (`https://…`), or you can run n8n on the same computer instead.

---

## Step 1: Import the workflow

1. Open n8n and go to **Overview** (or **Workflows**).
2. Click **Create workflow**. Then click the **⋯** menu at the top right and choose **Import from File…**
3. Choose `tax-document-intake.workflow.json` from this folder.
4. Click **Save**.

The canvas now shows coloured notes explaining each area. Several nodes show a red ⚠ until you connect their credentials in Step 3. That's expected.

## Step 2: Create the log table (one time)

1. Find the node **Set up the log table** (bottom left, in the green note).
2. Hover over the next node, **Create tax_document_log**, and click **▶ Execute step** (or open the node and click **Execute step**).
3. A green check means it worked. You'll find the table under **Overview → Data tables → tax_document_log**.

Running this again is safe. It won't erase anything.

> **Upgrading from an earlier version?** The table now has a `pages` column (which pages of the file a row is about). Running the step again does not add a column to an existing table: open the table and add a text column named `pages`, or delete the table and run the step again.

## Step 3: Connect the credentials

A *credential* is a saved login. Open each node below (double-click it), find the **Credential** box at the top, choose **Create new credential**, fill it in, and save.

| Node | Credential type | What to enter |
|---|---|---|
| **Run Document Pipeline** | Header Auth | **Name:** `X-API-Key` · **Value:** the API key from your IT person (starts with `ak_`). Call the credential *Tax Document API key*. |
| **Ollama Chat Model** | Ollama | **Base URL:** `http://localhost:11434` if Ollama is on the same computer as n8n. If n8n runs in Docker, use `http://host.docker.internal:11434`. Otherwise use the address your IT person gives you. |
| **Email the Reviewer** | SMTP | Your outgoing mail server. **Gmail / Google Workspace:** host `smtp.gmail.com`, port `465`, SSL on, your address as the user, and an **app password** (Google Account → Security → App passwords). **Microsoft 365:** host `smtp.office365.com`, port `587`, SSL off (it uses STARTTLS). |
| **Email Inbox** *(optional)* | IMAP | The inbox clients send documents to. **Gmail:** host `imap.gmail.com`, port `993`, SSL on, app password. Not using email intake? Right-click the node → **Deactivate**. |
| **Document API Webhook** *(optional)* | Header Auth | Only if another program will send files in. Make up a long secret: **Name:** `X-Webhook-Key` · **Value:** the secret. Give both to whoever sets up the other program. Not needed? Right-click → **Deactivate**. |
| **Google Drive Folder** and **Download from Drive** *(optional, off by default)* | Google Drive OAuth2 | Sign in with Google, then pick the folder to watch in **Google Drive Folder**. Right-click both nodes → **Activate**. |
| **Alert Slack** *(optional)* | Slack API | A bot token with the `chat:write` scope. Invite the bot to the channel, and change the channel from `#tax-pipeline-alerts` if yours has another name. |
| **Alert Telegram** *(optional)* | Telegram API | The bot token from @BotFather. Add the bot to your group and enter the group's **Chat ID** in the node (group IDs start with `-100`). |
| **Alert WhatsApp** *(optional)* | WhatsApp API | Your WhatsApp Business account. In the node pick the sender number and enter the recipient's number with country code. WhatsApp only lets a business message people with an approved *template*: create one named `tax_pipeline_alert` (language English US) whose body is just `{{1}}`, and wait for Meta to approve it. |

Not using one of the alert channels? Right-click its node → **Deactivate**. Each channel retries three times, and one failing doesn't stop the others.

> The `Accountant Assistant Review`, `client_history`, and `tax_form_reference` nodes call **this same workflow**. You don't need to pick anything for them.

## Step 4: Fill in your Firm Settings

Open the **Firm Settings** node. At the top is a short block to edit. Change only the text **between the quotes**:

```js
firm_name: 'Smith & Lee CPAs',
api_url: 'http://localhost:8000',         // from your IT person
reviewer_email: 'review@smithlee.com',    // who gets "needs review" emails
sender_email: 'documents@smithlee.com',   // must be allowed to send from your SMTP account
min_confidence: 0.90,                     // leave as is unless told otherwise
```

Click **Save**.

## Step 5: Turn on crash alerts

The alert nodes already fire when a file can't be processed. To also be alerted when the workflow itself breaks: open the workflow's **⋯ menu → Settings**, set **Error workflow** to *this same workflow*, and save. (n8n can't store this in the file, because the workflow only gets its ID when you import it.)

## Step 6: Test it

1. Open the **Client Upload Form** node and click **Test step** (or **Execute workflow** at the bottom of the canvas). A test version of the form opens.
2. Fill in your name and email, and upload a sample tax form. The `tests/samples` folder of the API project has fake, filled-in examples such as `w2.pdf` and `1099_nec_scan.png`.
3. Watch the nodes turn green. Reading a document usually takes 20–40 seconds per form.
4. Check **Overview → Data tables → tax_document_log**. A new row appears with the form type, summary, issues, and notes.
5. If the row says `needs_review` or `failed`, the reviewer should get an email. A `failed` row also sends the team alerts.
6. Try a multi-form file: `stack.pdf` (four pages: three forms and an invoice) gives three form rows plus one `needs_review` row for the invoice page; `consolidated_1099.pdf` gives a 1099-DIV, a 1099-INT and a 1099-B with all 39 sales.

If something turns red, see [Troubleshooting](#troubleshooting).

## Step 7: Turn it on

Click the **Active** / **Publish** switch at the top right of the workflow. From now on:

* **Upload form:** open **Client Upload Form**, switch to **Production URL**, and copy the link. Put it on your website, in your engagement letter, or in emails to clients. Clients can upload a whole stack as one PDF.
* **Email:** clients send documents to the inbox you connected. The workflow checks for new (unread) mail about every minute and marks it read when done.
* **Google Drive:** drop files into the watched folder.
* **Other software:** sends files to the **Document API Webhook** production URL (see [For IT](#for-it)).

---

## Using it day to day

**The log table.** Open **Overview → Data tables → tax_document_log**. Each row is one form (a file with three forms gives three rows):

| Column | Meaning |
|---|---|
| `status` | `ready`: routine, nothing flagged · `needs_review`: a person should look (you were emailed) · `failed`: the file couldn't be read (you were emailed, and the team was alerted) |
| `form_name`, `tax_year` | What the form is. `document_type` is `unsupported` for pages that aren't a supported form |
| `client_name`, `client_email`, `source` | Who sent it and how |
| `file_name`, `pages` | Which file, and which of its pages this row is about |
| `summary` | The assistant's plain-English description |
| `issues` | What to check, one per line |
| `client_follow_ups` | Questions or documents to request from the client |
| `return_notes` | Where the amounts go on the return |
| `extracted_fields` | Every value read from the form (tax IDs masked) |
| `confidence` | How sure the system is about the form type (1.0 = certain) |
| `document_id` | Reference number, also shown in emails. Forms from the same file share it, with `-1`, `-2`, … added |

**Review emails** have the subject `[Your Firm] A document needs review: <file> (<client>)` or `[Your Firm] Could not process a document: …`. They contain the summary, what to check, client questions, and return notes.

**Why was a document flagged?** The first lines of `issues` are the automatic checks. A document is always flagged when:

* it was a scan or photo (names and numbers were read by OCR, so check them against the image),
* the system was less than 90% sure of the form type,
* an amount couldn't be read, or a value didn't appear in the document,
* the numbers don't add up (for example, W-2 Social Security tax isn't 6.2% of Social Security wages),
* the tax year on the form differs from the year the client typed in,
* it is one of several forms in the same file (so you can check the pages were split correctly),
* the Accountant Assistant found something worth checking, or the assistant itself wasn't available.

The assistant can add flags but can't remove the automatic ones.

---

## Privacy and security

* **Tax IDs (SSNs/EINs) are masked** to the last 4 digits (`XXX-XX-1234`) before they reach the AI assistant, the log table, any email, or any chat alert. Alerts carry the file name, client name and the problem, never amounts. The full numbers are only in the original document.
* **Documents stay on your systems.** The Tax Document API and the AI models run on your own computer or server, and nothing is sent to an outside AI company.
* **Execution history:** this workflow is set so n8n does **not** save the details of successful runs (they would contain tax data). Failed runs are kept so problems can be diagnosed. You can delete old executions under **Executions**.
* The **upload form** is public by design (clients need to reach it). If you need it password-protected, open the node and set **Authentication → Basic Auth**.
* The **Document API Webhook** requires the secret header from Step 3.
* The Accountant Assistant treats document contents as data. Text hidden inside a document can't make it email anyone or change anything, because it has no tools that can.

---

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Red ⚠ on a node after import | Credential not connected | Step 3 |
| `failed` row: *"could not reach the Tax Document API"* | The API isn't running, or `api_url` is wrong | Check `api_url` in **Firm Settings**; ask IT to start the API (`uv run main.py serve`) |
| `failed` row: *"could not log in"* | Wrong API key | Re-enter the key in the **Tax Document API key** credential |
| `failed` row: *"AI models … are not running"* | Ollama is stopped on the API server | Start Ollama, then re-send the file |
| `needs_review` row: *"Page 3 of this file is not one of the supported tax forms"* | e.g. a Form 1040, an invoice, an instructions page | Handle that page by hand. The supported forms are listed under [What it does](#what-it-does) |
| `failed` row: *"file type cannot be read"* | Word, Excel, ZIP, HEIC… | Ask for a PDF or a photo (JPG/PNG) |
| `failed` row: *"too large"* / *"too many pages"* | Over 20 MB, or over 100 pages | Ask for the file in smaller parts |
| No team alerts | Credential, channel or template | Open the alert node → **Execute step** to see the error. Slack: invite the bot to the channel. Telegram: the bot must be in the group. WhatsApp: the template must be approved. Crash alerts also need Step 5 |
| Summary says *"The AI review was not available"* | The assistant timed out or Ollama is unreachable from n8n | Check the **Ollama** credential's Base URL; the document is still saved and flagged |
| No emails arrive | SMTP credential or sender address | Open **Email the Reviewer** → **Execute step** to see the error. Many providers require `sender_email` to match the login |
| Email intake does nothing | IMAP credential, or the emails were already read | The workflow only picks up **unread** emails |
| Upload form says the workflow is not active | Test URL vs Production URL | Use the **Production URL** after activating (Step 7) |
| Each document takes over a minute | Not enough graphics memory on the API server, so models are swapped in and out | Ask IT. See *Deployment notes* in the API README |

To see exactly what happened in a run, open **Executions** (left sidebar) and click the run. Failed runs are kept there.

---

## Changing the AI model

The Accountant Assistant uses `qwen3.5:4b` by default, the same model the Tax Document API already uses, so no extra graphics memory is needed. For better-written notes on a stronger machine, open **Ollama Chat Model** and pick a larger model (e.g. `qwen3.5:9b`). Your IT person may need to download it first (`ollama pull qwen3.5:9b`).

You can also connect a cloud AI model instead (OpenAI, Anthropic, …) by replacing the **Ollama Chat Model** node. Note that the document details (with masked tax IDs) would then be sent to that company.

---

## For IT

* **Requirements:** n8n ≥ 2.0 (Data tables, AI Agent v2, Call n8n Workflow Tool v2). The Tax Document API reachable from n8n. Ollama reachable from n8n for the agent.
* **One file, internal sub-workflows:** the workflow calls itself through `Execute Sub-workflow` / `Call n8n Workflow Tool` with `{{ $workflow.id }}`. The single `Sub-workflow Entry` trigger declares `{action, query, payload}` and **Route Action** dispatches to `review_document` (AI agent), `client_history` (Data table read), and `form_reference` (static IRS code reference). `client_history` is bound to the current document's client email in the tool configuration, not chosen by the model.
* **Webhook intake:** `POST <n8n>/webhook/tax-documents` with header `X-Webhook-Key: <secret>`, `multipart/form-data`, any number of file fields plus optional `client_name`, `client_email`, `tax_year`. Responds immediately with `{"status":"received"}`; results land in the log table.

  ```bash
  curl -H "X-Webhook-Key: $SECRET" -F "file=@w2.pdf" -F "client_email=client@example.com" https://n8n.example.com/webhook/tax-documents
  ```
* **API route:** each file goes to `POST /v1/documents/extract/bulk` (one file per call). **Split Forms** turns the answer into one item per form (`kind: form`) or per problem (`kind: problem`: unsupported pages, unreadable file); **Extracted?** routes them to the review or to **Explain Failure**. Items keep their link to the original file through `pairedItem`.
* **Timeouts:** the API call allows 5 minutes per file. Files from one submission are processed one after another. A very long brokerage statement may need a longer timeout in **Run Document Pipeline**.
* **Rate limits:** the bulk route defaults to 10 calls per minute per key. Large email backlogs may hit `429` and log `failed` rows with a "re-send in a minute" reason.
* **Alerts:** **Write Team Alert** formats one message three ways (Slack mrkdwn, Telegram HTML, one-line WhatsApp template text) for `failed` records and for the **Pipeline Crashed** error trigger; `needs_review` rows only email the reviewer.
* **Long 1099-B statements:** the assistant gets the first 10 sales plus totals per Form 8949 box; the log keeps every sale.
* **Testing changes to the Code nodes:** `node n8n/tests/code-nodes.test.mjs n8n/tax-document-intake.workflow.json n8n/tests/api-fixtures.json` runs every Code node against recorded API responses: splitting forms, TIN masking, review flags, failure messages, alerts, email escaping, and the IRS code reference. Re-export the workflow from n8n and re-run it after editing.
