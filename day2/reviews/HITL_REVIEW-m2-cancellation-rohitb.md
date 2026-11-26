# HITL_REVIEW - m2-cancellation handler

**Artefact:** `day2/src/cancellation/handler.py`
**Reviewer:** `rohitb`
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-3`

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                                                        |
|----------------|-------|------------------------------------------------------------------------------------|
| Correctness    | 2     | Harness 6/6, but JSON-number fare loses digits via float; unknown `departure_status` -> APPROVE |
| Security       | 2     | No PII or fare in logs; no secrets; no IAM in scope; PII not tagged                |
| Observability  | 2     | Contract-conformant JSON, correlation_id on every line, ERROR has error_type; no metrics |
| Scalability    | 2     | Stateless, bounded per batch; one out-of-range fare crashes the whole batch        |
| Cost           | 2     | One Lambda per SQS batch, 1-2 log lines per record; cost per record not measured   |
| **TOTAL**      | 10/15 |                                                                                    |

**Logging contract:** `python3 day2/tests/run_and_check.py` (check 4) -> CONFORMANT, 13 records, 0 violations

**Verdict:** REVISE

---

## Defects

| # | Line | Failure | Fix |
|---|------|---------|-----|
| 1 | 79, 91 | Numeric `fare_amount` passes through float: 12345678901234567.89 x 75% -> `9259259175925926.00` (should be `...925.92`) | `json.loads(body, parse_float=Decimal)` |
| 2 | 94-95, 107 | `"fare_amount": "1e30"` -> `decimal.InvalidOperation` in `quantize`, uncaught -> whole batch retried | Cap amount in `parse_body`, raise `InvalidCancellation` |
| 3 | 87, 103 | `departure_status` `"DEPARTED"` / `null` -> APPROVE 100% (departed flight refunded) | Allow-list statuses; else `InvalidCancellation` |
| 4 | 124-143 | Only `InvalidCancellation` caught per record; any other error fails the batch | Catch specific errors per record, log ERROR with `error_type`, add to `batchItemFailures` |
| 5 | 47, 71 | `boundary` is `sqs->lambda` on internal lines | `internal` for `cancellation.decided`, `batch.completed` |
| 6 | 53-58 | Rules hand-copied from `refund_rules.json`; drift goes unnoticed | Bundle the JSON, load at init |
| 7 | 1-38 | Six service-boundary answers missing for `sqs->lambda` | Add 6Q table (role, DLQ, maxReceiveCount) |

Reproduced: 1, 2, 3 (local edge-case run, 2026-11-25).

---

## If REVISE: what to change

1. **Correctness** - fix defects 1 and 3.
2. **Scalability** - fix defects 2 and 4 so one bad record never fails the batch.

**Next iteration prompt addition:**
> Parse JSON with `parse_float=Decimal`. Reject out-of-range `fare_amount` and any `departure_status` outside the allowed set as InvalidCancellation. Catch errors per record so one bad record never fails the batch.

---

*Pass bar: ≥12/15 AND every dim ≥2.*
