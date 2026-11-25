# M1 architecture options - chandra

_model: us.anthropic.claude-haiku-4-5-20251001-v1:0 | tokens in/out: 413/1200_

## Prompt

## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

**Role:** You are designing for Accelya's settlement platform on AWS (region us-east-1).

**Input:** CSV file `ticket_issue_<airline>_<date>.csv`, ~5–20 MB (up to ~100k rows), columns: ...

**Arrival:** once per day per agency/airline pair, ~50–200 files/day, mostly between 00:00–06:00 UTC; occasional late re-uploads.

**Must:** original file kept unchanged for audit (immutable, versioned, encrypted); ticket count per airline_code visible to finance within 5 minutes of upload; duplicate/re-uploaded files must not double-count.

**Must not:** no servers or containers to patch; never log fare_amount, full rows or PNR lists — log only file identifiers and counts.

**Constraints:** serverless only; sandbox limits: max 10 Lambda functions, no public buckets.

**Output wanted:** two architecture options, answered against the six service-boundary questions for every hop.


## Bedrock answer

# Option 1: S3 → EventBridge → Lambda → DynamoDB → QuickSight

**Summary:** Immutable S3 versioning + EventBridge routing to deduplicating Lambda that writes aggregates to DynamoDB for finance dashboard.

**Services in order:**
1. S3 (ticket files, versioned, encrypted)
2. EventBridge (s3:PutObject rule)
3. Lambda (parse, deduplicate, aggregate)
4. DynamoDB (airline settlement counts)
5. QuickSight (finance dashboard)

---

## Hop 1: S3 → EventBridge

| Aspect | Answer |
|--------|--------|
| **Push/Pull** | Push (S3 event notification) |
| **Sync/Async** | Async |
| **Payload shape** | `{ "bucket": "ticket-files", "key": "ticket_issue_<airline>_<date>.csv", "version-id": "xyz..." }` |
| **IAM role** | S3 bucket policy allows `events.amazonaws.com` principal |
| **Failure path** | EventBridge DLQ → SNS → ops alert; S3 event retry (up to 2 hours) |
| **Correlation ID location** | `version-id` + `key` concatenated as `file_correlation_id` |

---

## Hop 2: EventBridge → Lambda

| Aspect | Answer |
|--------|--------|
| **Push/Pull** | Push (EventBridge invokes) |
| **Sync/Async** | Async (async invoke, no wait) |
| **Payload shape** | EventBridge rule passes S3 event detail; Lambda receives `{ "bucket", "key", "version-id" }` |
| **IAM role** | `EventBridgeInvokeLambdaRole` with `lambda:InvokeFunction` |
| **Failure path** | Lambda DLQ (SQS) → manual replay; CloudWatch Logs for debugging (no PNR/fare logged) |
| **Correlation ID location** | Passed in event detail; Lambda logs as `file_correlation_id` |

---

## Hop 3: Lambda → S3 (read ticket file)

| Aspect | Answer |
|--------|--------|
| **Push/Pull** | Pull (Lambda GetObject) |
| **Sync/Async** | Sync |
| **Payload shape** | CSV stream; Lambda reads up to 20 MB in memory |
| **IAM role** | `LambdaExecutionRole` with `s3:GetObject` + `s3:GetObjectVersion` |
| **Failure path** | Retry on transient error (3 attempts); permanent 404 → DLQ + alert |
| **Correlation ID location** | Embedded in Lambda context; logged per row count only |

---

## Hop 4: Lambda → DynamoDB (write dedup + aggregate)

| Aspect | Answer |
|--------|--------|
| **Push/Pull** | Push (Lambda PutItem/UpdateItem) |
| **Sync/Async** | Sync |
| **Payload shape** | `{ PK: "airline_code#date", SK: "file_correlation_id", ticket_count: N, timestamp, status: "processed" }` |
| **IAM role** | `LambdaExecutionRole` with `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` |
| **Failure path** | Conditional write fails if duplicate detected (file_correlation_id exists) → idempotent, no double-count |
| **Correlation ID location** | `file_correlation_id` is SK; enables dedup on re-upload |

---

## Hop 5: DynamoDB → QuickSight

| Aspect | Answer |
|--------|--------|
| **Push/Pull** | Pull (QuickSight dataset refresh) |
| **Sync/Async** | Async (scheduled refresh every 2 min) |
| **Payload shape** | DynamoDB table scanned; QuickSight SPICE ingests `{ airline_code, date, ticket_count, timestamp }` |
| **IAM role** | `QuickSightServiceRole` with `dynamodb:Scan`, `dynamodb:Query` |
| **Failure path** | Refresh fails → dashboard shows stale data; CloudWatch alarm on refresh latency |
| **Correlation ID location** | Not needed downstream; file_correlation_id retained in DynamoDB for audit trail |

---

**Risks:**
1. **Lambda timeout on large CSV:** 20 MB file with 100k rows may exceed 15-min timeout if parsing is slow; mitigation: stream parse, early exit on row limit.
2. **DynamoDB hot partition:** all daily uploads write to same `airline_code#date` partition; high throughput spikes (00:00–06:00) may throttle; mitigation: on-demand billing or pre-provisioned with auto-
