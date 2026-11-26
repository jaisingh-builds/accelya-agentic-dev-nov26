# Refund decision service - final design (Option 1: SQS FIFO + Lambda + DynamoDB) - shailya

_Status: DRAFT for human review (see section 13). Selected from the three options in `m2-arch-variants-shailya.v1.md`.
Original options drafted with Bedrock model `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (461/1910 tokens); this document was revised by hand.
The refund decision itself does **not** call Bedrock: it is deterministic (section 6)._

## 1. Problem statement

A passenger or agent cancels a ticket. The cancellation event (`pnr`, `ticket_number`, `fare_class`, `departure_status`,
`no_show_flag`, `fare_amount`) must get exactly one refund decision (`APPROVE` / `REJECT` / `MANUAL_REVIEW`), and the
settlement team must see every decision.

- Volume: about 2,000 cancellations/day (about 60,000/month).
- Ordering: events for the same `pnr` are processed in the order they were sent.
- Non-goals: no Kinesis, no MSK, no EC2. Passenger names, emails and fare amounts are never logged.

## 2. Summary

A FIFO queue grouped by `pnr` feeds one refund-decision Lambda. The Lambda validates the event, checks that the PNR
is not blocked, applies versioned rules (stored as data) with a pure function, and writes the decision to DynamoDB
in one transaction. The transaction refuses a second decision for the same `event_id` **or** the same `ticket_number`.
Events that can never succeed become `MANUAL_REVIEW`; temporary failures are retried and then go to a FIFO DLQ, which
blocks the PNR so ordering is never broken silently.

```
Producer --SendMessage--> accelya-m2-shailya-refund-cancel.fifo  (group = pnr, dedup = event_id)
                              |  event source mapping (batch 10, partial batch failures)
                              v
                     accelya-m2-shailya-refund-decision  (Lambda, python3.12, 256 MB)
                              |  GetItem (blocked?)  +  TransactWriteItems (decision + ticket guard)
                              v
                     accelya-m2-shailya-refund-decisions  (DynamoDB, on-demand, PITR)
                              |  Query on GSI settlement_date
                              v
                     Settlement team (read-only role, query script)

after 5 failed receives -> accelya-m2-shailya-refund-cancel-dlq.fifo -> alarm -> accelya-m2-shailya-refund-alerts (SNS)
```

## 3. Platform and resources

All resources are defined in one AWS SAM template (`template.yaml`) and deployed with `sam deploy` to **us-east-1**.
Nothing is created by hand in the console. Lambda functions used: **1** of the 10 allowed in the sandbox.

| Resource | Name | Key settings |
|---|---|---|
| SQS FIFO queue | `accelya-m2-shailya-refund-cancel.fifo` | `FifoQueue: true`, `ContentBasedDeduplication: false`, `DeduplicationScope: messageGroup`, `FifoThroughputLimit: perMessageGroupId`, visibility timeout 180 s (6 x Lambda timeout), retention 4 days, `SqsManagedSseEnabled: true`, redrive `maxReceiveCount: 5` |
| SQS FIFO DLQ | `accelya-m2-shailya-refund-cancel-dlq.fifo` | retention 14 days, `SqsManagedSseEnabled: true`, `RedriveAllowPolicy: byQueue` (only the main queue) |
| Lambda | `accelya-m2-shailya-refund-decision` | `python3.12`, 256 MB, timeout 30 s, reserved concurrency 5, event source mapping batch size 10, `FunctionResponseTypes: [ReportBatchItemFailures]` |
| Log group | `/aws/lambda/accelya-m2-shailya-refund-decision` | created in SAM, retention 30 days |
| DynamoDB table | `accelya-m2-shailya-refund-decisions` | on-demand, keys `pk`/`sk`, encryption at rest (default), PITR on, `DeletionProtectionEnabled: true` |
| DynamoDB GSI | `accelya-m2-shailya-settlement-date-idx` | `gsi1pk` = `settlement_date`, `gsi1sk` = `decided_at#event_id`, projection: decision attributes only |
| SNS topic (alarms only) | `accelya-m2-shailya-refund-alerts` | email subscription for the on-call engineer; carries alarm text only, no event data |
| CloudWatch alarms | `accelya-m2-shailya-dlq-depth`, `accelya-m2-shailya-oldest-msg-age`, `accelya-m2-shailya-lambda-errors`, `accelya-m2-shailya-manual-review` | see section 9 |
| IAM roles | `accelya-m2-shailya-refund-decision-role`, `accelya-m2-shailya-settlement-read-role`, `accelya-m2-shailya-dlq-replay-role` | section 8 |

### Environment variables (Lambda)

No secrets, keys or account ids in code. Every ARN and name comes from SAM (`!Ref` / `!GetAtt`).

| Variable | Value |
|---|---|
| `DECISIONS_TABLE` | `!Ref DecisionsTable` |
| `SERVICE_NAME` | `accelya-m2-shailya-refund-decision` |
| `MAX_RECEIVE_COUNT` | `5` (the same SAM parameter as the redrive policy) |
| `LOG_LEVEL` | `INFO` |

## 4. Event contract (input)

The producer (the ticket cancellation system; **owner to be confirmed**) sends one message per cancellation:

- `MessageGroupId` = `pnr` (needed for per-PNR ordering)
- `MessageDeduplicationId` = `event_id` (needed for FIFO deduplication)

| Field | Type | Validation |
|---|---|---|
| `event_id` | string | required, `^[A-Za-z0-9-]{1,64}$` |
| `pnr` | string | required, `^[A-Z0-9]{6}$` |
| `ticket_number` | string | required, `^\d{13}$` |
| `fare_class` | string | required, `^[A-Z]$` |
| `departure_status` | string | required, allow-list (for example `scheduled`, `departed`; final list to be confirmed by the business) |
| `no_show_flag` | boolean | required |
| `fare_amount` | string | required, `^\d{1,9}\.\d{2}$`, > 0. Kept as a string on the wire so no float conversion ever happens |

Example (from the brief, with `ticket_number` added because the brief lists it as required):

```json
{"event_id": "evt-1002", "pnr": "LM4K8D", "ticket_number": "0000000000001", "fare_class": "K",
 "departure_status": "scheduled", "no_show_flag": false, "fare_amount": "4120.00"}
```
Expected result: `APPROVE`, rule `R-SAVER`, `refund_fare_amount` `3090.00`.

## 5. Data model (single table)

| Record | `pk` | `sk` | Attributes |
|---|---|---|---|
| Decision record | `PNR#<pnr>` | `EVT#<event_id>` | `pnr`, `ticket_number`, `event_id`, `fare_class`, `decision`, `refund_rule`, `rules_version`, `reason_code`, `refund_fare_amount` (Number, 2 dp), `settlement_date` (UTC `YYYY-MM-DD` of `decided_at`), `decided_at` (ISO-8601 UTC), `gsi1pk`, `gsi1sk` |
| Ticket guard | `TKT#<ticket_number>` | `GUARD` | `event_id` of the first decision for this ticket |
| PNR block marker | `PNR#<pnr>` | `BLOCK` | `event_id` that failed, `blocked_at` |

- `settlement_date` = the UTC date of the decision, set by the Lambda.
- Only decision records carry `gsi1pk`/`gsi1sk`, so only they appear in the GSI.

## 6. Deterministic decision logic

The decision is made by a **pure function**, with no Bedrock call, no randomness, no network call and no clock read:

```
decide(event, rules, pnr_blocked) -> (decision, refund_rule, reason_code, refund_fare_amount)
```

- **Rules are data.** `rules/refund_rules.json` ships in the Lambda package with a `rules_version`. Every decision
  stores `refund_rule` and `rules_version`, so the same event with the same rules version always gives the same result
  and any decision can be re-derived later.
- **Fixed precedence. The first match wins, and there is always exactly one outcome:**
  1. Schema validation fails -> `MANUAL_REVIEW`, `reason_code=INVALID_EVENT`
  2. PNR is blocked -> `MANUAL_REVIEW`, `reason_code=PNR_BLOCKED`
  3. `no_show_flag = true` -> rule from the rules file (for example `REJECT`, `R-NOSHOW`)
  4. `departure_status` is not refundable -> rule from the rules file
  5. A `fare_class` rule matches -> `APPROVE` with that rule's percentage (for example K -> `R-SAVER` 75%)
  6. Nothing matches -> `MANUAL_REVIEW`, `reason_code=NO_RULE`
- **Money:** `fare_amount = Decimal(event["fare_amount"])`; the body is parsed with
  `json.loads(body, parse_float=Decimal)`; `refund_fare_amount = (fare_amount * pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`.
  `pct` is read from the rules file as a string and converted to `Decimal`. A float is never used.
- `decided_at` and `settlement_date` are metadata recorded **after** the decision. They are never inputs to it.
- Only `R-SAVER` (K, 75%) is confirmed by the brief. **The full rule table must be signed off by the business**
  before go-live; until then, anything without a rule goes to `MANUAL_REVIEW`.

## 7. Processing flow and failure handling

For each record in the batch, **in order**:

1. Parse and validate. Failure -> write a `MANUAL_REVIEW` decision (`INVALID_EVENT`) and treat the message as done.
   Retrying could never fix it, so it never reaches the DLQ.
2. `GetItem` `PNR#<pnr>` / `BLOCK`. If the marker exists -> `MANUAL_REVIEW` (`PNR_BLOCKED`).
3. `decide(...)`.
4. `TransactWriteItems`:
   - Put the decision record, condition `attribute_not_exists(pk)`
   - Put the ticket guard, condition `attribute_not_exists(pk)`
5. If the transaction is cancelled, read `CancellationReasons`:
   - The decision put failed -> the same `event_id` was already processed -> skip (INFO, idempotent).
   - Only the ticket guard failed -> a different event for a ticket that already has a decision -> write a separate
     `MANUAL_REVIEW` decision (`reason_code=DUPLICATE_TICKET`), without a guard. **A ticket can never be refunded twice.**
6. Temporary error (throttling, timeout):
   - If `ApproximateReceiveCount >= MAX_RECEIVE_COUNT`, write the PNR block marker first (this is the last attempt
     before the DLQ).
   - Report this record **and every later record in the batch** in `batchItemFailures` (FIFO rule), and stop
     processing the batch.

Result: ordering for a PNR is either kept, or the PNR is visibly blocked. It is never broken silently.

### Exception mapping

| Exception | Boundary | Level | `error_type` | Action |
|---|---|---|---|---|
| `json.JSONDecodeError` | `validate` | ERROR | `JSONDecodeError` | `MANUAL_REVIEW` `INVALID_EVENT`, no retry |
| `ValueError` / `KeyError` (schema) | `validate` | ERROR | `ValueError` / `KeyError` | `MANUAL_REVIEW` `INVALID_EVENT`, no retry |
| `decimal.InvalidOperation` | `validate` | ERROR | `InvalidOperation` | `MANUAL_REVIEW` `INVALID_EVENT`, no retry |
| `ClientError` code `TransactionCanceledException` (`ConditionalCheckFailed`) | `ddb_write` | INFO | - | duplicate event: skip; duplicate ticket: `MANUAL_REVIEW` |
| `ClientError` code `ProvisionedThroughputExceededException` / `ThrottlingException` / `RequestLimitExceeded` | `ddb_write` / `ddb_read` | ERROR | the error code | retry through SQS (after boto3's built-in retries) |
| `botocore.exceptions.ConnectTimeoutError` / `ReadTimeoutError` | `ddb_write` / `ddb_read` | ERROR | class name | retry through SQS |
| Any other `ClientError` | `ddb_write` / `ddb_read` | ERROR | the error code | retry through SQS; DLQ after 5 attempts |

There is no bare `except Exception`. `str(exc)` is never logged; only `type(exc).__name__` or the AWS error code is.

## 8. IAM (named actions, ARN-scoped, no `*`)

| Principal | Actions | Resource |
|---|---|---|
| Producer role (owned by the producer team) | `sqs:SendMessage` | main queue ARN |
| `accelya-m2-shailya-refund-decision-role` | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | main queue ARN |
| | `dynamodb:GetItem`, `dynamodb:PutItem` (used by `TransactWriteItems`) | table ARN |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | the function's log group ARN |
| `accelya-m2-shailya-settlement-read-role` | `dynamodb:Query` | GSI ARN only |
| `accelya-m2-shailya-dlq-replay-role` | `sqs:StartMessageMoveTask`, `sqs:ListMessageMoveTasks`, `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | DLQ ARN |
| | `sqs:SendMessage` | main queue ARN |
| | `dynamodb:DeleteItem` (clear the block marker) | table ARN |
| CloudWatch alarms -> SNS | topic policy: `sns:Publish` for `cloudwatch.amazonaws.com`, condition `aws:SourceArn` = the alarm ARNs | topic ARN |

Queue resource policy (both queues): allow `sqs:SendMessage` only from the producer role (main queue) or through
redrive (DLQ), and **deny all actions when `aws:SecureTransport` is `false`**.

## 9. Security, logging and observability

**Security**
- Encryption at rest: SSE-SQS on both queues, DynamoDB default encryption. No customer-managed KMS key, so no KMS
  permissions are needed.
- Encryption in transit: the queue policies deny non-TLS requests.
- Every input is untrusted and checked against the strict schema in section 4.
- PITR and deletion protection on the table keep an audit trail and prevent accidental deletion.
- Reserved concurrency of 5 stops a backlog from flooding DynamoDB or starving other functions.
- The settlement read role can only `Query` the GSI. It sees `refund_fare_amount` by design; access to the role is
  restricted to the settlement team.
- Nothing is public. No S3 is used.

**Logging** (follows `docs/LOGGING_CONTRACT.md`; checked with `python3 day2/scripts/check_logs.py`)
- Every line carries `ts`, `level`, `service`, `correlation_id` (= `event_id`), `boundary`, `message`; error lines add `error_type`.
- Boundaries: `sqs_receive`, `validate`, `ddb_read`, `decide`, `ddb_write`, `batch_result`.
- Logged: identifiers and counts only (for example `message="decision_written"`, `message="decision_manual_review"`,
  `message="batch_done"`). **Never** logged: the message body, `fare_amount`, `refund_fare_amount`, passenger data or
  raw exception text.
- No log key outside `docs/logging-contract.json`. **To check:** if `decision` or `pnr` is not an allowed key, put
  only the outcome word into `message`.

**Alarms** (all notify `accelya-m2-shailya-refund-alerts`)

| Alarm | Metric | Threshold |
|---|---|---|
| `accelya-m2-shailya-dlq-depth` | DLQ `ApproximateNumberOfMessagesVisible` | >= 1 |
| `accelya-m2-shailya-oldest-msg-age` | main queue `ApproximateAgeOfOldestMessage` | > 900 s (a message group is stuck) |
| `accelya-m2-shailya-lambda-errors` | Lambda `Errors` | >= 5 in 5 min |
| `accelya-m2-shailya-manual-review` | log metric filter on `message = "decision_manual_review"` | > 50 per day (the settlement team's queue is building up) |

## 10. Service boundary analysis (six questions per hop)

| # | Hop | Push/Pull | Sync/Async | Payload shape | IAM | Failure path | Correlation ID |
|---|---|---|---|---|---|---|---|
| 1 | Producer -> SQS FIFO | Push | Sync (`SendMessage`) | Event in section 4; group = `pnr`, dedup = `event_id` | Producer role: `sqs:SendMessage` on queue ARN; TLS required | Producer retries with the same `event_id`; FIFO deduplicates within 5 min, and the DynamoDB guard covers later resends | `event_id` |
| 2 | SQS FIFO -> Lambda | Pull (event source mapping) | Async | Up to 10 records, in order per `pnr` | Lambda role: Receive/Delete/GetQueueAttributes on queue ARN | Partial batch failure: the failed record and all later records are returned; retried up to 5 times | `event_id` -> `correlation_id` |
| 3 | Lambda -> DynamoDB (block check) | Push | Sync (`GetItem`) | key `PNR#<pnr>` / `BLOCK` | `dynamodb:GetItem` on table ARN | Throttling or timeout -> retry through SQS | `event_id` |
| 4 | Lambda -> DynamoDB (decision) | Push | Sync (`TransactWriteItems`) | Decision record + ticket guard (section 5) | `dynamodb:PutItem` on table ARN | Same event -> skip; same ticket -> `MANUAL_REVIEW` `DUPLICATE_TICKET`; temporary error -> retry | `event_id` in `sk` and attribute |
| 5 | SQS FIFO -> DLQ | Push (redrive) | Async | Original message unchanged | Redrive policy; DLQ `RedriveAllowPolicy` = main queue only | Before the move, the Lambda writes the PNR block marker; DLQ depth alarm fires | `event_id` in body |
| 6 | DLQ -> SQS FIFO (replay) | Push (`StartMessageMoveTask`, run by `scripts/replay_dlq.sh`) | Async | Original message | DLQ replay role (section 8) | Runbook: fix the cause -> delete the block marker -> move messages -> check `ListMessageMoveTasks`. Later events for the PNR already decided `MANUAL_REVIEW` stay with the settlement team | `event_id` |
| 7 | Lambda -> CloudWatch Logs | Push | Async | Log lines per the logging contract, identifiers only | `logs:CreateLogStream`, `logs:PutLogEvents` on the log group ARN | Lambda runtime handles delivery; the log metric filter feeds the manual-review alarm | `correlation_id` |
| 8 | CloudWatch alarm -> SNS -> on-call | Push | Async | Alarm name and state only | Topic policy limited to the alarm ARNs | Alarm stays in ALARM state and is visible in the console | alarm name |
| 9 | DynamoDB -> settlement team | Pull | Sync (`Query` on GSI, `scripts/query_decisions.py --settlement-date YYYY-MM-DD`) | Decision records for one `settlement_date` | Settlement read role: `dynamodb:Query` on GSI ARN | Error returned to the caller; script exits non-zero | `event_id` on every record |

## 11. Risks

| Risk | Mitigation |
|---|---|
| **A ticket is refunded twice** because a resend arrives with a new `event_id` | Ticket guard in the same transaction; a second decision for the ticket -> `MANUAL_REVIEW` `DUPLICATE_TICKET` |
| **Per-PNR order breaks silently** after a DLQ move or a wrong batch-failure response | Block marker before the DLQ move; return the failed record and all later records; answer-key test for both |
| **A poison message holds up its PNR's message group** for up to 5 attempts | Permanent errors never retry (they become `MANUAL_REVIEW`); oldest-message-age alarm |
| **The settlement team misses `MANUAL_REVIEW` decisions** | Daily query script by `settlement_date` plus the manual-review alarm |
| **A rules change alters outcomes** | `rules_version` on every decision; a rule change requires a new version and the answer-key tests to pass |

## 12. Testing (deterministic)

- Unit tests on `decide()` using an answer-key file of synthetic events and their expected decisions, including:
  the brief's example (4120.00 -> 3090.00, `R-SAVER`), a rounding boundary (x.xx5 -> ROUND_HALF_UP), `no_show_flag`,
  no matching rule, an invalid event, and a blocked PNR.
- Batch-handler tests: a failure in the middle of a batch returns that record and every later record; duplicate
  `event_id` -> skip; duplicate `ticket_number` -> `MANUAL_REVIEW`.
- Log check: `python3 day2/scripts/check_logs.py` on the captured log lines.
- Synthetic data only. No real PNRs, tickets or passenger data.

## 13. Cost (us-east-1 list prices, approximate, before free tier)

About 60,000 events/month, Lambda 256 MB at about 200 ms per event.

| Component | Usage per month | Cost |
|---|---|---|
| SQS FIFO requests | ~180,000 | $0.09 |
| Lambda requests + compute | 60,000 invocations, ~3,000 GB-s | $0.06 |
| DynamoDB writes (transaction of 2 items = 4 write units) | ~240,000 write units | $0.15 |
| DynamoDB reads (block check) | ~60,000 read units | $0.01 |
| DynamoDB storage + PITR | ~60 MB, growing | $0.03 |
| CloudWatch Logs | ~180 MB | $0.09 |
| CloudWatch alarms | 4 | $0.40 |
| SNS (alarm emails only) | a few | ~$0 |
| **Total** | | **~$0.85/month** |

Cost is not the deciding factor. Even at 100x the volume this stays under about $50/month.

## 14. Open questions (need an answer before build)

1. Who owns the producer, and does it guarantee `MessageGroupId = pnr` and `MessageDeduplicationId = event_id`?
2. The complete refund rule table (fare classes, `departure_status` values, the no-show rule), signed off by the business.
3. Should `airline_code` and currency be added to the event and the decision record for settlement reporting?
4. Which log keys does `docs/logging-contract.json` allow beyond the required six?
5. Is a daily query enough for the settlement team, or do they need a push notification (Option 2 in the variants doc)?

## 15. Human review (`docs/HITL_REVIEW_TEMPLATE.md`)

| Item | Value |
|---|---|
| Reviewer | _to be filled in_ |
| Date | _to be filled in_ |
| Template completed | [ ] |
| Decision | [ ] Approved  [ ] Changes requested |

Not to be merged until this section is completed by a human reviewer.
