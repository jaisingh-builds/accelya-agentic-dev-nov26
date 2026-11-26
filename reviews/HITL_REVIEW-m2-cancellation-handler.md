# HITL_REVIEW - m2-cancellation-handler

---

**Artefact:** `day2/src/cancellation/handler.py`
**Reviewer:** `amol.more`
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-3 (cancellation refund decision)`

---

## Architecture (Option C - SQS FIFO + Streams)

Selected option from `day2/outputs/m2-arch-options-amol.more.md` and class whiteboard discussion.

### Full flow (whiteboard + Option C)

```
API Gateway
  -> SQS FIFO (group=PNR, dedup=event_id) -> DLQ (maxReceiveCount=3)
     -> Lambda `decide` (ReportBatchItemFailures)
        -> DynamoDB `refund_decisions` (condition: event_id not seen)
           -> DynamoDB Streams
              -> EventBridge
                 -> Firehose + S3 + Glue (analytics/Excel reporting)
                 -> CloudWatch Alarms (DLQ depth, error rate)
                 -> SNS (notifications)
              -> Lambda `publish`
                 -> SQS `settlement-feed`
```

**Volume:** ~60K/hour peak, ~2.5K/day steady state

**Estimated runtime cost:** ~$2.90/month at 2K events/day (DynamoDB Streams polling = 72% of cost)

### Service-boundary 6Q (hop: SQS FIFO -> Lambda decide)

| # | Answer |
|---|---|
| 1 | Pull. Lambda event source mapping polls the FIFO queue. |
| 2 | Sync per batch. Lambda processes up to 10 records per invocation. |
| 3 | Batch of SQS records, each body is cancellation JSON (event_id, fare_class, departure_status, no_show_flag, fare_amount). |
| 4 | Lambda role: sqs:ReceiveMessage, sqs:DeleteMessage, sqs:GetQueueAttributes on this queue ARN only. |
| 5 | ReportBatchItemFailures: failed record + all later records in same MessageGroupId returned. DLQ after maxReceiveCount=3. |
| 6 | Message attribute `correlation_id`; minted as `m2-{uuid}` if absent. Carried on every application log line. |

---

## The score (0-3 per dim - PASS = >=12/15 AND every dim >=2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 3     | All 8 decisions match expected; edge cases handled (bad JSON, missing fields, invalid Decimal); FIFO ordering enforced |
| Security       | 2     | No wildcards, no secrets in code, LEARNER_ID from env var; PII never logged (names, emails, fare amounts stripped) |
| Observability  | 2     | JSON structured logs, all 6 required keys on every line, classified errors with error_type, correlation_id traceable per record |
| Scalability    | 2     | Bounded memory (stream processing), FIFO ordering preserved after failure, idempotent via event_id condition |
| Cost           | 3     | Right-sized: Lambda + SQS FIFO + DynamoDB on-demand; ~$2.90/month at 2K/day; cost per record measurable |
| **TOTAL**      | **12/15** |                                                 |

**Logging contract:** `python3 day2/scripts/check_logs.py` -> 11 records, 0 violations. CONFORMANT.

**Test harness:** `python3 day2/tests/run_and_check.py` -> 6/6 checks pass.

**Verdict:** PASS

---

## Per-dimension detail

### Correctness - `3/3`

- 8 refund decisions match expected_decisions.json exactly (amounts, rule IDs, decision codes)
- m-07 (bad JSON) and m-08 (missing fields) correctly routed to batchItemFailures with ERROR logs
- First-match-wins rule evaluation: R-NOSHOW > R-DEPARTED > fare family > R-UNKNOWN
- Money: Decimal with ROUND_HALF_UP to 2dp, never float
- no_show_flag checked with `is True` (rejects truthy strings like `"false"`)
- InvalidOperation from malformed fare_amount caught and logged at ERROR
- FIFO: after a failure, all later records in the same MessageGroupId are failed (ordering preserved)

---

### Security - `2/3`

- No `Action: "*"` or `Resource: "*"` in IAM (handler has no IAM definitions; SAM template scopes these)
- No secrets, keys, or account IDs in code; LEARNER_ID from environment variable
- PII: passenger_name, contact_email, fare_amount never appear in any log line (check 5 passes)
- Not 3: no explicit PII tagging or data classification annotations in code

---

### Observability - `2/3`

- Structured JSON logging: every line has ts, level, service, correlation_id, boundary, message
- All keys conform to docs/logging-contract.json value patterns (11 records, 0 violations)
- Classified errors: ERROR lines carry error_type (ValueError, JSONDecodeError, InvalidOperation)
- correlation_id: passed through from SQS message attribute; minted as `m2-{uuid}` for m-10; batch.complete uses context.aws_request_id
- Not 3: no EMF metrics, no duration_ms timing, no cold_start detection

---

### Scalability - `2/3`

- Bounded memory: processes one record at a time from the batch, no accumulation
- FIFO ordering: failed_groups set tracks MessageGroupId; subsequent records in the same group are failed without processing
- Idempotent at DynamoDB layer: PutItem with condition `attribute_not_exists(event_id)` absorbs replays
- Not 3: no 10x scale test executed; no load test evidence

---

### Cost - `3/3`

- Lambda + SQS FIFO + DynamoDB on-demand is workload-appropriate for 2K events/day
- FIFO queue costs slightly more than Standard (~$0.00000050 vs $0.00000040 per request) but delivers per-PNR ordering
- Two Lambda invocations per cancellation (decide + publish) + stream reads
- Total estimated: ~$2.90/month at steady state (DynamoDB Streams polling dominates at 72%)
- Bedrock architecture generation: ~$0.02 per script call

---

## Defects found and fixed (pre-review)

Six defects were identified against AGENTS.md and fixed before this review:

| # | Line | Severity | Defect | Fix |
|---|------|----------|--------|-----|
| D1 | 110 | CRITICAL | `_compute_refund_amount()` outside try/except; Decimal crash kills entire batch | Moved inside try block; added `except InvalidOperation` |
| D2 | 94-106 | CRITICAL | FIFO ordering not enforced after parse failure | Added `failed_groups` set; later records in same MessageGroupId auto-failed |
| D3 | 16 | HIGH | SERVICE missing `<learner>` segment | Changed to `accelya-m2-{LEARNER_ID}-cancellation` from env var |
| D4 | 128 | HIGH | Literal `"batch"` as correlation_id | Changed to `context.aws_request_id` with UUID fallback |
| D5 | 65 | MEDIUM | `no_show_flag` truthy check accepts wrong types | Changed to `is True` explicit check |
| D6 | N/A | PROCESS | HITL review template not filled | This document |

---

## Council of 7 Judges - design review summary

Design scored **5.7/7** across 7 dimensions. Priority findings addressed:

1. **FIFO batch ordering** (Judge: Reliability) - Fixed in D2
2. **batch.complete correlation_id** (Judge: Observability) - Fixed in D4
3. **DLQ alarm** (Judge: Operations) - Addressed in architecture (CloudWatch Alarms on DLQ depth, per whiteboard)

---

## Architecture decision record

**Why Option C over A and B:**

| Criterion | A: SQS Standard | B: EventBridge | C: SQS FIFO + Streams |
|---|---|---|---|
| Per-PNR ordering | No | No | Yes (MessageGroupId=PNR) |
| Failure isolation | Partial batch | Async retries | Partial batch + group ordering |
| Settlement visibility | Query table | Event feed | Event feed from table via Streams |
| Moving parts | 3 | 5 | 6 (+ EventBridge, Firehose per whiteboard) |
| Cost at 2K/day | Lowest | Low | Low-medium (~$2.90/month) |

**Decision:** Option C selected because per-PNR ordering is a hard requirement for settlement correctness. A re-booking must never overtake its cancellation. The additional cost (~$2.90/month vs ~$1.50 for Option A) is negligible against the business risk of out-of-order settlement.

---

*Pass bar: >=12/15 AND every dim >=2. Score: 12/15, all dims >=2. PASS.*
