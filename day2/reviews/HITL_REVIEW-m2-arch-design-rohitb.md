# HITL_REVIEW - m2 cancellation architecture (trimmed Option 1)

**Artefact:** design - SQS FIFO -> Lambda -> DynamoDB -> Streams -> EventBridge Pipes -> settlement bus
(derived from Option 1 of `day2/outputs/m2-arch-variants-rohitb.md`)
**Reviewer:** `rohitb` (with a 5-judge AI review council: AWS facts, security, reliability, cost, airline business)
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-1 (architecture variants)`

Requirements: 2,000 cancellations/day, peak 50/min; order per PNR; settlement sees every decision;
no Kinesis/MSK/EC2; never log names, emails, fares, FFQ, PNR, account info.

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                                                              |
|----------------|-------|------------------------------------------------------------------------------------------|
| Correctness    | 2     | FIFO `MessageGroupId=pnr` keeps order; but no current decision per ticket, and Hop 0 (Pipes from an EventBridge bus) is not possible |
| Security       | 2     | No public endpoint, one role per hop; full DynamoDB image (PNR, amount) reaches the settlement bus; no bus resource policy |
| Observability  | 2     | One correlation_id end to end, contract logs; no alarms on DLQ, IteratorAge, Pipe failures |
| Scalability    | 2     | FIFO handles the peak, DLQ after 3; a poison message pushes other PNRs' messages to the DLQ; DLQ redrive can override a later correction |
| Cost           | 3     | ~$2.50-4.50/month for 60k events (~$0.85 with a stream-triggered Lambda); cheapest option meeting the requirements |
| **TOTAL**      | 11/15 |                                                                                          |

**Logging contract:** design artefact, no logs yet. Handler reviewed separately (`HITL_REVIEW-m2-cancellation-rohitb.md`).

**Verdict:** REVISE

---

## Findings

| # | Area | Failure | Fix |
|---|------|---------|-----|
| 1 | Correctness | evt-1002 APPROVE and evt-1003 REJECT are separate items; settlement can receive them in any order -> wrong or double refund | Per-ticket current-decision item with a version guard (`seq > stored_seq`); stream only that item |
| 2 | Correctness | Pipes cannot read an EventBridge bus | Producer sends to SQS FIFO, or EventBridge rule + input transformer -> SQS |
| 3 | Scalability | Original in DLQ unblocks the PNR; later redrive overrides the correction | Same version guard; hold the group while it has a DLQ message |
| 4 | Scalability | Mixed-group batch: returning all later messages sends healthy PNRs to the DLQ | Malformed -> terminal INVALID record; small batches; `maxReceiveCount` ≥5 |
| 5 | Correctness | Same `event_id` with new content after the 5-min dedup window is silently ignored | Store a payload hash; mismatch -> ERROR + MANUAL_REVIEW |
| 6 | Security | Stream image puts PNR and amount on the settlement bus | Pipes input transformer with an allow-list of fields |
| 7 | Security | Anyone could publish cancellations to the ingest bus | Bus resource policy: producer account + `source` only |
| 8 | Observability | "Guaranteed" notification is false: stream 24 h, Pipe and rule retries run out | Pipe DLQ + retry limit, rule-target DLQ, bus archive, alarms, reconciliation job |
| 9 | Security | No retention or TTL; `pnr` allowed by the logging contract | DynamoDB TTL, log retention, drop `pnr` from logs |

Out of scope (real, beyond `refund_rules.json`): involuntary refunds, coupon-level refunds, taxes/YQ/YR,
voids and exchanges, BSP routing, First class, per-currency decimals.

Unverified (check AWS docs): EventBridge rule dynamic `MessageGroupId`; Pipes SQS target message attributes;
default Pipe retry for stream sources; whether Pipes stream polling is billed.

---

## If REVISE: what to change

1. **Correctness** - add the per-ticket current-decision record with a version guard (findings 1, 3).
2. **Correctness** - redesign Hop 0 without Pipes-from-bus (finding 2).
3. **Security** - allow-list transform to settlement, bus resource policy (findings 6, 7).

**Next iteration prompt addition:**
> Keep one current decision per ticket_number with a version that only moves forward, and send settlement only that record. Do not use EventBridge Pipes with an EventBridge bus as the source. Settlement events carry only event_id, ticket_number, decision, refund_rule_id, refund_amount and correlation_id.

---

*Pass bar: ≥12/15 AND every dim ≥2.*
