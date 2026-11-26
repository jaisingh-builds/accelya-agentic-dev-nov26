# HITL_REVIEW: m2-arch-option-a

> Draft prepared with Claude's help from the template in `docs/HITL_REVIEW_TEMPLATE.md`. The reviewer must read the artefact, adjust the scores and own the verdict.

**Artefact:** `m2-arch-option-a.md` (Option A, hybrid, revision 2; reviewed from `Downloads`, not yet in the repository)
**Reviewer:** `<reviewer-handle>`
**Date:** 2026-11-25
**Module:** M2
**Lab:** Lab 3 (cancellation refund decision)

Checked against: `AGENTS.md`, `docs/LOGGING_CONTRACT.md`, `docs/logging-contract.json`, `docs/service-boundary-6q.md`, `glossary/airline.md`, `day2/data/refund_rules.json`, the spec in `day2/src/cancellation/handler.py`, `day2/data/cancellation_events.json`, `day2/data/expected_decisions.json`.

---

## The score (0-3 per dim · PASS = >=12/15 AND every dim >=2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 1     | Bad-record handling contradicts the lab spec and harness, and the duplicate handling names the wrong DynamoDB error. |
| Security       | 2     | PII stripped at the API and no wildcards, but IAM is incomplete and vague in places, and refund inputs have no trust boundary. |
| Observability  | 1     | The planned EMF metric fails `check_logs.py`, and `message_id` is missing from the per-record logs. |
| Scalability    | 1     | Idempotent and bounded, but about 12 services for fewer than 1 event a second, and the reporting chain has no consumer. |
| Cost           | 2     | Costs are quantified and cheap, but the Athena chain does not fit the stated consumer. |
| **TOTAL**      | 7/15  | |

**Logging contract (from Day 2, mandatory):** `python3 day2/scripts/check_logs.py <log>` → **2 non-conformant lines** out of 5 planned lines (see Observability).
*A non-conformant artefact scores at most 1 on Observability.*

**Verdict:** **REVISE** (total below 12, and Correctness, Observability and Scalability are below 2)

---

## What is good (keep)

- Money examples check out: `evt-1002` gives 3090.00 and `evt-1009` (1234.10 at 75% = 925.575) gives 925.58, matching `expected_decisions.json`.
- PII (`passenger_name`, `contact_email`) is dropped at the API, so it never reaches the queue, DLQ or later hops. `additionalProperties` is false.
- Every resource uses the `accelya-m2-<learner>-` prefix, and the one forced exception (Glue database name) is stated.
- Two-key idempotency (event and ticket) closes the double-refund gap. `seq` is honest that FIFO orders by arrival, not event time.
- Unverified sandbox services are flagged, the self-scored scorecard is removed, and the DLQ alarm path and runbook are mentioned.

---

## Per-dimension detail

### Correctness: `1/3`

**Anchors:**
- **3**: matches spec + edge cases tested
- **2**: matches spec; happy path only
- **1**: matches spec but missing field or wrong API name
- **0**: doesn't compile / hallucinated APIs

**Evidence:**
- **C1 Contradicts the lab spec on bad records.** The spec says an invalid body (m-07) or a missing required field (m-08) goes into `batchItemFailures` with an `InvalidCancellation`-style `ERROR`, and `run_and_check.py` expects both there. The artefact turns a missing `fare_class` into MANUAL_REVIEW (line 53, 75) and writes unparseable bodies to an S3 quarantine and acknowledges them (line 76, 79). Fixture m-08 has no `fare_class`, so it would be decided instead of failed. It also only covers a missing `fare_class`, not a missing `departure_status`, `no_show_flag` or `fare_amount`.
- **C2 Wrong error for the API it names.** `TransactWriteItems` does not raise `ConditionalCheckFailedException` (line 65). It raises `TransactionCanceledException`, and the failed condition is in `CancellationReasons`. As written, a harmless redelivery is treated as an unknown error, retried five times and dead-lettered with an alarm. The design also never says how to tell which key failed (event key or ticket lock) or that the lock record must be read to compare `event_id`.
- **C3 DynamoDB Streams will carry the lock and review records.** The Pipe has no filter (line 66). Lock and review records would be published as `DecisionMade`, to SNS and Firehose. That also doubles the stream count against the completeness check.
- **C4 `fare_amount` regex is unbounded.** `^\d+\.\d{2}$` accepts a number with more than 28 digits, and Decimal rounding then raises `InvalidOperation` and fails the batch. Bound the length.
- **C5 The invariant "every event ends in a decision or an alerted quarantine" is not true for duplicate tickets.** They are logged at WARNING and "counted" (line 84), with no named counter and no alarm, so settlement is never told.
- **C6 An Athena view cannot be created in SAM as claimed (line 7).** I am not aware of a native CloudFormation resource that creates an Athena view, so this likely needs a custom resource or a manual DDL run. Verify before promising "no click-ops".

---

### Security: `2/3`

**Anchors:**
- **3**: ARN-scoped IAM + named verbs + Conditions; secrets via env; PII tagged
- **2**: no wildcards; secrets via env; PII not addressed
- **1**: some wildcards present
- **0**: `Action: "*"` / `Resource: "*"` / hardcoded secrets

**Evidence:**
- **S1 No trust boundary for refund inputs.** The API validates structure only (line 53). Nothing says `fare_amount`, `fare_class`, `departure_status` and `no_show_flag` come from the booking system and not from a caller-controlled request. A caller could set its own refund.
- **S2 IAM is incomplete or inaccurate:**
  - The decide Lambda has no `s3:PutObject` on the quarantine prefix and no log-group actions.
  - Firehose is given "multipart actions" (line 68). Named actions are required, along with bucket-level ones and the Glue actions Parquet conversion needs.
  - H7 refers to a "workgroup role" (line 69). Athena workgroups do not have one, so the querying principal needs named Athena, S3 and Glue actions.
  - The rule DLQ has no `sqs:SendMessage` permission line.
- **S3 No Conditions anywhere.** Topic, queue and rule policies need `SourceArn` or `SourceAccount` conditions.
- **S4 Logging gap at the gateway.** The artefact does not say API Gateway execution or access logging is configured to exclude request bodies. Passenger data passes through it before the mapping template drops it.
- **S5 Reachability.** The artefact states "reachable through Athena workgroup access", but settlement has no AWS access (decided earlier in this work), so only the engineering team could use it.

---

### Observability: `1/3`

**Anchors:**
- **3**: powertools Logger + correlation_id + EMF + classified errors
- **2**: JSON logs + classified errors; no metrics yet
- **1**: JSON logs only
- **0**: `print` statements / silent `except: pass`

**Evidence:**
- **O1 EMF breaks the contract today.** I wrote the five lines the artefact plans to emit to a scratch file and ran `check_logs.py`: the EMF line fails with `key '_aws' is not in the contract` and `key 'DecisionsWritten' is not in the contract`. Contract section 5 only allows EMF once Powertools is configured on Day 4. Calling `PutMetricData` directly instead needs `Resource: "*"`, which `AGENTS.md` forbids. Either open a contract PR (version bump) or defer the completeness alarm to Day 4. The decided/duplicate/quarantine lines were conformant.
- **O2 `message_id` is missing.** The spec requires the SQS `messageId` on every per-record line, but the allowed-keys list (line 100) leaves it out.
- **O3 "Verified with `check_logs.py`" (line 97) is unsupported.** This is a design document, so no log output was checked. The claim should be removed or backed by a real run.
- **O4 `ingest.rejected` has no owner (line 62, 99).** API Gateway access logs cannot produce an ISO-8601 `ts` as far as I know, and no Lambda sits at that hop. Say which component writes the line, or record an exception in the contract.
- **O5 The service name differs from the spec.** The spec says `service = "accelya-m2-cancellation"`. The artefact uses `accelya-m2-<learner>-decide`. The checker allows both patterns, but it should match the spec.

---

### Scalability: `1/3`

**Anchors:**
- **3**: idempotent + bounded memory + maintainable + tested at 10× scale
- **2**: idempotent + bounded; maintainability OK
- **1**: idempotent only
- **0**: unbounded memory, non-idempotent, OR over-abstracted

**Evidence:**
- **SC1 Idempotent and bounded, but not maintainable at this size.** About 12 services (API Gateway, FIFO, DLQ, Lambda, DynamoDB, Streams, Pipe, bus, SNS, Firehose, S3, Glue, Athena, Chatbot) serve fewer than 1 event a second. Borderline against the anchor-0 over-abstraction wording, so scored 1 as a judgement call.
- **SC2 The reporting chain has no consumer.** Streams, Pipe, Firehose, Glue and Athena exist to feed a view settlement cannot use. The bus has two targets, but the SNS target is undefined (an email per decision would be about 2,000 a day).
- **SC3 A poison message in a FIFO group blocks that PNR** for up to five receives. Risk 1 admits this, but with no limit on how long the visibility timeout makes it.
- **SC4 No test at 10x scale** and no concurrency settings are stated.
- **SC5 Stack deletion is not addressed.** There is no `DeletionPolicy: Retain` for the table or bucket.

---

### Cost: `2/3`

**Anchors:**
- **3**: workload-appropriate service + sampled where applicable + cost-per-record measurable
- **2**: workload-appropriate service; no sampling discipline
- **1**: workload-appropriate; cost not considered
- **0**: wrong service for the workload

**Evidence:**
- Costs are quantified per component and per 1,000 events (about $2 to $6 a month at 60k events), and the figures look plausible. They are unverified, as the artefact itself says.
- The core pipeline is the right shape. The Athena/Glue/Firehose reporting chain is not workload-appropriate for a consumer without AWS access, which is why this is not a 3.
- Missing from the table: CloudWatch Logs ingestion, API Gateway logs and S3 storage.

---

## AGENTS.md checklist

| Rule | Result |
|---|---|
| Airline vocabulary, no generic names | Mostly. "lock item", "review item" and "decision item" (lines 55, 83, 90) use the banned word `item`, and `reviewer_id` is generic. Low. |
| us-east-1, Python 3.12, SAM, no click-ops | Stated, but the Athena view and the AWS Chatbot Slack authorization likely need manual steps. |
| Resource names `accelya-m2-<learner>-` | Table incomplete: no API Gateway, SNS topic, DLQs for the Pipe and rule, alarms, Glue table or log group. |
| IAM: named actions on ARN-scoped resources | Partly, see S2. |
| No secrets or account ids in code | Pass. |
| Logging contract | Fails for EMF and the missing `message_id`, see Observability. |
| Money is Decimal with `ROUND_HALF_UP` | Pass (verified on `evt-1002` and `evt-1009`). |
| Six service-boundary questions per hop | Missing hops: Lambda → S3 quarantine and SNS → Chatbot → Slack. H7 describes a non-existent role. |
| Sandbox limits | Lambda count and memory fine. Service availability is unverified, as the artefact says. |

---

## If REVISE: what to change

1. **Correctness:** Align bad-record handling with the lab spec (`batchItemFailures`, then DLQ for a missing field or an invalid body), or get the spec and harness amended first. Fix the duplicate handling to catch `TransactionCanceledException`, read `CancellationReasons`, and compare the stored `event_id`. Add a Pipe filter so only decision records emit `DecisionMade`. Bound the `fare_amount` length. Put duplicate-ticket cases on an alarm.
2. **Observability:** Remove the EMF metric and its alarm until Day 4, or open a contract PR with a version bump. Add `message_id` to the per-record lines. Name the component that writes `ingest.rejected`. Remove the "verified" claim until a real log has been checked. Use the spec's `service` name.
3. **Scalability:** Decide how settlement actually receives decisions. If it has no AWS access, remove the Streams, Pipe, Firehose, Glue and Athena chain (or defend it with a named consumer). Define the SNS target. State concurrency settings, a 10x test and `DeletionPolicy: Retain`.
4. **Security:** State that refund inputs come from the booking system, not the caller. Add the missing IAM (quarantine writes, log actions, Firehose, Athena principals, rule DLQ), name every action, and add `SourceArn`/`SourceAccount` conditions. Confirm gateway logging excludes bodies.
5. **Cost:** Re-cost after the reporting decision, and add logs and storage.
6. **AGENTS.md hygiene:** Replace "item" with "record", complete the naming table, add the two missing hops, and settle the Athena-view and Chatbot manual-step questions.

**Next iteration prompt addition:**

```
Revise m2-arch-option-a.md against day2/reviews/HITL_REVIEW-m2-arch-option-a.md. Constraints:
- Bad records follow the handler spec: invalid body or missing required field goes to batchItemFailures with an ERROR line carrying error_type, then the DLQ. No MANUAL_REVIEW or quarantine for them unless the spec is amended.
- Duplicate handling with TransactWriteItems catches TransactionCanceledException and inspects CancellationReasons; compare the stored event_id before deciding duplicate versus conflict.
- Settlement has no AWS access. Choose a delivery method that works without it, or remove the Athena chain.
- Every log line uses only keys in docs/logging-contract.json, including message_id on per-record lines. No EMF until the contract allows it.
- Refund inputs (fare_amount, fare_class, departure_status, no_show_flag) come from the booking system, never the caller.
- IAM lists named actions on ARN-scoped resources with SourceArn/SourceAccount conditions; no wildcards.
- Answer the six service-boundary questions for every hop, including Lambda to S3 and any chat notification.
```
