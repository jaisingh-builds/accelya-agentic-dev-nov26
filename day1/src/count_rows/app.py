"""Option A: count rows per airline file and record the result for finance.

Flow    : S3 -> EventBridge -> SQS -> this Lambda -> DynamoDB
Trigger : SQS record whose body is the EventBridge "Object Created" event.
Reads   : s3:GetObject on raw/* only (streamed, never loaded whole, never logged).
Writes  : one DynamoDB item per airline+date (re-sent file overwrites, never adds).
Logging : one JSON object per line, always a correlation_id. Counts and file names
          only - never fare_amount, PNRs or ticket numbers.

Error policy
  * Bad file (name, header, encoding, CSV syntax) -> record FAILED with 0 rows, do NOT
    raise: retrying a bad file can never succeed and finance must still see a row.
  * Transient AWS error (throttle, 5xx) -> raise, so SQS retries then dead-letters.
"""
import csv
import json
import os
import re
import sys
import time

import boto3
from botocore.exceptions import ClientError

LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
TABLE_NAME = os.environ.get("TABLE_NAME", "")

EXPECTED_HEADER = ["pnr", "ticket_number", "airline_code", "fare_class", "od_pair",
                   "issue_date", "fare_amount", "currency", "agency_iata"]
NAME_RE = re.compile(r"^ticket_issue_([A-Z0-9]{2,3})_(\d{4}-\d{2}-\d{2})\.csv$")

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")


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


class BadFile(Exception):
    """File is unusable; record FAILED, do not retry."""


def parse_name(key):
    m = NAME_RE.match(key.rsplit("/", 1)[-1])
    if not m:
        raise BadFile("file name is not ticket_issue_<AIRLINE>_<YYYY-MM-DD>.csv")
    return m.group(1), m.group(2)


def count_rows(bucket, key, version_id=None):
    """Data rows = CSV records minus header. Header is validated, never logged."""
    params = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    body = s3.get_object(**params)["Body"]
    try:
        lines = (b.decode("utf-8-sig" if i == 0 else "utf-8")
                 for i, b in enumerate(body.iter_lines(keepends=True)))
        reader = csv.reader(lines)
        header = next(reader, None)
        if header is None:
            raise BadFile("file is empty")
        if [h.strip() for h in header] != EXPECTED_HEADER:
            raise BadFile("header does not match the expected 9 columns")
        return sum(1 for row in reader if any(f.strip() for f in row))
    except (UnicodeDecodeError, csv.Error) as e:
        raise BadFile(f"unreadable csv: {type(e).__name__}") from e


def record(pk, airline, file_date, file_name, rows, status, version_id, event_time, cid, reason=""):
    """Upsert by airline+date. Older events never overwrite newer ones; replays are harmless."""
    item = {
        "pk": {"S": pk}, "airline_code": {"S": airline}, "file_date": {"S": file_date},
        "file_name": {"S": file_name}, "row_count": {"N": str(rows)}, "status": {"S": status},
        "version_id": {"S": version_id or "none"}, "updated_at": {"S": event_time},
        "correlation_id": {"S": cid},
    }
    if reason:
        item["reason"] = {"S": reason}
    try:
        ddb.put_item(TableName=TABLE_NAME, Item=item,
                     ConditionExpression="attribute_not_exists(pk) OR updated_at <= :t",
                     ExpressionAttributeValues={":t": {"S": event_time}})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            emit("WARNING", "count_rows.stale_event_ignored", cid, file_name=file_name)
            return
        raise


def process(event, request_id):
    detail = event["detail"]
    bucket = detail["bucket"]["name"]
    key = detail["object"]["key"]                  # not url-encoded in EventBridge
    version_id = detail["object"].get("version-id")
    event_time = event.get("time") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cid = f"m1-{request_id[:8]}-{event.get('id', '')[:6]}"
    file_name = key.rsplit("/", 1)[-1]
    started = time.time()

    emit("INFO", "count_rows.start", cid, file_name=file_name, bytes=detail["object"].get("size"))
    try:
        airline, file_date = parse_name(key)
        pk = f"{airline}#{file_date}"
        rows = count_rows(bucket, key, version_id)
        status, reason = "OK", ""
    except BadFile as e:
        airline, file_date = (parse_name_safe(key))
        pk = f"{airline}#{file_date}"
        rows, status, reason = 0, "FAILED", str(e)
        emit("ERROR", "count_rows.bad_file", cid, file_name=file_name, reason=reason)

    record(pk, airline, file_date, file_name, rows, status, version_id, event_time, cid, reason)
    emit("INFO", "count_rows.done", cid, file_name=file_name, airline_code=airline,
         record_count=rows, status=status, duration_ms=int((time.time() - started) * 1000))
    return {"pk": pk, "record_count": rows, "status": status}


def parse_name_safe(key):
    try:
        return parse_name(key)
    except BadFile:
        return "UNKNOWN", key.rsplit("/", 1)[-1]   # keeps distinct bad names from colliding


def handler(event, context):
    """SQS batch (size 1 in the template). Only transient errors escape and trigger a retry."""
    results = [process(json.loads(r["body"]), context.aws_request_id) for r in event["Records"]]
    return {"processed": len(results)}
