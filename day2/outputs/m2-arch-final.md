# M2 cancellation refund: final architecture

Status: **DRAFT for human review** (`docs/HITL_REVIEW_TEMPLATE.md`). Supersedes `day2/outputs/m2-arch-options.md`.
Chosen design: SQS standard queue, one decision Lambda, DynamoDB, a monthly CSV report emailed through SES, DLQ and alarms with SNS, CloudWatch Logs.
Region us-east-1 only. Python 3.12. All infrastructure in AWS SAM, no console click-ops.

Inputs: `AGENTS.md`, `docs/LOGGING_CONTRACT.md`, `docs/logging-contract.json`, `docs/service-boundary-6q.md`, `glossary/airline.md`,
`day2/data/refund_rules.json`, the spec in `day2/src/cancellation/handler.py`.

---

## 1. Review against AGENTS.md: defects found

Each line: what is wrong, how it fails, the fix. Defects 1 to 14 are in the earlier options document, defects 15 to 21 are in the handler (probed with edge-case events; the handler file was not changed).

| # | Defect | AGENTS.md rule | Failure mode | Fix (applied in section 3 onward) |
|---|---|---|---|---|
| 1 | Records described as "item" / "decision item" | Never use generic names such as `item` | Reviewers reject the PR. The glossary forbids it in payload examples. | Say "decision record". `batchItemFailures` and `itemIdentifier` are fixed AWS names and are the only exception. |
| 2 | Report index keyed on `decision_date`, a name outside the programme vocabulary | Airline vocabulary only | Inconsistent schema across modules | Use `settlement_date` (UTC date the decision was made). Confirm the meaning and cut-off with settlement. |
| 3 | `accelya-m2-<learner>-` prefix applied to log groups only | Every resource name starts `accelya-m<module>-<learner>-` | Unprefixed queues, tables and topics collide in a shared sandbox and fail review | Prefix every resource (section 4). |
| 4 | Replay permission written as "`sqs:StartMessageMoveTask` and related actions" | IAM: named actions on ARN-scoped resources | A vague role gets widened to a wildcard to make it work | Name every action and ARN (section 6). |
| 5 | "Log the report month", but no allowed log key holds a month | Only keys listed in `logging-contract.json` | The checker flags a non-contract key, or a free-text `message` carries data | Log `bucket`, `key` (the S3 key has the month), `record_count`, `bytes`, `duration_ms`. |
| 6 | Six-question rows answered "Not applicable" for the alarm, SNS and email hops | Answer all six questions for every hop | A blank answer hides a real gap, for example email subscriptions that were never confirmed | Every hop answered (section 7). |
| 7 | SES identity verification and SNS subscription confirmation described as manual steps | SAM only, no console click-ops | Environments that cannot be rebuilt from the repository | Define identities, topic, subscriptions, alarms and log retention in SAM. The recipient's confirmation click is the one unavoidable human step, so document it. |
| 8 | Lambda count never checked against the sandbox limit | Max 10 Lambda functions for the whole course | Deployment fails late | This design uses 2 (decide, report). Record it in the inventory. |
| 9 | Python runtime, memory and configuration source not stated | Python 3.12, memory <= 2048 MB, config from environment variables | Drift between code and template | State them (section 4). |
| 10 | HITL review gate and the logging-contract check never mentioned | Nothing merges without a human review | Unreviewed merge | Section 10 lists the review evidence. |
| 11 | Alarm design had no condition keys and no PII marking on the DLQ | Named actions on ARN-scoped resources, PII handling | The DLQ holds passenger names and emails | Add `SourceArn`/`SourceAccount` conditions, restrict DLQ readers, tag PII resources. |
| 12 | "Compare rows with events accepted that month", but nothing records accepted events | Explicit error paths | The report cannot prove completeness, so a missing decision goes unnoticed | Completeness check from scoped queue attributes (section 5.3). |
| 13 | Refund inputs described without a trust boundary | No speculative guesses about data; security | `fare_amount`, `no_show_flag` or `departure_status` taken from the passenger's request means a passenger sets their own refund | Requirement R-1 on the API (section 2). |
| 14 | `ses:SendRawEmail` scoped by identity ARN only | Named actions, ARN-scoped, conditions | Another sender address could be used | Add a `ses:FromAddress` condition. |
| 15 | Handler crashes the whole batch on a huge `fare_amount` (tested: `"1e30"` raises `InvalidOperation` inside the rounding step, outside the `try`) | "Every error path is explicit"; spec: a bad record must not crash the batch | One bad message makes Lambda retry the entire batch, so good messages are redelivered and eventually dead-lettered | Bound `fare_amount` and catch `InvalidOperation` around the calculation; report that record as `InvalidCancellation`. |
| 16 | `departure_status` compared to the exact string `"departed"` (tested: `"Departed"` is approved) | Business rules must hold | A departed flight is refunded at 100% because the value was cased differently or was an unknown status | Accept only `scheduled` and `departed` (the fixture values); anything else is `InvalidCancellation` (fail loud, not fail open). |
| 17 | `event_type` is never checked (tested: `reinstatement` is decided as a cancellation) | Domain vocabulary, correct decisions | A non-cancellation event produces a refund | Require `event_type == "cancellation"`; anything else is `UnsupportedEventType`, which goes to the DLQ for a human. |
| 18 | JSON is parsed with the default `float` parser | Money is `Decimal`, never float | A number with more than about 15 significant digits loses precision before the `Decimal` ever sees it | `json.loads(body, parse_float=Decimal)`. |
| 19 | Decisions are only returned, never stored | Platform design (system of record) | Every decision disappears when the invocation ends, so settlement sees nothing | Conditional write to DynamoDB (section 3). |
| 20 | Dedup only by `event_id` | Idempotency (HITL scalability anchor) | The same ticket cancelled through two channels with different `event_id`s gets two refunds | Key the table on `ticket_number`. A second, different `event_id` is `DuplicateCancellation` and goes to the DLQ. |
| 21 | A non-dict record in `Records` raises `AttributeError` (tested). SQS always sends dicts, so this is low severity. | Explicit error paths | A malformed test event crashes the invocation | Skip it with an `ERROR` log, error_type `InvalidRecord`. It has no `messageId`, so it cannot be reported. |

Not defects (checked and fine): money uses `Decimal` with `ROUND_HALF_UP`; logs use only contract keys and `python3 day2/tests/run_and_check.py` passes 6 of 6; `reports/*` in an S3 resource ARN is a prefix, not `"Resource": "*"`.

---

## 2. Requirements and open decisions

| ID | Requirement or decision | Owner | Status |
|---|---|---|---|
| R-1 | The passenger API must take `fare_amount`, `fare_class`, `departure_status` and `no_show_flag` from the booking system, never from the caller's request | API team | **Required** |
| R-2 | The API generates `event_id` (or takes a client idempotency key) and returns `202` once SQS accepts | API team | Required |
| R-3 | Each message carries `correlation_id` as a message attribute | API team | Required |
| D-1 | Ordering per PNR is **not** required, because no refund rule reads another event | Business owner | Confirm |
| D-2 | A ticket is cancelled once. If cancel, reinstate, cancel is possible, the key and ordering change. | Business owner | Confirm |
| D-3 | Report cadence is monthly. The glossary says settlement is typically a weekly BSP cycle. | Settlement | Confirm |
| D-4 | `settlement_date` is the UTC date the decision was made. Confirm the cut-off. | Settlement | Confirm |
| D-5 | Settlement mailbox, and whether its security policy allows financial data in email attachments | Settlement | Confirm |
| D-6 | Who works MANUAL_REVIEW decisions, and how fast. A monthly file is too slow for that. | Business owner | Open |
| D-7 | Payout happens in another system. This design stops at the decision. | Business owner | Confirm |
| D-8 | Data retention and archiving are not designed here (decided to defer). Until decided, decision records stay in DynamoDB indefinitely. | Business owner | Deferred |

---

## 3. Design

```
passenger API --SendMessage--> SQS standard queue ----> decide Lambda ----PutItem (conditional)----> DynamoDB decisions
                                    |  (3 failed receives)                                              |
                                    v                                                                   | Query by settlement_date
                                  DLQ ---alarm---> SNS alerts ---> team mailbox                         v
EventBridge Scheduler (monthly) --------------------------------> report Lambda --> S3 (private) --> SES email --> settlement mailbox
```

**Decision Lambda** (one message per invocation):
1. Parse the body with `parse_float=Decimal`. Require `event_id`, `event_type == "cancellation"`, `pnr`, `ticket_number`, `airline_code`, `fare_class`, `departure_status` (`scheduled` or `departed`), `no_show_flag` (boolean), `fare_amount` (bounded, finite, not negative) and `currency`. Anything else is `InvalidJson`, `InvalidCancellation` or `UnsupportedEventType`.
2. Apply `refund_rules.json` (first match wins). `Decimal`, `ROUND_HALF_UP`, two decimals, returned as a string.
3. Write one decision record with a conditional `PutItem` (`attribute_not_exists(ticket_number)`).
   - Success: log `cancellation.decided`.
   - Condition fails and the stored `event_id` is the same: it is a redelivery, so log `cancellation.duplicate` at `INFO` and succeed.
   - Condition fails and the stored `event_id` differs: log `ERROR` with error_type `DuplicateCancellation` and report the message as failed, so it reaches the DLQ and a human decides.
4. Failures go to `batchItemFailures` (`ReportBatchItemFailures` on). Throttles and timeouts raise, so SQS retries.

The lab handler's `decisions` output can stay for the course harness. SQS ignores it.

**Decision record** (table `accelya-m2-<learner>-decisions`, on-demand, point-in-time recovery on, deletion retained):

| Attribute | Notes |
|---|---|
| `ticket_number` | Partition key |
| `event_id`, `event_type`, `pnr`, `airline_code`, `fare_class` | Identifiers |
| `decision`, `refund_rule_id`, `rules_version` | Which rule version decided it, for audit |
| `refund_amount`, `currency` | Amount as a string, currency from the event |
| `settlement_date`, `decided_at` | UTC date and timestamp |
| `correlation_id` | Traceability |

No `passenger_name` or `contact_email` is stored. Index `settlement-date-index`: partition key `settlement_date`, sort key `decided_at`, projecting only the CSV columns.

**Queue settings:** standard queue, retention 4 days, visibility timeout at least 6 times the function timeout (function timeout 10 s, so 60 s), `maxReceiveCount` 3, DLQ retention 14 days (must be at least the source's), `BatchSize=1`, no batching window, encryption on, `ScalingConfig.MaximumConcurrency` set (check the account's concurrency first). Function memory 256 to 512 MB (limit 2048). Reserved concurrency is not set low.

### 3.1 Monthly report

1. EventBridge Scheduler runs on the 1st at 03:00 UTC for the previous month, with retry and a DLQ.
2. The report Lambda queries `settlement-date-index` once per day of the month, paginating on `LastEvaluatedKey` (no scans).
3. It writes `reports/month=YYYY-MM/decisions.zip` to the private bucket (same key on a re-run, so it overwrites), then emails it with SES.
4. CSV columns: `settlement_date`, `pnr`, `ticket_number`, `airline_code`, `fare_class`, `decision`, `refund_rule_id`, `refund_amount`, `currency`, `event_id`, `rules_version`, `decided_at`. No names or emails.
5. Size: about 60k rows, 10 to 15 MB CSV, about 2 MB zipped. Memory 512 MB to 1 GB, timeout 2 to 5 minutes.

### 3.2 Completeness check

Nothing records "events accepted", and reading SQS CloudWatch metrics would need `Resource: "*"`, which AGENTS.md forbids. Instead, at run time the report Lambda reads `ApproximateNumberOfMessages` for the source queue and the DLQ with `sqs:GetQueueAttributes` (ARN-scoped). The email body states row count, queue depth and DLQ depth. If either depth is above zero, the subject starts with `[INCOMPLETE]`.

---

## 4. Resource inventory (SAM, every name starts `accelya-m2-<learner>-`)

| Resource | Name suffix | Notes |
|---|---|---|
| SQS queue | `cancellations` | Standard |
| SQS DLQ | `cancellations-dlq` | PII: restrict readers |
| DynamoDB table | `decisions` | Plus index `settlement-date-index` |
| Lambda | `decide` | Python 3.12, 256 to 512 MB, 10 s |
| Lambda | `report` | Python 3.12, 512 MB to 1 GB, 5 min |
| S3 bucket | `reports-<suffix>` | Private, public access blocked, encrypted. The suffix keeps the globally unique name, built with SAM intrinsics, no account id hardcoded. |
| Scheduler and its DLQ | `report-schedule`, `report-schedule-dlq` | Monthly |
| SNS topic | `alerts` | Email subscription (needs a confirmation click) |
| SES identity and configuration set | sender identity, `report-mail` | Bounce and complaint events go to `alerts` |
| Log groups | `/aws/lambda/accelya-m2-<learner>-decide`, `-report` | Retention 30 days |
| CloudWatch alarms | `cancellations-dlq-depth`, `cancellations-oldest-age`, `decide-errors`, `report-errors`, `report-schedule-dlq-depth` | All publish to `alerts` |

Lambda count: **2 of 10**. Configuration comes from environment variables: `TABLE_NAME`, `QUEUE_URL`, `DLQ_URL`, `REPORT_BUCKET`, `SENDER_ADDRESS`, `RECIPIENT_ADDRESS`, `RULES_VERSION`. No secrets or account ids in code. Do not put the account id or mailbox into the repository. The `DeletionPolicy` on the table and bucket is `Retain`.

---

## 5. Observability

- All lines follow the logging contract. `service` is `accelya-m2-cancellation` (decide) and `accelya-m2-report` (report). `message` values are dotted event names, for example `cancellation.decided`, `cancellation.duplicate`, `report.completed`.
- Boundaries: `sqs->lambda`, `lambda->dynamodb`, `lambda->s3`, `lambda->ses`, `scheduler->lambda`. `error_type` on every `ERROR`.
- Allowed keys only: `message_id`, `event_id`, `fare_class`, `refund_rule_id`, `decision`, `record_count`, `bytes`, `bucket`, `key`, `duration_ms`, `error_type`. No bodies, names, emails, fare amounts, recipients, or CSV rows.
- Alarm for message age matters most for the requirement that messages must not wait long: `ApproximateAgeOfOldestMessage` above 300 s on the source queue.
- Validate with `python3 day2/scripts/check_logs.py` and `python3 day2/tests/run_and_check.py`.

---

## 6. IAM (named actions, ARN-scoped, no wildcards in `Action` or `Resource`)

| Principal | Actions | Resources | Conditions |
|---|---|---|---|
| Passenger API backend role | `sqs:SendMessage` | The queue ARN | The queue policy names this role only |
| `decide` execution role | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | The queue ARN | none |
| | `dynamodb:PutItem` | The table ARN | none |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | Its own log group ARN | none |
| `report` execution role | `dynamodb:Query` | The table's `index/settlement-date-index` ARN | none |
| | `s3:PutObject` | `<bucket ARN>/reports/*` | none |
| | `ses:SendRawEmail` | The sender identity ARN | `ses:FromAddress` equals the sender |
| | `sqs:GetQueueAttributes` | The queue ARN and the DLQ ARN | none |
| | `sns:Publish` (on-failure destination) | The `alerts` topic ARN | none |
| | `logs:CreateLogStream`, `logs:PutLogEvents` | Its own log group ARN | none |
| Scheduler role | `lambda:InvokeFunction`, `sqs:SendMessage` | The `report` function ARN, the schedule DLQ ARN | none |
| `alerts` topic policy | `sns:Publish` | The topic ARN | CloudWatch and SES as principals, limited by `SourceAccount` / `SourceArn` conditions |
| On-call operator (replay) | `sqs:StartMessageMoveTask`, `sqs:ListMessageMoveTasks`, `sqs:CancelMessageMoveTask`, `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | The DLQ ARN | A named role, not a wildcard. Also `sqs:SendMessage` on the source queue ARN. Verify the exact set against current SQS documentation. |

---

## 7. Six-question answers, every hop

| Hop | 1 Push/pull | 2 Sync/async | 3 Payload | 4 Role | 5 Failure path | 6 Correlation ID |
|---|---|---|---|---|---|---|
| API → SQS | Push (`SendMessage`) | Passenger waits only until SQS accepts, then `202`. The decision comes later. | Cancellation JSON v1, API owns it, additive changes only. Add a `schema_version` field. | API backend role (section 6) | API returns 5xx, client retries with the same `event_id` | `correlation_id` message attribute from `X-Correlation-Id`, minted if missing |
| SQS → decide | Pull (event source mapping) | Async to the API, batch runs synchronously | SQS event, `body` is a JSON string | `decide` role | Partial batch failure, 3 receives, then DLQ | Read from the attribute, minted if missing |
| decide → DynamoDB | Push (`PutItem`) | Sync, short SDK timeout well under the 10 s function timeout | Decision record (section 3) | `decide` role | Throttle or timeout raises and SQS retries. A duplicate is handled as in section 3. | Stored in `correlation_id`, on every log line |
| SQS → DLQ | Push (redrive policy) | Async | The original message, unchanged | Redrive policy names the DLQ ARN | Held 14 days | The message attribute travels with it |
| DLQ alarm → SNS | Push | Async | CloudWatch alarm notification (alarm name, metric, state, no event data) | Topic policy (section 6) | Alarm keeps firing until the DLQ is empty | None by design. The alarm text names the queue. The `correlation_id` of each failed message is read from the DLQ message. |
| SNS → email | Push | Async | Plain alarm text | Subscription defined in SAM | An unconfirmed or bounced subscription drops alerts, so confirm after deploy and test | None by design. Same as above. |
| Scheduler → report | Push | Async | Event with the report month | Scheduler role | Retry, then schedule DLQ with its own alarm | Minted per run, on every log line |
| report → DynamoDB | Pull (`Query`) | Sync | Records for one `settlement_date` | `report` role | Retry, then the run fails and alerts | Same run ID |
| report → SQS (attributes) | Pull (`GetQueueAttributes`) | Sync | Approximate depths, counts only | `report` role | Unreadable counts mean the email is marked `[INCOMPLETE]` | Same run ID |
| report → S3 | Push (`PutObject`) | Sync | Zipped CSV | `report` role | Same key makes a re-run safe | Object metadata `x-amz-meta-correlation-id` |
| report → SES | Push (`SendRawEmail`) | Sync | Email with the zip attached | `report` role with the `ses:FromAddress` condition | Failure raises. Lambda retries twice, then the on-failure destination publishes to `alerts`. | Run ID in a custom header, never in the body |
| SES → SNS (bounce/complaint) | Push (configuration set event destination) | Async | SES event notification | SNS topic policy for SES | A bounce means settlement did not receive the report, so alert and re-send | Message ID only |
| report failure → SNS | Push (Lambda destination) | Async | The failed invocation record (month only, no PII) | `report` role (`sns:Publish`) | Held until someone re-runs the month | Run ID |
| DLQ → source (replay) | Push (move task, started by a person) | Async | The original messages | On-call operator role | Fix the cause first, or they return to the DLQ. Replays are safe because of the conditional write. | Preserved |
| Lambda → CloudWatch Logs | Push (runtime writes stdout) | Async | One JSON line per event, contract shape | Each role's `logs:*` actions | A lost line is not retried, so correctness never depends on logs | On every line |

---

## 8. Cost (rough, unverified)

The pricing tool and the CLI price list were both unavailable in this environment, so unit prices come from memory. Check the AWS pricing pages before quoting.

| Component | Monthly |
|---|---|
| SQS, both Lambdas, Scheduler, SES | about $0 (inside free tiers) |
| DynamoDB (writes, index writes, point-in-time recovery) | about $0.10 |
| S3 | under $0.01 |
| CloudWatch Logs (about 110 MB) | about $0.05 to $0.10 |
| 5 alarms and SNS | up to $0.50 (about $0 if free-tier alarms apply) |
| **Total at 2,000 decisions a day** | **about $0.2 to $0.8** |

Not included: the existing passenger API, data transfer, support, and any customer-managed KMS key (about $1 a month each).

---

## 9. Handler changes required before this design works

1. Parse with `parse_float=Decimal` (defect 18).
2. Bound and validate `fare_amount`, and catch `InvalidOperation` around the calculation (defect 15).
3. Accept only `departure_status` of `scheduled` or `departed` (defect 16).
4. Require `event_type == "cancellation"` (defect 17).
5. Require `pnr`, `ticket_number`, `airline_code` and `currency`, and update the spec and fixtures. Rerun `run_and_check.py`, since fixtures must contain them.
6. Add the conditional `PutItem` with duplicate handling, `rules_version`, `settlement_date` and `decided_at` (defects 19, 20). Use a module-level client with bounded timeouts. Keep the write behind one plain function so the lab harness (no network) can replace it.
7. Skip non-dict records with an `ERROR` log (defect 21).
8. The lab spec says "do not call AWS". This change deliberately lifts that for DynamoDB only.

---

## 10. HITL review

Nothing merges until a human completes `docs/HITL_REVIEW_TEMPLATE.md` (copy it to `day2/reviews/HITL_REVIEW-<artefact>.md`, one per artefact, for example the handler and the SAM template). Evidence to gather:

| Dim | Evidence to attach |
|---|---|
| Correctness | `run_and_check.py` 6/6, tests for defects 15 to 21, report totals match decision records |
| Security | Section 6 reproduced in the SAM template with no wildcards, requirement R-1 confirmed by the API team, DLQ readers restricted and resources tagged for PII |
| Observability | `check_logs.py` result CONFORMANT, the five alarms firing in a test, SES bounce path tested |
| Scalability | Idempotency test (same message twice, same ticket with two `event_id`s), `MaximumConcurrency` and account concurrency checked, a 10x load note |
| Cost | Table in section 8 with verified prices and cost per decision |

The verdict is the reviewer's. This document does not score itself.
