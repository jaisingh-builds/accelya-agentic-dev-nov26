# Weekly Status Report — Airline Settlement Platform

**Learner:** chandra · **Week ending:** 2026-11-25 · **Region:** us-east-1 (sandbox)

## Summary
- Landing-zone ingest pipeline is **deployed and verified live in AWS**; two further Lambda handlers are built, fully tested offline, and pending review/deployment. No Bedrock spend beyond Lab 1; no cost anomalies.

## Delivered this week
- **csv-count stack live:** `accelya-m1-chandra-csv-count` (Lambda + S3 + IAM + CloudWatch) — S3 `raw/*.csv` uploads trigger a row counter; verified end-to-end with a real upload (`record_count: 25`, `correlation_id: lz-001`).
- **Landing zone hardened:** `raw/` prefix on a dedicated bucket with versioning, BucketOwnerEnforced, AES256, TLS-only bucket policy, and lifecycle rules (30-day expiry, 7-day noncurrent cleanup, 1-day abort of incomplete multipart uploads).
- **Row-counter quality:** streams via `csv.reader` (multi-line quoted fields count correctly); distinct error paths — retriable S3 errors re-raise for Lambda retries, deterministic parse errors logged once, no wasted retries.
- **Cancellation refund handler** (`day2/src/sample-demo/`): passes 6/6 harness checks — rule ordering, `batchItemFailures`, logging contract, PII scan, per-record correlation IDs (minted when absent).
- **Ticket refund window handler** (`day2/src/ticket-refund/`): <6 h → reject; ≥7 d → 90 %; ≥2 d → 50 %; departed → reject. Passes 12/12 local checks including exact boundary cases.
- **SAM templates for both**: lint-clean; standard SQS + DLQ (maxReceiveCount 3), `ReportBatchItemFailures`, roles scoped to their own queue and log group.
- **Cleanup script** for the versioned landing bucket (deletes object versions, then the stack).

## Design decisions
- IAM roles use named actions on ARN-scoped resources — no `Resource: "*"` or managed `AWSLambdaBasicExecutionRole`.
- One stack per function: S3 rejects overlapping `raw/` notifications on a shared bucket; Lambda count stays within the 10-function limit.
- All logging verified against the v1.1 contract — identifiers and counts only, never bodies, names, or fare amounts.

## Issues & risks
- **Open question:** the 6 h–2 day refund window is unspecified — currently MANUAL_REVIEW; needs a business decision (propose 25 %).
- **Credentials:** sandbox `accelya` profile keys expired — reconfigure before deploys.
- **Windows terminal quirk:** UCRT64 rewrites `/aws/...` arguments into local paths; fix exported as `MSYS2_ARG_CONV_EXCL='*'` — worth adding to `env.sh` for Windows learners.

## Next week
- Deploy `sample-demo` (SQS → Lambda + DLQ) and run end-to-end fixture tests.
- Confirm the 6 h–2 d rule, then deploy `ticket-refund`.
- Complete HITL review before merge; learner builds the Lab 3 stub independently.

**Compliance:** naming, region, IAM scope, log contract, Decimal money, and no-public-bucket rules all verified — nothing merged without human review.
