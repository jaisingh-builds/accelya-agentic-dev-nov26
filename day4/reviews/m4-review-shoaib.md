# M4 peer review - shoaib reviews shoaib (self-review)

Use `docs/HITL_REVIEW_TEMPLATE.md` for the 5-dimension score. Paste the evidence below.

> **Status: provisional.** The author and the reviewer are the same person (the code was written with an AI
> assistant). `docs/REFERENCE_ARCHITECTURE.md` (H5) asks for a reviewer who is **not** the author. A neighbour
> should repeat this review against this branch before it counts as a peer review.
> The stack `accelya-m4-shoaib` was deleted at the end of Lab 4, so the "deployed" results below are from the
> runs on 2026-11-27 (account of the `accelya-ai` profile, us-east-1), not re-run.

## Evidence (paste the output, don't summarise it)

| Check | Command | Result |
|---|---|---|
| Tests | `python3 day4/tests/run_tests.py` | **22/23, structure OK.** structure 13/13, unit 13/13, logging 6/6, functional 3/4. The one failure, `test_zones_are_byte_identical_to_day3`, is Windows-only (the test builds keys with `\` and Day 3 writes CRLF sidecars). Checked separately with POSIX keys and CRLF normalised: 9/9 files byte-identical. |
| Logging contract (deployed) | `aws logs tail $LG --since 30m --format short \| python3 day2/scripts/check_logs.py -` | `Logging contract v1.1 · 7 application lines checked` → **CONFORMANT** (0 violations) |
| correlation_id on every line | `aws logs tail $LG --since 15m --format short \| grep "$L-d4-001" \| grep -c '"message"'` | **7 of 7** application lines carry `shoaib-d4-001` (platform lines INIT/START/END/REPORT are not application records) |
| Retention | `aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'` | before: `None` (never expire); after the redeploy: **14 days** |
| Manifest | `aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -` | `42 21 1 1 {'format': 9, 'required': 2, 'referential': 5, 'business': 3} balanced True` |

Also checked live:
- **Same file again (`d4-002`):** only `file.received` and `file.already_processed`. 11 objects before and 11 after; manifest timestamp unchanged.
- **No metadata plus wrong header:** `metadata.missing` and `file.quarantined` share one minted id (`m4-af78dd29-a25`). The whole file went to `quarantine/schema/`.

## Read the code (one line each)

1. **Transient vs bad record.** A transient S3 error (`SlowDown`, 5xx, throttling) goes through `_call` ([service.py:62-69](../src/processor/service.py#L62-L69)), becomes `RecoverableError`, is logged `file.retry` ([handler.py:72](../src/processor/handler.py#L72)) and re-raised, so Lambda retries twice and then the DLQ holds it (`template.yaml` DeadLetterQueue, `MaximumRetryAttempts: 2`). A bad record never raises: `classify()` routes it to `quarantine/<reason>/` with a sidecar and the run still ends `processed`. They are different paths.
2. **Payload in a log call?** No payload keys are logged. The widest values are `key` (the S3 object key, i.e. a file name chosen by the uploader, plus the zone and rule reason) and `record_count`, for example [service.py:234](../src/processor/service.py#L234). A file name could contain anything the uploader types, but it is an identifier, not a row or a field value.
3. **What makes a rerun a no-op.** [service.py:158-161](../src/processor/service.py#L158-L161): the stored manifest's `source_sha256`, `rules_sha256` and `ref_data_sha256` all equal the current ones, so it logs `file.already_processed` and returns before any `put_object`. Proven live (d4-002).
4. **One change before production.** The whole file is read into memory (`body.read()`, decode, a list of rows, a dict per row) with no size guard and no test beyond 42 rows. With files of up to ~100,000 records in the requirements, add a size limit or process the file as a stream, and test at 10x (peak memory was 95 MB of 256 MB for 42 rows).

## Score (0-3 per dim; PASS = at least 12/15 AND every dim at least 2)

| Dim | Score | Evidence |
|---|---|---|
| Correctness | 3 | Matches the docstring; edge cases tested (unreadable bytes, bad file name, missing key, throttling, AccessDenied, rerun, changed rules); 42/42 routed as expected; manifest balanced on S3. |
| Security | 2 | IAM is ARN-scoped with named verbs and an `s3:prefix` condition on `ListBucket`; writes only to output zones, never `raw/`; no secrets (config from environment); public access blocked, SSE on. Not 3: PII in zone files is not classified or tagged, and the managed `AWSLambdaBasicExecutionRole` policy uses a `Resource: *` for logs. |
| Observability | 2 | Powertools Logger, `correlation_id` on every line, classified errors (`file.retry`, `file.failed`), contract CONFORMANT (7 lines). Not 3: no metrics (the reference architecture chooses metric filters over EMF, which the contract rejects). |
| Scalability | 2 | Idempotent by hash, outputs written first and the manifest last (a crash means a clean retry). Not 3: not tested at 10x, and memory grows with file size (see item 4). |
| Cost | 2 | Event-driven Lambda at 256 MB, pay per file, 14-day log retention, no idle resources (and cleaned up afterwards). Not 3: no per-file cost measurement or sampling. |
| **TOTAL** | **11/15** | |

**Logging contract (from Day 2, mandatory):** `python3 day2/scripts/check_logs.py` over the deployed logs → **CONFORMANT**, 0 violations.

## Verdict
**REVISE (provisional).** 11/15 is below the 12/15 bar, although every dimension is at least 2. The single most important fix is **Scalability**: add a file-size guard or stream the rows, and prove it with a larger-than-42-row test, because memory grows with the file and the requirement mentions files of about 100,000 records.

## If REVISE: what to change
1. **Scalability** - stream the CSV or reject files above a stated size, and add a test with a generated 10x file. This alone would lift the total to 12/15 and clear the bar.
2. **Security** - decide whether the zone files hold PII and tag the bucket accordingly; replace the managed logging policy with one scoped to this log group if the course rule "no `Resource: *`" applies.
3. **Observability** - add metric filters (not EMF) for `file.quarantined` and `file.failed`, as the reference architecture does for the refund pipeline.

**Next iteration prompt addition:** "Process the file without loading all rows at once, or reject objects above a size limit given in the config; keep outputs byte-identical to Day 3."
