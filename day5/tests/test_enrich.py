"""Tests for the enrichment job (day5/job/enrich.py). Local only: no AWS, no Docker."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "day5" / "job"
DATA = ROOT / "day5" / "data" / "validated"
sys.path.insert(0, str(JOB))
import enrich  # noqa: E402


def run_job(env_extra, local_in=DATA, out=None):
    out = out or tempfile.mkdtemp()
    env = {k: v for k, v in os.environ.items() if k not in ("LEARNER_ID", "CORRELATION_ID", "BUCKET")}
    env.update(env_extra)
    p = subprocess.run([sys.executable, str(JOB / "enrich.py"), "--local-in", str(local_in), "--local-out", out],
                       capture_output=True, text=True, env=env)
    return p, Path(out)


class TestEnrich(unittest.TestCase):
    def test_settlement_date_weekly_cycle(self):
        cases = {date(2026, 11, 19): date(2026, 11, 27), date(2026, 11, 22): date(2026, 11, 27),
                 date(2026, 11, 23): date(2026, 12, 4), date(2026, 9, 30): date(2026, 10, 9)}
        for issue, settle in cases.items():
            self.assertEqual(enrich.settlement_date(issue), settle, issue)

    def test_enrich_row_splits_od_and_keeps_money_decimal(self):
        row = {"pnr": "JHELYR", "ticket_number": "098-2015079145", "airline_code": "AI", "fare_class": "B",
               "od_pair": "DEL-FRA", "issue_date": "2026-11-25", "fare_amount": "6440.10", "currency": "INR",
               "agency_iata": "24924614", "flags": "", "source_line": "2"}
        r = enrich.enrich_row(row, "validated/business_date=2026-11-25/ticket_issue_AI_2026-11-25.csv")
        self.assertEqual((r["origin"], r["destination"], r["airline_name"]), ("DEL", "FRA", "Air India"))
        self.assertIsInstance(r["fare_amount"], Decimal)
        self.assertEqual(r["fare_amount"], Decimal("6440.10"))
        self.assertEqual(r["business_date"], date(2026, 11, 25))
        self.assertEqual(r["settlement_date"], date(2026, 12, 4))

    def test_partition_key_is_hive_style(self):
        self.assertEqual(enrich.partition_key("curated/ticket_issue/", date(2026, 11, 27), "BA"),
                         "curated/ticket_issue/settlement_date=2026-11-27/airline_code=BA/part-00000.parquet")

    def test_run_is_balanced_and_partitioned(self):
        p, out = run_job({"LEARNER_ID": "test", "CORRELATION_ID": "test-d5-001"})
        self.assertEqual(p.returncode, 0, p.stdout)
        m = json.loads((out / "manifests/curated/ticket_issue.manifest.json").read_text())
        self.assertEqual((m["rows_in"], m["rows_out"], m["balanced"]), (606, 606, True))
        self.assertEqual(len(m["partitions"]), 7)
        self.assertEqual(m["by_airline"], {"AI": 293, "BA": 130, "EK": 183})

    def test_parquet_types_and_no_partition_columns_inside(self):
        _, out = run_job({"LEARNER_ID": "test", "CORRELATION_ID": "test-d5-001"})
        f = out / "curated/ticket_issue/settlement_date=2026-11-27/airline_code=AI/part-00000.parquet"
        schema = pq.read_schema(f)
        self.assertEqual(str(schema.field("fare_amount").type), "decimal128(12, 2)")
        self.assertEqual(str(schema.field("issue_date").type), "date32[day]")
        self.assertNotIn("settlement_date", schema.names)
        self.assertNotIn("airline_code", schema.names)
        self.assertEqual(pq.read_table(f).num_rows, 179)

    def test_same_input_same_bytes(self):
        _, a = run_job({"LEARNER_ID": "test", "CORRELATION_ID": "test-d5-001"})
        _, b = run_job({"LEARNER_ID": "test", "CORRELATION_ID": "test-d5-001"})
        files = sorted(p.relative_to(a) for p in a.rglob("*.parquet"))
        self.assertEqual(len(files), 7)
        for f in files:
            self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)

    def test_logs_follow_the_contract(self):
        p, _ = run_job({"LEARNER_ID": "test", "CORRELATION_ID": "test-d5-001"})
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(p.stdout)
        chk = subprocess.run([sys.executable, str(ROOT / "day2/scripts/check_logs.py"), fh.name], capture_output=True, text=True)
        self.assertEqual(chk.returncode, 0, chk.stdout)
        lines = [json.loads(x) for x in p.stdout.splitlines()]
        self.assertTrue(all(x["correlation_id"] == "test-d5-001" and x["learner_id"] == "test" for x in lines))
        self.assertNotIn("6440.10", p.stdout)   # no payload values in logs

    def test_exit_2_when_config_missing(self):
        p, _ = run_job({"CORRELATION_ID": "test-d5-002"})
        self.assertEqual(p.returncode, 2)
        self.assertIn('"error_code":"LEARNER_ID"', p.stdout)

    def test_exit_3_when_no_input(self):
        p, _ = run_job({"LEARNER_ID": "test", "CORRELATION_ID": "test-d5-003"}, local_in=tempfile.mkdtemp())
        self.assertEqual(p.returncode, 3)
        self.assertIn('"message":"input.empty"', p.stdout)


if __name__ == "__main__":
    unittest.main()
