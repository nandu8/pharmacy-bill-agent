# Product Requirements Document
## Autonomous Pharmacy Bill Intake & Verification Agent

**Author:** [Your Name]
**Track:** Taskmaster — Agentic Hackathon
**Status:** Draft v3
**Last updated:** August 18, 2026

---

## 1. Overview

An autonomous agent that monitors a pharmacy's email inbox for vendor bills arriving in mixed formats (CSV, XLS, PDF), decides how to read each one, investigates anything that looks off, communicates with both the pharmacist (WhatsApp) and the vendor (email) to resolve ambiguity, and stages verified files into Google Drive for upload into the pharmacy's existing billing software.

Unlike a fixed pipeline, the agent operates as a **tool-calling loop**: it is given a goal and a toolbox, and decides each step based on what it has learned so far. Different bills take visibly different paths.

## 2. Problem Statement

The pharmacy's existing billing software (legacy, Windows XP/7, no API) already parses uploaded bills, populates fields, and updates inventory once a file is uploaded. What remains manual and slow (~20 min/day) is:

1. Checking each vendor's email
2. Downloading the correct attachment
3. Uploading it into the software
4. Reviewing the populated data before submitting

This is repetitive, doesn't scale across many vendors, and a quick manual review can miss subtle errors (price changes, duplicate submissions, quantity mismatches). Corrupted or unreadable files require the pharmacist to notice, then chase the vendor manually. There is also no quick way to check stock without opening the software.

## 3. Goals

- Automate everything between "email arrives" and "verified file is sitting in the right Drive folder"
- Investigate anomalies autonomously rather than applying fixed threshold checks
- Recover from unreadable files without human intervention where possible; escalate to the vendor when not
- Communicate over WhatsApp — the pharmacist's natural channel — for notifications, targeted questions, and ad hoc stock queries
- Meet all hackathon technical requirements (Gemini 3.5 Flash, Google Agent Framework, Google Cloud infra)

### Non-Goals (explicitly out of scope)
- Integrating with or replacing the existing legacy billing software
- Automating the legacy software's UI (Windows desktop automation — too fragile for a live demo)
- Converting vendor files to a standard PDF format (see Section 13 — Design Decisions)
- Point-of-sale / customer checkout / sales recording
- Payment processing
- Multi-pharmacy / multi-tenant support

## 4. Target User

Primary: A pharmacy owner/operator (the founder's father) who currently handles vendor billing manually and is more comfortable with WhatsApp and familiar file folders than a new dashboard.

## 5. User Stories

- As a pharmacist, I want bills downloaded, read, and verified automatically so I don't have to check each email myself.
- As a pharmacist, I want the verified file waiting in the right folder so I can upload it without hunting for it.
- As a pharmacist, I want a WhatsApp message confirming a bill was processed, with no further action needed.
- As a pharmacist, I want to be asked a *specific* question when the agent is genuinely unsure — not a generic approve/reject prompt.
- As a pharmacist, I want unreadable files chased with the vendor automatically, so I'm not the one writing that email.
- As a pharmacist, I want to ask "how much [medicine] do I have?" over WhatsApp and get an instant answer.

## 6. Agent Design

### 6.1 Operating Model

The agent runs as a reasoning loop (Gemini 3.5 Flash + Google ADK). Each turn it inspects the current state, selects a tool, observes the result, and decides what to do next. It terminates when the bill is resolved, staged, or parked pending an external response.

```
        Email arrives (Gmail)
                │
                ▼
    ┌────────────────────────────┐
    │   AGENT LOOP                │
    │   (Gemini reasons each turn)│◄──────┐
    └───────────┬────────────────┘        │
                │ selects a tool          │ result
                ▼                         │ feeds back
    ┌────────────────────────────┐        │
    │         TOOLBOX             │───────┘
    └────────────────────────────┘
                │
                ▼
        Agent decides it is done
```

### 6.2 Toolbox

| Tool | Purpose |
|---|---|
| `detect_format` | Inspect actual file contents, not just extension |
| `parse_csv` | Structured parse (pandas) |
| `parse_xls` | Excel parse; fails on proprietary binary formats |
| `parse_pdf_vision` | Gemini multimodal read of PDF invoice |
| `lookup_vendor_history` | Prior bills/pricing for this vendor from Firestore |
| `check_duplicate` | Invoice number + vendor already processed? |
| `cross_check_other_vendors` | Same item priced by other vendors — market shift vs vendor error |
| `query_inventory` | Current stock levels |
| `ask_pharmacist` | Send a targeted question over WhatsApp, await reply |
| `email_vendor` | Request a resend for unreadable/missing files |
| `update_inventory` | Write to Firestore ledger |
| `stage_file` | Save original file to the vendor's Google Drive folder |
| `notify_success` | WhatsApp confirmation, no action needed |

### 6.3 Format Handling & Recovery

Formats confirmed from real vendor samples: two distinct CSV schemas, PDF tax invoices, and a proprietary binary file carrying an `.xls` extension that is **not** real Excel and defeats standard libraries.

Recovery order when a parse fails:
1. Re-detect format from file contents (extension may be misleading)
2. Check whether the *same invoice number* arrived in another format — vendors frequently send both a data file and a PDF of the same invoice
3. Attempt the alternate format (typically `parse_pdf_vision`)
4. Only if all routes fail: `email_vendor` requesting a resend, and notify the pharmacist that the bill is parked

This ordering matters: escalation is a last resort, not a first response.

### 6.4 Validation & Investigation

Rather than fixed threshold checks, the agent forms a hypothesis about what looks wrong and chooses which lookups will confirm or rule it out.

**Checks available without any history (works from day one):**
- Internal arithmetic: quantity × rate = line amount; line items sum to invoice total
- Rate vs MRP plausibility (rate should sit meaningfully below MRP; flag implausible margins)
- Duplicate invoice number + vendor

**Checks requiring accumulated or seeded history:**
- Price deviation against this vendor's prior pricing for the same item
- Quantity pattern deviation against typical order size
- Missing expected bill (vendor silent beyond usual cadence)

**Investigation example:** a price looks high → `lookup_vendor_history` → still ambiguous → `cross_check_other_vendors` → other vendors also raised prices on the same molecule → agent concludes market-wide movement rather than vendor error, auto-approves with a note. A different bill produces a different investigation.

### 6.5 Memory & Adaptation

Every human resolution is written back to Firestore and fed into future validation context. If the pharmacist approves a price rise for a vendor, the agent stops flagging it. If he rejects something, that pattern is weighted more heavily next time.

### 6.6 Proactive Behaviour

Not every action is triggered by an incoming email. On a schedule the agent also checks for:
- Vendors that have gone unusually quiet (expected bill not received)
- Items below reorder threshold with no incoming bill covering them

### 6.7 WhatsApp Communication

**Outbound — success:** confirmation that N bills were processed, inventory updated, files staged.

**Outbound — targeted question:** the agent generates the specific question it needs answered, e.g. *"This batch number matches a delivery received in June — is this a re-delivery or a duplicate invoice?"* — not a generic approve/reject.

**Inbound — resolution:** the pharmacist's reply is interpreted by Gemini and fed back into the loop; the agent continues from where it paused.

**Inbound — stock queries:** free-text questions ("how much Paracetamol do I have?") are interpreted, fuzzy-matched against inventory item names (which are messy in real data, e.g. `K GLIM M 1 TAB 15S`), and answered with quantity, batch, and expiry.

### 6.8 Staging to Google Drive

Verified files are saved **unmodified** into a per-vendor folder structure in Google Drive. With Drive Desktop syncing to the pharmacist's machine, the file is simply present locally when he goes to upload it — no dashboard to learn, no download step.

The original file is never altered. The billing software expects the vendor's native format, so preserving it byte-for-byte is a hard requirement.

### 6.9 Guardrails

- `email_vendor` must never fire twice for the same invoice
- Outbound vendor emails are surfaced to the pharmacist (copied or confirmed) rather than sent silently
- No bill is ever silently dropped; unresolved bills are parked in a visible pending state

## 7. Non-Functional Requirements

- **Data integrity:** original vendor files are never modified — only copied to Drive and separately normalized for internal use
- **Explainability:** every flag carries a plain-language reason, not just "anomaly detected"
- **Reliability:** failures are logged and surfaced, never swallowed
- **Privacy:** billing and inventory data handling documented in the submission

## 8. Technical Stack

| Component | Technology |
|---|---|
| LLM / reasoning | Gemini 3.5 Flash (Vertex AI) |
| Agent orchestration | Google ADK |
| Compute | Cloud Run |
| Database / ledger | Firestore |
| File staging | Google Drive API |
| Email (read + send) | Gmail API |
| Messaging | WhatsApp (Twilio or Meta Cloud API) |
| Parsing | pandas, Gemini vision for PDF |
| Scheduling | Cloud Scheduler (proactive checks) |

## 9. Data

**Confirmed vendor formats (from real samples):**
- Format A — ~76-column pharma-distribution CSV (`itemname`, `batchno`, `expdate`, `invqty`, `salerate`, `itemmrp`, `hsnsaccode`, …)
- Format B — simpler CSV with H/D/F row-type structure (`Name`, `Quantity`, `Selling Rate`, `MRP`, `Batch No.`, `Exp. Date`, `HSN`)
- Format C — proprietary binary carrying `.xls` extension; not real Excel, standard libraries fail
- Format D — PDF tax invoices; frequently duplicates the same invoice as Format C

**Normalized internal schema:**
`vendor, invoice_no, invoice_date, item_name, batch_no, expiry_date, quantity, rate, mrp, amount, hsn_code`

**Firestore — bills:** `bill_id, vendor, invoice_number, date, line_items[], total_amount, status (auto_approved | pending_vendor | pending_pharmacist | resolved), resolution_history[], drive_file_url`

**Firestore — inventory:** `item_name, current_quantity, unit, batch_number, expiry_date, last_updated`

## 10. Demo Plan

The demo shows **three bills taking visibly different paths** through the same agent:

1. **Clean CSV** — detect → parse → duplicate check → history lookup → normal → update inventory → stage to Drive → WhatsApp success. No human involved.
2. **Ambiguous bill** — parse succeeds, but batch number matches a June delivery → history lookup inconclusive → agent asks a specific WhatsApp question → pharmacist replies → agent resolves and stages.
3. **Corrupted file** — xls parse fails → agent re-detects format → finds the same invoice as PDF → reads it via Gemini vision → proceeds normally. (Variant: no PDF available → agent emails the vendor for a resend and parks the bill.)

Closing beat: a live WhatsApp stock query answered from Firestore.

## 11. Success Metrics

- Proportion of sample bills resolved with zero human action
- Correct recovery from a deliberately corrupted file without escalation
- Correct identification of injected anomalies, with a sensible explanation
- Accurate natural-language answers to stock queries
- Full loop runs live during the demo without manual intervention

## 12. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Vendor format variance breaks parsing | Content-based format detection plus multi-route recovery |
| Proprietary binary `.xls` unparseable | PDF fallback for the same invoice; vendor email as last resort |
| Cold start — no history on day one | History-free checks work immediately; seed Firestore with real sample invoices |
| Agent emails a vendor incorrectly | Deduplication guard plus pharmacist visibility on outbound mail |
| Loop runs away / excessive tool calls | Cap turns per bill; park and notify if unresolved |

## 13. Design Decisions

**Why not convert CSV/XLS to a standard PDF?** The staged file exists to be uploaded into the legacy billing software, which expects the vendor's native format. A PDF cannot be uploaded there, so conversion would break the file's only purpose. Uniform human-readable records are served instead by the normalized Firestore data, at no extra cost and with no risk of a second copy drifting out of sync.

**Why Drive rather than a dashboard?** Drive Desktop syncing places the file directly on the pharmacist's machine in a familiar folder. It removes both the dashboard-learning step and the download step.

**Why not automate the upload itself?** The billing software is a legacy Windows desktop application with no API. UI automation against it would be brittle, untestable without physical access to that machine, and a live-demo liability.

## 14. Future Work

- Reverse-engineered parser for the proprietary binary format
- Direct billing-software integration should an import path ever be found
- Richer proactive alerting (expiry-soon, seasonal reorder suggestions)
- Customer sale recording for two-way inventory sync

## 15. Open Questions

- Daily bill volume and vendor count, to size demo data and history seeding
- Whether outbound vendor emails should require pharmacist approval on every send or only initially