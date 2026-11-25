#!/usr/bin/env python3
"""Day 3 Demo 1 - build ONE S3 ObjectCreated event for a file already in your bucket, so you can deliver
the SAME event to the ingest Lambda twice (what S3's at-least-once delivery can do).

    python3 day3/scripts/s3_event.py --bucket $B --key raw/ticket_issue_AI_2026-11-25.csv > day3/outputs/s3_event.json

The event carries identifiers only (bucket, key, size, eTag, versionId if any) - never file contents.
Pass --etag/--size from `aws s3api head-object`, or let the script read them with your current profile.
"""
import argparse
import json
import sys
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser(description="Print one S3 ObjectCreated:Put event (JSON) for an existing object")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--etag", help="from head-object (quotes optional); read from S3 if omitted")
    ap.add_argument("--size", type=int, help="bytes; read from S3 if omitted")
    ap.add_argument("--version-id", default=None)
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    if a.etag is None or a.size is None:
        try:
            import boto3
            h = boto3.client("s3", region_name=a.region).head_object(Bucket=a.bucket, Key=a.key)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"Can't read the object ({type(e).__name__}): pass --etag and --size from aws s3api head-object")
        a.etag = a.etag or h["ETag"]
        a.size = a.size if a.size is not None else h["ContentLength"]
        a.version_id = a.version_id or h.get("VersionId")
    obj = {"key": a.key, "size": a.size, "eTag": a.etag.strip('"'), "sequencer": "0066A1B2C3D4E5F601"}
    if a.version_id:
        obj["versionId"] = a.version_id
    event = {"Records": [{
        "eventVersion": "2.1", "eventSource": "aws:s3", "awsRegion": a.region,
        "eventTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "eventName": "ObjectCreated:Put",
        "s3": {"s3SchemaVersion": "1.0", "configurationId": "day3-replay",
               "bucket": {"name": a.bucket, "arn": f"arn:aws:s3:::{a.bucket}"}, "object": obj}}]}
    print(json.dumps(event, indent=1))


if __name__ == "__main__":
    main()
