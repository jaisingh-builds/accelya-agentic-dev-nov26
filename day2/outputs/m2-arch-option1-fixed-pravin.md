# Option 1 (final) - pravin

Revision of Option 1 in `m2-arch-variants-trainer.md` after critical review.
The original Bedrock answer is left unchanged as the record of what the model produced.

## Summary

The producer writes straight to an SQS FIFO queue grouped by PNR. One Lambda makes the decision.
It prices the refund from the **issued-ticket record**: the BSP ticket-issue file loaded by the Day 1
pipeline. It never uses the amount the caller sends. Each decision is written once to DynamoDB, keyed
by ticket_number, and the settlement team reads it there.

## Authoritative fare source

The authoritative fare is the **BSP ticket-issue record**. That is the ticket as issued and billed to the agency
(`day1/data/ticket_issue_<airline>_<date>.csv`: `pnr, ticket_number, airline_code, fare_class, od_pair,
issue_date, fare_amount, currency, agency_iata`). A cancellation event is only a claim about that ticket.
The ticket's fare and fare class come from what was issued.

The Day 1 ingestion Lambda (`day1/option1/src/app.py`) already reads and validates every row
(PNR, ticket and airline formats). It is extended to also write each **valid** row into
`accelya-m2-pravin-issued-tickets` (hop 0). No new Lambda is added.

| Field used by the decision | Source of truth | Event value |
|---|---|---|
| `fare_amount`, `currency` | issued-tickets table | cross-check only |
| `fare_class` (selects the refund rule) | issued-tickets table | cross-check only |
| `pnr` | issued-tickets table | must match |
| `departure_status`, `no_show_flag` | event (no departure-control (DCS) feed in scope) | used as is: see Risk 3 |

Decision rules on the lookup, checked in this order. Two reasons apply on top of `refund_rules.json`:

1. Ticket not in the table -> `MANUAL_REVIEW`, reason `TICKET_NOT_FOUND`.
2. Event `pnr` differs from the issued `pnr` -> `MANUAL_REVIEW`, reason `PNR_MISMATCH`.
3. Event `fare_class` or `fare_amount` differs from the issued value -> `MANUAL_REVIEW`, reason `FARE_MISMATCH`.
   The refund is still computed from the issued fare and stored, so the reviewer has the number to hand.
4. Otherwise apply `day2/data/refund_rules.json` to the **issued** `fare_class`:
   `refund_amount = (issued fare_amount * refund_pct / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)`
   e.g. issued K 4120.00, rule R-SAVER 75% -> 3090.00, in the ticket's `currency`.

## Services in order

```
Hop 0 (Day 1 pipeline, extended)
  S3 ticket-issue CSV -> Lambda accelya-m1-pravin-opt1 ingest -> DynamoDB accelya-m2-pravin-issued-tickets (PK ticket_number)

Decision flow
  Producer (ticketing)
  -> SQS FIFO      accelya-m2-pravin-cancellations.fifo     (MessageGroupId = pnr, MessageDeduplicationId = event_id, SSE-KMS)
       DLQ         accelya-m2-pravin-cancellations-dlq.fifo (maxReceiveCount = 3, retention 14 days)
  -> Lambda        accelya-m2-pravin-refund-decide          (own role, ReportBatchItemFailures)
       -> GetItem  accelya-m2-pravin-issued-tickets         (authoritative fare, read-only)
  -> DynamoDB      accelya-m2-pravin-refund-decisions       (PK ticket_number, conditional put, PITR, CMK)
       GSI         by_settlement_date                       (PK settlement_date, SK decision#ticket_number)
  -> Settlement read path: Query on the GSI; decision = MANUAL_REVIEW is the review queue
```

Lambda functions: 1 new (`refund-decide`) plus the existing Day 1 ingest Lambda. The sandbox course limit is 10.

### Why EventBridge was removed from the front

EventBridge doesn't guarantee delivery order. Any order lost before the FIFO queue can't be recovered
afterwards, so per-PNR ordering has to start at the producer's `SendMessage`. As far as I know, an
EventBridge rule's SQS target also sets a fixed `MessageGroupId`, not one taken from each event
(check this in the AWS docs before relying on it).

## Service boundaries (six questions per hop)

### Hop 0: Day 1 ingest Lambda -> DynamoDB issued-tickets
| # | Answer |
|---|---|
| 1 Push/pull | Push. `PutItem` for each valid CSV row, after the existing Day 1 checks pass. |
| 2 Sync/async | Sync, SDK timeout 3 s per call. The Day 1 Lambda is invoked asynchronously by EventBridge. |
| 3 Payload | Item `{ticket_number (PK), pnr, airline_code, fare_class, od_pair, issue_date, fare_amount (Decimal), currency, agency_iata, source_system:"BSP", source_file_key, source_file_etag}`. The column contract is `EXPECTED_COLUMNS` in `app.py`. New columns may only be added. |
| 4 Role | Day 1 Lambda role gains `dynamodb:PutItem` on the issued-tickets table ARN only, plus `kms:GenerateDataKey`/`kms:Encrypt` on the CMK. |
| 5 Failure | `attribute_not_exists(ticket_number)`: a resent file never overwrites an issued fare. `ConditionalCheckFailedException` is counted as `duplicate_rows`. Throttling or other `ClientError`: log at ERROR and raise, so the async invoke is retried. Rows that fail validation still go to `quarantine/` as on Day 1. |
| 6 Corr. ID | The file's `correlation_id` (Day 1 rule). `source_file_key` and `source_file_etag` on each item trace the fare back to the exact file version. |

### Hop 1: Producer -> SQS FIFO
| # | Answer |
|---|---|
| 1 Push/pull | Push. The producer calls `sqs:SendMessage`. |
| 2 Sync/async | Sync acknowledgement (SDK timeout 5 s). Processing after that is async. |
| 3 Payload | JSON body `{schema_version:"1", event_id, pnr, ticket_number, fare_class, departure_status, no_show_flag, fare_amount}`. `fare_class` and `fare_amount` are cross-checks only. The ticketing team owns the schema. Changes may only add new optional fields. A breaking change means `schema_version` "2" and a new queue. |
| 4 Role | Producer role: `sqs:SendMessage` on the queue ARN, and `kms:GenerateDataKey` on the CMK. The queue policy denies every other principal and denies non-TLS access (`aws:SecureTransport` = false). |
| 5 Failure | On a send error the producer retries with the **same** `event_id`. FIFO deduplication drops repeats within 5 minutes. Repeats after 5 minutes are caught by the conditional put in hop 4. |
| 6 Corr. ID | Message attribute `correlation_id` (if missing, the Lambda sets `correlation_id = event_id`). |

### Hop 2: SQS FIFO -> Lambda refund-decide
| # | Answer |
|---|---|
| 1 Push/pull | Pull. A Lambda event source mapping polls the queue. |
| 2 Sync/async | The mapping invokes the Lambda synchronously. Batch size 10, function timeout 30 s, queue visibility timeout 180 s (6x the function timeout). |
| 3 Payload | SQS batch of up to 10 records, same body as hop 1. |
| 4 Role | Lambda execution role: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the queue ARN, and `kms:Decrypt` on the CMK. |
| 5 Failure | Return `batchItemFailures`. FIFO rule: once one message in a group fails, also return every later message **of the same group** in that batch, so their order is kept. After 3 receives the message goes to the FIFO DLQ. An alarm fires when the DLQ has more than 0 visible messages. |
| 6 Corr. ID | Read from the message attribute. It is written on every log line with `boundary = "sqs->refund-decide"`. |

### Hop 3: Lambda -> DynamoDB issued-tickets (authoritative fare lookup)
| # | Answer |
|---|---|
| 1 Push/pull | Push. `GetItem` by `ticket_number`, `ConsistentRead=true`. |
| 2 Sync/async | Sync, SDK timeout 2 s, standard SDK retries. SQS redelivery is the outer retry. |
| 3 Payload | Request: `ticket_number`. Response: `pnr, fare_class, fare_amount, currency, issue_date, source_file_key, source_file_etag`. The contract is the hop 0 item. |
| 4 Role | Lambda execution role: `dynamodb:GetItem` on the issued-tickets table ARN only, plus `kms:Decrypt`. No write access. |
| 5 Failure | No item: `MANUAL_REVIEW` / `TICKET_NOT_FOUND`. A mismatch: `MANUAL_REVIEW` / `PNR_MISMATCH` or `FARE_MISMATCH`. Throttling or other `ClientError`: log at ERROR with `error_type` and raise, so SQS retries and then the DLQ takes it. Fare amounts are never logged. |
| 6 Corr. ID | Logged with `boundary = "refund-decide->issued-tickets"`. The issued item's `source_file_etag` is copied onto the decision. |

### Hop 4: Lambda -> DynamoDB refund-decisions
| # | Answer |
|---|---|
| 1 Push/pull | Push. `PutItem`. |
| 2 Sync/async | Sync, SDK timeout 3 s. |
| 3 Payload | Item `{ticket_number (PK), pnr, event_id, decision, reason_code, rule_id, rule_version, refund_amount (Decimal, ROUND_HALF_UP), currency, fare_source_etag, settlement_date, decided_at, correlation_id, schema_version}`. |
| 4 Role | Lambda execution role: `dynamodb:PutItem` on the decisions table ARN, plus `kms:Encrypt`/`kms:Decrypt`/`kms:GenerateDataKey` on the CMK. No `UpdateItem` or `DeleteItem`, so a decision can't be changed after it is written. |
| 5 Failure | `ConditionExpression attribute_not_exists(ticket_number)`. `ConditionalCheckFailedException` means a duplicate cancellation: log at WARN with `error_type`, treat it as success and delete the message, so there is never a second refund. Throttling or other `ClientError`: log at ERROR and raise, so SQS retries. |
| 6 Corr. ID | Stored on the item as `correlation_id`. |

### Hop 5: Settlement team -> DynamoDB (read path)
| # | Answer |
|---|---|
| 1 Push/pull | Pull. The settlement team queries the decisions. |
| 2 Sync/async | Sync `Query` on the `by_settlement_date` GSI. |
| 3 Payload | Decisions for one `settlement_date`, filterable by decision (`APPROVE` / `REJECT` / `MANUAL_REVIEW`). |
| 4 Role | Settlement reader role: `dynamodb:Query` on the GSI ARN only, plus `kms:Decrypt`. |
| 5 Failure | Read-only, safe to retry. Point-in-time recovery (PITR) and deletion protection guard the record. |
| 6 Corr. ID | `correlation_id` traces to the logs. `fare_source_etag` traces to the exact BSP file the fare came from. |

## Security controls

- Only the producer role can send to the queue. The queue policy requires TLS. SSE-KMS uses a customer managed key (CMK).
- **The refund amount and the rule both come from the issued ticket**, so changing `fare_amount` or `fare_class`
  in an event can't raise a refund. A mismatch goes to a person.
- Only the Day 1 ingest role can write issued tickets, and existing ones can't be overwritten. `refund-decide` can only read them.
- Each role is scoped to named actions on specific ARNs. There is no `*` action or resource.
- The decisions table is write-once (conditional put, and no update or delete permissions anywhere). It records `rule_id`, `rule_version`
  and `fare_source_etag` for audit. Both tables have PITR, deletion protection and a CMK.
- Logs follow `docs/LOGGING_CONTRACT.md`: identifiers and counts only. No `fare_amount`, no `refund_amount`,
  no raw exception messages. Every error path catches a specific exception and logs `boundary` and `error_type`.
- Only the operator role can read the DLQ. DLQ messages contain fare_amount.

## Risks

1. **A bad message blocks its PNR group.** One message that keeps failing holds up later cancellations for the same PNR
   until it reaches the DLQ (3 receives x 180 s visibility = about 9 min). Other PNRs are not affected.
   Mitigation: DLQ alarm, plus a runbook to fix the message and send it again.
2. **A cancellation can arrive before its ticket-issue file.** BSP files are daily batches, so a same-day cancellation can find no
   ticket and go to `MANUAL_REVIEW` / `TICKET_NOT_FOUND`. Mitigation: a daily count of `TICKET_NOT_FOUND`, and reviewers
   re-run those tickets after the next file has loaded.
3. **`departure_status` and `no_show_flag` are still taken from the event.** They decide R-DEPARTED and R-NOSHOW, so a wrong value
   can turn a REJECT into an APPROVE. Mitigation now: only the authenticated ticketing producer role can send events,
   and the settlement team's daily review samples APPROVE decisions. Later: verify against a departure-control (DCS)
   or flight-status feed (out of scope for module 2).

## Cost driver

At 2,000 per day (about 60k per month), SQS, Lambda and the two DynamoDB tables cost cents (one extra
`GetItem` per decision, plus one `PutItem` per issued ticket). The KMS customer managed key ($1/month) and
CloudWatch Logs volume make up most of the bill (under $5/month in total).

## Changes from the original Option 1

| Original | Final |
|---|---|
| EventBridge at the front: no order guarantee | Producer sends to SQS FIFO, `MessageGroupId = pnr` |
| PK `event_id`, unconditional write | PK `ticket_number`, `attribute_not_exists` conditional put |
| Refund computed from the caller's `fare_amount` and `fare_class` | Both read from the BSP issued-ticket record. A mismatch or missing ticket goes to `MANUAL_REVIEW` |
| Engine swallows exceptions | Specific exceptions logged at ERROR, then raised so SQS retries, then DLQ |
| Streams + FIFO + 2 tables + 3 Lambdas | 1 queue, 1 new Lambda, decisions table + issued-tickets table |
| Shared or made-up roles (`StreamsInvokeRole`, `SQSInvokeRole`) | Each function and reader has its own execution role, scoped to ARNs |
| No read path for the settlement team | GSI `by_settlement_date`; `MANUAL_REVIEW` is the review queue |
