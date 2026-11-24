"""Accelya M1 Option 1 - Parser Lambda.

Hop 1  S3 -> Lambda       : async push on raw/*.csv. A failed invoke retries twice, then goes to the SQS DLQ.
Hop 2  Lambda -> DynamoDB : sync TransactWriteItems. FILE#<etag> is written only if absent, in the same
                            transaction as the per-airline WINDOW items, so a duplicate S3 event or an
                            identical re-upload is counted once.
correlation_id: x-amz-meta-correlation-id if the uploader set one, else minted here. It is stored on every
DynamoDB item so the aggregator logs it too.
Logs follow docs/LOGGING_CONTRACT.md: one JSON object per line, identifiers and counts only - never rows.
"""
import csv
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

SERVICE = "accelya-m1-opt1-parser"
LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
TABLE = os.environ.get("TABLE_NAME", "")
TTL_DAYS = 7
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
AIRLINE_RE = re.compile(r"^[A-Z0-9]{2}$")

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")


def emit(level, message, correlation_id, boundary, **fields):
    line = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "level": level,
            "service": SERVICE, "learner_id": LEARNER_ID, "correlation_id": correlation_id,
            "boundary": boundary, "message": message}
    line.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(line) + "\n")


def count_by_airline(lines):
    """Ticket rows per airline_code, or None if the header has no airline_code column."""
    reader = csv.DictReader(line.decode("utf-8-sig") for line in lines if line.strip())
    if not reader.fieldnames or "airline_code" not in reader.fieldnames:
        return None
    counts = Counter()
    for row in reader:
        code = (row.get("airline_code") or "").strip().upper()
        counts[code if AIRLINE_RE.match(code) else "unknown"] += 1
    return counts


def _items(etag, key, cid, counts, now):
    window = now.strftime("%Y-%m-%dT%H:%M")
    expires = {"N": str(int((now + timedelta(days=TTL_DAYS)).timestamp()))}
    items = [{"Put": {"TableName": TABLE,
                      "Item": {"pk": {"S": f"FILE#{etag}"}, "sk": {"S": "FILE"}, "key": {"S": key},
                               "correlation_id": {"S": cid}, "expires_at": expires},
                      "ConditionExpression": "attribute_not_exists(pk)"}}]
    # shortcut: one transaction holds 100 items, so at most 99 airlines per file; batch it if files ever mix more
    for airline, n in counts.items():
        items.append({"Put": {"TableName": TABLE,
                              "Item": {"pk": {"S": f"WINDOW#{window}"}, "sk": {"S": f"{airline}#{etag}"},
                                       "airline_code": {"S": airline}, "ticket_count": {"N": str(n)},
                                       "correlation_id": {"S": cid}, "expires_at": expires}}})
    return items


def handler(event, context):
    files = 0
    for record in event.get("Records", []):
        started = time.time()
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        etag = record["s3"]["object"].get("eTag") or key
        cid = f"m1-{uuid.uuid4().hex[:12]}"

        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            emit("ERROR", "file.read_failed", cid, "lambda->s3", bucket=bucket, key=key,
                 error_type="ClientError", error_code=e.response["Error"]["Code"])
            raise
        meta_cid = obj.get("Metadata", {}).get("correlation-id", "")
        cid = meta_cid if CID_RE.match(meta_cid) else cid
        emit("INFO", "file.received", cid, "s3->lambda", bucket=bucket, key=key, bytes=obj.get("ContentLength"))

        try:
            counts = count_by_airline(obj["Body"].iter_lines())
        except (UnicodeDecodeError, csv.Error) as e:
            emit("ERROR", "file.parse_failed", cid, "internal", key=key, error_type=type(e).__name__)
            raise
        if counts is None:
            emit("ERROR", "file.parse_failed", cid, "internal", key=key, error_type="HeaderMismatch")
            raise ValueError("HeaderMismatch")   # no retry will fix it; after 3 attempts it sits in the DLQ
        if not counts:
            emit("WARNING", "file.empty", cid, "internal", key=key, record_count=0)

        try:
            ddb.transact_write_items(TransactItems=_items(etag, key, cid, counts, datetime.now(timezone.utc)))
        except ClientError as e:
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            if e.response["Error"]["Code"] == "TransactionCanceledException" and reasons[:1] == ["ConditionalCheckFailed"]:
                emit("INFO", "file.duplicate_skipped", cid, "lambda->dynamodb", key=key)
                continue
            emit("ERROR", "file.write_failed", cid, "lambda->dynamodb", key=key,
                 error_type="ClientError", error_code=e.response["Error"]["Code"])
            raise

        for airline, n in sorted(counts.items()):
            emit("INFO", "file.counted", cid, "lambda->dynamodb", key=key, airline_code=airline, record_count=n)
        emit("INFO", "file.completed", cid, "internal", key=key, record_count=sum(counts.values()),
             duration_ms=int((time.time() - started) * 1000))
        files += 1
    return {"files": files}
