## ROLE
You are a senior business analyst writing a Business Requirements Document (BRD) for Accelya's airline
settlement platform. An AWS data engineering team will design and build from it, and it is reviewed
before anyone writes code.

## CONTEXT
- One-line business requirement (from the business):
  **Every ticket-issue file an agency sends must be validated record by record before settlement, so
  finance only settles clean tickets and can see, per file, what was rejected and why.**
- Files: `ticket_issue_<airline>_<YYYY-MM-DD>.csv`, one per airline per business day, columns: pnr,
  ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.
  Volume: <FILL: e.g. about 40 files a day, up to ~100,000 records each, most arriving 00:00-03:00 IST>.
- Storage zones already agreed: raw (the file exactly as received, never changed), validated, quarantine
  (by reason), curated (for reporting, later).
- Validation rule design from our team (summarise, don't paste JSON):
  <FILL: your 5 most important rules, one line each, e.g. "ticket_number prefix must belong to the airline -> quarantine/referential">
- Standards every requirement must respect: docs/LOGGING_CONTRACT.md (one JSON object per log record,
  correlation_id on every record, identifiers and counts only - never record contents or fares).
- Synthetic data only. Use airline vocabulary: pnr, ticket_number, fare_class, od_pair, settlement.

## EXAMPLES
A good functional requirement looks like this:
> **FR-3 Record routing.** Each record goes to exactly one outcome: validated, flagged (validated with a
> flag), quarantine/<reason>, or skipped as a duplicate.
> *Acceptance:* for the synthetic file ticket_issue_AI_2026-11-25.csv, records in = validated + flagged +
> quarantined + skipped (42 = 42), and every quarantined record names its rule id.

## NON-GOALS
- Do not choose AWS services, write code or draw an architecture. That is the design step, tomorrow.
- Do not estimate effort or cost in money.
- Do not invent requirements the business didn't give: list them as open questions instead.
- <FILL: one more non-goal of your own>

## OUTPUT FORMAT
Markdown, at most 900 words, with exactly these `##` sections:
1. Problem statement (who feels the pain today)
2. Scope (in scope / out of scope, as two short lists)
3. Functional requirements (FR-1, FR-2, ... each with a one-line *Acceptance:* criterion that can be tested)
4. Non-functional requirements (one line each for: correctness, security, observability, scalability, cost)
5. Data handling (what is kept in each zone, for how long, and who may read quarantine)
6. Open questions for the business (numbered)

## REVISION
(Draft 1: leave this section empty. Draft 2: paste your review's revision notes here, then run again.)
