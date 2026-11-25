# Solution Architecture — Cancellation Refund Decisions (M2)

**Owner:** chandra · **Region:** us-east-1 · **Status:** ready for HITL review
**Chosen option:** SQS → Lambda → DynamoDB + SNS (Option A from `m2-arch-variants-chandra.md`)

---

## 1. Overview

Cancellation events from passengers/agents are ingested asynchronously on a standard SQS queue,
decided by a single Lambda (refund rules from `day2/data/refund_rules.json`), persisted to DynamoDB
as the system of record, and fanned out to the settlement team via SNS. Every decision is logged
under the v1.1 logging contract — identifiers and counts only.

Volume: 2,000 events/day (~1.4/min avg), 50/min peak. Ordering: not required → standard queue.

## 2. Diagram

```
producer (agent / airline system)
      |  sqs:SendMessage  (+ correlation_id attribute)
      v
+------------------------------------------+
| SQS  accelya-m2-chandra-cancellations    |<--- redrive (maxReceiveCount 3)
|  SSE-SQS, retention 14d, VT 180s         |       |
+------------------------------------------+       v
      |  event source mapping (batch 10,         +-----------------+
      |   ReportBatchItemFailures, batchWindow)  | DLQ + alarm     |
      v                                          +-----------------+
+------------------------------------------+
| Lambda  accelya-m2-chandra-cancellation  |
|  decide -> validate -> log -> persist    |
+-----+--------------------------------+---+
      | PutItem (conditional)          | Publish (after write)
      v                                v
+-------------------------+   +-------------------------+
| DynamoDB  decisions     |   | SNS  settlement topic   |
|  pk = event_id          |   |  -> settlement queue/   |
|  (system of record)     |   |     email / HTTPS       |
+-------------------------+   +-------------------------+
      |                               
      v (all application records)
+------------------------------------------+
| CloudWatch  /aws/lambda/accelya-m2-...   |  contract v1.1, 7-day retention
+------------------------------------------+
```

## 3. Components

| Component | Config | Why |
|---|---|---|
| SQS `...-cancellations` | standard, SSE-SQS, VT = 180 s (6× fn timeout), retention 14 d | buffers 50/min spikes; unordered is accepted |
| SQS `...-dlq` | same hardening | poison records visible, not lost |
| Event source mapping | BatchSize 10, MaxBatchingWindow ≤10 s, `ReportBatchItemFailures` | partial-batch retry; bounds low-volume latency |
| Lambda `...-cancellation` | python3.12, 256 MB, 30 s, env `LEARNER_ID` only | decides; already implemented in `day2/src/sample-demo/` |
| DynamoDB `...-decisions` | on-demand, pk `event_id` | idempotent writes; settlement's system of record |
| SNS `...-settlement` | fan-out to settlement queue/email | "see every decision" without coupling |
| CloudWatch log group | `/aws/lambda/accelya-m2-chandra-cancellation`, 7 d | contract logs + DLQ alarm |
| CloudWatch alarm | `DLQ ApproximateNumberOfMessagesVisible > 0` | no DLQ rot |

## 4. The six boundary questions (per hop)

| Hop | Push/pull | Sync/async | Payload | Role | Failure path | Correlation ID |
|---|---|---|---|---|---|---|
| Producer → SQS | push | async SendMessage | JSON body + attr | `sqs:SendMessage` on queue ARN | send failure → producer retry | `correlation_id` attribute |
| SQS → Lambda | pull (ESM) | sync invoke/batch | Records[] ≤10 | sqs Rcv/Del/ChgVis/GetAttrs on queue ARN | batchItemFailures → redeliver → DLQ @3 | attribute → minted if absent |
| Lambda → DynamoDB | push | sync PutItem | decision item | `dynamodb:PutItem` on table ARN | `attribute_not_exists(event_id)`; else → batchItemFailures | item attribute |
| Lambda → SNS | push | async Publish | {event_id, decision, refund_rule_id, pnr} | `sns:Publish` on topic ARN | WARNING + metric; DDB already has it | message attribute |
| Lambda → Logs | n/a | n/a | contract JSON line | logs:CreateLogStream/PutLogEvents on own group | — | `correlation_id` every line |

## 5. IAM (one execution role)

`accelya-m2-chandra-cancellation-role`, trust = `lambda.amazonaws.com` only:
- `sqs:ReceiveMessage/DeleteMessage/ChangeMessageVisibility/GetQueueAttributes` → queue ARN only
- `dynamodb:PutItem` → decisions table ARN only
- `sns:Publish` → topic ARN only
- `logs:CreateLogStream/PutLogEvents` → own log group ARN only

No managed `AWSLambdaBasicExecutionRole` (`Resource: "*"`), no wildcard actions.

## 6. Decision logic (implemented, tested)

- Rules (`refund_rules.json`), first match: R-NOSHOW / R-DEPARTED → REJECT 0%; FLEX/BUSINESS → APPROVE 100%; SAVER → 75%; else MANUAL_REVIEW.
- Money: Decimal, ROUND_HALF_UP, 2 dp.
- Validation: malformed JSON or missing field → one ERROR `record.invalid` + `batchItemFailures`.
- Harness: 6/6 checks (decisions, failures, contract, PII, traceability).

## 7. Cost (derived, us-east-1)

| Component | Unit | Volume | /day |
|---|---|---|---|
| SQS | $0.40/M | ~4,200 ops | $0.0017 |
| Lambda | $0.20/M + GB-s | ~200 batched invokes | $0.0003 |
| DynamoDB | $1.25/M WRU | 2,000 writes | $0.0025 |
| SNS | $0.50/M | 2,000 publishes | $0.0010 |
| **Total** | | | **≈ $0.0055 (~$0.17/mo)** |

10× volume → ~$0.03/day; batching keeps the Lambda term flat.

## 8. Performance & scale

- End-to-end 0.3–15 s (dominated by batch window at low volume); decision compute <100 ms.
- Peak 50/min: batches fill every ~12 s; single function's default concurrency is over-provisioned.
- Idempotency: conditional PutItem on `event_id` neutralizes at-least-once delivery and re-sends.
- Headroom: 10× volume unchanged design; the first real scaling question is multi-consumer fan-out (then revisit EventBridge).

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| DLQ rot | alarm + redrive runbook (`aws sqs start-message-move-task`) |
| Duplicate delivery | conditional PutItem on `event_id` |
| SNS publish failure | publish AFTER durable write; WARNING + metric; settlement can poll DDB |
| Payload in logs | contract checker in CI; only allowed keys emitted |

## 10. Build status & next steps

- [x] Handler + tests: `day2/src/sample-demo/` (6/6 harness)
- [x] SAM template (queue + DLQ + role + mapping): same folder, `sam validate --lint` clean
- [ ] Deploy: `sam deploy --stack-name accelya-m2-chandra-cancellation`
- [ ] Add DynamoDB table + SNS topic to template (not yet in IaC — extend `template.yaml`)
- [ ] DLQ alarm resource + SNS subscription for settlement
- [ ] HITL review per `docs/HITL_REVIEW_TEMPLATE.md` before merge
