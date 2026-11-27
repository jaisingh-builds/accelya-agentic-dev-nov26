#!/usr/bin/env python3
"""Run SQL in your Athena workgroup and print the result and the bytes scanned.

Usage (from the repo root, after source day5/scripts/env.sh <id>):
    python3 day5/scripts/athena.py day5/sql/01_create_curated.sql        # a file (one statement per file)
    python3 day5/scripts/athena.py -e "SELECT count(*) FROM ticket_issue"  # inline
Runs in workgroup $WG against database $GDB. ${BUCKET} in the SQL is replaced with your bucket.
Exit 0 = SUCCEEDED; 1 = FAILED/CANCELLED (the reason is printed).
"""
import os
import sys
import time
from pathlib import Path

import boto3


def run(sql, wg, db, client=None, quiet=False):
    a = client or boto3.client("athena")
    qid = a.start_query_execution(QueryString=sql, WorkGroup=wg, QueryExecutionContext={"Database": db})["QueryExecutionId"]
    while True:
        q = a.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        if q["Status"]["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    rows = []
    if q["Status"]["State"] == "SUCCEEDED" and q.get("StatementType") in ("DML", "UTILITY"):
        for page in a.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
            rows += [[c.get("VarCharValue", "") for c in r["Data"]] for r in page["ResultSet"]["Rows"]]
    return q, rows


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 2
    sql = args[1] if args[0] == "-e" else Path(args[0]).read_text()
    wg, db, bucket = os.environ.get("WG"), os.environ.get("GDB"), os.environ.get("B")
    if not (wg and db and bucket):
        print("WG, GDB and B are not set: source day5/scripts/env.sh <your-id>"); return 2
    sql = sql.replace("${BUCKET}", bucket).strip().rstrip(";")
    q, rows = run(sql, wg, db)
    st = q["Status"]
    stats = q.get("Statistics", {})
    for r in rows[:40]:
        print("  " + " | ".join(r))
    if len(rows) > 40:
        print(f"  ... {len(rows) - 40} more rows")
    n = max(len(rows) - (q.get("StatementType") == "DML"), 0)   # SELECT results carry a header row
    print(f"{st['State']}  rows={n}  scanned={stats.get('DataScannedInBytes', 0)} bytes  "
          f"time={stats.get('TotalExecutionTimeInMillis', 0)} ms  id={q['QueryExecutionId']}")
    if st["State"] != "SUCCEEDED":
        print("reason:", st.get("StateChangeReason", "?"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
