# M1 architecture options - dheeraj

_model: us.anthropic.claude-haiku-4-5-20251001-v1:0 | tokens in/out: 426/1200_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_AI_2026-11-23.csv`, ~100kb , columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** One file per agency per airline per day, ~200 files/day, arriving throughout the day with peaks after midnight UTC.

**Must:** Original file must be stored unchanged for audit and regulatory purposes.
users must see ticket counts per airline within 5 minutes of file upload.

**Must not:** No servers or operating systems requiring patching or maintenance.
Never log or expose sensitive business data such as fare_amount values, PNR lists, or ticket numbers in application logs.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.


## Bedrock answer

# Option 1: S3 → EventBridge → Lambda → DynamoDB → QuickSight

**Summary:** File upload triggers EventBridge rule invoking Lambda for aggregation, results stored in DynamoDB, visualized in QuickSight.

**Service chain:** S3 → EventBridge → Lambda (aggregator) → DynamoDB → QuickSight

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|-----------|---------------|----------|--------------|-----------------|
| S3→EventBridge | Push | Async | `{bucket, key, size}` | `s3:ObjectCreated:*` event | EventBridge DLQ → SQS | `s3-event-id` |
| EventBridge→Lambda | Push | Async | `{detail: {bucket, key}, source: "aws.s3"}` | `events:PutEvents` | Lambda DLQ (SQS) + CloudWatch alarm | `detail.requestParameters.requestId` |
| Lambda→S3 (read) | Pull | Sync | CSV rows streamed | `s3:GetObject` on source bucket | Retry 3x, fail Lambda invocation | `x-amzn-requestid` header |
| Lambda→DynamoDB | Push | Sync | `{pk: "airline_code#date", sk: "agency_iata", ticket_count: N, processed_at, file_key}` | `dynamodb:PutItem` | Exponential backoff, CloudWatch metric, manual intervention | Generated UUID in Lambda code |
| DynamoDB→QuickSight | Pull | Async | SPICE dataset refresh via Athena federation | `quicksight:CreateDataSet`, `athena:StartQueryExecution` | QuickSight refresh failure notification → SNS | Athena `QueryExecutionId` |

**Risks:**
1. **Lambda timeout on large files:** 100kb CSV with 200 concurrent uploads could exceed 15-minute timeout during peak hours; mitigation requires file chunking via S3 Select or step functions.
2. **DynamoDB hot partition:** All files for same airline on same date write to single partition key; under 200 files/day this is manageable but requires on-demand billing and monitoring for throttling.

---

# Option 2: S3 → SQS → Lambda (batch) → Kinesis Data Firehose → S3 (aggregates) → Athena → QuickSight

**Summary:** File upload queued to SQS, batch Lambda processes multiple files, aggregates written to S3 via Firehose, Athena queries for QuickSight dashboard.

**Service chain:** S3 → SQS → Lambda (batch processor) → Kinesis Firehose → S3 (aggregates) → Athena → QuickSight

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|-----------|---------------|----------|--------------|-----------------|
| S3→SQS | Push | Async | `{Records: [{s3: {bucket, key}}]}` via S3 event notification | `s3:ObjectCreated:*` → `sqs:SendMessage` | SQS DLQ after 3 receive attempts | `MessageId` |
| SQS→Lambda | Pull | Async | Batch of 10 messages, each `{Body: JSON s3 event}` | `sqs:ReceiveMessage`, `sqs:DeleteMessage` | Message visibility timeout 5min, retry via Lambda reserved concurrency | `sqs:MessageId` |
| Lambda→S3 (read) | Pull | Sync | CSV streamed via S3 Select | `s3:GetObject` | Fail message, return to DLQ | `x-amzn-requestid` |
| Lambda→Firehose | Push | Async | `{airline_code, agency_iata, ticket_count, processed_timestamp, file_key}` (newline-delimited JSON) | `firehose:PutRecord` | Firehose buffer retry, CloudWatch alarm on delivery failure | Lambda-generated `correlation_id` field in record |
| Firehose→S3 (aggregates) | Push | Async | Partitioned by `s3://aggregates/airline_code=XX/date=YYYY-MM-DD/` | `firehose:DeliverToS3` | Firehose DLQ bucket, manual reprocessing | Firehose `DeliveryStreamArn` |
| S3→Athena | Pull | Sync | SQL query on partitioned Parquet | `athena:StartQueryExecution`, `s3:GetObject` on aggregates | Query failure → SNS alert, manual query retry | Athena `QueryExecutionId` |
| Athena→QuickSight | Pull | Async | SPICE dataset refresh via Athena connector | `quicksight:CreateDataSet` | Refresh failure
