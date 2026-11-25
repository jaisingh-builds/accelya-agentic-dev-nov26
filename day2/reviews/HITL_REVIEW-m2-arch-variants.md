# HITL_REVIEW — m2-arch-variants

**Artefact:** `day2/prompts/m2-arch-variants.md` (three architecture options generated from it)
**Reviewer:** `<your-github-handle>`
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `<lab-N>`

> Draft scores prepared by the AI assistant, scored as designs (no code or tests exist for them yet).
> The human reviewer must confirm or change every score.

**Options reviewed**
- **A** — SQS FIFO (`MessageGroupId=pnr`, dedup on `ticket_number`) → Lambda → DynamoDB + SNS
- **B** — API Gateway → Lambda → DynamoDB (events) → DynamoDB Streams → Lambda → DynamoDB + SNS
- **C** — SQS FIFO → Lambda → EventBridge decision bus → settlement SQS / Step Functions / Firehose→S3

---

## The score (0–3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | A | B | C |
|----------------|---|---|---|
| Correctness    | 2 | 1 | 2 |
| Security       | 2 | 2 | 2 |
| Observability  | 2 | 2 | 2 |
| Scalability    | 2 | 1 | 1 |
| Cost           | 2 | 2 | 1 |
| **TOTAL**      | **10/15** | **8/15** | **8/15** |

**Logging contract (from Day 2, mandatory):** not run — these are designs, not code, so there is no log to check.
*A non-conformant artefact scores at most 1 on Observability.*

**Verdict:** A — REVISE · B — REVISE · C — REVISE

---

## Per-dimension detail

### Correctness
- **A (2/3)** — Meets strict order per `pnr` (message group) and per-`ticket_number` dedup; names the 5-minute FIFO dedup gap and covers it with a conditional write. No edge cases tested.
- **B (1/3)** — DynamoDB Streams orders per item key only; two events for one `pnr` need a sequence scheme that is not defined.
- **C (2/3)** — Order holds at intake; downstream consumers may see decisions out of order and must use a sequence field.

### Security
- **A, B, C (2/3 each)** — ARN-scoped, named-action IAM on every hop, no wildcards. No Conditions specified and PII fields not tagged.

### Observability
- **A, B, C (2/3 each)** — `correlation_id` carried on every hop, classified errors. No EMF metrics or Powertools specified.

### Scalability
- **A (2/3)** — Idempotent via conditional write on `ticket_number`, bounded memory. Not tested at 10× load.
- **B (1/3)** — Idempotent, but a stream retry blocks the shard and the `pnr` partition can get hot.
- **C (1/3)** — Idempotency is pushed to every consumer; extra parts are speculative abstraction at 1,000 events/day.

### Cost
- **A (2/3)** — Workload-appropriate pay-per-use; cost driver named (SQS FIFO requests, Lambda). No cost-per-record measure.
- **B (2/3)** — Workload-appropriate, but each event is written twice and API Gateway adds per-request cost.
- **C (1/3)** — EventBridge, Step Functions and Firehose paid for without a current need.

---

## If REVISE: what to change

1. **Observability (A)** — Specify Powertools Logger plus EMF metrics (+1).
2. **Scalability (A)** — Add a 10× load test, 500 events/min (+1).
3. **Cost (A)** — Add a cost-per-record figure (+1).

With these A reaches 13/15 and PASSES. B and C need a redesign, not just additions (B: define per-`pnr` sequencing; C: drop services not needed yet).

**Recommendation:** proceed with Option A.

**Next iteration prompt addition:** `Use Powertools Logger with EMF metrics, state the idempotency key (ticket_number conditional write), include a 10x load test plan and a cost-per-record estimate.`

---

*Pass bar: ≥12/15 AND every dim ≥2.*
