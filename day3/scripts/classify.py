#!/usr/bin/env python3
"""Day 3 - classify a ticket-issue batch file with a rules file, and show where every record goes.

This is a DESIGN tool, run locally: it applies the validation rules you wrote in
day3/rules/validation_rules.json to a synthetic batch file, routes every record to a zone, and
(optionally) writes the zone layout to a local folder so you can see it. Day 4 builds the real
Lambda validator from the same rules file.

Rules are evaluated in fixed phases, whatever order you list them in:
    file rules (header)  ->  required  ->  format  ->  referential  ->  business  ->  late flag  ->  duplicates
The first failing quarantine rule decides the record's reason. Duplicates are checked last, only
for records that passed everything else (a bad record never "claims" a ticket number).

Run from the repo root:
    python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25.csv
    python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25.csv --check
    python3 day3/scripts/classify.py day3/data/ticket_issue_AI_2026-11-25.csv --zones day3/outputs/zones
    python3 day3/scripts/classify.py <file> --rules <other rules file>

Output shows line numbers, routes, reasons and rule ids only - never field values (logging contract).
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAY3 = ROOT / "day3"
REF_DIR = DAY3 / "data" / "ref"
DEFAULT_RULES = DAY3 / "rules" / "validation_rules.json"
EXPECTED = DAY3 / "data" / "expected_routing.json"

PHASES = ["required", "format", "referential", "business", "late", "duplicate"]
ROUTES = {"quarantine", "flag", "skip"}
FILE_DATE = re.compile(r"_(\d{4}-\d{2}-\d{2})")


class RuleError(Exception):
    pass


def load_ref():
    ref = {}
    for p in REF_DIR.glob("*.json"):
        data = json.loads(p.read_text())
        ref[p.stem] = set(data["codes"]) if "codes" in data else {k: v for k, v in data.items() if not k.startswith("_")}
    return ref


def file_business_date(path):
    m = FILE_DATE.search(Path(path).name)
    if not m:
        raise SystemExit(f"Can't find a YYYY-MM-DD business date in the file name: {Path(path).name}")
    return date.fromisoformat(m.group(1))


# ---------------------------------------------------------------- one function per rule type
def _field(rule, row):
    return (row.get(rule["field"]) or "").strip()


def check(rule, row, ctx):
    """Return True if the record PASSES the rule."""
    t = rule["type"]
    if t == "required":
        return all((row.get(f) or "").strip() for f in rule["fields"])
    v = _field(rule, row)
    if t == "regex":
        return re.fullmatch(rule["pattern"], v) is not None
    if t == "in_ref":
        return v in ctx["ref"][rule["ref"]]
    if t == "od_airports_in_ref":
        parts = v.split("-")
        return len(parts) == 2 and all(p in ctx["ref"]["airports"] for p in parts)
    if t == "od_distinct":
        parts = v.split("-")
        return len(parts) == 2 and parts[0] != parts[1]
    if t == "ticket_prefix":
        airline = ctx["ref"]["airlines"].get((row.get(rule["airline_field"]) or "").strip())
        return bool(airline) and v.split("-")[0] == airline["ticket_prefix"]
    if t == "decimal_places":
        try:
            d = Decimal(v)
        except InvalidOperation:
            return False
        return d.is_finite() and -d.as_tuple().exponent <= rule["max_places"]
    if t == "min_exclusive":
        try:
            return Decimal(v) > Decimal(str(rule["value"]))
        except InvalidOperation:
            return False
    if t == "date_format":
        try:
            datetime.strptime(v, rule.get("format", "%Y-%m-%d"))
            return True
        except ValueError:
            return False
    if t == "date_not_after_file_date":
        try:
            return date.fromisoformat(v) <= ctx["file_date"]
        except ValueError:
            return False
    if t == "date_within_days":
        try:
            return date.fromisoformat(v) >= ctx["file_date"] - timedelta(days=rule["days"])
        except ValueError:
            return False
    if t == "unique":
        return True   # handled in classify(), after every other rule
    raise RuleError(f"rule {rule.get('id')}: unknown type {t!r}")


def load_rules(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"Can't read rules file {path}: {e}")
    rules = [r for r in data.get("rules", []) if isinstance(r, dict)]
    file_rules = [r for r in data.get("file_rules", []) if isinstance(r, dict)]
    problems = []
    seen = set()
    for r in rules + file_rules:
        rid = r.get("id")
        if not rid or rid in seen:
            problems.append(f"missing or duplicate id: {rid!r}")
        seen.add(rid)
    for r in rules:
        if r.get("reason") not in PHASES:
            problems.append(f"{r.get('id')}: reason must be one of {PHASES}")
        if r.get("on_fail") not in ROUTES:
            problems.append(f"{r.get('id')}: on_fail must be one of {sorted(ROUTES)}")
    if problems:
        raise SystemExit("Rules file problems:\n  " + "\n  ".join(problems))
    rules.sort(key=lambda r: PHASES.index(r["reason"]))
    return data, file_rules, rules


def classify(path, file_rules, rules, ref):
    """Return (file_result, per-record results). Never returns field values."""
    path = Path(path)
    ctx = {"ref": ref, "file_date": file_business_date(path)}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        records = list(reader)
    for fr in file_rules:
        if fr["type"] == "header" and header != fr["columns"]:
            missing = [c for c in fr["columns"] if c not in header]
            extra = [c for c in header if c not in fr["columns"]]
            return {"route": "quarantine", "reason": fr.get("reason", "schema"), "rule": fr["id"],
                    "detail": f"missing={missing} unexpected={extra}"}, [], len(records)
    results = []
    seen = {}
    for i, row in enumerate(records):
        line = i + 2
        out = {"line": line, "route": "validated", "reason": "", "rule": "", "flags": []}
        for r in rules:
            if r["type"] == "unique":
                continue
            if check(r, row, ctx):
                continue
            if r["on_fail"] == "flag":
                out["flags"].append(r["id"])
                if out["route"] == "validated":
                    out.update(route="flagged", reason=r["reason"], rule=r["id"])
                continue
            out.update(route="quarantine" if r["on_fail"] == "quarantine" else "skipped",
                       reason=r["reason"], rule=r["id"])
            break
        if out["route"] in ("validated", "flagged"):
            for r in (r for r in rules if r["type"] == "unique"):
                k = (row.get(r["field"]) or "").strip()
                if k in seen:
                    route = {"skip": "duplicate", "quarantine": "quarantine", "flag": "flagged"}[r["on_fail"]]
                    out.update(route=route, reason=r["reason"], rule=r["id"], dup_of=seen[k])
                    break
                seen[k] = line
        results.append(out)
    return {"route": "accepted"}, results, len(records)


def write_zones(path, zones, file_res, results, rules_path, cid):
    """Write the local zone layout: validated/, quarantine/<reason>/, and a manifest. Rows are data
    and belong in the zones; the sidecar and manifest carry line numbers and rule ids only."""
    path = Path(path)
    d = file_business_date(path).isoformat()
    zones = Path(zones)
    raw_bytes = path.read_bytes()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    by_zone = defaultdict(list)
    if file_res["route"] == "quarantine":
        by_zone[("quarantine", file_res["reason"])] = list(range(len(rows)))
    for r in results:
        if r["route"] in ("validated", "flagged"):
            by_zone[("validated", "")].append(r)
        elif r["route"] == "quarantine":
            by_zone[("quarantine", r["reason"])].append(r)
    written = []
    for (zone, reason), items in by_zone.items():
        folder = zones / zone / (reason if zone == "quarantine" else "") / f"business_date={d}"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / path.name
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if zone == "validated":
                w.writerow(header + ["flags", "source_line"])
                for r in items:
                    w.writerow(rows[r["line"] - 2] + [";".join(r["flags"]), r["line"]])
            else:
                w.writerow(header + ["source_line"])
                for r in items:
                    line = r["line"] if isinstance(r, dict) else r + 2
                    w.writerow(rows[line - 2] + [line])
        if zone == "quarantine":
            side = {"source_key": f"raw/{path.name}", "correlation_id": cid, "reason": reason,
                    "records": [{"source_line": r["line"], "rule_id": r["rule"]} for r in items if isinstance(r, dict)]
                    or [{"file_rule": file_res.get("rule"), "detail": file_res.get("detail")}]}
            # newline="\n": the same bytes on Windows and Linux (the CSVs already use newline="")
            (folder / (path.name + ".sidecar.json")).write_text(json.dumps(side, indent=1), encoding="utf-8", newline="\n")
        written.append(out)
    counts = Counter(r["route"] for r in results)
    manifest = {
        "source_key": f"raw/{path.name}", "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "business_date": d, "correlation_id": cid,
        "rules_file": str(Path(rules_path).relative_to(ROOT)) if Path(rules_path).resolve().is_relative_to(ROOT) else str(rules_path),
        "rules_sha256": hashlib.sha256(Path(rules_path).read_bytes()).hexdigest()[:16],
        "ref_data_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sorted(REF_DIR.glob("*.json")))).hexdigest()[:16],
        "classifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
        "file_route": file_res["route"],
        "rows_in": len(rows),
        "validated": counts["validated"], "flagged": counts["flagged"], "duplicates": counts["duplicate"],
        "quarantined": dict(Counter(r["reason"] for r in results if r["route"] == "quarantine"))
        if file_res["route"] == "accepted" else {file_res["reason"]: len(rows)},
    }
    total_out = (manifest["validated"] + manifest["flagged"] + manifest["duplicates"]
                 + sum(manifest["quarantined"].values()) + counts["skipped"])
    manifest["balanced"] = total_out == len(rows)
    mf = zones / "manifests" / f"business_date={d}"
    mf.mkdir(parents=True, exist_ok=True)
    (mf / (path.name + ".manifest.json")).write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    return written, manifest


def compare(path, file_res, results):
    """Compare against day3/data/expected_routing.json. Returns (matched, total, mismatches)."""
    exp_all = json.loads(EXPECTED.read_text())["files"]
    exp = exp_all.get(Path(path).name)
    if exp is None:
        raise SystemExit(f"No expected routing for {Path(path).name} in {EXPECTED.relative_to(ROOT)}")
    if exp["file_route"] == "quarantine":
        ok = file_res["route"] == "quarantine" and file_res.get("reason") == exp["reason"]
        return (1 if ok else 0), 1, ([] if ok else [f"whole file: expected quarantine/{exp['reason']}, got "
                                                   f"{file_res['route']}{'/' + file_res.get('reason', '') if file_res['route'] == 'quarantine' else ''}"])
    if file_res["route"] != "accepted":
        return 0, len(exp["rows"]), [f"whole file rejected by {file_res.get('rule')} ({file_res.get('detail')}), expected it accepted"]
    got = {r["line"]: r for r in results}
    bad = []
    for e in exp["rows"]:
        g = got.get(e["line"], {"route": "missing", "reason": ""})
        want = e["route"] if e["route"] == "validated" else f"{e['route']}/{e['reason']}"
        have = g["route"] if g["route"] == "validated" else f"{g['route']}/{g['reason']}"
        if want != have:
            hint = f"  ({e['area']}: {e['field']})" if e["area"] else ""
            caught = f" by {g['rule']}" if g.get("rule") else (" - no rule caught it" if g["route"] == "validated" else "")
            bad.append(f"line {e['line']:>2}: expected {want:<24} got {have}{caught}{hint}")
    return len(exp["rows"]) - len(bad), len(exp["rows"]), bad


def main():
    ap = argparse.ArgumentParser(description="Classify a ticket-issue batch with your validation rules (local, no AWS)")
    ap.add_argument("file", help="CSV batch file, e.g. day3/data/ticket_issue_AI_2026-11-25.csv")
    ap.add_argument("--rules", default=str(DEFAULT_RULES), help="rules file (default: day3/rules/validation_rules.json)")
    ap.add_argument("--zones", help="write the zone layout to this local folder, e.g. day3/outputs/zones")
    ap.add_argument("--check", action="store_true", help="compare the routing with day3/data/expected_routing.json")
    ap.add_argument("--cid", help="correlation_id for the manifest (default: minted)")
    args = ap.parse_args()

    if not Path(args.rules).exists():
        raise SystemExit(f"No rules file at {args.rules}. Start from the template:\n"
                         "  cp day3/rules/validation_rules.template.json day3/rules/validation_rules.json")
    _data, file_rules, rules = load_rules(args.rules)
    ref = load_ref()
    try:
        file_res, results, n = classify(args.file, file_rules, rules, ref)
    except RuleError as e:
        raise SystemExit(str(e))
    name = Path(args.file).name
    print(f"{name}  ·  {n} records  ·  {len(rules)} record rules + {len(file_rules)} file rule(s)  ·  rules: {Path(args.rules).name}\n")

    if file_res["route"] == "quarantine":
        print(f"  WHOLE FILE -> quarantine/{file_res['reason']}/   ({file_res['rule']}: {file_res['detail']})")
    else:
        for r in results:
            if r["route"] == "validated":
                continue
            extra = f"  (same ticket_number as line {r['dup_of']})" if r.get("dup_of") else ""
            dest = {"quarantine": f"quarantine/{r['reason']}/", "flagged": "validated/ + flag",
                    "duplicate": "skipped as duplicate", "skipped": "skipped"}[r["route"]]
            print(f"  line {r['line']:>2}  ->  {dest:<26} {r['rule']}{extra}")
        c = Counter(r["route"] for r in results)
        q = Counter(r["reason"] for r in results if r["route"] == "quarantine")
        print(f"\n  validated {c['validated']}  ·  flagged {c['flagged']}  ·  duplicate {c['duplicate']}  ·  "
              f"quarantined {sum(q.values())} " + (f"({', '.join(f'{k} {v}' for k, v in sorted(q.items()))})" if q else ""))
        total = sum(c.values())
        print(f"  manifest balance: {n} in = {total} out  ->  {'BALANCED' if total == n else 'NOT BALANCED'}")

    if args.zones:
        cid = args.cid or f"m3-{uuid.uuid4().hex[:8]}"
        written, manifest = write_zones(args.file, args.zones, file_res, results, args.rules, cid)
        print(f"\n  zones written under {args.zones}/  (correlation_id {cid})")
        for w in sorted(written):
            print(f"    {w}")
        print(f"    manifest: balanced={manifest['balanced']}  rules={manifest['rules_sha256']}  "
              f"ref_data={manifest['ref_data_sha256']}  classifier={manifest['classifier_sha256']}")

    if args.check:
        ok, total, bad = compare(args.file, file_res, results)
        print(f"\n  CHECK against expected routing: {ok}/{total} " + ("records" if total > 1 else "file") + " routed as expected")
        for b in bad:
            print("    " + b)
        if not bad:
            print("    All routed as expected.")
        sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
