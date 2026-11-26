# M2 Option A (v3): SQS FIFO → Lambda → DynamoDB → SNS

Supersedes v2. Fixes the three correctness bugs and the AGENTS.md convention misses found in the critical review.
Status: **design only, not built, not load-tested.** Scores in `day2/reviews/HITL_REVIEW-m2-arch-variants.md` are the AI's draft until a human reviews.

**Summary:** strict per-`pnr` order on a FIFO queue; one Lambda decides; DynamoDB is the idempotent, append-only
record of decisions; SNS fans decisions out to settlement. Serverless, no idle compute.

```
Producer ─► SQS FIFO ─► Lambda ─► DynamoDB (decisions) ─► (status PENDING→PUBLISHED)
  (role)   group=pnr      │            ▲ conditional put on (pnr, event_id)
           dedup=event_id │
              │           └─► SNS topic ─► settlement SQS queue ─► settlement consumers
              ▼
        FIFO DLQ (3 receives)         CloudWatch Logs ─► metric filters ─► alarms ─► ops SNS topic
```

## 1. Resources and conventions (AGENTS.md L12, L18, L20)

Region `us-east-1`. Everything is defined in one SAM `template.yaml`; no console changes. `<learner>` is the learner id.

| Resource | Name |
|---|---|
| SQS FIFO queue / DLQ | `accelya-m2-<learner>-cancellations.fifo` / `accelya-m2-<learner>-cancellations-dlq.fifo` |
| Lambda | `accelya-m2-<learner>-cancellation-decider` (python3.12, arm64, 256 MB, timeout 30 s) |
| DynamoDB table | `accelya-m2-<learner>-decisions` |
| SNS topics | `accelya-m2-<learner>-decisions` (to settlement), `accelya-m2-<learner>-ops-alerts` |
| Settlement queue | `accelya-m2-<learner>-settlement-decisions` (standard SQS, subscribed to the decisions topic) |

Lambda environment variables (no ids or ARNs in code): `TABLE_NAME`, `TOPIC_ARN`, `LEARNER_ID`, `CONTRACT_VERSION`.
Airline vocabulary only: the stored row is a **decision record**, never an "item".

## 2. Message contract v1 (data minimisation)

Producer sends only what the decision needs: `event_id`, `event_type`, `pnr`, `ticket_number`, `airline_code`,
`fare_class`, `departure_status`, `no_show_flag`, `fare_amount`, `currency`, `cancellation_timestamp`.
**No `passenger_name` or `contact_email`.** Message attribute `correlation_id`.
Consumer validates on receipt: `pnr` `^[A-Z0-9]{6}$`, `event_id` `^[A-Za-z0-9-]{1,40}$`, `fare_amount` a non-negative Decimal.

## 3. Decision record (DynamoDB)

- Key: partition `pnr`, sort `event_id`. Attributes: `ticket_number`, `airline_code`, `fare_class`, `decision`, `refund_rule_id`,
  `rules_version` (from `refund_rules.json`), `refund_amount` (**string**, `Decimal`, `ROUND_HALF_UP`, 2 dp), `currency`,
  `settlement_date`, `cancellation_timestamp`, `status` (`PENDING` / `PUBLISHED`), `correlation_id`.
- GSI `settlement_date-index` (hash `settlement_date`) so settlement can query by day.
- Encryption at rest, point-in-time recovery on, deletion protection on. Records are never overwritten.

## 4. Processing algorithm (fixes critical bugs 1 and 2)

1. Parse and validate. Permanent bad input is `InvalidCancellation` (see section 6).
2. **Idempotency key is `(pnr, event_id)`**, not `ticket_number`. SQS `MessageDeduplicationId = event_id`.
   A second legitimate event on the same ticket (partial then full cancel) is therefore processed, not dropped.
3. Query the `pnr` partition (strongly consistent) for earlier records. This gives two guards:
   - **Double-refund guard:** if an earlier record for the same `ticket_number` is an APPROVE with a refund, the new event becomes `MANUAL_REVIEW` (`R-UNKNOWN`-class handling, WARNING).
   - **Out-of-order guard:** if `cancellation_timestamp` is older than the latest record for the `pnr`, the event becomes `MANUAL_REVIEW` (WARNING).
4. Decide with the existing rules (first match wins).
5. Conditional put `attribute_not_exists(pnr)` with `status=PENDING`.
   - Success: continue.
   - `ConditionalCheckFailed`: read the record. `PUBLISHED` means skip, log `cancellation.duplicate` at INFO. `PENDING` means a previous attempt stopped before publishing, so go to step 6.
6. Publish to SNS (message attributes `correlation_id`, `decision`; ids only, no names or amounts).
7. Update `status=PUBLISHED`.

Retry after an SNS or DynamoDB failure is now genuinely safe: a `PENDING` record is always re-published, so settlement cannot miss a decision.
SNS delivery is at-least-once, so settlement consumers must dedupe on `event_id`.

## 5. Failure handling and the DLQ (fixes critical bug 3)

- **Transient errors** (`DynamoThrottle`, `DynamoError`, `SnsPublishFailed`): return the failed `messageId` and every later `messageId` in the batch (FIFO rule). The group stays blocked and in order while SQS retries.
- **Permanent errors** (`InvalidCancellation`): if `pnr` and `event_id` are readable, write a `MANUAL_REVIEW` decision record and **delete** the message (no blocked group, no gap). If they are not readable, log ERROR and fail the message to the DLQ; the alarm fires and the runbook says to check that `pnr` before redriving.
- DLQ is a **FIFO queue**, retention 14 days, `maxReceiveCount` 3. Redrive is a manual, reviewed step.
- Unexpected exceptions are not caught (AGENTS L24 says catch specific ones): the invocation fails and the batch is retried; the Lambda `Errors` alarm covers it.

## 6. Queue settings, security and IAM

- Visibility timeout **180 s** (6× the Lambda timeout), retention 4 days, `ReportBatchItemFailures` on, event source mapping `MaximumConcurrency` 5 (replaces the reserved-concurrency idea, which could push throttled messages into the DLQ).
- SSE on SQS (queue and DLQ), SNS and DynamoDB.
- **Queue resource policy:** allow `sqs:SendMessage` only to the producer role ARN; deny any request without `aws:SecureTransport`.
- **Lambda role (named actions, ARN-scoped, no wildcards):**
  `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the queue ARN;
  `dynamodb:PutItem`, `dynamodb:Query`, `dynamodb:UpdateItem` on the table ARN (Query also on the GSI ARN);
  `sns:Publish` on the decisions topic ARN;
  `logs:CreateLogStream`, `logs:PutLogEvents` on the function's log group ARN.
  The v2 `LeadingKeys` condition is removed (it restricts partition-key values and added nothing here).
- PII: nothing passenger-identifying is sent, stored or logged. `pnr` and `ticket_number` are treated as sensitive identifiers (logged only where the contract allows).

## 7. Hops: six-question answers (AGENTS L29)

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure | 6 Corr. ID |
|---|---|---|---|---|---|---|
| H1 Producer → SQS FIFO | Push | Sync, 5 s timeout | Contract v1 (section 2), additive changes only | Producer role: `sqs:SendMessage` on the queue ARN | Retry with the same `event_id`; SQS dedups for 5 min | Message attribute `correlation_id` |
| H2 SQS → Lambda | Pull (mapping) | Async to caller, 30 s timeout | SQS event, AWS-owned | Mapping role: `ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` | Partial-batch failure rules (section 5), 3 receives then DLQ | Read attribute, mint `m2-<uuid>` if absent, on every log line |
| H3 Lambda → DynamoDB | Push | Sync | Decision record (section 3) | `PutItem`, `Query`, `UpdateItem` on table (+GSI) ARN | Conditional put gives idempotency; throttles retried by the SDK, then the record is retried | Record attribute |
| H4 Lambda → SNS | Push | Sync | Ids-only decision event | `sns:Publish` on topic ARN | Failure leaves the record `PENDING`; the retry re-publishes | SNS message attribute |
| H5 SNS → settlement queue | Push | Async | Same event | Topic needs `sqs:SendMessage` on the settlement queue (queue policy, `aws:SourceArn` = topic) | SNS retries, then its DLQ; consumers dedupe on `event_id` | Preserved in message attributes |
| H6 SQS → DLQ | Push (redrive policy) | Async | Original message | SQS service redrive, scoped by the policy | Retained 14 days, alarm on depth > 0, manual reviewed redrive | Preserved in message attributes |
| H7 Lambda → CloudWatch Logs | Push | Async | JSON log lines per `docs/logging-contract.json` | `logs:CreateLogStream`, `PutLogEvents` on the log group ARN | Lost lines are visible only via the Errors alarm | `correlation_id` on every line |
| H8 CloudWatch alarm → ops SNS topic | Push | Async | Alarm state change | Alarm needs `sns:Publish` on the ops topic ARN | Alarm retried by CloudWatch | Alarm name carries the learner id |

## 8. Observability (AGENTS L21 to L24)

- **Logging:** `json.dumps` records with exactly the contract keys (`ts`, `level`, `service`, `correlation_id`, `boundary`, `message`, plus allowed extras such as `message_id`, `event_id`, `pnr`, `decision`, `refund_rule_id`, `learner_id`, `error_type`). Powertools Logger is deferred to Day 4 (contract section 5), with the formatter verified by `python3 day2/scripts/check_logs.py`.
- **No raw exception text, no `logger.exception`:** log only `error_type`.
- **Error types (fixed set):** `InvalidCancellation` (ERROR), `DynamoThrottle` / `DynamoError` / `SnsPublishFailed` (ERROR, transient), `OutOfOrderEvent` / `DoubleRefundRisk` (WARNING, routed to MANUAL_REVIEW), duplicates logged INFO as `cancellation.duplicate`.
- **Metrics without EMF:** CloudWatch **metric filters** on the existing log fields (`cancellation.decided` by `decision`; ERROR by `error_type`). No contract change needed.
- **Alarms** (notify the ops SNS topic): DLQ depth > 0; oldest message age > 5 min; Lambda `Errors` and `Throttles` > 0; DynamoDB throttled requests > 0.

## 9. Test plan (not yet run)

10× load: 500 events/min for 10 min, hot-`pnr` groups of 5, 5% duplicate `event_id`s. Plus fault injection:
SNS failure after the put, DynamoDB throttle, poison message, Lambda timeout mid-batch, out-of-order timestamps, second event on one `ticket_number`.
Pass: zero double refunds, order preserved per `pnr`, every decision published once or more and deduped by `event_id`, DLQ empty except injected poison, p95 under 2 s, `check_logs.py` CONFORMANT.

## 10. Cost (estimate, list prices from memory, verify before quoting)

Per record: 1 SQS send/receive/delete set, 1 Lambda invocation, 2 DynamoDB writes (put + status update) plus a GSI write and 1 query, 1 SNS publish, 1 SNS→SQS delivery.
Roughly **$5–8 per million records**, about $0.15–0.25 a month at 1,000 events a day.
Fixed: about 8 filter metrics (~$2.40), 6 alarms (~$0.60), logs (~$0.03). **Total about $3 a month.**
Cost per record is measurable from the `cancellation.decided` metric against the tagged resources' bill.

## 11. Open decisions for the human reviewer

1. **Business rule not in `refund_rules.json`:** the double-refund guard (same `ticket_number` already refunded → MANUAL_REVIEW) and the out-of-order guard are my additions. Confirm them with the business.
2. **Gap detection:** timestamps catch out-of-order events but not a lost event. A producer-supplied per-`pnr` sequence number would; this needs a producer change.
3. **Currency exponents:** 2 dp is assumed. Currencies with other exponents should go to MANUAL_REVIEW until handled.
4. **Settlement consumer:** the SNS→SQS settlement queue is my default; confirm who consumes decisions and how they query history.
5. **handler.py:** the current handler targets a standard queue and has none of section 4 or 5. It needs a FIFO rewrite before this design is built.
