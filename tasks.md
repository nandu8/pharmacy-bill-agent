# Build Backlog — Pharmacy Bill Agent

Derived from `pharmacy_bill_agent_prd_v3.md`. Organized into phases in build order — mostly linear, with parallelizable stories called out. Each story references the PRD section it implements. Check items off as you go; this file is the source of truth for "what's left," not the PRD (the PRD is *what to build*, this is *in what order*).

**Budget:** ~2 weeks solo. Day estimates below assume ~10 real build days once demo/video/submission time is reserved at the end.

---

## Phase 0 — Accounts & Environment (Day 1, ~half day)

Nothing downstream can start until this is done. Do it in one sitting.

- [x] **T01** — Google Cloud account confirmed (`nandu0103@gmail.com`), billing account linked (`My Billing Account`, 01221D-846796-941715), standard new-account Free Trial active — ₹28,694 (~$300) credit, 0 used, expires 2026-11-17. This is the generic GCP trial, **not** the hackathon's $150 credit — those are separate and stack. Still worth claiming the hackathon one via the Resources-tab promo code for extra headroom, but not blocking
- [x] **T02** — Created project `pharmacy-bill-agent`, billing linked, set as active gcloud config
- [x] **T03** — Enabled: aiplatform, gmail, drive, run, firestore, pubsub, cloudscheduler, cloudtrace, logging, secretmanager APIs
- [x] **T04** — Firestore Native mode created, region `asia-south1` (Mumbai)
- [ ] **T05** — Create Twilio account, activate WhatsApp sandbox, note the join code (PRD §9) — manual, needs Twilio signup — **deferred by user request, doing this last** among the Phase 0/1 manual steps
- [x] **T06** — `git init`, first commit, GitHub repo created and pushed — `origin` is `https://github.com/nandu8/pharmacy-bill-agent.git`, branch `main` up to date with `origin/main`. If the repo is private, still need to share with `testing@devpost.com` + `cloudhackathons@google.com` before submission (PRD §17, tracked as T67)
- [x] **T07** — Add `.gitignore` covering secrets, `.env`, credentials, `__pycache__`, local venvs — before anything else gets committed (also covers raw `samples/`, see T14)

---

## Phase 1 — Gmail OAuth & Credential Security (Day 1–2, ~half day)

Blocks all ingestion work. Do this early because of the 7-day token trap (PRD §9).

- [x] **T08** — OAuth consent screen configured (Google Auth Platform), scopes added: `gmail.readonly`, `gmail.send`, `drive`. Desktop OAuth client created (`credentials.json`, gitignored, not committed)
- [x] **T09** — Published to Production via the Audience tab
- [~] **T10** — Secret Manager: `gmail-refresh-token` populated (version 1, minted via `scripts/gmail_oauth_setup.py`) ✓. `twilio-auth-token` still empty — deferred, doing Twilio last per user request. Vertex AI uses the Cloud Run-attached service account directly, no downloaded key needed
- [x] **T11** — Created `pharmacy-agent-sa@pharmacy-bill-agent.iam.gserviceaccount.com`, scoped to: `aiplatform.user`, `datastore.user`, `pubsub.editor`, `secretmanager.secretAccessor`, `logging.logWriter`, `cloudtrace.agent`, `run.invoker` — no project-editor role. (Gmail/Drive access is via the pharmacist's own OAuth consent in T08/T09, not this service account — personal Gmail accounts can't use domain-wide delegation.)

---

## Phase 2 — Real Data & Pharmacist Input (Day 1–2, parallelizable with Phase 0–1)

- [ ] **T12** — Get remaining/updated sample files from your father when available (more invoices per vendor, ideally with overlapping SKUs, to get real price-history signal — PRD §14 gap)
- [ ] **T13** — Get daily bill volume and vendor count from your father (PRD §14 open question #1 — the only unresolved item in the PRD)
- [x] **T14** — Write the sanitization/scrubber script: replace vendor names, GSTINs, licence numbers, phone numbers, addresses with synthetic values, preserving invoice-number shape and all prices/quantities/dates exactly (PRD §10) — run on all samples before anything touches the repo or a recording. `scripts/scrub_samples.py`, output in `samples_sanitized/` (committed); raw `samples/` stays gitignored. Verified zero residual PII across all 5 PDFs and byte-identical CSV/XLS output except the one Format B vendor-name field.

---

## Phase 3 — Parsing & Normalization (Day 2–3)

The data layer everything else depends on. Build against the real samples in `samples/`.

- [x] **T15** — `detect_format`: inspect file bytes (not extension) — distinguish CSV/delimited text, BIFF-signature binary, PDF. `src/pharmacy_agent/formats/detect.py`, keyed on the literal BIFF2 BOF magic bytes + `%PDF` + CSV header sniffing.
- [x] **T16** — `parse_csv`: handle Format A (79-column ERP export) and Format B (H/D/F row-type, VAT/TS naming) — both dialects confirmed from real samples. `src/pharmacy_agent/formats/parse_csv.py`.
- [x] **T17** — `parse_xls`: xlrd-based reader for the BIFF2 `.xls` files — confirmed working 5/5 on samples (PRD §7.3). `src/pharmacy_agent/formats/parse_xls.py`; regression test asserts openpyxl/default-engine still fail on these files (so detect_format's routing reason stays true).
- [x] **T18** — `parse_pdf_vision`: Gemini multimodal read of PDF tax invoices, via Vertex AI (`google-genai` SDK). `src/pharmacy_agent/formats/parse_pdf_vision.py`; builds a `Bill` directly (no separate normalize step — PDF has no raw-row schema). Confirmed against real sanitized PDFs: matches the xls twin's hand-verified line (`SILVEREX SSD CREAM 20GM`) and correctly resolves the `PH-26-49832` reconciliation case (13-item/2959.00 version). Model `gemini-3.5-flash` only serves from the Vertex AI `global` location for this project, not a region like `us-central1` — 404s there. Needed local `gcloud auth application-default login` (ADC) to test; production path (Cloud Run service account) is unaffected. `tests/test_parse_pdf_vision.py` (2/2 passing, live Vertex AI calls).
- [x] **T19** — Normalizer: map every format onto the unified schema (§10), including the generic `tax_component_1/2_label/rate/amount` fields — don't hardcode CGST/SGST. `src/pharmacy_agent/normalize.py`.
- [x] **T20** — Per-line arithmetic validator: `taxable_value = qty×rate − discount`, `line_total = taxable_value + tax1 + tax2` — verify against real sample math (already hand-verified in the PRD; port that into code + a test). `src/pharmacy_agent/validate.py` + `tests/test_parse_and_normalize.py` (10/10 passing against `samples_sanitized/`).

---

## Phase 4 — Firestore Data Layer (Day 3, parallelizable with tail of Phase 3)

- [x] **T21** — Firestore collections `bills`, `purchase_ledger`, `agent_runs` per PRD §10. `src/pharmacy_agent/firestore_client.py`: client + collection accessors (project `pharmacy-bill-agent`, database `(default)`, region `asia-south1` from T04). Firestore collections are virtual (exist only while non-empty), so "creation" is verified via a live write/read/delete round-trip per collection in `tests/test_firestore_client.py` (4/4 passing) rather than a provisioning call. Added `google-cloud-firestore` to `requirements.txt`.
- [x] **T22** — `record_purchase`: write normalized bill to `purchase_ledger`, keyed on `invoice_no` + `vendor` (idempotent — PRD §7.10). `src/pharmacy_agent/purchase_ledger.py`: one ledger doc per line item, doc ID is a sha256 hash of vendor+invoice_no+item_name+batch_no so retries/resumes overwrite rather than duplicate; `normalize_item_key` slugifies item names for future cross-vendor lookups (T24/T33). `tests/test_purchase_ledger.py` (4/4 passing, live Firestore writes).
- [x] **T23** — `check_duplicate`: invoice number + vendor lookup; distinguish "already resolved" from "same number, different content" (reconciliation case). `src/pharmacy_agent/check_duplicate.py`: queries `bills` by vendor+invoice_number, fingerprints line items (item_name/batch_no/quantity/rate/taxable_value/line_total) + total_amount to classify `NEW` / `DUPLICATE` (identical content) / `RECONCILIATION` (same number, different content — routes to T36) — surfaces the matched bill's `status` so callers can tell an already-resolved repeat from an in-flight one. `tests/test_check_duplicate.py` (3/3 passing, live Firestore).
- [x] **T24** — `lookup_vendor_history`: prior invoices/pricing for a vendor+item from the ledger. `src/pharmacy_agent/lookup_vendor_history.py`: queries `purchase_ledger` by vendor + `normalized_item_key`, sorted most-recent-first by `purchase_date`, optional `limit` (for the "last four invoices" pattern in PRD §7.4). `tests/test_lookup_vendor_history.py` (4/4 passing, live Firestore).
- [x] **T25** — Seed script: load real sample invoices into `purchase_ledger`; separately generate clearly-labelled synthetic history for price-drift demo data (PRD §10 — keep the two sources distinguishable in the data itself, e.g. a `seeded: true` flag). `scripts/seed_purchase_ledger.py`: `seed_real_samples` records all real Format B/C samples plus the authoritative PH-26-49832 CSV version (skips PDFs — twins of data already captured; skips the conflicting 14-item twin — reserved for T36); `seed_synthetic_price_history` writes 4 months of synthetic AMLODIPINE 5MG/SUMMIT PHARMA rate history. Added a `seeded: bool` field to every `purchase_ledger` doc (`record_purchase`'s new `seeded` param, default `False`) and to `VendorHistoryEntry`. Idempotent re-run confirmed (55 real + 4 synthetic docs live in Firestore). `tests/test_seed_purchase_ledger.py` (2/2), plus updated `tests/test_purchase_ledger.py` (5/5 passing).

---

## Phase 5 — Agent Core Loop (Day 4–5)

The reasoning engine. Get one full bill resolving end-to-end before adding more tools.

- [x] **T26** — ADK project scaffold; wire Gemini 3.5 Flash as the reasoning model. `src/pharmacy_agent/agent/model.py`: `build_agent()`/`build_model()` construct an ADK `Agent` (LlmAgent) with a `VertexGlobalGemini` subclass pinning the underlying `google.genai.Client` to the Vertex AI `global` location (same 404-on-region issue as T18). Added `google-adk` to `requirements.txt`. `tests/test_agent_model.py` (2/2 passing) — one live turn against real Vertex AI confirms the model is actually reachable, not just constructible.
- [x] **T27** — Define the toolbox (PRD §7.2) as ADK tool functions — start with the Phase 3/4 tools already built. `src/pharmacy_agent/agent/tools.py`: ADK `FunctionTool`-compatible wrappers for `detect_format`, `parse_csv`, `parse_xls`, `parse_pdf_vision`, `lookup_vendor_history`, `check_duplicate`, `record_purchase` (the other 6 PRD §7.2 tools depend on Phase 6/7/9 infra and aren't wired yet). Tools read/write the run's session state (`tool_context.state`) rather than take file bytes or a parsed `Bill` as an LLM-visible arg; `_impl` functions take a plain `state` dict so they're unit-testable without a real `ToolContext`. Verified the generated function-calling schemas are well-formed for all 7 (`FunctionTool._get_declaration()`). `tests/test_agent_tools.py` (12/12 passing, live Firestore + one live Gemini PDF call).
- [x] **T28** — Agent loop: turn-by-turn tool selection, observation, decision (PRD §7.1). `src/pharmacy_agent/agent/loop.py`: `run_bill(file_bytes, vendor_hint="")` seeds a fresh ADK session's state with the bill and drives the ADK `Runner` turn by turn, recording each turn's tool call(s) into `tool_call_history` (the PRD §8 trace). Terminal-state interpretation (resolved/pending_pharmacist/pending_vendor) is T29 — a run here just ends when the model stops calling tools. `tests/test_agent_loop.py` (1/1 passing) — a live, real multi-turn Gemini run over the clean SUMMIT PHARMA sample resolves end-to-end (detect → parse → record) with zero validation issues.
- [x] **T29** — Terminal states: `resolved` / `pending_pharmacist` / `pending_vendor` — wire the loop to stop cleanly in each case. `src/pharmacy_agent/agent/terminal.py`: a `finish(status, summary)` tool the model must call as its last action (ADK's "no more tool calls" signal only means the model stopped, not what it concluded); `record_bill_result` persists the outcome to `bills`, keyed on a deterministic `vendor::invoice_no` hash (same idempotency pattern as `purchase_ledger`), using the `invoice_number`/`status` field names `check_duplicate.py` already reads. `loop.py` falls back to `pending_pharmacist` if the model ever stops without calling `finish` (PRD §7.10: no bill silently dropped). `tests/test_agent_terminal.py` (4/4); `test_agent_loop.py` extended to assert the live run ends via `finish` → `resolved` and the `bills` doc is written.
- [ ] **T30** — Turn cap guardrail: park and notify on exhaustion rather than looping forever (PRD §7.10)
- [ ] **T31** — **Milestone check:** one clean sample bill (e.g. Sterling Pharma CSV) goes in, resolves automatically, writes to `purchase_ledger`, with no anomalies. This is Demo Bill 1 — get it working before anything else.

---

## Phase 6 — Validation & Investigation Logic (Day 5–6)

- [ ] **T32** — Rate vs MRP plausibility check
- [ ] **T33** — `cross_check_other_vendors`: same molecule, other vendors, market-shift vs. vendor-error signal
- [ ] **T34** — Price deviation check against vendor history (runs on seeded data from T25)
- [ ] **T35** — `find_related_document`: locate the same invoice number in another format/email
- [ ] **T36** — Reconciliation logic: same invoice number, conflicting content → treat PDF as authoritative, stage the matching version, flag the discrepancy (PRD §7.3/§7.4) — this is Demo Bill 3, and it runs entirely on real, already-verified sample data (`PH-26-49832`)
- [ ] **T37** — Vendor-silence check (Cloud Scheduler-triggered, no incoming email) — PRD §7.8

---

## Phase 7 — Dispute & Communication (Day 6–7)

- [ ] **T38** — `email_vendor`: resend-request mode and dispute mode — recipient always from the trusted vendor directory, never parsed from document content (PRD §7.10 guardrail)
- [ ] **T39** — Dispute gating: implement the four conditions from PRD §7.5 (≥3 prior invoices, conclusive cross-vendor check, ₹500 floor, no duplicate dispute)
- [ ] **T40** — `DISPUTE_REQUIRES_APPROVAL` config flag (default `false`) — PRD §7.5
- [ ] **T41** — Twilio WhatsApp integration: `notify_pharmacist` (outbound) and `ask_pharmacist` (outbound + suspends run)
- [ ] **T42** — Inbound WhatsApp webhook — separate entrypoint, receives replies
- [ ] **T43** — **Milestone check:** Demo Bill 2 — seeded overcharge triggers investigation → dispute email sent → pharmacist notified, zero human involvement

---

## Phase 8 — Durable Pause & Resume (Day 7–8)

- [ ] **T44** — Serialize run state to `agent_runs` on `ask_pharmacist`/`pending_vendor` (bill_id, line items, findings, tool-call history, open question, correlation key) — PRD §7.6
- [ ] **T45** — Resume Cloud Run entrypoint: match inbound reply to correlation key, rehydrate state, continue the loop from the paused turn
- [ ] **T46** — Wire the same resume mechanism to vendor resend replies (`pending_vendor` case)
- [ ] **T47** — **Milestone check:** Demo Bill 4 — park a bill, wait, reply on WhatsApp days (or minutes) later, confirm it resumes correctly and `pending_pharmacist → resolved` on Firestore

---

## Phase 9 — Drive Staging & Ingestion Pipeline (Day 8–9)

- [ ] **T48** — Google Drive API integration: per-vendor folder structure
- [ ] **T49** — `stage_file`: byte-for-byte copy, original never modified (PRD §7.9)
- [ ] **T50** — Install/confirm Drive Desktop sync on the pharmacy machine (real-world step, not code — PRD §14 #4)
- [ ] **T51** — Gmail `watch` + Pub/Sub push subscription — replaces polling (PRD §9)
- [ ] **T52** — Cloud Run trigger wired to the Pub/Sub push — new email → agent loop starts
- [ ] **T53** — **Milestone check:** send a real test email with a sample attachment, confirm it triggers the full pipeline end-to-end without manual intervention

---

## Phase 10 — Observability & Status Page (Day 9–10)

- [ ] **T54** — Export ADK trace output to Cloud Trace / Cloud Logging (PRD §8)
- [ ] **T55** — Confirm per-bill reasoning chains are retrievable and distinguishable (short chain vs. longer investigation chain)
- [ ] **T56** — Build the judge-facing status page (PRD §7.11): read-only Cloud Run view, lists bills with vendor/timestamp/status, links to trace
- [ ] **T57** — Deploy the status page, confirm it's reachable without GCP IAM access

---

## Phase 11 — Memory & Adaptation (Day 10, can slip if time is tight)

- [ ] **T58** — Write pharmacist resolutions back to Firestore, feed into future validation context (PRD §7.7) — lowest priority if the schedule slips; the four demo bills don't strictly require this to be visible on camera

---

## Phase 12 — Demo Prep (Day 11–12)

- [ ] **T59** — Seed final price-history data for the demo (synthetic, clearly labelled per T25)
- [ ] **T60** — Dry-run all four demo bills end-to-end, confirm timing against the §11 beat sheet
- [ ] **T61** — Pre-stage Bill 4's parked state in Firestore before the recording session (legitimate setup, not an edit — PRD §11)
- [ ] **T62** — Draft the architecture diagram as a real asset (not the PRD's ASCII sketch) — PRD §17
- [ ] **T63** — Write `README.md` with spin-up instructions (local + cloud deploy) — PRD §17, required even if judges don't run it
- [ ] **T64** — Rehearse the ~4-minute script; confirm re-auth checklist (Gmail token, Twilio sandbox join) is fresh on recording day (PRD §9, §15)

---

## Phase 13 — Recording & Submission (Day 13–14)

- [ ] **T65** — Record the demo as a single continuous, unedited take (PRD §11)
- [ ] **T66** — Finalize repo: confirm no secrets committed, confirm `.gitignore` caught everything, push
- [ ] **T67** — If repo is private, share with `testing@devpost.com` and `cloudhackathons@google.com`
- [ ] **T68** — Write the Devpost submission text (description, features, tech stack, data sources, findings/learnings) — PRD §17
- [ ] **T69** — Submit: video, repo URL, status-page URL, architecture diagram, write-up
- [ ] **T70** — *(Optional, bonus only)* Public blog/video post about the build, stating it was made for this hackathon
- [ ] **T71** — *(Optional, bonus only)* Social post with `#AllThingsAgenticHackathon`

---

## Notes on sequencing

- **Phases 0–2 can overlap** — account setup, OAuth, and pinging your father for samples/volume numbers don't block each other.
- **Phase 5's milestone (T31) is the first real checkpoint** — a single bill resolving end-to-end. Don't move on to anomaly logic until this works, or you'll be debugging two layers at once.
- **Phases 6–9 are the bulk of the build** and are mostly sequential because each demo bill depends on the previous phase's tools existing.
- **Phase 11 (memory/adaptation) is the one thing safe to cut** if the schedule slips — nothing in the four-bill demo strictly requires it.
- Reserve **Phases 12–13 as non-negotiable** — PRD, video, README, and diagram are worth as much in judging as the code itself (60% of scoring is Architectural Discipline + Demo/Production Readiness combined).
