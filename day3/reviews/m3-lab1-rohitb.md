# M3 Lab 1 - raw zone, trigger, duplicate vs resend: evidence

**Learner:** `rohitb`   **Account (aws sts get-caller-identity):** `…2198`   **Bucket:** `accelya-m1-rohitb-…2198`

Commands: day3/LABS.md (Lab 1). Write counts and identifiers only - never row values.

| Step | Correlation id | file.received count (grep by that id) | Expected |
|---|---|---|---|
| Land in raw/ | `rohitb-d3-001` | 1 (then 2: step 3 was run twice by mistake, see note) | 1 |
| Bucket root (filter read first: prefix `raw/`) | `rohitb-d3-002` | _pending_ | 0 |
| Replay the event once (simulated duplicate delivery) | `rohitb-d3-001` | 3 in total | 2 in total |
| Resend: new upload, same bytes | `rohitb-d3-003` | _pending_ | 1 |

**Note on the 3:** the second `s3 cp` with `rohitb-d3-001` (06:44:19Z) was a resend reusing the old id; the replay was 06:48:51Z.
All three `file.received` lines are identical (same id, key, bytes 2964, record_count 42); only the Lambda RequestId differs.
So the log alone can't tell a resend from a duplicate: the handler should also log the S3 `sequencer` / `versionId` and the ETag.

- ETag 1 (after landing): `3916ab33d669a45d8a98ad8276c532c9`    ETag 2 (after the resend): _pending_    equal? _pending_ - what does that tell you about this file?
- Paste ONE file.received line (keys and counts only):
  `{"level": "INFO", "service": "accelya-m1-ingest", "correlation_id": "rohitb-d3-001", "message": "file.received", "boundary": "s3->lambda", "key": "raw/ticket_issue_AI_2026-11-25.csv", "bytes": 2964, "record_count": 42, "cold_start": true, "duration_ms": 33}`

## Duplicate delivery vs resend (one line each)
- Duplicate delivery is the same S3 event handed to the Lambda twice (at-least-once delivery): same correlation id, same object, same event.
- A resend is a new upload of the same bytes: a new S3 event with a new correlation id, but the same key and ETag.
- Day 4's validator should dedupe a duplicate on the event identity (bucket + key + versionId or sequencer), and a resend on a content hash (ETag for single-part SSE-S3, or a SHA-256 it computes) checked against files already settled.

## The zone layout you'd design
| Zone | Prefix | Who writes | Who reads | Kept for | Why it exists |
|---|---|---|---|---|---|
| raw | `raw/` | airline / agency upload | ingest Lambda only | 90 days | untouched copy of what was received, for replay and audit |
| validated | `validated/` | validator | settlement, reader role | until settled + audit period | rows that passed every rule (flagged rows included) |
| quarantine | `quarantine/<reason>/` | validator | ops / data-quality team | until fixed or resent (30 days) | failed rows kept apart with the reason, never settled |
| curated | `curated/` | settlement batch | reporting, finance | 7 years | settled, reconciled data for finance and regulators |
