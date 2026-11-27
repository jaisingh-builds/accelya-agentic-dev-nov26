# M4 peer review - rohitb reviews rohitb (branch `day4-rohitb`)

Use `docs/HITL_REVIEW_TEMPLATE.md` for the 5-dimension score. Paste the evidence below.
Reviewed on 2026-11-27 against the stack `accelya-m4-rohitb` in sandbox `…1117`.

## Evidence (paste the output, don't summarise it)

| Check | Command | Result |
|---|---|---|
| Tests | `python3 day4/tests/run_tests.py` | **23/23**, structure OK (13/13) - `READY TO DEPLOY` |
| Logging contract (deployed) | `aws logs tail $LG --since 30m --format short \| python3 day2/scripts/check_logs.py -` | full stream: **48 non-conformant lines**, all Lambda's own `[ERROR] … Traceback` records from the DLQ demo; our 22 lines alone: **CONFORMANT** |
| correlation_id on every line | `aws logs tail $LG --since 30m --format short \| grep -c '"correlation_id"'` vs total lines | **22 of 89** lines; ours **22 of 22** (the other 67 are Lambda platform lines: START/END/REPORT and tracebacks) |
| Retention | `aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'` | **14** days |
| Manifest | `aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -` | rows_in 42, validated 21, flagged 1, duplicates 1, quarantined {format 9, required 2, referential 5, business 3}, **balanced: True**, correlation_id `rohitb-d4-001` |

Runs seen in the log (one correlation_id per run):
```
rohitb-d4-001      file.received -> 5 x zone.written -> file.processed            (42-row file, with metadata)
m4-1f68f910-109    metadata.missing -> file.received -> file.already_processed     (same file again, no metadata: no-op)
m4-59d2c870-d13    metadata.missing -> file.received -> file.quarantined           (resend file, renamed header -> schema)
m4-203afa3b-6f1    3 x (metadata.missing, file.received, file.failed NoSuchBucket) (bad bucket: 3 attempts, then DLQ depth 1)
```

## Read the code (one line each)

1. **Transient vs bad record:** a transient S3 error (SlowDown, throttling, 5xx) becomes `RecoverableError` in `_call()` (service.py:151) and is re-raised, so Lambda retries twice and then the DLQ holds the event; a bad record is routed by `classify()` to `quarantine/<reason>/` with a sidecar (service.py:118) and never raises. Yes, different paths.
2. **A log call:** `file.received` (handler.py:55) logs `bucket`, `key`, `bytes` - the object name and size, not a row; no payload field. `key` would only leak data if a sender put a PNR in a file name.
3. **No-op on a second run:** service.py:95-99 - the stored manifest has the same `source_sha256`, `rules_sha256` and `ref_data_sha256`, so it logs `file.already_processed` and writes nothing (proven by `m4-1f68f910-109` above).
4. **Change before production:** read the file as a stream instead of `Body.read()` + `list(csv.reader(...))` (service.py:86, 107) - today the whole file sits in memory three times (bytes, text, rows); fine at 42 rows, untested at 100,000.

---

## HITL score (`docs/HITL_REVIEW_TEMPLATE.md`)

**Artefact:** `day4/src/processor/` (handler.py, service.py) + `day4/template.yaml`   **Reviewer:** `rohitb`   **Date:** 2026-11-27   **Module:** M4   **Lab:** lab-5

| Dim | Score | Evidence (one line per dim) |
|---|---|---|
| Correctness | 3 | Matches the spec; edge cases tested (unreadable, no date, missing object, throttling, AccessDenied, rerun, rule change) and proven live: 42/42, schema reject, no-op, DLQ |
| Security | 2 | S3 actions ARN-scoped, ListBucket limited to `raw/*`/`manifests/*`, never writes `raw/`; but `AWSLambdaBasicExecutionRole` grants logs on `Resource: "*"`, and PII in the zones (pnr, ticket_number) is not tagged |
| Observability | 2 | Powertools logger, correlation_id on 22/22 of our lines, classified errors (`error_type`); no business metrics yet; the platform traceback makes the full stream non-conformant |
| Scalability | 2 | Idempotent (manifest fingerprints) and bounded per file; whole file held in memory, not tested at 10x |
| Cost | 2 | Event-driven Lambda fits the workload, 14-day retention; cost per file not measured |
| **TOTAL** | **11/15** | |

**Logging contract (from Day 2, mandatory):** `python3 day2/scripts/check_logs.py <log>` -> our lines **CONFORMANT**; full stream 48 violations, all from Lambda's runtime traceback, none from our code.

**Verdict:** **REVISE** (11/15, needs >= 12; every dimension is >= 2)

## If REVISE: what to change

1. **Security** - replace `AWSLambdaBasicExecutionRole` with an inline policy: `logs:CreateLogStream` and `logs:PutLogEvents` on the processor's own log group ARN only (AGENTS.md: never `Resource: "*"`).
2. **Observability** - add metric filters on our contract lines (`file.quarantined`, `file.failed` by `error_type`) and an alarm on DLQ depth > 0, as the reference architecture does in hop 9a; no EMF, so the contract stays intact.
3. **Scalability** - stream the CSV from S3 and run once with a 100,000-row synthetic file to measure memory and duration.

**Single most important fix:** #1 - it is the only line in this stack that breaks an `AGENTS.md` rule.

**Next iteration prompt addition:** "IAM for the function's logs: CreateLogStream and PutLogEvents on its own log group ARN only - no managed policies with Resource *."
