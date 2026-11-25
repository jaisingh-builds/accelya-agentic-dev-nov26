"""Accelya M2 - cancellation refund decision Lambda (SQS-triggered).   <-- YOU BUILD THIS IN LAB 3

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
"""


import json
import re
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SERVICE = "accelya-m2-cancellation"
BOUNDARY = "sqs->lambda"
REQUIRED = ("event_id", "fare_class", "departure_status", "no_show_flag", "fare_amount")
FAMILIES = {"FLEX": "YBM", "SAVER": "KLQV", "BUSINESS": "JCD"}
FAMILY_RULES = {  # family -> (rule_id, decision, refund_pct)
    "FLEX": ("R-FLEX", "APPROVE", 100),
    "BUSINESS": ("R-BUSINESS", "APPROVE", 100),
    "SAVER": ("R-SAVER", "APPROVE", 75),
}
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
FARE_CLASS_RE = re.compile(r"^[A-Z]$")


class InvalidCancellation(Exception):
    """Body is not valid JSON, or misses/has a malformed required field."""


def _log(level, correlation_id, message, **extra):
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": level,
        "service": SERVICE,
        "correlation_id": correlation_id,
        "boundary": BOUNDARY,
        "message": message,
    }
    rec.update(extra)
    print(json.dumps(rec))


def _correlation_id(record):
    attr = (record.get("messageAttributes") or {}).get("correlation_id") or {}
    cid = attr.get("stringValue")
    if isinstance(cid, str) and CID_RE.match(cid):
        return cid
    return "m2-" + uuid.uuid4().hex[:12]


def _parse(body):
    try:
        event = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise InvalidCancellation("body is not valid JSON") from exc
    if not isinstance(event, dict):
        raise InvalidCancellation("body is not a JSON object")
    if any(k not in event or event[k] is None for k in REQUIRED):
        raise InvalidCancellation("missing required field")
    if not isinstance(event["no_show_flag"], bool):
        raise InvalidCancellation("no_show_flag is not a boolean")
    try:
        fare = Decimal(str(event["fare_amount"]))
    except InvalidOperation as exc:
        raise InvalidCancellation("fare_amount is not a number") from exc
    if not fare.is_finite() or fare < 0:
        raise InvalidCancellation("fare_amount out of range")
    event["fare_amount"] = fare
    return event


def decide(event):
    """First match wins. Returns (rule_id, decision, refund_pct)."""
    if event["no_show_flag"]:
        return "R-NOSHOW", "REJECT", 0
    if event["departure_status"] == "departed":
        return "R-DEPARTED", "REJECT", 0
    for family, classes in FAMILIES.items():
        if event["fare_class"] in classes:
            return FAMILY_RULES[family]
    return "R-UNKNOWN", "MANUAL_REVIEW", 0


def refund_amount(fare, pct):
    return str((fare * Decimal(pct) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def handler(event, context):
    records = event.get("Records", [])
    failures, decisions = [], []
    _log("INFO", "m2-batch-" + uuid.uuid4().hex[:8], "batch.received", record_count=len(records))

    for record in records:
        message_id = record.get("messageId", "unknown")
        cid = _correlation_id(record)
        try:
            cancellation = _parse(record.get("body"))
            rule_id, decision, pct = decide(cancellation)
            amount = refund_amount(cancellation["fare_amount"], pct)
        except InvalidCancellation as exc:
            _log("ERROR", cid, "cancellation.invalid", message_id=message_id,
                 error_type=type(exc).__name__)
            failures.append({"itemIdentifier": message_id})
            continue

        event_id = str(cancellation["event_id"])
        extra = {"event_id": event_id} if EVENT_ID_RE.match(event_id) else {}
        fare_class = str(cancellation["fare_class"])
        if FARE_CLASS_RE.match(fare_class):
            extra["fare_class"] = fare_class
        level = "WARNING" if decision == "MANUAL_REVIEW" else "INFO"
        _log(level, cid, "cancellation.decided", message_id=message_id,
             refund_rule_id=rule_id, decision=decision, **extra)
        decisions.append({"event_id": event_id, "decision": decision,
                          "refund_rule_id": rule_id, "refund_amount": amount})

    _log("INFO", "m2-batch-" + uuid.uuid4().hex[:8], "batch.completed",
         record_count=len(records) - len(failures))
    return {"batchItemFailures": failures, "decisions": decisions}
