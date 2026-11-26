# HITL_REVIEW - m2 cancellation architecture (peer, Option C)

---

**Artefact:** peer's `m2-cancellation-architecture-<peer>.md` (Option C: SQS FIFO + DynamoDB Streams), received 2026-11-25
**Reviewer:** `rohitb`
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `lab-1 (architecture) + lab-3 (handler section)`

Line numbers (L) refer to the artefact file.

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                                                                 |
|----------------|-------|---------------------------------------------------------------------------------------------|
| Correctness    | 1     | Streams -> EventBridge hop has no component; IAM action on H4 is wrong; row lacks `ticket_number` used downstream; no latest decision per ticket |
| Security       | 2     | No public ingress, per-hop ARN-scoped roles, no PII in logs; no bus resource policy, API Gateway auth unspecified |
| Observability  | 2     | correlation_id end to end, classified ERRORs, DLQ/IteratorAge alarms -> SNS; alarms reference a Pipe/consumer that is not in the design |
| Scalability    | 2     | FIFO per PNR + failed-group skip is right; DLQ unblocks the group after 3 receives and a later redrive can override a correction |
| Cost           | 2     | Serverless, pay-per-use, ~$3/month; extra forwarding Lambda, and the $2.10 stream-polling line contradicts its own design |
| **TOTAL**      | 9/15  |                                                                                             |

**Logging contract (from Day 2, mandatory):** design artefact. The handler claims 6/6 harness checks and 0 contract
violations (L127); not re-run by this reviewer (handler code not provided).

**Verdict:** REVISE

---

## Per-dimension detail

### Correctness - `1/3`

**Anchors:** 3 spec + edge cases tested · 2 spec, happy path only · 1 spec but missing field or wrong API · 0 hallucinated APIs

**Evidence:**
- **No stream consumer (L34-36, L61).** DynamoDB Streams cannot deliver to EventBridge by itself; it needs EventBridge
  Pipes or a Lambda. L61 describes Lambda event-source-mapping behaviour (bisect, on-failure destination) but no such
  function is drawn, and L126 alarms on "Pipe" failures that do not exist.
- **Wrong IAM on H4 (L62).** `events:PutEvents` belongs to whoever writes to the bus (the missing stream consumer), not to
  the rule -> `publish` Lambda hop. The rule needs a Lambda resource policy; `publish` needs only `sqs:SendMessage`.
- **Missing field (L60 vs L62).** The DynamoDB row has no `ticket_number`, yet H4 sends `ticket_number` to settlement.
  The stream's new image cannot provide it.
- **No latest decision per ticket.** Rows are keyed by `event_id` only. A no-show correction (REJECT) and the original
  APPROVE are two rows; `settlement-feed` (L37) gives no order guarantee, so settlement can end on APPROVE and pay a
  refund that should be rejected.
- **Requirements inconsistent (L11 vs L129).** Peak "~60K/hour" (≈1,000/min) contradicts the course requirement
  (2,000/day, 50/min) and its own cost basis of ~60K events/month.
- **Ordering example (L12)** covers re-booking vs cancellation but not the correction case (later event for the same ticket).
- **Not 2:** a component is missing and an API/role is wrong. **Not 0:** every service named is real and the core FIFO
  design works on the happy path.

### Security - `2/3`

**Anchors:** 3 ARN-scoped + named verbs + Conditions; PII tagged · 2 no wildcards; PII not addressed · 1 some wildcards · 0 `*`

**Evidence:**
- Good: no public endpoint (L112), producer role pinned to principal + account (L58, L112), one role per hop, no
  wildcards (L114), PII never logged (L113), S3 Block Public Access (L116).
- Gaps: no resource policy on the EventBridge bus (who may put events); API Gateway "future ingress" (L24) has no auth
  design; raw PNR used as `MessageGroupId` (L47) and stored; encryption key for Firehose/Glue/Athena results not stated.
- **Not 3:** no IAM Conditions (e.g. `aws:SourceArn` on service trust policies), PII not tagged. **Not 1:** no wildcards.

### Observability - `2/3`

**Anchors:** 3 Powertools + correlation_id + EMF + classified errors · 2 JSON logs + classified errors · 1 JSON logs only · 0 print / silent except

**Evidence:**
- Good: contract-conformant JSON with required keys (L120-123), classified `error_type` (L123), correlation_id from SQS
  through DynamoDB to settlement and S3 (L124), per-invocation id on batch lines (L95, L125), alarms -> SNS (L41, L107).
- Gaps: IteratorAge and "Pipe/rule failures" alarms (L126) point at a stream consumer that is not designed; no
  reconciliation between DynamoDB and settlement.
- **Not 3:** no metrics (EMF) yet. **Not 1:** errors are classified.

### Scalability - `2/3`

**Anchors:** 3 idempotent + bounded + tested at 10x · 2 idempotent + bounded · 1 idempotent only · 0 unbounded / non-idempotent / over-abstracted

**Evidence:**
- Good: FIFO `MessageGroupId = PNR` (L47); permanent conditional put on `event_id` (L60, L103); `failed_groups` skips only
  the failed PNR's later records (L93, L106) so other PNRs keep flowing; visibility timeout ≥ 6x function timeout (L59).
- Gaps: `maxReceiveCount=3` (L27) moves a failed event to the DLQ and **unblocks its PNR group**; later events run
  without it, and a DLQ redrive then overwrites the newer decision. A malformed body is retried 3 times though it can
  never succeed. Same `event_id` with different content after 5 min is silently dropped (L60).
- **Not 3:** no 10x test; failure path breaks ordering. **Not 1:** idempotent and memory-bounded.

### Cost - `2/3`

**Anchors:** 3 right service + sampled + cost-per-record measurable · 2 right service, no sampling · 1 right service, cost not considered · 0 wrong service

**Evidence:**
- Good: all pay-per-use; cost shown per line (L129-142); option cost comparison (L148-155).
- Gaps: the `publish` Lambda (L37) only forwards to SQS - an EventBridge rule can target SQS directly (saves a Lambda,
  1 of 10). The $2.10 "Streams polling" line (L136) is only billed if Pipes polls; with a Lambda consumer (implied by
  L61) stream reads are free - so the line and the design disagree. Alarms ≤ 10 are free (L140).
- **Not 3:** no cost-per-record measurement. **Not 1:** services fit the workload.

---

## Handler section (L66-97)

| Item | Assessment |
|------|-----------|
| D1 `InvalidOperation` caught per record | Correct fix |
| D2 `failed_groups` per `MessageGroupId` | Correct and better than "fail the rest of the batch" |
| D3 `SERVICE = accelya-m2-{LEARNER_ID}-cancellation` | Allowed by the contract pattern, but the lab spec fixes `service = "accelya-m2-cancellation"`; default learner id hard-coded in code |
| D4 batch correlation_id = `aws_request_id` | Correct |
| D5 `no_show_flag is True` | **Still wrong:** a string `"true"` is now treated as *not* no-show -> APPROVE -> refund paid to a no-show. Reject non-boolean values as invalid instead |
| Not stated | Whether JSON numbers are parsed with `parse_float=Decimal` (float precision loss) and whether `departure_status` is validated (e.g. `"DEPARTED"` / `null` -> APPROVE) |

---

## If REVISE: what to change

1. **Correctness** - Add the stream consumer explicitly (EventBridge Pipes with an allow-list transform, or a Lambda that
   checks `FailedEntryCount`), with its own retry limit and DLQ; fix the H3/H4 IAM rows.
2. **Correctness** - Add `ticket_number` and a source `seq` to the row; keep a per-ticket current decision guarded by
   `seq > stored_seq` so settlement always ends on the latest decision.
3. **Scalability** - Store malformed events as a terminal INVALID record (no retries); handle a DLQ'd event without
   letting a redrive override a newer decision.
4. **Cost** - Replace the `publish` Lambda with an EventBridge rule targeting `settlement-feed` directly; align the cost
   table with the chosen stream consumer.
5. **Requirements** - Use 2,000/day, peak 50/min (or justify 60K/hour and resize the cost table).

**Next iteration prompt addition:**
> Show the component that reads DynamoDB Streams and writes to EventBridge, with its IAM and DLQ. Keep one current
> decision per ticket_number using a source sequence number, and send settlement only the latest. Route EventBridge
> rules straight to SQS without a forwarding Lambda. Treat non-boolean no_show_flag as invalid.

---

*Pass bar: ≥12/15 AND every dim ≥2.*
