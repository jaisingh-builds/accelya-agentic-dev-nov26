"""Unit tests for service.py: routing, error classification, idempotency. Small inline rules, fake S3."""
import json
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa: F401  (sets sys.path)
from botocore.exceptions import ClientError
from fakes import Capture, FakeS3, client_error

from config import load_config
from log import build_logger
from service import RecoverableError, load_ruleset, process_object

HEADER = "pnr,ticket_number,airline_code,fare_class,od_pair,issue_date,fare_amount,currency,agency_iata"
GOOD = "JHELYR,098-2015079145,AI,B,DEL-FRA,2026-11-25,6440.10,INR,24924614"
BAD_PNR = "jh3,098-2015079146,AI,B,DEL-FRA,2026-11-25,6440.10,INR,24924614"
SMALL_RULES = {
    "file_rules": [{"id": "F-01", "type": "header", "reason": "schema", "on_fail": "quarantine", "columns": HEADER.split(",")}],
    "rules": [
        {"id": "R-02", "type": "regex", "field": "pnr", "pattern": "^[A-Z0-9]{6}$", "reason": "format", "on_fail": "quarantine"},
        {"id": "R-19", "type": "unique", "field": "ticket_number", "reason": "duplicate", "on_fail": "skip"},
    ],
}
B = "accelya-m4-test-111111111111"
NAME = "ticket_issue_AI_2026-11-25.csv"


CAP = Capture()
LOGGER = build_logger("accelya-m4-unit-processor", "test", handler=CAP)   # one per process: Powertools reuses by name


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rules = Path(self.tmp.name) / "rules.json"
        rules.write_text(json.dumps(SMALL_RULES))
        self.cfg = load_config({"LEARNER_ID": "test", "RULES_PATH": str(rules), "REF_DIR": str(_paths.SRC / "rules" / "ref")})
        self.rs = load_ruleset(self.cfg)
        CAP.lines.clear()
        self.cap, self.logger = CAP, LOGGER
        self.s3 = FakeS3()

    def tearDown(self):
        self.tmp.cleanup()

    def run_file(self, body, key=f"raw/{NAME}"):
        self.s3.add(B, key, body.encode() if isinstance(body, str) else body)
        return process_object(self.s3, self.cfg, self.rs, B, key, "t-d4-001", self.logger)

    # ---- happy path
    def test_happy_path_routes_valid_to_validated(self):
        res = self.run_file(f"{HEADER}\n{GOOD}\n")
        self.assertEqual(res["status"], "processed")
        self.assertEqual(self.s3.keys(B, "validated/"), [f"validated/business_date=2026-11-25/{NAME}"])
        m = res["manifest"]
        self.assertEqual((m["rows_in"], m["validated"], m["balanced"]), (1, 1, True))
        self.assertIn(f"manifests/business_date=2026-11-25/{NAME}.manifest.json", self.s3.keys(B))

    def test_invalid_record_goes_to_quarantine_with_sidecar(self):
        res = self.run_file(f"{HEADER}\n{GOOD}\n{BAD_PNR}\n")
        q = f"quarantine/format/business_date=2026-11-25/{NAME}"
        self.assertIn(q, self.s3.keys(B))
        side = json.loads(self.s3.text(B, q + ".sidecar.json"))
        self.assertEqual(side["records"], [{"source_line": 3, "rule_id": "R-02"}])
        self.assertEqual(res["manifest"]["quarantined"], {"format": 1})

    def test_duplicate_ticket_is_skipped_and_counted(self):
        res = self.run_file(f"{HEADER}\n{GOOD}\n{GOOD}\n")
        self.assertEqual((res["manifest"]["validated"], res["manifest"]["duplicates"]), (1, 1))
        self.assertTrue(res["manifest"]["balanced"])

    # ---- schema failure
    def test_schema_failure_quarantines_whole_file(self):
        res = self.run_file(HEADER.replace("currency", "fare_currency") + f"\n{GOOD}\n")
        self.assertEqual(res["status"], "quarantined")
        self.assertEqual(self.s3.keys(B, "validated/"), [])
        self.assertIn(f"quarantine/schema/business_date=2026-11-25/{NAME}", self.s3.keys(B))
        self.assertEqual(res["manifest"]["quarantined"], {"schema": 1})

    # ---- poison / non-recoverable data problems: never raise
    def test_unreadable_bytes_are_quarantined_not_retried(self):
        res = self.run_file(b"\xff\xfe\x00not,a,csv")
        self.assertEqual(res["status"], "quarantined")
        self.assertIn(f"quarantine/unreadable/business_date=2026-11-25/{NAME}", self.s3.keys(B))

    def test_file_name_without_business_date_is_quarantined(self):
        res = self.run_file(f"{HEADER}\n{GOOD}\n", key="raw/tickets.csv")
        self.assertEqual(res["status"], "quarantined")
        self.assertIn("quarantine/filename/business_date=unknown/tickets.csv", self.s3.keys(B))

    def test_missing_object_is_logged_not_raised(self):
        res = process_object(self.s3, self.cfg, self.rs, B, "raw/gone_2026-11-25.csv", "t-d4-002", self.logger)
        self.assertEqual(res["status"], "missing")

    def test_key_outside_raw_is_ignored(self):
        self.s3.add(B, f"validated/{NAME}", b"x")
        res = process_object(self.s3, self.cfg, self.rs, B, f"validated/{NAME}", "t-d4-003", self.logger)
        self.assertEqual(res["status"], "ignored")
        self.assertEqual(self.s3.puts, [])

    # ---- recoverable vs unknown AWS errors: raise so Lambda retries, then the DLQ holds it
    def test_throttling_raises_recoverable(self):
        self.s3.add(B, f"raw/{NAME}", f"{HEADER}\n{GOOD}\n".encode())
        self.s3.fail_next["put_object"] = client_error("SlowDown", 503, "PutObject")
        with self.assertRaises(RecoverableError):
            process_object(self.s3, self.cfg, self.rs, B, f"raw/{NAME}", "t-d4-004", self.logger)

    def test_access_denied_is_not_swallowed(self):
        self.s3.add(B, f"raw/{NAME}", f"{HEADER}\n{GOOD}\n".encode())
        self.s3.fail_next["get_object"] = client_error("AccessDenied", 403)
        with self.assertRaises(ClientError) as ctx:
            process_object(self.s3, self.cfg, self.rs, B, f"raw/{NAME}", "t-d4-005", self.logger)
        self.assertNotIsInstance(ctx.exception, RecoverableError)
        self.assertEqual(ctx.exception.response["Error"]["Code"], "AccessDenied")

    # ---- idempotency
    def test_same_file_twice_is_a_no_op(self):
        self.run_file(f"{HEADER}\n{GOOD}\n")
        writes = len(self.s3.puts)
        res = process_object(self.s3, self.cfg, self.rs, B, f"raw/{NAME}", "t-d4-006", self.logger)
        self.assertEqual(res["status"], "already_processed")
        self.assertEqual(len(self.s3.puts), writes)

    def test_changed_rules_reprocess_the_same_file(self):
        self.run_file(f"{HEADER}\n{GOOD}\n")
        rules = dict(SMALL_RULES, version="2")
        Path(self.cfg.rules_path).write_text(json.dumps(rules))
        res = process_object(self.s3, self.cfg, load_ruleset(self.cfg), B, f"raw/{NAME}", "t-d4-007", self.logger)
        self.assertEqual(res["status"], "processed")

    def test_outputs_carry_lineage_metadata(self):
        self.run_file(f"{HEADER}\n{GOOD}\n")
        meta = self.s3.objects[(B, f"validated/business_date=2026-11-25/{NAME}")]["Metadata"]
        self.assertEqual(meta["correlation-id"], "t-d4-001")
        self.assertEqual(len(meta["source-sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
