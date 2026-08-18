# Product Requirements Document
## Autonomous Pharmacy Bill Intake, Verification & Dispute Agent

**Author:** [Your Name]
**Track:** Taskmaster — All Things Agentic Hackathon
**Status:** v3 (validated against real vendor samples)
**Last updated:** 18 August 2026

---

## 1. Overview

An autonomous agent that monitors a pharmacy's email inbox for vendor bills arriving in mixed and often broken formats (two CSV dialects, PDF tax invoices, and a proprietary binary masquerading as `.xls`), decides how to read each one, investigates anything that looks wrong, **disputes confirmed overcharges with the vendor on its own**, and stages verified files into Google Drive where the pharmacist's existing billing software can consume them.

Unlike a fixed pipeline, the agent is a **tool-calling loop**: it is given a goal and a toolbox and chooses each step from what it has learned so far. Different bills take visibly different paths, and the trace of those paths is a first-class output of the system.

When the agent genuinely cannot decide, it does not block. It serializes its reasoning state, parks the bill, asks the pharmacist one specific question over WhatsApp, and terminates. The reply — minutes or days later — rehydrates the loop and it continues from exactly where it stopped.

---

## 2. Problem Statement

This is a real chore in a real pharmacy. The owner (the author's father, 65) is regularly late home because of it.

The pharmacy's billing software is a legacy Windows desktop application with no API. It already does its job well: once a vendor's file is uploaded, it parses the bill, populates the fields, and updates its own inventory. It is the system of record for stock, and it will remain so.

What is manual, slow, and error-prone is everything *before* that upload (~20 min/day, every day):

1. Checking each vendor's email for today's bill
2. Working out which attachment is the real one, and whether it will even open
3. Eyeballing line items for price hikes, quantity mismatches, and duplicate invoices
4. Downloading and dragging the file into the software
5. Chasing the vendor manually whenever a file is corrupt or missing

Step 3 is where money is lost. A tired scan of a 76-column bill at 9pm does not catch a ₹22 rate increase on one line of forty. Those increases compound silently across months and vendors, and nobody ever disputes them — not because the pharmacist doesn't care, but because reconstructing the evidence takes longer than the amount at stake.

---

## 3. Goals & Non-Goals

### Goals

- Automate everything between "email arrives" and "verified file is sitting in the right Drive folder"
- Investigate anomalies by forming and testing hypotheses, not by applying fixed thresholds
- **Resolve** confirmed overcharges autonomously by disputing them with the vendor, rather than escalating them to the pharmacist
- Recover from unreadable files without human intervention where possible; escalate to the vendor only when not
- Survive long asynchronous waits — a bill parked for a human answer must resume correctly days later
- Make the agent's reasoning chain visible and auditable, to the pharmacist via WhatsApp and to a judge via a hosted status page (§7.11) without requiring GCP project access
- Meet all hackathon technical requirements (Gemini 3.5 Flash, Google ADK, Google Cloud infrastructure)

### Non-Goals

- **Programmatic integration with the legacy billing software.** The agent hands off a file in the exact format the software already accepts. It does not drive, patch, or replace it.
- Automating the legacy software's UI (Windows desktop automation — brittle, untestable without physical access, a live-demo liability)
- **Owning inventory truth.** The billing software owns stock levels because it sees customer sales; the agent never does. See §5.
- Converting vendor files to a standard format (see §12)
- Point-of-sale, customer checkout, payment processing
- Multi-pharmacy / multi-tenant support

---

## 4. Target User

A pharmacy owner/operator who handles vendor billing personally, is comfortable with WhatsApp and with familiar file folders, and has no interest in learning a new dashboard. Every interaction surface in this system was chosen because he already uses it.

---

## 5. Scope Decision: Purchase Ledger, Not Inventory

**Firestore stores what was purchased. It does not store what is in stock.**

This is deliberate and load-bearing. The legacy software decrements stock on every customer sale, and the agent has no visibility into sales. A parallel Firestore "inventory" would only ever count goods *in*, drifting monotonically upward and becoming confidently wrong within weeks.

The purchase ledger — what was billed, by which vendor, at what rate, in which batch, on what date — is something the agent observes completely and can therefore be correct about indefinitely. It is also *exactly* what the validation logic needs: price history, quantity patterns, duplicate detection, and vendor cadence are all purchase-side questions.

**Consequences of this decision, applied throughout:**
- No stock-level queries. The agent cannot answer "how much Paracetamol do I have?" truthfully, so it does not offer to.
- No reorder-threshold alerts (they require stock levels).
- Vendor-silence detection is retained — it runs on billing cadence, which the agent owns.

---

## 6. User Stories

- As a pharmacist, I want bills downloaded, read, and verified automatically so I don't have to check each email myself.
- As a pharmacist, I want the verified file waiting in the right folder so I can upload it without hunting for it.
- As a pharmacist, I want a WhatsApp message confirming a bill was processed, needing no action from me.
- **As a pharmacist, I want overcharges disputed with the vendor automatically, with the evidence attached, because I will never write that email myself.**
- As a pharmacist, I want to be asked a *specific* question when the agent is genuinely unsure — not a generic approve/reject prompt.
- As a pharmacist, I want unreadable files chased with the vendor automatically.
- As a pharmacist, I want to answer a question from three days ago and have the agent pick up exactly where it left off.

---

## 7. Agent Design

### 7.1 Operating Model

The agent runs as a reasoning loop (Gemini 3.5 Flash via Vertex AI, orchestrated with Google ADK). Each turn it inspects state, selects a tool, observes the result, and decides what to do next.

A run terminates in exactly one of three terminal states:

| Terminal state | Meaning |
|---|---|
| `resolved` | Bill verified and staged; inventory-side handoff complete. May include an auto-sent dispute. |
| `pending_pharmacist` | Agent is genuinely unsure; state serialized, question sent, loop suspended. |
| `pending_vendor` | Agent cannot read the file by any route; resend requested, bill parked visibly. |

No bill is ever silently dropped, and no run blocks on a human.

```
   Gmail watch → Pub/Sub push
              │
              ▼
   ┌──────────────────────────┐
   │   AGENT LOOP  (Cloud Run) │◄────── tool result ──────┐
   │   Gemini reasons per turn │                          │
   └────────┬─────────────────┘                          │
            │ selects a tool                              │
            ▼                                             │
   ┌──────────────────────────┐                          │
   │        TOOLBOX            │──────────────────────────┘
   └────────┬─────────────────┘
            │
            ▼
   ┌──────────────────────────┐
   │  terminal state reached   │
   └────┬──────────┬──────────┘
        │          │
    resolved   pending_*  ── state serialized to Firestore
                              │
                              ▼
                   WhatsApp reply / vendor reply
                              │
                              ▼
                   ┌──────────────────────┐
                   │  RESUME ENTRYPOINT    │  (separate Cloud Run service)
                   │  rehydrate → continue │
                   └──────────────────────┘
```

### 7.2 Toolbox

| Tool | Purpose |
|---|---|
| `detect_format` | Inspect actual file contents, not the extension |
| `parse_csv` | Structured parse (pandas), handles both CSV dialects |
| `parse_xls` | Excel parse; fails deliberately and informatively on the proprietary binary |
| `parse_pdf_vision` | Gemini multimodal read of a PDF tax invoice |
| `find_related_document` | Locate the same invoice number arriving in a different format/email |
| `lookup_vendor_history` | This vendor's prior bills and pricing, from the purchase ledger |
| `check_duplicate` | Invoice number + vendor already processed? If seen with different content, treat as a reconciliation case (§7.3), not a simple duplicate |
| `cross_check_other_vendors` | Same molecule priced by other vendors — market shift vs. vendor error |
| `record_purchase` | Write the normalized bill to the Firestore purchase ledger |
| `stage_file` | Copy the original file, byte-for-byte, to the vendor's Drive folder |
| `email_vendor` | Two modes: **resend request** (unreadable file) or **dispute** (confirmed overcharge, with evidence) |
| `ask_pharmacist` | Serialize state, send one targeted WhatsApp question, suspend the run |
| `notify_pharmacist` | Outbound-only WhatsApp: success digest, or notice of an action already taken |

### 7.3 Format Handling & Recovery

Formats confirmed from real vendor samples (13 files, 7 invoices, 3 identifiable vendors — see §14):

- **Format A** — ~79-column pharma-distribution ERP export (`itemname`, `batchno`, `expdate`, `invqty`, `salerate`, `itemmrp`, `cgstper`, `sgstper`, `hsnsaccode`, …), seen as **both** plain CSV and a legacy Excel container (Format C below) — same schema, same source software, two different file containers.
- **Format B** — CSV with H/D/F row-type structure (`Type, Code, Name, Packing, Quantity, Selling Rate, MRP, Batch No., Exp. Date, Discount, VAT %, VAT Amt, TS %, TS Amt, Cess, Amount, HSN, …`). Carries per-line discount and per-line tax like Format A, but under this vendor's own **VAT/TS** naming rather than CGST/SGST — confirms tax fields must be mapped per vendor, not assumed to share one vocabulary.
- **Format C** — `.xls` extension, but genuinely old Excel: BIFF2 (Excel 2.0-era binary, c.1987), **not corrupt and not proprietary.** Confirmed against all 5 sample `.xls` files: `pandas.read_excel()` fails with no engine specified, and the natural next guess — `engine='openpyxl'` — fails too (`BadZipFile`, because openpyxl only reads the modern zip-based `.xlsx` container). `engine='xlrd'` parses all 5/5 cleanly on the first try. The failure is a **misrouted parser choice, not an unreadable file.**
- **Format D** — PDF tax invoice. Legally the vendor's authoritative GST document — treated as source of truth when it conflicts with a CSV/XLS export of the same invoice (see the reconciliation case below).

**Revised recovery ladder** — reflects what the samples actually require, not a hypothetical worst case:

1. `detect_format` inspects file bytes (not the extension) and identifies the true container: CSV/delimited text, BIFF-signature binary, or PDF.
2. BIFF-signature files route straight to `parse_xls` using an xlrd-based reader — this succeeds directly in the large majority of real cases; no recovery needed.
3. If a file still fails to parse by any known route (e.g. a container `detect_format` can't identify, or a genuinely damaged file — not demonstrated in the current sample set), `find_related_document` checks whether the same invoice number arrived in another format, and `parse_pdf_vision` reads it if so.
4. Only if every route fails: `email_vendor` in resend mode, park as `pending_vendor`, notify the pharmacist.

**Separately — a reconciliation check, not a parse-failure recovery:** when the *same invoice number* arrives more than once with **conflicting content** (different line items or totals, not just a re-send of the identical file), the agent treats the PDF as authoritative, stages the version that matches it, and surfaces the discrepancy to the pharmacist rather than silently picking one. This is a distinct, confirmed-real scenario — see §11 Bill 2 — and is a different code path from "the file wouldn't open."

Measured from the sample set: **4 of 5 `.xls` files have a PDF twin; 1 of 5 does not** — but since Format C parses directly via the correct engine, the missing twin didn't block anything. See §14.

### 7.4 Validation & Investigation

The agent forms a hypothesis about what looks wrong and chooses the lookups that confirm or rule it out. It does not run a fixed checklist.

**Available with no history (works from day one):**
- Internal arithmetic — **per line**, not just at the invoice footer: `taxable_value = (quantity × rate) − discount`, then `line_total = taxable_value + tax_component_1_amount + tax_component_2_amount`; line totals sum to the invoice total (± rounding). Checking only `quantity × rate = total` is wrong for real pharma bills — every sample format carries per-line discount and a two-component tax split (CGST/SGST or, for at least one vendor, VAT/TS — see §7.3) — and a naive check would misfire on every clean invoice. Verified against real samples: formula holds exactly (e.g. `148.57 taxable + 3.71 CGST + 3.71 SGST = 155.99 line amount`).
- Rate vs MRP plausibility — flag implausible margins in either direction
- Duplicate invoice number + vendor — and where the *same* invoice number recurs with **different content**, not just a repeat send, reconcile against the vendor's PDF rather than flagging a generic duplicate (§7.3)

**Requiring accumulated or seeded history:**
- Price deviation against this vendor's prior rate for the same item
- Quantity pattern deviation against typical order size
- Vendor silence — expected bill not received within usual cadence

**Worked investigation — market shift (auto-approve):**
Rate on `AMLODIPINE 5MG` is 27% above last month → `lookup_vendor_history` confirms the rise is real but gives no cause → `cross_check_other_vendors` shows two other suppliers raised the same molecule in the same window → agent concludes market-wide movement, records the purchase, stages the file, and notes the reasoning in the success digest. No human touched it.

**Worked investigation — vendor error (auto-dispute):**
Rate on one line is 26% above this vendor's own last four invoices → `cross_check_other_vendors` shows no other supplier moved on that molecule → arithmetic elsewhere on the bill is clean, so this is not a systemic file issue → agent concludes a vendor-side pricing error worth ₹4,200, sends a dispute email citing invoice numbers, dates, and prior rates, CCs the pharmacist, stages the file, and sends a WhatsApp notice of the action taken. **No human touched it.**

### 7.5 Autonomous Dispute — Rules of Engagement

The dispute path is the agent's highest-value action and its highest-risk one. It fires only when **all** of the following hold:

1. The deviation is confirmed against ≥3 prior invoices from the same vendor for the same item
2. `cross_check_other_vendors` returns a conclusive "no market movement" signal
3. The disputed amount exceeds a configured value floor (default ₹500) — small deviations are logged, not disputed
4. No dispute has already been sent for this invoice number

The email is factual and non-accusatory, cites its evidence, requests clarification rather than demanding a credit note, and always CCs the pharmacist. If any of conditions 1–3 fail, the agent falls back to `ask_pharmacist` with its findings attached.

**Deployment-time control:** a `DISPUTE_REQUIRES_APPROVAL` flag gates whether a passing dispute sends immediately or is first surfaced to the pharmacist as a one-tap WhatsApp confirmation. **Default: `false`.** The hackathon build runs with it off, so the demo shows the agent resolving the overcharge unsupervised — the flag exists to demonstrate that graduated trust was a deliberate design consideration, not an oversight, and a real rollout would start with it on for the first few weeks before flipping it off.

### 7.6 Durable Pause & Resume

**`ask_pharmacist` never blocks.** When invoked it:

1. Serializes the run state to Firestore: `bill_id`, normalized line items, findings so far, the full tool-call history, and the open question
2. Sets `status: pending_pharmacist` and stores a correlation key
3. Sends the WhatsApp question
4. **Terminates the Cloud Run request**

Inbound WhatsApp messages hit a **separate Cloud Run entrypoint**. That service matches the reply to a parked bill via the correlation key, rehydrates the serialized state into a fresh agent run, appends the pharmacist's answer as a tool result, and the loop continues from precisely the turn it stopped on.

The same mechanism handles `pending_vendor` — a vendor's resend email resumes the parked run.

This is what makes the agent long-running rather than request-scoped: a bill parked on Tuesday resolves correctly on Friday, on a different container, with full context.

### 7.7 Memory & Adaptation

Every human resolution is written back to Firestore and injected into the validation context of future runs. If the pharmacist approves a price rise for a vendor, the agent stops flagging it. If he rejects one, that pattern is weighted more heavily next time. Resolution history is queryable per vendor and per item.

### 7.8 Proactive Behaviour

On a Cloud Scheduler cadence, independent of any incoming email, the agent checks for vendors that have gone unusually quiet relative to their established billing rhythm, and flags them. This is the one proactive check that survives the purchase-ledger scope decision (§5), because cadence is purchase-side data.

### 7.9 Staging to Google Drive

Verified files are copied **unmodified** into a per-vendor Drive folder. With Drive Desktop syncing to the pharmacist's machine, the file is simply present locally when he goes to upload — no dashboard, no download step.

The original is never altered. The billing software expects the vendor's native format, so byte-for-byte preservation is a hard requirement.

### 7.10 Guardrails

- `email_vendor` fires at most once per invoice per mode
- All outbound vendor mail CCs the pharmacist — nothing leaves silently
- Dispute mode is gated on §7.5 conditions 1–4
- Turn cap per bill; on exhaustion, park and notify rather than loop
- No bill is silently dropped; every unresolved bill sits in a visible pending state
- Original vendor files are immutable
- **Untrusted content is never treated as instructions.** Every bill the agent reads is content from a vendor it does not control. Recipient addresses for `email_vendor` and `notify_pharmacist`/`ask_pharmacist` are always drawn from a trusted vendor/pharmacist directory (Firestore config), **never** parsed out of the document or email body being processed — so a malformed or adversarial attachment cannot redirect where the agent sends anything, including a dispute email. Extracted line items, prices, and free text are validation *inputs*, not tool-call directives.
- **Multi-step resolution is idempotent.** If `stage_file` succeeds and a later step in the same run fails (e.g. `record_purchase`), re-running the bill does not re-copy the file or double-write the ledger — each write is keyed on `invoice_no` + `vendor`, so retries and resumed runs (§7.6) converge rather than duplicate.

### 7.11 Judge-Facing Status Page

A minimal, read-only web view on Cloud Run, separate from the pharmacist's WhatsApp/Drive workflow (§12 — the "no dashboard" decision was about *his* interface, not this one). It lists incoming bills with vendor, timestamp, and current status (`resolved` / `pending_pharmacist` / `pending_vendor`), and links each to its Cloud Trace reasoning chain (§8).

This exists specifically because Cloud Trace and Cloud Logging require GCP IAM access a judge won't have — without it, "visible proof" only exists inside the video. A hosted URL judges can open themselves is "highly encouraged" in the submission requirements and gives them something independently checkable rather than trust-the-recording. No auth data or file contents are exposed — status and metadata only.

---

## 8. Observability

The agent's thesis is that different bills take visibly different paths. That divergence is worthless if it is invisible, so tracing is a product requirement, not instrumentation.

- ADK trace output is exported to **Cloud Trace** and **Cloud Logging**, giving an OpenTelemetry-compliant record of every run
- Each bill's full reasoning chain — tools called, arguments, results, and the model's decision at each turn — is retrievable and rendered per bill
- Traces are a **demo asset**: a clean bill's four-call chain shown beside a corrupted bill's nine-call chain with two failures and a recovery is the most direct available proof that this is an agent and not a pipeline
- The same view satisfies the hackathon's requirement for visible proof the backend runs on Google Cloud

---

## 9. Technical Stack

| Component | Technology |
|---|---|
| LLM / reasoning | Gemini 3.5 Flash (Vertex AI) |
| Agent orchestration | Google ADK |
| Compute | Cloud Run (agent loop + resume entrypoint) |
| Event ingestion | Gmail API `watch` → Pub/Sub push |
| Purchase ledger & agent state | Firestore |
| File staging | Google Drive API |
| Email (read + send) | Gmail API |
| Messaging | WhatsApp via Twilio sandbox |
| Parsing | pandas; Gemini multimodal for PDF |
| Scheduling | Cloud Scheduler (vendor-silence checks) |
| Tracing | Cloud Trace / Cloud Logging (OpenTelemetry via ADK) |
| Status page | Cloud Run (read-only, §7.11) |
| Secrets | Secret Manager |

**Credential security.** Gmail OAuth refresh tokens, the Twilio auth token, and the Vertex AI service account key are held in **Secret Manager**, not environment variables or source — nothing sensitive is ever committed to the repo. The Cloud Run service account is scoped to only the APIs it actually calls (Gmail, Drive, Vertex AI, Firestore, Pub/Sub) rather than a broad project-editor role.

**Gmail authentication.** `gmail.readonly` and `gmail.send` are restricted scopes. In OAuth *Testing* publishing status, refresh tokens are invalidated after 7 days — long enough to build and short enough to die before the demo. The OAuth app is therefore pushed to **"In production"** status (unverified is fine for a single owned account; the interstitial is clicked through once), with a belt-and-braces re-authorization on recording day.

Ingestion uses Gmail `watch` + Pub/Sub push rather than polling: genuinely event-driven, a second Google Cloud infrastructure service, and a materially better story than a cron poll.

**WhatsApp channel.** Twilio sandbox over the Meta Cloud API — running in ~30 minutes versus a day of Business verification and template approval, and the hackathon is not judging the messaging integration. The sandbox's known limitations (join code, 72-hour inactivity expiry) are tracked as a recording-day checklist item (§15), not an architectural concern; the fallback if it degrades is plain email notification, which costs nothing structurally since `notify_pharmacist` is already an isolated tool.

---

## 10. Data

**Normalized internal schema:**
`vendor, invoice_no, invoice_date, item_name, batch_no, expiry_date, quantity, rate, discount, taxable_value, tax_component_1_label, tax_component_1_rate, tax_component_1_amount, tax_component_2_label, tax_component_2_rate, tax_component_2_amount, mrp, line_total, hsn_code`

Confirmed from real samples: every vendor format carries per-line discount and a two-component tax split, but the components are **not uniformly named** — Format A/C use CGST/SGST, Format B uses VAT/TS. The schema stores the label alongside the rate and amount rather than hardcoding `cgst_*`/`sgst_*` field names, so the normalizer maps each vendor's own columns onto two generic tax-component slots instead of assuming GST vocabulary. `invoice_no` also needs a per-format assembly rule — Format A/C split it across separate `pfx` and `invno` columns rather than storing it as one field.

**Firestore — `bills`:**
`bill_id, vendor, invoice_number, invoice_date, line_items[], total_amount, status (resolved | pending_pharmacist | pending_vendor), findings[], resolution_history[], dispute_sent, drive_file_url, trace_id`

**Firestore — `purchase_ledger`:**
`item_name, normalized_item_key, vendor, rate, mrp, quantity, batch_number, expiry_date, invoice_no, purchase_date`

**Firestore — `agent_runs`:**
`bill_id, correlation_key, serialized_state, tool_call_history[], open_question, paused_at, resumed_at`

**Sanitization.** Before any file is committed or appears on video, a scrubber replaces vendor names, GSTINs, drug licence numbers, phone numbers, and addresses with synthetic values. It **must** preserve invoice-number shape and every price, quantity, batch, and date exactly — the validation logic and the anomaly demo are only real if the numbers are.

**History seeding.** Cold-start is handled by history-free checks (§7.4). For the demo, the purchase ledger is seeded from the real sample invoices; any synthesized history beyond what the samples contain is labelled as seeded in the write-up rather than presented as observed.

---

## 11. Demo Plan

Four bills through one agent, with the trace panel visible throughout. Target 4:00.

**Recorded as a single continuous take.** The judging criteria explicitly asks for "a live, unedited demo." Pre-existing state (Bill 4's parked bill, seeded history for Bill 2) is legitimate setup done *before* recording starts — same as seeding a database before a demo — but the video itself is one take with no cuts between beats. The timestamps below are a rehearsal guide, not an editing plan.

| Time | Beat |
|---|---|
| 0:00–0:30 | **The problem.** A 65-year-old pharmacist is late home every night because of a 20-minute chore. Real bills, real formats, real money leaking. |
| 0:30–0:50 | **Architecture.** One diagram: Gmail watch → Pub/Sub → Cloud Run agent loop → toolbox → Firestore/Drive/WhatsApp, with the resume entrypoint shown. |
| 0:50–1:25 | **Bill 1 — clean CSV.** Detect, parse, dedupe, history check, record, stage, notify. Four tool calls, no human. Trace panel shows the short chain. |
| 1:25–2:25 | **Bill 2 — the overcharge (dispute beat).** Rate deviation → vendor history → cross-vendor check → no market movement → agent drafts and **sends the dispute email**, CCs the pharmacist, stages the file, WhatsApp notice of action taken. Show the sent email. No human involved at any point. *(Runs against seeded history — labelled as such in the write-up; see §10.)* |
| 2:25–3:05 | **Bill 3 — the conflicting invoice (real-data hero beat).** The same invoice number arrives twice from a vendor with different totals — one version has an extra line item the other doesn't. The agent cross-references the vendor's own PDF tax invoice, confirms it's the authoritative 13-item, ₹2,959 version (not the 14-item, ₹3,268 one), stages the correct file, and flags the discrepancy to the pharmacist. **This is genuine, unmodified sample data — not staged for the video.** |
| 3:05–3:25 | **Bill 4 — resume from parked.** A bill suspended earlier: pharmacist replies on WhatsApp now, the resume entrypoint rehydrates the serialized state, and the loop continues from the turn it stopped on. Firestore status flips `pending_pharmacist → resolved` on camera. |
| 3:25–3:50 | **Proof on Google Cloud.** Cloud Run dashboard, Vertex AI logs, Cloud Trace showing the reasoning chains just demonstrated. |
| 3:50–4:00 | Close: 20 minutes a day, and one disputed invoice that would otherwise have been paid. |

**De-risking:** Bill 4 is the only beat depending on a live human reply, and its parked state exists in Firestore *before* recording — the reply is the pharmacist's own phone, one message, ~15 seconds. Bill 3 depends only on files already in hand and a comparison against arithmetic and the vendor's own PDF — no live parsing risk, no format-detection gamble.

---

## 12. Design Decisions

**Why a purchase ledger and not an inventory ledger?** The legacy software sees customer sales; the agent does not. Any stock figure the agent maintained would drift upward forever and be confidently wrong within weeks. Purchases are fully observable, so the agent can be permanently correct about them — and price history, quantity patterns, duplicates, and vendor cadence are all purchase-side questions anyway. The feature this costs us (WhatsApp stock queries) was one we could not have answered honestly.

**Why does the agent dispute vendors itself rather than ask first?** Because asking first is what already happens, and it is why nothing ever gets disputed. Reconstructing evidence for a ₹4,200 discrepancy takes longer than the amount justifies, so the pharmacist absorbs it. An agent that assembles the evidence and sends the email removes the friction that causes the loss. The §7.5 conditions keep it from being reckless; the pharmacist is on every CC.

**Why not convert CSV/XLS to a standard PDF?** The staged file exists to be uploaded into the legacy software, which expects the vendor's native format. A PDF cannot be uploaded there, so conversion would destroy the file's only purpose. Human-readable uniformity is served by the normalized Firestore records instead, at no cost and with no second copy to drift.

**Why Drive rather than a dashboard?** Drive Desktop puts the file directly on the pharmacist's machine in a familiar folder. It removes both the dashboard-learning step and the download step.

**Why not automate the upload itself?** No API, legacy Windows desktop app. UI automation would be brittle, untestable without physical access to that machine, and a live-demo liability.

**Why terminate and resume rather than wait?** A request-scoped agent holding a Cloud Run instance open for six hours awaiting a WhatsApp reply is expensive, fragile, and dies on any instance recycle. Serializing state and resuming on a webhook makes the agent survive across days and containers — which is what "runs in the background while you do something else" actually requires.

---

## 13. Success Metrics

Measured over the full sample set and reported with denominators.

| Metric | Definition |
|---|---|
| Straight-through rate | % of *N* bills across *M* vendors resolved with zero human action |
| Recovery rate | Successful autonomous recovery on *k* deliberately corrupted files, without escalation |
| Anomaly recall & precision | Against an injected-error set of known size, with per-flag plain-language reasoning |
| Dispute correctness | Disputes sent / disputes warranted; zero false disputes is the bar |
| Resume correctness | Parked bills resuming with full context after ≥24h |
| Median end-to-end latency | Email received → file staged in Drive |
| **Cost per bill (₹)** | Vertex AI + Cloud Run + Firestore, per processed bill |

*N*, *M*, and *k* to be fixed once the sample set is measured (§14).

---

## 14. Open Questions

1. **Daily bill volume and vendor count** — the sample set (13 files) gives 7 invoices across 3 identifiable vendors (Getwell Medicare, Bruklyn Associates, Sterling Pharma) plus one unlabelled export, which is enough to validate parsing and reconciliation logic but not to size real daily throughput. *(Still requires the pharmacist.)*
2. ~~**Format C ↔ Format D pairing reliability**~~ **Resolved (revised finding):** measured across the sample set — 4 of 5 `.xls` files have a PDF twin, 1 does not. But this turned out not to matter: all 5/5 `.xls` files parse directly and correctly via an xlrd-based reader (`file` identifies them as genuine BIFF2 Excel, not a proprietary or corrupt format). The PDF-fallback path in §7.3 is retained as a defensive last resort for a genuinely unreadable file, but it is not the primary recovery mechanism the original PRD assumed — see §7.3 for the full correction.
3. ~~**Invoices per vendor and date range**~~ **Resolved:** checked every item name across all 5 sample `.xls` invoices — **zero items repeat across any two invoices.** No vendor has two real data points on the same drug in the current sample set. Price-deviation detection has no real signal today and must run entirely on synthesized, clearly-labelled history for the demo (§10).
4. ~~**Is Drive Desktop actually installed on the pharmacist's machine?**~~ **Resolved:** Drive Desktop can be installed on the pharmacy machine. The §7.9 handoff stands as designed. Setup is a one-time step and belongs in the README's spin-up instructions.
5. ~~**Twilio sandbox vs Meta WhatsApp Cloud API**~~ **Resolved:** Twilio sandbox (§9) — fastest path to a working channel within the 2-week window; messaging vendor is not a judged component.
6. ~~**Dispute approval mode**~~ **Resolved:** `DISPUTE_REQUIRES_APPROVAL` config flag, default `false` (§7.5) — off for the hackathon demo to preserve the autonomous-resolution beat, on by default for any real deployment.

All open questions are now resolved except #1, which requires the pharmacist's own numbers rather than analysis. This PRD is otherwise final pending that number and pending a larger sample set if genuine (non-seeded) price-drift history is wanted for the demo.

---

## 15. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Vendor format variance breaks parsing | Content-based detection routes each container (CSV, BIFF-signature binary, PDF) to the correct parser; PDF-twin fallback and vendor resend request cover the residual case (§7.3) |
| `.xls` files misrouted to the wrong parser (e.g. openpyxl on a BIFF2 file) | `detect_format` reads file bytes, not the extension, and routes BIFF-signature files to an xlrd-based reader directly — confirmed 5/5 on real samples |
| Cold start — no history on day one | History-free checks work immediately; ledger seeded from real samples |
| **Gmail refresh token expires mid-build** | OAuth app pushed to "In production"; re-authorization on recording day (§9) |
| **Twilio sandbox session expires (72h inactivity)** | Re-join check added to the recording-day checklist alongside Gmail re-auth (§9); email fallback if it fails |
| Agent sends an incorrect dispute | Four-condition gate (§7.5), per-invoice dedupe, pharmacist on every CC, fallback to `ask_pharmacist` when inconclusive |
| Agent emails a vendor twice | Per-invoice, per-mode send guard |
| Loop runs away / excessive tool calls | Turn cap per bill; park and notify on exhaustion |
| Live human reply fails during demo | Only Bill 4 depends on it; parked state pre-exists in Firestore; reply is a single WhatsApp message |
| Sensitive data exposure in repo or video | Scrubber pass preserving numeric fidelity (§10) |
| Credentials leaked via repo or logs | Secret Manager only; scoped service account; no secrets in source (§9) |
| Malformed/adversarial vendor file manipulates agent behavior | Recipients drawn from trusted directory, never from document content; extracted content is validation input, not a tool directive (§7.10) |

---

## 16. Future Work

- Reverse-engineered parser for the proprietary binary format
- Direct billing-software integration should an import path ever be found
- Sale-side capture, which would make a true inventory ledger honest and re-enable stock queries
- Richer proactive alerting (expiry-soon, seasonal reorder patterns)
- Multi-pharmacy tenancy

---

## 17. Submission Checklist

Mapped directly to the hackathon's "What to Submit" list, so nothing required is missed under deadline pressure.

| Deliverable | Status / plan |
|---|---|
| Hosted project URL | §7.11 status page — read-only, no GCP access required to view |
| Text description (features, tech, data sources, findings) | Drawn from this PRD at submission time; "other data sources" = none beyond vendor-provided bills |
| Code repository (GitHub/GitLab/Bitbucket) | **Not yet initialized — first build task.** If kept private, must be shared with `testing@devpost.com` and `cloudhackathons@google.com` before submission |
| README.md spin-up instructions | Step-by-step local + cloud deploy guide — required even though judges may not run it; proves reproducibility (30%-weighted criterion) |
| Architecture diagram | A real diagram asset for submission, not the ASCII sketch in §7.1 — same content, presentable form |
| ~4-min demo video | Single continuous take (§11) — problem, value prop, live demo, visible Google Cloud proof |
| *(Bonus, optional)* Public blog/video about the build | Not required; consider only if time remains after the above are solid — must be public (not unlisted) and state it was made for this hackathon |
| *(Bonus, optional)* Social post with `#AllThingsAgenticHackathon` | Not required; low-cost if time allows |
| *(Bonus, optional)* Gemma / Veo / Lyria integration | Not planned — out of scope for this project's problem |

- Reverse-engineered parser for the proprietary binary format
- Direct billing-software integration should an import path ever be found
- Sale-side capture, which would make a true inventory ledger honest and re-enable stock queries
- Richer proactive alerting (expiry-soon, seasonal reorder patterns)
- Multi-pharmacy tenancy
