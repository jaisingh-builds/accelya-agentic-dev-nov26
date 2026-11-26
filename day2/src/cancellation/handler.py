"""Accelya M2 - cancellation refund decision Lambda (SQS-triggered).

Spec (give this whole docstring to your assistant, together with AGENTS.md and docs/LOGGING_CONTRACT.md):

Trigger A STANDARD SQS queue -> Lambda event source mapping with ReportBatchItemFailures enabled.
        With it on, a successful return deletes every message EXCEPT those listed in batchItemFailures.
        Without it, Lambda ignores batchItemFailures: a successful return deletes the WHOLE batch
        (failed records are lost), and only an invocation error (an exception) retries the whole batch.
        On a FIFO queue you must stop at the first failure and return it AND every later messageId.

Input   event["Records"], each record has
        - messageId
        - body: JSON cancellation event (synthetic fixtures: day2/data/cancellation_events.json)
        - messageAttributes.correlation_id.stringValue
          Missing (fixture m-10)? Mint one (e.g. "m2-<uuid>"), use it on every log line for that
          record, and process normally - a missing attribute is not a failure.
        Log message_id (the SQS messageId) on every per-record log line, so each record is traceable.

Rules   day2/data/refund_rules.json. First match wins:
        R-NOSHOW (no_show_flag) -> REJECT 0%
        R-DEPARTED (departure_status == "departed") -> REJECT 0%
        fare family FLEX / BUSINESS -> APPROVE 100%, SAVER -> APPROVE 75%
        anything else -> MANUAL_REVIEW 0% (R-UNKNOWN)
        refund_amount = fare_amount * pct / 100 with Decimal, ROUND_HALF_UP, 2 dp, as a string

Errors  A record whose body is not valid JSON (m-07), or that misses a required field (m-08:
        event_id, fare_class, departure_status, no_show_flag, fare_amount), must NOT crash the batch.
        Add {"itemIdentifier": <that record's messageId>} to batchItemFailures and log ONE ERROR record
        for it with a non-empty error_type (e.g. "InvalidCancellation"). SQS redelivers only those messages; after maxReceiveCount they go to the DLQ (Day 6).

Output  {"batchItemFailures": [{"itemIdentifier": "<messageId>"}, ...],
         "decisions": [{"event_id", "decision", "refund_rule_id", "refund_amount"}, ...]}

Logs    Follow docs/LOGGING_CONTRACT.md exactly. service = "accelya-m2-cancellation".
        Never log the body, passenger_name, contact_email or fare_amount.

Check   python3 day2/tests/run_and_check.py   (all 6 checks must PASS; add -v for every bad log record)

Examples
  - fare_class "K", departure_status "scheduled", no_show_flag false, fare_amount "4120.00"
    -> {"decision": "APPROVE", "refund_rule_id": "R-SAVER", "refund_amount": "3090.00"}
  - fare_class "L" (SAVER), departure_status "scheduled", no_show_flag true, fare_amount "52310.00" (m-03)
    -> {"decision": "REJECT", "refund_rule_id": "R-NOSHOW", "refund_amount": "0.00"}  (no-show wins over SAVER)
  - body is truncated JSON (m-07) -> no decision; {"itemIdentifier": "m-07"} in batchItemFailures,
    one ERROR line with error_type "InvalidCancellation", error_code "malformed_json".

Non-goals
  - No classes, factories or strategy patterns. No AWS API calls (SQS deletes are done by Lambda).
  - FIFO queues are not supported: this handler does not stop at the first failure.

Implementation notes (for the reviewer)
  - The rules below mirror day2/data/refund_rules.json v1.0. They are embedded because that file is
    not part of the Lambda deployment package; change both together.
  - Only identifiers and counts are logged. passenger_name, contact_email, fare_amount and the body
    are never read into a log line. Identifier values that would break the contract's value rules
    are left out of the line rather than logged.
"""
import json
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SERVICE = "accelya-m2-cancellation"
BOUNDARY = "sqs->lambda"

# Mirrors day2/data/refund_rules.json v1.0 (see module docstring).
FARE_FAMILY_RULE = {
    "Y": "R-FLEX", "B": "R-FLEX", "M": "R-FLEX",
    "J": "R-BUSINESS", "C": "R-BUSINESS", "D": "R-BUSINESS",
    "K": "R-SAVER", "L": "R-SAVER", "Q": "R-SAVER", "V": "R-SAVER",
}
RULE_OUTCOME = {  # rule id -> (decision, refund_pct)
    "R-NOSHOW": ("REJECT", 0),
    "R-DEPARTED": ("REJECT", 0),
    "R-FLEX": ("APPROVE", 100),
    "R-BUSINESS": ("APPROVE", 100),
    "R-SAVER": ("APPROVE", 75),
    "R-UNKNOWN": ("MANUAL_REVIEW", 0),
}
REQUIRED_FIELDS = ("event_id", "fare_class", "departure_status", "no_show_flag", "fare_amount")
CENT = Decimal("0.01")
ERROR_TYPE_INVALID = "InvalidCancellation"

# Value rules from docs/logging-contract.json for identifiers taken from the message.
CORRELATION_ID_RULE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EVENT_ID_RULE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
MESSAGE_ID_RULE = re.compile(r"^[A-Za-z0-9-]{1,100}$")
FARE_CLASS_RULE = re.compile(r"^[A-Z]$")


def log(level, message, correlation_id, **fields):
    """Write one contract-shaped JSON line. Keys with value None are omitted (no nulls)."""
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": level,
        "service": SERVICE,
        "correlation_id": correlation_id,
        "boundary": BOUNDARY,
        "message": message,
    }
    line.update({k: v for k, v in fields.items() if v is not None})
    print(json.dumps(line, separators=(",", ":")), flush=True)


def safe_id(value, rule):
    """Return value if it is a string matching the contract rule, else None (left out of the log)."""
    return value if isinstance(value, str) and rule.match(value) else None


def mint_correlation_id():
    return f"m2-{uuid.uuid4()}"


def correlation_id_of(record):
    """Return (correlation_id, minted). A missing or contract-invalid attribute is minted, not failed."""
    attr = (record.get("messageAttributes") or {}).get("correlation_id") or {}
    value = attr.get("stringValue")
    if isinstance(value, str) and CORRELATION_ID_RULE.match(value):
        return value, False
    return mint_correlation_id(), True


def parse_cancellation(body):
    """Return (event, None) for a decidable event, or (None, error_code) with a payload-free reason."""
    try:
        event = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None, "malformed_json"
    if not isinstance(event, dict):
        return None, "not_an_object"
    for field in REQUIRED_FIELDS:
        if event.get(field) is None:
            return None, f"missing_field.{field}"
    if not isinstance(event["event_id"], str) or not event["event_id"].strip():
        return None, "invalid_field.event_id"
    if not isinstance(event["fare_class"], str):
        return None, "invalid_field.fare_class"
    if not isinstance(event["departure_status"], str):
        return None, "invalid_field.departure_status"
    if not isinstance(event["no_show_flag"], bool):
        return None, "invalid_field.no_show_flag"
    # Money is never float: accept a decimal string or an integer only.
    fare_amount = event["fare_amount"]
    if isinstance(fare_amount, bool) or not isinstance(fare_amount, (str, int)):
        return None, "invalid_field.fare_amount"
    try:
        amount = Decimal(str(fare_amount).strip())
    except InvalidOperation:
        return None, "invalid_field.fare_amount"
    if not amount.is_finite() or amount < 0:
        return None, "invalid_field.fare_amount"
    return {**event, "fare_amount": amount}, None


def match_rule(event):
    """First match wins: R-NOSHOW, R-DEPARTED, fare family, else R-UNKNOWN."""
    if event["no_show_flag"]:
        return "R-NOSHOW"
    if event["departure_status"] == "departed":
        return "R-DEPARTED"
    return FARE_FAMILY_RULE.get(event["fare_class"], "R-UNKNOWN")


def refund_amount(fare_amount, refund_pct):
    return (fare_amount * Decimal(refund_pct) / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)


def decide(event):
    rule_id = match_rule(event)
    decision, pct = RULE_OUTCOME[rule_id]
    return {
        "event_id": event["event_id"],
        "decision": decision,
        "refund_rule_id": rule_id,
        "refund_amount": str(refund_amount(event["fare_amount"], pct)),
    }


def process_record(record):
    """Return a decision dict, or None if the record failed and must be reported as a batch item failure."""
    message_id = record.get("messageId")
    log_message_id = safe_id(message_id, MESSAGE_ID_RULE)
    correlation_id, minted = correlation_id_of(record)
    if minted:
        log("WARNING", "correlation_id.minted", correlation_id, message_id=log_message_id)

    event, error_code = parse_cancellation(record.get("body"))
    if error_code:
        log("ERROR", "cancellation.invalid", correlation_id, message_id=log_message_id,
            error_type=ERROR_TYPE_INVALID, error_code=error_code)
        return None

    result = decide(event)
    log("WARNING" if result["decision"] == "MANUAL_REVIEW" else "INFO", "cancellation.decided", correlation_id,
        message_id=log_message_id,
        event_id=safe_id(event["event_id"], EVENT_ID_RULE),
        fare_class=safe_id(event["fare_class"], FARE_CLASS_RULE),
        refund_rule_id=result["refund_rule_id"],
        decision=result["decision"])
    return result


def handler(event, context):
    started = time.monotonic()
    request_id = getattr(context, "aws_request_id", None)
    batch_correlation_id = request_id if isinstance(request_id, str) and CORRELATION_ID_RULE.match(request_id) \
        else mint_correlation_id()
    records = event.get("Records") or []
    log("INFO", "batch.received", batch_correlation_id, record_count=len(records),
        contract_version="1.1")

    failures = []
    decisions = []
    for record in records:
        result = process_record(record)
        if result is None:
            failures.append({"itemIdentifier": record.get("messageId")})
        else:
            decisions.append(result)

    log("INFO", "batch.completed", batch_correlation_id, record_count=len(decisions),
        duration_ms=round((time.monotonic() - started) * 1000, 2))
    if failures:
        log("WARNING", "batch.partial_failure", batch_correlation_id, record_count=len(failures))
    return {"batchItemFailures": failures, "decisions": decisions}
