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

**A v2** (`day2/outputs/m2-arch-option-A-v2.md`) — **retracted.** An earlier note here projected 12–13/15 PASS. A critical review found three correctness bugs (dedup on `ticket_number` drops legitimate events; SNS failure after the put loses the decision; DLQ breaks per-`pnr` order) and AGENTS.md misses (EMF keys outside the logging contract, raw exception logging, no resource names, missing hops). Do not rely on that score.

**A v3** (`day2/outputs/m2-arch-option-A-v3.md`, AI draft, design only, needs human review):

| Dim | Score | Evidence |
|---|---|---|
| Correctness | 2 | Idempotent on `(pnr, event_id)`, PENDING→PUBLISHED status, double-refund and out-of-order guards; two guards are new rules needing business sign-off; nothing tested |
| Security | 3 | ARN-scoped named actions, queue policy with TLS deny, encryption on all stores, data minimisation (no names or emails) |
| Observability | 2 | Contract-conformant `json.dumps` logs, classified errors, metric filters and alarms; no Powertools/EMF until Day 4 |
| Scalability | 2 | Idempotent, bounded, `MaximumConcurrency` set; 10× and fault-injection tests planned, **not run** |
| Cost | 2 | Workload-appropriate, cost per record estimated and measurable; no sampling discipline |
| **TOTAL** | **11/15** | **REVISE** (pass bar is 12/15) |

To reach PASS: run the 10× and fault-injection tests and record the result (Scalability 3, total 12/15). Also resolve the open decisions in section 11 of the v3 doc.

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
