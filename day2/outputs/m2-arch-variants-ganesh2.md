# Final Architecture — Accelya Cancellation Refund Pipeline · ganesh

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0_

> **Defects fixed from m2-arch-variants-ganesh.md review:**
> D-01 no unlisted field names · D-02 fare_amount stripped before persistence · D-03 resource names follow accelya-m<module>-<learner>- · D-04 IAM roles carry named actions + ARN scope · D-05 no account IDs in doc · D-06 correlation_id (not event_id) threads every hop · D-07 boundary value stated per hop · D-08 message dotted-event name stated per hop · D-09 error_type required on every ERROR path · D-10 money annotated as Decimal ROUND_HALF_UP · D-11/D-12 "no copy of data" non-goal reconciled · D-13 Aurora→SNS impossible hop removed · D-14 HITL review pointer added · D-15 timeout stated on every Sync hop · D-16 Step Functions pricing corrected · D-17 service count corrected · D-18 S3 consistency risk corrected · D-19 model id via env var noted

---

## Role

Senior AWS architect, Accelya airline settlement platform, region `us-east-1`.

---

## Context

A passenger or agent cancels a ticket. The cancellation event (`pnr`, `ticket_number`, `airline_code`,
`fare_class`, `departure_status`, `no_show_flag`) must receive a refund decision (`APPROVE` / `REJECT` /
`MANUAL_REVIEW`) and the settlement team must see every decision.

Volume: ~2 000 cancellations/day with possible intraday bursts.
Ordering: strict per-PNR ordering required.
Money: `refund_amount = fare_amount × refund_pct / 100`, `Decimal`, `ROUND_HALF_UP` to 2 d.p., returned as a string.

---

## Non-Goals

- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log `passenger_name`, `contact_email`, or `fare_amount` — identifiers and counts only.
- Raw inbound payload (including `fare_amount` and any PII fields) **must be stripped** before any
  record is persisted or emitted to a downstream service. Only derived decision fields are stored.
- No secrets, keys, or account IDs in code or documentation — config from environment variables only.
- Bedrock model ID injected via Lambda environment variable `BEDROCK_MODEL_ID`; never hardcoded.

---

## Example

Input (fields used by decision engine only — PII fields discarded on receipt):

```
event_id: "evt-1002"  pnr: "LM4K8D"  fare_class: "K"
departure_status: "scheduled"  no_show_flag: false
```

Output decision:

```
event_id: "evt-1002"  decision: "APPROVE"  refund_rule_id: "R-SAVER"
refund_amount: Decimal("3090.00")   ← ROUND_HALF_UP, stored as string "3090.00"
```

---

## Final Architecture

### Services in order

```
API Gateway (IAM auth)
  → SQS FIFO accelya-m2-ganesh-cancellations.fifo  (MessageGroupId = pnr)
      → DLQ: accelya-m2-ganesh-cancellations-dlq.fifo
  → Lambda accelya-m2-ganesh-consumer  (strips PII, decides, writes DynamoDB)
      → DynamoDB accelya-m2-ganesh-decisions  (decision record only — no raw payload)
          → DynamoDB Streams
              → EventBridge (custom bus accelya-m2-ganesh-bus)
                  ├─ Rule → SNS accelya-m2-ganesh-settlement-notify
                  │          → Lambda accelya-m2-ganesh-settlement-subscriber
                  └─ Rule → Kinesis Data Firehose accelya-m2-ganesh-decisions-firehose
                              → S3 accelya-m2-ganesh-decisions-archive (Glue Catalog → Athena)

Observability sidecar (all services):
  CloudWatch Alarms → SNS accelya-m2-ganesh-ops-alerts
```

---

## Service Boundary Analysis

Six-question answers for every hop (`docs/service-boundary-6q.md`).

---

### Hop 1 · API Gateway → SQS FIFO

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Push.** API Gateway AWS-service integration calls `sqs:SendMessage` synchronously. |
| 2 | Sync/async? | **Sync to caller** (HTTP 200 / 4xx / 5xx returned); async from caller's perspective — the response carries `MessageId`, not a decision. API GW integration timeout: **29 s** (Lambda proxy not used; direct SQS integration). |
| 3 | Payload shape? | JSON body: `{event_id, pnr, ticket_number, airline_code, fare_class, departure_status, no_show_flag}`. `fare_amount` and PII fields (`passenger_name`, `contact_email`) are **not** forwarded — the API Gateway mapping template drops them. `MessageGroupId` = `pnr`. `MessageDeduplicationId` = `event_id` (content-based dedup enabled on queue as backstop). |
| 4 | Which role? | IAM role `accelya-m2-ganesh-apigw-sqs-role`: `sqs:SendMessage` on `arn:aws:sqs:us-east-1:${AWS::AccountId}:accelya-m2-ganesh-cancellations.fifo` only. API Gateway endpoint requires `AWS_IAM` auth — callers must sign with SigV4. |
| 5 | Failure path? | API GW returns 5xx on SQS `ServiceUnavailable`; caller must retry with exponential back-off. No DLQ at this hop — the caller is responsible for re-submission. CloudWatch alarm on `4XXError` and `5XXError` metrics. |
| 6 | Correlation ID? | API GW mapping template reads header `X-Correlation-Id` and sets SQS `MessageAttribute` `correlation_id` (String). If header absent, API GW request ID is used. Log `boundary: apigw->sqs`. |

**Log event name:** `cancellation.received`

---

### Hop 2 · SQS FIFO → Lambda (Consumer)

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Pull.** Lambda event source mapping polls the FIFO queue. `MaximumConcurrency` set to **1** on the ESM to preserve per-PNR ordering (one Lambda execution active per MessageGroupId at a time). |
| 2 | Sync/async? | **Async.** Lambda is invoked asynchronously by the ESM. Lambda function timeout: **60 s**. SQS visibility timeout: **90 s** (> Lambda timeout). |
| 3 | Payload shape? | `Records[]` batch (batch size = **1** per MessageGroupId for ordering). Each record: `{messageId, body: {event_id, pnr, ticket_number, airline_code, fare_class, departure_status, no_show_flag}, messageAttributes: {correlation_id}}`. |
| 4 | Which role? | IAM role `accelya-m2-ganesh-consumer-role`: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on `arn:aws:sqs:us-east-1:${AWS::AccountId}:accelya-m2-ganesh-cancellations.fifo`; `sqs:SendMessage` on DLQ ARN; `dynamodb:PutItem`, `dynamodb:UpdateItem` on `arn:aws:dynamodb:us-east-1:${AWS::AccountId}:table/accelya-m2-ganesh-decisions`. |
| 5 | Failure path? | Lambda returns `batchItemFailures` (partial batch response enabled — `ReportBatchItemFailures`). Failed `messageId`s become invisible again and retry up to `maxReceiveCount` = **3**. After 3 failures the message is moved to `accelya-m2-ganesh-cancellations-dlq.fifo`. CloudWatch alarm on `ApproximateNumberOfMessagesVisible` on the DLQ ≥ 1. Catch `ClientError` → log at `ERROR` with `error_type: ClientError`, `boundary: sqs->lambda`; return item in `batchItemFailures`. |
| 6 | Correlation ID? | Read from `messageAttributes.correlation_id.stringValue`; mint a new one (UUID4) if absent. Thread on every log line as `correlation_id`. Log `boundary: sqs->lambda`. |

**Log event names:** `cancellation.processing_started` (INFO) · `cancellation.decided` (INFO) · `cancellation.processing_failed` (ERROR, must include `error_type`)

---

### Hop 3 · Lambda (Consumer) → DynamoDB

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Push.** Lambda calls `dynamodb:PutItem` / `dynamodb:UpdateItem`. |
| 2 | Sync/async? | **Sync.** Lambda blocked until DynamoDB acknowledges. DynamoDB SDK default timeout: **10 s** per attempt; Lambda timeout is the outer bound (60 s). |
| 3 | Payload shape? | Decision record only — **no raw payload fields, no `fare_amount`**: `{event_id (PK), pnr, ticket_number, airline_code, fare_class, refund_rule_id, decision, settlement_date, correlation_id, processing_state}`. PII stripped before this write. Idempotency: `ConditionExpression: attribute_not_exists(event_id)` on PutItem; on `ConditionalCheckFailedException` treat as already-processed and delete SQS message. |
| 4 | Which role? | Same `accelya-m2-ganesh-consumer-role` as Hop 2 — `dynamodb:PutItem`, `dynamodb:UpdateItem` on table ARN only. |
| 5 | Failure path? | Catch `ClientError` (e.g. `ProvisionedThroughputExceededException`, `InternalServerError`); log at ERROR with `error_type`; return item in `batchItemFailures` to trigger SQS retry. `ConditionalCheckFailedException` is not an error — log INFO `cancellation.duplicate_skipped`. |
| 6 | Correlation ID? | `correlation_id` stored as an attribute in the DynamoDB item and carried on every log line. Log `boundary: lambda->dynamodb`. |

**Log event names:** `cancellation.written` (INFO) · `cancellation.duplicate_skipped` (INFO) · `cancellation.write_failed` (ERROR)

---

### Hop 4 · DynamoDB Streams → EventBridge (Pipes)

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Push.** EventBridge Pipes polls the DynamoDB Stream and forwards `INSERT` / `MODIFY` events that have `decision` attribute set (enrichment filter). |
| 2 | Sync/async? | **Async.** Pipe forwards the stream shard record; DynamoDB Streams shard iterator timeout up to **24 h** for unprocessed records. Pipe batch size = 1. |
| 3 | Payload shape? | EventBridge custom event: `{source: "accelya.cancellation", detail-type: "RefundDecided", detail: {event_id, pnr, ticket_number, airline_code, fare_class, refund_rule_id, decision, settlement_date, correlation_id}}`. No `fare_amount`. |
| 4 | Which role? | IAM role `accelya-m2-ganesh-pipe-role`: `dynamodb:GetRecords`, `dynamodb:GetShardIterator`, `dynamodb:DescribeStream`, `dynamodb:ListStreams` on `arn:aws:dynamodb:us-east-1:${AWS::AccountId}:table/accelya-m2-ganesh-decisions/stream/*`; `events:PutEvents` on `arn:aws:events:us-east-1:${AWS::AccountId}:event-bus/accelya-m2-ganesh-bus`. |
| 5 | Failure path? | On Pipe failure, EventBridge Pipes retries up to 2× with back-off. Failed records written to `accelya-m2-ganesh-pipe-dlq` (SQS). CloudWatch alarm on DLQ depth ≥ 1. |
| 6 | Correlation ID? | `correlation_id` is an attribute in the DynamoDB item; Pipe mapping template copies it into `detail.correlation_id`. Log `boundary: dynamodb->eventbridge`. |

**Log event names:** `cancellation.event_emitted` (INFO on the Pipe enrichment Lambda if used)

---

### Hop 5 · EventBridge → SNS (Settlement Notification Rule)

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Push.** EventBridge rule matches `source = "accelya.cancellation"` and `detail-type = "RefundDecided"`; targets SNS topic. |
| 2 | Sync/async? | **Async.** EventBridge delivers to SNS; no caller blocking. EventBridge built-in retry with exponential back-off for up to **24 h** on target failure. |
| 3 | Payload shape? | SNS message: `{event_id, pnr, ticket_number, airline_code, fare_class, refund_rule_id, decision, settlement_date, correlation_id}`. `MessageAttributes`: `correlation_id` (String). |
| 4 | Which role? | IAM role `accelya-m2-ganesh-eb-rule-role`: `sns:Publish` on `arn:aws:sns:us-east-1:${AWS::AccountId}:accelya-m2-ganesh-settlement-notify` only. |
| 5 | Failure path? | EventBridge dead-letter queue `accelya-m2-ganesh-eb-settlement-dlq` (SQS). CloudWatch alarm on DLQ depth ≥ 1. SNS topic has SSE (KMS CMK `accelya-m2-ganesh-cmk`) and a resource policy allowing only the EventBridge role to publish. |
| 6 | Correlation ID? | `correlation_id` forwarded in `MessageAttributes.correlation_id`. Log `boundary: eventbridge->sns`. |

**Log event names:** no Lambda in this hop; correlation carried through message attributes.

---

### Hop 6 · SNS → Lambda (Settlement Subscriber)

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Push.** SNS invokes Lambda subscription. |
| 2 | Sync/async? | **Async.** SNS does not wait for Lambda response. Lambda timeout: **30 s**. SNS retry policy: 3 retries over 20 min. |
| 3 | Payload shape? | Lambda event: `Records[0].Sns.Message` = JSON string with `{event_id, pnr, ticket_number, airline_code, fare_class, refund_rule_id, decision, settlement_date}`; `MessageAttributes.correlation_id`. No `fare_amount`. |
| 4 | Which role? | IAM role `accelya-m2-ganesh-settlement-role`: no outbound AWS API calls in this module. SNS subscription `RedrivePolicy` points to `accelya-m2-ganesh-settlement-dlq`. |
| 5 | Failure path? | On Lambda error, SNS retries 3×. After exhausting retries, message moves to `accelya-m2-ganesh-settlement-dlq` (SQS). CloudWatch alarm on DLQ depth ≥ 1 triggers ops page. Catch `Exception` → log ERROR with `error_type`, `boundary: sns->lambda`. |
| 6 | Correlation ID? | Read from `MessageAttributes.correlation_id.Value`; mint if absent. Thread on every log line. Log `boundary: sns->lambda`. |

**Log event names:** `settlement.notification_received` (INFO) · `settlement.notification_failed` (ERROR)

---

### Hop 7 · EventBridge → Firehose → S3 (Audit / Analytics Rule)

| # | Question | Answer |
|---|----------|--------|
| 1 | Push/pull? | **Push.** EventBridge rule (same event) targets Kinesis Data Firehose delivery stream. |
| 2 | Sync/async? | **Async.** Firehose buffers records (buffer size 5 MB or 60 s, whichever first). |
| 3 | Payload shape? | Firehose record = EventBridge `detail` JSON (same fields as Hop 5 — no `fare_amount`). S3 prefix: `dt=YYYY-MM-DD/airline_code=<code>/`. Glue Crawler catalogues weekly; Athena queries via Glue Data Catalog. S3 bucket `accelya-m2-ganesh-decisions-archive`: SSE-KMS, versioning enabled, `BlockPublicAccess` all true, lifecycle to Glacier after 90 days. |
| 4 | Which role? | IAM role `accelya-m2-ganesh-eb-firehose-role`: `firehose:PutRecord`, `firehose:PutRecordBatch` on `arn:aws:firehose:us-east-1:${AWS::AccountId}:deliverystream/accelya-m2-ganesh-decisions-firehose`. Firehose delivery role `accelya-m2-ganesh-firehose-s3-role`: `s3:PutObject` on `arn:aws:s3:::accelya-m2-ganesh-decisions-archive/*`, `kms:GenerateDataKey` on CMK ARN. |
| 5 | Failure path? | Firehose S3 delivery failures written to `accelya-m2-ganesh-decisions-archive/errors/` prefix. CloudWatch alarm on `DeliveryToS3.DataFreshness` > 300 s. |
| 6 | Correlation ID? | `correlation_id` present in the EventBridge `detail` payload and stored as a field in every S3 record. Log `boundary: eventbridge->firehose`. |

**Log event names:** no Lambda in this hop; Firehose is managed.

---

## Risks

1. **SQS FIFO per-group ordering + Lambda concurrency.** `MaximumConcurrency = 1` on the ESM serialises all PNRs through one Lambda instance. At burst (e.g. mass cancellation event), throughput is capped at ~10 events/s (1 msg × batch × ~100 ms Lambda duration). **Mitigation:** increase batch size to 10 (batch per group still delivered to one execution); tune Lambda memory to reduce duration; monitor `ApproximateAgeOfOldestMessage` and alarm at > 300 s.

2. **DynamoDB `ConditionExpression` idempotency race on retry.** If Lambda crashes after `PutItem` succeeds but before `DeleteMessage`, SQS re-delivers the same message. The `attribute_not_exists(event_id)` condition prevents a double write, but a concurrent retry for the same `event_id` on a different PNR group could still hit `ConditionalCheckFailedException`. **Mitigation:** treat `ConditionalCheckFailedException` as success; log `cancellation.duplicate_skipped` at INFO and delete the message.

---

## Cost Drivers

Volume basis: 2 000 cancellations/day, 30-day month = 60 000 events.

| Service | Derivation | $/day | $/month |
|---------|-----------|-------|---------|
| API Gateway (HTTP API) | 60 K req/month × $1/million | $0.002 | $0.06 |
| SQS FIFO | 60 K req/month × $0.50/million (+ ~10% overhead) | $0.001 | $0.03 |
| Lambda (consumer + settlement, 128 MB, 200 ms avg) | 60 K inv × 2 fn × $0.20/million inv + GB-s: 60 K × 2 × 0.2 s × 0.125 GB × $0.0000166667 | $0.001 | $0.07 |
| DynamoDB on-demand | 60 K writes × 2 WCU avg × $1.25/million WCU + 1 GB storage $0.25/GB | $0.005 | $0.17 |
| EventBridge custom bus | 60 K events × $1/million | $0.002 | $0.06 |
| Firehose | 60 K records × avg 0.5 KB = ~30 MB/month; min 5 KB/record billing = 60 K × 5 KB = ~300 MB; $0.029/GB | <$0.001 | $0.01 |
| S3 (decisions archive) | Negligible at this volume + standard storage | <$0.001 | $0.01 |
| SNS | 60 K publishes × $0.50/million | <$0.001 | $0.03 |
| CloudWatch Alarms | ~10 alarms × $0.10/alarm | — | $1.00 |
| **Total** | | **~$0.01** | **~$1.50** |

> Aurora removed (D-13: Aurora→SNS was architecturally impossible; D-12: Aurora persisting full events violated "no copy" non-goal). DynamoDB replaces Aurora for decision state; Firehose+S3+Athena replaces Aurora for settlement team queries at a fraction of the cost.

---

## Sandbox Compliance Check

| Constraint | Status |
|-----------|--------|
| Region `us-east-1` only | All resources in `us-east-1` |
| Max 10 Lambda functions | 2 Lambdas used: `accelya-m2-ganesh-consumer`, `accelya-m2-ganesh-settlement-subscriber` |
| Memory ≤ 2048 MB | Both Lambdas at 128 MB |
| No public S3 buckets | `BlockPublicAccess` all true on archive bucket |
| Max 50 Bedrock calls / 20 K tokens | Architecture does not invoke Bedrock; decision engine uses rule-based logic from `refund_rules.json` |
| Resource names `accelya-m<module>-<learner>-` | All resources prefixed `accelya-m2-ganesh-` |

---

## Logging Contract Compliance (docs/LOGGING_CONTRACT.md v1.1)

Every Lambda log line must carry:

```json
{
  "ts":             "2026-11-25T10:00:00Z",
  "level":          "INFO",
  "service":        "accelya-m2-ganesh-consumer",
  "correlation_id": "c-91a001",
  "boundary":       "sqs->lambda",
  "message":        "cancellation.decided",
  "event_id":       "evt-1002",
  "pnr":            "LM4K8D",
  "fare_class":     "K",
  "refund_rule_id": "R-SAVER",
  "decision":       "APPROVE"
}
```

Forbidden on any log line: `passenger_name`, `contact_email`, `fare_amount`, raw exception messages, any payload field not in `logging-contract.json`.

ERROR lines must include `error_type` (exception class name, e.g. `ClientError`, `ValidationError`).

Verify with: `python3 day2/scripts/check_logs.py`

---

## IAM Summary

| Role | Principal | Actions | Resource scope |
|------|-----------|---------|---------------|
| `accelya-m2-ganesh-apigw-sqs-role` | `apigateway.amazonaws.com` | `sqs:SendMessage` | FIFO queue ARN |
| `accelya-m2-ganesh-consumer-role` | `lambda.amazonaws.com` | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on FIFO queue; `sqs:SendMessage` on DLQ; `dynamodb:PutItem`, `dynamodb:UpdateItem` on decisions table | Specific ARNs only |
| `accelya-m2-ganesh-pipe-role` | `pipes.amazonaws.com` | `dynamodb:GetRecords`, `dynamodb:GetShardIterator`, `dynamodb:DescribeStream`, `dynamodb:ListStreams` on stream ARN; `events:PutEvents` on custom bus ARN | Specific ARNs only |
| `accelya-m2-ganesh-eb-rule-role` | `events.amazonaws.com` | `sns:Publish` on settlement SNS ARN; `firehose:PutRecord`, `firehose:PutRecordBatch` on Firehose ARN | Specific ARNs only |
| `accelya-m2-ganesh-settlement-role` | `lambda.amazonaws.com` | `logs:CreateLogGroup`, `logs:CreateLogDelivery`, `logs:PutLogEvents` | Log group ARN |
| `accelya-m2-ganesh-firehose-s3-role` | `firehose.amazonaws.com` | `s3:PutObject` on archive bucket prefix; `kms:GenerateDataKey`, `kms:Decrypt` on CMK ARN | Specific ARNs only |

No role has `"Action": "*"` or `"Resource": "*"`.

---

## HITL Review

Reviewer must complete `docs/HITL_REVIEW_TEMPLATE.md` and file at:
`reviews/HITL_REVIEW-m2-arch-variants-ganesh2.md`

Pass bar: ≥ 12/15 AND every dimension ≥ 2.

| Dimension | Expected evidence |
|-----------|------------------|
| Correctness | Aurora→SNS impossible hop removed; `batchItemFailures` present; idempotency `ConditionExpression` present |
| Security | No wildcards in IAM; API GW requires IAM auth; S3 BlockPublicAccess; SNS SSE-KMS; no account IDs or secrets |
| Observability | `correlation_id` threaded every hop; `boundary` stated every hop; `message` dotted-event name stated; `error_type` on every ERROR path; DLQ alarms defined |
| Scalability | `MaximumConcurrency = 1` for PNR ordering; `ReportBatchItemFailures` for partial batch; idempotent DynamoDB writes |
| Cost | Aurora eliminated; total ~$1.50/month; Step Functions not used (cost calculation error D-16 avoided by design) |
