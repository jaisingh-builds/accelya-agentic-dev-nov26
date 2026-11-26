# M2 final solution - cancellation refund decisions (Option 3, revised)

**Author:** dheeraj · **Date:** 2026-11-25 · **Module:** M2 · **Region:** us-east-1
**Based on:** Option 3 in `day2/outputs/m2-arch-variants-dheeraj.md` (Bedrock, Sonnet 4.5), revised to fix the
41 defects found in the review against `AGENTS.md` (traceability in section 11).
**Status:** draft for human review with `docs/HITL_REVIEW_TEMPLATE.md` - not approved until that review passes.

---

## 1. Requirement (from `day2/prompts/m2-arch-variants.md`)

| # | Requirement | How this design meets it |
|---|---|---|
| R1 | Every cancellation event gets APPROVE / REJECT / MANUAL_REVIEW | One Lambda applies a rule table; anything no rule matches becomes MANUAL_REVIEW (never guessed) |
| R2 | Settlement team sees **every** decision | Every decision is written once to DynamoDB; an **unfiltered** pipe copies every new decision to the settlement queue |
| R3 | Order matters per PNR | `pnr` is the FIFO message group on both queues and the DynamoDB partition key; no unordered hop anywhere |
| R4 | 1,000/day, peaks of 10/minute | Every service is request-billed and scales far beyond this; one Lambda, batch of 10 |
| N1 | No Kinesis, MSK, EC2 | API Gateway, SQS, Lambda, DynamoDB, EventBridge Pipes, CloudWatch only |
| N2 | Never log passenger names, emails, fare amounts | Request schema rejects unknown fields; logs follow logging contract v1.1 (identifiers and counts only); API Gateway data tracing off |
| N3 | Python standard library only | Lambda uses `json`, `decimal`, `os`, `time`, `uuid` and the runtime's `boto3` |

## 2. Architecture

```
Caller (booking/agency backend, signed with SigV4)
  |  POST /cancellations   header X-Correlation-Id (optional)
  v
[1] API Gateway REST API  accelya-m2-dheeraj-cancellation-api
      IAM auth · JSON-schema request validation · throttling · direct SQS integration (no Lambda)
  |  SendMessage  group=pnr  dedup=event_id  attribute correlation_id
  v
[2] SQS FIFO  accelya-m2-dheeraj-cancellation.fifo  (SSE, redrive 3 -> cancellation-dlq.fifo)
  |  event source mapping, batch 10, ReportBatchItemFailures
  v
[3] Lambda  accelya-m2-dheeraj-refund-decision  (Python 3.12, 256 MB, 30 s, Decimal money)
  |  conditional PutItem (idempotent)
  v
[4] DynamoDB  accelya-m2-dheeraj-refund-decisions  pk=pnr sk=event_id, stream NEW_IMAGE
  |                                              |
  | Pipe A (INSERT only, NO decision filter)     | Pipe B (INSERT and decision = MANUAL_REVIEW)
  v                                              v
[5] SQS FIFO settlement.fifo  (group=pnr)     [6] SQS FIFO manual-review.fifo  (group=pnr)
    read by: settlement team                      read by: refunds operations team
```

Alarms on every dead-letter queue, Lambda errors, pipe failures and API 5xx go to `accelya-m2-dheeraj-alerts` (SNS).

**What changed from the Bedrock Option 3 and why**
| Bedrock Option 3 | Final | Reason |
|---|---|---|
| `ingestion-handler` Lambda forwards body to SQS | API Gateway -> SQS direct integration | Lambda added no logic (over-abstraction); one less Lambda of the 10 allowed |
| API with no auth | IAM (SigV4) auth + schema validation + throttling | An open endpoint could create refunds |
| Batch size 1 "for ordering" | Batch 10 + `ReportBatchItemFailures` | FIFO keeps order per group inside a batch |
| Unconditional write + GSI on timestamp | Conditional write, no GSI | Idempotency beyond the 5-minute FIFO dedup window; GSI had no query |
| Pipe filtered on `decision` | Pipe A unfiltered (INSERT only) | A decision filter drops decisions - breaks R2 |
| EventBridge bus target | SQS FIFO target, group = pnr | A bus doesn't keep order (R3) and had no rule, so events vanished |
| MANUAL_REVIEW not routed | Pipe B -> manual-review FIFO queue | Someone must own manual cases |

## 3. Resource inventory

All names follow `accelya-m2-<learner>-`. Defined in one SAM template (`day2/template.yaml`, to be written) - no console changes.

| Resource | Name | Key settings |
|---|---|---|
| REST API | `accelya-m2-dheeraj-cancellation-api` | `AWS_IAM` auth, request validator (body), stage throttle 20 rps / burst 50, data trace **off**, execution logging `ERROR`, access log without body |
| Request model | `CancellationEvent` | schema in section 4 |
| SQS FIFO | `accelya-m2-dheeraj-cancellation.fifo` | SSE-SQS, visibility timeout 180 s (6 x Lambda timeout), retention 4 days, redrive `maxReceiveCount=3` |
| SQS FIFO DLQ | `accelya-m2-dheeraj-cancellation-dlq.fifo` | SSE-SQS, retention 14 days |
| Lambda | `accelya-m2-dheeraj-refund-decision` | Python 3.12, 256 MB, 30 s, reserved concurrency 5, env `TABLE_NAME`, `LEARNER_ID`, `CONTRACT_VERSION=1.1` |
| Event source mapping | SQS -> Lambda | batch 10, `FunctionResponseTypes: [ReportBatchItemFailures]` |
| Log group | `/aws/lambda/accelya-m2-dheeraj-refund-decision` | retention 7 days |
| Log group | `/aws/apigateway/accelya-m2-dheeraj-cancellation-api` | access logs, retention 7 days |
| DynamoDB | `accelya-m2-dheeraj-refund-decisions` | on-demand, pk `pnr` (S), sk `event_id` (S), stream `NEW_IMAGE`, PITR on, encryption at rest (default) |
| Pipe A | `accelya-m2-dheeraj-settlement-pipe` | source: table stream, start `TRIM_HORIZON`, batch 10, parallelization factor **1**, filter `eventName = INSERT`, retries 10, on-failure DLQ |
| Pipe B | `accelya-m2-dheeraj-review-pipe` | as Pipe A + filter `decision = MANUAL_REVIEW` |
| SQS FIFO | `accelya-m2-dheeraj-settlement.fifo` | SSE-SQS; group = `pnr`, dedup = `event_id` |
| SQS FIFO | `accelya-m2-dheeraj-manual-review.fifo` | SSE-SQS; group = `pnr`, dedup = `event_id` |
| SQS DLQs (standard) | `accelya-m2-dheeraj-settlement-pipe-dlq`, `accelya-m2-dheeraj-review-pipe-dlq` | SSE-SQS, retention 14 days (pipe on-failure destinations) |
| SNS | `accelya-m2-dheeraj-alerts` | alarm notifications |
| Alarms (6) | `accelya-m2-dheeraj-*` | 3 x DLQ `ApproximateNumberOfMessagesVisible > 0`, Lambda `Errors > 0`, API `5XXError > 0`, pipe execution failures `> 0` (5-minute periods) |

**Sandbox fit:** 1 Lambda (course limit 10), 256 MB (limit 2048), no public buckets, us-east-1 only.

## 4. Contracts

### 4.1 Request (API Gateway model, enforced before any compute)
```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["event_id", "pnr", "ticket_number", "fare_class", "departure_status", "no_show_flag", "fare_amount"],
  "properties": {
    "event_id":         {"type": "string", "pattern": "^[A-Za-z0-9-]{1,40}$"},
    "pnr":              {"type": "string", "pattern": "^[A-Z0-9]{6}$"},
    "ticket_number":    {"type": "string", "pattern": "^\\d{3}-\\d{10}$"},
    "fare_class":       {"type": "string", "pattern": "^[A-Z]$"},
    "departure_status": {"type": "string", "maxLength": 20},
    "no_show_flag":     {"type": "boolean"},
    "fare_amount":      {"type": "string", "pattern": "^\\d{1,9}\\.\\d{2}$"}
  }
}
```
`additionalProperties: false` means passenger names or emails can't enter the pipeline at all.
`fare_amount` is a string so it is never a float anywhere.

Response: `202 Accepted` with `{"event_id": ..., "status": "accepted"}`. The decision is made asynchronously -
callers do **not** get the decision in the response (the Bedrock answer's "real-time caller feedback" was wrong).

### 4.2 API Gateway -> SQS integration (request template sketch)
```
Action=SendMessage
&MessageBody=$util.urlEncode($input.body)
&MessageGroupId=$util.urlEncode($input.path('$.pnr'))
&MessageDeduplicationId=$util.urlEncode($input.path('$.event_id'))
&MessageAttribute.1.Name=correlation_id
&MessageAttribute.1.Value.DataType=String
#if($input.params('X-Correlation-Id') != "")
&MessageAttribute.1.Value.StringValue=$util.urlEncode($input.params('X-Correlation-Id'))
#else
&MessageAttribute.1.Value.StringValue=$context.requestId
#end
```

### 4.3 Decision record (DynamoDB item = what settlement receives)
| Attribute | Type | Note |
|---|---|---|
| `pnr` | S | partition key |
| `event_id` | S | sort key |
| `ticket_number`, `fare_class` | S | from the event |
| `decision` | S | APPROVE / REJECT / MANUAL_REVIEW |
| `refund_rule_id` | S | e.g. `R-SAVER`; absent when no rule matched |
| `refund_amount` | N | Decimal, 2 places, ROUND_HALF_UP; absent for REJECT / MANUAL_REVIEW |
| `correlation_id` | S | carried from the SQS message attribute |
| `decided_at` | S | UTC ISO 8601 |

`fare_amount` is **not** stored in the decision record - settlement needs the refund, not the fare.

### 4.4 Settlement / review message (Pipe input transformer)
```json
{"event_id": "<$.dynamodb.NewImage.event_id.S>", "pnr": "<$.dynamodb.NewImage.pnr.S>",
 "ticket_number": "<$.dynamodb.NewImage.ticket_number.S>", "decision": "<$.dynamodb.NewImage.decision.S>",
 "refund_rule_id": "<$.dynamodb.NewImage.refund_rule_id.S>", "refund_amount": "<$.dynamodb.NewImage.refund_amount.N>",
 "correlation_id": "<$.dynamodb.NewImage.correlation_id.S>", "decided_at": "<$.dynamodb.NewImage.decided_at.S>"}
```
Target parameters: `MessageGroupId = $.dynamodb.NewImage.pnr.S`, `MessageDeduplicationId = $.dynamodb.NewImage.event_id.S`.

## 5. Decision logic (Lambda)

1. For each SQS record in order: read `correlation_id` from message attributes (mint `m2-<uuid8>` and log WARNING if
   missing or not matching `^[A-Za-z0-9._:-]{1,64}$`).
2. Parse the body; on missing/invalid fields log ERROR `cancellation.invalid` (`error_type=ValidationError`) and report
   the record as failed (it reaches the DLQ after 3 receives and raises an alarm). The API schema makes this rare.
3. Look up the **rule table** (data, not code branches). Only one rule is known from the requirement:

   | refund_rule_id | Match | Decision | Refund |
   |---|---|---|---|
   | `R-SAVER` | fare_class `K`, departure_status `scheduled`, no_show_flag `false` | APPROVE | 75% of fare_amount |

   Any event no rule matches -> **MANUAL_REVIEW** with no refund. REJECT rules must come from the revenue-accounting
   business owner (open question Q1) - they are not invented here.
4. Money: `(Decimal(fare_amount) * Decimal("0.75")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` ->
   `4120.00` gives `3090.00`, matching the prompt example. Never `float`.
5. `PutItem` with `ConditionExpression="attribute_not_exists(event_id)"`.
   `ConditionalCheckFailedException` = already decided -> log WARNING `cancellation.duplicate`, treat as success.
6. **FIFO partial failure rule:** on the first failed record, report it **and every later record in the batch** as
   `batchItemFailures`, so no PNR's later message is processed before an earlier one.

## 6. Six service-boundary questions, every hop

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role (principal -> named actions on ARN) | 5 Failure path | 6 Correlation id |
|---|---|---|---|---|---|---|
| Caller -> API Gateway | Push | Sync, 29 s max (answers in ms) | Request schema 4.1 | Caller's IAM principal -> `execute-api:Invoke` on `arn:aws:execute-api:us-east-1:<acct>:<api-id>/prod/POST/cancellations` | 400 schema error (no retry); 403 auth; 429 throttled -> caller retries with backoff; 5xx -> caller retries, safe because `event_id` dedups | `X-Correlation-Id` header, else `$context.requestId` |
| API Gateway -> SQS FIFO | Push | Sync | Body unchanged, group `pnr`, dedup `event_id` | `accelya-m2-dheeraj-apigw-sqs-role` (trust `apigateway.amazonaws.com`) -> `sqs:SendMessage` on `cancellation.fifo` ARN | Send fails -> 500 to caller, who retries; a retry inside 5 min is deduped by SQS, later ones by the conditional write | Message attribute `correlation_id` |
| SQS FIFO -> Lambda | Pull (event source mapping polls) | Async to the caller; batch of 10 | SQS records (body 4.1 + attributes) | Function role `accelya-m2-dheeraj-refund-decision-role` -> `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on `cancellation.fifo` ARN | Partial batch failure (rule in 5.6); after 3 receives -> `cancellation-dlq.fifo` + alarm; the failing PNR group waits, other PNRs continue | Read from message attribute |
| Lambda -> DynamoDB | Push | Sync, SDK retries with backoff | Item 4.3 | Same function role -> `dynamodb:PutItem` on table ARN; `logs:CreateLogStream`, `logs:PutLogEvents` on its log group ARN | Conditional fail = duplicate (success); throttling/other `ClientError` -> ERROR log with `error_type`, record failed -> SQS retry -> DLQ | Stored as item attribute |
| DynamoDB stream -> Pipe A / B | Pull (pipe polls stream) | Async | Stream record `NEW_IMAGE` | Pipe roles `accelya-m2-dheeraj-settlement-pipe-role` / `-review-pipe-role` -> `dynamodb:DescribeStream`, `dynamodb:GetRecords`, `dynamodb:GetShardIterator` on the **stream** ARN | Retries 10, then the record goes to the pipe's on-failure DLQ + alarm (not lost silently at 24 h) | In `NEW_IMAGE.correlation_id` |
| Pipe A -> settlement.fifo | Push | Async | Message 4.4, group `pnr`, dedup `event_id` | Settlement pipe role -> `sqs:SendMessage` on `settlement.fifo` ARN and on `settlement-pipe-dlq` ARN | Same as row above | In message body |
| Pipe B -> manual-review.fifo | Push | Async | Message 4.4 (MANUAL_REVIEW only) | Review pipe role -> `sqs:SendMessage` on `manual-review.fifo` ARN and `review-pipe-dlq` ARN | Same as row above | In message body |
| settlement.fifo -> settlement team | Pull | Async | Message 4.4 | Settlement team's role -> `sqs:ReceiveMessage`, `sqs:DeleteMessage` on `settlement.fifo` ARN (granted by queue policy) | Their redrive policy; out of this design's scope - agreed contract only | In message body |

No `*` actions or resources anywhere. Pipe trust policy: `pipes.amazonaws.com` with `aws:SourceArn` = the pipe ARN.
Open item: confirm whether Pipes also needs `dynamodb:ListStreams`; that action can't be scoped to an ARN, so it
would need a documented exception in review (Q4).

## 7. Observability

**Logs** - one JSON line per record, logging contract v1.1, checked with `python3 day2/scripts/check_logs.py`.
`service` = `accelya-m2-cancellation`; only keys from `docs/logging-contract.json`; never `fare_amount`, `refund_amount`,
the message body or raw exception text.

```json
{"ts": "2026-11-25T10:15:02Z", "level": "INFO", "service": "accelya-m2-cancellation", "correlation_id": "c-7f3a91", "boundary": "sqs->lambda", "message": "cancellation.received", "learner_id": "dheeraj", "event_id": "evt-1002", "pnr": "LM4K8D", "message_id": "4f1c2a9e-0b7d-4c55-9e1f-3a2b6c8d0e11", "attempt": 1}
{"ts": "2026-11-25T10:15:02Z", "level": "INFO", "service": "accelya-m2-cancellation", "correlation_id": "c-7f3a91", "boundary": "lambda->dynamodb", "message": "cancellation.decided", "learner_id": "dheeraj", "event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "refund_rule_id": "R-SAVER", "decision": "APPROVE"}
{"ts": "2026-11-25T10:15:03Z", "level": "WARNING", "service": "accelya-m2-cancellation", "correlation_id": "c-7f3a91", "boundary": "lambda->dynamodb", "message": "cancellation.duplicate", "learner_id": "dheeraj", "event_id": "evt-1002", "pnr": "LM4K8D"}
{"ts": "2026-11-25T10:15:04Z", "level": "ERROR", "service": "accelya-m2-cancellation", "correlation_id": "c-8a21b0", "boundary": "lambda->dynamodb", "message": "cancellation.failed", "learner_id": "dheeraj", "event_id": "evt-1003", "pnr": "QX7P2M", "error_type": "ClientError", "error_code": "ProvisionedThroughputExceededException"}
```

**API Gateway:** access log fields `requestId`, `status`, `integrationStatus`, `responseLatency` only. Data trace
(full request/response logging) stays **off** because the body carries `fare_amount`.

**Alarms:** the 6 alarms in section 3. A message in any DLQ always pages someone - nothing fails silently.

**End-to-end trace:** search one `correlation_id` across the Lambda log group, the DynamoDB item and the settlement message.

## 8. Security summary

- IAM auth on the API; throttling; request schema with `additionalProperties: false`.
- Least privilege: four roles, each with named actions on single ARNs (section 6). No resource policy grants to `*`.
- Encryption: SSE-SQS on all five queues, DynamoDB encryption at rest (default), TLS everywhere.
- Data minimisation: `fare_amount` stops at the Lambda; decision records and settlement messages carry only the refund.
- DLQs hold full payloads -> only the function role and named operators can read them; 14-day retention.
- Queue policies on `settlement.fifo` and `manual-review.fifo` grant receive/delete to the owning team's role only.
- No secrets in code or template; config from environment variables.

## 9. Cost (monthly, 30,000 events, list prices, free tier ignored)

Method as in `m2-arch-variants-dheeraj.md`; assumptions: Lambda 256 MB x 200 ms, 2 KB logs per invoke,
~10% MANUAL_REVIEW, 3 SQS requests per message.

| Line | Calculation | Cost |
|---|---|---|
| API Gateway REST | 30,000 x $3.50/1M | $0.105 |
| SQS FIFO cancellation | 90,000 x $0.50/1M | $0.045 |
| Lambda requests + compute | 30,000 x $0.20/1M + 1,500 GB-s x $0.0000166667 | $0.031 |
| DynamoDB writes (no GSI) | 30,000 x $0.625/1M | $0.019 |
| Pipe A + Pipe B | (30,000 + 3,000) x $0.40/1M | $0.013 |
| SQS FIFO settlement + review | (90,000 + 9,000) x $0.50/1M | $0.050 |
| CloudWatch Logs | 0.06 GB Lambda + 0.015 GB API access x $0.50 | $0.038 |
| **Subtotal (per-request services)** | | **~$0.30** |
| CloudWatch alarms | 6 x $0.10 | $0.60 |
| **Total** | | **~$0.90** |

Alarms are two-thirds of the bill at this volume and are still worth it - they are what turns a silent failure into
a page. Cost per decision ≈ $0.00003. Verify prices in the AWS Pricing Calculator before quoting.

## 10. Risks and open questions

| # | Item | Owner / next step |
|---|---|---|
| Q1 | Only `R-SAVER` is known. Full refund and REJECT rule table needed | Revenue accounting - until then everything else goes to MANUAL_REVIEW by design |
| Q2 | Who calls the API, and can they sign SigV4? If agents call directly, switch to a Cognito/JWT authorizer | Product owner |
| Q3 | A poison message blocks its own PNR group for up to 3 receives (~9 min) before reaching the DLQ | Accepted: other PNRs are unaffected; DLQ alarm fires |
| Q4 | Does EventBridge Pipes need `dynamodb:ListStreams` (not ARN-scopable)? | Check in the sandbox; document as the only exception if so |
| Q5 | A DynamoDB stream supports two concurrent readers per shard - Pipes A and B use both | Any third consumer must read from `settlement.fifo`, not the stream |
| Q6 | Is EventBridge Pipes available in the Pluralsight sandbox? | Test with `sam deploy` before Day 3; fallback: one small forwarder Lambda (2 of 10) |
| Q7 | Stream ordering is per item and per partition key in practice; verify under a 10x burst | Load test with repeated PNRs |

## 11. Defect traceability (review of `m2-arch-variants-dheeraj.md`)

| Defect # | Category | Resolution in this design |
|---|---|---|
| 1 | Other (naming) | Every name `accelya-m2-dheeraj-*` (section 3) |
| 2 | Other (IAM) | Each role has named actions on named ARNs (section 6) |
| 3 | Payload logging | Logging contract v1.1 lines (section 7); data trace off; schema blocks names/emails |
| 4 | Other (logging contract) | `correlation_id` carried as SQS message attribute -> item -> settlement message; `boundary` and `error_type` on every line |
| 5 | Wrong business rule | MANUAL_REVIEW routed by Pipe B to `manual-review.fifo` with an owning team |
| 6 | Other (money) | Decimal + ROUND_HALF_UP; `fare_amount` is a string end to end (section 5) |
| 7-27 | Various | Options 1 and 2 not selected |
| 28 | Other (security) | IAM auth, throttling, request validation |
| 29 | Over-abstraction | Ingestion Lambda removed; API Gateway -> SQS direct |
| 30 | Other (IAM) | No Lambda invoke from API Gateway any more; integration role scoped to `sqs:SendMessage` on one queue |
| 31 | Hallucinated API | Batch 10 + `ReportBatchItemFailures` with the FIFO partial-failure rule; function execution role named correctly |
| 32 | Missing error path | Conditional `PutItem`; duplicates logged and acknowledged |
| 33 | Over-abstraction | GSI removed |
| 34 | Wrong business rule | Pipe A has no decision filter (INSERT only) |
| 35 | Wrong business rule | Settlement target is SQS FIFO grouped by `pnr` |
| 36 | Silent failure | Defined targets + pipe DLQs + alarms |
| 37 | Hallucinated API | Batch-1 claim dropped |
| 38 | Other (cost) | Cost recalculated per event (section 9) |
| 39 | Wrong business rule | Ordering now holds on every hop (R3 table, section 1) |
| 40 | Other | Response is `202 Accepted`; decision is asynchronous (section 4.1) |
| 41 | Other | Service count stated from the inventory (section 3) |

## 12. Next steps

1. Human review of this document with `docs/HITL_REVIEW_TEMPLATE.md` -> `day2/reviews/HITL_REVIEW-m2-final-solution.md`.
2. Resolve Q1, Q2, Q4, Q6.
3. Write `day2/template.yaml` (SAM) and the Lambda; unit-test the rule table, Decimal rounding, duplicate and
   partial-failure paths; run `day2/scripts/check_logs.py` on real logs.
4. Add the stack to `start-of-day.ps1` once it deploys.
