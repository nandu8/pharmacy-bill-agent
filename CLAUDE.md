# Pharmacy Bill Agent — Working Agreement

This file is auto-loaded every session. It is the standing process for this
repo — follow it for every task pickup without being asked again.

## Scope discipline

- `hackathon_details.md` is the outer boundary of what this project is for.
  Every task must trace back to a requirement in there (Taskmaster track:
  agent takes real action on a messy multi-step workflow, uses Gemini 3.5+ via
  Vertex AI/Gemini API, at least one Google agent framework, at least one GCP
  infra service) or to `pharmacy_bill_agent_prd_v3.md` / `tasks.md`, which
  implement it.
- If a task would add something not called for by the hackathon rules or the
  PRD (extra polish, unrequested features, speculative abstractions), stop
  and flag it instead of building it.
- `tasks.md` is the source of truth for "what's left" and in what order — not
  this file, not the PRD. The PRD says *what* to build; `tasks.md` says *what
  order*.

## Per-task workflow (TDD, one task at a time)

For every `tasks.md` item you pick up:

1. **Read `tasks.md` first**, pick the next unchecked, unblocked item in
   phase order (respect the "Notes on sequencing" section at the bottom).
2. **Write the test(s) first** for the behavior the task describes, run them,
   confirm they fail for the expected reason (not a typo/import error).
3. **Implement** the minimum code to make them pass.
4. **Run the full test suite**, not just the new tests — a task isn't done if
   it regresses an earlier one.
5. Only once all tests pass: **update `tasks.md`** — check the box, add a
   short note on what was actually built (file paths, any deviation from the
   plan), same style as the existing entries.
6. **Commit and push** — one commit per completed task/story, message
   references the task ID (e.g. `T21: create Firestore collections`).
7. **Suggest clearing context** (tell the user to run `/clear` or start a
   fresh session) before picking up the next task. Don't carry prior tasks'
   exploration/tool output into the next one's context — re-read `tasks.md`
   and the relevant source files fresh instead of relying on memory of them.

Do not batch multiple `tasks.md` items into one commit or one context unless
the user explicitly asks for that.

## Token optimization

- Prefer targeted `Read` (with offset/limit) or `Grep` over reading whole
  large files (PRD, sample data dumps) repeatedly — re-read only the section
  needed for the current task.
- Never paste raw sample invoice contents (CSV/XLS/PDF bytes) into the
  conversation — read them via code/tests, not via chat.
- For open-ended investigation (e.g. "where does X happen across the
  codebase"), fork or use a subagent so exploration noise doesn't stay in the
  main context.
- Keep `tasks.md` updates terse (one line + short parenthetical), matching
  the existing entries — it's a checklist, not a changelog.
- Clearing context between tasks (see step 7 above) is itself the main
  token-optimization lever — don't try to compress instead of clearing.

## Secrets

- Real credentials only ever go into Secret Manager or a gitignored local
  file (`credentials.json`, `token.json`, `.env`) — never into `tasks.md`,
  commits, or committed source.
- Raw vendor samples with real PII stay in `samples/` (gitignored); only
  `samples_sanitized/` is committed.
