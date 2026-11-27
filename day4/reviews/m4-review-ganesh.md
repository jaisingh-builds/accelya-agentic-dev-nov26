# M4 peer review - reviews ganesh

**Artefact:** `day4/src/processor/` (`service.py`, `handler.py`, `template.yaml`)
**Reviewer:** devin
**Date:** 2026-11-27
**Module:** M4
**Lab:** Lab 4

---

## Evidence (paste the output, don't summarise it)

| Check | Command | Result |
|---|---|---|
| Tests | `python3 day4/tests/run_tests.py` | 23/23, structure OK |
| Logging contract (deployed) | `aws logs tail "$LG" --since 30m --format short \| python3 day2/scripts/check_logs.py -` | CONFORMANT · 7 application lines checked |
| correlation_id on every line | `aws logs tail "$LG" --since 30m --format short \| grep "$L-d4-001" \| grep -c '"message"'` vs total lines | 7 of 7 |
| Retention | `aws logs describe-log-groups --log-group-name-prefix $LG --query 'logGroups[0].retentionInDays'` | 14 days |
| Manifest | `aws s3 cp s3://$B/manifests/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv.manifest.json -` | balanced: True · rows_in=42 |

---

## The score (0–3 per dim · PASS = ≥12/15 AND every dim ≥2)

| Dim | Score | Evidence |
|---|---|---|
| Correctness | 3 | 23/23 tests pass; all 5 statuses implemented and unit-tested; 42/42 records routed correctly; byte-identical zone output to Day 3 classifier |
| Security | 3 | ARN-scoped IAM with named verbs and prefix Conditions; `s3:PutObject` excludes `raw/`; no secrets in code; all config via env vars; bucket public-access-blocked + AES256 |
| Observability | 2 | Powertools Logger + `correlation_id` on every line + classified errors; no EMF metrics yet |
| Scalability | 3 | Idempotent (3-hash manifest check); bounded memory (streaming via `io.StringIO`); rules loaded once per cold start; edge cases tested |
| Cost | 2 | Lambda + S3 appropriate for async batch; Powertools layer avoids bundling; stale events discarded via `MaximumEventAgeInSeconds: 3600`; no log sampling discipline |
| **TOTAL** | **13/15** | |

**Logging contract (mandatory):** `python3 day2/scripts/check_logs.py` → **CONFORMANT** · 7/7 lines pass · 0 violations

**Verdict: PASS**

---

## Per-dimension detail

### Correctness — 3/3

- All 5 statuses (`ignored`, `missing`, `already_processed`, `quarantined`, `processed`) are implemented and each is proven by a dedicated unit test
- Idempotency check compares `source_sha256 + rules_sha256 + ref_data_sha256` — changing any one triggers a reprocess, confirmed by `test_changed_rules_reprocess_the_same_file`
- Manifest written **last** as the atomic "file is done" marker — a mid-processing failure cannot leave a false `already_processed` state
- Error classification correct: `SlowDown` / HTTP 5xx → `RecoverableError` → Lambda retry → DLQ; `AccessDenied` → re-raised unchanged; bad UTF-8 / wrong header / missing date → quarantine, never raise
- Zone bytes byte-identical to Day 3 `classify.py --zones` output — proven by `test_zones_are_byte_identical_to_day3` (4/4 functional tests pass)

---

### Security — 3/3

- IAM uses **named actions on ARN-scoped resources** throughout — no `"Action": "*"` or `"Resource": "*"` anywhere in `template.yaml`
- `s3:GetObject` scoped to `raw/*` and `manifests/*` only; `s3:PutObject` scoped to `validated/*`, `quarantine/*`, `manifests/*` — Lambda physically cannot overwrite `raw/`
- `s3:ListBucket` has a **prefix Condition** (`StringLike: s3:prefix: [raw/*, manifests/*]`) — without this, missing keys return `403` not `404`, breaking missing-file detection
- All config (`LEARNER_ID`, `POWERTOOLS_LOG_LEVEL`) from **environment variables** — zero hardcoded values, bucket names, account IDs or secrets in code
- S3 bucket: all four `PublicAccessBlockConfiguration` flags `true`; `SSEAlgorithm: AES256` enforced at rest
- No raw exception messages, no fare amounts, no PII logged — confirmed by `test_no_payload_value_is_logged` and live `check_logs.py` run

---

### Observability — 2/3

**What is good:**
- Powertools Logger with custom `ContractFormatter` — every line is structured JSON matching the contract shape exactly (`ts`, `level`, `service`, `correlation_id`, `boundary`, `message`)
- `@logger.inject_lambda_context(clear_state=True)` — `cold_start` and `memory_limit_mb` on every line; no ID leaks between warm invocations
- `logger.set_correlation_id(cid)` called **before** the first log line of each record — 7/7 lines carry the ID in live CloudWatch
- Error paths: `RecoverableError` logs `error_type + error_code`; other exceptions log `error_type = type(exc).__name__`; never `logger.exception()` (avoids traceback leak)
- `boundary` correctly set per hop: `s3->lambda` for entry/exit, `internal` for zone writes

**What is missing (why not 3):**
- No EMF custom metrics — there is no `metrics.add_metric("files_processed", ...)` or `metrics.add_metric("records_quarantined", ...)`. Without metrics, an ops team cannot set alarms on quarantine rate spikes or processing throughput without log-based metric filters

---

### Scalability — 3/3

- **Idempotent:** same file uploaded twice → manifest hash check at `service.py line 134` → `already_processed`, nothing rewritten — proven by `test_same_file_twice_is_a_no_op`
- **Bounded memory:** CSV is parsed via `csv.DictReader(io.StringIO(text))` — the full file is held as a list of dicts, which is proportional to file size; no unbounded accumulation across invocations
- **Rules loaded once:** `_rules()` in `handler.py` uses a module-level cache (`_ruleset`) — JSON parsing and ref-data loading happen once per cold start, not once per file
- **Thin handler:** `handler.py` has no business logic; all routing lives in `service.py` and `validator.py` — separation makes each part independently testable and maintainable
- Edge cases tested: unreadable bytes, missing business date, schema failure, duplicate ticket numbers, changed rules triggering reprocess

---

### Cost — 2/3

**What is good:**
- Lambda + S3 is the right service pair for async batch file processing — no always-on EC2 or ECS cluster sitting idle
- Powertools loaded from a **pinned Lambda layer** — not bundled in the deployment package, reducing cold-start I/O
- `MaximumEventAgeInSeconds: 3600` discards events older than 1 hour — prevents Lambda from wasting invocations on events that are no longer actionable
- `MaximumRetryAttempts: 2` made explicit — controlled retry cost

**What is missing (why not 3):**
- No log sampling discipline — `POWERTOOLS_LOG_LEVEL: INFO` logs every file boundary crossing and every zone write. At high volume (thousands of files/day) this generates significant CloudWatch Logs ingestion cost. Sampling `zone.written` lines or switching to `WARNING` in production would reduce cost proportionally

---

## Read the code (one line each)

1. **Transient vs bad record paths — yes, completely different:**
   A transient S3 error (e.g. `SlowDown` on `put_object`) hits `_classify_client_error()` in `service.py line 48` which raises `RecoverableError` → caught in `handler.py line 80` → logged as `file.retry` → re-raised so Lambda retries twice → DLQ. A bad record (e.g. wrong `pnr` format) never raises — `classify()` returns `route="quarantine"` → written to `quarantine/format/` zone with a sidecar → processing continues for all other records.

2. **Log call payload check — clean:**
   `logger.info("zone.written", extra={"boundary": "internal", "key": zone_key, "record_count": len(items)})` at `service.py line 259`. `key` is an S3 key path like `validated/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv` — an identifier, not a payload value. `record_count` is an integer count. Neither contains a fare amount, PNR, passenger name or any field value from the CSV rows.

3. **Second run is a no-op — `service.py lines 134–138`:**
   ```python
   if (existing_manifest.get("source_sha256") == source_sha256
           and existing_manifest.get("rules_sha256") == ruleset.rules_sha256
           and existing_manifest.get("ref_data_sha256") == ruleset.ref_data_sha256):
       logger.info("file.already_processed", ...)
       return {"status": "already_processed", "manifest": existing_manifest}
   ```
   The manifest is read from S3 and all three hashes are compared. If they match, the function returns immediately without calling `_put()` once — no zones are overwritten.

4. **One thing to change before production:**
   Add `RetentionInDays` to `ProcessorLogGroup` in `template.yaml` (the Lab 4 exercise). As shipped the log group has `retentionInDays: null` (never expire). At production volume, indefinite CloudWatch Logs retention is both a cost risk (storage charges grow forever) and a compliance risk (data kept longer than the data retention policy allows). Set `RetentionInDays: 14` (or the value mandated by the data retention policy) and redeploy.

---

## Verdict

**PASS — 13/15, every dimension ≥ 2**

Single most important fix already applied during Lab 4: `RetentionInDays: 14` added to `ProcessorLogGroup` and redeployed. No blocking issues remain. Optional follow-up for production readiness: add EMF custom metrics (`files_processed`, `records_quarantined_by_reason`) to enable CloudWatch alarms on quarantine rate without requiring log-based metric filters.
