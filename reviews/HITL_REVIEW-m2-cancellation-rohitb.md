# HITL_REVIEW - m2-cancellation-architecture-rohitb

---

**Artefact:** `day2/m2-cancellation-architecture-rohitb.md`
**Reviewer:** `amol.more`
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-3 (cancellation refund decision)`

---

## The score (0-3 per dim - PASS = >=12/15 AND every dim >=2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 3     | First-match-wins rules, `seq`-based version guard for corrections, TransactWriteItems for atomic decision+ticket update, SUPERSEDED handling |
| Security       | 3     | Cross-account role with `aws:SourceAccount`, CMK encryption, allow-listed fields only in Pipes transform, no PII in logs/events, per-hop ARN-scoped roles |
| Observability  | 2     | Logging contract referenced, correlation_id end-to-end, alarms on DLQ/IteratorAge/errors; no EMF metrics or duration_ms |
| Scalability    | 3     | Idempotent (FIFO dedup + conditional put + hash check), bounded memory, `seq` guard handles out-of-order corrections, DLQ redrive safe |
| Cost           | 3     | Itemised per-service breakdown, Pipes vs stream-triggered Lambda trade-off noted, KMS key cost included |
| **TOTAL**      | **14/15** |                                                 |

**Logging contract:** design artefact (no running code reviewed). Contract conformance stated but not machine-verified.

**Verdict:** PASS

---

## Per-dimension detail

### Correctness - `3/3`

- Refund rules match `refund_rules.json` exactly: NOSHOW > DEPARTED > fare family > UNKNOWN, first match wins.
- Business story walks through a complete ticket lifecycle including correction (no-show overrides cancellation).
- `seq`-based version guard: a stale event (lower `seq`) is stored as SUPERSEDED, never becomes current. This handles DLQ redrive after a correction has already arrived.
- Same `event_id` with different content detected via `payload_hash` and routed to MANUAL_REVIEW — a scenario most designs miss.
- TransactWriteItems makes DECISION + TICKET update atomic.
- Edge cases explicitly named: duplicate delivery, malformed body, stale seq, same-id-different-content.

---

### Security - `3/3`

- Cross-account producer role with `aws:SourceAccount` condition — not just principal pinning.
- One customer-managed KMS key for SQS, DynamoDB, Firehose, S3 — consistent encryption.
- Pipes allow-list transform: settlement and audit only see decision fields, never raw input.
- No PII: producer explicitly told not to send name/email/FFQ; `pnr` excluded from logs.
- Bus resource policy allows only this account's Pipe — no open ingress.
- S3 Block Public Access, Athena results bucket encrypted.
- Per-hop IAM roles with named actions on ARN-scoped resources (table in section 4).

---

### Observability - `2/3`

- Logging contract (`docs/LOGGING_CONTRACT.md`) referenced; `correlation_id` on every line; ERROR with `error_type`.
- End-to-end correlation: producer body -> DynamoDB items -> settlement event -> S3 audit record.
- Alarms: DLQ depth, ApproximateAgeOfOldestMessage, Lambda errors, IteratorAge, Pipe failures, rule FailedInvocations.
- Not 3: no EMF metrics, no `duration_ms` timing, no `cold_start` tracking; no machine-verified log output (design only).

---

### Scalability - `3/3`

- Idempotent at three levels: FIFO dedup (5 min), conditional put on `DECISION#<event_id>` (permanent), and `seq` guard on `TICKET#<ticket_number>`.
- Bounded memory: batch processing, no accumulation.
- `seq`-based version guard handles the hardest scenario: DLQ redrive of an older event after a correction has already been processed.
- MessageGroupId = `airline_code#pnr` — parallelism across PNRs, ordering within.
- Visibility timeout >= 6x function timeout noted (prevents phantom redelivery).

---

### Cost - `3/3`

- Itemised breakdown per service, including KMS key ($1/month).
- Pipes polling cost highlighted as the largest item ($1.50-4.00) — and the alternative (stream-triggered Lambda at $1.80-2.50 total) is noted.
- 60K events/month is realistic for 2K/day steady state.
- Firehose + S3 + Athena (partition projection, no crawler) is the right choice for audit at this volume.

---

## Strengths (what Rohit's design does well)

1. **`seq`-based version guard** — the TICKET item with `current_seq` ensures a stale or redriven event never overrides a later correction. Our design doesn't have this yet.
2. **Same-id-different-content detection** — `payload_hash` check catches data corruption or replayed events with mutations. Routes to MANUAL_REVIEW instead of silently accepting.
3. **TransactWriteItems** — atomic DECISION + TICKET update. Our design uses plain PutItem.
4. **Allow-list transform in Pipes** — settlement and audit only see approved fields, not the raw SQS payload.
5. **EventBridge archive (30 days)** — enables replay if settlement misses events. Our design doesn't include this.
6. **KMS customer-managed key** — single key for all services; our design uses AWS-managed keys.
7. **Open questions section** — explicitly calls out unknowns (how does source send? is `seq` available? retention policy?) rather than assuming.

## Areas for improvement

1. **No running handler code** — the design references `refund_rules.json` but doesn't include or reference a working Lambda implementation with passing tests.
2. **Logging not machine-verified** — claims contract conformance but no `check_logs.py` output shown.
3. **Pipes vs Lambda trade-off** — cost range is wide ($3-5.50); the stream-triggered Lambda alternative is cheaper and simpler but pushed to a footnote.
4. **`no_show_flag` type safety** — the design doesn't address the Python truthy bug (string `"false"` evaluates as truthy).
5. **Service name** — uses `accelya-m2-cancellation` in observability section (line 99), missing the `rohitb` learner segment from the naming convention established in section 1.

---

*Pass bar: >=12/15 AND every dim >=2. Score: 14/15, all dims >=2. PASS.*
