"""Accelya M2 - cancellation refund decision Lambda (SQS-triggered).

Trigger  SQS queue -> Lambda with ReportBatchItemFailures enabled.
Input    event["Records"] — each record has messageId, body (JSON cancellation
         event), and messageAttributes.correlation_id.stringValue.
Rules    day2/data/refund_rules.json. First match wins.
Output   {"batchItemFailures": [...], "decisions": [...]}
Logs     docs/logging-contract.json, service = "accelya-m2-cancellation".
"""
import json
import os
import sys
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

LEARNER_ID = os.environ.get("LEARNER_ID", "amol")
SERVICE = f"accelya-m2-{LEARNER_ID}-cancellation"

FARE_FAMILIES = {
    "FLEX": {"Y", "B", "M"},
    "SAVER": {"K", "L", "Q", "V"},
    "BUSINESS": {"J", "C", "D"},
}

REQUIRED_FIELDS = ("event_id", "fare_class", "departure_status", "no_show_flag", "fare_amount")


def emit(level, message, correlation_id, **fields):
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": SERVICE,
        "correlation_id": correlation_id,
        "boundary": fields.pop("boundary", "internal"),
        "message": message,
        **fields,
    }
    sys.stdout.write(json.dumps(line, default=str) + "\n")


def _extract_correlation_id(record):
    attrs = record.get("messageAttributes") or {}
    cid_attr = attrs.get("correlation_id", {})
    cid = cid_attr.get("stringValue")
    if cid:
        return cid
    return f"m2-{uuid.uuid4().hex[:12]}"


def _parse_body(raw_body):
    parsed = json.loads(raw_body)
    missing = [f for f in REQUIRED_FIELDS if f not in parsed]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    return parsed


def _fare_family(fare_class):
    for family, classes in FARE_FAMILIES.items():
        if fare_class in classes:
            return family
    return None


def _decide_refund(cancellation):
    if cancellation["no_show_flag"] is True:
        return "REJECT", "R-NOSHOW", 0

    if cancellation["departure_status"] == "departed":
        return "REJECT", "R-DEPARTED", 0

    family = _fare_family(cancellation["fare_class"])

    if family == "FLEX":
        return "APPROVE", "R-FLEX", 100
    if family == "BUSINESS":
        return "APPROVE", "R-BUSINESS", 100
    if family == "SAVER":
        return "APPROVE", "R-SAVER", 75

    return "MANUAL_REVIEW", "R-UNKNOWN", 0


def _compute_refund_amount(fare_amount_str, refund_pct):
    fare = Decimal(fare_amount_str)
    pct = Decimal(refund_pct)
    amount = (fare * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(amount)


def handler(event, context):
    batch_item_failures = []
    decisions = []
    failed_groups = set()

    for record in event.get("Records", []):
        message_id = record["messageId"]
        correlation_id = _extract_correlation_id(record)

        group_id = (record.get("attributes") or {}).get("MessageGroupId")
        if group_id and group_id in failed_groups:
            emit("INFO", "record.skipped.ordering", correlation_id,
                 boundary="internal",
                 message_id=message_id)
            batch_item_failures.append({"itemIdentifier": message_id})
            continue

        try:
            cancellation = _parse_body(record["body"])
            event_id = cancellation["event_id"]
            decision, rule_id, refund_pct = _decide_refund(cancellation)
            refund_amount = _compute_refund_amount(
                cancellation["fare_amount"], refund_pct)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            emit("ERROR", "record.parse.failed", correlation_id,
                 boundary="sqs->lambda",
                 message_id=message_id,
                 error_type=type(exc).__name__)
            batch_item_failures.append({"itemIdentifier": message_id})
            if group_id:
                failed_groups.add(group_id)
            continue
        except InvalidOperation:
            emit("ERROR", "record.money.invalid", correlation_id,
                 boundary="internal",
                 message_id=message_id,
                 error_type="InvalidOperation")
            batch_item_failures.append({"itemIdentifier": message_id})
            if group_id:
                failed_groups.add(group_id)
            continue

        emit("INFO", "refund.decision.made", correlation_id,
             boundary="internal",
             message_id=message_id,
             event_id=event_id,
             airline_code=cancellation.get("airline_code", "unknown"),
             fare_class=cancellation["fare_class"],
             decision=decision,
             refund_rule_id=rule_id)

        decisions.append({
            "event_id": event_id,
            "decision": decision,
            "refund_rule_id": rule_id,
            "refund_amount": refund_amount,
        })

    batch_cid = (getattr(context, "aws_request_id", None)
                 or f"m2-batch-{uuid.uuid4().hex[:12]}")
    emit("INFO", "batch.complete", batch_cid,
         boundary="internal",
         record_count=len(event.get("Records", [])))

    return {
        "batchItemFailures": batch_item_failures,
        "decisions": decisions,
    }
