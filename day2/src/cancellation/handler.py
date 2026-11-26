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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SERVICE = "accelya-m2-cancellation"
BOUNDARY = "sqs->lambda"
REQUIRED_FIELDS = ("event_id", "fare_class", "departure_status", "no_show_flag", "fare_amount")
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
FARE_CLASS_RE = re.compile(r"^[A-Z]$")

# shortcut: mirrors day2/data/refund_rules.json v1.0 so the zip needs no data file; load it from S3/config once rules change outside code.
FARE_FAMILY_RULE = {
    **{c: ("R-FLEX", "APPROVE", 100) for c in ("Y", "B", "M")},
    **{c: ("R-BUSINESS", "APPROVE", 100) for c in ("J", "C", "D")},
    **{c: ("R-SAVER", "APPROVE", 75) for c in ("K", "L", "Q", "V")},
}


class InvalidCancellation(ValueError):
    """Body is not JSON, misses a required field, or has a field of the wrong type."""


def log(level, correlation_id, message, **fields):
    print(json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": level,
        "service": SERVICE,
        "correlation_id": correlation_id,
        "boundary": BOUNDARY,
        "message": message,
        **{k: v for k, v in fields.items() if v is not None},
    }))


def parse_body(body):
    try:
        event = json.loads(body)
    except (TypeError, ValueError) as e:
        raise InvalidCancellation("body is not valid JSON") from e
    if not isinstance(event, dict):
        raise InvalidCancellation("body is not a JSON object")
    missing = [f for f in REQUIRED_FIELDS if f not in event]
    if missing:
        raise InvalidCancellation("missing required field")
    if not isinstance(event["no_show_flag"], bool) or not isinstance(event["fare_class"], str):
        raise InvalidCancellation("wrong field type")
    try:
        # str() first so a JSON float never reaches Decimal with binary noise.
        event["fare_amount"] = Decimal(str(event["fare_amount"]))
    except InvalidOperation as e:
        raise InvalidCancellation("fare_amount is not a number") from e
    if not event["fare_amount"].is_finite() or event["fare_amount"] < 0:
        raise InvalidCancellation("fare_amount out of range")
    return event


def decide(event):
    """First match wins: R-NOSHOW, R-DEPARTED, fare family, else R-UNKNOWN."""
    if event["no_show_flag"]:
        rule_id, decision, pct = "R-NOSHOW", "REJECT", 0
    elif event["departure_status"] == "departed":
        rule_id, decision, pct = "R-DEPARTED", "REJECT", 0
    else:
        rule_id, decision, pct = FARE_FAMILY_RULE.get(event["fare_class"], ("R-UNKNOWN", "MANUAL_REVIEW", 0))
    amount = (event["fare_amount"] * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"event_id": event["event_id"], "decision": decision, "refund_rule_id": rule_id,
            "refund_amount": str(amount)}


def safe(value, pattern):
    """Log a body-derived identifier only when it fits the contract; otherwise leave it out."""
    return value if isinstance(value, str) and pattern.match(value) else None


def handler(event, context):
    # Standard queue only. A FIFO queue would need stop-at-first-failure (see spec).
    batch_cid = context.aws_request_id
    records = event.get("Records", [])
    log("INFO", batch_cid, "batch.received", record_count=len(records))

    failures, decisions = [], []
    for record in records:
        message_id = record["messageId"]
        cid = record.get("messageAttributes", {}).get("correlation_id", {}).get("stringValue")
        if not (isinstance(cid, str) and CID_RE.match(cid)):
            cid = f"m2-{uuid.uuid4()}"
            log("WARNING", cid, "correlation_id.minted", message_id=message_id)
        try:
            cancellation = parse_body(record.get("body"))
        except InvalidCancellation:
            failures.append({"itemIdentifier": message_id})
            log("ERROR", cid, "cancellation.rejected_invalid", message_id=message_id,
                error_type="InvalidCancellation")
            continue

        result = decide(cancellation)
        decisions.append(result)
        log("WARNING" if result["decision"] == "MANUAL_REVIEW" else "INFO", cid, "cancellation.decided",
            message_id=message_id, event_id=safe(result["event_id"], EVENT_ID_RE),
            fare_class=safe(cancellation["fare_class"], FARE_CLASS_RE),
            refund_rule_id=result["refund_rule_id"], decision=result["decision"])

    log("INFO", batch_cid, "batch.completed", record_count=len(records))
    return {"batchItemFailures": failures, "decisions": decisions}
