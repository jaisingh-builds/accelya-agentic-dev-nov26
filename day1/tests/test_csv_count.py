"""Fixture test for the csv-count Lambda. No AWS account needed.

Run from the repo root:   python3 day1/tests/test_csv_count.py

Checks:
  1. record_count is correct for the AI fixture file (25 rows)
  2. every application log line has the uploader's correlation_id
  3. no ticket data (ticket numbers, fares) leaks into the logs
  4. airline_code parsed from the object key
  5. every line conforms to docs/logging-contract.json
  6. S3 read failure logs ERROR with error_type and re-raises
"""
import io
import json
import os
import sys
from pathlib import Path

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                  LEARNER_ID="test")
os.environ.pop("AWS_PROFILE", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "csv-count"))
sys.path.insert(0, str(ROOT.parent / "day2" / "scripts"))

import app  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from botocore.response import StreamingBody  # noqa: E402
from botocore.stub import Stubber  # noqa: E402
from check_logs import check_lines  # noqa: E402

BUCKET = "accelya-m1-test-csv-count-000000000000"
KEY = "raw/ticket_issue_AI_2026-11-23.csv"
DATA = (ROOT / "data" / "ticket_issue_AI_2026-11-23.csv").read_bytes()
EVENT = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": KEY, "size": len(DATA)}}}]}
PARAMS = {"Bucket": BUCKET, "Key": KEY}


class Ctx:
    aws_request_id = "test-req-0001"
    memory_limit_in_mb = "256"


def run(get_ok=True):
    """Invoke the handler with stubbed S3. Returns (raw log lines, raised exception or None)."""
    st = Stubber(app.s3)
    st.add_response("head_object", {"ContentLength": len(DATA), "Metadata": {"correlation-id": "t-001"}}, PARAMS)
    if get_ok:
        st.add_response("get_object", {"Body": StreamingBody(io.BytesIO(DATA), len(DATA))}, PARAMS)
    else:
        st.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403,
                            expected_params=PARAMS)
    buf, real, raised = io.StringIO(), sys.stdout, None
    sys.stdout = buf
    try:
        with st:
            app.handler(EVENT, Ctx())
    except ClientError as e:
        raised = e
    finally:
        sys.stdout = real
    return buf.getvalue().splitlines(), raised


def main():
    raw, _ = run()
    lines = [json.loads(line) for line in raw if line.strip()]
    received = next(l for l in lines if l["message"] == "file.received")
    err_raw, err_raised = run(get_ok=False)
    err = [json.loads(line) for line in err_raw if line.strip()]
    checks = [
        ("record_count == 25", received.get("record_count") == 25),
        ("correlation_id on every line", all(l.get("correlation_id") == "t-001" for l in lines)),
        ("no ticket data in logs", not any("098-" in line for line in raw)),
        ("airline_code == 'AI'", received.get("airline_code") == "AI"),
        ("every line conforms to docs/logging-contract.json", not check_lines(raw + err_raw)[1]),
        ("read failure: ERROR file.read.failed + re-raise",
         err_raised is not None and any(l["level"] == "ERROR" and l["message"] == "file.read.failed"
                                        and l.get("error_code") == "AccessDenied" for l in err)),
    ]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    failed = [n for n, ok in checks if not ok]
    print("\nALL PASS" if not failed else f"\n{len(failed)} failing")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
