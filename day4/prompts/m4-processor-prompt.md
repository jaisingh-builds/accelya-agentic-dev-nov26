# M4 processor prompt (Copilot Chat or Devin)

Attach these files as context first (Copilot: `#file`; Devin: mention the paths):
`AGENTS.md`, `docs/LOGGING_CONTRACT.md`, `day3/rules/RULE_TYPES.md`,
`day4/src/processor/service.py`, `day4/src/processor/handler.py`, `day4/src/processor/validator.py`,
`day4/src/processor/log.py`, `day4/tests/test_service.py`, `day4/tests/test_logging.py`.

---

## Part A - service.py (Lab 2)

**Role.** You are a senior Python engineer on an airline settlement platform. You write small, testable AWS Lambda code.

**Task.** Implement `process_object()` in `day4/src/processor/service.py` exactly as its docstring specifies.
Return the complete file.

**Context.**
- `validator.py` is the rule engine, already written and tested: call `business_date`, `classify`; do not change it.
- The S3 client is passed in. Tests use a fake S3 with `get_object`, `put_object`, `head_object` only.
- Zones and file names must match Day 3's `classify.py --zones` byte for byte: CSV via `csv.writer`, sidecars via `json.dumps(..., indent=1)`.

**Constraints.**
- Python 3.12 standard library + boto3/botocore only. No new dependencies.
- Data problems never raise. Transient ClientErrors (`TRANSIENT_CODES` or HTTP status >= 500) raise `RecoverableError`; any other ClientError is re-raised unchanged.
- Write the manifest **last**. Same `source_sha256` + `rules_sha256` + `ref_data_sha256` as the stored manifest means nothing is written.
- Logging: only the keys in `docs/logging-contract.json`. Identifiers and counts only - never a row, a field value or an exception message.
- No `print`, no environment reads, no hard-coded bucket, region or account.

**Non-goals.** No Glue, no Parquet, no retries inside the function (Lambda retries async events), no new files.

**Output format.** The full `service.py`, then a 5-line list: each status the function can return and which test proves it.

---

## Part B - handler.py (Lab 3)

**Task.** Implement `handler()` in `day4/src/processor/handler.py` exactly as its docstring specifies. Return the complete file.

**Constraints.**
- Use the existing `logger` (Powertools, shaped by `log.py`). Decorate with `@logger.inject_lambda_context(clear_state=True)`.
- `logger.set_correlation_id(cid)` before the first log line of each record.
- The handler stays thin: no CSV parsing, no rule calls, at most 70 code lines.
- ERROR lines carry `error_type`; never `logger.exception` (it would log the traceback).

**Output format.** The full `handler.py`, then one sentence: how the correlation_id reaches every log line.
