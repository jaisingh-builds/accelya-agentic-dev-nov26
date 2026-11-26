# M2 - Cancellation refund decision: architecture (Option C)

**Author:** `amol.more` · **Date:** 2026-11-25 · **Module:** M2 · **Status:** ready for HITL review
(`docs/HITL_REVIEW_TEMPLATE.md`)

## 1. Requirement

When a ticket is cancelled, decide the refund (APPROVE / REJECT / MANUAL_REVIEW) using
`day2/data/refund_rules.json` and make sure the settlement team sees every decision.

- Volume: ~2,500 cancellations/day steady state, peak ~60K/hour after flight disruption.
- Order matters per PNR: a re-booking must not overtake its cancellation.
- Non-goals: no Step Functions, no Kinesis/MSK/EC2; never log passenger names, emails, fare amounts.
- Region `us-east-1`, serverless, SAM, Python 3.12, resource names `accelya-m2-amol-*`.

## 2. Selected option: C - SQS FIFO + DynamoDB Streams

Options A (SQS Standard) and B (EventBridge async) were evaluated and rejected because neither
guarantees per-PNR ordering. Full comparison in `day2/outputs/m2-arch-options-amol.more.md`.

## 3. Flow

```
API Gateway (future ingress point)
   |
   v
SQS FIFO  accelya-m2-amol-cancellations.fifo  ---(maxReceiveCount=3)--->  FIFO DLQ
   |  H1  event source mapping (batch <= 10)
   v
Lambda  accelya-m2-amol-refund-decision   (ReportBatchItemFailures)
   |  H2  PutItem (condition: event_id not seen)
   v
DynamoDB  accelya-m2-amol-decisions
   |  H3  DynamoDB Streams (new image)
   v
EventBridge
   |--- H4  rule: decisions ---> Lambda `publish` ---> SQS `settlement-feed`
   |--- H5  rule: all decisions ---> Firehose ---> S3 + Glue (audit/analytics/Excel reporting)
   |--- H6  rule: MANUAL_REVIEW ---> SNS  accelya-m2-amol-alerts
   v
CloudWatch Alarms (DLQ depth, error rate, IteratorAge) ---> same SNS topic
```

## 4. Business story (one ticket)

1. Agent cancels ticket `098-2304511902` on PNR `LM4K8D` (fare class K, scheduled, fare 4120.00).
   Producer sends `evt-1002` to SQS FIFO with `MessageGroupId = LM4K8D`, `MessageDeduplicationId = evt-1002`.
2. The event waits in PNR `LM4K8D`'s FIFO group; other PNRs process in parallel.
3. Lambda applies first-match-wins rules: not no-show, not departed, K = SAVER -> **APPROVE, R-SAVER, 3090.00**.
4. DynamoDB stores the decision (conditional on `event_id` not seen = idempotent).
5. DynamoDB Streams triggers EventBridge; settlement receives it via SQS `settlement-feed`.
6. Firehose archives to S3; Glue makes it queryable for finance reporting.

## 5. Six service-boundary questions per hop

| Hop | Push/pull | Sync/async | Payload | Role (actions on ARNs) | Failure path | Correlation ID |
|-----|-----------|------------|---------|------------------------|--------------|----------------|
| H0 Producer -> SQS FIFO | Push (SendMessage) | Sync | Cancellation JSON: event_id, airline_code, pnr, ticket_number, fare_class, departure_status, no_show_flag, fare_amount. No name/email. GroupId=PNR, DedupId=event_id | Producer role: `sqs:SendMessage` on this queue ARN only | SDK retries; FIFO dedup absorbs duplicates for 5 min | Message attribute `correlation_id` |
| H1 SQS FIFO -> Lambda | Pull (ESM) | Sync per batch (<=10) | SQS records, in order per MessageGroupId | Lambda role: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on queue ARN | ReportBatchItemFailures: failed record + all later in same group returned. DLQ after maxReceiveCount=3. Visibility timeout >= 6x function timeout | From message attribute; minted `m2-<uuid>` if absent |
| H2 Lambda -> DynamoDB | Push (PutItem) | Sync | Decision row: event_id, decision, refund_rule_id, refund_amount, correlation_id. No PII | Lambda role: `dynamodb:PutItem` on table ARN. Condition: `attribute_not_exists(event_id)` | SDK retries; condition fail = duplicate = no-op; InvalidOperation on bad fare_amount -> ERROR + batchItemFailures | Stored as attribute on the row |
| H3 Streams -> EventBridge | Pull (ESM) | Sync per batch | New image from Streams | Stream read role: `dynamodb:GetRecords`, `dynamodb:GetShardIterator`, `dynamodb:DescribeStream` on table stream ARN | Retries until success or record expires (24h). Bisect on failure. On-failure destination: SQS | Read from the row |
| H4 EventBridge -> Lambda publish -> SQS settlement | Push (rule) | Async | Decision (no PII): event_id, ticket_number, decision, refund_rule_id, refund_amount, correlation_id | `events:PutEvents` on bus; `sqs:SendMessage` on settlement queue | EventBridge retry 24h; rule-target DLQ | In event detail -> message attribute |
| H5 EventBridge -> Firehose -> S3 | Push (rule) | Async (buffered) | All decisions, GZIP JSON, partitioned by date | Firehose role: `s3:PutObject` on audit prefix | Firehose retry; S3 error prefix | In each record |
| H6 EventBridge -> SNS | Push (rule) | Async | Identifiers only: event_id, ticket_number, decision, correlation_id | `sns:Publish` on topic ARN | SNS retries; subscriber DLQ | In message |

## 6. Handler implementation (`day2/src/cancellation/handler.py`)

The Lambda handler processes SQS FIFO batches with `ReportBatchItemFailures`. Key design decisions:

### Refund rules (first-match-wins from `refund_rules.json`)

| Rule ID | Condition | Decision | Refund % |
|---------|-----------|----------|----------|
| R-NOSHOW | `no_show_flag is True` | REJECT | 0 |
| R-DEPARTED | `departure_status == "departed"` | REJECT | 0 |
| R-FLEX | fare class in {Y, B, M} | APPROVE | 100 |
| R-BUSINESS | fare class in {J, C, D} | APPROVE | 100 |
| R-SAVER | fare class in {K, L, Q, V} | APPROVE | 75 |
| R-UNKNOWN | fare class not in any family | MANUAL_REVIEW | 0 |

### Money handling

`refund_amount = fare_amount * refund_pct / 100` using `Decimal` with `ROUND_HALF_UP` to 2dp, never float.

### Critical defects found and fixed

Six defects were identified by an AGENTS.md compliance review and a Council of 7 Judges design review.
All fixed and verified (6/6 test harness checks pass):

| # | Severity | Defect | Fix applied |
|---|----------|--------|-------------|
| D1 | CRITICAL | `_compute_refund_amount()` was outside try/except — a malformed `fare_amount` like `"abc"` raises `decimal.InvalidOperation`, crashing the entire Lambda invocation and retrying all records | Moved inside try block; added `except InvalidOperation` with ERROR log (`record.money.invalid`) and `batchItemFailures` |
| D2 | CRITICAL | After a parse failure, later records in the same FIFO `MessageGroupId` continued processing — breaking per-PNR ordering guarantee | Added `failed_groups` set tracking failed `MessageGroupId`s; subsequent records in same group auto-failed with `record.skipped.ordering` log |
| D3 | HIGH | `SERVICE = "accelya-m2-cancellation"` missing `<learner>` segment per AGENTS.md naming rule | Changed to `accelya-m2-{LEARNER_ID}-cancellation` with `LEARNER_ID` from env var (default `"amol"`) |
| D4 | HIGH | `batch.complete` log line used literal `"batch"` as correlation_id — untraceable in CloudWatch | Changed to `context.aws_request_id` (Lambda provides per-invocation), with UUID fallback |
| D5 | MEDIUM | `if cancellation["no_show_flag"]:` — a string `"false"` is truthy in Python, wrongly rejecting valid refunds | Changed to `if cancellation["no_show_flag"] is True:` explicit boolean check |
| D6 | PROCESS | HITL review template not filled — merge gate unmet | Completed in `reviews/HITL_REVIEW-m2-cancellation-handler.md` |

## 7. Failure handling

| Case | Handling |
|------|----------|
| Duplicate delivery | FIFO dedup (5 min on `event_id`) + DynamoDB conditional put (permanent) |
| Malformed body / missing field | ERROR log with `error_type`, record added to `batchItemFailures`; subsequent records in same PNR group also failed (FIFO ordering) |
| Invalid fare_amount | `InvalidOperation` caught, ERROR log (`record.money.invalid`), same FIFO group handling |
| FIFO ordering after failure | `failed_groups` set: once a record fails, all later records with the same `MessageGroupId` are returned in `batchItemFailures` without processing |
| DLQ accumulation | CloudWatch alarm on DLQ `ApproximateNumberOfMessagesVisible > 0` -> SNS alert |
| Streams delivery failure | Retries until success or 24h expiry; bisect on batch failure; on-failure destination SQS |

## 8. Security

- No public endpoint. Ingest through producer's IAM role (queue policy scoped to principal + account).
- PII: passenger_name, contact_email never logged. fare_amount never logged. Only identifiers and counts in logs.
- IAM: one role per hop, named actions on ARN-scoped resources (see hop table). No `Action: "*"` or `Resource: "*"`.
- No secrets, keys, or account IDs in code. Config from environment variables (`LEARNER_ID`).
- Encryption: AWS-managed keys for SQS, DynamoDB, S3. S3 Block Public Access on.

## 9. Observability

- Structured JSON logging per `docs/logging-contract.json` v1.1 (service `accelya-m2-amol-cancellation`).
- Required keys on every line: `ts`, `level`, `service`, `correlation_id`, `boundary`, `message`.
- Only keys listed in the contract (no extra fields).
- ERROR lines carry `error_type` (e.g. `ValueError`, `JSONDecodeError`, `InvalidOperation`).
- correlation_id: passed from SQS message attribute -> every log line -> DynamoDB row -> settlement event -> S3 audit.
- `batch.complete` uses `context.aws_request_id` (unique per Lambda invocation).
- Alarms -> SNS: DLQ depth > 0, Lambda errors, IteratorAge, Pipe/rule failures.
- Test harness: `python3 day2/tests/run_and_check.py` -> 6/6 checks pass, 11 log records, 0 contract violations.

## 10. Cost (us-east-1, ~60K events/month, estimates)

| Item | $/month |
|------|---------|
| SQS FIFO + DLQ | ~$0.03 |
| Lambda (decide) | ~$0.02 |
| DynamoDB on-demand writes | ~$0.08 |
| DynamoDB Streams polling | ~$2.10 (72% of total) |
| Lambda (publish) | ~$0.02 |
| EventBridge bus | ~$0.06 |
| Firehose + S3 | ~$0.10 |
| CloudWatch logs + alarms | ~$0.10 |
| SNS | ~$0.01 |
| **Total** | **~$2.50-2.90** |

Bedrock architecture generation cost: ~$0.02 per script call (Sonnet 4.6, ~648 input + ~1200 output tokens).

## 11. Why Option C over A and B

| Criterion | A: SQS Standard | B: EventBridge | C: SQS FIFO + Streams |
|---|---|---|---|
| Per-PNR ordering | No | No | Yes (MessageGroupId=PNR) |
| Failure isolation | Partial batch + DLQ | Async retries + destination | Partial batch + FIFO group ordering + DLQ |
| Idempotency | Needs conditional write | Needs conditional write | FIFO dedup (5 min) + conditional write |
| Settlement visibility | Query table | Event feed | Event feed from table via Streams + EventBridge |
| Moving parts | 3 | 5 | 6 |
| Cost at 2K/day | ~$1.50 | ~$2.00 | ~$2.90 |

**Decision:** Option C because per-PNR ordering is a hard requirement for settlement correctness.
The ~$1.40/month premium over Option A is negligible against the business risk of out-of-order
settlement processing.

## 12. Open questions

1. Settlement retention period (drives DynamoDB TTL and S3 lifecycle expiry).
2. Is API Gateway needed at ingress, or is direct SQS `SendMessage` via cross-account role sufficient?
3. KMS customer-managed key vs AWS-managed key (cost: $1/month per CMK).
4. Firehose buffer interval (900s default vs shorter for near-real-time audit).
