# THREE ARCHITECTURE OPTIONS FOR ACCELYA AIRLINE SETTLEMENT

_Fixed version of the Bedrock output (m2-arch-variants-chandra-chandra.md). Corrections marked **[fix]**;
best practices marked **[bp]**. Region us-east-1; naming `accelya-m2-chandra-*`; logging contract v1.1._

---

## OPTION 1: Producer -> SQS Standard -> Lambda -> DynamoDB + SNS (recommended)

**Summary:** Cancellation events land on a standard SQS queue; one Lambda decides refunds in batches,
persists to DynamoDB keyed on `event_id`, and notifies the settlement team on SNS.

**Service chain:** producer -> SQS `accelya-m2-chandra-cancellations` (+DLQ) -> Lambda
`accelya-m2-chandra-cancellation` -> DynamoDB `accelya-m2-chandra-decisions` ->
SNS `accelya-m2-chandra-settlement`

**[fix] One execution role** (`accelya-m2-chandra-cancellation-role`) — a Lambda has exactly one
execution role; it carries SQS read, DynamoDB write, SNS publish and CloudWatch logs permissions.
The Bedrock draft invented four roles.

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| Producer -> SQS | push | async SendMessage | JSON body + `correlation_id` message attribute | producer role: `sqs:SendMessage` on queue ARN | send failure returns to producer (retry/NACK); visibility timeout does not apply here **[fix: it applies to consumers]** | `correlation_id` attribute |
| SQS -> Lambda | pull (event source mapping) | sync invoke per batch | `Records[]`, BatchSize 10 | `sqs:ReceiveMessage/DeleteMessage/ChangeMessageVisibility/GetQueueAttributes` on queue ARN | `FunctionResponseTypes: ReportBatchItemFailures` **[bp]** -> failed records redelivered; after `maxReceiveCount` 3 -> DLQ + CloudWatch alarm on DLQ depth **[fix: DLQ was missing]** | attribute on each record; minted if absent; on every log line |
| Lambda -> DynamoDB | push | sync PutItem | `{event_id (pk), pnr, decision, refund_rule_id, refund_amount, correlation_id, settled_at}` | `dynamodb:PutItem` on table ARN | `ConditionExpression: attribute_not_exists(event_id)` -> duplicate delivery is a no-op **[fix: dedup on event_id, not pnr — one PNR has several events]**; other errors -> batchItemFailures | stored as item attribute |
| Lambda -> SNS | push | async Publish, AFTER the PutItem | `{event_id, decision, refund_rule_id, pnr}` — contract-safe fields only **[fix: no fare_amount]** | `sns:Publish` on topic ARN | publish error -> WARNING log + error metric; decision already durable in DynamoDB | `correlation_id` message attribute |
| SNS -> settlement | push (fan-out to SQS/email/HTTPS) **[fix: SNS is push-only; a dashboard can't pull it]** | async | notification envelope | subscription protocol | per-subscription delivery policy + redrive to DLQ | in message attributes |

**Best practices applied to Option 1:**
- **[bp] VisibilityTimeout 180 s** = 6x the 30 s function timeout; documented, not picked at random.
- **[bp] `MaximumBatchingWindowInSeconds` ~10 s** — at 1.4 msg/min average, batch 10 rarely fills;
  the window bounds added latency. At 50/min peak the batch fills every ~12 s.
- **[bp] SqsManagedSseEnabled + 14-day retention + TLS** on both queues.
- **[bp] Logging contract:** `correlation_id` on every line, `error_type` on ERROR, `message_id` per
  record — never the body, fare_amount or passenger data.
- **[bp] Write order matters:** DynamoDB first, SNS second — a failed notification can't hide a
  decision because the table is the system of record; settlement may also poll the table.

**Risks:**
1. **DLQ rot** — messages in the DLQ are invisible without the alarm + redrive runbook. Mitigation:
   alarm on `ApproximateNumberOfMessagesVisible > 0`.
2. **Duplicate delivery** — standard queue, at-least-once. Mitigation: conditional PutItem on
   `event_id`; the rule may re-run but the write is idempotent.

**Cost/day derivation (2,000 events, us-east-1 list prices):**
| Component | Unit price | Volume | Cost |
|---|---|---|---|
| SQS | $0.40/M | ~4,200 ops (send, receive, delete) | $0.0017 |
| Lambda | $0.20/M + GB-s | ~200 batched invokes/day **[fix: not 2,000 — batching]** | $0.0003 |
| DynamoDB | $1.25/M WRU | 2,000 x 1 WRU | $0.0025 |
| SNS | $0.50/M | 2,000 publishes | $0.0010 |
**Total: ~$0.0055/day = ~$0.17/month.**

---

## OPTION 2: API Gateway -> Lambda (sync) -> DynamoDB -> SNS

**Summary:** synchronous decision over API; caller gets the answer in the response.

| Hop | Notes / corrections |
|---|---|
| API GW -> Lambda | sync proxy, 29 s cap; IAM auth (no API keys in calls) |
| Lambda -> DynamoDB | same conditional PutItem; failure -> 5xx to caller |
| Lambda -> SNS | same as Option 1 |

**[fix] Removed the draft's DynamoDB Streams -> notifier Lambda hop** — it worked but added a
second function and stream machinery just to send an SNS the deciding Lambda can send itself;
the "max 100 retries" number in the draft was invented (streams retry until record expiry ~24 h).

Performance: fastest end-to-end (~0.2 s) — use only if the cancelling agent truly needs the decision
in the same request. No buffer: a 50/min spike hits the function directly; caller must implement
idempotent retries on `event_id`.

Cost/day: API GW REST $3.50/M x 2,000 = $0.0070; Lambda ~$0.001; DDB+SNS ~$0.0035.
**Total ~$0.012/day = ~$0.35/month.**

Risks: (1) caller-owned retry/idempotency is the most commonly botched part. (2) Downstream stall
becomes a caller-facing failure immediately — nothing queues.

---

## OPTION 3: EventBridge -> Lambda -> DynamoDB + SNS

**Summary:** producers PutEvents to a custom bus; a rule on `detail-type: cancellation` invokes Lambda.

**[fix] Dropped the draft's SQS FIFO hop** — the prompt explicitly says PNR ordering is not required;
FIFO adds cost and head-of-line blocking (a failed record stalls its group until visibility timeout)
for zero benefit.

Key hop (corrected): EventBridge -> Lambda is **async invoke** **[fix: draft marked it sync]**;
failure -> retries up to 24 h / 185 times -> rule DLQ. A malformed record therefore retries for a
day unless the handler classifies it and returns success + logs ERROR (self-dead-letter pattern).

Cost/day: EventBridge $1.00/M x 2,000 = $0.002; Lambda ~$0.001; DDB+SNS ~$0.0035.
**Total ~$0.007/day = ~$0.20/month** — Lambda term scales linearly (no batching).

Risks: (1) retry storm on poison records. (2) Bus + rules owned for no gain at one consumer — pays
off only when a second consumer (fraud, ops) needs the same stream.

---

## COMPARISON TABLE

| Criterion | Option 1 (SQS) | Option 2 (API GW sync) | Option 3 (EventBridge) |
|---|---|---|---|
| **Cost/day (2k)** | **$0.0055** | $0.012 | $0.007 |
| Cost at 10x | ~$0.03 | ~$0.12 | ~$0.07 |
| Latency | 0.3-15 s (batch window) | ~0.2 s | 0.5-2 s |
| Buffers 50/min peak | yes | no | yes |
| Bad-record path | batchItemFailures -> DLQ @3, alarmed | caller retry | 24 h retries -> rule DLQ |
| Idempotency | conditional PutItem on event_id | same, but caller must retry | same |
| Ordering | standard queue (not required) | n/a | standard semantics |
| Lambda count | 1 | 1 | 1 |
| Moving parts | 5 | 4 | 5 |

**Recommendation: Option 1** — cheapest, fails bad records fast into an alarmed DLQ, batching keeps
cost flat at 10x, and it is exactly what `day2/src/sample-demo/` implements and tests (6/6).

## Best-practice checklist used for the fixes
- One execution role per Lambda; named actions on ARN-scoped resources.
- ReportBatchItemFailures on every SQS event source mapping; DLQ + alarm always paired.
- Conditional writes for idempotency; dedupe on the message's unique id, never a business key.
- Visibility timeout = 6x function timeout; batch window stated, not defaulted blindly.
- No PII/fare_amount in notifications or logs; correlation_id (minted if absent) on every line.
- Cost claimed only with stated unit price x volume; sanity bound ~$5/month at 2,000/day.
