# HITL_REVIEW — m2-arch-option1-final-shailya

---

**Artefact:** `day2/outputs/m2-arch-option1-final-shailya.md`
**Reviewer:** `dheeraj` (peer review, drafted with an AI assistant - confirm or adjust each score before signing)
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `Architecture options -> final design`

---

## The score (0–3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 2     | Meets volume, per-PNR ordering and decision visibility; invalid events with no `pnr`/`event_id` can't be keyed (L133) and the PNR block marker isn't guaranteed (L145) |
| Security       | 2     | ARN-scoped named actions, TLS-deny and `aws:SourceArn` conditions; but replay role has table-wide `dynamodb:DeleteItem` (L177) and PII fields aren't tagged |
| Observability  | 1     | Logging design is **non-conformant** to contract v1.1: boundaries and messages fail the value rules (L198-199) - capped at 1 |
| Scalability    | 2     | Idempotent (event + ticket guard), bounded (batch 10, reserved concurrency 5), maintainable pure function; no 10x test or stated peak |
| Cost           | 3     | Workload-appropriate serverless; line-by-line estimate checks out (~$0.85/month = ~$0.000014 per decision) |
| **TOTAL**      | 10/15 |                                                      |

**Logging contract (from Day 2, mandatory):** `python3 day2/scripts/check_logs.py <log>` → **4 violations-bearing lines / 4**
(run on lines built exactly as section 9 specifies: `boundary` `validate`/`ddb_write`/`batch_result`,
`message` `decision_written`/`decision_manual_review`/`batch_done`, and a 13-digit `ticket_number`).
*A non-conformant artefact scores at most 1 on Observability.*

**Verdict:** **REVISE** (10/15 and Observability < 2)

---

## Per-dimension detail

### Correctness — `2/3`

**Anchors:**
- **3** — matches spec + edge cases tested
- **2** — matches spec; happy path only
- **1** — matches spec but missing field or wrong API name
- **0** — doesn't compile / hallucinated APIs

**Evidence:**
- Strong: exactly-one-decision via `TransactWriteItems` with an event guard **and** a ticket guard (L137-143) - a
  ticket can't be refunded twice even with a new `event_id`; deterministic `decide()` with rules as data and
  `rules_version` (L112-127); Decimal + `ROUND_HALF_UP`, `parse_float=Decimal` (L122-124); FIFO partial-failure rule
  correct (L147); the brief's example reproduces (4120.00 -> 3090.00).
- L133 / L116: an invalid event becomes a `MANUAL_REVIEW` record keyed `PNR#<pnr>` / `EVT#<event_id>` (L97) - but if
  the body is unparseable or lacks `pnr`/`event_id`, there is no key to write it under. The design doesn't say what
  happens then.
- L145: the block marker is written only when a *caught* temporary error happens on the last receive. A Lambda
  timeout, out-of-memory or crash on that attempt - or the marker write failing with the same throttling - sends the
  message to the DLQ **without** blocking the PNR; later events for that PNR are then decided out of order, silently.
  This contradicts L150 ("never broken silently").
- L79 / L88: `ticket_number` is `^\d{13}$`; the course data and the logging contract use `^\d{3}-\d{10}$`
  (e.g. `098-2304511901`). Producer events in the course format would all fail validation and become `MANUAL_REVIEW`.
- Edge cases are listed as tests (L240-244) but not yet run - acceptable for a design, so not scored 3.

---

### Security — `2/3`

**Anchors:**
- **3** — ARN-scoped IAM + named verbs + Conditions; secrets via env; PII tagged
- **2** — no wildcards; secrets via env; PII not addressed
- **1** — some wildcards present
- **0** — `Action: "*"` / `Resource: "*"` / hardcoded secrets

**Evidence:**
- Strong: no wildcards; named actions on single ARNs (L170-178); settlement role limited to `Query` on the GSI ARN
  (L174); SNS topic policy with `aws:SourceArn` (L178); queue policies deny non-TLS (L180-181); SSE-SQS, PITR,
  deletion protection (L186-190); config from SAM refs, no account ids (L59).
- L177: `accelya-m2-shailya-dlq-replay-role` gets `dynamodb:DeleteItem` on the **whole table** to clear block
  markers - it can also delete decision records (audit trail). Block markers share the partition key `PNR#<pnr>`
  with decisions (L99), so `dynamodb:LeadingKeys` can't separate them.
- PII (`pnr`, `ticket_number`, `refund_fare_amount`) is handled in logs but not tagged in the data model or
  resource tags - the anchor for 3 requires it.

---

### Observability — `1/3`

**Anchors:**
- **3** — powertools Logger + correlation_id + EMF + classified errors
- **2** — JSON logs + classified errors; no metrics yet
- **1** — JSON logs only
- **0** — `print` statements / silent `except: pass`

**Evidence:**
- Strong: JSON lines, required keys listed, classified errors with `error_type` and an explicit exception map,
  no `str(exc)` (L152-164, L197); good alarms including oldest-message age for a stuck group (L205-212).
- L198: boundaries `sqs_receive`, `validate`, `ddb_read`, `decide`, `ddb_write`, `batch_result` fail the contract
  rule `source->target` or `internal` (e.g. `sqs->lambda`, `lambda->dynamodb`, `internal`).
- L199: messages `decision_written`, `decision_manual_review`, `batch_done` fail the rule "dotted event name"
  (e.g. `refund.decided`, `refund.manual_review`, `batch.completed`).
- L212: the manual-review alarm's metric filter matches `message = "decision_manual_review"` - it must change in
  step with the message fix, or the alarm silently stops counting.
- L197: `correlation_id` = `event_id`. The contract says it is taken from the message attribute and minted only if
  absent; with `event_id` the id can't link the producer's own request to this service.
- L202-203 and open question 4 are answerable now: `pnr`, `ticket_number`, `decision`, `refund_rule_id`,
  `event_id`, `message_id`, `attempt`, `record_count`, `error_code` are all allowed keys in `docs/logging-contract.json`.

---

### Scalability — `2/3`

**Anchors:**
- **3** — idempotent + bounded memory + maintainable + tested at 10× scale
- **2** — idempotent + bounded; maintainability OK
- **1** — idempotent only
- **0** — unbounded memory, non-idempotent, OR over-abstracted

**Evidence:**
- Idempotent at two levels (FIFO dedup + transactional guards), bounded batch and concurrency, one Lambda, rules as
  data - maintainable, no over-abstraction.
- No disruption peak stated (L13; the brief asks for one) and no 10x test planned. The GSI partition key is
  `settlement_date` (L52), so every write of a day lands on one GSI partition - fine at 2,000/day, worth a note for 10x.
- L47: `DeduplicationScope: messageGroup` + `FifoThroughputLimit: perMessageGroupId` (high-throughput FIFO) isn't
  needed at this volume - harmless, but say why it's there.

---

### Cost — `3/3`

**Anchors:**
- **3** — workload-appropriate service + sampled where applicable + cost-per-record measurable
- **2** — workload-appropriate service; no sampling discipline
- **1** — workload-appropriate; cost not considered
- **0** — wrong service for the workload

**Evidence:**
- Checked every line (L254-262): SQS 180k x $0.50/1M = $0.09; transactional writes correctly counted at 2 WRU per
  item (240k WRU = $0.15); alarms $0.40; total ~$0.83 ≈ stated $0.85. Cost per decision is derivable (~$0.000014).
- Correctly notes that alarms dominate and that cost doesn't decide the design (L264).

---

## If REVISE: what to change

1. **Observability** — rename boundaries to the contract form (`sqs->lambda`, `lambda->dynamodb`, `internal`) and
   messages to dotted names (`refund.decided`, `refund.manual_review`, `refund.duplicate_event`, `batch.completed`);
   use the allowed keys `decision`, `refund_rule_id`, `pnr` instead of encoding them in `message`; update the L212
   metric filter to match. Attach a `check_logs.py` CONFORMANT run.
2. **Observability** — producer sends a `correlation_id` message attribute; fall back to `event_id` only when absent,
   and log WARNING when falling back.
3. **Correctness** — make the PNR block fail-safe: write the block marker on the **first** temporary failure of a
   message and delete it when that message succeeds, so a timeout/crash/throttled last attempt still leaves the PNR
   blocked. Also stop processing when `context.get_remaining_time_in_millis()` is low.
4. **Correctness** — key unkeyable invalid events by the SQS envelope: `pk=PNR#<MessageGroupId>`,
   `sk=MSG#<messageId>`, `reason_code=INVALID_EVENT`.
5. **Correctness** — `ticket_number` pattern `^\d{3}-\d{10}$` (airline prefix + serial), matching course data and
   the logging contract.
6. **Security** — move block markers to their own key space (`pk=BLOCK#<pnr>`, `sk=BLOCK`) and condition the replay
   role's `dynamodb:DeleteItem` (and the Lambda's block-marker writes) with `dynamodb:LeadingKeys` = `BLOCK#*`;
   tag PII attributes in the data model and add a `data-classification` resource tag.
7. **Scalability** — state the disruption peak (e.g. 50/minute) and show reserved concurrency 5 + batch 10 covers it.
8. **Verify** — that `StartMessageMoveTask` works for a FIFO DLQ in the sandbox before relying on `replay_dlq.sh`.

**Next iteration prompt addition:**
`Logging must conform to docs/logging-contract.json v1.1: boundary is "source->target" or "internal", message is a lowercase dotted event name, extra keys only from the "optional" list. ticket_number is ^\d{3}-\d{10}$. Every failure path that can lose ordering (timeout, crash, throttled last attempt) must leave the PNR blocked.`

---

*Pass bar: ≥12/15 AND every dim ≥2.*
