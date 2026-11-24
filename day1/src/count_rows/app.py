"""Count data rows in a CSV that lands under raw/.

Trigger : EventBridge "Object Created" (bucket has EventBridge notifications on).
Reads   : s3:GetObject on raw/* only (streamed, never loaded whole, never logged).
Logging : one JSON object per line, always a correlation_id, never file contents.
"""
import csv
import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
s3 = boto3.client("s3")


def emit(level, message, correlation_id, **fields):
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": "accelya-m1-count-rows",
        "learner_id": LEARNER_ID,
        "correlation_id": correlation_id,
        "message": message,
        **fields,
    }
    sys.stdout.write(json.dumps(line) + "\n")


def count_rows(bucket, key):
    """Data rows = CSV records minus header. csv module handles quoted newlines."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    lines = (b.decode("utf-8-sig" if i == 0 else "utf-8")
             for i, b in enumerate(body.iter_lines(keepends=True)))
    records = sum(1 for row in csv.reader(lines) if any(f.strip() for f in row))
    return max(records - 1, 0)


def handler(event, context):
    detail = event["detail"]                       # EventBridge S3 shape
    bucket = detail["bucket"]["name"]
    key = detail["object"]["key"]                  # NOT url-encoded in EventBridge
    cid = f"m1-{context.aws_request_id[:8]}"
    started = time.time()

    emit("INFO", "count_rows.start", cid, bucket=bucket, key=key,
         bytes=detail["object"].get("size"))
    try:
        rows = count_rows(bucket, key)
    except ClientError as e:
        emit("ERROR", "count_rows.failed", cid, bucket=bucket, key=key,
             error_code=e.response["Error"]["Code"], error=e.response["Error"]["Message"][:300])
        raise                                       # let Lambda retry / surface the failure
    except (UnicodeDecodeError, csv.Error) as e:
        emit("ERROR", "count_rows.bad_csv", cid, bucket=bucket, key=key,
             error_type=type(e).__name__, error=str(e)[:300])
        raise

    emit("INFO", "count_rows.done", cid, bucket=bucket, key=key, record_count=rows,
         duration_ms=int((time.time() - started) * 1000))
    return {"key": key, "record_count": rows}
