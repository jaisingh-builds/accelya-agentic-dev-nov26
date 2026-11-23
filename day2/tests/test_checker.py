#!/usr/bin/env python3
"""Self-test for the logging-contract checker. Run: python3 day2/tests/test_checker.py
Proves the checker rejects payloads hidden inside ALLOWED keys, not just unknown keys."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_logs import check_line  # noqa: E402

GOOD = {"ts": "2026-11-24T05:12:09Z", "level": "INFO", "service": "accelya-m2-cancellation",
        "correlation_id": "c-91a002", "boundary": "sqs->lambda", "message": "cancellation.decided",
        "event_id": "evt-1002", "fare_class": "K", "refund_rule_id": "R-SAVER", "decision": "APPROVE"}
BODY = '{"pnr": "LM4K8D", "passenger_name": "Rohan Mehta", "fare_amount": "4120.00"}'
CASES = [
    ("conformant record", GOOD, True),
    ("payload in 'message'", {**GOOD, "message": "Processing " + BODY}, False),
    ("payload in 'decision'", {**GOOD, "decision": BODY}, False),
    ("payload in 'event_id'", {**GOOD, "event_id": BODY}, False),
    ("payload in 'key'", {**GOOD, "key": "raw/" + BODY}, False),
    ("name in 'pnr'", {**GOOD, "pnr": "Rohan Mehta"}, False),
    ("unknown key 'details'", {**GOOD, "details": "x"}, False),
    ("object value", {**GOOD, "event_id": {"a": 1}}, False),
    ("ERROR without error_type", {**GOOD, "level": "ERROR"}, False),
    ("bool as string", {**GOOD, "cold_start": "true"}, False),
]
fails = 0
for name, obj, should_pass in CASES:
    v = check_line(obj, json.dumps(obj))
    ok = (not v) == should_pass
    fails += not ok
    print(("  PASS  " if ok else "  FAIL  ") + f"{name:28s} -> {'accepted' if not v else 'rejected: ' + v[0]}")
print("\nchecker self-test:", "OK" if not fails else f"{fails} unexpected result(s)")
sys.exit(1 if fails else 0)
