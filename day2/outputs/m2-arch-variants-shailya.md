# Architecture options (3) - shailya

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 461/3011_

## Prompt

## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: average of 500 cancellation per day
Ordering: order matter per PNR

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop, two risks, and a rough cost driver. End with a comparison table.


## Bedrock answer

# Option 1: EventBridge → Lambda → DynamoDB Streams → Lambda → SQS FIFO → Lambda → S3

**Summary:** Event-driven pipeline with FIFO ordering per PNR, DynamoDB for state, S3 for settlement audit.

**Services in order:**
EventBridge → Lambda (Decision Engine) → DynamoDB → DynamoDB Streams → Lambda (Enrichment) → SQS FIFO → Lambda (Settlement Writer) → S3

## Service Boundary Analysis

### Hop 1: EventBridge → Lambda (Decision Engine)

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (event pattern match) |
| Sync or Async | Async invocation |
| Payload Shape | Full cancellation event JSON |
| IAM Role | EventBridge service role, lambda:InvokeFunction |
| Failure Path | Dead-letter queue, retry 2x |
| Correlation ID | event_id in EventBridge event detail |

### Hop 2: Lambda (Decision Engine) → DynamoDB

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (PutItem API call) |
| Sync or Async | Sync write, wait for acknowledgment |
| Payload Shape | Decision record: pnr, decision, refund_amount |
| IAM Role | Lambda execution role, dynamodb:PutItem |
| Failure Path | Lambda retry, exponential backoff |
| Correlation ID | event_id as partition key attribute |

### Hop 3: DynamoDB → DynamoDB Streams

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (stream record to poller) |
| Sync or Async | Async, near real-time propagation |
| Payload Shape | NEW_IMAGE with decision attributes |
| IAM Role | DynamoDB service-linked role automatic |
| Failure Path | Stream retention 24h, bisect batch |
| Correlation ID | event_id in stream record |

### Hop 4: DynamoDB Streams → Lambda (Enrichment)

| Aspect | Answer |
|--------|--------|
| Push or Pull | Pull (Lambda polls stream shards) |
| Sync or Async | Async batch processing |
| Payload Shape | Batch of stream records |
| IAM Role | Lambda execution role, dynamodb:GetRecords |
| Failure Path | Bisect on error, DLQ after |
| Correlation ID | event_id extracted from each record |

### Hop 5: Lambda (Enrichment) → SQS FIFO

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (SendMessage API call) |
| Sync or Async | Sync send, wait for MessageId |
| Payload Shape | Enriched settlement record JSON |
| IAM Role | Lambda execution role, sqs:SendMessage |
| Failure Path | Lambda retry, then DLQ |
| Correlation ID | event_id in message body |

### Hop 6: SQS FIFO → Lambda (Settlement Writer)

| Aspect | Answer |
|--------|--------|
| Push or Pull | Pull (Lambda polls queue) |
| Sync or Async | Async batch retrieval |
| Payload Shape | Batch up to 10 messages |
| IAM Role | Lambda execution role, sqs:ReceiveMessage |
| Failure Path | Visibility timeout, redrive after 3x |
| Correlation ID | event_id in message body |

### Hop 7: Lambda (Settlement Writer) → S3

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (PutObject API call) |
| Sync or Async | Sync write with confirmation |
| Payload Shape | JSONL file, one decision per line |
| IAM Role | Lambda execution role, s3:PutObject |
| Failure Path | Lambda retry, partial batch reprocess |
| Correlation ID | event_id in each JSONL row |

**Risks:**
1. DynamoDB Streams shard iterator expires after 24h; unprocessed records lost if Lambda fails repeatedly.
2. SQS FIFO MessageGroupId must be PNR; high cardinality may reduce throughput below 500/day capacity.

---

# Option 2: EventBridge → Step Functions Express → Lambda → Aurora Serverless → Athena

**Summary:** Orchestrated workflow with Step Functions, Aurora for transactional state, Athena for settlement queries.

**Services in order:**
EventBridge → Step Functions Express → Lambda (Decision) → Aurora Serverless v2 (PostgreSQL) → Lambda (Export) → S3 → Athena

## Service Boundary Analysis

### Hop 1: EventBridge → Step Functions Express

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (StartExecution via target) |
| Sync or Async | Async execution start |
| Payload Shape | Cancellation event as input JSON |
| IAM Role | EventBridge service role, states:StartExecution |
| Failure Path | EventBridge retry 185 times |
| Correlation ID | event_id in execution input |

### Hop 2: Step Functions → Lambda (Decision)

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (task invocation) |
| Sync or Async | Sync, Step Functions waits response |
| Payload Shape | Cancellation event from state input |
| IAM Role | Step Functions role, lambda:InvokeFunction |
| Failure Path | Retry 3x, Catch state, manual |
| Correlation ID | event_id in task input |

### Hop 3: Lambda (Decision) → Aurora Serverless

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (INSERT via Data API) |
| Sync or Async | Sync transaction, wait for commit |
| Payload Shape | Row: pnr, decision, refund, timestamp |
| IAM Role | Lambda execution role, rds-data:ExecuteStatement |
| Failure Path | Transaction rollback, Lambda throws error |
| Correlation ID | event_id as primary key column |

### Hop 4: Aurora → Lambda (Export)

| Aspect | Answer |
|--------|--------|
| Push or Pull | Pull (EventBridge schedule triggers Lambda) |
| Sync or Async | Async batch export hourly |
| Payload Shape | SELECT result set via Data API |
| IAM Role | Lambda execution role, rds-data:ExecuteStatement |
| Failure Path | CloudWatch alarm on Lambda error |
| Correlation ID | event_id in each exported row |

### Hop 5: Lambda (Export) → S3

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (PutObject for Parquet file) |
| Sync or Async | Sync write with ETag confirmation |
| Payload Shape | Parquet partitioned by date, pnr |
| IAM Role | Lambda execution role, s3:PutObject |
| Failure Path | Lambda retry, idempotent write |
| Correlation ID | event_id in Parquet schema |

### Hop 6: S3 → Athena

| Aspect | Answer |
|--------|--------|
| Push or Pull | Pull (settlement team runs queries) |
| Sync or Async | Sync query execution |
| Payload Shape | Parquet files via Glue catalog |
| IAM Role | User/role with athena:StartQueryExecution |
| Failure Path | Query timeout, user retries |
| Correlation ID | event_id queryable in WHERE clause |

**Risks:**
1. Aurora Serverless v2 scales slowly; sudden spike above 500/day may cause connection exhaustion or cold-start latency.
2. Step Functions Express 5-minute limit; if decision Lambda hangs, execution terminates without completing write.

---

# Option 3: EventBridge → SQS FIFO → Lambda → DynamoDB → EventBridge Pipes → S3

**Summary:** Queue-first with FIFO ordering, DynamoDB for decisions, EventBridge Pipes for zero-code settlement export.

**Services in order:**
EventBridge → SQS FIFO → Lambda (Decision & Write) → DynamoDB → EventBridge Pipes → S3

## Service Boundary Analysis

### Hop 1: EventBridge → SQS FIFO

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (SendMessage via target) |
| Sync or Async | Async, EventBridge fire-and-forget |
| Payload Shape | Cancellation event as message body |
| IAM Role | EventBridge service role, sqs:SendMessage |
| Failure Path | EventBridge retry, then DLQ |
| Correlation ID | event_id in message body |

### Hop 2: SQS FIFO → Lambda (Decision & Write)

| Aspect | Answer |
|--------|--------|
| Push or Pull | Pull (Lambda event source mapping) |
| Sync or Async | Async batch poll, up to 10 |
| Payload Shape | Batch of SQS message records |
| IAM Role | Lambda execution role, sqs:ReceiveMessage |
| Failure Path | Visibility timeout, redrive after 3x |
| Correlation ID | event_id in each message body |

### Hop 3: Lambda → DynamoDB

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (BatchWriteItem for efficiency) |
| Sync or Async | Sync batch write, wait acknowledgment |
| Payload Shape | Decision items: pnr, decision, refund |
| IAM Role | Lambda execution role, dynamodb:BatchWriteItem |
| Failure Path | Partial batch retry, unprocessed items |
| Correlation ID | event_id as sort key |

### Hop 4: DynamoDB → EventBridge Pipes

| Aspect | Answer |
|--------|--------|
| Push or Pull | Pull (Pipes polls DynamoDB stream) |
| Sync or Async | Async stream consumption |
| Payload Shape | Stream record with NEW_IMAGE |
| IAM Role | Pipes service role, dynamodb:GetRecords |
| Failure Path | Pipes retry, DLQ after exhaustion |
| Correlation ID | event_id in stream record |

### Hop 5: EventBridge Pipes → S3

| Aspect | Answer |
|--------|--------|
| Push or Pull | Push (PutObject via Pipes target) |
| Sync or Async | Async write per record |
| Payload Shape | JSON object per decision record |
| IAM Role | Pipes service role, s3:PutObject |
| Failure Path | Pipes DLQ, CloudWatch alarm |
| Correlation ID | event_id in S3 object content |

**Risks:**
1. SQS FIFO MessageGroupId = PNR creates ordering bottleneck; single slow PNR blocks others in same group.
2. EventBridge Pipes writes one S3 object per record; 500/day creates 15,000/month objects, increasing LIST costs.

---

# Comparison Table

| Criterion | Option 1 (EventBridge-Lambda-DDB-S3) | Option 2 (Step Functions-Aurora-Athena) | Option 3 (SQS-Lambda-DDB-Pipes) |
|-----------|--------------------------------------|----------------------------------------|--------------------------------|
| **Ordering Guarantee** | DynamoDB + SQS FIFO per PNR | Step Functions sequential per execution | SQS FIFO MessageGroupId per PNR |
| **Settlement Query** | S3 JSONL, manual parsing | Athena SQL on Parquet | S3 JSON objects, Athena |
| **Complexity** | 7 hops, 3 Lambdas | 6 hops, orchestration logic | 5 hops, Pipes config |
| **Cost Driver** | Lambda invocations, DynamoDB writes | Aurora ACU-hours, Step Functions | SQS requests, Pipes throughput |
| **Failure Visibility** | CloudWatch per Lambda, DLQ | Step Functions execution history | SQS redrive, Pipes DLQ |
| **PNR Ordering Risk** | Medium (FIFO cardinality) | Low (single execution) | High (MessageGroupId bottleneck) |
