# Sample Scenarios — What the Agent Actually Does

Plain-language walkthrough of the five situations the agent handles, referenced from the PRD (`pharmacy_bill_agent_prd_v3.md`, §7.3–§7.8). Each is a different "day" for a bill arriving in the inbox.

---

## Scenario 1 — Everything's fine

A vendor emails a CSV. The agent opens it, checks the math for each line — quantity × rate, minus any discount, plus CGST and SGST, does that add up to what the vendor billed for that line? (Real pharma bills have tax and discount on every item, not just a flat total, so the check has to work at that level or it'll misfire constantly.) It also checks it's not a bill it's already seen, and compares the price against what this vendor usually charges — all normal.

It saves the bill's details, drops the file into the pharmacist's Google Drive folder (which syncs to his PC), and sends one WhatsApp message: *"Bill from MedPlus processed, nothing needs your attention."*

The pharmacist does nothing. This is the boring, common case — most bills should end here.

**Toolbox path:** `detect_format` → `parse_csv` → `check_duplicate` → `lookup_vendor_history` → `record_purchase` → `stage_file` → `notify_pharmacist`
**Terminal state:** `resolved`

---

## Scenario 2 — The vendor overcharged him

Same as above, except one line item is priced way higher than usual — say paracetamol jumped from ₹82 to ₹104 a strip.

Before assuming it's an error, the agent checks: did *other* vendors also raise prices on paracetamol recently?

- **If yes** → probably a real market price rise. Agent lets it through, leaves a plain-language note explaining why.
- **If no other vendor moved** → this vendor likely made a mistake. The agent **writes the vendor an email itself** — *"Your invoice #4521 shows ₹104/strip, your last four invoices were ₹82, please clarify"* — CCs the pharmacist, stages the file anyway, and pings him: *"Found a possible overcharge on MedPlus's bill, I've disputed it, here's the email."*

The pharmacist never had to notice the discrepancy or write that email himself — he's just told it happened.

**Toolbox path:** `lookup_vendor_history` → `cross_check_other_vendors` → `email_vendor` (dispute mode) → `stage_file` → `notify_pharmacist`
**Terminal state:** `resolved` (dispute sent autonomously — see PRD §7.5 for the four conditions that must all hold before this fires)

---

## Scenario 3 — The file is broken

The vendor's file is one of those garbage `.xls` files that isn't really Excel — normal software can't open it.

Instead of giving up, the agent checks: did this same vendor also send this same bill as a PDF (they often send both)?

- **If yes** → it reads the PDF instead. Gemini can read scanned or photographed invoices, not just clean text, so this works even on a phone-camera PDF. Carries on normally.
- **If no PDF exists anywhere** → it emails the vendor: *"Couldn't open your invoice, please resend,"* and tells the pharmacist: *"MedPlus's bill is unreadable, I've asked them to resend it, nothing to do yet."*

**Toolbox path:** `parse_xls` (fails) → `detect_format` (re-check) → `find_related_document` → `parse_pdf_vision` → continues normally
**Fallback path:** `find_related_document` (nothing found) → `email_vendor` (resend mode) → `notify_pharmacist`
**Terminal state:** `resolved` or `pending_vendor`

---

## Scenario 4 — The agent genuinely can't decide

Say a batch number on today's bill matches a delivery from two months ago. Could be a legitimate re-order, could be the vendor accidentally billing twice — the agent can't tell from the data alone.

Rather than guessing in either direction (auto-approving or auto-disputing would both be a bad bet here), it asks the pharmacist one specific WhatsApp question: *"This batch number matches a June delivery — is this a re-order or a duplicate bill?"*

Then it **goes to sleep** — it doesn't sit there waiting, doesn't hold anything open, just parks itself. Whenever the pharmacist replies — five minutes or three days later — that reply wakes it back up, and it finishes the job from exactly where it stopped.

**Toolbox path:** `lookup_vendor_history` (ambiguous) → `ask_pharmacist` → *[state serialized, run terminates]* → *[reply arrives, days later]* → resume from saved state → `record_purchase` → `stage_file`
**Terminal state:** `pending_pharmacist`, later `resolved` (PRD §7.6 — durable pause & resume)

---

## Scenario 5 — A vendor's gone quiet

This one isn't triggered by an email at all — once a day the agent checks itself: "Has vendor X, who usually bills every week, gone unusually silent?"

If so, it flags it: *"MedPlus hasn't sent a bill in 12 days, unusual for them — might be worth a call."*

This is the agent noticing something on its own, not just reacting to what lands in the inbox.

**Toolbox path:** (scheduled, no incoming email) → `lookup_vendor_history` (cadence check) → `notify_pharmacist`
**Terminal state:** `resolved` (informational; no bill to park)

---

## The common thread

The agent never just reports a problem and stops. It either:
- **resolves it outright** (Scenarios 1, 2),
- **recovers on its own** (Scenario 3), or
- **asks the one specific question it actually needs answered, then remembers to pick the thread back up later** (Scenario 4).

It only bothers the pharmacist when it's truly stuck, and even then it asks something precise instead of a generic "please review this." Scenario 5 shows it also acts without being prompted at all.
