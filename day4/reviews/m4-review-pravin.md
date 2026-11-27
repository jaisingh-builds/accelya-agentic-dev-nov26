# M4 peer review - <reviewer-id> reviews pravin

Use `docs/HITL_REVIEW_TEMPLATE.md` for the 5-dimension score. Paste the evidence below.

> Pre-filled on 2026-11-27 from branch `day4-pravin1` and stack `accelya-m4-pravin` (us-east-1).
> `service.py` and `handler.py` were AI-generated (Claude), so an independent human reviewer must confirm
> the scores and the verdict and put their id in the title.

## Evidence (paste the output, don't summarise it)

| Check | Command | Result |
|---|---|---|
| Tests | `python3 day4/tests/run_tests.py` | 23/23, structure OK |
| Logging contract (deployed) | `aws logs tail $LG --since 30m --format short \| python3 day2/scripts/check_logs.py -` | CONFORMANT |
| correlation_id on every line | `aws logs tail $LG --since 30m --format short \| grep -c '"correlation_id"'` vs total lines | 13 of 13 |
| Retention | `aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'` | null days (never expires - Lab 4 step 6 not done yet) |
| Manifest | `aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -` | balanced: true |

```
$ python3 day4/tests/run_tests.py
  PASS  structure   structure: 13/13 checks pass
  PASS  unit        13/13
  PASS  logging     6/6
  PASS  functional  4/4

Day 4: 23/23 tests pass, structure OK  ->  READY TO DEPLOY

$ aws logs tail $LG --since 30m --format short | python3 day2/scripts/check_logs.py -
Logging contract v1.1  ·  13 application lines checked

CONFORMANT

$ aws logs tail $LG --since 30m --format short | grep -c '"correlation_id"'
13          # total application lines: 13

$ aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'
null

$ aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -
{
 "source_key": "raw/ticket_issue_AI_2026-11-25.csv",
 "source_sha256": "38dbf39b9b1b224cff0c5e534ecdaf702c189e22bcb72d256abc4d92e19e0d00",
 "business_date": "2026-11-25",
 "correlation_id": "pravin-d4-001",
 "rules_sha256": "2d708d4a5f75aaca",
 "ref_data_sha256": "fde83baa44c69ccb",
 "rows_in": 42,
 "file_route": "accepted",
 "validated": 21,
 "flagged": 1,
 "duplicates": 1,
 "quarantined": {
  "format": 9,
  "required": 2,
  "referential": 5,
  "business": 3
 },
 "balanced": true
}
```

## Read the code (one line each)

1. A **transient** S3 error (SlowDown/throttling/5xx) becomes `RecoverableError` (`service.py:243`), the handler logs `ERROR file.retry` and re-raises (`handler.py:73`), so Lambda retries twice and then sends it to the DLQ; a **bad record** never raises - it is routed to `quarantine/<reason>/` with a sidecar (`service.py:167`); yes, different paths.
2. `handler.py:69` `file.received` logs `bucket`, `key`, `bytes` - none is a payload field; `key` is the object name (`raw/ticket_issue_AI_2026-11-25.csv`, airline code + business date), an identifier, though a key with spaces or other characters would break the contract's `key` pattern.
3. `service.py:107`: the stored manifest's `source_sha256` + `rules_sha256` + `ref_data_sha256` all equal this run's, so it logs `file.already_processed` and returns before any `put_object`; the manifest is written last, so a crash mid-file is retried, not skipped.
4. Stream the file instead of `get_object(...)["Body"].read()` (`service.py:90`): the whole file plus its parsed rows sits in 256 MB of memory, so a large settlement file would run out of memory and go to the DLQ instead of being processed.

## HITL score (suggested - reviewer to confirm)

| Dim | Score | Evidence |
|---|---|---|
| Correctness | 3 | Matches the docstring; unit tests cover schema, unreadable, filename, missing, ignored, duplicate, throttling, AccessDenied, idempotency, changed rules; 42/42 routed and zone files byte-identical to Day 3. |
| Security | 2 | IAM is named actions on ARN-scoped resources (GetObject/ListBucket/PutObject on the bucket, SendMessage on the DLQ); no secrets or account ids in code; no Conditions, PII not tagged. |
| Observability | 2 | Powertools Logger shaped to the contract, correlation_id on 13/13 lines, CONFORMANT, classified errors (`error_type`, `error_code`); no metrics (no EMF / metric filters) yet. |
| Scalability | 2 | Idempotent (manifest check, manifest written last); maintainable; memory not bounded for large files (whole object read into memory); not tested at 10x. |
| Cost | 2 | Lambda + S3 suit a small daily batch; log retention is never-expire (null) and there is no cost-per-record measure. |
| **TOTAL** | **11/15** | |

**Logging contract (mandatory):** `python3 day2/scripts/check_logs.py -` → CONFORMANT, 0 violations

## Verdict
REVISE - set `RetentionInDays: 14` on `ProcessorLogGroup` in `day4/template.yaml` and redeploy (Lab 4 step 6): logs that never expire are an unbounded cost, and retention is a checked line in this review. 11/15 is below the PASS bar of 12; this fix plus a metric filter for `file.quarantined` / `file.retry` should get there.
