# M3 Lab 1 - raw zone, trigger, duplicate vs resend: evidence

**Learner:** `chandra`   **Account (aws sts get-caller-identity):** `…7098`   **Bucket:** `accelya-m1-chandra-851725487098`

Commands: day3/LABS.md (Lab 1). Write counts and identifiers only - never row values.

| Step | Correlation id | file.received count (grep by that id) | Expected |
|---|---|---|---|
| Land in raw/ | `chandra-d3-001` | 1 | 1 |
| Bucket root (filter read first: prefix `raw/`) | `chandra-d3-002` | 0 | 0 |
| Replay the event once (simulated duplicate delivery) | `chandra-d3-001` | 3 in total | 2 in total |
| Resend: new upload, same bytes | `chandra-d3-003` | 1 | 1 |

- ETag 1 (after landing): `3916ab33d669a45d8a98ad8276c532c9`    ETag 2 (after the resend): `3916ab33d669a45d8a98ad8276c532c9`    equal? `yes` - the bytes are identical, so the resend carried the same content; ETag equality holds here (single-part, SSE-S3) but a computed SHA-256 is the safe dedup key.
- Paste ONE file.received line (keys and counts only):

```json
{"ts": "2026-11-26T06:15:57Z", "level": "INFO", "service": "accelya-m1-ingest", "learner_id": "chandra", "correlation_id": "chandra-d3-001", "message": "file.received", "boundary": "s3->lambda", "bucket": "accelya-m1-chandra-851725487098", "key": "raw/ticket_issue_AI_2026-11-25.csv", "bytes": 2964, "record_count": 42, "cold_start": true, "init_age_s": 0.1, "memory_limit_mb": 256, "duration_ms": 31}
```

Note on the replay step: grep returned 3, not the expected 2. Timestamps are 06:15:57 (upload, cold_start), 06:19:14 (my replay invoke, warm), and 06:19:28 (warm, ~14s later). The third line is a real extra delivery - S3 notifications are at-least-once, so one more delivery of the same event arrived on its own. Same key, same correlation id, same bytes.

## Duplicate delivery vs resend (one line each)
- Duplicate delivery is the **same event** arriving again: same key, same correlation id, same object version - a platform retry, not a new business fact.
- A resend is a **new event** for the same content: new correlation id (`chandra-d3-003`) and a new object write (same ETag only because the bytes matched).
- How should Day 4's validator recognise each one, so nothing is settled twice? Key on **content + business identity**, not on delivery: dedup by a computed hash of the file (or `source_key` + `source_sha256` in the manifest) and, at record level, by `ticket_number` as the unique rule already does - so a redelivery or a resend of the same bytes can never settle a record twice.

## The zone layout you'd design
| Zone | Prefix | Who writes | Who reads | Kept for | Why it exists |
|---|---|---|---|---|---|
| raw | `raw/` | producer (S3 PutObject) | ingest Lambda only | short (days), then lifecycle-delete | landing zone - the only trigger prefix; untouched evidence of what arrived |
| validated | `validated/` | validator Lambda | settlement / downstream jobs | per settlement cycle | records that passed every rule, partitioned by `business_date=` |
| quarantine | `quarantine/<reason>/` | validator Lambda | ops / data owner | weeks, human-reviewed | failed records kept by failure reason with a sidecar (line + rule id) |
| curated | `curated/` | batch/reconciliation job | reporting, Athena | long-term | deduplicated, conformed records for analytics |
