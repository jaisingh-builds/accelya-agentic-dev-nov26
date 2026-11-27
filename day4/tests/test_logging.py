"""Logging tests through the real handler: contract-conformant lines, correlation_id everywhere, no payloads."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths
from test_service import HEADER, SMALL_RULES

_TMP = tempfile.mkdtemp()
Path(_TMP, "rules.json").write_text(json.dumps(SMALL_RULES))
os.environ["RULES_PATH"] = str(Path(_TMP, "rules.json"))     # before handler is imported
os.environ["LEARNER_ID"] = "test"

import handler as fn                                          # noqa: E402
from fakes import FakeContext, FakeS3, capture, client_error  # noqa: E402

CAP = capture(fn.logger)
B = "accelya-m4-test-111111111111"
CHECKER = _paths.ROOT / "day2" / "scripts" / "check_logs.py"


def s3_event(key):
    return {"Records": [{"s3": {"bucket": {"name": B}, "object": {"key": key}}}]}


class LoggingTests(unittest.TestCase):
    def setUp(self):
        CAP.lines.clear()
        fn.s3 = self.s3 = FakeS3()
        fn._ruleset = None

    def invoke(self, key, body, metadata=None):
        self.s3.add(B, key, body, metadata)
        return fn.handler(s3_event(key), FakeContext())

    def lines(self):
        return [json.loads(x) for x in CAP.lines]

    def test_every_line_has_correlation_id_and_learner_id_across_warm_invocations(self):
        self.invoke("raw/ticket_issue_AI_2026-11-25.csv", _paths.FILE42.read_bytes(), {"correlation-id": "test-d4-001"})
        first = self.lines()
        self.assertGreaterEqual(len(first), 3)
        self.assertEqual({x.get("correlation_id") for x in first}, {"test-d4-001"})
        # second (warm) invocation: clear_state=True must not drop learner_id or leak the first id
        self.invoke("raw/ticket_issue_AI_2026-11-26.csv", f"{HEADER}\n".encode(), {"correlation-id": "test-d4-002"})
        second = self.lines()[len(first):]
        self.assertEqual({x.get("correlation_id") for x in second}, {"test-d4-002"})
        self.assertEqual({x.get("learner_id") for x in first + second}, {"test"})

    def test_lines_pass_the_logging_contract_checker(self):
        self.invoke("raw/ticket_issue_AI_2026-11-25.csv", _paths.FILE42.read_bytes(), {"correlation-id": "test-d4-001"})
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("\n".join(CAP.lines) + "\n")
        r = subprocess.run([sys.executable, str(CHECKER), f.name], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-600:])

    def test_no_payload_value_is_logged(self):
        self.invoke("raw/ticket_issue_AI_2026-11-25.csv", _paths.FILE42.read_bytes(), {"correlation-id": "test-d4-001"})
        text = "\n".join(CAP.lines)
        with _paths.FILE42.open(newline="") as f:
            rows = list(csv.DictReader(f))
        leaked = {v for r in rows for k in ("pnr", "ticket_number", "fare_amount", "agency_iata")
                  for v in [(r.get(k) or "").strip()] if len(v) >= 4 and v in text}
        self.assertEqual(leaked, set(), "payload values found in logs")

    def test_missing_metadata_mints_an_id_and_warns(self):
        self.invoke("raw/ticket_issue_AI_2026-11-25.csv", f"{HEADER}\n".encode())
        lines = self.lines()
        cids = {x["correlation_id"] for x in lines}
        self.assertEqual(len(cids), 1)
        self.assertTrue(cids.pop().startswith("m4-"))
        self.assertIn(("WARNING", "metadata.missing"), [(x["level"], x["message"]) for x in lines])

    def test_error_line_has_error_type_and_no_traceback(self):
        self.s3.add(B, "raw/ticket_issue_AI_2026-11-25.csv", b"x", {"correlation-id": "test-d4-009"})
        self.s3.fail_next["get_object"] = client_error("AccessDenied", 403)
        with self.assertRaises(Exception):
            fn.handler(s3_event("raw/ticket_issue_AI_2026-11-25.csv"), FakeContext())
        err = [x for x in self.lines() if x["level"] == "ERROR"]
        self.assertEqual(len(err), 1)
        self.assertEqual(err[0]["error_type"], "ClientError")
        self.assertNotIn("Traceback", CAP.lines[-1])

    def test_retryable_error_is_raised_for_lambda_to_retry(self):
        self.s3.add(B, "raw/ticket_issue_AI_2026-11-25.csv", f"{HEADER}\n".encode(), {"correlation-id": "test-d4-010"})
        self.s3.fail_next["put_object"] = client_error("SlowDown", 503, "PutObject")
        with self.assertRaises(fn.RecoverableError):
            fn.handler(s3_event("raw/ticket_issue_AI_2026-11-25.csv"), FakeContext())
        self.assertEqual(self.lines()[-1]["message"], "file.retry")


if __name__ == "__main__":
    unittest.main()
