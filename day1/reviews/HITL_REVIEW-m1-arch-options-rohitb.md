# HITL_REVIEW - m1-arch-options

---

**Artefact:** `day1/outputs/m1-arch-options-rohitb.md` - Option 1 and Option 2
**Model:** `us.anthropic.claude-opus-4-1-20250805-v1:0` (tokens in/out 416/1200 - output hit the 1,200 cap and is truncated)
**Reviewer:** `rohitb`
**Date:** 2026-11-24
**Module:** `M1`
**Lab:** `lab-1 (architecture options)`

Scored against the prompt `day1/prompts/m1-arch-options.md`: ~50 files/day of ~5 KB, per-airline count visible
within 2 minutes, raw file kept unchanged, a re-uploaded file processed only once, serverless only, no PII in logs.

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Option 1 | Option 2 | Evidence (one line per dim)                                                                 |
|----------------|----------|----------|---------------------------------------------------------------------------------------------|
| Correctness    | 2        | 1        | Opt 1 meets the musts incl. re-upload once, but its aggregator query design is flawed; Opt 2 double-counts re-uploads and is truncated |
| Security       | 2        | 2        | Both: per-function roles, named verbs, no wildcards; no ARN scoping, no Conditions, PII not addressed |
| Observability  | 1        | 1        | Both: no log design, no classified errors, correlation_id changes at every hop               |
| Scalability    | 1        | 0        | Opt 1 idempotent but aggregator read is unbounded; Opt 2 non-idempotent and over-built       |
| Cost           | 2        | 0        | Opt 1 pay-per-use services but polls 24/7; Opt 2 Kinesis is the wrong service for 50 files/day |
| **TOTAL**      | **8/15** | **4/15** |                                                                                             |

**Logging contract (from Day 2, mandatory):** design artefact, no logs yet. Neither option specifies a log format
or one end-to-end correlation_id, so Observability is capped at 1 for both.

**Verdict:** Option 1 - REVISE (Observability and Scalability below 2). Option 2 - REVISE (three dims below 2).
**Decision:** take **Option 1** forward and revise it. It is the only one that meets the re-upload rule and fits the volume.

---

## Hops reviewed

| Option | Hop | Note |
|---|---|---|
| 1 | S3 → Lambda (Parser) | correlation_id "ETag stored in Lambda context" - Lambda context does not carry it |
| 1 | Lambda (Parser) → DynamoDB | conditional check on file_hash gives the re-upload-once rule |
| 1 | DynamoDB → EventBridge | listed in the services line but has no hop table; EventBridge here is a schedule, not fed by DynamoDB |
| 1 | EventBridge → Lambda (Aggregator) | runs every minute, 24/7 |
| 1 | Lambda (Aggregator) → SNS | aggregates "last 5 minutes" while the prompt asks for 2 |
| 2 | S3 → Lambda (Validator) | ETag used as Kinesis partition key |
| 2 | Lambda (Validator) → Kinesis | partition key does not deduplicate |
| 2 | Kinesis → Lambda (Processor) | IAM lists only `kinesis:GetRecords` |
| 2 | Lambda (Processor) → CloudWatch | correlation_id "timestamp + airline_code" is not an id |

---

## Per-dimension detail

### Correctness - Option 1 `2/3` · Option 2 `1/3`

**Why it matters:** if the design does not meet the business requirement, nothing else counts.

**Anchors:** 3 = spec + edge cases · 2 = spec, happy path only · 1 = spec but missing field or wrong API · 0 = hallucinated APIs

**Option 1 = 2**
- Meets: per-airline counts, within 2 minutes (parse in seconds + up to a 1-minute tick), re-upload once via a
  DynamoDB conditional check on `file_hash`.
- Not 3: no edge cases (empty file, header-only file, unknown airline code). The aggregator does
  `dynamodb:Query` for "the last 5 minutes", but the partition key is `file_hash`, so a time-window Query is not
  possible without a GSI or a Scan. The window is also 5 minutes, not the prompt's 2.
- Not 1: all APIs exist and no required field is missing; the Query problem is a key-design gap, not a wrong API name.

**Option 2 = 1**
- Meets the happy path: per-airline `TicketCount` metric within 2 minutes.
- Misses the hard "must": a re-uploaded file is counted again. `file_hash` is only a Kinesis partition key, which does
  not deduplicate, so `PutMetricData` runs twice.
- IAM for the Kinesis event source mapping is incomplete: it needs `kinesis:GetRecords`, `GetShardIterator`,
  `DescribeStream`, `ListShards`.
- Output truncated: risk 2 and the comparison table are missing.
- Not 2: a hard requirement is unmet. Not 0: every service and feature named is real.

---

### Security - Option 1 `2/3` · Option 2 `2/3`

**Why it matters:** IAM and data-leak mistakes are the hardest to undo after go-live.

**Anchors:** 3 = ARN-scoped + named verbs + Conditions; PII tagged · 2 = no wildcards; PII not addressed · 1 = some wildcards · 0 = `*`

**Both = 2**
- One role per Lambda with named actions (`s3:GetObject`, `dynamodb:PutItem`, `sns:Publish` /
  `kinesis:PutRecord`, `cloudwatch:PutMetricData`). No wildcards anywhere.
- Not 3: no resource ARNs (e.g. `raw/*`, the table ARN, the topic ARN), no Conditions (e.g. `cloudwatch:namespace`),
  no encryption mentioned (SNS topic, Kinesis stream), and the prompt's no-PII rule is not addressed.
- Not 1: no wildcards are present.

---

### Observability - Option 1 `1/3` · Option 2 `1/3`

**Why it matters:** when finance says "BA's count is wrong", you must trace that one file through every hop.

**Anchors:** 3 = Powertools Logger + correlation_id + EMF + classified errors · 2 = JSON logs + classified errors · 1 = JSON logs only · 0 = `print` / silent `except`

**Option 1 = 1**
- correlation_id is different at every hop: ETag, then `file_hash`, then "rule name + timestamp", then an
  aggregation-window timestamp. One file cannot be followed end to end.
- Only signal: a CloudWatch alarm on Lambda errors. No log format, no `error_type`.
- Not 2: no JSON log design and no classified errors. Not 0: nothing is silently swallowed; errors raise an alarm.

**Option 2 = 1**
- Same broken correlation chain: ETag, then `file_hash`, then Kinesis sequence number, then "timestamp + airline_code".
- Plus: a per-airline metric and alarms. Still no log format and no classified errors.
- Not 2 / not 0: same reasons as Option 1. (Arguable 2 for the metrics; kept at 1 because the correlation chain is broken.)

---

### Scalability - Option 1 `1/3` · Option 2 `0/3`

**Why it matters:** retries and duplicate deliveries are normal on AWS; a non-idempotent design drifts as volume grows.

**Anchors:** 3 = idempotent + bounded + tested at 10x · 2 = idempotent + bounded · 1 = idempotent only · 0 = unbounded, non-idempotent, or over-abstracted

**Option 1 = 1**
- Idempotent: the conditional write on `file_hash` turns a duplicate S3 event or an identical re-upload into a no-op.
- Not 2: the aggregator reads a table that only grows (no time-keyed index, no TTL), so each run reads more over time.
  Its named risk (hot partition) is wrong: `file_hash` spreads writes evenly. The real risk is the growing read.
- Not 0: it is idempotent and not over-abstracted.

**Option 2 = 0**
- Not idempotent: a re-upload, or a duplicate Kinesis delivery, adds to the metric again.
- Over-built: a streaming pipeline for ~50 files of ~5 KB a day.
- Not 1: anchor 1 requires idempotency. Anchor 0 is "non-idempotent OR over-abstracted"; it is both.

---

### Cost - Option 1 `2/3` · Option 2 `0/3`

**Why it matters:** in the sandbox and in production, an always-on service bills every hour even when nothing arrives.

**Anchors:** 3 = right service + sampled + cost-per-record measurable · 2 = right service, no sampling discipline · 1 = right service, cost not considered · 0 = wrong service for the workload

**Option 1 = 2**
- Lambda, DynamoDB on-demand, an EventBridge schedule and SNS are all pay-per-use and fit 50 files a day.
- Not 3: nothing measures cost per file, and the aggregator runs every minute, 24/7 (~43,000 invocations a month),
  while files arrive in a 2-hour window. Cheap, but not disciplined.
- Not 1: the services chosen are the workload-appropriate pay-per-use ones. (Arguable 1, since the answer never
  discusses cost.)

**Option 2 = 0**
- Kinesis Data Streams bills per shard-hour (or per stream-hour on-demand) around the clock - roughly $10-30 a month
  to carry ~50 tiny records a day.
- The Validator Lambda could call `PutMetricData` itself, removing Kinesis and one of the 10 allowed Lambdas.
- Not 1: anchor 1 needs the right service for the workload; Kinesis is not.

---

## If REVISE: what to change (Option 1)

1. **Scalability / Correctness** - Key the DynamoDB item on `airline_code` + `settlement_date` (or add a GSI on
   upload time) so the aggregator reads one small window instead of the whole table. Add a TTL to old items.
   Keep the conditional write on `file_hash` for the re-upload rule.
2. **Observability** - Mint one `correlation_id` per file (object metadata, else minted by the Parser), carry it on
   the DynamoDB item and the SNS message attribute, and log it on every line. JSON logs per the logging contract;
   ERROR lines carry `error_type`.
3. **Security** - Scope `s3:GetObject` to `raw/*` and each DynamoDB/SNS action to its own ARN; encrypt the SNS topic.
4. **Correctness** - Use a 2-minute aggregation window, matching the prompt, and name the edge cases
   (empty file, header only, unknown airline code).

**Next iteration prompt addition:**
> Revise Option 1 only. Keep one correlation_id per file from upload to the SNS message. Key DynamoDB so the
> aggregator reads only the last 2 minutes, with a TTL on old items. Scope every IAM action to its resource ARN.
> Name the edge cases. Max 600 words so the answer is not truncated.

---

*Pass bar: ≥12/15 AND every dim ≥2.*
