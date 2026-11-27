# M3 Lab 1 - raw zone, trigger, duplicate vs resend: evidence

**Learner:** `vikramsing`   **Account (aws sts get-caller-identity):** `…4111`   **Bucket:** `accelya-m1-vikramsing-…4111`

Commands: day3/LABS.md (Lab 1). Write counts and identifiers only - never row values.

| Step | Correlation id | file.received count (grep by that id) | Expected |
|---|---|---|---|
| Land in raw/ | `vikramsing-d3-001` | 1 | 1 |
| Bucket root (filter read first: prefix `____`) | `vikramsing-d3-002` | (not run yet) | 0 |
| Replay the event once (simulated duplicate delivery) | `vikramsing-d3-001` | (not run yet) | 2 in total |
| Resend: new upload, same bytes | `vikramsing-d3-003` | 1 | 1 |

- ETag 1 (after landing): `(not recorded yet)`    ETag 2 (after the resend): `(not recorded yet)`    equal? `yes / no` - what does that tell you about this file?
- Paste ONE file.received line (keys and counts only):

```json
{"ts": "2026-11-26T06:38:27Z", "level": "INFO", "service": "accelya-m1-ingest", "learner_id": "vikramsing", "correlation_id": "vikramsing-d3-001", "message": "file.received", "boundary": "s3->lambda", "bucket": "accelya-m1-vikramsing-…4111", "key": "raw/ticket_issue_AI_2026-11-25.csv", "bytes": 2964, "record_count": 42, "cold_start": true, "init_age_s": 0.2, "memory_limit_mb": 256, "duration_ms": 29}
```

## Duplicate delivery vs resend (one line each)
- Duplicate delivery is the same S3 event delivered to the Lambda more than once (S3 delivers at least once): nothing new was uploaded, so bucket, key and ETag are all the same.
- A resend is a new upload of the file: S3 creates a new event with a new event time, even when the bytes (and so the ETag) are identical.
- How should Day 4's validator recognise each one, so nothing is settled twice? Treat a repeated event identity (bucket + key + version/ETag + sequencer) as a duplicate and skip it; for a resend, compare a content hash the validator computes itself (not the ETag): same hash = already processed, skip and flag it; different hash = a corrected file, route it for review.

## The zone layout you'd design
| Zone | Prefix | Who writes | Who reads | Kept for | Why it exists |
|---|---|---|---|---|---|
| raw | `raw/` | The uploader (agency or booking system) | Ingest Lambda (read-only) and the Day 4 validator | 30 days, then expires | Landing zone: the untouched original, so any file can be replayed |
| validated | `validated/` | The validator | The curated builder and reconciliation | 90 days (the replay window in the reference architecture) | Rows that passed every rule, including flagged ones |
| quarantine | `quarantine/<reason>/` | The validator | Reviewers | 30 days (lifecycle in the reference architecture) | Rows or payloads that failed a rule, held for a person instead of being dropped |
| curated | `curated/` | The curation job, after validation | Settlement and reporting (Athena) | Long term, to be agreed with the business | Clean, ready-to-use data for settlement |
