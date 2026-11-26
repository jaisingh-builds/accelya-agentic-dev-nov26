# Architecture options (3) - shailya

_Manually revised (not a Bedrock answer). Original Bedrock run: model us.anthropic.claude-sonnet-4-5-20250929-v1:0 | tokens in/out: 461/1910._
_Revision reason: the original options put Step Functions / a poller Lambda in front of the refund logic and used Aurora and QuickSight. The problem statement is "write a Lambda that processes cancellation events and decides refunds", so every option below has ONE Lambda as the direct event consumer._
_Updated for the revised prompt: volume raised from 500 to 2,000 cancellations/day; costs recalculated._

## Prompt

## ROLE
You are a senior AWS architect for Accelya's airline settlement platform (region us-east-1).

## CONTEXT
Business flow: a passenger or agent cancels a ticket. The cancellation event (pnr, ticket_number, fare_class,
departure_status, no_show_flag, fare_amount) must get a refund decision (APPROVE / REJECT / MANUAL_REVIEW)
and the settlement team must see every decision.
Volume: average of 2000 cancellation per day
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


## Revised answer

# Architecture Option 1: SQS FIFO + Lambda + DynamoDB (recommended)

**Summary:** A FIFO queue grouped by PNR feeds one refund-decision Lambda, which writes each decision to DynamoDB with a guard against duplicates.

**Services in order:**
Producer → SQS FIFO (MessageGroupId = pnr) → Lambda (Refund Decision) → DynamoDB (decisions table) → Settlement team queries the table

**Service Boundary Analysis:**

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|------------|---------------|----------|--------------|----------------|
| Producer → SQS FIFO | Push | Sync (SendMessage) | Cancellation event: event_id, pnr, ticket_number, fare_class, departure_status, no_show_flag, fare_amount | Producer role: `sqs:SendMessage` on the queue ARN | Producer retries; dedup ID = event_id prevents duplicates | event_id as MessageDeduplicationId and in the body |
| SQS FIFO → Lambda | Pull (event source mapping) | Async | Batch of up to 10 records, in order per pnr group | Lambda role: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the queue ARN | Partial batch failure reporting; after maxReceiveCount=3, message goes to the FIFO DLQ | event_id from the message body, logged as correlation_id |
| Lambda → DynamoDB | Push | Sync (PutItem, condition: event_id not already written) | Decision record: pnr, ticket_number, event_id, decision, refund_rule, refund_fare_amount (Decimal), settlement_date | Lambda role: `dynamodb:PutItem` on the table ARN | Duplicate write → skipped and logged; throttling → error returned so SQS retries | event_id as sort key |
| DynamoDB → Settlement team | Pull | Sync (Query) | Decisions filtered by settlement_date (via an index) | Settlement read role: `dynamodb:Query` on the table and index ARNs | Query error is surfaced to the caller | event_id on every record |

**Risks:**
1. **Poison message:** one failing event blocks the rest of that PNR's queue until it moves to the DLQ. Keep maxReceiveCount low and alarm on DLQ depth.
2. **DLQ replay:** events in the DLQ must be replayed in order for their PNR, so this needs a documented manual process.

**Cost driver:** SQS FIFO requests and DynamoDB on-demand writes. About $0.30/month before free tier (see Cost justification). Uses 1 Lambda.

---

# Architecture Option 2: EventBridge + SQS FIFO + Lambda + DynamoDB + Pipes + SNS

**Summary:** Same core as Option 1, but producers are decoupled through EventBridge, and decisions are pushed to the settlement team through a DynamoDB Stream, an EventBridge Pipe and SNS.

**Services in order:**
EventBridge rule → SQS FIFO (group = pnr) → Lambda (Refund Decision) → DynamoDB → DynamoDB Stream → EventBridge Pipe → SNS → Settlement team

**Service Boundary Analysis:**

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|------------|---------------|----------|--------------|----------------|
| EventBridge → SQS FIFO | Push | Async | Cancellation event detail; MessageGroupId set from detail.pnr | Queue resource policy: `sqs:SendMessage` allowed for the rule ARN only | EventBridge retries (up to 24h), then the rule's DLQ | event_id in detail; also used as dedup ID |
| SQS FIFO → Lambda | Pull (event source mapping) | Async | Batch of up to 10 records, in order per pnr | Lambda role: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the queue ARN | Partial batch failure; after 3 receives, FIFO DLQ | event_id from the body |
| Lambda → DynamoDB | Push | Sync (conditional PutItem) | Decision record: pnr, event_id, decision, refund_rule, refund_fare_amount, settlement_date | Lambda role: `dynamodb:PutItem` on the table ARN | Duplicate skipped; other errors returned so SQS retries | event_id as sort key |
| DynamoDB Stream → Pipe | Pull | Async | NEW_IMAGE record | Pipe role: `dynamodb:DescribeStream`, `GetRecords`, `GetShardIterator`, `ListStreams` on the stream ARN | Pipe retries, then the Pipe DLQ | event_id in NEW_IMAGE |
| Pipe → SNS | Push | Async | Filtered message: pnr, event_id, decision, settlement_date (no fare_amount) | Pipe role: `sns:Publish` on the topic ARN | Pipe DLQ; alarm on failed invocations | event_id as a message attribute |
| SNS → Settlement team | Push | Async | Decision notification (identifiers only) | Topic policy: subscriptions restricted to the settlement team's endpoints | Subscription DLQ | event_id as a message attribute |

**Risks:**
1. **Data leakage:** the Pipe must strip fare amounts before SNS. If its filter is misconfigured, money values leak into notifications.
2. **More pieces:** more SAM resources and IAM roles to review. Stream records expire after 24h if the Pipe stays broken.

**Cost driver:** SNS email deliveries dominate; EventBridge, Pipes and Streams add cents. About $1.60/month before free tier (see Cost justification). Uses 1 Lambda.

---

# Architecture Option 3: Direct async invoke + Lambda + DynamoDB

**Summary:** The simplest path. The producer invokes the Lambda asynchronously, and ordering is enforced in code with a per-PNR sequence check.

**Services in order:**
Producer → Lambda async invoke (on-failure destination: SQS DLQ) → DynamoDB (decisions table plus a per-PNR sequence item)

**Service Boundary Analysis:**

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role | Failure Path | Correlation ID |
|-----|-----------|------------|---------------|----------|--------------|----------------|
| Producer → Lambda | Push | Async (InvocationType=Event) | Cancellation event plus sequence_no per pnr | Producer role: `lambda:InvokeFunction` on the function ARN | 2 automatic retries, then on-failure destination: SQS DLQ | event_id in the payload |
| Lambda → DynamoDB (sequence check) | Push | Sync (UpdateItem, condition: sequence_no = last + 1) | pnr, sequence_no | Lambda role: `dynamodb:UpdateItem` on the table ARN | Condition fails → event arrived out of order → decision = MANUAL_REVIEW | event_id logged with pnr |
| Lambda → DynamoDB (decision) | Push | Sync (conditional PutItem) | Decision record: pnr, event_id, decision, refund_fare_amount, settlement_date | Lambda role: `dynamodb:PutItem` on the table ARN | Duplicate skipped; other errors raised so AWS retries the invoke | event_id as sort key |
| Lambda → SQS DLQ | Push | Async (Lambda destination) | Invocation record with the original event | Lambda role: `sqs:SendMessage` on the DLQ ARN | Alarm on DLQ depth | event_id in the request payload |
| DynamoDB → Settlement team | Pull | Sync (Query) | Decisions by settlement_date | Settlement read role: `dynamodb:Query` on the table and index ARNs | Query error is surfaced to the caller | event_id on every record |

**Risks:**
1. **Weak ordering:** order is only detected, not guaranteed. Out-of-order events go to manual review, which increases the settlement team's workload.
2. **Producer burden:** producers must generate a sequence_no for each PNR, and AWS retries an async invoke only twice.

**Cost driver:** Lambda invocations and DynamoDB writes (two per event: sequence check + decision). About $0.25/month before free tier (see Cost justification). Uses 1 Lambda.

---

# Comparison Table

| Option | Design | Keeps each PNR's events in order? | Cost |
|---|---|---|---|
| 1 (recommended) | SQS FIFO, Lambda, DynamoDB | Yes, guaranteed by the FIFO queue | ~$0.30/month |
| 2 | EventBridge, SQS FIFO, Lambda, DynamoDB, Pipes, SNS | Yes, guaranteed by the FIFO queue | ~$1.60/month |
| 3 | Lambda async invoke, DynamoDB, SQS DLQ | Partly: the Lambda detects events that arrive out of order and sends them to manual review | ~$0.25/month |

| Criterion | Option 1 (SQS FIFO) | Option 2 (EventBridge + Pipes) | Option 3 (Async invoke) |
|-----------|---------------------|--------------------------------|-------------------------|
| **Duplicate handling** | FIFO dedup + conditional write | FIFO dedup + conditional write | Conditional write only |
| **Settlement team sees decisions by** | Querying DynamoDB | SNS push notification | Querying DynamoDB |
| **Lambdas used** | 1 | 1 | 1 |
| **Number of hops** | 4 | 6 | 5 |
| **Complexity** | Low | Medium | Lowest infrastructure, highest code |
| **Failure recovery** | FIFO DLQ + replay in order | FIFO DLQ + Pipe DLQ | Destination DLQ + manual review |

**Recommendation:** Option 1 meets the per-PNR ordering requirement with the fewest parts. Choose Option 2 if the settlement team needs to be notified rather than query the table.

---

# Cost justification

**Assumptions:** 2,000 cancellations/day ≈ **60,000 events/month**. Lambda at 256 MB, ~200 ms per event. Decision item < 1 KB (1 write unit). ~3 log lines (~1 KB) per event. us-east-1 list prices (approximate; verify against the AWS Pricing Calculator before sign-off). Figures are **before** the AWS free tier, which would cover almost all of it.

| Component | Usage per month | Unit price (approx.) | Option 1 | Option 2 | Option 3 |
|---|---|---|---|---|---|
| SQS FIFO requests | 60,000 × 3 (send, receive, delete) = 180,000 | $0.50 per million | $0.09 | $0.09 | - |
| Lambda requests | 60,000 invocations | $0.20 per million | $0.01 | $0.01 | $0.01 |
| Lambda compute | 60,000 × 0.25 GB × 0.2 s = 3,000 GB-s | $0.0000167 per GB-s | $0.05 | $0.05 | $0.05 |
| DynamoDB on-demand writes | 60,000 (Option 3: 120,000, incl. sequence check) | $0.625 per million write units | $0.04 | $0.04 | $0.08 |
| DynamoDB storage | ~60,000 × 0.5 KB ≈ 30 MB, growing monthly | $0.25 per GB-month | $0.01 | $0.01 | $0.01 |
| CloudWatch Logs ingestion | ~180 MB | $0.50 per GB | $0.09 | $0.09 | $0.09 |
| EventBridge custom events | 60,000 | $1.00 per million | - | $0.06 | - |
| DynamoDB Streams reads (via Pipe) | ~60,000 | $0.02 per 100,000 | - | $0.01 | - |
| EventBridge Pipes | 60,000 requests | $0.40 per million | - | $0.02 | - |
| SNS email deliveries | 60,000 | $2.00 per 100,000 | - | $1.20 | - |
| SQS DLQ | Failures only | $0.40 per million | ~$0 | ~$0 | ~$0 |
| **Total (approx.)** | | | **~$0.30** | **~$1.60** | **~$0.25** |

**Why the totals differ:**
- **Option 1** is pure pay-per-request with nothing idle: one queue, one function, one table. Nothing costs money when no events arrive.
- **Option 2** costs ~5× more almost entirely because of **SNS email** (one email per decision, ~2,000 emails/day to the settlement team). Sending a daily digest, or delivering SNS to an SQS queue instead of email, brings it back to ~$0.40.
- **Option 3** removes SQS but doubles DynamoDB writes, so it lands slightly below Option 1. The real cost is people: every out-of-order event becomes a MANUAL_REVIEW that someone on the settlement team must handle.

**What would change the picture:** at 100× volume (200,000 events/day) Option 1 is still only ~$30/month. Cost is not the deciding factor between these options; ordering guarantee and operational effort are.
