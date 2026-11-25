## Business requirement (one line, from the business)
Every time an agency uploads a daily ticket-issue file, finance must see how many tickets arrived, per airline, within 5 minutes.

## Technical prompt

**Role:** You are a senior AWS solutions architect designing for Accelya's airline settlement platform on AWS (region `us-east-1` only). Use airline vocabulary (`pnr`, `ticket_number`, `airline_code`, `agency_iata`, `settlement_date`); never generic names like `order` or `user`.

**Input:** CSV file `ticket_issue_<airline_code>_<issue_date>.csv` (e.g. `ticket_issue_AI_2026-11-23.csv`), ~2 KB (~25 rows), UTF-8, one header row. Columns: `pnr, ticket_number, airline_code, fare_class, od_pair, issue_date, fare_amount, currency, agency_iata`. A file may contain more than one `airline_code`, so count from the column, not only the file name.

**Arrival:** ~1,000 files/day, about 42 files/hour on average; assume bursts up to 5x (~200 files in a peak hour). Files land in the S3 `raw/` prefix; uploaders may set `x-amz-meta-correlation-id`.

**Must:**
1. Keep the original file byte-for-byte unchanged in `raw/` (audit evidence): no rewrite, rename or delete.
2. Make ticket counts per `airline_code` visible to finance within 5 minutes of upload (say where finance looks).
3. Be idempotent: a retry or duplicate S3 event must not double-count.
4. Carry a correlation ID across every hop, from upload to the per-airline count.

**Must not:**
1. Never log PII or commercial data: no `pnr`, `ticket_number`, `fare_amount`, row contents or full payloads. Log identifiers (bucket, key, `airline_code`) and counts only.
2. No servers to patch, no public S3 buckets, no `"Action": "*"` / `"Resource": "*"` in IAM.

**Constraints:** serverless only, defined in AWS SAM. Sandbox: max 10 Lambda functions for the whole course, at most 100 concurrent Lambda executions (enforce with reserved concurrency), memory <= 2048 MB, max 50 Bedrock calls / 20,000 tokens per learner.

**Output wanted:**
1. Two distinct architecture options (e.g. S3 -> Lambda -> DynamoDB counter vs. S3 -> SQS -> Lambda with batching), each with a one-line hop diagram.
2. For **every hop**, a table answering the six service-boundary questions (`docs/service-boundary-6q.md`): push/pull, sync/async + timeout, payload shape, IAM role (minimum actions, ARN-scoped resources), failure path (retries, DLQ/quarantine), correlation ID.
3. A comparison: latency vs the 5-minute target, behaviour at peak under the 100-concurrency cap, Lambda count vs the 10-function budget, rough cost, operational risk.
4. A recommendation naming the main trade-off, plus open questions for the business.
