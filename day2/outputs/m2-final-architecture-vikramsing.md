# M2 final architecture - cancellation refund decisions (vikramsing)

**Status:** design only, not deployed or tested. **Date:** 2026-11-25. **Prompt:** `day2/prompts/m2-arch-variants.md`.
**Prices:** us-east-1 list prices from memory; verify on the AWS pricing pages before relying on them.
**Names:** every resource starts `accelya-m2-vikramsing-`; Glue and Athena names use `accelya_m2_vikramsing_`.

## 1. Flow

```
booking system (producer)
  | 1 SendMessage (group = pnr, dedup id = event_id)
  v
SQS FIFO ...-cancellations.fifo ---(3 failed receives, 13)---> FIFO DLQ ...-cancellations-dlq.fifo
  | 2 Lambda event source mapping (batch 10, ReportBatchItemFailures)
  v
Lambda ...-decide   (pure function: event + refund_rules.json -> decision)
  | 3 conditional PutItem on ticket_number
  v
DynamoDB ...-decisions  <-------------- 15 settlement reads current decisions
  | 4 stream (NEW_IMAGE, INSERT only)
  v
EventBridge Pipe --5--> custom bus ...-decisions
                          |-- rule MANUAL_REVIEW --6--> SNS ...-manual-review --> settlement email
                          |-- rule every decision --7--> Lambda ...-archive
                                                          |-- 8 GetItem --> DynamoDB
                                                          |-- 9 PutObject --> S3 ...-archive (decisions/raw/)
EventBridge Scheduler (daily 01:00 UTC) --10--> Glue workflow --11--> S3 (decisions/curated/ Parquet)
                                                  --12--> Glue catalog <--16-- Athena (settlement)
CloudWatch alarms --14--> SNS ...-ops-alarms --> ops email
Pipe, bus rules and the archive Lambda failures --> standard SQS ...-events-dlq
```

## 2. Why each service is here (requirement it meets)

| Service | Requirement it meets |
|---|---|
| SQS FIFO | Order per PNR (group = pnr) and duplicate suppression (dedup id = event_id); DLQ so no event is lost |
| Lambda | Refund decision as a pure function; archiver. **2 new functions = 3 of 10 for the course** (with the Day 1 ingest Lambda) |
| DynamoDB | Settlement sees every decision in under 5 minutes; one decision per `ticket_number` (conditional put) |
| EventBridge | Notifications and archive happen off the decision write; Scheduler starts Glue |
| SNS | MANUAL_REVIEW notice to settlement; alarm delivery to ops |
| S3 | Audit archive of decisions |
| Glue | **No requirement in CONTEXT.** Only an assumed history-reporting need, so it is the first thing to drop (section 9) |
| CloudWatch | Contract-conformant logs, built-in metrics and alarms, the 5-minute target |

## 3. Failure and order policy

- A failed message is retried (up to 3 receives); the whole PNR group waits while it retries. After the 3rd failure it goes to the DLQ, the DLQ alarm fires, and later events for that PNR then proceed.
- **So per-PNR order is guaranteed only while messages succeed.** The safety net is one decision per `ticket_number`: a second event for the same ticket cannot create a second refund.
- Same `ticket_number`, same `event_id` = duplicate (INFO, counts as success). Same `ticket_number`, different `event_id` = conflict (WARNING, treated as a failure, goes to the DLQ for a person).

## 4. Hops (six service-boundary questions)

Correlation id = `correlation_id` message attribute, read by the decider (minted as `m2-<uuid>` if absent) and carried on every later hop.

| # | Hop | Push/pull, sync/async | Payload | Role | Failure path | Correlation ID |
|---|---|---|---|---|---|---|
| 1 | producer->sqs | Push, sync | Event JSON under 2 KB, only the 11 listed fields | Producer role: `sqs:SendMessage` on the queue | Producer retries; queue dedupes 5 min | Message attribute |
| 2 | sqs->lambda | Pull, async | Batch up to 10 | Decide role: Receive/Delete/GetQueueAttributes | `batchItemFailures` (first failure and later ones of its group); DLQ after 3 | Read; minted if missing; logged with `message_id` |
| 3 | lambda->dynamodb | Push, sync | Decision record | `dynamodb:PutItem` on table | Duplicate = success; conflict or throttle = failure | Stored as attribute |
| 4 | dynamodb->pipes | Pull, async | Stream record | Pipe role: stream read actions | Pipe retry, then events DLQ | In record |
| 5 | pipes->eventbridge | Push, async | "decided" detail: event_id, pnr, ticket_number, airline_code, decision, refund_rule_id (no amounts) | `events:PutEvents` on the bus | Retry, then events DLQ | In detail |
| 6 | eventbridge->sns | Push, async | event_id and pnr only | Topic policy for the rule | Rule retry policy + events DLQ | Message attribute |
| 7 | eventbridge->lambda | Push, async | "decided" detail | Lambda resource policy for the rule | Rule retry + Lambda async retry, then events DLQ | In detail |
| 8 | lambda->dynamodb (read) | Pull, sync | `ticket_number` key | Archive role: `dynamodb:GetItem` | Lambda error, retry | In record |
| 9 | lambda->s3 | Push, sync | Decision JSON, no PII | `s3:PutObject` on `decisions/raw/*` | Lambda error, retry, events DLQ | Object metadata `correlation-id` |
| 10 | scheduler->glue | Push, async | Start workflow | Scheduler role: `glue:StartWorkflowRun` | Scheduler retry; Glue failure alarm | Workflow run id |
| 11 | glue->s3 | Pull then push, sync | Raw JSON to Parquet, schema set explicitly | Glue role: Get/Put on raw and curated prefixes | Job failure alarm | Job run id |
| 12 | glue->catalog | Push, sync | Table and partitions | Glue role: named catalog actions | Job failure alarm | Job run id |
| 13 | sqs->dlq | Push, async | Original message | SQS redrive (allow policy on DLQ) | 14-day retention, alarm, manual redrive | Attributes preserved |
| 14 | cloudwatch->sns | Push, async | Alarm state | Topic policy for `cloudwatch.amazonaws.com` | SNS delivery retries | Alarm name |
| 15 | settlement->dynamodb | Pull, sync | GetItem or Query by airline and time | Settlement read role | Client retry | Stored attribute |
| 16 | settlement->athena | Pull, sync | SQL on curated table | Settlement read role | Query error, user retries | Query execution id |

## 5. IAM (named actions on ARNs; no `"*"`)

Let `T` = decisions table, `B` = archive bucket, `Q` = cancellations queue, `L` = bus.

| Role | Actions | Resources |
|---|---|---|
| producer | `sqs:SendMessage` | `Q` |
| decide | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`; `dynamodb:PutItem`; `logs:CreateLogStream`, `logs:PutLogEvents` | `Q`; `T`; its own log group |
| archive | `dynamodb:GetItem`; `s3:PutObject`; logs | `T`; `B/decisions/raw/*`; its own log group |
| pipe | `dynamodb:DescribeStream`, `GetRecords`, `GetShardIterator`, `ListStreams`; `events:PutEvents`; `sqs:SendMessage` | `T` stream; `L`; events DLQ |
| scheduler | `glue:StartWorkflowRun` | the workflow |
| glue | `s3:GetObject` raw; `s3:PutObject` curated; `s3:ListBucket`; `glue:GetDatabase`, `GetTable`, `CreateTable`, `UpdateTable`, `GetPartitions`, `BatchCreatePartition`; logs | `B` prefixes; the catalog database and tables; `/aws-glue` log groups |
| settlement read | `dynamodb:GetItem`, `Query`; `athena:StartQueryExecution`, `GetQueryExecution`, `GetQueryResults`; `glue:GetDatabase`, `GetTable`, `GetPartitions`; `s3:GetObject` curated; `s3:PutObject` athena-results | `T` and its index; the workgroup; catalog; `B` prefixes |

Resource policies: only the producer role may send to the queue; all queues, the bucket and the topics deny requests without TLS (`aws:SecureTransport`); the topics allow only the named rule and alarm ARNs (`aws:SourceArn`).
Encryption: SSE-SQS, DynamoDB and S3 default encryption, SNS server-side encryption. Config via environment variables; no keys or account IDs in code.

## 6. Observability

- **Log keys used:** the six required keys plus `message_id`, `event_id`, `pnr`, `ticket_number`, `airline_code`, `fare_class`, `refund_rule_id`, `decision`, `attempt`, `bucket`, `key`, `record_count`, `duration_ms`, `error_type`, `error_code`. Never logged: amounts, names, emails, bodies, raw exceptions. `rules_version` is stored in DynamoDB but not logged, because the contract has no such key.
- **Messages:** `decision.made`, `decision.duplicate`, `decision.conflict` (WARNING), `archive.written`, `decision.failed` (ERROR with `error_type`).
- **Built-in metrics and alarms (no PutMetricData, no custom metrics):** DLQ messages visible >= 1; events DLQ >= 1; SQS oldest message age > 300 s (the 5-minute target); Lambda Errors >= 1 for each function; DynamoDB `ConditionalCheckFailedRequests` above a threshold (duplicates and conflicts); Glue job failed (EventBridge rule on state change to ops SNS).
- **Logs:** 7-day retention on every log group.

## 7. Risks

1. After an event reaches the DLQ, later events for the same PNR are decided, so order is not guaranteed for that PNR (section 3).
2. A conflict (same ticket, different `event_id`) goes to the DLQ and blocks its PNR group for about 3 attempts until it lands there.
3. The sandbox may not allow Glue, Pipes or Scheduler; confirm before building. SNS email subscriptions need one manual confirmation (the only manual step).
4. About 60,000 tiny S3 objects a month; Glue compacts them into Parquet, but the raw prefix stays small-file heavy.

## 8. Cost per month (derivation)

Assumptions: 2,000 events/day x 30 = **60,000 events/month**; 5% are `MANUAL_REVIEW`; free tier ignored; DynamoDB items under 1 KB.

| Line | Quantity x unit price | $ |
|---|---|---|
| SQS FIFO | 3 requests x 60,000 = 180,000 x $0.50/M | 0.09 |
| Lambda decide | 60,000 x $0.20/M + 1,500 GB-s (256 MB, 100 ms) x $0.0000166667 | 0.04 |
| Lambda archive | 60,000 x $0.20/M + 2,250 GB-s (256 MB, 150 ms) x $0.0000166667 | 0.05 |
| DynamoDB | writes 60,000 x $0.625/M = 0.04; reads 0.00; **stream polling by the Pipe about 2.6M calls x $0.02/100k = 0.52**; storage and recovery 0.02 | 0.58 |
| EventBridge and Pipes | Pipe 60,000 x $0.40/M = 0.02; bus events 60,000 x $1.00/M = 0.06 | 0.08 |
| SNS | 3,000 publishes (about $0.00) + email beyond 1,000 free: 2,000 x $2/100k | 0.04 |
| S3 | 60,000 PUT x $0.005/1,000 = 0.30; reads and storage 0.03 | 0.33 |
| Glue | ETL daily: 30 x 2 DPU x 1.5 min x $0.44/DPU-hour = 0.66; **crawler daily: 30 x 2 DPU x 10-min minimum x $0.44/DPU-hour = 4.40** | 5.06 |
| Athena | about 1 GB scanned x $5/TB | 0.01 |
| CloudWatch | Logs 120 MB x $0.50/GB = 0.06; 8 alarms x $0.10 = 0.80 | 0.87 |
| **Total** | | **about $7.2** |

**About $0.12 per 1,000 events.** Glue is 71% of the bill, mostly the crawler.
**At 10x volume (20,000/day): about $15/month, not $72.** Per-event lines grow 10x (SQS, Lambda, S3 puts, bus events, SNS email), but the crawler, alarms and stream polling stay almost flat.

## 9. Score (0-3) and what to drop first

| Dimension | Score | Evidence |
|---|---|---|
| Correctness | 2 | Meets the spec; happy path only, no fixtures run, order guaranteed only until the DLQ |
| Security | 3 | Named actions on ARNs, TLS-only and source-ARN policies, no PII anywhere, config from environment |
| Observability | 2 | Contract JSON logs, classified errors, built-in alarms; no custom metrics allowed by the contract |
| Scalability | 2 | Idempotent and bounded; not yet tested at 10x |
| Cost | 2 | Services fit the load, but the Glue crawler is not justified at this volume |
| **Total** | **11/15 REVISE** | Needs 12 and every dimension at 2 or more; add golden-fixture tests (correctness 3) and a 10x test (scalability 3) to pass |

**Drop first, in this order:**
1. The Glue crawler: define the table by hand with partition projection. Saves about $4.40/month.
2. Glue and Athena entirely, keeping the S3 raw archive. Saves about $5.1/month and one assumed requirement.
3. The Pipe, bus and archive Lambda if no audit archive is required. Saves about $0.7/month and one Lambda.
