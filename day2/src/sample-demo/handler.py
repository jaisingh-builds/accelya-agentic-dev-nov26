"""Accelya M2 - cancellation refund decision Lambda (SQS-triggered).

Hop    SQS -> Lambda : standard queue, event source mapping with ReportBatchItemFailures.
       A bad record comes back in batchItemFailures; SQS redelivers only those, DLQ after maxReceiveCount.
Rules  day2/data/refund_rules.json, first match wins:
       R-NOSHOW / R-DEPARTED -> REJECT 0%; FLEX / BUSINESS -> APPROVE 100%; SAVER -> APPROVE 75%;
       anything else -> MANUAL_REVIEW (R-UNKNOWN). Money: Decimal, ROUND_HALF_UP, 2 dp.
Logs   docs/LOGGING_CONTRACT.md - identifiers and counts only, never the body or passenger data.
"""
import json
import os
import re
import sys
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SERVICE = "accelya-m2-cancellation"
LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
CENT = Decimal("0.01")

REQUIRED = ("event_id", "fare_class", "departure_status", "no_show_flag", "fare_amount")
FARE_FAMILIES = {                    # day2/data/refund_rules.json
    "FLEX": {"Y", "B", "M"},
    "SAVER": {"K", "L", "Q", "V"},
    "BUSINESS": {"J", "C", "D"},
}
RULES = {
    "FLEX": ("R-FLEX", "APPROVE", 100),
    "BUSINESS": ("R-BUSINESS", "APPROVE", 100),
    "SAVER": ("R-SAVER", "APPROVE", 75),
}


def emit(level, message, correlation_id, boundary, **fields):
    """Write one contract-shaped log line. None-valued fields are omitted (no nulls)."""
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": SERVICE,
        "learner_id": LEARNER_ID,
        "correlation_id": correlation_id,
        "boundary": boundary,
        "message": message,
        **{k: v for k, v in fields.items() if v is not None},
    }
    sys.stdout.write(json.dumps(line) + "\n")


def _correlation_id(record):
    """messageAttributes.correlation_id.stringValue if valid, else mint one for this record only."""
    cid = (record.get("messageAttributes") or {}).get("correlation_id", {}).get("stringValue") or ""
    return cid if CID_RE.match(cid) else f"m2-{uuid.uuid4().hex[:12]}"


def _validated(body):
    """Parse the body and return the event dict, or an error_type string. Never returns payloads."""
    try:
        e = json.loads(body)
    except json.JSONDecodeError:
        return None, "MalformedJson"
    if not isinstance(e, dict):
        return None, "MalformedJson"
    if any(f not in e for f in REQUIRED):
        return e, "InvalidCancellation"
    if not isinstance(e["no_show_flag"], bool) \
            or not isinstance(e["departure_status"], str) \
            or not isinstance(e["fare_class"], str):
        return e, "InvalidCancellation"
    try:
        fare = Decimal(str(e["fare_amount"]))
    except (InvalidOperation, ValueError):
        return e, "InvalidCancellation"
    if fare < 0:
        return e, "InvalidCancellation"
    return e, None


def _decide(e):
    """First match wins per refund_rules.json."""
    if e["no_show_flag"]:
        return "R-NOSHOW", "REJECT", 0
    if e["departure_status"] == "departed":
        return "R-DEPARTED", "REJECT", 0
    for family, rule in RULES.items():
        if e["fare_class"] in FARE_FAMILIES[family]:
            return rule
    return "R-UNKNOWN", "MANUAL_REVIEW", 0


def handler(event, context):
    failures, decisions = [], []
    for record in event.get("Records", []):
        mid = record.get("messageId", "")
        cid = _correlation_id(record)
        e, error_type = _validated(record.get("body", ""))
        if error_type:
            emit("ERROR", "record.invalid", cid, "internal", message_id=mid,
                 event_id=e["event_id"] if isinstance(e, dict) and EVENT_ID_RE.match(str(e.get("event_id", ""))) else None,
                 error_type=error_type)
            failures.append({"itemIdentifier": mid})
            continue
        rule_id, decision, pct = _decide(e)
        refund = (Decimal(str(e["fare_amount"])) * pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        emit("INFO", "cancellation.decided", cid, "sqs->lambda", message_id=mid,
             event_id=str(e["event_id"]) if EVENT_ID_RE.match(str(e["event_id"])) else None,
             pnr=e.get("pnr"), airline_code=e.get("airline_code"),
             fare_class=e.get("fare_class"), refund_rule_id=rule_id, decision=decision)
        decisions.append({"event_id": e["event_id"], "decision": decision,
                          "refund_rule_id": rule_id, "refund_amount": str(refund)})
    return {"batchItemFailures": failures, "decisions": decisions}
