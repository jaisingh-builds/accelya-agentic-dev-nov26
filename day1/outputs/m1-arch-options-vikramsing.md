# M1 architecture options - vikramsing

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 381/1200_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~1 MB (about 10,000 rows), columns: pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata.

**Arrival:** One file per airline per day

**Must:**
- Keep the original file unchanged
- Show the row count per airline in CloudWatch

**Must not:**
- No servers to manage
- Never log PNR or fare_amount

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.


## Bedrock answer

# Option A: S3 Event → Lambda → CloudWatch Logs Insights

**Summary:** Direct Lambda trigger from S3 PUT event, parse CSV in-memory, emit structured logs, query with Logs Insights.

**Services in order:**
1. S3 (landing bucket)
2. Lambda (parser function)
3. CloudWatch Logs
4. CloudWatch Logs Insights (query layer)
5. CloudWatch Dashboard

## Service Boundary Analysis

### Hop 1: S3 → Lambda

| Question | Answer |
|----------|--------|
| Push or Pull | Push (S3 Event Notification) |
| Sync or Async | Async |
| Payload shape | S3 event JSON: `{"Records":[{"s3":{"bucket":{"name":"..."},"object":{"key":"ticket_issue_<airline>_<date>.csv"}}}]}` |
| IAM role | Lambda execution role with `s3:GetObject` on landing bucket |
| Failure path | Lambda DLQ (SQS) captures failed invocations; S3 object remains untouched |
| Correlation ID | `s3.object.key` becomes `correlation_id`; stored in Lambda context and all logs |

### Hop 2: Lambda → CloudWatch Logs

| Question | Answer |
|----------|--------|
| Push or Pull | Push (Lambda runtime auto-streams) |
| Sync or Async | Async (buffered writes) |
| Payload shape | Structured JSON per airline: `{"correlation_id":"ticket_issue_AA_20250115.csv","airline_code":"AA","ticket_count":1247,"timestamp":"2025-01-15T10:23:45Z"}` |
| IAM role | Lambda execution role with implicit `logs:CreateLogStream`, `logs:PutLogEvents` |
| Failure path | Lambda retries log writes; if persistent failure, invocation fails and routes to DLQ |
| Correlation ID | Embedded in every log line as `correlation_id` field |

### Hop 3: CloudWatch Logs → Logs Insights

| Question | Answer |
|----------|--------|
| Push or Pull | Pull (user-initiated query) |
| Sync or Async | Sync (query waits for results) |
| Payload shape | Query: `fields airline_code, ticket_count | filter correlation_id like /ticket_issue/ | stats sum(ticket_count) by airline_code` |
| IAM role | Finance team IAM role with `logs:StartQuery`, `logs:GetQueryResults` on `/aws/lambda/ticket-parser` log group |
| Failure path | Query timeout (15 min max); user retries manually |
| Correlation ID | Queried as filter dimension; returned in result set |

### Hop 4: Logs Insights → CloudWatch Dashboard

| Question | Answer |
|----------|--------|
| Push or Pull | Pull (dashboard refresh) |
| Sync or Async | Sync (on-demand or scheduled refresh) |
| Payload shape | Widget config references saved Logs Insights query; renders bar chart of airline vs. count |
| IAM role | Dashboard viewer role inherits Logs Insights permissions |
| Failure path | Widget shows "No data" or error message; does not block other widgets |
| Correlation ID | Not displayed; aggregated away in `stats` clause |

## Risks
1. **CSV parsing in Lambda memory:** 1 MB file with 10K rows fits easily, but malformed CSV (e.g., unescaped quotes) could crash parser; no schema validation before processing.
2. **Logs Insights query cost:** Each query scans all ingested data; if finance runs 100 queries/day across 30 days of logs, cost escalates; no pre-aggregated metric.

---

# Option B: S3 Event → EventBridge → Lambda → CloudWatch Metric

**Summary:** EventBridge rule filters S3 events, Lambda writes custom metrics directly, CloudWatch dashboard shows real-time counts.

**Services in order:**
1. S3 (landing bucket with EventBridge notification enabled)
2. EventBridge (event bus with rule)
3. Lambda (metric publisher)
4. CloudWatch Metrics
5. CloudWatch Dashboard

## Service Boundary Analysis

### Hop 1: S3 → EventBridge

| Question | Answer |
|----------|--------|
| Push or Pull | Push (S3 sends to default event bus) |
| Sync or Async | Async |
| Payload shape | EventBridge event: `{"source":"aws.s3","detail-type":"Object Created","detail":{"bucket":{"name":"..."},"object":{"key":"ticket_issue_BA_20250115.csv"}}}` |
| IAM role | S3 service principal; no explicit role (AWS-managed integration) |
| Failure path | EventBridge retries delivery 24 hours with exponential backoff; dead-letter queue on event bus captures permanent failures |
| Correlation ID | `detail.object.key` extracted as `correlation_id` in EventBridge rule transform
