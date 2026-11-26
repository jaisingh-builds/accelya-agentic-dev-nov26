# Peer comparison: amol.more vs rohitb — M2 cancellation architecture

**Date:** 2026-11-25

## Side-by-side

| Aspect | amol.more | rohitb |
|--------|-----------|--------|
| **Selected option** | C: SQS FIFO + Streams | C: SQS FIFO + Streams (same) |
| **Entry point** | API Gateway (future) -> SQS FIFO | Cross-account role -> SQS FIFO |
| **MessageGroupId** | PNR | airline_code#PNR |
| **DedupId** | event_id | event_id (same) |
| **Lambda count** | 2 (decide + publish) | 1 (decide only; Pipes replaces publish Lambda) |
| **DynamoDB write** | PutItem + condition `attribute_not_exists(event_id)` | TransactWriteItems: DECISION + TICKET items atomically |
| **Version guard (seq)** | No | Yes — TICKET item tracks `current_seq`, stale events stored as SUPERSEDED |
| **Payload hash check** | No | Yes — same event_id + different hash = MANUAL_REVIEW |
| **Streams consumer** | EventBridge -> Lambda publish -> SQS settlement | EventBridge Pipes (allow-list transform) -> EventBridge bus -> multiple targets |
| **Settlement delivery** | Lambda publish -> SQS settlement-feed | EventBridge rule -> settlement target (with rule-target DLQ) |
| **Audit/analytics** | Firehose -> S3 + Glue | Firehose -> S3 + Glue + Athena (same pattern) |
| **Alerting** | CloudWatch Alarms -> SNS | CloudWatch Alarms -> SNS (same) + MANUAL_REVIEW/INVALID routed via EventBridge rule |
| **EventBridge archive** | No | Yes (30-day archive for replay) |
| **Encryption** | AWS-managed keys | Customer-managed KMS key (all services) |
| **DLQ maxReceiveCount** | 3 | 5 |
| **FIFO ordering in handler** | Yes — `failed_groups` set enforced | Mentioned (stop-at-first-failure) but noted as not yet in handler code |
| **Handler code** | Yes — working, 6/6 tests pass, 6 defects fixed | No handler code — design only |
| **Logging verified** | Yes — `check_logs.py` 11 records, 0 violations | Referenced but not machine-verified |
| **Cost estimate** | ~$2.50-2.90/month | ~$3-5.50/month (includes $1 KMS key) |
| **HITL score** | 12/15 (PASS) | 14/15 (PASS) |

## What Rohit has that we don't (consider adopting)

| # | Feature | Why it matters | Effort to add |
|---|---------|----------------|---------------|
| 1 | **`seq`-based version guard** | A DLQ redrive of an older event after a correction has arrived would override the correction in our design. Rohit's TICKET item with `current_seq` prevents this | Medium — add a TICKET item to DynamoDB, change PutItem to TransactWriteItems |
| 2 | **Payload hash check** | Same `event_id` with different content = data corruption or replay mutation. We silently no-op (conditional put). Rohit detects and routes to MANUAL_REVIEW | Low — hash the body, store on DECISION item, compare on conditional put failure |
| 3 | **EventBridge Pipes** (instead of publish Lambda) | Eliminates the second Lambda entirely. Pipes polls Streams and routes to EventBridge bus with an allow-list transform — fewer moving parts, no code to maintain | Medium — replace Lambda publish with Pipes configuration in SAM |
| 4 | **EventBridge archive** (30-day) | Enables replay if settlement misses events. We have no replay mechanism | Low — one property in SAM template |
| 5 | **Allow-list transform** | Settlement and audit only see approved fields. Our Lambda publish would need to explicitly strip fields | Included if we adopt Pipes |
| 6 | **Customer-managed KMS key** | Single key across all services; better for compliance audit trail | Low — but adds $1/month |

## What we have that Rohit doesn't

| # | Feature | Why it matters |
|---|---------|----------------|
| 1 | **Working handler code** | 6/6 test harness checks pass. Rohit's is design-only, not implemented |
| 2 | **FIFO ordering enforced in code** | `failed_groups` set actually stops later records in same PNR group. Rohit notes the need but hasn't built it |
| 3 | **6 critical defects found and fixed** | InvalidOperation crash, truthy bug, SERVICE naming, batch correlation_id — all caught by AGENTS.md review + Council of 7 Judges |
| 4 | **Machine-verified logging** | `check_logs.py` run, 11 records, 0 violations. Rohit claims conformance but hasn't verified |
| 5 | **Bedrock cost analysis** | ~$0.02 per architecture generation call, token budget guard, usage logging |

## Review points for discussion

1. **GroupId: `PNR` vs `airline_code#PNR`** — Rohit uses `airline_code#pnr` which further isolates processing per airline. If two airlines share a codeshare PNR, this prevents cross-airline blocking. Worth adopting?

2. **maxReceiveCount: 3 vs 5** — We use 3 (DLQ faster, less retry noise). Rohit uses 5 (more chances for transient errors). The right answer depends on how often DynamoDB throttles at peak.

3. **TransactWriteItems vs PutItem** — Rohit's atomic DECISION + TICKET write is more correct for the correction scenario. Our plain PutItem works for the happy path but doesn't handle `seq`-based corrections. If we add the version guard, we need the transaction.

4. **Pipes billing** — Rohit notes the wide cost range ($1.50-4.00 for Pipes). Our Lambda publish approach is cheaper (~$0.02/month) but requires maintaining code. Trade-off: cost vs operational simplicity.

5. **Service name inconsistency in Rohit's doc** — Section 1 says `accelya-m2-rohitb-*` but section 8 says `accelya-m2-cancellation` (missing learner). Same bug we fixed (D3).
