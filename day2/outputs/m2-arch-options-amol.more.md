# Architecture options (3) — M2 cancellation refund decision

Learner: amol.more
Model: us.anthropic.claude-haiku-4-5-20251001-v1:0

# Accelya M2 Refund Decision Architecture – Three Options

---

## OPTION 1: SQS → Lambda → DynamoDB → EventBridge → SNS

**Summary:** Ordered queue-driven Lambda processor with DynamoDB state, EventBridge fan-out to settlement dashboards.

### Service Flow
1. **SQS** (FIFO queue) ← cancellation events
2. **Lambda** (refund-decision processor) → rules engine
3. **DynamoDB** (decisions table) ← write decision + audit
4. **EventBridge** (custom events) ← decision events
5. **SNS** (settlement topics) → settlement team notifications

### Service-Boundary Analysis

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role & Actions | Failure Path | Correlation ID |
|-----|-----------|-----------|---|---|---|---|
| **SQS → Lambda** | Pull | Async | `{event_id, pnr, fare_class, departure_status, no_show_flag, fare_amount}` (JSON) | `lambda:InvokeFunction` on FIFO queue ARN; `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility` on queue ARN | DLQ after 3 retries; Lambda timeout → visibility reset → retry | `event_id` (passed in payload) |
| **Lambda → DynamoDB** | Push | Sync | `{event_id, pnr, decision, refund_pct, refund_amount, rule_matched, timestamp}` | `dynamodb:PutItem` on decisions table ARN; `dynamodb:Query` on GSI (pnr-index) for ordering check | Write failure → Lambda exception → SQS retry; CloudWatch alarm | `event_id` (partition key) |
| **Lambda → EventBridge** | Push | Async | `{detail: {event_id, pnr, decision, rule_matched}, source: "accelya.refund", detail-type: "RefundDecision"}` | `events:PutEvents` on EventBridge bus ARN | Event loss if bus is throttled; no retry in Lambda code → manual replay needed | `event_id` (in detail) |
| **EventBridge → SNS** | Push | Async | Same as EventBridge detail | EventBridge rule has `events:PutEvents` + SNS topic has `sns:Publish` (cross-service) | SNS delivery failure → EventBridge DLQ; settlement team misses notification | `event_id` (propagated) |
| **SNS → Settlement Dashboard** | Pull | Async | SNS message (JSON) wrapped in SNS envelope | Dashboard IAM role: `sns:Subscribe` + `sqs:ReceiveMessage` if using SQS subscription | Message not consumed → stays in SQS; manual query of DynamoDB table | `event_id` (in message) |

### Risks
1. **Ordering guarantee fragile:** SQS FIFO guarantees per-group (PNR), but if Lambda crashes mid-execution, visibility timeout resets and another Lambda instance may process a later event first. Mitigation: idempotency key in DynamoDB + conditional writes.
2. **EventBridge → SNS async gap:** If EventBridge rule fails to invoke SNS, settlement team sees no real-time alert. Mitigation: DLQ on EventBridge rule + Lambda poller to replay failed events.

### Cost Driver
- **Lambda invocations:** 2,000/day baseline + 50/min peaks = ~100k invocations/month (~$2).
- **DynamoDB:** on-demand writes (2k/day) + queries for ordering checks (~$1–2/month).
- **EventBridge + SNS:** negligible (<$1/month).
- **Total:** ~$3–5/month + data transfer.

---

## OPTION 2: SQS → Lambda → Aurora PostgreSQL → RDS Proxy → Kinesis Data Firehose → S3 + CloudWatch Logs Insights

**Summary:** Relational database for transactional ordering guarantees; Firehose for audit trail to S3; CloudWatch for settlement team queries.

### Service Flow
1. **SQS** (FIFO queue) ← cancellation events
2. **Lambda** (refund-decision processor) → rules engine
3. **RDS Proxy** (connection pooling) ↔ Aurora PostgreSQL (decisions table + audit log)
4. **Kinesis Data Firehose** ← decision events (async write)
5. **S3** (audit bucket) ← Firehose delivery
6. **CloudWatch Logs Insights** ← settlement team queries

### Service-Boundary Analysis

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role & Actions | Failure Path | Correlation ID |
|-----|-----------|-----------|---|---|---|---|
| **SQS → Lambda** | Pull | Async | `{event_id, pnr, fare_class, departure_status, no_show_flag, fare_amount}` (JSON) | `lambda:InvokeFunction` on FIFO queue ARN; `sqs:ReceiveMessage`, `sqs:DeleteMessage` on queue ARN | DLQ after 3 retries; Lambda timeout → SQS retry | `event_id` |
| **Lambda → RDS Proxy** | Push | Sync | SQL INSERT: `INSERT INTO decisions (event_id, pnr, decision, refund_pct, refund_amount, rule_matched, ts) VALUES (...)` with PNR ordering check via `SELECT MAX(created_at) FROM decisions WHERE pnr = ?` | `rds-db:connect` on RDS Proxy endpoint ARN; `rds:DescribeDBInstances` for endpoint discovery | Connection pool exhaustion → Lambda timeout → SQS retry; DB constraint violation (duplicate event_id) → rollback + MANUAL_REVIEW fallback | `event_id` (in SQL) |
| **Lambda → Firehose** | Push | Async | `{event_id, pnr, decision, refund_pct, rule_matched, timestamp}` (no fare_amount per rules) | `firehose:PutRecord` on Firehose stream ARN | Firehose buffer full → async retry; if persistent, events dropped after TTL → CloudWatch alarm | `event_id` (in record) |
| **Firehose → S3** | Push | Async | Parquet or JSON lines (batched, compressed) | Firehose IAM role: `s3:PutObject` on audit bucket ARN + `s3:GetBucketVersioning` | S3 write failure (quota, permissions) → Firehose DLQ (if configured) or data loss | `event_id` (in object) |
| **Settlement Team → CloudWatch Logs Insights** | Pull | Sync | CloudWatch Logs query: `fields @timestamp, event_id, pnr, decision, refund_pct \| stats count() by decision` | IAM role: `logs:StartQuery`, `logs:GetQueryResults` on log group ARN | Query timeout (>15 min) → partial results; no real-time dashboard | `event_id` (in log) |

### Risks
1. **RDS Proxy connection pool contention:** At 50/min peaks, Lambda concurrency may exceed pool size (default 100 connections). Mitigation: scale RDS Proxy max connections; use connection multiplexing.
2. **Audit trail lag:** S3 delivery via Firehose has 1–5 min latency (buffer flush). Settlement team sees stale data in Logs Insights. Mitigation: dual-write to CloudWatch Logs (synchronous) for real-time, S3 for compliance.

### Cost Driver
- **Lambda:** ~$2/month (same as Option 1).
- **RDS Proxy + Aurora:** ~$50–100/month (minimum 2 AZ, 1 ACU = 2 GB RAM).
- **Firehose + S3:** ~$5–10/month (2k records/day, 1 MB/day).
- **CloudWatch Logs:** ~$10–20/month (if dual-writing logs).
- **Total:** ~$70–150/month.

---

## OPTION 3: SQS → Lambda → DynamoDB Streams → Lambda (async processor) → DynamoDB (audit table) + SNS

**Summary:** Event-sourcing pattern with DynamoDB Streams; Lambda-driven async audit trail; minimal external dependencies.

### Service Flow
1. **SQS** (FIFO queue) ← cancellation events
2. **Lambda** (sync refund-decision processor) → rules engine
3. **DynamoDB** (decisions table) ← write decision
4. **DynamoDB Streams** (NEW_IMAGE) → Lambda trigger
5. **Lambda** (async audit processor) → enrichment & compliance
6. **DynamoDB** (audit table) ← write audit record
7. **SNS** (settlement notifications) ← async Lambda invocation

### Service-Boundary Analysis

| Hop | Push/Pull | Sync/Async | Payload Shape | IAM Role & Actions | Failure Path | Correlation ID |
|-----|-----------|-----------|---|---|---|---|
| **SQS → Lambda (sync)** | Pull | Async | `{event_id, pnr, fare_class, departure_status, no_show_flag, fare_amount}` (JSON) | `lambda:InvokeFunction` on FIFO queue ARN; `sqs:ReceiveMessage`, `sqs:DeleteMessage` on queue ARN | DLQ after 3 retries; Lambda timeout → SQS retry | `event_id` |
| **Lambda (sync) → DynamoDB (decisions)** | Push | Sync | `{event_id (PK), pnr (SK), decision, refund_pct, refund_amount, rule_matched, timestamp}` | `dynamodb:PutItem` on decisions table ARN; `dynamodb:Query` on GSI (pnr-timestamp) for ordering | Write failure → Lambda exception → SQS retry; conditional write (event_id already exists) → idempotent NOOP | `event_id` (PK) |
| **DynamoDB Streams → Lambda (async)** | Pull | Async | Stream record: `{eventID, dynamodb: {NewImage: {...}}, eventName: "INSERT"}` | `lambda:InvokeFunction` on async Lambda ARN; `dynamodb:GetRecords`, `dynamodb:GetShardIterator` on stream ARN | Stream record loss if Lambda fails → DLQ on async Lambda; Streams retention 24h → replay window | `event_id` (in NewImage) |
| **Lambda (async) → DynamoDB (audit)** | Push | Async | `{audit_id (PK), event_id (GSI), pnr, decision, rule_matched, processor_version, timestamp}` | `dynamodb:PutItem` on audit table ARN | Write failure (throttle, capacity) → async Lambda retry (built-in 2x); after max retries → DLQ or discard | `event_id` (GSI) |
| **Lambda (async) → SNS** | Push | Async | `{event_id, pnr, decision, rule_matched}` (no fare_amount) | `sns:Publish` on settlement topic ARN | SNS publish failure → async Lambda retry; after max retries → CloudWatch alarm | `event_id` (in message) |
| **SNS → Settlement Dashboard** | Pull | Async | SNS message (JSON) | Dashboard IAM role: `sns:Subscribe` + `sqs:ReceiveMessage` (if SQS subscription) | Message not consumed → SQS DLQ; manual query of audit table | `event_id` |

### Risks
1. **DynamoDB Streams processing lag:** At 50/min peaks, async Lambda may lag 30–60 sec behind decision write. Settlement team sees delayed audit trail. Mitigation: CloudWatch metric on stream iterator age; scale async Lambda concurrency.
2. **Duplicate audit records:** If async Lambda retries after partial success, audit table may have duplicates. Mitigation: use `event_id` as idempotency key; conditional write with `attribute_not_exists(event_id)`.

### Cost Driver
- **Lambda (sync + async):** ~$5/month (2k sync + 2k async invocations/day).
- **DynamoDB:** on-demand writes (4k/day: 2k decisions + 2k audits) + Streams read capacity (~$3–5/month).
- **SNS:** negligible (<$1/month).
- **Total:** ~$8–10/month.

---

## COMPARISON TABLE

| Aspect | Option 1 (SQS→Lambda→DDB→EB→SNS) | Option 2 (SQS→Lambda→Aurora→Firehose→S3) | Option 3 (SQS→Lambda→DDB Streams→Lambda→DDB+SNS) |
|--------|---|---|---|
| **Ordering Guarantee** | Per-PNR (SQS FIFO) + idempotency in DDB | Per-PNR (SQS FIFO) + DB constraints | Per-PNR (SQS FIFO) + idempotency in DDB |
| **Real-Time Settlement Visibility** | ✅ EventBridge → SNS (sub-second) | ⚠️ CloudWatch Logs Insights (1–5 min lag) | ✅ SNS (1–2 sec lag via Streams) |
| **Audit Trail** | ✅ DDB (queryable, TTL-able) | ✅ S3 (immutable, compliance-grade) | ✅ DDB audit table (queryable, TTL-able) |
| **Operational Complexity** | Low (3 services) | Medium (RDS Proxy + Aurora ops) | Low (4 services, all serverless) |
| **Cost** | ~$3–5/month | ~$70–150/month | ~$8–10/month |
| **Failure Recovery** | Manual replay from SQS DLQ | Manual replay from SQS DLQ | Automatic via Streams (24h window) |
| **Scaling at 50/min Peaks** | ✅ Lambda auto-scales; SQS FIFO throughput 300/sec | ⚠️ RDS Proxy pool may bottleneck | ✅ Lambda + Streams auto-scale |
| **Compliance (no PII/amounts)** | ✅ Logging rules enforced in Lambda | ✅ Firehose can filter; S3 encryption | ✅ Logging rules enforced in Lambda |
| **Correlation Tracing** | `event_id` in all hops | `event_id` in SQL + Firehose | `event_id` in all hops |

---

## RECOMMENDATION

**Choose Option 3** for Accelya:
- **Lowest cost** (~$8–10/month) with no managed database overhead.
- **Fastest real-time visibility** (SNS sub-2-sec) for settlement team.
- **Automatic failure recovery** via DynamoDB Streams 24h retention.
- **Simplest ops:** all serverless, no connection pooling or RDS patching.
- **Ordering guarantee:** SQS FIFO + idempotency keys handle re-bookings safely.

**Fallback to Option 1** if:
- Settlement team demands <500ms latency (EventBridge is faster than Streams polling).
- Audit trail must be queryable in real-time (DDB on-demand faster than S3).

**Avoid Option 2** unless:
- Compliance mandates immutable S3 audit trail (use Option 3 + async S3 export).
- Multi-tenant isolation requires relational schema (not needed here).