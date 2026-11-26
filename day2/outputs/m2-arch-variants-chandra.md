# Architecture options (3) — cancellation refund decisions

_Prompt: day2/prompts/m2-arch-variants-chandra.md. Synthetic scenario; no real Accelya data.
Region us-east-1; naming `accelya-m2-chandra-*`; logging per docs/LOGGING_CONTRACT.md.
Volume: 2,000 cancellations/day (~1.4/min average), peaks of 50/min after a disruption.
Ordering: not required — standard SQS semantics are acceptable._

## Option A — SQS queue -> Lambda -> DynamoDB + SNS (recommended)

Summary: producers put cancellation events on a standard SQS queue; a Lambda processes batches with
`ReportBatchItemFailures`, writes each decision to DynamoDB keyed on `event_id` and publishes an
identifier-only summary to an SNS topic the settlement team subscribes to.

Services in order: producer -> SQS `accelya-m2-chandra-cancellations` (+DLQ, maxReceiveCount 3) ->
event source mapping -> Lambda `accelya-m2-chandra-cancellation` -> DynamoDB `accelya-m2-chandra-decisions` ->
SNS `accelya-m2-chandra-settlement`

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| Producer -> SQS | push | async SendMessage | JSON body + `correlation_id` message attribute | producer role: `sqs:SendMessage` on queue ARN | producer retries; NACK surfaces to caller | `correlation_id` attribute |
| SQS -> Lambda | pull (event source mapping) | sync invoke per batch | `Records[]`, batch 10 | Lambda role: `sqs:ReceiveMessage/DeleteMessage/ChangeMessageVisibility/GetQueueAttributes` on queue ARN | `batchItemFailures` -> only failed records redelivered; after 3 receives -> DLQ; CloudWatch alarm on `ApproximateNumberOfMessagesVisible` in DLQ | attribute read by handler; minted if absent; on every log line |
| Lambda -> DynamoDB | push | sync PutItem | `{event_id(pk), decision, refund_rule_id, refund_amount, pnr, settled_at, correlation_id}` | `dynamodb:PutItem` on table ARN | `ConditionExpression: attribute_not_exists(event_id)` — duplicate delivery/retry = silent no-op; other errors -> batchItemFailures | stored as item attribute |
| Lambda -> SNS | push | async Publish, AFTER the PutItem | summary `{event_id, decision, refund_rule_id}` — no amounts, no PII | `sns:Publish` on topic ARN | publish error -> WARNING log + error metric; decision already in DynamoDB so settlement loses nothing | `correlation_id` message attribute |

Performance: average 1.4 msg/min means batches rarely fill — set `MaximumBatchingWindowInSeconds` ~10 s
or BatchSize 10 adds up to ~10 s latency at off-peak; at the 50/min peak the batch fills every ~12 s.
End-to-end: queue wait + invoke ≈ 0.3–15 s (vs ~0.2 s decide-time). At 10× scale (20,000/day, 500/min)
still trivial for one function's concurrency; the standard queue is unordered — accepted, PNR ordering
is not required.

Cost per day — derivation (us-east-1 list prices, 2,000 events):
| Component | Unit price | Volume | Cost |
|---|---|---|---|
| SQS requests | $0.40 / 1M | 2,000 sends + ~2,200 receives/deletes | $0.0016 |
| Lambda | $0.20 / 1M invokes + $0.0000167/GB-s | ~200 invokes of batch 10 × 0.2 s × 0.25 GB | $0.0003 |
| DynamoDB | $1.25 / 1M WRU | 2,000 × 1 WRU (< 1 KB item) | $0.0025 |
| SNS | $0.50 / 1M publishes | 2,000 | $0.0010 |
**≈ $0.005/day (~$0.15/month).** Assumes messages < 64 KB (one SQS billing unit), items < 1 KB,
one SNS subscriber protocol free tier.

Risks: (1) DLQ messages need an operator runbook (alarm + redrive script) or they rot silently.
(2) At-least-once delivery: a redelivered message re-runs the rule — mitigated by the
`attribute_not_exists` conditional write, but it does run.

## Option B — API Gateway -> Lambda (synchronous) -> DynamoDB + SNS

Summary: producers POST each cancellation to an API; the Lambda decides synchronously, returns the
decision in the response and persists/publishes as in A.

Services in order: producer -> API Gateway `POST /cancellations` (IAM auth) -> Lambda -> DynamoDB -> SNS

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| Producer -> API Gateway | push | sync, 29 s cap | JSON body | `execute-api:Invoke` via IAM (no API keys — no secrets in calls) | caller retry; must send idempotency key | `X-Correlation-Id` header |
| API Gateway -> Lambda | push | sync proxy invoke | proxy event | resource policy: `lambda:InvokeFunction` for the API ARN | invocation error -> 5xx to caller | header -> log lines |
| Lambda -> DynamoDB / SNS | push | sync | as Option A | as Option A | failure -> 5xx + ERROR log; caller retries whole event | as Option A |

Performance: fastest end-to-end (~150–400 ms) because nothing buffers. But at 50/min the function is
invoked per request — fine technically, the real cost is the caller: a synchronous dependency means the
producer must survive downstream stalls, and every retried POST needs an `event_id` idempotency check
in the handler or the ticket gets decided twice (writes stay idempotent via the same conditional put).

Cost per day — derivation: API Gateway REST $3.50/1M × 2,000 = $0.0070; Lambda 2,000 invokes
(one per request) × $0.20/1M + GB-s ≈ $0.0010; DynamoDB $0.0025 and SNS $0.0010 as in A.
**≈ $0.012/day (~$0.35/month).** API Gateway is the new fixed-per-request driver; at 10× volume the
total scales to ~$0.12/day, still ~2.5× Option A.

Risks: (1) no buffer — any downstream stall fails caller-facing requests instead of queueing.
(2) Sync retry semantics are the caller's problem; most callers get idempotency wrong.

## Option C — EventBridge -> Lambda -> DynamoDB + SNS

Summary: producers `PutEvents` onto a custom bus; a rule on `detail-type: "cancellation"` invokes the
Lambda; persistence and notification as in A.

Services in order: producer -> EventBridge `accelya-m2-chandra-bus` -> rule -> Lambda -> DynamoDB -> SNS

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| Producer -> EventBridge | push | async PutEvents | `{source, detail-type, detail}` | `events:PutEvents` on bus ARN | per-entry failures returned to caller | `detail.correlation_id` |
| EventBridge -> Lambda | push | async invoke | matched event | resource policy: `lambda:InvokeFunction` for rule ARN | retries up to 24 h / 185 times, then rule DLQ + alarm | event `id` + `detail.correlation_id` |
| Lambda -> DynamoDB / SNS | push | sync | as Option A | as Option A | handler exception -> EventBridge retry; handler MUST be idempotent | as Option A |

Performance: rule matching + async invoke ≈ 0.5–2 s end-to-end. One event per invocation — no batching,
so at 500/min peak it is 500 invokes/min where A needs ~50. The 185-retry window is the trap: a
deterministically bad record burns up to a day of retries before landing in the rule DLQ; the handler
must classify malformed records and return success + dead-letter them itself to fail fast.

Cost per day — derivation: EventBridge custom-bus puts $1.00/1M × 2,000 = $0.0020; Lambda 2,000
individual invokes ≈ $0.0010; DynamoDB $0.0025 and SNS $0.0010 as in A.
**≈ $0.007/day (~$0.20/month)** — the Lambda term is ~3× A's because there is no batching, so at 10×
scale ($0.07/day) the gap over A widens from 1.4× to 3×+.

Risks: (1) retry-storm on malformed records (see above). (2) Adds a bus + rules to own for no gain over A
at this volume — pays off only when many consumers need the same event.

## Comparison

| | A: SQS -> Lambda | B: API Gateway -> Lambda | C: EventBridge -> Lambda |
|---|---|---|---|
| **Cost/day (2k events)** | **≈ $0.005** | ≈ $0.012 | ≈ $0.007 |
| End-to-end latency | 0.3–15 s (batch window) | ~0.2 s | 0.5–2 s |
| Buffers 50/min spike | yes | no — caller feels it | yes |
| Batching | 10/invoke | n/a | 1/invoke |
| Duplicate handling | conditional PutItem | caller + conditional PutItem | conditional PutItem |
| Bad-record path | batchItemFailures -> DLQ @3 | caller retry | 24 h retries -> DLQ |
| DLQ visibility | alarm on DLQ depth | none needed | rule DLQ + alarm |
| Settlement visibility | DynamoDB + SNS | DynamoDB + SNS | DynamoDB + SNS |
| 10× scale | cheapest | fine | Lambda cost ×10 |
| Moving parts | 5 | 4 | 5 |
| Sandbox fit | M2 lab handler exists | new API surface | new bus + rules |

**Recommendation: Option A.** At 2,000/day it is the cheapest and the only one that fails bad records
fast into an alarmed DLQ; the batch-window latency is acceptable for settlement (not a customer-facing
path). Option B is the right shape only if the cancelling agent truly needs the decision in the same
request. Option C is premature fan-out — worth revisiting if a second consumer (e.g. fraud, ops) ever
needs the same cancellation stream.

## Self-review vs HITL template (expected score)

Correctness 3 (rules, ordering caveat, edge cases named) · Security 3 (ARN-scoped named actions,
IAM auth, no secrets, no PII/amounts on the wire) · Observability 2 (contract logs + classified errors
+ DLQ alarm; EMF metrics are Module 9) · Scalability 2 (idempotent writes + bounded batches; 10× noted,
not tested) · Cost 3 (workload-appropriate, measured per-day). **Expected: 13/15 — PASS.**
