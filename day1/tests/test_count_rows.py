"""Option A count_rows Lambda - fixture test, no AWS account needed.

Run from the repo root:   python3 day1/tests/test_count_rows.py
"""
import io
import json
import os
import sys
from pathlib import Path

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                  LEARNER_ID="test", TABLE_NAME="counts")
os.environ.pop("AWS_PROFILE", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "count_rows"))

import app  # noqa: E402
from botocore.response import StreamingBody  # noqa: E402
from botocore.stub import ANY, Stubber  # noqa: E402


class Ctx:
    aws_request_id = "test-req-0001"


def run(name, data):
    key = f"raw/{name}"
    s3, ddb = Stubber(app.s3), Stubber(app.ddb)
    s3.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))}, {"Bucket": "b", "Key": key})
    ddb.add_response("put_item", {}, {"TableName": "counts", "Item": ANY, "ConditionExpression": ANY,
                                      "ExpressionAttributeValues": ANY})
    s3.activate(); ddb.activate()
    event = {"Records": [{"body": json.dumps({"id": "abc123", "time": "2026-11-23T01:00:00Z", "detail": {
        "bucket": {"name": "b"}, "object": {"key": key, "size": len(data)}}})}]}
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        app.handler(event, Ctx())
    finally:
        sys.stdout = real
        s3.deactivate(); ddb.deactivate()
    return [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]


def main():
    expect = {"AI": 25, "BA": 18, "EK": 12}
    checks = []
    for code, n in expect.items():
        name = f"ticket_issue_{code}_2026-11-23.csv"
        lines = run(name, (ROOT / "data" / name).read_bytes())
        done = next(l for l in lines if l["message"] == "count_rows.done")
        checks += [
            (f"{code}: record_count == {n}", done["record_count"] == n),
            (f"{code}: status OK, airline_code {code}", done["status"] == "OK" and done["airline_code"] == code),
            (f"{code}: correlation_id on every line", all(l.get("correlation_id") for l in lines)),
        ]
    # bad file: wrong header -> FAILED, 0 rows, no exception
    bad = run("ticket_issue_ZZ_2026-11-23.csv", b"a,b,c\n1,2,3\n")
    done = next(l for l in bad if l["message"] == "count_rows.done")
    checks.append(("bad header -> FAILED with 0 rows", done["status"] == "FAILED" and done["record_count"] == 0))
    # no ticket data / fares in logs (fixture ticket numbers start 098-)
    all_logs = json.dumps(lines + bad)
    checks.append(("no ticket data in logs", "098-" not in all_logs and "fare_amount" not in all_logs))
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    failed = [n for n, ok in checks if not ok]
    print("\nALL PASS" if not failed else f"\n{len(failed)} failing")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
