# HITL_REVIEW - m2 architecture comparison: ours (A) vs peer whiteboard (B)

**Reviewer:** `rohitb` (with a 5-judge AI review council: AWS facts, security, reliability, cost, airline business)
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-1 (architecture variants)`

Requirements: 2,000 cancellations/day (~60k/month), peak 50/min; order per PNR; settlement sees every decision;
no Kinesis/MSK/EC2; never log names, emails, fares, FFQ, PNR, account info.

**A (ours, revised):** Producer SendMessage -> SQS FIFO (`airline_code#pnr`, dedup `event_id`, DLQ) -> Lambda ->
DynamoDB transaction (event record + per-ticket current decision, version guard) -> Streams -> Pipes (allow-list) ->
settlement EventBridge bus (resource policy, archive).

**B (peer, whiteboard transcript; "?" = unclear):** API Gateway -> SQS FIFO (-> DLQ) -> Lambda -> DynamoDB Streams ->
EventBridge -> Firehose + S3 + Glue (Athena), CloudWatch Alarms, SNS. Notes: "ACU" (Aurora?), "D.S", "2000", "60K/m", cost ~$2?

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim           | A   | B   | Evidence                                                                                  |
|---------------|-----|-----|-------------------------------------------------------------------------------------------|
| Correctness   | 2   | 1   | A: right final decision per ticket; settlement misses INVALID/superseded events. B: no version guard or idempotency -> wrong final after DLQ redrive or out-of-order correction; Streams -> EventBridge hop not drawn |
| Security      | 2   | 0-1 | A: no public ingress, allow-list egress. B: public API Gateway, no auth drawn; Firehose -> S3 -> Athena stores PII |
| Observability | 2   | 2   | A: alarms, no notify path. B: SNS + queryable Athena audit trail                          |
| Scalability   | 3   | 2   | 50/min trivial. B: possible single message group; Aurora resume ~15 s                     |
| Cost          | 2-3 | 2 (0 with Aurora) | A: $2.50-4.50/month (~$0.85 with Lambda stream consumer). B: $0.50-7; $45-90 with Aurora (0.5 ACU floor + VPC) |
| **TOTAL**     | **11-12/15** | **6-8/15** |                                                                       |

**Verdict:** A wins on core correctness, security and cost; B wins on operability. Present A + three B ideas.

---

## Adopt from B

| # | Idea | How | Cost |
|---|------|-----|------|
| 1 | SNS alerts | EventBridge rule on MANUAL_REVIEW decisions + alarms -> SNS, identifiers only | ~$0 |
| 2 | Audit and reporting store | EventBridge rule -> Firehose -> S3 (GZIP, SSE-KMS, lifecycle expiry) -> Glue table with partition projection (no crawler) -> Athena; PII stripped first | ~$0.05-0.15/month |
| 3 | Explicit DLQ | FIFO DLQ on the queue with depth alarm, shown on the diagram | ~$0 |

API Gateway only if the source is an external HTTPS system: HTTP API, IAM or JWT auth, map `MessageGroupId` from the body.

## Gaps still open in A

| # | Failure | Fix |
|---|---------|-----|
| 1 | Streams filtered to ticket items -> INVALID, hash-conflict and superseded events never reach settlement | Decision-log item per event (CURRENT / SUPERSEDED / INVALID), streamed |
| 2 | Version guard is wrong if `seq` is arrival order | `seq` assigned at source (DCS/producer); settlement applies `seq` (EventBridge is unordered) |
| 3 | EventBridge rule -> SQS FIFO may only set a static `MessageGroupId` -> one group for all events | Direct SendMessage until verified |
| 4 | PNR stored in clear in group key and DynamoDB; `pnr` still in logs | Salted HMAC key; drop `pnr` from logs |
| 5 | Transaction writes 2 items = 4 WRU per event | Accept (~$0.15/month) |

## Mentor line

> Both designs use SQS FIFO, Lambda, DynamoDB and EventBridge. Ours adds a per-ticket version guard and idempotency, so
> settlement always ends on the right refund; the peer's adds SNS alerts and an Athena audit trail, which we merge in.
> Aurora is not needed at 2,000/day ($45-90/month vs ~$3).

---

Not verified: B judged from a whiteboard with assumptions (auth, idempotency, group key) - confirm with the peer.
AWS facts from memory - check EventBridge SQS-target `MessageGroupId` and the Pipes stream DLQ payload in the AWS docs.

*Pass bar: ≥12/15 AND every dim ≥2.*
