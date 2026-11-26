# M2 - Cancellation refund decision: architecture

**Author:** `rohitb` · **Date:** 2026-11-25 · **Module:** M2 · **Status:** ready for HITL review
(`docs/HITL_REVIEW_TEMPLATE.md`)

## 1. Requirement

When a ticket is cancelled, decide the refund (APPROVE / REJECT / MANUAL_REVIEW) using `day2/data/refund_rules.json`
and make sure the settlement team sees every decision.

- Volume: 2,000 cancellations/day (~60k/month), peak 50/min after a flight disruption.
- Order matters per PNR: a later correction (e.g. DCS no-show) must win over the original cancellation.
- Non-goals: no Kinesis, MSK, EC2 or servers; never log passenger names, emails, fares, FFQ, PNR or account info.
- Region `us-east-1`, serverless, SAM, Python 3.12, resource names `accelya-m2-rohitb-*`.

**Assumption:** the ticketing/DCS side can call SQS `SendMessage` through a cross-account IAM role and assigns a
`seq` per ticket at the source.

## 2. Flow

```
Ticketing / DCS (producer, cross-account role)
   │ H0  SendMessage
   ▼
SQS FIFO  accelya-m2-rohitb-cancellations.fifo ──(5 receives)──► FIFO DLQ
   │ H1  event source mapping
   ▼
Lambda  accelya-m2-rohitb-refund-decision   (the only Lambda)
   │ H2  TransactWriteItems
   ▼
DynamoDB  accelya-m2-rohitb-decisions   (DECISION#<event_id> + TICKET#<ticket_number>)
   │ H3  Streams (DECISION# items only)
   ▼
EventBridge Pipes (allow-list transform) ──► Pipe DLQ
   │
   ▼
EventBridge bus  accelya-m2-rohitb-settlement   (resource policy, 30-day archive)
   ├─ H4 rule: CURRENT decisions ──► settlement target (rule-target DLQ)
   ├─ H5 rule: all decisions ──► Firehose ──► S3 (audit) ──► Glue table ──► Athena
   └─ H6 rule: MANUAL_REVIEW / INVALID ──► SNS  accelya-m2-rohitb-alerts
CloudWatch alarms ──► same SNS topic
```

## 3. Business story (one ticket)

1. Agent cancels ticket `098-1234567890` on PNR `LM4K8D` (fare class K, scheduled, fare 4120.00); ticketing sends
   `evt-1002`, `seq` 1.
2. The event waits in PNR `LM4K8D`'s FIFO group; other PNRs run in parallel.
3. Lambda applies the rules: not no-show, not departed, K = SAVER -> **APPROVE, R-SAVER, 3090.00**.
4. DynamoDB stores the decision and marks it the ticket's current decision (version 1).
5. Settlement receives it; the audit store keeps it; finance can query totals in Athena.
6. DCS later flags a no-show: `evt-1003`, `seq` 2 -> **REJECT, R-NOSHOW**. It becomes current (version 2); settlement
   receives it with `seq` 2 and replaces the APPROVE.

## 4. Six service-boundary questions per hop

| Hop | Push/pull | Sync/async | Payload | Role (actions on ARNs) | Failure path | Correlation ID |
|-----|-----------|------------|---------|------------------------|--------------|----------------|
| H0 Producer -> SQS FIFO | Push | Sync | Cancellation JSON: event_id, seq, airline_code, pnr, ticket_number, fare_class, departure_status, no_show_flag, fare_amount (string), currency, correlation_id. No name/email/FFQ | Producer role: `sqs:SendMessage` on the queue; queue policy pins principal + `aws:SourceAccount` | Producer retries; FIFO dedup on `event_id` (5 min) | In body (`correlation_id`) |
| H1 SQS FIFO -> Lambda | Pull (ESM) | Sync per batch | SQS records, batch ≤ 10; `MessageGroupId = airline_code#pnr` | Lambda role: `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on the queue | ReportBatchItemFailures, stop at first failure; `maxReceiveCount` 5 -> FIFO DLQ; visibility timeout ≥ 6x function timeout | From body; minted `m2-<uuid>` if missing |
| H2 Lambda -> DynamoDB | Push | Sync | Transaction: `DECISION#<event_id>` (conditional on absence, payload hash, status, seq) + `TICKET#<ticket_number>` (update only if `seq > stored_seq`) | Lambda role: `dynamodb:PutItem`, `UpdateItem`, `ConditionCheckItem` on the table | Same id + same hash = success (no-op); same id + new hash = ERROR + MANUAL_REVIEW; stale seq = stored as SUPERSEDED | Stored on both items |
| H3 Streams -> Pipes -> bus | Pull (Pipes polls) | Async | Allow-listed decision: event_id, ticket_number, seq, status, decision, refund_rule_id, refund_amount, currency, correlation_id | Pipe role: stream read on the table stream; `events:PutEvents` on the bus | Pipe retry limit + Pipe DLQ; IteratorAge alarm; stream retention 24 h | In event detail |
| H4 Bus -> settlement | Push (rule) | Async | Same allow-listed decision, `status = CURRENT` only | EventBridge rule role scoped to the target ARN | Rule retry 24 h -> rule-target DLQ; bus archive for replay; settlement applies latest `seq` per ticket | In event detail |
| H5 Bus -> Firehose -> S3 | Push | Async (buffered 900 s) | All decisions, GZIP JSON, partitioned by date | Firehose role: `s3:PutObject` on the audit prefix, `kms:GenerateDataKey` on the key | Firehose retry, S3 error prefix | In each record |
| H6 Bus -> SNS | Push (rule) | Async | Identifiers only: event_id, ticket_number, status, correlation_id | `sns:Publish` on the topic | SNS retries; subscriber DLQ | In message |

## 5. Data model (DynamoDB)

| Item | Key | Attributes | Purpose |
|------|-----|------------|---------|
| Decision log | `pk = DECISION#<event_id>` | seq, ticket_number, status (CURRENT / SUPERSEDED / INVALID / MANUAL_REVIEW), decision, refund_rule_id, refund_amount, payload_hash, correlation_id, expires_at | One record per event; the only items streamed |
| Ticket state | `pk = TICKET#<ticket_number>` | current_event_id, current_seq | Version guard so a stale or redriven event never overrides a later one |

TTL on `expires_at` per the settlement retention policy (open question). Point-in-time recovery on.

## 6. Failure handling

| Case | Handling |
|------|----------|
| Duplicate delivery | FIFO dedup (5 min) + conditional put on `DECISION#<event_id>` (permanent) |
| Malformed body / missing field | Terminal `INVALID` record, no retry loop; alert via SNS |
| Same event_id, different content | ERROR log, `MANUAL_REVIEW`, alert |
| Correction before original, or DLQ redrive after correction | Ticket `seq` guard: older event stored as `SUPERSEDED`, never becomes current |
| Lambda timeout mid-batch | Batch retried; transaction is atomic and idempotent |
| Pipe or settlement delivery failure | Pipe DLQ, rule-target DLQ, bus archive replay, alarms; daily reconciliation of DynamoDB vs settlement via Athena |

## 7. Security

- No public endpoint. Ingest only through the producer's cross-account role (queue policy pinned to principal and
  account). Bus resource policy allows only this account's Pipe.
- PII: producer sends no name/email/FFQ; settlement, SNS and S3 get allow-listed fields only. No `pnr` in logs.
- Encryption: one customer-managed KMS key for SQS, DynamoDB, Firehose and S3; S3 Block Public Access on; Athena
  results bucket encrypted.
- IAM: one role per hop, named actions on ARN-scoped resources (table 4); Pipes trust policy with `aws:SourceArn`.
- Retention: logs 30 days, archive 30 days, S3 lifecycle expiry, DynamoDB TTL.

## 8. Observability

- Logs follow `docs/LOGGING_CONTRACT.md` (service `accelya-m2-cancellation`, `correlation_id` on every line, ERROR with
  `error_type`); checked by `day2/scripts/check_logs.py`.
- One correlation_id: producer body -> DynamoDB items -> settlement event -> S3 audit record.
- Alarms -> SNS: DLQ depth > 0, ApproximateAgeOfOldestMessage, Lambda errors, IteratorAge, Pipe failures,
  rule FailedInvocations.

## 9. Cost (us-east-1 list prices, ~60k events/month, no free tier, estimates)

| Item | $/month |
|------|---------|
| SQS FIFO + DLQ | ~0.10 |
| Lambda | ~0.04 |
| DynamoDB on-demand (transaction = 4 WRU/event) | ~0.15 |
| Pipes polling the stream | ~1.50-4.00 |
| EventBridge bus + archive | ~0.07 |
| Firehose + S3 + Athena (partition projection, no crawler) | ~0.05-0.15 |
| KMS key | 1.00 |
| CloudWatch logs + alarms (≤ 10 free) | ~0.05-0.10 |
| **Total** | **~$3-5.50** (~$1.80-2.50 with a stream-triggered Lambda instead of Pipes) |

## 10. Out of scope (beyond `refund_rules.json`)

Involuntary refunds (`cancel_reason_code`), coupon-level refunds, taxes/YQ/YR and filed penalties, voids and
exchanges, BSP/agency routing, First class fare family, per-currency decimals (fixtures are INR only).

## 11. Open questions for review

1. How does the source actually send cancellations (SQS call, event, webhook, file)? Decides H0.
2. Is `seq` per ticket available from ticketing/DCS?
3. Settlement retention period (drives TTL and S3 expiry).
4. Verify in AWS docs: Pipes DLQ for a DynamoDB stream holds full records or only pointers; Pipes stream-polling billing.
5. The Day 2 Lab 3 handler is standard-SQS only; FIFO stop-at-first-failure must be added before this design is built.
