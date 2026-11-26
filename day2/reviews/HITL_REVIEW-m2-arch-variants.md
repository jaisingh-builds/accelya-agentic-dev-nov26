# HITL_REVIEW — m2-arch-variants (prompt)

> **Scope note.** The artefact reviewed here is the **prompt**, before any architecture answer has been generated.
> The 0-3 anchors below are written for generated code, so each dimension is read as "does the prompt make the
> answer satisfy this dimension?". When the three architecture options exist, repeat this review on that output.

---

**Artefact:** `day2/prompts/m2-arch-variants.md`
**Reviewer:** `vikramsing`
**Date:** 2026-11-25
**Module:** `M2`
**Lab:** `M2 architecture variants (cancellation refund decisions)`

---

## The score (0–3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 2     | Flow, decisions, volume and ordering are clear; only one happy-path example and the refund rules are not stated. |
| Security       | 2     | No-log rule for names, emails and fares is present; no IAM or encryption requirement, and events carry PII. |
| Observability  | 1     | Never mentions the logging contract, `correlation_id`, metrics or alarms; only says what not to log. |
| Scalability    | 2     | Volume, peaks, per-PNR ordering and idempotency are stated; no 10x question. |
| Cost           | 2     | Asks for a cost driver per option and gives volume; no sandbox limits or budget constraint. |
| **TOTAL**      | 9/15  |                                                      |

**Logging contract (from Day 2, mandatory):** not applicable to a prompt (no log produced). Re-run
`python3 day2/scripts/check_logs.py <log>` on the implementation later.
*A non-conformant artefact scores at most 1 on Observability.*

**Verdict:** REVISE (total 9/15 is under 12, and Observability is 1, below the minimum of 2)

---

## Per-dimension detail

### Correctness — `2/3`

**Evidence:**
- States the business flow, the three decisions (APPROVE / REJECT / MANUAL_REVIEW) and "settlement team must see every decision".
- Only one example (K fare, APPROVE, 3090.00). No edge case (no-show, departed, malformed body, missing correlation id), and the refund percentages (R-SAVER 75%) are not given.
- "Must see every decision" has no how or how fast (table, report, dashboard).

### Security — `2/3`

**Evidence:**
- Non-goal bans logging passenger names, emails and fare amounts.
- Real events carry `passenger_name` and `contact_email`, but the prompt asks for no encryption at rest for the queue and no named-action, ARN-scoped IAM role per hop.

### Observability — `1/3`

**Evidence:**
- Does not point to `docs/LOGGING_CONTRACT.md`, so the options may ignore the mandatory keys (`correlation_id`, `boundary`).
- No request for metrics, an alarm on the dead-letter queue, or a way to measure decision latency.
- The six service-boundary questions do include a correlation id per hop, which is the only observability pull in the prompt.

### Scalability — `2/3`

**Evidence:**
- Gives normal and peak volume (under 5/min, 50/min), per-PNR ordering and duplicate delivery, which pushes the options to address idempotency.
- Does not ask what changes at 10x volume (20,000/day, 500/min), or the trade-off between strict ordering and throughput.

### Cost — `2/3`

**Evidence:**
- Asks for a rough cost driver per option, and the volume is stated, so options can be compared.
- Missing the sandbox constraints used in M1 (max 10 Lambda functions, memory <= 2048 MB, no public buckets), so an option could be unbuildable.

---

## If REVISE: what to change

1. **Observability** — reference `docs/LOGGING_CONTRACT.md`, require `correlation_id` on every record, and ask for a metric and a dead-letter-queue alarm.
2. **Security** — require an IAM role with named actions on ARN-scoped resources for every hop, and encryption at rest for the queue (events contain passenger name and email).
3. **Correctness** — add one edge-case example (no-show) and say how the settlement team sees each decision, and within what time.
4. **Scalability** — ask each option to say what changes at 10x volume.
5. **Cost** — add the sandbox constraints line used in M1.

**Next iteration prompt addition:**

```
Constraints: serverless only; sandbox limits: max 10 Lambda functions, memory <= 2048 MB, no public buckets.
Every hop must name its IAM role with named actions on ARN-scoped resources, and say how the queue is encrypted at rest (events contain passenger_name and contact_email).
Logging follows docs/LOGGING_CONTRACT.md: one JSON record per line, correlation_id on every record, never the event body. Include a metric and an alarm for messages in the dead-letter queue.
Say how the settlement team sees each decision and within what time (suggest: 5 minutes).
For each option, state what changes at 10x volume (20,000 cancellations/day, 500/minute).
Second example: fare_class "L", no_show_flag true, fare_amount "52310.00" -> REJECT, R-NOSHOW, refund 0.00
```

---

*Pass bar: ≥12/15 AND every dim ≥2.*
