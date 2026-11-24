# HITL_REVIEW - m1-arch-options (sample solution)

> **Worked example.** A completed review of `day1/samples/m1-arch-options-sample-output.md`, the answer
> to `day1/samples/m1-arch-options-solution.md`. Use it to calibrate your own scores. Synthetic data.

---

**Artefact:** `day1/samples/m1-arch-options-sample-output.md` - Option A (the recommended option)
**Reviewer:** `trainer`
**Date:** 2026-11-24
**Module:** `M1`
**Lab:** `lab-1 (architecture options)`

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 2     | Per-airline counts and FAILED status meet the spec; the re-sent-file "must" is not designed |
| Security       | 1     | `s3:GetObject` on `<bucket>/*` instead of `raw/*`; no scoped cross-account role as the prompt required |
| Observability  | 1     | Plans to log the first 5 rows (PNRs, fares); correlation ID minted but not on every log line |
| Scalability    | 1     | Streams the file (bounded memory) but not idempotent: S3 can deliver an event twice and re-sends double-count |
| Cost           | 3     | Right-sized: one Lambda per file and ~40 on-demand writes a day; cost per file is measurable |
| **TOTAL**      | 8/15  |                                                      |

**Logging contract (from Day 2, mandatory):** design artefact, no logs yet. The design **breaks the
contract by intent** (row payloads in logs), so Observability is capped at 1.

**Verdict:** REVISE

---

## Per-dimension detail

### Correctness - `2/3`

- Meets: counts per `airline_code`, 5-minute target (a 20 MB stream counts in seconds), failed files
  recorded as `row_count = 0, status = FAILED`.
- Misses: the prompt's "a re-sent file has the same name and must not be double-counted". The
  comparison table admits it ("not addressed") but still recommends the option.
- Not 3: no edge cases named (empty file, header only, unknown airline code, mixed airlines in one file).

### Security - `1/3`

- `s3:GetObject` on `arn:aws:s3:::<bucket>/*` is a wildcard over the whole bucket; scope it to `raw/*`.
- The prompt says the bucket belongs to the airline data team and is read through a scoped role.
  Option A never assumes one (Day 1 lab: STS AssumeRole with an ExternalId condition).
- PII: logging sample rows puts PNRs and fares in CloudWatch. That's a security finding as well as an
  observability one.

### Observability - `1/3`

- "Log the first 5 rows for debugging" breaks the logging contract and the prompt's "never log PNR
  lists or fare_amount". Counts and file names only.
- Correlation ID: minted per file, which is right, but the answer only stores it on the DynamoDB item.
  It must be on every application log line too.
- Not 2 yet: no classified errors (`error_type`) for the FAILED path, and no metric finance can alarm on.

### Scalability - `1/3`

- Bounded memory: yes, it streams the object.
- Idempotency: no. S3 event notifications are at-least-once, and a re-sent file reuses the key.
  A plain PutItem per airline overwrites or duplicates depending on the key design, and the answer
  doesn't say which.
- The "15-minute limit" risk is not real at 20 MB; the real risk (duplicates) is missing. Naming the
  wrong risk is a review finding in itself.

### Cost - `3/3`

- Lambda + DynamoDB on demand is the workload-appropriate choice for ~40 small files a day.
- Option B correctly flagged as most expensive: Glue's minimum billing per run and start-up time
  are wasted on a row count. The answer also correctly sees B's 5-minute risk.

---

## If REVISE: what to change

1. **Scalability / Correctness** - Key the DynamoDB item on `file_key + eTag` and write with
   `ConditionExpression: attribute_not_exists(pk)`. A duplicate event or an identical re-send becomes a
   no-op; a re-send with new content gets a new eTag and a new row marked `status = RESENT`.
2. **Security** - Read through the airline team's scoped role (`sts:AssumeRole` with `ExternalId`),
   and limit it to `s3:GetObject` on `raw/*`.
3. **Observability** - Delete the "first 5 rows" logging. Log `file_key`, `airline_code`,
   `row_count`, `status` and `correlation_id` on every line; ERROR records carry `error_type`
   (e.g. `HeaderMismatch`, `EmptyFile`).
4. **Failure path** - Add an on-failure destination (SQS) for the async invoke, so a file that fails
   three times is visible rather than lost.

**Next iteration prompt addition:**
> Revise Option A only. Make it idempotent for duplicate S3 events and re-sent files (say exactly
> which key and condition you use). Read the bucket through a scoped cross-account role limited to
> `raw/*`. Never log row contents. Give the async failure path an on-failure destination. Keep it
> serverless and under 10 Lambda functions.

---

## Expected re-score after the revision

| Dim | Before | After | Why |
|---|---|---|---|
| Correctness | 2 | 3 | Re-send rule designed; edge cases named |
| Security | 1 | 2 | Scoped role, `raw/*` only, no PII in logs (3 needs the PII tagging and conditions in code) |
| Observability | 1 | 2 | Contract-conformant JSON logs + classified errors; metrics come in Module 9 |
| Scalability | 1 | 2 | Idempotent + bounded memory (3 needs a 10x test) |
| Cost | 3 | 3 | Unchanged |
| **Total** | **8** | **12** | **PASS** (≥12 and every dimension ≥2) |

**Teaching point:** the model recommended the right option for the wrong reasons. The cost call was
spot on; the "must" it skipped (re-sent files) is the one that would double-count money in production.
That's why a human scores it.

---

*Pass bar: ≥12/15 AND every dim ≥2.*
