# HITL_REVIEW - m1-arch-options (our solution)

> Review of `day1/outputs/m1-arch-options-output.md` (Option A, built and deployed), the answer to
> `day1/samples/m1-arch-options-solution.md`. Scored one dimension at a time; pending rows are filled in
> as each is reviewed. Synthetic data.

---

**Artefact:** `day1/outputs/m1-arch-options-output.md` - Option A (the recommended and built option)
**Reviewer:** `shoaib`
**Date:** 2026-11-24
**Module:** `M1`
**Lab:** `lab-1 (architecture options)`

---

## The score (0-3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim            | Score | Evidence (one line per dim)                          |
|----------------|-------|------------------------------------------------------|
| Correctness    | 2     | Counts match fixtures (25/18/12), re-sends overwrite, FAILED rows exist; mixed-airline files and DLQ'd files are not handled, no production-size test |
| Security       | 2     | Least-privilege role (read `raw/*` only), no PII in logs, bucket locked down; no assumed cross-account role, and the 30-day version expiry conflicts with the 7-year audit rule |
| Observability  | 2     | JSON logs with `correlation_id` on every line and no row data; no classified `error_type`, no alarms or metrics |
| Scalability    | 2     | Streams the file (bounded memory), idempotent, queue-buffered with DLQ, on-demand table; no load test, no concurrency or throttling plan, no read-side index |
| Cost           | 2     | Compute is right-sized and near zero (one Lambda, on-demand, no always-on parts); the real cost is 7-year S3 storage, which has no tiering, and cost per file is not tagged or measured |
| **TOTAL**      | _pending_ |                                                  |

**Logging contract (from Day 2, mandatory):** kept. One JSON object per line, `correlation_id` on every
line, file name, airline, count and status only. Checked by `day1/tests/test_count_rows.py` against the
three fixtures; not yet checked in CloudWatch after a deploy.

**Verdict:** _pending (all dimensions must be scored)_

---

## Per-dimension detail

### Observability - `2/3`

- Meets:
  - Correlation: `correlation_id` (`m1-<request id>-<event id>`) is on every application log line and
    is also stored on the DynamoDB item, so one file can be followed from queue message to logs to
    result row.
  - Logging contract: one JSON object per line with file name, airline, row count, status and duration.
    No fare_amount, PNRs, ticket numbers or row contents; the fixture test asserts this.
  - Failures are visible: a bad file logs an `ERROR` `count_rows.bad_file` line and writes a FAILED row
    (0 rows); a stale event logs a `WARNING` `count_rows.stale_event_ignored`. Log retention is explicit
    (7 days), not "Never Expire".
- Not 3:
  - Errors are not classified: the failure line carries a free-text `reason`, not a fixed `error_type`
    (for example `HeaderMismatch`, `EmptyFile`, `BadFileName`).
  - No metrics or alarms: nothing alerts when a file fails or a message reaches the main queue's DLQ or
    the rule DLQ, so a DLQ can fill silently.
  - `count_rows.start` does not carry `airline_code` (it is parsed later), and the correlation ID is
    minted per run rather than taken from an uploader-set `x-amz-meta-correlation-id`, so a re-sent file
    gets a new ID each time.
  - Logging has only been exercised locally against fixtures, not observed in CloudWatch after a deploy.

#### To reach 3 (Observability)

1. Add `error_type` to every ERROR line and use a small fixed set of values.
2. Add alarms on the queue's oldest-message age and on both DLQs (visible messages > 0).
3. Put `airline_code` on `count_rows.start`.
4. Prefer an uploader-supplied `correlation-id` metadata value when present, else mint one.
5. After the next deploy, upload a fixture and confirm the real log lines in CloudWatch.

### Correctness - `2/3`

- Meets:
  - Row counts are right: the fixtures give AI = 25, BA = 18, EK = 12 (`test_count_rows.py`, 11/11 pass).
    The `csv` module handles quoted newlines and a BOM, and blank rows and the header are not counted.
  - Re-sent file ("same name, must not be double-counted"): the item key is `<airline>#<file date>`, so a
    re-send overwrites instead of adding. A duplicate S3 event writes the same value again; an older
    event cannot overwrite a newer one (conditional write on `updated_at`).
  - Failed files: wrong name, wrong header, empty file, bad encoding or bad CSV syntax write
    `status = FAILED`, `row_count = 0`, with a `reason`, and are not retried.
  - 5 minutes: the path is event, queue, one Lambda, one write, so seconds in principle.
- Not 3:
  - The per-airline count comes from the file name, not the data. The `airline_code` column is never
    checked, so a file named `..._EK_...` that contains AI rows is counted as EK and reported OK.
  - A file whose processing keeps failing for a transient reason ends in the DLQ with no row at all,
    which breaks "finance sees every file, including failed ones". Nothing writes FAILED in that case.
  - Edge cases are undefined: a header-only file is recorded as OK with 0 rows (arguably it should be
    flagged), and any 2 to 3 character airline code in the name is accepted.
  - Never run at the stated size (about 20 MB, 100,000 rows) or against the deployed stack; the 5-minute
    claim and the 30 s timeout are reasoning, not measurement. The sample files are 12 to 25 rows.

#### To reach 3 (Correctness)

1. Compare the file-name airline with the `airline_code` column; mismatch -> `FAILED` with a reason.
2. Make DLQ'd files visible: a small handler on the DLQ that writes a `FAILED` row, or an alarm plus a
   documented replay step.
3. Decide and document header-only and unknown-airline behaviour (for example header-only ->
   `FAILED, EmptyFile`; allow-list of airline codes).
4. Generate a 100,000-row file, upload it to the deployed bucket and record the actual duration.

### Security - `2/3`

- Meets:
  - Least privilege on the `count-rows` role: `s3:GetObject` / `GetObjectVersion` on `raw/*` only (no
    wildcard over the bucket), `sqs:ReceiveMessage` / `DeleteMessage` / `GetQueueAttributes` on its own
    queue, `dynamodb:PutItem` on one table, and `logs:CreateLogStream` / `PutLogEvents` on its own log
    group. No `s3:Put*` or `Delete*`, so processing cannot modify or delete the original.
  - Event path is scoped: the queue policy lets `events.amazonaws.com` send only when `aws:SourceArn` is
    this stack's rule. A forged event naming another bucket cannot be read, because the role cannot
    read it.
  - Data protection: bucket has public access blocked, AES256, versioning and a TLS-only policy; both
    queues use SQS-managed encryption; the table has encryption and point-in-time recovery.
  - PII: logs and the results table hold file name, airline, count and status only. No fares, PNRs,
    ticket numbers or row contents, and the error reason carries the exception type, not the data.
  - Input checks: the file name must match a strict pattern and the header must match exactly before any
    row is counted.
- Not 3:
  - No scoped cross-account read role. The prompt says the airline data team owns the bucket and our
    code reads it through a scoped role, not bucket-wide credentials. Here the Lambda reads with its
    own execution role because the stack owns the bucket. The `reader` role with an ExternalId exists
    only for the Lab 1 probe and is not used by `count-rows`; its trust default is `TrustMode=open`
    (no ExternalId).
  - Audit retention conflict: the lifecycle rule expires non-current versions after 30 days, so an
    earlier copy of a re-sent file is deleted after 30 days. The requirement is the original kept
    unchanged for 7 years. There is also no Object Lock or delete-deny policy protecting `raw/`.
  - Encryption is AWS-managed keys only (no customer-managed KMS key), and there are no data
    classification tags or conditions such as `aws:SourceAccount` on the roles' trust and queue policies.
  - Not verified against the deployed account (for example with IAM Access Analyzer or a deliberate
    denied-access test); the review is of the template and code.

#### To reach 3 (Security)

1. Read through an assumed role limited to `s3:GetObject` on `raw/*`, with an `ExternalId` condition
   (or state in the doc why the same-account role is acceptable for this sandbox).
2. Fix retention: remove or lengthen the non-current expiry to 7 years, and add Object Lock or a bucket
   policy that denies `s3:DeleteObject*` on `raw/`.
3. Add `aws:SourceAccount` to the SQS queue policy and a customer-managed key if policy requires it.
4. Tag the table and bucket with data classification, and confirm with a denied-access test after deploy.

### Scalability - `2/3`

- Meets:
  - Bounded memory: the object is streamed line by line, so a 20 MB / 100,000-row file does not need to
    fit in the 256 MB function memory.
  - Idempotent: S3 and EventBridge deliver at least once, and a re-send reuses the name. The key
    `<airline>#<file date>` plus the conditional write makes duplicates and re-sends harmless, and an
    out-of-order older event cannot overwrite a newer one.
  - Burst handling: the expected pattern is about 40 files mostly between 00:00 and 03:00 IST. The
    queue absorbs the burst, Lambda scales with the queue, and the on-demand table needs no capacity
    planning. There is no hot key (one item per airline and day).
  - Sensible limits: queue visibility timeout 180 s against a 30 s function timeout (at least 6x),
    BatchSize 1 so one bad file cannot block others, DLQ after 3 receives, rule retries for up to 1 hour.
- Not 3:
  - Nothing measured. The fixtures are 12 to 25 rows; no run at about 100,000 rows, no 10x test (400
    files, or 40 files at once), so the 30 s timeout, 256 MB memory and 5-minute claim are untested.
  - Throttling is not planned for. No reserved concurrency is set and a sandbox account's concurrency
    limit may be low. With the queue as the source, throttled deliveries count as receives, so a burst
    can push healthy files to the DLQ after 3 attempts although nothing is wrong with them.
  - The read side does not scale: finance can only look up a known `<airline>#<date>` key. "All
    airlines for one date" needs a Scan, because there is no index on `file_date`.
  - Table growth is small (about 15,000 items a year at 40 airlines) but has no TTL or archive plan.

#### To reach 3 (Scalability)

1. Run a 100,000-row file and a 10x burst against the deployed stack; record duration, memory used and
   any throttles or DLQ messages.
2. Raise `maxReceiveCount` (for example 5) and set the event source mapping's `MaximumConcurrency` so
   throttling does not consume receives; check the account's Lambda concurrency limit.
3. Add a global secondary index on `file_date` for the "all airlines for a day" view.
4. State the growth plan (retention or archive) for the results table.

### Cost - `2/3`

Estimates below are reasoning from the prompt (about 40 files a day, about 20 MB each) and public AWS
list prices, not measured bills. Treat them as orders of magnitude.

- Meets:
  - Nothing runs idle: one Lambda, SQS, EventBridge, DynamoDB on demand. No servers, no NAT gateway, no
    provisioned capacity, no Glue or Step Functions. Option B was correctly judged more expensive.
  - Compute is tiny: about 1,200 invocations a month at a few seconds each on 256 MB is far below the
    Lambda free tier. SQS (about 1,200 messages), DynamoDB writes (about 1,200 a month), and the
    EventBridge rule (S3 service events on the default bus) are each negligible.
  - Log cost is bounded: a few JSON lines per file, 7-day retention.
- Not 3:
  - The real cost driver is storage, not compute. 40 files x 20 MB is about 0.8 GB a day, so keeping the
    originals for 7 years reaches about 2 TB. In S3 Standard that is roughly $45 a month by year 7,
    against well under $1 a month for all compute. The architecture doc names Lambda duration as the
    main driver; that is wrong for the 7-year view.
  - No storage tiering: nothing moves `raw/` to a cheaper class (Glacier or Deep Archive) after the
    first weeks, which is the main saving available for audit data kept 7 years.
  - Cost per file is not measurable yet: no cost-allocation tags on the bucket, table, queue and
    function, and no budget or billing alarm.
  - Lambda memory (256 MB) and timeout (30 s) were chosen, not tuned against a real 100,000-row run.
  - Versioning keeps a re-sent file's earlier copy, which adds storage; the lifecycle rule that expires
    those copies after 30 days saves money but conflicts with the 7-year rule (see Security).

#### To reach 3 (Cost)

1. Add a lifecycle transition for `raw/` (for example to Glacier Instant Retrieval or Deep Archive after
   30 to 90 days) and decide the retention that satisfies audit, so cost and the 7-year rule agree.
2. Correct the architecture doc: main cost driver is 7-year S3 storage, with a rough monthly figure.
3. Tag every resource (for example `project`, `learner`, `data-class`) and add a monthly budget alarm.
4. After the 100,000-row run, tune memory and timeout and record the cost per file.

---

## If REVISE: what to change

_Filled in once every dimension is scored. Observability items so far: see "To reach 3 (Observability)"._

---

*Pass bar: ≥12/15 AND every dim ≥2.*
