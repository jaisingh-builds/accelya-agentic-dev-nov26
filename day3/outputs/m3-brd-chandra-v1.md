# BRD draft v1 - chandra

_model: none — drafted locally (Bedrock creds expired); regenerate with `python3 day3/scripts/draft_brd.py --learner chandra --profile accelya-ai` | prompt: m3-brd-prompt.md_

_Requirements from `docs/REFERENCE_ARCHITECTURE.md` §1, §3, §5a and hop 8 — no service names, no code._

## 1. Problem statement

Finance settles tickets from agency files that arrive with no record-level validation: malformed rows, wrong
airline mappings, duplicate tickets and late issues slip into settlement, causing incorrect payouts and
untraceable rejections. Analysts cannot say, per file, what was rejected and why; ops cannot tell a platform
retry from a new send; and rule changes cannot be verified against history.

## 2. Scope

**In scope:** record-by-record validation of every `ticket_issue_<airline>_<YYYY-MM-DD>.csv` file before
settlement; one recorded outcome per record; quarantine by reason; whole-file rejection on contract drift;
deterministic reruns and replays; per-file audit and reconciliation; human review of flagged and quarantined work.

**Out of scope:** the settlement/payment itself; fixing producer data upstream; the curated reporting zone
(later module); changes to the agency submission format; reviewer tooling.

## 3. Functional requirements

**FR-1 File contract.** A file is accepted only when its name and its nine-column header match the agreed
contract exactly.
*Acceptance:* a file whose header drifts (e.g. `currency` → `fare_currency`) is rejected whole before any record
is evaluated, with the file-level reason recorded.

**FR-2 Validation order.** Each record is checked in a fixed order: required fields present → formats →
reference data → business rules → lateness → duplicates. The first failing quarantine rule decides the reason.
*Acceptance:* on the synthetic file, every rejected record names the rule id that routed it.

**FR-3 Record routing.** Every record ends in exactly one outcome: validated, flagged (validated with a flag),
quarantine/<reason>, or skipped as a duplicate.
*Acceptance:* for `ticket_issue_AI_2026-11-25.csv`, 42 in = 21 validated + 1 flagged + 19 quarantined +
1 skipped, and the manifest balances.

**FR-4 Duplicates within a file.** The first accepted occurrence of a `ticket_number` wins; later occurrences
are skipped and counted.
*Acceptance:* line 22 skips as a duplicate of line 2; `duplicates: 1` in the manifest; a bad record can never
"claim" a ticket number.

**FR-5 Repeat deliveries distinguished.** A redelivered event (same key, same content hash) is a duplicate
delivery; a resend of identical bytes is a new file event, not a duplicate record.
*Acceptance:* replaying a delivery adds no new outcomes; a resend produces a new manifest under its own
correlation id.

**FR-6 Deterministic reruns.** All date rules compare against the file's business date from its name, never
the wall clock. Every output records its pins: rules version, reference-data version, code version.
*Acceptance:* rerunning validation next month on the same file yields identical routing and counts; the same
file plus the same versions gives the same result on every runner.

**FR-7 Money and date safety.** `fare_amount` parses as a decimal with at most 2 decimal places and must be
greater than 0; `issue_date` must be ISO, on or before the file's business date, and older than 30 days is late.
*Acceptance:* the −1200.00 record quarantines on business, the 4520.555 record on format, the future-dated
record on business, and the 56-day-old record is flagged.

**FR-8 Manifest and reconciliation.** Every processed file emits a manifest: source key, source hash, business
date, correlation id, pins, rows in, per-route counts, balanced flag. Quarantine sidecars carry line number and
rule id only.
*Acceptance:* `rows_in = validated + flagged + quarantined + skipped`; else the run fails visibly.

**FR-9 Human review.** Flagged and quarantined work is visible to the data owner with reason codes; corrections
are recorded events carrying reviewer identity and the record corrected — never direct edits.
*Acceptance:* an open-review list is producible per file; every correction names its reviewer and target record.

## 4. Non-functional requirements

- **Correctness:** every record ends in exactly one recorded outcome; the manifest balances or the run fails.
- **Security:** no payloads, passenger names, emails or fare amounts in logs or notifications; raw and
  quarantine zones readable only by the validating service and the data owner.
- **Observability:** every log record follows `docs/LOGGING_CONTRACT.md` — one JSON object with
  `correlation_id` on every line, identifiers and counts only; each file's correlation id traces
  raw → zones → manifest end to end.
- **Scalability:** ~40 files/day up to ~100k records each, peaking 00:00–03:00 IST; reruns must not disturb
  in-flight files.
- **Cost:** cost per file and per record measurable from manifest counts; retention bounded per zone.

## 5. Data handling

| Zone | Kept | Retention | Readers |
|---|---|---|---|
| `raw/` | file exactly as received, never modified | days | validating service only |
| `validated/` | clean + flagged records (flags, `source_line`) | settlement cycle | settlement, ops |
| `quarantine/<reason>/` | failed records + sidecar (line, rule id) | 30 days | data owner, ops — nobody else |
| `manifests/` | per-file counts, pins, hashes | ≥ replay horizon (90 days) | ops, audit |
| `curated/` | deduplicated conformed records | later phase | reporting |

## 6. Open questions for the business

1. Do flagged (late) records settle immediately, or wait for ops clearance inside the settlement window?
2. Is 30 days the right late threshold, and is it the same for every airline?
3. Which currencies and decimal exponents beyond the listed six must be supported?
4. Who owns quarantine clearance — the agency, Accelya ops, or the airline?
5. Does a resend replace prior decisions for that file, or append alongside them?
6. What retention do regulators require on `raw/` and `manifests/` beyond the 90-day replay horizon?
7. Should agencies receive machine-readable rejection feedback (the sidecar), and through which channel?
