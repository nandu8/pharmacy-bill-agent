This is a real life scenario where I call my father to come home at night but my father says he will late, who is over 65 years of age, because he takes a lot of time to go through his email and download the required company billing files and then open the billing software and upload this. Which parses the file and then he verified for any issues and then uploads it. So below is the solution which I was planning to submit for the hackathon


# Product Requirements Document (PRD)

**Title:** Autonomous Pharmacy Bill Intake, Validation & Conversational Inventory Agent  
**Track:** Taskmaster — Agentic Hackathon  
**Target LLM:** Gemini 3.5 Flash  
**Status:** Draft (Closed-Loop Edition)  

---

### 1. Overview
An autonomous AI agent designed for small-to-midsize retail pharmacies. It monitors vendor email inboxes, parses non-standard bill attachments (CSVs/PDFs), normalizes data, and conducts multi-tiered validation checks for price drift, mathematical errors, and duplicate submissions.

Validated bills update a live Firestore inventory, notify the pharmacist over WhatsApp, and trigger a local Watchdog script that automatically places the file directly into the legacy Windows desktop billing software's import folder. Additionally, the agent handles two-way WhatsApp interaction for manual flag approvals and real-time inventory queries.

---

### 2. Problem Statement
Small retail pharmacies rely on legacy, offline desktop software (e.g., Windows XP/7/10 apps without open APIs) to run their business. While this software manages internal sales well, processing incoming vendor bills requires manual work (~20–30 mins/day):
1. Monitoring vendor emails for invoice attachments.
2. Manually downloading files and converting formats.
3. Checking line items manually for price hikes or quantity errors.
4. Manually dragging files into the desktop software's ingestion folder.
5. Opening desktop software just to check current stock levels.

---

### 3. Goals & Non-Goals

#### Goals
* **Autonomous Pipeline:** Zero manual intervention between "Email Received" and "File dropped in Local Legacy Import Folder" for non-anomalous bills.
* **Agentic Pre-Validation:** Multi-tiered anomaly detection (math validation + historical price drift detection).
* **Two-Way WhatsApp Interface:** Outbound notification alerts and inbound natural-language stock queries.
* **Closed-Loop Delivery:** Local Watchdog script running on the pharmacy PC to auto-sync approved files into the local software's target directory.

#### Non-Goals
* **Direct UI Automation:** Automating Windows desktop GUI clicks via Screen OCR/PyAutoGUI.
* **Retail Point-of-Sale (POS) Billing:** Handling customer checkout or generating retail sale receipts.
* **Proprietary Binary Parse:** Support for binary .xls mock vendor formats.

---

### 4. System Architecture & Workflow
1. **Intake & Storage:** Gmail API + Google Cloud Storage
2. **Parsing & Normalization:** Pandas + Gemini 3.5 Flash
3. **Agentic Reasoning & Anomaly Engine:** Gemini 3.5 + Firestore (Tier 1 Math & Dupes; Tier 2 Historical Drift)
4. **Firestore State Update:** Running stock & batch updates
5. **Local Drop-Folder Bridge:** Python Watchdog script on local PC
6. **Legacy Software Ingestion:** Auto-drop into `C:\PharmacyApp\Imports\`

---

### 5. Functional Requirements

#### 5.1 Email Intake & Normalization
* Monitor designated Gmail inbox for vendor emails containing bill attachments (CSV primary, PDF secondary).
* Gemini 3.5 Flash standardizes varying vendor schemas into standard internal schema:
  `{vendor, invoice_no, invoice_date, item_name, batch_no, expiry_date, quantity, rate, mrp, total_amount}`

#### 5.2 Multi-Tiered Anomaly Detection Engine
* **Tier 1 (Instant):** Math consistency checks (Quantity × Rate = Total), duplicate invoice checks.
* **Tier 2 (Historical):** Price drift checks (>X% price increase) and order quantity anomaly detection.
* Plain-language explanation generation for all flags.

#### 5.3 Local Drop-Folder Bridge (Closed-Loop Ingestion)
* Python background service (`watchdog`/`requests`) on the pharmacy machine.
* Auto-syncs approved files directly to `C:\PharmacyApp\Imports\`.

#### 5.4 Conversational WhatsApp Interface
* **Outbound:** Success digests and review alerts.
* **Inbound Approvals:** Process `YES`/`NO` replies.
* **Inbound Stock Queries:** Handle natural language queries ("How much Paracetamol do we have?") using Gemini 3.5 Flash fuzzy matching.

---

### 6. Tech Stack
* **LLM Reasoning:** Gemini 3.5 Flash (Vertex AI / Gemini API)
* **Agent Framework:** Google Agent Development Kit (ADK)
* **Cloud Infrastructure:** Cloud Run, Firestore, Google Cloud Storage
* **Integrations:** Gmail API, Twilio WhatsApp API, Python Watchdog

---

### 7. Success Metrics
* **100% Automation Rate:** End-to-end execution from email intake to local drop-folder delivery.
* **100% Anomaly Precision:** Accurate detection of injected price drifts and math errors.
* **Conversational Accuracy:** Precise inventory counts returned for WhatsApp queries.


Where I'd push back

Too many subsystems to demo in 4 minutes. You've got: email intake → parsing (CSV+PDF) → Tier 1 math checks → Tier 2 historical drift → Firestore update → WhatsApp outbound alert → WhatsApp inbound approval → WhatsApp inbound stock query → local watchdog → legacy folder drop. That's ~6 distinct live moments. Realistically you can show 3 well in 4 minutes; the rest becomes narration over a diagram, which weakens "Demo & Production Readiness" (30% of your score, and they explicitly want a live, unedited demo).
Two-way WhatsApp is a demo risk, not just a feature. Twilio sandbox approval, webhook latency, and phone verification are classic last-minute failure points. If it flakes during the recorded demo, you lose the "proof it works" credibility judges are scoring you on. Consider: keep outbound alerts (safe, easy to show) and cut or pre-record inbound approval/query as a fallback, rather than betting the whole demo on live two-way messaging.
The local watchdog bridge undercuts your cloud story. The rules explicitly require proof the backend runs on Google Cloud (Cloud Run dashboard, Vertex AI logs, etc.). A script that lives on a local Windows PC and drops files into a local folder is, by design, off Google Cloud. It's a great real-world touch, but don't let it be the visual centerpiece of the video — show it as a 5-second "and then it lands here" beat, not the star.
Tier 2 historical price drift needs seeded data. You can't demonstrate "drift detection" convincingly without pre-existing price history in Firestore. Seed that ahead of time so the anomaly triggers cleanly on camera — don't try to build history live.