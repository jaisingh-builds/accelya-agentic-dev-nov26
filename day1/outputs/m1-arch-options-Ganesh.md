# M1 architecture options - Ganesh

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 405/2521_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, file will always be maximum of size 100MB, columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** files most offten will arrive at the end of each hour for each airline, but could arrive at any time.

**Must:** while file is being processed, it should not be modified or deleted.it must check if all mentioned colmns are present in the file. file must be processed within 5 minutes.

**Must not:** passenger details should not be logged or stored.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.


## Bedrock answer

# Option 1: S3 Event → Lambda Fan-Out → DynamoDB Aggregation

**Summary:** S3 event triggers orchestrator Lambda that streams CSV, validates schema, aggregates counts in-memory, writes per-airline totals to DynamoDB, SNS notifies finance.

**Service chain:** S3 → Lambda (Orchestrator) → DynamoDB → EventBridge → Lambda (Notifier) → SNS → Finance Dashboard

## Hop 1: S3 → Lambda (Orchestrator)

| Question | Answer |
|----------|--------|
| Push or Pull | Push (S3 Event Notification) |
| Sync or Async | Async |
| Payload shape | Event contains bucket name, object key (e.g., `ticket_issue_BA_20250115.csv`), size, etag; correlation_id = `s3-request-id` |
| IAM role | Lambda execution role with `s3:GetObject`, `s3:GetObjectTagging` on source bucket |
| Failure path | Lambda DLQ captures failed invocations; S3 object remains; CloudWatch alarm on DLQ depth |
| Correlation_id location | Injected from S3 request-id into Lambda context, logged to CloudWatch |

## Hop 2: Lambda (Orchestrator) → DynamoDB

| Question | Answer |
|----------|--------|
| Push or Pull | Push (SDK PutItem) |
| Sync or Async | Sync (awaits acknowledgment) |
| Payload shape | `{settlement_date: "2025-01-15", airline_code: "BA", ticket_count: 1247, file_key: "...", correlation_id: "..."}` |
| IAM role | Same Lambda role with `dynamodb:PutItem` on `SettlementSummary` table |
| Failure path | Lambda retries with exponential backoff; after 3 attempts writes to DLQ; original file untouched |
| Correlation_id location | Stored as DynamoDB attribute; indexed for traceability |

## Hop 3: DynamoDB → EventBridge

| Question | Answer |
|----------|--------|
| Push or Pull | Push (DynamoDB Streams → EventBridge Pipes) |
| Sync or Async | Async |
| Payload shape | `{eventName: "INSERT", dynamodb: {NewImage: {...}}, correlation_id: "..."}` |
| IAM role | EventBridge Pipes role with `dynamodb:DescribeStream`, `dynamodb:GetRecords`, `events:PutEvents` |
| Failure path | Pipes retries with exponential backoff; failed events to Pipes DLQ; manual replay required |
| Correlation_id location | Extracted from DynamoDB item, passed in EventBridge detail |

## Hop 4: EventBridge → Lambda (Notifier)

| Question | Answer |
|----------|--------|
| Push or Pull | Push (EventBridge rule target) |
| Sync or Async | Async |
| Payload shape | EventBridge event wrapper with settlement summary in `detail` field |
| IAM role | Lambda execution role; EventBridge invokes via resource policy |
| Failure path | Lambda DLQ; EventBridge retries twice; alarm on notifier DLQ |
| Correlation_id location | Passed through event detail, logged |

## Hop 5: Lambda (Notifier) → SNS

| Question | Answer |
|----------|--------|
| Push or Pull | Push (SDK Publish) |
| Sync or Async | Sync publish, async delivery |
| Payload shape | `{airline: "BA", date: "2025-01-15", ticket_count: 1247, file: "...", correlation_id: "..."}` |
| IAM role | Lambda role with `sns:Publish` on `FinanceNotifications` topic |
| Failure path | Lambda retries; SNS delivery retries per subscription; failed messages to SNS DLQ |
| Correlation_id location | SNS message attribute `CorrelationId` |

**Risks:**
1. **Lambda timeout:** 100MB CSV streaming may exceed 15min timeout if network/CPU throttled; mitigate with S3 Select or Step Functions.
2. **Concurrent file uploads:** 50 airlines uploading simultaneously = 50 Lambda invocations; may hit account concurrency limits (default 1000).

---

# Option 2: S3 Event → Step Functions → Athena Query → SNS

**Summary:** S3 event triggers Step Functions workflow that validates schema via Lambda, runs Athena COUNT query on S3 file in-place, publishes results via SNS.

**Service chain:** S3 → EventBridge → Step Functions → Lambda (Validator) → Athena → S3 (results) → Lambda (Publisher) → SNS

## Hop 1: S3 → EventBridge

| Question | Answer |
|----------|--------|
| Push or Pull | Push (S3 Event Notifications to EventBridge) |
| Sync or Async | Async |
| Payload shape | CloudTrail-style event with `detail.bucket.name`, `detail.object.key`, correlation_id = `x-amz-request-id` |
| IAM role | S3 service principal publishes to EventBridge (no explicit role) |
| Failure path | EventBridge guarantees delivery; failed rule targets go to DLQ |
| Correlation_id location | EventBridge event `id` field; also extract S3 request-id from detail |

## Hop 2: EventBridge → Step Functions

| Question | Answer |
|----------|--------|
| Push or Pull | Push (EventBridge rule target) |
| Sync or Async | Async (StartExecution) |
| Payload shape | Full EventBridge event passed as Step Functions input |
| IAM role | EventBridge role with `states:StartExecution` on settlement workflow |
| Failure path | EventBridge retries; DLQ for failed invocations; CloudWatch alarm |
| Correlation_id location | Step Functions execution name = `settlement-<correlation_id>`; input contains original ID |

## Hop 3: Step Functions → Lambda (Validator)

| Question | Answer |
|----------|--------|
| Push or Pull | Push (Step Functions Task state) |
| Sync or Async | Sync (waits for Lambda response) |
| Payload shape | `{bucket: "...", key: "...", correlation_id: "..."}` |
| IAM role | Step Functions role with `lambda:InvokeFunction`; Lambda role with `s3:GetObject` (first 1KB only) |
| Failure path | Step Functions Catch block; retry 2x with backoff; on failure sends SNS alert and ends |
| Correlation_id location | Passed in task input; Lambda logs to CloudWatch with correlation_id |

## Hop 4: Lambda (Validator) → Athena

| Question | Answer |
|----------|--------|
| Push or Pull | Push (SDK StartQueryExecution) |
| Sync or Async | Async (returns query execution ID immediately) |
| Payload shape | SQL: `SELECT airline_code, COUNT(*) FROM "s3://bucket/key" GROUP BY airline_code`; uses CSV SerDe |
| IAM role | Lambda role with `athena:StartQueryExecution`, `glue:GetTable`, `s3:GetObject`, `s3:PutObject` (results bucket) |
| Failure path | Lambda returns query ID to Step Functions; workflow polls with GetQueryExecution; timeout after 4min triggers failure |
| Correlation_id location | Athena query tagged with `correlation_id`; Step Functions context maintains ID |

## Hop 5: Athena → S3 (results)

| Question | Answer |
|----------|--------|
| Push or Pull | Push (Athena writes results) |
| Sync or Async | Async |
| Payload shape | CSV file with columns `airline_code`, `_col1` (count); metadata file with query stats |
| IAM role | Athena service role with `s3:PutObject` on results bucket |
| Failure path | Query fails if S3 inaccessible; Step Functions detects FAILED state; sends alert |
| Correlation_id location | S3 object key includes query execution ID; Step Functions maps back to correlation_id |

## Hop 6: Step Functions → Lambda (Publisher)

| Question | Answer |
|----------|--------|
| Push or Pull | Push (Task state after Athena success) |
| Sync or Async | Sync |
| Payload shape | `{query_execution_id: "...", result_location: "s3://...", correlation_id: "..."}` |
| IAM role | Lambda role with `s3:GetObject` on results bucket, `sns:Publish` |
| Failure path | Step Functions retries 2x; DLQ; CloudWatch alarm |
| Correlation_id location | Passed through; logged and included in SNS message |

## Hop 7: Lambda (Publisher) → SNS

| Question | Answer |
|----------|--------|
| Push or Pull | Push |
| Sync or Async | Sync publish, async delivery |
| Payload shape | `{settlement_date: "...", airlines: [{code: "BA", ticket_count: 1247}, ...], file: "...", correlation_id: "..."}` |
| IAM role | Lambda role with `sns:Publish` |
| Failure path | Lambda retries; SNS DLQ for failed deliveries |
| Correlation_id location | SNS message attribute |

**Risks:**
1. **Athena cold start:** First query on new partition may take 30-60s for metadata scan; risks breaching 5min SLA.
2. **Schema drift:** If agency adds columns, Athena SerDe may fail; validator Lambda must enforce strict schema or use schema-on-read with column projection.

---

## Comparison

**Option 1** processes in-memory (faster, simpler) but Lambda may timeout on large files and requires careful memory tuning. **Option 2** leverages Athena for scalable aggregation without Lambda memory limits but adds latency (query queue, cold start) and complexity (polling, result parsing). **For 100MB files arriving hourly, Option 1 meets the 5min SLA with lower operational overhead; Option 2 suits unpredictable file sizes or future analytics needs.**
