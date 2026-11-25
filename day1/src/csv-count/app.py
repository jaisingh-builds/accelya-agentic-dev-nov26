"""Accelya M1 csv-count Lambda - counts data rows in a CSV landed under raw/.

Hop 1  S3 -> Lambda : async push, S3 ObjectCreated on raw/*.csv. Lambda retries a failed invoke twice.
Hop 2  Lambda -> S3 : sync HeadObject + GetObject on raw/* with the csv-count role (s3:GetObject only).

Logging follows docs/LOGGING_CONTRACT.md: one JSON object per line, identifiers and counts only,
never file rows or raw exception messages.
"""
import codecs
import csv
import json
import os
import re
import sys
import time
import uuid
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

SERVICE = "accelya-m1-csv-count"
LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9/_.=-]{1,256}$")
AIRLINE_RE = re.compile(r"^ticket_issue_([A-Z0-9]{2})_")

s3 = boto3.client("s3", config=Config(retries={"mode": "standard"}, connect_timeout=5, read_timeout=20))

_COLD_START = True


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


def _airline_code(key):
    """ticket_issue_<AIRLINE>_<date>.csv -> AIRLINE, else 'unknown'."""
    m = AIRLINE_RE.match(key.rsplit("/", 1)[-1])
    return m.group(1) if m else "unknown"


def _correlation_id(bucket, key, log_key, request_id):
    """Use x-amz-meta-correlation-id if the uploader set a valid one, else mint one."""
    minted = f"m1-{request_id[:8]}-{uuid.uuid4().hex[:6]}"
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        emit("WARNING", "file.head.failed", minted, "lambda->s3", bucket=bucket, key=log_key,
             error_type=type(e).__name__, error_code=e.response["Error"]["Code"])
        return minted, None
    cid = head.get("Metadata", {}).get("correlation-id", "")
    return (cid if CID_RE.match(cid) else minted), head["ContentLength"]


def _count_rows(bucket, key):
    """Count non-blank CSV data rows (header excluded). Streams the body; rows are never kept or logged."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    rows = sum(1 for row in csv.reader(codecs.getreader("utf-8-sig")(body)) if row)
    return max(rows - 1, 0)


def handler(event, context):
    global _COLD_START
    cold, _COLD_START = _COLD_START, False

    files = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        log_key = key if KEY_RE.match(key) else None
        cid, size = _correlation_id(bucket, key, log_key, context.aws_request_id)
        started = time.monotonic()
        try:
            rows = _count_rows(bucket, key)
        except (ClientError, BotoCoreError) as e:
            emit("ERROR", "file.read.failed", cid, "lambda->s3", bucket=bucket, key=log_key,
                 error_type=type(e).__name__,
                 error_code=e.response["Error"]["Code"] if isinstance(e, ClientError) else None)
            raise  # possibly transient: let Lambda's async retry have another go
        except (UnicodeDecodeError, csv.Error) as e:
            emit("ERROR", "file.parse.failed", cid, "internal", bucket=bucket, key=log_key,
                 error_type=type(e).__name__)
            continue  # deterministic: retrying the same bytes cannot succeed
        emit("INFO", "file.received", cid, "s3->lambda",
             bucket=bucket, key=log_key, bytes=size, airline_code=_airline_code(key),
             record_count=rows, cold_start=cold,
             memory_limit_mb=int(context.memory_limit_in_mb),
             duration_ms=int((time.monotonic() - started) * 1000))
        files += 1

    return {"files": files, "cold_start": cold}
