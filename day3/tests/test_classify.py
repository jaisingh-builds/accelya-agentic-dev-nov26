#!/usr/bin/env python3
"""Self-test for day3/scripts/classify.py: every rule type, phase order, file-date rules, no values printed.
Run from the repo root:  python3 day3/tests/test_classify.py"""
import csv
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "day3" / "scripts"))
import classify as c  # noqa: E402

REF = c.load_ref()
CTX = {"ref": REF, "file_date": date(2026, 11, 25)}
GOOD = {"pnr": "JHELYR", "ticket_number": "098-2015079145", "airline_code": "AI", "fare_class": "B",
        "od_pair": "DEL-FRA", "issue_date": "2026-11-25", "fare_amount": "6440.10", "currency": "INR",
        "agency_iata": "24924614"}

CASES = [  # (rule, field override, expected pass)
    ({"type": "required", "fields": ["pnr"]}, {"pnr": " "}, False),
    ({"type": "regex", "field": "pnr", "pattern": "^[A-Z0-9]{6}$"}, {"pnr": "ab12cd"}, False),
    ({"type": "regex", "field": "pnr", "pattern": "^[A-Z0-9]{6}$"}, {}, True),
    ({"type": "in_ref", "field": "airline_code", "ref": "airlines"}, {"airline_code": "XX"}, False),
    ({"type": "in_ref", "field": "fare_class", "ref": "fare_classes"}, {"fare_class": "R"}, False),
    ({"type": "od_airports_in_ref", "field": "od_pair"}, {"od_pair": "BOM-XYZ"}, False),
    ({"type": "od_airports_in_ref", "field": "od_pair"}, {"od_pair": "BOMDXB"}, False),
    ({"type": "od_distinct", "field": "od_pair"}, {"od_pair": "BOM-BOM"}, False),
    ({"type": "ticket_prefix", "field": "ticket_number", "airline_field": "airline_code"}, {"ticket_number": "125-7571677966"}, False),
    ({"type": "ticket_prefix", "field": "ticket_number", "airline_field": "airline_code"}, {}, True),
    ({"type": "decimal_places", "field": "fare_amount", "max_places": 2}, {"fare_amount": "4520.555"}, False),
    ({"type": "decimal_places", "field": "fare_amount", "max_places": 2}, {"fare_amount": "NaN"}, False),
    ({"type": "min_exclusive", "field": "fare_amount", "value": 0}, {"fare_amount": "-1200.00"}, False),
    ({"type": "min_exclusive", "field": "fare_amount", "value": 0}, {"fare_amount": "0.00"}, False),
    ({"type": "date_format", "field": "issue_date"}, {"issue_date": "25/11/2026"}, False),
    ({"type": "date_not_after_file_date", "field": "issue_date"}, {"issue_date": "2026-11-26"}, False),
    ({"type": "date_not_after_file_date", "field": "issue_date"}, {}, True),
    ({"type": "date_within_days", "field": "issue_date", "days": 30}, {"issue_date": "2026-10-26"}, True),
    ({"type": "date_within_days", "field": "issue_date", "days": 30}, {"issue_date": "2026-10-25"}, False),
]


def run_file(rows, rules, header=None, name="ticket_issue_AI_2026-11-25.csv"):
    tmp = Path(tempfile.mkdtemp()) / name
    header = header or list(GOOD)
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, header)
        w.writeheader()
        w.writerows(rows)
    rules_file = tmp.with_suffix(".rules.json")
    rules_file.write_text(json.dumps(rules))
    data, fr, rr = c.load_rules(rules_file)
    return c.classify(tmp, fr, rr, REF)


def main():
    fails = []
    for rule, override, want in CASES:
        got = c.check({"id": "T", **rule}, {**GOOD, **override}, CTX)
        if got != want:
            fails.append(f"{rule['type']} with {override}: expected {'pass' if want else 'fail'}")

    # phase order: a format rule listed AFTER a referential rule still decides the reason
    rules = {"rules": [
        {"id": "REF", "type": "in_ref", "field": "airline_code", "ref": "airlines", "reason": "referential", "on_fail": "quarantine"},
        {"id": "FMT", "type": "regex", "field": "airline_code", "pattern": "^[A-Z0-9]{2}$", "reason": "format", "on_fail": "quarantine"}]}
    _f, res, _n = run_file([{**GOOD, "airline_code": "AIR"}], rules)
    if res[0]["rule"] != "FMT":
        fails.append(f"phase order: expected FMT to decide, got {res[0]['rule']}")

    # duplicates: a quarantined record doesn't claim the ticket number
    rules = {"rules": [
        {"id": "P", "type": "regex", "field": "pnr", "pattern": "^[A-Z0-9]{6}$", "reason": "format", "on_fail": "quarantine"},
        {"id": "U", "type": "unique", "field": "ticket_number", "reason": "duplicate", "on_fail": "skip"}]}
    _f, res, _n = run_file([{**GOOD, "pnr": "bad"}, GOOD, GOOD], rules)
    if [r["route"] for r in res] != ["quarantine", "validated", "duplicate"]:
        fails.append(f"duplicate handling: got {[r['route'] for r in res]}")

    # header drift quarantines the whole file
    rules = {"file_rules": [{"id": "F", "type": "header", "columns": list(GOOD), "reason": "schema", "on_fail": "quarantine"}], "rules": []}
    hdr = [h if h != "currency" else "fare_currency" for h in GOOD]
    row = {("fare_currency" if k == "currency" else k): v for k, v in GOOD.items()}
    fres, _r, _n = run_file([row], rules, header=hdr)
    if fres["route"] != "quarantine":
        fails.append("header drift: file was not quarantined")

    # output never prints field values
    out = subprocess.run([sys.executable, str(ROOT / "day3/scripts/classify.py"),
                          str(ROOT / "day3/data/ticket_issue_AI_2026-11-25.csv"),
                          "--rules", str(ROOT / "day3/rules/validation_rules.template.json")],
                         capture_output=True, text=True).stdout
    values = set()
    with (ROOT / "day3/data/ticket_issue_AI_2026-11-25.csv").open() as f:
        for r in csv.DictReader(f):
            values.update(v for k, v in r.items() if k in ("pnr", "ticket_number", "fare_amount") and len(v) >= 6)
    leaked = [v for v in values if v in out]
    if leaked:
        fails.append(f"classify.py printed {len(leaked)} field value(s)")

    total = len(CASES) + 4
    if fails:
        print("classify self-test: FAIL\n  " + "\n  ".join(fails))
        sys.exit(1)
    print(f"classify self-test: OK ({total} checks)")


if __name__ == "__main__":
    main()
