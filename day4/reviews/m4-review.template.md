# M4 peer review - vikramsing reviews <author-id>

Use `docs/HITL_REVIEW_TEMPLATE.md` for the 5-dimension score. Paste the evidence below.

## Evidence (paste the output, don't summarise it)

| Check | Command | Result |
|---|---|---|
| Tests | `python3 day4/tests/run_tests.py` | 23/23, structure OK (structure 13/13, unit 13/13, logging 6/6, functional 4/4) |
| Logging contract (deployed) | `aws logs tail $LG --since 30m --format short \| python3 day2/scripts/check_logs.py -` | (not run yet - needs the deployed log group) |
| correlation_id on every line | `aws logs tail $LG --since 30m --format short \| grep -c '"correlation_id"'` vs total lines | (not run yet) |
| Retention | `aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'` | (not run yet) |
| Manifest | `aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -` | balanced: True. Output: `42 21 1 1 {'format': 9, 'required': 2, 'referential': 5, 'business': 3} balanced True` |

## Read the code (one line each)

1. Where does a **transient** S3 error end up? Where does a **bad record** end up? Are they different paths?
   - A transient error (throttling or 5xx) becomes `RecoverableError` in `_call` (`service.py`, line 199); the handler logs `file.retry` and re-raises, so Lambda retries and then the DLQ holds it. A bad record never raises: `classify` sends it to `quarantine/<reason>/` with a sidecar and the file carries on. Yes, two different paths.
2. Find one log call. Could any value in it be a payload field? Which key, which value?
   - `logger.info("zone.written", extra={...})` (`service.py`, line 151) with keys `boundary`, `bucket`, `key`, `record_count`, `bytes`. None is a payload field: `key` is the S3 object name, the rest are identifiers and counts.
3. What makes a second run of the same file a no-op? Which line of code?
   - The manifest, which is written last. `service.py` lines 94 to 95 compare `source_sha256`, `rules_sha256` and `ref_data_sha256` with the stored manifest; if all three match, it returns `already_processed` and writes nothing.
4. One thing you'd change before this goes to production, and why.
   - The whole file is read into memory (`raw = _read_object(...)`, line 80). That is fine for 3 KB, but a large file would need a size limit or streaming.

## Verdict
(decide after the three deployed rows above are filled in)
