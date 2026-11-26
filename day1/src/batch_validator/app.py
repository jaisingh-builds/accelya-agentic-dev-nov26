"""Accelya M1 Option 2 - Batch Validator Lambda.

Boundary 1  Step Functions -> Lambda  : sync push, invoked by state machine.
Boundary 2  Lambda -> S3              : sync call, reads CSV from raw/ prefix.
Boundary 3  Lambda -> DynamoDB        : sync call, BatchWriteItem for settlement records.
Boundary 4  Lambda -> SNS             : sync call, publishes settlement summary (counts only, no PII).
"""
import csv
import io
import json
import os
import re
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
SETTLEMENT_TABLE = os.environ.get("SETTLEMENT_TABLE", "")
SETTLEMENT_TOPIC_ARN = os.environ.get("SETTLEMENT_TOPIC_ARN", "")

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

_AIRLINE_RE = re.compile(r"ticket_issue_([A-Z0-9]{2})_")


def emit(level, message, correlation_id, **fields):
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": "accelya-m1-batch-validator",
        "learner_id": LEARNER_ID,
        "correlation_id": correlation_id,
        "message": message,
        **fields,
    }
    sys.stdout.write(json.dumps(line) + "\n")


def _parse_airline_code(key):
    m = _AIRLINE_RE.search(key)
    return m.group(1) if m else None


def _read_csv(bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(body))
    return list(reader)


def _batch_write_records(records, airline_code, correlation_id):
    table = dynamodb.Table(SETTLEMENT_TABLE)
    written = 0
    skipped = 0
    duplicates = 0
    seen_keys = set()
    batch = []
    for row in records:
        pnr = (row.get("pnr") or "").strip()
        ticket = (row.get("ticket_number") or "").strip()
        if not pnr or not ticket:
            skipped += 1
            emit("WARNING", "record.skipped_empty_key", correlation_id,
                 boundary="lambda->dynamodb", pnr_empty=not pnr, ticket_empty=not ticket)
            continue
        key = (pnr, ticket)
        if key in seen_keys:
            duplicates += 1
            emit("WARNING", "record.skipped_duplicate", correlation_id,
                 boundary="lambda->dynamodb", pnr=pnr, ticket_number=ticket)
            continue
        seen_keys.add(key)
        currency = row.get("currency") or row.get("fare_currency") or ""
        batch.append({
            "PutRequest": {
                "Item": {
                    "pnr": pnr,
                    "ticket_number": ticket,
                    "airline_code": row.get("airline_code", airline_code),
                    "fare_class": row.get("fare_class", ""),
                    "od_pair": row.get("od_pair", ""),
                    "issue_date": row.get("issue_date", ""),
                    "currency": currency,
                    "agency_iata": row.get("agency_iata", ""),
                    "correlation_id": correlation_id,
                    "processed_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            }
        })
        if len(batch) == 25:
            _flush_batch(table, batch, correlation_id)
            written += len(batch)
            batch = []
    if batch:
        _flush_batch(table, batch, correlation_id)
        written += len(batch)
    if duplicates:
        emit("INFO", "batch.duplicates_summary", correlation_id,
             boundary="lambda->dynamodb", duplicates=duplicates)
    return written


def _flush_batch(table, batch, correlation_id):
    try:
        resp = dynamodb.meta.client.batch_write_item(
            RequestItems={SETTLEMENT_TABLE: batch}
        )
        unprocessed = resp.get("UnprocessedItems", {}).get(SETTLEMENT_TABLE, [])
        if unprocessed:
            emit("WARNING", "batch.unprocessed_items", correlation_id,
                 boundary="lambda->dynamodb", count=len(unprocessed))
    except ClientError as e:
        emit("ERROR", "batch.write_failed", correlation_id,
             boundary="lambda->dynamodb",
             error_code=e.response["Error"]["Code"],
             error=e.response["Error"]["Message"][:300])
        raise


def _publish_summary(airline_code, record_count, correlation_id, bucket, key):
    if not SETTLEMENT_TOPIC_ARN:
        emit("INFO", "notify.skipped", correlation_id, reason="no SETTLEMENT_TOPIC_ARN")
        return
    try:
        sns.publish(
            TopicArn=SETTLEMENT_TOPIC_ARN,
            Subject=f"Settlement loaded: {airline_code} - {record_count} records",
            Message=json.dumps({
                "airline_code": airline_code,
                "record_count": record_count,
                "bucket": bucket,
                "key": key,
                "correlation_id": correlation_id,
            }),
            MessageAttributes={
                "correlation_id": {"DataType": "String", "StringValue": correlation_id},
                "airline_code": {"DataType": "String", "StringValue": airline_code or "unknown"},
            },
        )
        emit("INFO", "notify.published", correlation_id,
             boundary="lambda->sns", airline_code=airline_code, record_count=record_count)
    except ClientError as e:
        emit("ERROR", "notify.failed", correlation_id,
             boundary="lambda->sns",
             error_code=e.response["Error"]["Code"],
             error=e.response["Error"]["Message"][:300])
        raise


def _get_s3_correlation_id(bucket, key):
    try:
        resp = s3.head_object(Bucket=bucket, Key=key)
        return (resp.get("Metadata") or {}).get("correlation-id")
    except ClientError:
        return None


def handler(event, context):
    bucket = event["bucket"]
    key = event["key"]
    s3_corr = _get_s3_correlation_id(bucket, key)
    correlation_id = s3_corr or event.get("correlation_id", f"m1-{uuid.uuid4().hex[:12]}")

    started = time.time()
    airline_code = _parse_airline_code(key)

    emit("INFO", "batch.started", correlation_id,
         boundary="sfn->lambda", bucket=bucket, key=key, airline_code=airline_code,
         correlation_source="s3-metadata" if s3_corr else "event")

    records = _read_csv(bucket, key)
    record_count = len(records)

    emit("INFO", "batch.csv_parsed", correlation_id,
         boundary="lambda->s3", bucket=bucket, key=key, record_count=record_count)

    written = _batch_write_records(records, airline_code, correlation_id)

    emit("INFO", "batch.written", correlation_id,
         boundary="lambda->dynamodb", record_count=written)

    _publish_summary(airline_code, record_count, correlation_id, bucket, key)

    duration_ms = int((time.time() - started) * 1000)
    emit("INFO", "batch.completed", correlation_id,
         airline_code=airline_code, record_count=record_count,
         written=written, duration_ms=duration_ms)

    return {
        "airline_code": airline_code,
        "record_count": record_count,
        "written": written,
        "correlation_id": correlation_id,
        "duration_ms": duration_ms,
    }
