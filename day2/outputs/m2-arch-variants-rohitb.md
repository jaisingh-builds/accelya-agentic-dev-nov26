# Architecture options (3) - rohitb

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 508/2729_

## Prompt

## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: 2000 cancellations/day, peaks of 50/minute after a flight disruption
Ordering: order matters per PNR (process events for the same PNR in sequence; different PNRs may run in parallel)

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.
- Never log FFQ, PNR And account Info

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop, two risks, and a rough cost driver. End with a comparison table.


## Bedrock answer

# Option 1: EventBridge → Lambda → DynamoDB Streams → Lambda → SQS FIFO → Lambda → DynamoDB

**Summary:** Event-driven serverless with FIFO queues enforcing per-PNR ordering and DynamoDB for audit trail.

**Services in order:**
EventBridge → Lambda (Validator) → DynamoDB (Events Table) → DynamoDB Streams → Lambda (Router) → SQS FIFO → Lambda (Refund Processor) → DynamoDB (Decisions Table) → EventBridge (Settlement Bus)

## Service Boundary Analysis

### Hop 1: EventBridge → Lambda (Validator)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (event trigger) |
| Sync or Async | Async invocation |
| Payload Shape | Full cancellation event JSON |
| IAM Role | EventBridge execution role invokes Lambda |
| Failure Path | DLQ on Lambda, retry 2x |
| Correlation ID | event_id in EventBridge metadata |

### Hop 2: Lambda (Validator) → DynamoDB (Events)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (SDK PutItem) |
| Sync or Async | Sync write with response |
| Payload Shape | Event plus validation timestamp |
| IAM Role | Lambda execution role writes DynamoDB |
| Failure Path | Lambda catches, logs to CloudWatch |
| Correlation ID | event_id as partition key |

### Hop 3: DynamoDB (Events) → DynamoDB Streams → Lambda (Router)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (stream trigger) |
| Sync or Async | Async batch invocation |
| Payload Shape | NEW_IMAGE with event attributes |
| IAM Role | DynamoDB Streams invokes Lambda |
| Failure Path | Bisect batch, DLQ after retries |
| Correlation ID | event_id in stream record |

### Hop 4: Lambda (Router) → SQS FIFO

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (SendMessage API) |
| Sync or Async | Sync acknowledgment |
| Payload Shape | Event with refund rules metadata |
| IAM Role | Lambda execution role sends SQS |
| Failure Path | Lambda retries, exponential backoff |
| Correlation ID | MessageDeduplicationId = event_id, GroupId = pnr |

### Hop 5: SQS FIFO → Lambda (Refund Processor)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Pull (Lambda polls queue) |
| Sync or Async | Sync processing, batch size 1 |
| Payload Shape | Full event with routing metadata |
| IAM Role | Lambda execution role receives SQS |
| Failure Path | Visibility timeout, redrive to DLQ |
| Correlation ID | event_id in message body |

### Hop 6: Lambda (Refund Processor) → DynamoDB (Decisions)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (PutItem with decision) |
| Sync or Async | Sync write |
| Payload Shape | Decision record with ticket_number |
| IAM Role | Lambda execution role writes DynamoDB |
| Failure Path | Conditional write, Lambda retry logic |
| Correlation ID | event_id as sort key |

### Hop 7: Lambda (Refund Processor) → EventBridge (Settlement)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (PutEvents API) |
| Sync or Async | Async fire-and-forget |
| Payload Shape | Decision summary for settlement team |
| IAM Role | Lambda execution role puts EventBridge |
| Failure Path | Best effort, log failures only |
| Correlation ID | event_id in detail field |

## Risks
1. **DynamoDB Streams lag during disruption peaks** – 50 events/min per PNR could cause Lambda throttling on stream processing.
2. **SQS FIFO throughput ceiling** – 300 TPS per queue limits scale; need multiple queues sharded by fare_class or od_pair.

---

# Option 2: API Gateway → Step Functions Express → Lambda (Decision) → DynamoDB + SNS

**Summary:** Synchronous REST API with Step Functions orchestrating decision logic and parallel fan-out to settlement.

**Services in order:**
API Gateway → Step Functions Express Workflow → Lambda (Rules Engine) → DynamoDB (Audit) + SNS Topic → Lambda (Settlement Subscriber)

## Service Boundary Analysis

### Hop 1: API Gateway → Step Functions

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (HTTP POST invokes) |
| Sync or Async | Sync StartSyncExecution |
| Payload Shape | Cancellation event in request body |
| IAM Role | API Gateway execution role starts workflow |
| Failure Path | 500 error to caller, retry |
| Correlation ID | Generated execution ARN |

### Hop 2: Step Functions → Lambda (Rules Engine)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (task invocation) |
| Sync or Async | Sync with result capture |
| Payload Shape | Event with workflow context |
| IAM Role | Step Functions role invokes Lambda |
| Failure Path | Catch state, manual review path |
| Correlation ID | Execution ARN in context |

### Hop 3: Lambda → DynamoDB (Audit)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (TransactWriteItems) |
| Sync or Async | Sync transaction |
| Payload Shape | Decision plus event_id, timestamp |
| IAM Role | Lambda execution role writes DynamoDB |
| Failure Path | Transaction fails, Lambda throws error |
| Correlation ID | event_id as primary key |

### Hop 4: Lambda → SNS Topic

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (Publish API) |
| Sync or Async | Async publish |
| Payload Shape | Decision notification with ticket_number |
| IAM Role | Lambda execution role publishes SNS |
| Failure Path | Best effort, CloudWatch metric alarm |
| Correlation ID | MessageAttributes include event_id |

### Hop 5: SNS → Lambda (Settlement)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (subscription trigger) |
| Sync or Async | Async invocation |
| Payload Shape | SNS envelope with decision |
| IAM Role | SNS invokes Lambda via subscription |
| Failure Path | DLQ on Lambda, retry 2x |
| Correlation ID | event_id from message attributes |

## Risks
1. **No native PNR ordering guarantee** – Step Functions processes concurrently; need DynamoDB conditional writes to detect race conditions.
2. **API Gateway timeout at 29 seconds** – Complex refund rules or external fare lookups may exceed limit during peak.

---

# Option 3: S3 Event → Lambda → SQS Standard (per-PNR) → Lambda → DynamoDB + EventBridge

**Summary:** Batch file ingestion with dynamic per-PNR queue creation ensuring ordering without FIFO queue limits.

**Services in order:**
S3 Bucket → Lambda (Splitter) → SQS Standard Queues (one per active PNR) → Lambda (Processor) → DynamoDB + EventBridge

## Service Boundary Analysis

### Hop 1: S3 → Lambda (Splitter)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (S3 event notification) |
| Sync or Async | Async invocation |
| Payload Shape | S3 object key, batch file |
| IAM Role | S3 invokes Lambda via bucket policy |
| Failure Path | DLQ, manual reprocess file |
| Correlation ID | S3 object ETag |

### Hop 2: Lambda (Splitter) → SQS Standard

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (SendMessageBatch) |
| Sync or Async | Sync batch send |
| Payload Shape | Individual cancellation events |
| IAM Role | Lambda execution role sends SQS |
| Failure Path | Partial batch failure, retry failed |
| Correlation ID | event_id in message body |

### Hop 3: SQS Standard → Lambda (Processor)

| Aspect | Answer |
|--------|---------|
| Push or Pull | Pull (Lambda event source) |
| Sync or Async | Sync batch processing |
| Payload Shape | Cancellation event from queue |
| IAM Role | Lambda execution role receives SQS |
| Failure Path | Visibility timeout, redrive policy |
| Correlation ID | event_id extracted from body |

### Hop 4: Lambda (Processor) → DynamoDB

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (BatchWriteItem) |
| Sync or Async | Sync batch write |
| Payload Shape | Decision records with pnr index |
| IAM Role | Lambda execution role writes DynamoDB |
| Failure Path | Unprocessed items, Lambda retry |
| Correlation ID | event_id as range key |

### Hop 5: Lambda (Processor) → EventBridge

| Aspect | Answer |
|--------|---------|
| Push or Pull | Push (PutEvents) |
| Sync or Async | Async batch publish |
| Payload Shape | Settlement notification batch |
| IAM Role | Lambda execution role puts events |
| Failure Path | Failed entries logged, no retry |
| Correlation ID | event_id in detail-type |

## Risks
1. **Queue proliferation overhead** – Creating/deleting queues per PNR adds latency; need queue lifecycle management Lambda.
2. **S3 batch file delays visibility** – Settlement team sees decisions only after full file processes, not real-time per cancellation.

---

# Comparison

| Criterion | Option 1 (EventBridge+FIFO) | Option 2 (API Gateway+Step Functions) | Option 3 (S3 Batch) |
|-----------|------------------------------|---------------------------------------|---------------------|
| **Ordering Guarantee** | Strong (FIFO MessageGroupId) | Weak (app-level locking) | Strong (per-PNR queues) |
| **Real-time Visibility** | Yes (stream-driven) | Yes (sync response) | No (batch delay) |
| **Scalability** | Limited by FIFO 300 TPS | High (Express workflows) | High (Standard SQS) |
| **Operational Complexity** | Medium (stream tuning) | Low (managed orchestration) | High (queue lifecycle) |
| **Cost Driver** | DynamoDB Streams + Lambda invocations | Step Functions executions | S3 storage + Lambda duration |
| **Best For** | Event-driven, strict ordering | Synchronous API, audit trails | Bulk settlement, batch ingestion |
