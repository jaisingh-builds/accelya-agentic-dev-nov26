# Architecture options (3) - ganesh

_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 500/2282_

## Prompt

## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: cancellation Volume might vary on the day, but on an average we get around 2000 cancellations per day.
Ordering: ordering is important per PNR.

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.
- Do not create and save any copy of the data.

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop, two risks, and a rough cost driver(including how you derived the cost). End with a comparison table.


## Bedrock answer

# Option 1: EventBridge + Lambda + DynamoDB Streams + Step Functions

**Summary:** Event-driven orchestration with DynamoDB as single source of truth and Step Functions for refund workflow.

**Services in order:**
EventBridge → Lambda (Validator) → DynamoDB → DynamoDB Streams → Lambda (Processor) → Step Functions → Lambda (Decision Engine) → DynamoDB → EventBridge → Lambda (Settlement Notifier)

## Service Boundary Analysis

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|------------|---------------|----------|--------------|----------------|
| EventBridge→Lambda(Validator) | Push | Async | Full cancellation event JSON | EventBridgeInvokeLambda | DLQ to SQS | event_id in detail |
| Lambda→DynamoDB | Push | Sync | Item with pnr, ticket_number, status=PENDING | LambdaWriteDynamoDB | Lambda retry, CloudWatch alarm | event_id as partition key |
| DynamoDB→Streams→Lambda(Processor) | Push | Async | Stream record with NEW_IMAGE | StreamsInvokeLambda | Retry with exponential backoff | event_id in Keys |
| Lambda→Step Functions | Push | Async | Execution input with event_id, pnr | LambdaStartExecution | Step Functions built-in retry | execution_arn contains event_id |
| Step Functions→Lambda(Decision) | Push | Sync | Task input with fare_class, departure_status | StepFunctionsInvokeLambda | Catch state, fallback to MANUAL_REVIEW | event_id in input context |
| Lambda→DynamoDB | Push | Sync | Update item: decision, refund_amount, timestamp | LambdaUpdateDynamoDB | Step Functions retry policy | event_id as key |
| DynamoDB→EventBridge | Push | Async | Event pattern match on decision attribute | DynamoDBPutEvents | EventBridge retry, DLQ | event_id in detail |
| EventBridge→Lambda(Settlement) | Push | Async | Decision event with pnr, ticket_number | EventBridgeInvokeLambda | DLQ to SQS | event_id in detail |

**Risks:**
1. DynamoDB hot partition if single airline dominates PNR distribution; mitigate with composite key (pnr#ticket_number).
2. Step Functions concurrent execution limits (default 1000) could throttle during peak cancellation waves.

**Cost drivers:**
- DynamoDB: 2000 writes/day × 2 (initial + update) × $1.25/million = $0.005/day
- Lambda: 2000 invocations × 4 functions × $0.20/million = $0.002/day
- Step Functions: 2000 executions × $0.025/1000 = $0.05/day
- **Total: ~$2/month** (Step Functions dominates)

---

# Option 2: API Gateway + SQS FIFO + Lambda + Aurora Serverless

**Summary:** FIFO queues guarantee PNR ordering with Aurora for settlement team queries and audit trail.

**Services in order:**
API Gateway → SQS FIFO → Lambda (Consumer) → Aurora Serverless (PostgreSQL) → Lambda (Decision Engine) → Aurora → SNS → Lambda (Settlement Subscriber)

## Service Boundary Analysis

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|------------|---------------|----------|--------------|----------------|
| API Gateway→SQS FIFO | Push | Async | Message body with cancellation event | APIGatewayPublishSQS | API Gateway 5xx, client retry | MessageId returned to caller |
| SQS→Lambda(Consumer) | Pull | Async | Batch up to 10 messages | SQSInvokeLambda | Visibility timeout, redrive to DLQ | MessageId in Records array |
| Lambda→Aurora | Push | Sync | INSERT with pnr, status, processing_state | LambdaRDSDataAPI | Transaction rollback, Lambda retry | event_id as primary key |
| Lambda(Decision)→Aurora | Push | Sync | UPDATE SET decision, refund_amount WHERE event_id | LambdaRDSDataAPI | Deadlock retry, transaction isolation | event_id in WHERE clause |
| Aurora→SNS | Push | Async | Publish decision message with ticket_number | AuroraPublishSNS | SNS retry policy | event_id in MessageAttributes |
| SNS→Lambda(Settlement) | Push | Async | Notification with decision payload | SNSInvokeLambda | DLQ for failed invocations | event_id in MessageAttributes |

**Risks:**
1. FIFO queue throughput limited to 3000 messages/sec per group; PNR as MessageGroupId could bottleneck high-volume PNRs.
2. Aurora Serverless v2 cold starts (10-30 sec) during low-traffic periods delay first cancellations of day.

**Cost drivers:**
- SQS FIFO: 2000 requests × $0.50/million = $0.001/day
- Lambda: 2000 invocations × 3 functions × $0.20/million = $0.001/day
- Aurora Serverless v2: 0.5 ACU min × $0.12/ACU-hour × 24 = $1.44/day
- **Total: ~$45/month** (Aurora minimum capacity dominates)

---

# Option 3: AppSync + Lambda + S3 + Athena + SNS

**Summary:** GraphQL mutations trigger decisions, S3 stores settlement records, Athena provides ad-hoc settlement queries.

**Services in order:**
AppSync (GraphQL) → Lambda (Resolver) → Lambda (Decision Engine) → S3 (Partitioned by date) → SNS → Lambda (Settlement Notifier) + Athena (Query Layer)

## Service Boundary Analysis

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|------------|---------------|----------|--------------|----------------|
| AppSync→Lambda(Resolver) | Push | Sync | GraphQL mutation args with cancellation fields | AppSyncInvokeLambda | GraphQL error response to client | event_id in arguments |
| Lambda→Lambda(Decision) | Push | Sync | Direct invocation with event payload | LambdaInvokeLambda | RequestResponse error, caller handles | event_id in InvocationType payload |
| Lambda→S3 | Push | Sync | PutObject JSON Lines format, partitioned dt=YYYY-MM-DD | LambdaPutS3 | S3 retry with exponential backoff | event_id as object key prefix |
| Lambda→SNS | Push | Async | Publish with decision and ticket_number | LambdaPublishSNS | SNS DLQ for failures | event_id in MessageAttributes |
| SNS→Lambda(Settlement) | Push | Async | Fan-out to settlement subscribers | SNSInvokeLambda | Per-subscription DLQ | event_id in Message body |
| Athena→S3 | Pull | Sync | SELECT query on partitioned settlement table | AthenaReadS3 | Query timeout, retry from console | event_id in WHERE filter |

**Risks:**
1. S3 eventual consistency for LIST operations means Athena queries might miss records written in last second.
2. No native PNR ordering guarantee; application must sort results or use event_id sequence numbers.

**Cost drivers:**
- AppSync: 2000 requests × $4/million = $0.008/day
- Lambda: 2000 invocations × 3 functions × $0.20/million = $0.001/day
- S3: 2000 objects × $0.005/1000 + storage negligible = $0.01/day
- Athena: ~10 queries/day × $5/TB scanned (minimal) = $0.01/day
- **Total: ~$1/month** (lowest cost option)

---

# Comparison Table

| Criterion | Option 1 (EventBridge+DynamoDB) | Option 2 (SQS FIFO+Aurora) | Option 3 (AppSync+S3) |
|-----------|--------------------------------|---------------------------|----------------------|
| **PNR Ordering** | Application-level sorting required | Native FIFO per MessageGroupId | No guarantee, client sorts |
| **Settlement Query** | DynamoDB query with GSI | SQL queries on Aurora | Athena SQL on S3 |
| **Latency** | 200-500ms end-to-end | 1-3 sec (queue polling) | 100-300ms synchronous |
| **Scalability** | Auto-scales to 40K RCU | FIFO 3K msg/sec limit | Unlimited AppSync requests |
| **Monthly Cost** | ~$2 | ~$45 | ~$1 |
| **Operational Complexity** | Medium (6 services) | Low (4 services) | Medium (Athena partition management) |
| **Audit Trail** | DynamoDB Point-in-Time Recovery | Aurora automated backups | S3 versioning, immutable |
