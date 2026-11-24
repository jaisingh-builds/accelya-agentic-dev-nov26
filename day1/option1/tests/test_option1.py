"""Option 1 fixture test - stubbed AWS, no account needed.

Run from the repo root:   python3 day1/option1/tests/test_option1.py

Checks:
  1. parser counts the AI fixture: 25 tickets for airline_code AI
  2. a re-uploaded file (DynamoDB condition fails) is skipped, not counted
  3. aggregator sums two files into one SNS message per window and logs each file's correlation_id
  4. every log line passes docs/LOGGING_CONTRACT.md (day2/scripts/check_logs.py)
  5. no ticket data (ticket numbers) leaks into the logs
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                  LEARNER_ID="test", TABLE_NAME="t", TOPIC_ARN="arn:aws:sns:us-east-1:000000000000:t")
os.environ.pop("AWS_PROFILE", None)
OPT1 = Path(__file__).resolve().parents[1]
REPO = OPT1.parents[1]

from botocore.response import StreamingBody  # noqa: E402
from botocore.stub import ANY, Stubber  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, OPT1 / "src" / name / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


parser, aggregator = load("parser"), load("aggregator")
KEY = "raw/ticket_issue_AI_2026-11-23.csv"
DATA = (REPO / "day1" / "data" / "ticket_issue_AI_2026-11-23.csv").read_bytes()
BUCKET = "accelya-m1-test-opt1"
EVENT = {"Records": [{"s3": {"bucket": {"name": BUCKET},"object": {"key": KEY, "eTag": "abc123"}}}]}


def capture(fn, *args):
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        result = fn(*args)
    finally:
        sys.stdout = real
    return result, [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]


def parser_run(duplicate):
    st = Stubber(parser.s3)
    st.add_response("get_object", {"Body": StreamingBody(io.BytesIO(DATA), len(DATA)), "ContentLength": len(DATA),
                                   "Metadata": {"correlation-id": "t-001"}}, {"Bucket": BUCKET, "Key": KEY})
    dt = Stubber(parser.ddb)
    if duplicate:
        dt.add_client_error("transact_write_items", service_error_code="TransactionCanceledException",
                            modeled_fields={"CancellationReasons": [{"Code": "ConditionalCheckFailed"},
                                                                    {"Code": "None"}]},
                            expected_params={"TransactItems": ANY})
    else:
        dt.add_response("transact_write_items", {}, {"TransactItems": ANY})
    with st, dt:
        return capture(parser.handler, EVENT, None)


def aggregator_run():
    items = [{"pk": {"S": "WINDOW#2026-11-24T10:14"}, "sk": {"S": "AI#e1"}, "airline_code": {"S": "AI"},
              "ticket_count": {"N": "25"}, "correlation_id": {"S": "t-001"}},
             {"pk": {"S": "WINDOW#2026-11-24T10:14"}, "sk": {"S": "BA#e2"}, "airline_code": {"S": "BA"},
              "ticket_count": {"N": "30"}, "correlation_id": {"S": "t-002"}}]
    dt, nt = Stubber(aggregator.ddb), Stubber(aggregator.sns)
    dt.add_response("query", {"Items": items}, {"TableName": "t", "KeyConditionExpression": "pk = :pk",
                                                 "ExpressionAttributeValues": {":pk": {"S": "WINDOW#2026-11-24T10:14"}}})
    nt.add_response("publish", {"MessageId": "11111111-2222-3333-4444-555555555555"},
                    {"TopicArn": ANY, "Subject": ANY, "Message": ANY, "MessageAttributes": ANY})
    with dt, nt:
        return capture(aggregator.handler, {"time": "2026-11-24T10:15:00Z"}, None)


def contract_ok(lines):
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write("\n".join(json.dumps(l) for l in lines) + "\n")
    try:
        return subprocess.run([sys.executable, str(REPO / "day2" / "scripts" / "check_logs.py"), f.name],
                              capture_output=True).returncode == 0
    finally:
        os.unlink(f.name)


def main():
    _, p_lines = parser_run(duplicate=False)
    _, d_lines = parser_run(duplicate=True)
    result, a_lines = aggregator_run()
    counted = [l for l in p_lines if l["message"] == "file.counted"]
    included = {l["correlation_id"] for l in a_lines if l["message"] == "window.file_included"}
    everything = p_lines + d_lines + a_lines
    checks = [
        ("parser counts AI = 25", [(l["airline_code"], l["record_count"]) for l in counted] == [("AI", 25)]),
        ("re-upload skipped, not counted", any(l["message"] == "file.duplicate_skipped" for l in d_lines)
         and not any(l["message"] == "file.counted" for l in d_lines)),
        ("aggregator AI 25 + BA 30, file cids logged", result["ticket_counts"] == {"AI": 25, "BA": 30}
         and included == {"t-001", "t-002"}),
        ("logs conform to LOGGING_CONTRACT", contract_ok(everything)),
        ("no ticket data in logs", not any("098-" in json.dumps(l) for l in everything)),
    ]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    failed = [n for n, ok in checks if not ok]
    print("\nALL PASS" if not failed else f"\n{len(failed)} failing")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
