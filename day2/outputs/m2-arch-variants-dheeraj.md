# Architecture options (3) - dheeraj

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 489/2717_

## Prompt

## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: 1,000 cancellations/day, peaks of 10/minute after a flight disruption
Ordering: order matter per PNR

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.
- Do not introduce third-party dependencies outside the Python standard library.

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop, two risks, and a rough cost driver. End with a comparison table.


## Bedrock answer

# Option 1: EventBridge → Lambda → DynamoDB Streams → Lambda → SQS FIFO → Lambda

**Summary:** Event-driven pipeline using EventBridge rules, DynamoDB for state, FIFO queues for ordered settlement delivery.

**Services in order:**
1. EventBridge custom bus
2. Lambda (refund-decision-engine)
3. DynamoDB table (decisions)
4. DynamoDB Streams
5. Lambda (settlement-forwarder)
6. SQS FIFO queue (settlement-queue)
7. Lambda (settlement-consumer)

## Service Boundary Analysis

### Hop 1: EventBridge → Lambda (refund-decision-engine)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Async | Full cancellation event JSON | EventBridgeInvokeLambdaRole | DLQ after 2 retries | event_id in event detail |

### Hop 2: Lambda → DynamoDB

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Item: pk=PNR, sk=ticket_number, decision | LambdaWriteDynamoRole | Lambda retry, then DLQ | event_id in item attribute |

### Hop 3: DynamoDB → DynamoDB Streams

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Async | Stream record with NEW_IMAGE | N/A (native) | Stream retention 24h | event_id in NEW_IMAGE |

### Hop 4: DynamoDB Streams → Lambda (settlement-forwarder)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Async | Batch of stream records | StreamsInvokeLambdaRole | Bisect batch, DLQ after retries | event_id extracted from records |

### Hop 5: Lambda → SQS FIFO

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Decision summary, MessageGroupId=PNR | LambdaSendSQSRole | Lambda retry logic | event_id as MessageDeduplicationId |

### Hop 6: SQS FIFO → Lambda (settlement-consumer)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Pull | Async | Batch up to 10 messages | SQSInvokeLambdaRole | Visibility timeout, redrive to DLQ | event_id in message body |

**Risks:**
1. DynamoDB Streams shard iterator expires if settlement-forwarder fails repeatedly; lost events require replay from table scan.
2. FIFO queue throughput limited to 300 TPS per MessageGroupId; single busy PNR blocks others in same group.

**Cost driver:** DynamoDB on-demand writes (1,000/day minimal), Lambda invocations.

---

# Option 2: S3 Event Notifications → Lambda → Step Functions → Lambda → SNS → SQS

**Summary:** S3-triggered workflow orchestration with Step Functions managing refund logic and settlement fan-out via SNS.

**Services in order:**
1. S3 bucket (cancellation-events)
2. Lambda (event-validator)
3. Step Functions (refund-workflow)
4. Lambda (decision-logic)
5. DynamoDB (audit-trail)
6. SNS topic (settlement-notifications)
7. SQS standard queue (settlement-inbox)

## Service Boundary Analysis

### Hop 1: S3 → Lambda (event-validator)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Async | S3 event notification JSON | S3InvokeLambdaRole | Retry 3x, undelivered stays in S3 | S3 object key contains event_id |

### Hop 2: Lambda → Step Functions

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Parsed cancellation event object | LambdaStartExecutionRole | Lambda catches, logs, fails | event_id as execution name |

### Hop 3: Step Functions → Lambda (decision-logic)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Task input with fare_class, no_show | StepFunctionsInvokeLambdaRole | Retry 3x exponential backoff | execution ARN in context |

### Hop 4: Lambda → DynamoDB

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Decision record, timestamp, PNR | LambdaWriteDynamoRole | Step Functions catches, transitions to fail | event_id in partition key |

### Hop 5: Step Functions → SNS

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Settlement summary message | StepFunctionsPublishSNSRole | Retry then Catch state | event_id in message attributes |

### Hop 6: SNS → SQS

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Async | Same settlement message | SNS subscription (native) | DLQ on subscription | event_id preserved in attributes |

**Risks:**
1. S3 event notifications do not guarantee order; concurrent uploads for same PNR may process out-of-sequence.
2. Step Functions execution history grows unbounded; 25K event limit reached if decision-logic loops or retries excessively.

**Cost driver:** Step Functions state transitions (7 per execution), S3 PUT requests.

---

# Option 3: API Gateway → Lambda → SQS FIFO → Lambda → DynamoDB → EventBridge Pipes

**Summary:** Synchronous ingestion via API Gateway, FIFO processing for ordering, EventBridge Pipes for settlement delivery.

**Services in order:**
1. API Gateway REST API
2. Lambda (ingestion-handler)
3. SQS FIFO queue (cancellation-queue)
4. Lambda (refund-processor)
5. DynamoDB (decisions-table)
6. EventBridge Pipes
7. EventBridge target (settlement-bus)

## Service Boundary Analysis

### Hop 1: API Gateway → Lambda (ingestion-handler)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | POST body with cancellation event | APIGatewayInvokeLambdaRole | 5xx returned to caller | event_id in request body |

### Hop 2: Lambda → SQS FIFO

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Event JSON, MessageGroupId=PNR | LambdaSendSQSRole | Lambda returns 500 to API Gateway | event_id as deduplication ID |

### Hop 3: SQS FIFO → Lambda (refund-processor)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Pull | Async | Batch size 1 for ordering | SQSInvokeLambdaRole | Redrive after maxReceiveCount=3 | event_id in message body |

### Hop 4: Lambda → DynamoDB

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Sync | Decision item with GSI on timestamp | LambdaWriteDynamoRole | Message returns to queue | event_id as sort key |

### Hop 5: DynamoDB → EventBridge Pipes

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Pull | Async | Streams record filtered by decision attribute | PipesReadStreamsRole | DLQ on pipe, retries 3x | event_id in dynamodb.NewImage |

### Hop 6: EventBridge Pipes → EventBridge (settlement-bus)

| Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----------|------------|---------------|----------|--------------|----------------|
| Push | Async | Transformed event with detail-type=SettlementDecision | PipesPutEventsRole | Pipe DLQ after retries | event_id in detail object |

**Risks:**
1. FIFO queue batch-size=1 reduces throughput; 10/min peak easily handled but limits future scale.
2. EventBridge Pipes filtering in DynamoDB Streams may miss records if filter expression references non-existent attributes.

**Cost driver:** API Gateway requests (1,000/day), EventBridge Pipes polling.

---

# Comparison

| Criterion | Option 1 (EventBridge-first) | Option 2 (S3-triggered) | Option 3 (API Gateway sync) |
|-----------|------------------------------|-------------------------|------------------------------|
| **Ordering guarantee** | FIFO queue per PNR | None (S3 notifications) | FIFO queue per PNR |
| **Ingestion model** | Event-driven push | File drop | Synchronous REST API |
| **Complexity** | Medium (7 services) | High (orchestration) | Medium (6 services) |
| **Settlement delivery** | SQS pull | SNS fan-out | EventBridge Pipes push |
| **Audit trail** | DynamoDB table | DynamoDB + S3 objects | DynamoDB table |
| **Best for** | Decoupled event sources | Batch file uploads | Real-time caller feedback |

---

## Cost estimate - how it was derived (added by reviewer, not part of the Bedrock answer)

The Bedrock answer only named a "cost driver" per option. This section turns that into a monthly estimate
so the three options can be compared on numbers.

### 1. Volume (from the prompt)
- 1,000 cancellations/day x 30 days = **30,000 events/month**.
- The peak of 10/minute changes concurrency, not cost: every service here bills per request, so cost
  scales linearly with the monthly count.

### 2. Assumptions
| Item | Assumption | Why |
|---|---|---|
| Lambda size and run time | 256 MB, 200 ms average = 0.25 GB x 0.2 s = **0.05 GB-s per invoke** | Small JSON decision logic, standard library only |
| Event / item size | < 1 KB | One cancellation record |
| DynamoDB write | 1 write request unit per item (< 1 KB) | On-demand capacity |
| SQS messages | 3 requests per message (send + receive + delete), < 64 KB | Lambda event source mapping does receive + delete |
| Logs | ~2 KB per Lambda invoke (app JSON lines + START/END/REPORT) | Logging contract: identifiers and counts only |
| Free tier | **Ignored** | Sandbox and production accounts can't rely on it; with free tier, Lambda/SQS/SNS/Step Functions would mostly be $0 |

### 3. Unit prices used (us-east-1 list prices, approximate)
| Service | Price |
|---|---|
| Lambda requests | $0.20 per 1M |
| Lambda compute (x86) | $0.0000166667 per GB-s |
| DynamoDB on-demand writes | $0.625 per 1M write request units |
| DynamoDB Streams read by a Lambda trigger | $0 (not billed when Lambda is the consumer) |
| SQS standard / FIFO | $0.40 / $0.50 per 1M requests |
| EventBridge custom events (PutEvents) | $1.00 per 1M |
| EventBridge Pipes | $0.40 per 1M requests (after filtering) |
| Step Functions Standard | $0.025 per 1,000 state transitions |
| SNS publish | $0.50 per 1M (SNS -> SQS delivery free) |
| S3 PUT / GET | $0.005 / $0.0004 per 1,000 |
| API Gateway REST API (HTTP API) | $3.50 ($1.00) per 1M |
| CloudWatch Logs ingestion | $0.50 per GB |

Prices change - verify in the AWS Pricing Calculator (calculator.aws) before quoting a number to anyone.

### 4. Arithmetic per option (per month, 30,000 events)

**Option 1 - EventBridge -> Lambda -> DynamoDB Streams -> Lambda -> SQS FIFO -> Lambda**
| Line | Calculation | Cost |
|---|---|---|
| EventBridge PutEvents | 30,000 x $1.00/1M | $0.030 |
| Lambda requests (3 functions) | 90,000 x $0.20/1M | $0.018 |
| Lambda compute | 90,000 x 0.05 GB-s = 4,500 GB-s x $0.0000166667 | $0.075 |
| DynamoDB writes | 30,000 x $0.625/1M | $0.019 |
| DynamoDB Streams -> Lambda | free | $0.000 |
| SQS FIFO | 30,000 x 3 = 90,000 x $0.50/1M | $0.045 |
| CloudWatch Logs | 90,000 x 2 KB = 0.18 GB x $0.50 | $0.090 |
| **Total** | | **~$0.28** |

**Option 2 - S3 -> Lambda -> Step Functions -> Lambda/DynamoDB -> SNS -> SQS**
| Line | Calculation | Cost |
|---|---|---|
| S3 PUT (one object per event) | 30,000 x $0.005/1,000 | $0.150 |
| S3 GET (validator reads it) | 30,000 x $0.0004/1,000 | $0.012 |
| Lambda requests (2 functions) | 60,000 x $0.20/1M | $0.012 |
| Lambda compute | 60,000 x 0.05 = 3,000 GB-s x $0.0000166667 | $0.050 |
| **Step Functions Standard** | 30,000 x 7 transitions = 210,000 x $0.025/1,000 | **$5.250** |
| DynamoDB writes | 30,000 x $0.625/1M | $0.019 |
| SNS publish | 30,000 x $0.50/1M | $0.015 |
| SQS standard | 90,000 x $0.40/1M | $0.036 |
| CloudWatch Logs | 60,000 x 2 KB = 0.12 GB x $0.50 | $0.060 |
| **Total** | | **~$5.60** |

**Option 3 - API Gateway -> Lambda -> SQS FIFO -> Lambda -> DynamoDB -> EventBridge Pipes**
| Line | Calculation | Cost |
|---|---|---|
| API Gateway REST API | 30,000 x $3.50/1M | $0.105 |
| Lambda requests (2 functions) | 60,000 x $0.20/1M | $0.012 |
| Lambda compute | 3,000 GB-s x $0.0000166667 | $0.050 |
| SQS FIFO | 90,000 x $0.50/1M | $0.045 |
| DynamoDB writes incl. GSI on timestamp | 30,000 x 2 = 60,000 x $0.625/1M | $0.038 |
| EventBridge Pipes | 30,000 x $0.40/1M | $0.012 |
| EventBridge PutEvents to settlement bus | 30,000 x $1.00/1M | $0.030 |
| CloudWatch Logs | 0.12 GB x $0.50 | $0.060 |
| **Total** | | **~$0.35** |

DynamoDB storage adds about $0.25 per GB-month on top (30,000 items x 1 KB = ~0.03 GB/month, under $0.01).

### 5. What the numbers say
| | Option 1 | Option 2 | Option 3 |
|---|---|---|---|
| Monthly cost | ~$0.28 | ~$5.60 | ~$0.35 |
| Biggest line | Logs + Lambda compute | Step Functions transitions (94%) | API Gateway |
| Model's stated cost driver | DynamoDB writes - *not quite*: logs and compute are larger | Step Functions transitions - *correct* | API Gateway + "Pipes polling" - *half right*: Pipes bills per event, not per poll |

- At this volume **all three are cheap**; cost should not decide between them - ordering, failure paths and
  observability should.
- Option 2 is ~20x the others only because of Step Functions **Standard**. Step Functions **Express**
  (billed per request + duration) would bring it close to the other two.
- Option 3 drops ~$0.075 by using an HTTP API instead of a REST API.
- The estimate is most sensitive to Lambda run time and log volume: logging full payloads (which the
  logging contract forbids anyway) would multiply the log line several times.
