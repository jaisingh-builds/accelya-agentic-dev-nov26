"""Functional test: the real 42-record Day 3 file through the service with YOUR rules
(day4/src/processor/rules/validation_rules.json, copied from day3/rules/ in Lab 2).

It proves two things from the outputs alone:
  1. every record lands where day3/data/expected_routing.json says it should (42/42);
  2. the Lambda writes exactly the same zone files as Day 3's local classifier (same rules -> same bytes).
"""
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import _paths
from fakes import Capture, FakeS3

from config import load_config
from log import build_logger
from service import load_ruleset, process_object

sys.path.insert(0, str(_paths.ROOT / "day3" / "scripts"))
B = "accelya-m4-test-111111111111"
NAME = _paths.FILE42.name
CID = "test-d4-042"
CAP = Capture()
LOGGER = build_logger("accelya-m4-functional-processor", "test", handler=CAP)


def routing_from_outputs(s3):
    """Rebuild where each source line went, using only what the processor wrote to S3."""
    got = {}
    for key in s3.keys(B, "validated/"):
        for row in csv.DictReader(io.StringIO(s3.text(B, key))):
            got[int(row["source_line"])] = "validated"
    for key in s3.keys(B, "quarantine/"):
        if key.endswith(".sidecar.json"):
            side = json.loads(s3.text(B, key))
            for r in side["records"]:
                got[r["source_line"]] = f"quarantine/{side['reason']}"
    return got


@unittest.skipUnless(_paths.PKG_RULES.exists(), "copy your Day 3 rules first: "
                     "cp day3/rules/validation_rules.json day4/src/processor/rules/")
class FunctionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config({"LEARNER_ID": "test"})
        cls.s3 = FakeS3()
        cls.s3.add(B, f"raw/{NAME}", _paths.FILE42.read_bytes())
        cls.res = process_object(cls.s3, cls.cfg, load_ruleset(cls.cfg), B, f"raw/{NAME}", CID, LOGGER)

    def test_42_of_42_routed_as_expected(self):
        expected = json.loads((_paths.DATA / "expected_routing.json").read_text())["files"][NAME]["rows"]
        got = routing_from_outputs(self.s3)
        wrong = []
        for e in expected:
            want = "validated" if e["route"] in ("validated", "flagged") else (
                "duplicate" if e["route"] == "duplicate" else f"quarantine/{e['reason']}")
            have = got.get(e["line"], "duplicate")         # duplicates are counted, not written
            if want != have:
                wrong.append(f"line {e['line']}: expected {want}, got {have}")
        self.assertEqual(wrong, [], f"{42 - len(wrong)}/42 routed as expected")

    def test_manifest_balances_21_1_19_1(self):
        m = self.res["manifest"]
        self.assertEqual((m["rows_in"], m["validated"], m["flagged"], m["duplicates"], sum(m["quarantined"].values())),
                         (42, 21, 1, 1, 19))
        self.assertTrue(m["balanced"])

    def test_zones_are_byte_identical_to_day3(self):
        import classify as day3
        with tempfile.TemporaryDirectory() as tmp:
            fr, rules = day3.load_rules(_paths.PKG_RULES)[1:]
            file_res, results, _ = day3.classify(_paths.FILE42, fr, rules, day3.load_ref())
            day3.write_zones(_paths.FILE42, tmp, file_res, results, _paths.PKG_RULES, CID)
            for p in Path(tmp).rglob("*"):
                if p.is_file() and "manifests" not in p.parts:
                    key = str(p.relative_to(tmp))
                    self.assertEqual(self.s3.objects[(B, key)]["Body"], p.read_bytes(), key)

    def test_resend_file_is_quarantined_as_schema(self):
        s3 = FakeS3()
        s3.add(B, f"raw/{_paths.RESEND.name}", _paths.RESEND.read_bytes())
        res = process_object(s3, self.cfg, load_ruleset(self.cfg), B, f"raw/{_paths.RESEND.name}", CID, LOGGER)
        self.assertEqual((res["status"], list(res["manifest"]["quarantined"])), ("quarantined", ["schema"]))


if __name__ == "__main__":
    unittest.main()
