# HITL_REVIEW — m2-solution-architecture

> Review of `day2/outputs/m2-solution-architecture-chandra.md` (design artefact) plus the
> implemented handler it describes (`day2/src/sample-demo/`). Synthetic data only.

---

**Artefact:** `day2/outputs/m2-solution-architecture-chandra.md`
**Reviewer:** `chandra` (self-review — a second human should countersign before merge)
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-3 (cancellation refund decisions)`

---

## The score (0–3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                                        |
|----------------|-------|----------------------------------------------------------------------|
| Correctness    | 3     | Rules + batchItemFailures + DLQ + idempotent write all specified; handler passes 6/6 harness |
| Security       | 2     | ARN-scoped named actions everywhere; PII excluded from notifications and logs; PII fields not formally tagged |
| Observability  | 2     | Contract logs + classified errors + DLQ alarm; EMF metrics deferred to M9 by course design |
| Scalability    | 2     | Conditional PutItem idempotency + bounded batches; 10× analyzed on paper, not load-tested |
| Cost           | 3     | Per-day cost derived with unit prices; workload-appropriate; sanity bound respected |
| **TOTAL**      | **12/15** |                                                                  |

**Logging contract (Day 2, mandatory):** `python3 day2/tests/run_and_check.py day2/src/sample-demo/handler.py`
→ 6/6, including `check_logs` CONFORMANT over live-emitted lines. Design itself names `correlation_id`
on every record and `error_type` on ERROR records.

**Verdict:** PASS (borderline) — merge the design; the follow-ups below are build tasks, not design changes.

---

## Per-dimension detail

### Correctness — `3/3`

- Spec match: APPROVE/REJECT/MANUAL_REVIEW per `refund_rules.json`, first-match-wins, Decimal
  ROUND_HALF_UP money. Verified by harness, not just asserted.
- Failure semantics correct end-to-end: `ReportBatchItemFailures` (partial batch), redrive to DLQ at
  `maxReceiveCount` 3, conditional `attribute_not_exists(event_id)` for at-least-once delivery.
- Ordering requirement handled consciously: standard queue justified by "PNR ordering not required",
  not chosen by accident — the fix that distinguishes this from the Bedrock draft (which used FIFO).
- Edge cases named: malformed JSON vs missing field produce different `error_type`s; duplicate
  delivery is a no-op, not an error.

### Security — `2/3`

- One execution role, named verbs, ARN-scoped resources; no `*`, no managed `AWSLambdaBasicExecutionRole`.
- SNS payload restricted to `{event_id, decision, refund_rule_id, pnr}` — fare_amount never crosses
  a notification boundary; contract keys only in logs.
- SSE-SQS on both queues; DynamoDB encrypted at rest by default.
- Not 3: PII fields (pnr in DynamoDB) are not tagged/classified, and no condition keys
  (e.g. `aws:SecureTransport`, `aws:SourceAccount`) appear on any statement.

### Observability — `2/3`

- `correlation_id` from the message attribute (minted if absent) on every line; `message_id` per record.
- ERROR records carry classified `error_type` (`MalformedJson`, `InvalidCancellation`) — verified.
- DLQ has a CloudWatch alarm specified (`ApproximateNumberOfMessagesVisible > 0`).
- Not 3: no EMF metrics (decision counts by rule, refund totals); the course defers this to Module 9.

### Scalability — `2/3`

- Bounded: BatchSize 10, streaming decision logic, no per-event memory growth.
- Idempotent: conditional PutItem on `event_id`; replays and duplicate sends are safe.
- Batch window (≤10 s) explicitly bounds low-volume latency; at 50/min the batch fills every ~12 s.
- Not 3: the 10× claim is arithmetic, not a load test.

### Cost — `3/3`

- $0.0055/day derived with stated unit prices × volumes — passes the ~$5/month sanity bound.
- Right-sized: one function, on-demand DynamoDB, no standing capacity. 10× modeled (~$0.03/day)
  and still cheapest of the three options.

---

## Follow-ups before merge / deploy

1. **IaC gap** — `sample-demo/template.yaml` lacks the DynamoDB table, SNS topic, DLQ alarm and
   settlement subscription that §3/§10 describe. Design says what; template doesn't yet.
2. **Settlement subscription** — the SNS fan-out's consumer (queue, email, HTTPS) is named but not
   chosen. Pick one and add it to the template.
3. **PII classification** — tag `pnr`/`ticket_number` columns in the decisions-table doc and note
   the retention story (DynamoDB TTL or none).
4. **Second reviewer** — this is a self-review; the template intends an independent human.

**Next iteration prompt addition:**
> Extend `day2/src/sample-demo/template.yaml` with the DynamoDB decisions table (pk `event_id`,
> on-demand), the SNS settlement topic with one chosen subscription, and the DLQ-depth CloudWatch
> alarm. Add `dynamodb:PutItem` + `sns:Publish` to the role, ARN-scoped. Keep the logging contract.

---

## What would drop this below PASS

- Any `Action: "*"` or `Resource: "*"` reaching the template (Security → 0).
- Logging fare_amount or the record body (Observability → 0, contract violation).
- Removing the conditional PutItem (Scalability → 1: idempotency was the M1 lesson).
- Deploying without the DLQ alarm (Correctness of the failure path → revise).

---

*Pass bar: ≥12/15 AND every dim ≥2. Scored 12/15 — PASS with tracked follow-ups.*
