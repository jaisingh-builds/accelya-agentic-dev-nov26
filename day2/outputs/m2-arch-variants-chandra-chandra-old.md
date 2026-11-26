# Architecture options (3) - chandra

_model: us.anthropic.claude-haiku-4-5-20251001-v1:0 | tokens in/out: 498/1871_

## Prompt

## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: 2,000 cancellations/day, peaks of 50/minute after a flight disruption.
Ordering: no PNR ordering required. requested event processed first

## EXAMPLES
One event: {"event_id": "evt-1002", "pnr": "LM4K8D", "fare_class": "K", "departure_status": "scheduled",
"no_show_flag": false, "fare_amount": "4120.00"}  ->  APPROVE, R-SAVER, refund 3090.00

## NON-GOALS
- No Kinesis, no MSK, no EC2 or servers to patch.
- Never log passenger names, emails or fare amounts.
- No complex business logic, keep it simple.

## OUTPUT FORMAT
Exactly THREE architecture options. For each: a one-line summary, the services in order, the six
service-boundary answers for every hop, two risks, and a rough cost driver(including how you derived the cost). End with a comparison table.


## Bedrock answer

# THREE ARCHITECTURE OPTIONS FOR AIRLINE SETTLEMENT CANCELLATION PROCESSING

---

## OPTION 1: EventBridge → Lambda → DynamoDB → SQS → Settlement Dashboard

**Summary:** Event-driven synchronous refund decisions with async settlement notification.

**Services in order:** EventBridge → Lambda (Refund Decision) → DynamoDB → SQS → Lambda (Settlement Logger) → CloudWatch Logs

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|-----------|---------------|----------|--------------|-----------------|
| EventBridge→Lambda | Push | Sync | `{event_id, pnr, fare_class, departure_status, no_show_flag, fare_amount}` | EventBridge-invoke-Lambda | DLQ to SQS | event_id in request |
| Lambda→DynamoDB | Push | Sync | `{event_id, pnr, decision, refund_amount, timestamp}` | Lambda-write-DynamoDB | Exponential backoff retry | event_id as PK |
| Lambda→SQS | Push | Async | `{event_id, pnr, decision, refund_amount, od_pair}` | Lambda-send-SQS | Message retained 14 days | event_id in message |
| SQS→Lambda | Pull | Async | Same as above | SQS-invoke-Lambda | Visibility timeout 300s | event_id in body |
| Lambda→CloudWatch | Push | Async | `{event_id, decision, settlement_status}` | Lambda-write-CloudWatch | Local buffering | event_id in log group |

**Risks:**
1. **Lambda cold starts during 50/minute peaks** may cause timeouts; refund decision latency could exceed SLA if concurrency exhausted.
2. **DynamoDB hot partition on pnr prefix** if certain airlines dominate traffic; write throttling possible without on-demand billing.

**Cost Driver:** ~$180/month (2,000 events × $0.0000002 EventBridge + Lambda compute 512MB × 2s × $0.0000166 + DynamoDB on-demand + SQS standard queue).

---

## OPTION 2: API Gateway → Step Functions → Lambda → RDS Aurora → SNS → Settlement Portal

**Summary:** Orchestrated workflow with relational audit trail and SNS fan-out to settlement teams.

**Services in order:** API Gateway → Step Functions → Lambda (Refund Logic) → Aurora PostgreSQL → SNS → Lambda (Portal Updater)

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|-----------|---------------|----------|--------------|-----------------|
| API Gateway→Step Functions | Push | Sync | `{event_id, pnr, fare_class, departure_status, no_show_flag, fare_amount}` | API-invoke-StepFunctions | 400 error to client | event_id in path |
| Step Functions→Lambda | Push | Sync | Same as above | StepFunctions-invoke-Lambda | Retry policy 3× then MANUAL_REVIEW | event_id in context |
| Lambda→Aurora | Push | Sync | `INSERT INTO refund_decisions (event_id, pnr, decision, refund_amount)` | Lambda-write-Aurora | Rollback transaction | event_id as FK |
| Aurora→SNS | Push | Async | `{event_id, pnr, decision, refund_amount, settlement_status}` | Aurora-publish-SNS | SNS retry 3× | event_id in message attribute |
| SNS→Lambda | Pull | Async | Same as above | SNS-invoke-Lambda | DLQ for failed messages | event_id in attribute |
| Lambda→Portal API | Push | Async | `{event_id, decision, timestamp, od_pair}` | Lambda-invoke-Portal | Exponential backoff | event_id in header |

**Risks:**
1. **Aurora provisioned capacity underestimated** during 50/minute peaks; scaling lag causes write contention and settlement delays.
2. **Step Functions execution history cost** grows unbounded; 2,000/day × 365 = 730K executions/year at $0.000025 each = $18/year but audit retention policy unclear.

**Cost Driver:** ~$320/month (Aurora db.t3.small ~$40 + Step Functions executions $18/year + Lambda + SNS + data transfer).

---

## OPTION 3: SQS Standard → Lambda Batch Processor → S3 Parquet → Athena → QuickSight Dashboard

**Summary:** Batch-oriented settlement ledger with S3 data lake and SQL analytics.

**Services in order:** SQS → Lambda (Batch Processor) → S3 (Parquet) → Glue Catalog → Athena → QuickSight

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|-----------|---------------|----------|--------------|-----------------|
| SQS→Lambda | Pull | Async | `{event_id, pnr, fare_class, departure_status, no_show_flag, fare_amount}` | SQS-invoke-Lambda | Visibility timeout 900s | event_id in body |
| Lambda→S3 | Push | Async | Batch `[{event_id, pnr, decision, refund_amount, timestamp}]` | Lambda-write-S3 | Retry to DLQ SQS | event_id in object metadata |
| S3→Glue Catalog | Push | Async | Parquet schema auto-discovery | Glue-crawl-S3 | Crawler failure logged | event_id in partition key |
| Glue→Athena | Pull | Sync | SQL query on Parquet | Athena-read-Glue | Query timeout 30min | event_id in WHERE clause |
| Athena→QuickSight | Pull | Async | `SELECT event_id, decision, refund_amount FROM refund_decisions` | QuickSight-query-Athena | Cached result stale | event_id in dataset |

**Risks:**
1. **Settlement visibility delay:** batch processing introduces 5–15 minute lag; real-time refund status unavailable for settlement team queries.
2. **Athena query cost explosion** if settlement team runs unoptimized queries; no partition pruning on event_id causes full table scans ($6.25 per TB scanned).

**Cost Driver:** ~$95/month (SQS standard $0.40 + Lambda batch 512MB × 10s × $0.0000166 + S3 storage 100GB × $0.023 + Athena scans ~$50 + QuickSight reader $9).

---

## COMPARISON TABLE

| Aspect | Option 1 | Option 2 | Option 3 |
|--------|----------|----------|----------|
| **Latency** | <2s decision | <5s orchestrated | 5–15m batch |
| **Real-time Settlement View** | Yes (SQS→Lambda) | Yes (SNS fan-out) | No (batch delay) |
| **Audit Trail** | DynamoDB TTL | Aurora ACID | S3 immutable |
| **Cost** | $180/mo | $320/mo | $95/mo |
| **Scalability** | Lambda concurrency limit | Aurora provisioning | Batch throughput |
| **Operational Complexity** | Low | Medium | Medium |
| **Best For** | High-frequency, low-latency | Regulated, audit-heavy | Cost-optimized analytics |
