"""Accelya M2 - cancellation refund decision Lambda (SQS-triggered).

SQS standard queue -> Lambda, ReportBatchItemFailures enabled. A bad record is reported in
batchItemFailures and never crashes the batch. Rules mirror day2/data/refund_rules.json
(first match wins: R-NOSHOW, R-DEPARTED, fare family, R-UNKNOWN). Logging follows
docs/LOGGING_CONTRACT.md: identifiers and counts only, never the body or fare_amount.
"""
import json
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SERVICE = "accelya-m2-cancellation"
BOUNDARY = "sqs->lambda"
REQUIRED_FIELDS = ("event_id", "fare_class", "departure_status", "no_show_flag", "fare_amount")

# Mirrors refund_rules.json (the data file is not part of the Lambda package).
FARE_FAMILIES = {
    "FLEX": ("Y", "B", "M"),
    "SAVER": ("K", "L", "Q", "V"),
    "BUSINESS": ("J", "C", "D"),
}
FAMILY_RULES = {  # family -> (rule id, refund pct)
    "FLEX": ("R-FLEX", 100),
    "BUSINESS": ("R-BUSINESS", 100),
    "SAVER": ("R-SAVER", 75),
}

# Value rules copied from docs/logging-contract.json, applied to body-derived values
# so a payload can never ride into a log line under an allowed key.
CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
FARE_CLASS_RE = re.compile(r"^[A-Z]$")
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,100}$")


def log(level, correlation_id, message, **fields):
    """Write one contract-shaped JSON record. Keys with None values are left out (no nulls)."""
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": level,
        "service": SERVICE,
        "correlation_id": correlation_id,
        "boundary": BOUNDARY,
        "message": message,
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    print(json.dumps(record))


def get_correlation_id(record):
    """Use the message attribute when valid; otherwise mint one (missing is not a failure)."""
    attrs = record.get("messageAttributes")
    value = None
    if isinstance(attrs, dict) and isinstance(attrs.get("correlation_id"), dict):
        value = attrs["correlation_id"].get("stringValue")
    if isinstance(value, str) and CORRELATION_RE.match(value):
        return value
    return f"m2-{uuid.uuid4()}"


def safe_value(value, pattern):
    """Return value only if it is a string matching the contract rule, else None (omitted)."""
    return value if isinstance(value, str) and pattern.match(value) else None


def parse_cancellation(body):
    """Return (cancellation dict, None) or (None, error_type). Never raises on bad input."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None, "InvalidJson"
    if not isinstance(data, dict):
        return None, "InvalidCancellation"
    if any(field not in data or data[field] in (None, "") for field in REQUIRED_FIELDS):
        return None, "InvalidCancellation"
    if not isinstance(data["no_show_flag"], bool) or not isinstance(data["fare_class"], str):
        return None, "InvalidCancellation"
    try:
        fare_amount = Decimal(str(data["fare_amount"]))
    except InvalidOperation:
        return None, "InvalidCancellation"
    if not fare_amount.is_finite() or fare_amount < 0:
        return None, "InvalidCancellation"
    data["fare_amount"] = fare_amount
    return data, None


def decide(cancellation):
    """Apply refund_rules.json, first match wins. Returns (decision, rule id, refund_amount str)."""
    fare_class = cancellation["fare_class"]
    if cancellation["no_show_flag"]:
        decision, rule_id, pct = "REJECT", "R-NOSHOW", 0
    elif cancellation["departure_status"] == "departed":
        decision, rule_id, pct = "REJECT", "R-DEPARTED", 0
    else:
        family = next((f for f, classes in FARE_FAMILIES.items() if fare_class in classes), None)
        if family:
            rule_id, pct = FAMILY_RULES[family]
            decision = "APPROVE"
        else:
            decision, rule_id, pct = "MANUAL_REVIEW", "R-UNKNOWN", 0
    refund = (cancellation["fare_amount"] * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return decision, rule_id, str(refund)


def handler(event, context):
    started = time.monotonic()
    records = event.get("Records", []) if isinstance(event, dict) else []
    batch_correlation_id = f"m2-{uuid.uuid4()}"
    failures = []
    decisions = []

    log("INFO", batch_correlation_id, "batch.received", record_count=len(records))

    for record in records:
        message_id = record.get("messageId")
        correlation_id = get_correlation_id(record)
        log_id = safe_value(message_id, MESSAGE_ID_RE)
        log("INFO", correlation_id, "cancellation.received", message_id=log_id)

        cancellation, error_type = parse_cancellation(record.get("body"))
        if error_type:
            failures.append({"itemIdentifier": message_id})
            log("ERROR", correlation_id, "cancellation.rejected", message_id=log_id, error_type=error_type)
            continue

        decision, rule_id, refund_amount = decide(cancellation)
        decisions.append({
            "event_id": cancellation["event_id"],
            "decision": decision,
            "refund_rule_id": rule_id,
            "refund_amount": refund_amount,
        })
        log(
            "WARNING" if decision == "MANUAL_REVIEW" else "INFO",
            correlation_id,
            "cancellation.decided",
            message_id=log_id,
            event_id=safe_value(cancellation["event_id"], EVENT_ID_RE),
            fare_class=safe_value(cancellation["fare_class"], FARE_CLASS_RE),
            refund_rule_id=rule_id,
            decision=decision,
        )

    log(
        "INFO",
        batch_correlation_id,
        "batch.completed",
        record_count=len(records),
        duration_ms=round((time.monotonic() - started) * 1000, 2),
    )
    return {"batchItemFailures": failures, "decisions": decisions}
