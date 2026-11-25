# M1 architecture options - shailya

_model: us.anthropic.claude-haiku-4-5-20251001-v1:0 | tokens in/out: 916/1200_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are a senior AWS solutions architect designing for Accelya's airline settlement platform on AWS (region `us-east-1` only). Use airline vocabulary (`pnr`, `ticket_number`, `airline_code`, `agency_iata`, `settlement_date`); never generic names like `order` or `user`.

**Input:** CSV file `ticket_issue_<airline_code>_<issue_date>.csv` (e.g. `ticket_issue_AI_2026-11-23.csv`), ~2 KB (~25 rows), UTF-8, one header row. Columns: `pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata`. A file may contain more than one `airline_code`, so count from the column, not only the file name.

**Arrival:** ~1,000 files/day, about 42 files/hour on average; assume bursts up to 5x (~200 files in a peak hour). Files land in the S3 `raw/` prefix; uploaders may set `x-amz-meta-correlation-id`.

**Must:**
1. Keep the original file byte-for-byte unchanged in `raw/` (audit evidence): no rewrite, rename or delete.
2. Make ticket counts per `airline_code` visible to finance within 5 minutes of upload (say where finance looks).
3. Be idempotent: a retry or duplicate S3 event must not double-count.
4. Carry a correlation ID across every hop, from upload to the per-airline count.

**Must not:**
1. Never log PII or commercial data: no `pnr`, `ticket_number`, `fare_amount`, row contents or full payloads. Log identifiers (bucket, key, `airline_code`) and counts only.
2. No servers to patch, no public S3 buckets, no `"Action": "*"` / `"Resource": "*"` in IAM.

**Constraints:** serverless only, defined in AWS SAM. Sandbox: max 10 Lambda functions for the whole course, at most 100 concurrent Lambda executions (enforce with reserved concurrency), memory <= 2048 MB, max 50 Bedrock calls / 20,000 tokens per learner.

**Output wanted:**
1. Two distinct architecture options (e.g. S3 -> Lambda -> DynamoDB counter vs. S3 -> SQS -> Lambda with batching), each with a one-line hop diagram.
2. For **every hop**, a table answering the six service-boundary questions (`docs/service-boundary-6q.md`): push/pull, sync/async + timeout, payload shape, IAM role (minimum actions, ARN-scoped resources), failure path (retries, DLQ/quarantine), correlation ID.
3. A comparison: latency vs the 5-minute target, behaviour at peak under the 100-concurrency cap, Lambda count vs the 10-function budget, rough cost, operational risk.
4. A recommendation naming the main trade-off, plus open questions for the business.


## Bedrock answer

# Two Architecture Options for Airline Ticket Settlement Ingestion

## Option 1: S3 → Lambda (Synchronous) → DynamoDB Counter

**One-line summary:** Direct S3 event triggers Lambda to parse CSV, increment per-airline counters in DynamoDB, finance queries DynamoDB via CloudWatch dashboard.

**Service chain:**
```
S3 (raw/) → EventBridge → Lambda (parser) → DynamoDB (counters) → CloudWatch (dashboard)
```

### Hop 1: S3 → EventBridge

| Aspect | Detail |
|--------|--------|
| **Push/Pull** | Push (S3 event notification) |
| **Sync/Async** | Async; EventBridge rule triggers immediately |
| **Payload shape** | S3 event JSON: `bucket`, `key`, `x-amz-meta-correlation-id` (if present) |
| **IAM role** | S3 bucket policy allows `s3:GetObject` on `arn:aws:s3:::raw/*`; EventBridge service role has `events:PutEvents` |
| **Failure path** | EventBridge retries up to 2× over 1 hour; unmatched events dropped (no DLQ for S3 events) |
| **Correlation ID** | Extracted from S3 object metadata; if absent, generate UUID in Lambda |

### Hop 2: EventBridge → Lambda

| Aspect | Detail |
|--------|--------|
| **Push/Pull** | Push (EventBridge invokes) |
| **Sync/Async** | Async (fire-and-forget from EventBridge perspective) |
| **Payload shape** | S3 event JSON passed to Lambda handler |
| **IAM role** | Lambda execution role: `s3:GetObject` on `arn:aws:s3:::raw/*`, `dynamodb:UpdateItem` on counter table ARN |
| **Failure path** | Lambda timeout (15 min) or exception → EventBridge retries; if exhausted, event lost (no DLQ configured) |
| **Correlation ID** | Passed in event; Lambda logs it; stored as attribute in DynamoDB item |

### Hop 3: Lambda → DynamoDB

| Aspect | Detail |
|--------|--------|
| **Push/Pull** | Pull (Lambda calls DynamoDB API) |
| **Sync/Async** | Sync; UpdateItem blocks until response |
| **Payload shape** | `{PK: "airline_code#YYYYMMDD", SK: "counter", count: N, correlation_ids: [...]}`; use atomic increment |
| **IAM role** | Lambda role: `dynamodb:UpdateItem` on `arn:aws:dynamodb:us-east-1:ACCOUNT:table/ticket-counters` |
| **Failure path** | DynamoDB throttle (ProvisionedThroughput exceeded) → Lambda retries with exponential backoff; on persistent failure, Lambda exception triggers EventBridge retry |
| **Correlation ID** | Appended to `correlation_ids` set in DynamoDB item for audit trail |

### Hop 4: DynamoDB → CloudWatch Dashboard

| Aspect | Detail |
|--------|--------|
| **Push/Pull** | Pull (finance queries dashboard; dashboard reads DynamoDB via CloudWatch Insights or custom metric) |
| **Sync/Async** | Async; CloudWatch Insights query latency ~10–30 sec |
| **Payload shape** | DynamoDB item attributes: `airline_code`, `settlement_date`, `count`, `correlation_ids` |
| **IAM role** | Finance IAM role: `dynamodb:GetItem`, `dynamodb:Query` on counter table; `cloudwatch:GetMetricStatistics` |
| **Failure path** | Dashboard query fails if DynamoDB unavailable; no retry (user refreshes manually) |
| **Correlation ID** | Visible in DynamoDB item; finance can trace back to S3 key via logs |

**Risks:**
1. **DynamoDB hot partition:** All airline codes for a given date hash to same partition key; under 200 files/hour peak, write throughput may spike and trigger throttling. Mitigation: On-Demand billing or pre-provision burst capacity.
2. **No DLQ for EventBridge S3 events:** If Lambda fails after 2 retries, the ticket count is silently lost. No audit trail of dropped files.

---

## Option 2: S3 → SQS (Batching) → Lambda (Batch Parser) → DynamoDB + SNS (Alert)

**One-line summary:** S3 events enqueue to SQS; Lambda polls in batches, parses multiple CSVs per invocation, updates DynamoDB, publishes summary to SNS for finance.

**Service chain:**
```
S3 (raw/) → SQS (FIFO queue) → Lambda (batch parser, reserved concurrency=2) → DynamoDB (counters) + SNS (finance topic)
