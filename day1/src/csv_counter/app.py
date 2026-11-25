"""Accelya csv-counter Lambda.

Triggered by S3 ObjectCreated on the raw/ prefix.  Counts data rows in the
uploaded CSV and emits a single structured log line.  Never logs file contents,
PNR lists, fare amounts, or full payloads - only the object key and row count.

Service-boundary answers (hop: S3 -> Lambda)
  1 Push      : S3 event notification invokes this function.
  2 Async     : S3 does not wait; Lambda retries async events twice on error.
  3 Payload   : S3 event JSON (Records[].s3.bucket.name, object.key, size).
  4 Role      : csv-counter-role, s3:GetObject on raw/* only.
  5 Failure   : Lambda async retry x2; no DLQ configured (add one for production).
  6 Corr. ID  : x-amz-meta-correlation-id when present; minted here otherwise.
"""
import json
import os
import sys
import time
import uuid
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
SERVICE_NAME = "accelya-m1-csv-counter"

s3 = boto3.client("s3")


def _emit(level: str, message: str, correlation_id: str, **fields) -> None:
    """Write one structured log line - one JSON object, nothing more."""
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": SERVICE_NAME,
        "learner_id": LEARNER_ID,
        "correlation_id": correlation_id,
        "message": message,
        **fields,
    }
    sys.stdout.write(json.dumps(line) + "\n")


def _correlation_id(bucket: str, key: str, request_id: str) -> tuple[str, int | None]:
    """Return (correlation_id, byte_size).

    Uses x-amz-meta-correlation-id when the uploader set one; mints a new id
    otherwise.  Never logs the object metadata values.
    """
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        cid = head.get("Metadata", {}).get("correlation-id")
        size = head.get("ContentLength")
        if cid:
            return cid, size
        return f"csv-{request_id[:8]}-{uuid.uuid4().hex[:6]}", size
    except ClientError:
        return f"csv-{request_id[:8]}-{uuid.uuid4().hex[:6]}", None


def _count_csv_rows(bucket: str, key: str) -> int:
    """Stream the CSV object and return the number of data rows (header excluded).

    Uses iter_lines() so only one chunk lives in memory at a time - safe for
    large settlement files.  Never stores or logs row contents.
    """
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    total = sum(1 for line in body.iter_lines() if line.strip())
    return max(total - 1, 0)   # subtract the header row


def handler(event, context):
    """Lambda entry point.  One log line per file, fields: key (identifier only),
    byte_size, row_count, duration_ms, boundary.
    """
    processed = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        cid, byte_size = _correlation_id(bucket, key, context.aws_request_id)
        t0 = time.time()

        try:
            row_count = _count_csv_rows(bucket, key)
        except ClientError as exc:
            _emit(
                "ERROR",
                "csv.count.failed",
                cid,
                boundary="s3->lambda",
                bucket=bucket,
                key=key,
                error_code=exc.response["Error"]["Code"],
            )
            continue

        # Single structured log line per AGENTS.md logging contract.
        _emit(
            "INFO",
            "csv.row_count",
            cid,
            boundary="s3->lambda",
            bucket=bucket,
            key=key,
            byte_size=byte_size,
            row_count=row_count,
            duration_ms=int((time.time() - t0) * 1000),
        )
        processed += 1

    return {"processed": processed}
