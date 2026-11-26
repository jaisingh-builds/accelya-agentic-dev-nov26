"""Accelya M2 - ticket refund window Lambda (SQS-triggered).

Hop    SQS -> Lambda : standard queue, event source mapping with ReportBatchItemFailures.
       A bad record comes back in batchItemFailures; SQS redelivers only those, DLQ after maxReceiveCount.
Rules  Time between cancellation_timestamp and departure_datetime, first match wins:
       departure already passed          -> R-DEPARTED   REJECT 0%
       < 6 hours to departure            -> R-LASTMIN    REJECT 0%
       >= 7 days to departure            -> R-WEEK       APPROVE 90%
       >= 2 days to departure            -> R-TWODAYS    APPROVE 50%
       between 6 h and 2 days            -> R-REVIEW     MANUAL_REVIEW 0%
       Money: Decimal, ROUND_HALF_UP, 2 dp, returned as a string.
Logs   docs/LOGGING_CONTRACT.md - identifiers and counts only, never the body or passenger data.
"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SERVICE = "accelya-m2-ticket-refund"
LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
TICKET_RE = re.compile(r"^\d{3}-\d{10}$")
PNR_RE = re.compile(r"^[A-Z0-9]{6}$")
CENT = Decimal("0.01")
LAST_MINUTE = timedelta(hours=6)
TWO_DAYS = timedelta(days=2)
ONE_WEEK = timedelta(days=7)

REQUIRED = ("ticket_number", "pnr", "airline_code", "fare_class",
            "fare_amount", "departure_datetime", "cancellation_timestamp")


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


def _parse_dt(value):
    """ISO 8601 with a timezone (Z or +/-hh:mm). Returns datetime or None."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else None


def _validated(body):
    """Parse the body and return the event dict, or an error_type string. Never returns payloads."""
    try:
        e = json.loads(body)
    except json.JSONDecodeError:
        return None, "MalformedJson"
    if not isinstance(e, dict):
        return None, "MalformedJson"
    if any(f not in e for f in REQUIRED):
        return e, "InvalidTicket"
    dep = _parse_dt(e["departure_datetime"])
    canc = _parse_dt(e["cancellation_timestamp"])
    if dep is None or canc is None:
        return e, "InvalidTicket"
    try:
        fare = Decimal(str(e["fare_amount"]))
    except (InvalidOperation, ValueError):
        return e, "InvalidTicket"
    if fare < 0:
        return e, "InvalidTicket"
    e["_dep"], e["_canc"], e["_fare"] = dep, canc, fare
    return e, None


def _decide(e):
    """First match wins on the cancellation-to-departure window."""
    delta = e["_dep"] - e["_canc"]
    if delta <= timedelta(0):
        return "R-DEPARTED", "REJECT", 0
    if delta < LAST_MINUTE:
        return "R-LASTMIN", "REJECT", 0
    if delta >= ONE_WEEK:
        return "R-WEEK", "APPROVE", 90
    if delta >= TWO_DAYS:
        return "R-TWODAYS", "APPROVE", 50
    return "R-REVIEW", "MANUAL_REVIEW", 0


def handler(event, context):
    failures, decisions = [], []
    for record in event.get("Records", []):
        mid = record.get("messageId", "")
        cid = _correlation_id(record)
        e, error_type = _validated(record.get("body", ""))
        if error_type:
            tno = e.get("ticket_number") if isinstance(e, dict) else None
            emit("ERROR", "record.invalid", cid, "internal", message_id=mid,
                 ticket_number=tno if isinstance(tno, str) and TICKET_RE.match(tno) else None,
                 error_type=error_type)
            failures.append({"itemIdentifier": mid})
            continue
        rule_id, decision, pct = _decide(e)
        refund = (e["_fare"] * pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        pnr = e.get("pnr")
        emit("INFO", "ticket.refund.decided", cid, "sqs->lambda", message_id=mid,
             pnr=pnr if isinstance(pnr, str) and PNR_RE.match(pnr) else None,
             ticket_number=e["ticket_number"] if isinstance(e["ticket_number"], str)
             and TICKET_RE.match(e["ticket_number"]) else None,
             airline_code=e.get("airline_code"), fare_class=e.get("fare_class"),
             refund_rule_id=rule_id, decision=decision)
        decisions.append({"ticket_number": e["ticket_number"], "pnr": e.get("pnr"),
                          "decision": decision, "refund_rule_id": rule_id,
                          "refund_amount": str(refund)})
    return {"batchItemFailures": failures, "decisions": decisions}
