"""Day 1 Lab 2 - fixture test for the ingest Lambda. No AWS account needed.

Run from the repo root:   python3 day1/tests/test_ingest.py

Checks:
  1. record_count is correct for the AI fixture file (25 rows)
  2. every application log line has a correlation_id
  3. no ticket data (ticket numbers, fares) leaks into the logs
  4. YOUR CHANGE: file.received carries airline_code, parsed from the object key
     (ticket_issue_<AIRLINE>_<date>.csv -> "AI").  Fails until you add it.
"""
import io
import json
import os
import sys
from pathlib import Path

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                  LEARNER_ID="test", PROBE_ROLE_ARN="")
os.environ.pop("AWS_PROFILE", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "ingest"))

import app  # noqa: E402
from botocore.response import StreamingBody  # noqa: E402
from botocore.stub import Stubber  # noqa: E402

KEY = "raw/ticket_issue_AI_2026-11-23.csv"
DATA = (ROOT / "data" / "ticket_issue_AI_2026-11-23.csv").read_bytes()


class Ctx:
    aws_request_id = "test-req-0001"
    memory_limit_in_mb = "256"


def run():
    st = Stubber(app.s3)
    st.add_response("head_object", {"ContentLength": len(DATA), "Metadata": {"correlation-id": "t-001"}},
                    {"Bucket": "b", "Key": KEY})
    st.add_response("get_object", {"Body": StreamingBody(io.BytesIO(DATA), len(DATA))}, {"Bucket": "b", "Key": KEY})
    st.activate()
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        app.handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": KEY, "size": len(DATA)}}}]}, Ctx())
    finally:
        sys.stdout = real
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def main():
    lines = run()
    received = next(l for l in lines if l["message"] == "file.received")
    checks = [
        ("record_count == 25", received.get("record_count") == 25),
        ("correlation_id on every line", all(l.get("correlation_id") == "t-001" for l in lines)),
        ("no ticket data in logs", not any("098-" in json.dumps(l) for l in lines)),
        ("airline_code == 'AI' on file.received  (your change)", received.get("airline_code") == "AI"),
    ]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    failed = [n for n, ok in checks if not ok]
    print("\nALL PASS - deploy it." if not failed else f"\n{len(failed)} failing - fix the handler, then rerun.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
