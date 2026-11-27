#!/usr/bin/env python3
"""Print the raw log messages of ONE ECS task (its own CloudWatch stream), one per line.

Usage (from the repo root, after source day5/scripts/env.sh <id>):
    python3 day5/scripts/task_logs.py $TASK | python3 day2/scripts/check_logs.py -
    python3 day5/scripts/task_logs.py $TASK | grep -c "$L-d5-001"
The stream is <awslogs-stream-prefix>/<container>/<task-id> = enrich/enrich/<task-id> in $LG.
Raw messages only: no timestamp prefix, nothing from any other run.
"""
import os
import sys

import boto3


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr); return 2
    lg = os.environ.get("LG")
    if not lg:
        print("LG is not set: source day5/scripts/env.sh <your-id>", file=sys.stderr); return 2
    stream = "enrich/enrich/" + sys.argv[1].rsplit("/", 1)[-1]
    logs = boto3.client("logs")
    kw, last = {"logGroupName": lg, "logStreamName": stream, "startFromHead": True}, None
    while True:
        page = logs.get_log_events(**kw)
        for e in page["events"]:
            print(e["message"].rstrip("\n"))
        if page["nextForwardToken"] == last:
            return 0
        last = kw["nextToken"] = page["nextForwardToken"]


if __name__ == "__main__":
    sys.exit(main())
