# Business Requirements Document
## Accelya Cancellation and Refund Settlement Pipeline

| Attribute | Value |
|---|---|
| Document Version | 1.0 |
| Status | Draft |
| Date | 2026-11-26 |
| Owner | Accelya Platform Engineering |
| Reference Architecture | `docs/reference-architecture.png`, `docs/REFERENCE_ARCHITECTURE.md` |
| Review Template | `docs/HITL_REVIEW_TEMPLATE.md` |

---

## 1. Executive Summary

Accelya requires an event-driven, fully audited cancellation and refund settlement pipeline on AWS. The system must receive flight cancellation events from airline settlement producers, apply configurable refund rules and AI-assisted risk scoring, enforce strict per-PNR ordering and idempotency, and produce deterministic refund decisions that feed downstream payments processing and financial reconciliation. Human-in-the-loop review points handle all ambiguous and high-value cases. Every decision must be traceable from originating event through to settlement.

---

## 2. Business Context

### 2.1 Domain

This pipeline operates within the Accelya airline settlement platform. All terminology follows the airline settlement vocabulary:

| Term | Meaning |
|---|---|
| `pnr` | Passenger Name Record — the booking reference; the ordering unit |
| `ticket_number` | The refund unit; one PNR can hold multiple tickets |
| `airline_code` | IATA airline designator |
| `fare_class` | Booking class of service |
| `od_pair` | Origin-destination pair |
| `settlement_date` | The business date used for interline settlement |
| `agency_iata` | Issuing travel agency IATA code |
| `fare_amount` | The ticketed fare; the financial claim being refunded |

### 2.2 Business Problem

Airline settlement requires that cancellation refunds are:
- processed exactly once per ticket (never double-refunded),
- never refunded above the booked fare,
- processed in strict time order per PNR,
- auditable end-to-end for regulatory and interline settlement purposes,
- reconciled daily to confirm that every accepted event produces exactly one recorded decision.

Manual or batch-based approaches do not meet the volume, ordering, or audit requirements. A fully automated, event-driven pipeline with explicit human review gates is required.

---

## 3. Scope

### 3.1 In Scope

- Ingestion of cancellation events via HTTPS from airline settlement producers.
- Refund decision engine applying configurable fare rules and AI risk scoring.
- Idempotency and duplicate detection at every processing step.
- Strict per-PNR ordering through a FIFO queue.
- Human-in-the-loop review for MANUAL_REVIEW decisions (fare mismatch, high value, risk, conflict).
- Reviewer correction channel (`/corrections`) feeding back into the same decision audit trail.
- Real-time notifications to payments processing and reviewer email.
- Raw event archive and decision archive in S3 for audit and reconciliation.
- Daily Athena-based reconciliation: event count balancing, over-refund check, settlement sums.
- Replay job for recovery and dry-run what-if analysis when rules or model versions change.
- Operational alarms and on-call alerting via CloudWatch and SNS.

### 3.2 Out of Scope

- Step Functions orchestration (non-goal; single decision step does not warrant it).
- Kinesis Data Streams or Amazon MSK (per-shard ordering does not meet per-PNR ordering requirement).
- Passenger-facing UI or mobile channels.
- Payment execution (the pipeline produces an approved refund record; a downstream payments service executes the transfer).
- Custom model training (a pre-trained SageMaker Serverless endpoint is consumed, not trained here).

---

## 4. Stakeholders

| Role | Responsibility |
|---|---|
| Airline settlement producer | Submits cancellation events via `POST /cancellations` |
| Refund reviewer (H1) | Reviews and decides MANUAL_REVIEW cases via `POST /corrections` |
| Team lead (H2) | Monitors review SLA; reassigns or decides stuck reviews older than 24 hours |
| On-call engineer (H3, H4) | Responds to DLQ alarms, conflict alerts, infrastructure errors |
| Rules / model owner (H5, H7) | Manages fare rules, high-value thresholds, model versions; runs dry-run replay before promoting changes |
| Finance / settlement team | Consumes the daily Athena settlement query and reconciliation report |
| Accelya platform engineering | Owns the pipeline design, infrastructure-as-code, deployment, and monitoring |

---

## 5. Functional Requirements

### 5.1 Event Ingestion (Hop 0 → Hop 1 → Hop 2)

| ID | Requirement |
|---|---|
| FR-01 | The system shall accept cancellation events via `HTTPS POST /cancellations` validated against the canonical event schema (`schemas/cancellation-event.v1.json`). |
| FR-02 | Malformed requests (schema violations, invalid `fare_amount` format) shall be rejected with HTTP 400 and never reach the queue. |
| FR-03 | The API Gateway shall rate-limit producers: 50 requests/second sustained, 100 burst. |
| FR-04 | Each accepted event shall be placed on the FIFO queue with `MessageGroupId = pnr` and `MessageDeduplicationId = event_id` to enforce per-PNR ordering and deduplicate retries within the 5-minute window. |
| FR-05 | A successful enqueue shall return HTTP 202 with the SQS MessageId. Any SQS error shall return HTTP 500 so the producer retries with the same `event_id`. |
| FR-06 | Reviewer corrections shall be submitted via `POST /corrections` using a separate credential (reviewer API key in labs; `refund-reviewer` IAM role in production). This route accepts only `event_type: refund_correction`; `/cancellations` accepts only `event_type: cancellation`. |

### 5.2 Event Contract

| Field | Constraint |
|---|---|
| `event_id` | `^[A-Za-z0-9-]{1,40}$`; unique per event; serves as the idempotency key |
| `event_type` | `cancellation` or `refund_correction` |
| `pnr` | `^[A-Z0-9]{6}$`; the ordering and grouping key |
| `ticket_number` | `^\d{3}-\d{10}$`; the refund unit |
| `fare_amount` | JSON string `^\d{1,9}\.\d{2}$` (e.g. `"8450.00"`); never a JSON number |
| `currency` | ISO-4217 from an allowlist in reference data |
| `cancellation_timestamp` | UTC ISO-8601; used for event ordering and stale checks |
| `corrects_event_id`, `review_outcome`, `reviewer_id` | Present only on `refund_correction`; `review_outcome` is `APPROVE` or `REJECT` |
| `passenger_name`, `contact_email` | May be present but are never logged, never in notifications, never in decision records |

### 5.3 Reliability and Fault Tolerance (Hops 2 → 3 → 4)

| ID | Requirement |
|---|---|
| FR-07 | Events that fail processing after 5 delivery attempts (maxReceiveCount = 5) shall move to a FIFO Dead Letter Queue. All later events for the same PNR shall move with them and in order; other PNRs are unaffected. |
| FR-08 | The DLQ alarm shall trigger an on-call notification when depth > 0 or oldest message age > 1 hour. |
| FR-09 | Lambda processing shall use partial batch failure reporting (`ReportBatchItemFailures`, `SqsFifoPartialProcessor(skip_group_on_error=True)`) so a single failing PNR does not block other PNRs in the same batch. |

### 5.4 Refund Decision Engine (Hop 5)

Processing within the Lambda `refund-decider` follows a fixed step sequence:

| Step | Requirement |
|---|---|
| FR-10 (5a Archive) | All raw events in the batch shall be written to Firehose `refund-archive` before any business logic runs. |
| FR-11 (5b Idempotency) | Before processing, the system shall check the `refund-idempotency` table. A previously COMPLETED event with the same payload hash shall be skipped (or re-emitted if not yet emitted). A COMPLETED event with a different payload hash shall result in `MANUAL_REVIEW` with `refund_rule_id = R-CONFLICT` and an alert. |
| FR-12 (5c Validate) | Schema validation and money checks (§4 money rules) shall be applied. Invalid payloads shall be written to `S3 quarantine/` with a deterministic key `quarantine/<reason>/<event_id>.json` and get decision `REJECT`, `R-INVALID`. |
| FR-13 (5d Load State) | The current ticket state shall be read from the `ticket-state` DynamoDB table with consistent reads. |
| FR-14 (5e Decide) | The decision shall be computed using the Day 2 fare rules, the booking record (fare verification), and the SageMaker Serverless risk score. The decision shall store its pins: `rules_sha256`, `ref_data_sha256`, `model_version`, `code_version`, `schema_version`. Decisions shall use `cancellation_timestamp`, never `now()`, to ensure determinism. |
| FR-15 (5f Commit) | The idempotency record and ticket state update shall be written as a single `TransactWriteItems`. Condition expressions enforce optimistic locking; a condition failure causes a re-read and retry. |
| FR-16 (5g Emit) | The decision record shall be written to Firehose `refund-decisions` and published to SNS `refund-notifications`. `decision_id = event_id` so duplicates are identical. |
| FR-17 (5h Mark Emitted) | The idempotency record shall be marked `emitted=true` after a successful emit. A crash before this point results in re-emission of the stored decision; the Lambda never recomputes. |

### 5.5 Money Rules

| ID | Requirement |
|---|---|
| FR-18 | `fare_amount` must be a string with exactly 2 decimal places. Any other format is `REJECT`, `R-INVALID`. |
| FR-19 | All monetary arithmetic shall use Python `Decimal` with `ROUND_HALF_UP`; never `float`. |
| FR-20 | The event's `fare_amount` is a claim. It shall be validated against the booking record (`ref/bookings.json` in labs; a PSS lookup in production). A mismatch gives `MANUAL_REVIEW`, `R-FAREMISMATCH`. |
| FR-21 | A ticket already in REFUNDED state shall give `REJECT`, `R-DUPREFUND`. |
| FR-22 | `fare_amount` and `refund_amount` shall never appear in log lines. They live only in the archive, decision records, and DynamoDB. |

### 5.6 Ticket State Machine

| Current State | Event | Condition | New State | Decision / Rule ID |
|---|---|---|---|---|
| any | any | `cancellation_timestamp` older than `last_event_ts` | unchanged | REJECT, `R-STALE` |
| NONE | cancellation | invalid payload | NONE | REJECT, `R-INVALID` |
| NONE | cancellation | fare ≠ booking record | REVIEW | MANUAL_REVIEW, `R-FAREMISMATCH` |
| NONE | cancellation | APPROVE + refund ≥ high_value_threshold | REVIEW | MANUAL_REVIEW, `R-HIGHVALUE` |
| NONE | cancellation | APPROVE + risk score ok | REFUNDED | APPROVE, `R-FLEX` / `R-BUSINESS` / … |
| NONE | cancellation | APPROVE + risk score high | REVIEW | MANUAL_REVIEW, `R-RISK` |
| NONE | cancellation | model unavailable | REVIEW | MANUAL_REVIEW, `R-NOMODEL` |
| NONE | cancellation | rules → REJECT | CLOSED | REJECT, `R-NOSHOW` / `R-DEPARTED` / … |
| REVIEW | cancellation | — | REVIEW | MANUAL_REVIEW, `R-DUPPENDING` |
| REFUNDED | cancellation | — | REFUNDED | REJECT, `R-DUPREFUND` |
| CLOSED | cancellation | — | CLOSED | REJECT, `R-DECIDED` |
| REVIEW | refund_correction | `corrects_event_id` = open review | REFUNDED / CLOSED | APPROVE / REJECT, `R-REVIEWED` |
| not REVIEW | refund_correction | — | unchanged | REJECT, `R-DECIDED` |

Every state transition writes a decision record. No event disappears silently.

### 5.7 Human-in-the-Loop Review Points

| Point | Trigger | Actor | Action | Recorded As |
|---|---|---|---|---|
| H1 Refund review | MANUAL_REVIEW (R-FAREMISMATCH, R-RISK, R-NOMODEL, R-HIGHVALUE, R-CONFLICT) | Refund reviewer | Reviews email (no PII) + Athena `review_context` query; posts `refund_correction` to `/corrections` | APPROVE / REJECT with `R-REVIEWED` and `reviewer_id` |
| H2 Review SLA | Ticket in REVIEW > 24 hours | Team lead | Reassigns or decides via H1 | Remains listed until closed |
| H3 Conflict | Same `event_id`, different payload (R-CONFLICT) | On-call + producer owner | Fixes producer; closes review via H1 | Correction event |
| H4 DLQ redrive | DLQ depth alarm | On-call | Diagnoses root cause, deploys fix, then redrives | Runbook entry + redrive task ID |
| H5 Rule/model change | New `rules_sha256`, threshold, or `model_version` | Reviewer (not author) | Reviews PR + golden test + decision-diff report from dry-run replay; approves only if changed decisions are intended | PR approval + diff report in S3 |
| H6 Correction batch | Reconciliation finds wrong decisions | Reviewer + team lead | Reviews generated `refund_correction` file using HITL template before replay | The correction events themselves |
| H7 Learning loop | Weekly | Model / rules owner | Analyses reviewer overturn rate; tunes threshold or model; triggers H5 | New pins |

**Rules for all HITL points:**
- Humans act through events, never by editing DynamoDB, S3, or the queue directly.
- Only the reviewer channel (`/corrections`) can submit `refund_correction` events.
- Both routes feed the same FIFO queue; per-PNR ordering is preserved for reviewer corrections.

### 5.8 Notifications (Hop 7)

| ID | Requirement |
|---|---|
| FR-23 | MANUAL_REVIEW decisions shall trigger an email to the refund reviewer distribution list. The email body shall contain `event_id`, `refund_rule_id`, `correlation_id`, and a masked PNR only — no PII, no fare amounts. |
| FR-24 | APPROVE decisions shall be published to the SQS payments queue for downstream payments processing. The payments queue policy shall allow messages only from the `refund-notifications` SNS topic. |
| FR-25 | Operational alarms (DLQ depth, Lambda errors, API 5XX, Firehose latency, DynamoDB throttles) shall publish to `ops-alerts` SNS topic, triggering on-call email. |

### 5.9 Archive and Reconciliation (Hops 6, 8)

| ID | Requirement |
|---|---|
| FR-26 | All raw cancellation events (raw batch, pre-decision) shall be written to `S3 archive/` via Kinesis Data Firehose, partitioned by `ingest_dt`. |
| FR-27 | All decision records shall be written to `S3 decisions/` via Kinesis Data Firehose, partitioned by `ingest_dt`. |
| FR-28 | Firehose delivery shall buffer at most 60 seconds / 5 MB before writing. Files shall be GZIP-compressed JSON lines. |
| FR-29 | The Glue catalog shall register both the archive and decisions tables with partition projection on `ingest_dt`. |
| FR-30 | The Athena reconciliation workgroup shall enforce that: (a) `DISTINCT event_id in archive = DISTINCT event_id in decisions + event_ids in DLQ`; (b) approved `refund_amount` per `ticket_number` never exceeds the booking fare; (c) `SUM(refund_amount)` per `business_date` and `currency` for `decision=APPROVE` is computed for settlement. |
| FR-31 | The reconciliation query shall run over a closed window (day D, after D+1 02:00 UTC). |

### 5.10 Replay Job (Hop 10)

| ID | Requirement |
|---|---|
| FR-32 | The replay job (Fargate, ECR image) shall read the archive, deduplicate by `event_id`, sort by `(pnr, cancellation_timestamp, event_id)`, and re-inject events into the FIFO queue with `MessageDeduplicationId = rp-<run_id>-<event_id>`. |
| FR-33 | Recovery replay window shall not exceed 90 days (the DynamoDB idempotency TTL). |
| FR-34 | In dry-run mode (H5), the job shall evaluate candidate rule/model pins over the archive window and write per-event diffs to `diffs/<run_id>/`. It shall never touch the queue or DynamoDB. |
| FR-35 | Correction mode shall never re-drive old events through the money path; Athena findings shall become new `refund_correction` events. |

---

## 6. Non-Functional Requirements

### 6.1 Volume and Performance

| Metric | Target |
|---|---|
| Sustained throughput | 2,000 cancellation events/day (~60,000/month) |
| Peak throughput | 50 events/minute |
| Lambda decision latency (p90) | ≤ 30 seconds (Lambda timeout) |
| SageMaker risk score timeout | 5 seconds (fall through to MANUAL_REVIEW `R-NOMODEL` on breach) |
| Firehose delivery freshness | ≤ 900 seconds (alarm threshold) |
| API rate limit | 50 rps sustained, 100 burst |

### 6.2 Ordering and Idempotency

| Requirement | Mechanism |
|---|---|
| Strict per-PNR ordering | SQS FIFO `MessageGroupId = pnr` |
| Duplicate event suppression (5-minute window) | SQS `MessageDeduplicationId = event_id` |
| At-least-once to exactly-once processing | DynamoDB idempotency table + `TransactWriteItems` |
| Deterministic decisions on retry/replay | Decisions stored before emit; never recomputed; pins recorded |

### 6.3 Data Integrity and Money Safety

- Refund amount computed with `Decimal`, `ROUND_HALF_UP`; never `float`.
- `fare_amount` stored as string; Athena columns `DECIMAL(12,2)`; never `DOUBLE`.
- Over-refund check runs daily in Athena; zero rows expected on a healthy system.
- Booking record is the financial truth; event fare is the claim.

### 6.4 Security

| Area | Requirement |
|---|---|
| Transport | TLS 1.2 minimum; SigV4 body signing in production (prevents in-transit fare tampering) |
| API authentication | API key + usage plan in labs; IAM SigV4 in production |
| Queue injection prevention | SQS queue policy denies `sqs:SendMessage` except from the API Gateway integration role and the replay task role |
| IAM | Named actions scoped to individual resource ARNs; never `"Action": "*"` or `"Resource": "*"` |
| S3 | Block Public Access; BucketOwnerEnforced; versioning on; deny non-TLS; nobody may delete object versions |
| Logging | No PII (passenger names, emails), no fare amounts, no raw exception messages in logs; identifiers and counts only |
| Secrets | No secrets or account IDs in code; all config from environment variables |
| Reviewer channel | Separate credential (API key / IAM role) from the producer channel; `/corrections` accepts only `refund_correction` |

### 6.5 Observability

| Signal | Mechanism |
|---|---|
| Structured logs | JSON, all required keys: `ts`, `level`, `service`, `correlation_id`, `boundary`, `message` |
| Distributed tracing | X-Ray active tracing on the Lambda |
| Business metrics | CloudWatch metric filters on contract-conformant log lines (namespace `Accelya/Refunds`) |
| Alarms | DLQ depth, queue oldest message age, Lambda errors/throttles, API 5XX/4XX rate, Firehose freshness, DynamoDB system errors/throttles, SageMaker 5XX and p90 model setup time, business metrics (conflicts, manual review rate, decision errors) |
| Operational alerting | CloudWatch alarms → SNS `ops-alerts` → on-call email |
| Correlation ID | Present in every hop: HTTP header → SQS message attribute → log lines → decision records |

### 6.6 Availability and Recovery

| Requirement | Detail |
|---|---|
| Dead-letter handling | FIFO DLQ with `maxReceiveCount = 5`; alarmed; redrive only after root cause is fixed (H4) |
| Recovery replay | Fargate replay job over archive within 90-day window |
| DynamoDB | On-demand capacity with PITR enabled |
| Idempotency TTL | 90 days (the replay horizon) |
| Log retention | 7 days in labs; 30 days in production |
| Athena results retention | 7 days (lifecycle policy) |
| Quarantine retention | 30 days (lifecycle policy) |

### 6.7 Cost

| Constraint | Detail |
|---|---|
| Estimated steady-state cost | ~$1–2/month at 60,000 events/month, excluding SageMaker (list prices) |
| Lambda concurrency | Reserved concurrency = 5; `MaximumConcurrency = 5` on the event source mapping |
| Lambda memory | 512 MB (maximum allowed: 2,048 MB per sandbox policy) |
| Athena | Bytes-scanned limit enforced on the workgroup |
| Lambda function count | One Lambda function for the entire pipeline (counts toward sandbox limit of 10 for the whole course) |

---

## 7. Architecture Component Summary

| # | Component | AWS Service | Key Configuration |
|---|---|---|---|
| 0 | Producer | External caller | HTTPS POST `/cancellations` |
| 1 | API Gateway | Amazon API Gateway REST | Schema validation, auth, throttle (50 rps / 100 burst); direct SQS integration |
| 2 | Refund events queue | Amazon SQS FIFO | `refund-events.fifo`; Group=pnr; Dedup=event_id; visibility timeout 180 s; SSE-SQS |
| 3 | Dead letter queue | Amazon SQS FIFO | `refund-events-dlq.fifo`; maxReceiveCount 5; RedriveAllowPolicy = byQueue |
| 4 | Event source mapping | AWS Lambda ESM | Batch size 10; MaximumConcurrency 5; ReportBatchItemFailures |
| 5 | Refund decider | AWS Lambda | Python 3.12; Powertools; alias `live`; 30 s timeout; 512 MB; X-Ray active tracing |
| 6a | Raw archive stream | Amazon Data Firehose | `refund-archive`; buffer 60 s / 5 MB; GZIP; prefix `archive/ingest_dt=...` |
| 6b | Decisions stream | Amazon Data Firehose | `refund-decisions`; buffer 60 s / 5 MB; GZIP; prefix `decisions/ingest_dt=...` |
| DDB | Idempotency + ticket state | Amazon DynamoDB | Two tables; on-demand; PITR on; TTL 90 days on idempotency table; optimistic locking |
| SM | Risk scoring | Amazon SageMaker Serverless | `refund-risk` endpoint; 5 s timeout; fall-through to R-NOMODEL on failure |
| S3Q | Quarantine | Amazon S3 | `quarantine/`; lifecycle 30 days |
| S3A | Archive | Amazon S3 | `archive/`; versioning on; no delete; BucketOwnerEnforced |
| S3D | Decisions | Amazon S3 | `decisions/`; versioning on; no delete; BucketOwnerEnforced |
| 7a | Refund notifications | Amazon SNS | `refund-notifications`; MANUAL_REVIEW filter → reviewer email; APPROVE → SQS payments queue |
| 7b | Ops alerts | Amazon SNS | `ops-alerts`; fed by CloudWatch alarms; → on-call email |
| 8 | Data catalog | AWS Glue | Tables: archive, decisions, quarantine_index; partition projection on ingest_dt |
| Athena | Reconciliation | Amazon Athena | Workgroup with enforced result location, SSE, bytes-scanned limit |
| 9 | Observability | Amazon CloudWatch + AWS X-Ray | Metric filters; alarms; X-Ray tracing |
| 10 | Replay job | AWS Fargate (ECS) + Amazon ECR | Recovery, dry-run, and correction modes; private subnets + VPC endpoints in production |
| H1 | Refund reviewer | Human | Receives MANUAL_REVIEW email; posts correction to `/corrections` |

---

## 8. Data Flow Summary

```
Producer (0)
  → HTTPS POST /cancellations
    → API Gateway (1): validate, auth, throttle
      → SQS FIFO refund-events.fifo (2): ordered per PNR, deduped per event_id
        → [maxReceiveCount 5] DLQ refund-events-dlq.fifo (3)
        → Event Source Mapping (4): batch 10, max concurrency 5
          → Lambda refund-decider (5):
              5a → Firehose refund-archive (6a) → S3 archive/
              5b → DynamoDB refund-idempotency (read)
              5c → S3 quarantine/ (invalid payloads)
              5d → DynamoDB ticket-state (read)
              5e → SageMaker refund-risk (risk score)
              5f → DynamoDB TransactWrite (idempotency + ticket-state)
              5g → Firehose refund-decisions (6b) → S3 decisions/
              5g → SNS refund-notifications (7a)
                    → [MANUAL_REVIEW] Email: refund reviewers
                        → Refund reviewer (H1) → POST /corrections → API Gateway (1)
                    → [APPROVE] SQS payments queue

S3 archive/ + S3 decisions/ → Glue catalog (8) → Athena reconciliation workgroup

CloudWatch alarms (9) → SNS ops-alerts (7b) → Email: on-call

S3 archive/ → Fargate replay job (10) → SQS FIFO refund-events.fifo (2)
```

---

## 9. Resource Naming Convention

All resources follow the pattern `accelya-m<module>-<learner>-<resource>`.

| Module | Resources |
|---|---|
| 6 | API `accelya-m6-<learner>-refund-api`; queues `accelya-m6-<learner>-refund-events.fifo` and `…-refund-events-dlq.fifo`; Firehose `accelya-m6-<learner>-refund-archive`; bucket `accelya-m6-<learner>-refund-data-<account-id>` |
| 7 | Lambda `accelya-m7-<learner>-refund-decider`; tables `accelya-m7-<learner>-refund-idempotency` and `…-ticket-state`; Firehose `accelya-m7-<learner>-refund-decisions`; topics `accelya-m7-<learner>-refund-notifications` and `…-ops-alerts` |
| 8 | SageMaker endpoint `accelya-m8-<learner>-refund-risk` |
| 10 | Glue database `accelya_m10_<learner>`; Athena workgroup `accelya-m10-<learner>`; ECR repo and ECS task `accelya-m10-<learner>-replay` |

---

## 10. Build Phasing (Module Map)

| Module | Deliverables |
|---|---|
| 6 | Hops 1–3: API Gateway, SQS FIFO, DLQ, event schema, correlation ID, Firehose raw archive (6a) |
| 7 | Hop 5: Lambda refund-decider (steps 5b–5h), DynamoDB ticket state machine, decisions Firehose (6b), SNS notifications, CloudWatch alarms, reviewer correction channel (H1) |
| 8 | Step 5e: SageMaker Serverless risk scoring endpoint integration with pinned `model_version` |
| 10 | Athena reconciliation (over-refund check, open-review query H2), Fargate replay / dry-run / correction job (H5, H6), golden determinism test suite |

---

## 11. Acceptance Criteria

| ID | Criterion |
|---|---|
| AC-01 | A valid cancellation event submitted to `POST /cancellations` returns HTTP 202 within 2 seconds and appears in the archive within 90 seconds. |
| AC-02 | Resubmitting the same `event_id` with the same payload produces one decision record and no duplicate in the ticket state. |
| AC-03 | Resubmitting the same `event_id` with a different payload results in `MANUAL_REVIEW` with `R-CONFLICT` and an ops-alert email. |
| AC-04 | A malformed event (invalid `fare_amount` format) is rejected with HTTP 400 and does not appear in the archive. |
| AC-05 | A MANUAL_REVIEW decision triggers a reviewer notification email within 60 seconds containing no PII. |
| AC-06 | An APPROVE decision produces a message on the SQS payments queue within 60 seconds. |
| AC-07 | The Athena reconciliation query returns zero rows for the over-refund check on a known-clean test dataset. |
| AC-08 | The golden determinism test (`tests/golden/`) passes: same decisions across two live runs and one simulated replay run; sha256 of sorted decisions is identical across all three. |
| AC-09 | A DLQ alarm fires within 5 minutes of a deliberately poisoned event exceeding maxReceiveCount. |
| AC-10 | The dry-run replay job writes diffs to `S3 diffs/<run_id>/` and does not write to the queue or DynamoDB. |
| AC-11 | All log lines produced by the Lambda pass `python3 day2/scripts/check_logs.py` with zero violations. |
| AC-12 | No `fare_amount` or `refund_amount` values appear in any CloudWatch log group. |

---

## 12. Constraints and Assumptions

| Type | Detail |
|---|---|
| Cloud region | AWS `us-east-1` only (sandbox also allows `us-west-2`) |
| IaC | AWS SAM only; no console click-ops |
| Lambda count | Maximum 10 Lambda functions for the whole course; this pipeline uses 1 |
| Lambda memory | Maximum 2,048 MB; pipeline uses 512 MB |
| Bedrock quota | Maximum 50 calls / 20,000 tokens per learner (not on critical path for this pipeline) |
| S3 visibility | No public S3 buckets |
| Settlement timezone | UTC until Accelya confirms the production settlement timezone (`SettlementTz` parameter) |
| Booking reference | `ref/bookings.json` in labs; a PSS lookup in production |
| Currency allowlist | ISO-4217 from reference data; list to be provided by Accelya settlement team |
| High-value threshold | Per-currency threshold stored in reference data; changes require H5 dry-run review |
| SageMaker availability | Module 8 only; prior modules fall through to `R-NOMODEL` |

---

## 13. Open Items

| # | Item | Owner | Target |
|---|---|---|---|
| OI-01 | Confirm production settlement timezone (`SettlementTz`) | Accelya settlement team | Before Module 10 |
| OI-02 | Provide currency allowlist and high-value thresholds per currency | Accelya settlement team | Before Module 7 |
| OI-03 | Confirm production PSS lookup endpoint and authentication method | Accelya platform engineering | Before production deployment |
| OI-04 | Confirm maker-checker second threshold for high-value production refunds | Accelya risk / finance | Before production deployment |
| OI-05 | Confirm on-call email distribution list for `ops-alerts` subscription | Accelya platform engineering | Module 7 |
| OI-06 | Confirm reviewer email distribution list for MANUAL_REVIEW notifications | Accelya platform engineering | Module 7 |

---

## 14. References

| Document | Location |
|---|---|
| Reference architecture diagram | `docs/reference-architecture.png` |
| Reference architecture detailed spec | `docs/REFERENCE_ARCHITECTURE.md` |
| Logging contract | `docs/LOGGING_CONTRACT.md` |
| Logging contract schema | `docs/logging-contract.json` |
| Service boundary six questions | `docs/service-boundary-6q.md` |
| HITL review template | `docs/HITL_REVIEW_TEMPLATE.md` |
| Airline glossary | `glossary/airline.md` |
| Project coding rules | `AGENTS.md` |

---

*This BRD requires human review using `docs/HITL_REVIEW_TEMPLATE.md` before any module implementation begins.*
