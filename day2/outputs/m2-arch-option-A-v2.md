# M2 Option A (v2): SQS FIFO → Lambda → DynamoDB + SNS

> **Superseded by `m2-arch-option-A-v3.md`.** v2 has known correctness bugs and AGENTS.md violations. Kept for history only.

Revision of Option A to close the gaps found in `day2/reviews/HITL_REVIEW-m2-arch-variants.md`
(Observability 2→3, Scalability 2→3, Cost 2→3). The hops and the six-question answers are unchanged
except where marked **[v2]**.

**Summary:** strict per-`pnr` ordering on a FIFO queue, one Lambda decides, DynamoDB is the idempotent
system of record, SNS tells settlement. Capacity follows traffic (no idle servers).

**Services in order:** SQS FIFO (`MessageGroupId=pnr`, `MessageDeduplicationId=ticket_number`) + DLQ → Lambda → DynamoDB → SNS.

## Hops (six-question answers)

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure | 6 Corr. ID |
|---|---|---|---|---|---|---|
| Producer → SQS FIFO | Push | Sync `SendMessage`, 5 s | Cancellation JSON v1, additive changes only | `sqs:SendMessage` on the queue ARN | Producer retries with the same dedup id | Attribute `correlation_id` |
| SQS → Lambda | Pull | Async to caller, Lambda timeout 30 s | SQS event | `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on the queue ARN | `ReportBatchItemFailures`; FIFO: return first failure and every later `messageId`; `maxReceiveCount` 3 then DLQ | Read from attribute, mint if absent, on every log line |
| Lambda → DynamoDB | Push | Sync | Decision item: `pnr`, `ticket_number`, `decision`, `refund_rule_id`, `refund_amount` | `dynamodb:PutItem` on the table ARN, Condition `dynamodb:LeadingKeys` limited to this table's key | Conditional put `attribute_not_exists(ticket_number)` = idempotent; throttle retried by SDK then record retried | Item attribute |
| Lambda → SNS | Push | Sync | Ids-only decision event | `sns:Publish` on the topic ARN | Failure fails the record; retry is safe (idempotent put) | SNS message attribute |

## Changes in v2

### Observability (target 3)
- Use **Powertools for AWS Lambda Logger**, configured with a custom formatter so records match `docs/logging-contract.json` (`ts`, not `timestamp`; no `location`). Proof: `python3 day2/scripts/check_logs.py` returns CONFORMANT.
- **EMF metrics** (namespace `accelya-m2-cancellation`, dimensions limited to `service` and `decision`, so cardinality stays low):
  `CancellationDecided` (count), `DuplicateSuppressed` (count), `BatchItemFailures` (count), `ProcessingLatencyMs`.
- **Classified errors:** every failure logs `error_type` from a fixed set (`InvalidCancellation`, `DuplicateTicket`, `DynamoThrottle`, `SnsPublishFailed`).
- **Alarms:** DLQ depth > 0, `ApproximateAgeOfOldestMessage` > 5 min.

### Scalability (target 3)
- **Idempotent:** conditional write on `ticket_number` is the double-refund guard (FIFO dedup only lasts 5 minutes).
- **Bounded memory:** 256 MB, batch size 10, no accumulation across records.
- **Tested at 10× before PASS:** replay 500 events/min for 10 min (10× the 50/min peak) using synthetic fixtures, including hot-`pnr` groups with 5 events each and 5% duplicate `ticket_number`s.
  Pass criteria: zero double refunds, order preserved per `pnr`, zero throttles, DLQ empty, p95 latency under 2 s.
- Reserved concurrency capped (10) so a surge cannot exhaust the sandbox's Lambda limits.
- *Status:* this is a test plan. The Scalability 3 anchor needs the test **run and recorded**.

### Cost (target 3)
- Lambda on **arm64, 256 MB**, SQS batch size 10 to cut request counts. No always-on resources.
- **Cost per record (estimate, us-east-1 list prices, verify before quoting):** roughly 3–4 USD per million records.
  Breakdown: SQS FIFO ≈ 1.5 (send+receive+delete unbatched), Lambda ≈ 0.6, DynamoDB on-demand write ≈ 0.6–1.25, SNS ≈ 0.5.
  At 1,000 events/day ≈ 0.1 USD/month.
- **Measurable:** the EMF `CancellationDecided` count divided by the month's bill for these four services gives actual cost per record.
- **Sampling:** DEBUG logs off in deployed functions; INFO per record kept (needed for settlement traceability).

## Risks
- One poison message blocks its `pnr` group until it reaches the DLQ (mitigated by `maxReceiveCount` 3 and the DLQ alarm).
- Powertools default log shape does not match the contract; the custom formatter must be verified with the checker.
