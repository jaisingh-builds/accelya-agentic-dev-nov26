"""Accelya M1 ingest Lambda.

Boundary 1  S3 -> Lambda   : async push, S3 event notification on raw/ prefix.
Boundary 2  Lambda -> STS  : sync call, ingest role assumes the reader role.
Boundary 3  Lambda -> S3   : sync call with the *reader* role's temporary credentials.

Logging rule (preview of the Day 2 logging contract):
  one JSON object per line, always a correlation_id, never the file contents.
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
PROBE_ROLE_ARN = os.environ.get("PROBE_ROLE_ARN", "")
PROBE_EXTERNAL_ID = os.environ.get("PROBE_EXTERNAL_ID", "")
PROCESSED_TABLE = os.environ.get("PROCESSED_TABLE", "")
CLAIM_TTL_DAYS = 30

s3 = boto3.client("s3")
sts = boto3.client("sts")
dynamodb = boto3.client("dynamodb")

_COLD_START = True          # module scope survives between warm invocations
_INIT_AT = time.time()


def emit(level, message, correlation_id, **fields):
    """Write one structured log line. Day 4 replaces this with Powertools Logger."""
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": "accelya-m1-ingest",
        "learner_id": LEARNER_ID,
        "correlation_id": correlation_id,
        "message": message,
        **fields,
    }
    sys.stdout.write(json.dumps(line) + "\n")


def _correlation_id(bucket, key, request_id):
    """Use x-amz-meta-correlation-id if the uploader set one, else mint one."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        cid = head.get("Metadata", {}).get("correlation-id")
        if cid:
            return cid, head["ContentLength"]
        return f"m1-{request_id[:8]}-{uuid.uuid4().hex[:6]}", head["ContentLength"]
    except ClientError:
        return f"m1-{request_id[:8]}-{uuid.uuid4().hex[:6]}", None


def _claim_file(bucket, key, etag, correlation_id):
    """Record bucket/key#etag once. True = first time seen, False = duplicate.

    Same object bytes => same eTag, so this catches both a duplicate S3 delivery
    and a resend of an identical file. A corrected file has a new eTag and is processed.
    """
    if not PROCESSED_TABLE:
        return True
    try:
        dynamodb.put_item(
            TableName=PROCESSED_TABLE,
            Item={
                "file_id": {"S": f"{bucket}/{key}#{etag}"},
                "correlation_id": {"S": correlation_id},
                "first_seen_ts": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                "expires_at": {"N": str(int(time.time()) + CLAIM_TTL_DAYS * 86400)},
            },
            ConditionExpression="attribute_not_exists(file_id)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        emit("ERROR", "file.claim_failed", correlation_id, boundary="lambda->dynamodb",
             bucket=bucket, key=key, error_type="ClientError",
             error_code=e.response["Error"]["Code"])
        raise


def _release_claim(bucket, key, etag, correlation_id):
    """Processing failed after the claim: remove it so the S3 retry is not skipped."""
    try:
        dynamodb.delete_item(TableName=PROCESSED_TABLE,
                             Key={"file_id": {"S": f"{bucket}/{key}#{etag}"}})
    except ClientError as e:
        emit("ERROR", "file.claim_release_failed", correlation_id, boundary="lambda->dynamodb",
             bucket=bucket, key=key, error_type="ClientError",
             error_code=e.response["Error"]["Code"])


def _count_records(bucket, key):
    """Count data rows without ever logging them."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    lines = sum(1 for line in body.iter_lines() if line.strip())
    return max(lines - 1, 0)   # minus CSV header


def _probe_reader_role(bucket, correlation_id):
    """Assume the reader role, then list validated/ with ITS credentials."""
    if not PROBE_ROLE_ARN:
        emit("INFO", "probe.skipped", correlation_id, reason="no PROBE_ROLE_ARN (fallback mode)")
        return
    params = {"RoleArn": PROBE_ROLE_ARN, "RoleSessionName": f"m1-{LEARNER_ID}"[:64]}
    if PROBE_EXTERNAL_ID:
        params["ExternalId"] = PROBE_EXTERNAL_ID
    try:
        creds = sts.assume_role(**params)["Credentials"]
    except ClientError as e:
        emit("ERROR", "probe.assume_role.denied", correlation_id,
             boundary="lambda->sts", error_code=e.response["Error"]["Code"],
             error=e.response["Error"]["Message"][:300],
             external_id_sent=bool(PROBE_EXTERNAL_ID))
        return
    reader_s3 = boto3.client(
        "s3",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    emit("INFO", "probe.assume_role.ok", correlation_id, boundary="lambda->sts",
         expires=creds["Expiration"].isoformat())
    for prefix in ("validated/", "raw/"):   # first is allowed, second must be denied
        try:
            resp = reader_s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=5)
            emit("INFO", "probe.list.ok", correlation_id, boundary="lambda->s3(reader)",
                 prefix=prefix, key_count=resp.get("KeyCount", 0))
        except ClientError as e:
            emit("WARNING", "probe.list.denied", correlation_id, boundary="lambda->s3(reader)",
                 prefix=prefix, error_code=e.response["Error"]["Code"])


def handler(event, context):
    global _COLD_START
    cold = _COLD_START
    _COLD_START = False

    files = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        etag = record["s3"]["object"].get("eTag", "").strip('"')
        cid, size = _correlation_id(bucket, key, context.aws_request_id)
        if not _claim_file(bucket, key, etag, cid):
            emit("INFO", "file.duplicate_skipped", cid, boundary="lambda->dynamodb",
                 bucket=bucket, key=key, bytes=size)
            continue
        started = time.time()
        try:
            rows = _count_records(bucket, key)
        except ClientError as e:
            emit("ERROR", "file.read_failed", cid, boundary="lambda->s3",
                 bucket=bucket, key=key, error_type="ClientError",
                 error_code=e.response["Error"]["Code"])
            _release_claim(bucket, key, etag, cid)
            raise
        emit("INFO", "file.received", cid,
             boundary="s3->lambda", bucket=bucket, key=key, bytes=size,
             record_count=rows, cold_start=cold,
             init_age_s=round(started - _INIT_AT, 1),
             memory_limit_mb=int(context.memory_limit_in_mb),
             duration_ms=int((time.time() - started) * 1000))
        _probe_reader_role(bucket, cid)
        files += 1

    return {"files": files, "cold_start": cold}
