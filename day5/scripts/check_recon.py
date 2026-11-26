#!/usr/bin/env python3
"""Check your Lab 4 queries against the answer key (day5/data/expected_recon.json).

Usage (from the repo root, after source day5/scripts/env.sh <id>):
    python3 day5/scripts/check_recon.py
Runs day5/sql/10_unmatched_remittance.sql, (re)creates the view from 11_settlement_recon.sql,
then runs 12_recon_summary.sql, and compares both with the key. Exit 0 = both match.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from athena import run  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "day5" / "sql"
KEY = json.loads((ROOT / "day5" / "data" / "expected_recon.json").read_text())


def q(name, wg, db, bucket):
    sql = (SQL / name).read_text().replace("${BUCKET}", bucket).strip().rstrip(";")
    res, rows = run(sql, wg, db)
    if res["Status"]["State"] != "SUCCEEDED":
        print(f"  ✗ {name}: {res['Status']['State']} - {res['Status'].get('StateChangeReason', '?')}")
        return None
    return rows


def main():
    wg, db, bucket = os.environ.get("WG"), os.environ.get("GDB"), os.environ.get("B")
    if not (wg and db and bucket):
        print("WG, GDB and B are not set: source day5/scripts/env.sh <your-id>"); return 2
    ok = 0

    rows = q("10_unmatched_remittance.sql", wg, db, bucket)
    if rows is not None:
        hdr = [h.lower() for h in rows[0]] if rows else []
        need = ["ticket_number", "agency_iata", "remitted_amount", "fare_amount", "reason"]
        if hdr != need:
            print(f"  ✗ 10_unmatched_remittance: columns {hdr}, expected {need}")
        else:
            got = [{"ticket_number": r[0], "reason": r[4]} for r in rows[1:]]
            if got == KEY["unmatched_remittance"]:
                ok += 1
                print(f"  ✓ 10_unmatched_remittance: {len(got)} rows, all match the key")
            else:
                print(f"  ✗ 10_unmatched_remittance: got {len(got)} rows, expected {len(KEY['unmatched_remittance'])}")
                for g in got[:8]:
                    print(f"      got      {g}")
                for e in KEY["unmatched_remittance"]:
                    print(f"      expected {e}")

    if q("11_settlement_recon.sql", wg, db, bucket) is not None:
        rows = q("12_recon_summary.sql", wg, db, bucket)
        if rows is not None:
            got = {r[0]: int(r[1]) for r in rows[1:]}
            exp = KEY["recon_status_counts"]
            grain = run("SELECT count(*), count(DISTINCT ticket_number) FROM settlement_recon", wg, db)[1]
            n, distinct = (int(x) for x in grain[1]) if len(grain) > 1 else (0, 0)
            if (n, distinct) != (KEY["recon_rows"], KEY["recon_rows"]):
                print(f"  ✗ settlement_recon: {n} rows, {distinct} distinct ticket_numbers; expected {KEY['recon_rows']} and {KEY['recon_rows']}"
                      " (one row per ticket on either side)")
            elif got == exp:
                ok += 1
                print(f"  ✓ settlement_recon: {n} rows, one per ticket_number: {' · '.join(f'{k} {v}' for k, v in sorted(got.items()))}")
            else:
                print("  ✗ settlement_recon status counts differ")
                for k in sorted(set(got) | set(exp)):
                    mark = "" if got.get(k) == exp.get(k) else "   <-"
                    print(f"      {k:<20} got {got.get(k, 0):>4}   expected {exp.get(k, 0):>4}{mark}")
    print(f"\nreconciliation: {ok}/2 checks pass")
    return 0 if ok == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
