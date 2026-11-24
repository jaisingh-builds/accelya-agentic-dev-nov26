"""Accelya M1 Option 1 - Aggregator Lambda.

Hop 3  EventBridge -> Lambda : async push, rate(1 minute). Each run reports the PREVIOUS minute only, so a
                               file is reported once, at most ~2 minutes after the parser counted it.
Hop 4  Lambda -> DynamoDB    : sync Query on one partition, WINDOW#<minute> - a bounded read.
Hop 5  Lambda -> SNS         : sync Publish of per-airline ticket counts to the finance topic.
Every file's own correlation_id is logged (window.file_included), so one file is traceable from upload to SNS.
Empty minutes publish and log nothing.
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

SERVICE = "accelya-m1-opt1-aggregator"
LEARNER_ID = os.environ.get("LEARNER_ID", "unknown")
TABLE = os.environ.get("TABLE_NAME", "")
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")

ddb = boto3.client("dynamodb")
sns = boto3.client("sns")


def emit(level, message, correlation_id, boundary, **fields):
    line = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "level": level,
            "service": SERVICE, "learner_id": LEARNER_ID, "correlation_id": correlation_id,
            "boundary": boundary, "message": message}
    line.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(line) + "\n")


def previous_minute(event_time):
    """'2026-11-24T10:15:00Z' -> '2026-11-24T10:14'."""
    t = datetime.strptime(event_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (t - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")


def handler(event, context):
    # shortcut: a parser write that commits in the last milliseconds of a minute can land after this read; add a
    # one-minute grace (read minute -2) if finance ever reports a missing file
    window = previous_minute(event["time"])
    cid = f"agg-{window}"

    items = []
    try:
        for page in ddb.get_paginator("query").paginate(
                TableName=TABLE, KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": {"S": f"WINDOW#{window}"}}):
            items += page["Items"]
    except ClientError as e:
        emit("ERROR", "window.read_failed", cid, "lambda->dynamodb",
             error_type="ClientError", error_code=e.response["Error"]["Code"])
        raise
    if not items:
        return {"window": window, "files": 0}

    counts, file_cids = Counter(), set()
    for it in items:
        airline, n, file_cid = it["airline_code"]["S"], int(it["ticket_count"]["N"]), it["correlation_id"]["S"]
        counts[airline] += n
        file_cids.add(file_cid)
        emit("INFO", "window.file_included", file_cid, "lambda->dynamodb", airline_code=airline, record_count=n)

    message = {"window_utc": window, "ticket_counts": dict(sorted(counts.items())),
               "file_count": len(file_cids), "correlation_ids": sorted(file_cids)}
    try:
        resp = sns.publish(TopicArn=TOPIC_ARN, Subject=f"Ticket counts {window} UTC", Message=json.dumps(message),
                           MessageAttributes={"correlation_id": {"DataType": "String", "StringValue": cid}})
    except ClientError as e:
        emit("ERROR", "window.publish_failed", cid, "lambda->sns",
             error_type="ClientError", error_code=e.response["Error"]["Code"])
        raise

    for airline, n in sorted(counts.items()):
        emit("INFO", "window.airline_total", cid, "lambda->sns", airline_code=airline, record_count=n)
    emit("INFO", "window.published", cid, "lambda->sns", message_id=resp["MessageId"],
         record_count=sum(counts.values()))
    return {"window": window, "files": len(file_cids), "ticket_counts": dict(counts)}
